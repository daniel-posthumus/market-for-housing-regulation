#!/usr/bin/env python3
"""
train.py — fine-tune a T5 model to extract structured JSON from minutes blocks.

Capacity options (set via environment variables):
  MINUTES_MODEL    base model      (default: google/flan-t5-base)
  MINUTES_USE_LORA "1" to fine-tune with LoRA/PEFT (recommended for -base/-large)
  MINUTES_EPOCHS   training epochs (default: 10)

Schema, prompt, and the scoring metric are imported from extraction_common so
they stay in sync with llm_extract.py.

The training logic is factored into importable functions (`load_records`,
`split_records`, `train_and_evaluate`, …) so the learning-curve harness
(`learning_curve.py`) reuses exactly this code path instead of duplicating it.
"""

import os, sys, numpy as np
from pathlib import Path
from datasets import Dataset, DatasetDict
from transformers import (
    AutoTokenizer, AutoModelForSeq2SeqLM,
    DataCollatorForSeq2Seq,
    Seq2SeqTrainingArguments, Seq2SeqTrainer
)

# shared schema + metric (sibling module)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from extraction_common import PROMPT_INSTRUCTION, score_examples

# Token caps. Measured on the consolidated set: ~21% of (prompt+block) inputs
# exceed 512 and ~86% of completions exceed 256 — i.e. the old 512/256 caps
# truncated most JSON targets mid-object and many blocks before the ACTION/AYES
# lines. Raised to cover ~p90 of the distribution.
MAX_IN, MAX_OUT = 1024, 1024
DEFAULT_MODEL = "google/flan-t5-base"


# ─────────────────────────── data loading / splitting ───────────────────────
def load_records(train_file) -> list[dict]:
    """Read the JSONL consolidated training file into a list of
    {"prompt", "completion"} dicts."""
    import json
    train_file = Path(train_file)
    if not train_file.exists():
        raise FileNotFoundError(
            f"{train_file} not found. Run training_sample_create.py first to build it."
        )
    recs = []
    for line in train_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            recs.append(json.loads(line))
    return recs


def split_records(records, seed: int = 42) -> DatasetDict:
    """80/10/10 train / validation / test split (model selection on validation,
    test held out and reported once)."""
    ds = Dataset.from_list(list(records))
    s1 = ds.train_test_split(test_size=0.2, seed=seed)
    s2 = s1["test"].train_test_split(test_size=0.5, seed=seed)
    return DatasetDict(train=s1["train"], validation=s2["train"], test=s2["test"])


# ─────────────────────────── model / preprocessing ──────────────────────────
def load_model_and_tokenizer(model_name: str | None = None, use_lora: bool | None = None):
    model_name = model_name or os.environ.get("MINUTES_MODEL", DEFAULT_MODEL)
    use_lora = (os.environ.get("MINUTES_USE_LORA", "0") == "1") if use_lora is None else use_lora
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
    model.gradient_checkpointing_enable()
    if use_lora:
        from peft import LoraConfig, get_peft_model, TaskType
        lora = LoraConfig(task_type=TaskType.SEQ_2_SEQ_LM, r=16, lora_alpha=32,
                          lora_dropout=0.05, target_modules=["q", "v"])
        model = get_peft_model(model, lora)
        model.print_trainable_parameters()
    return model, tokenizer


def make_preprocess(tokenizer, max_in: int = MAX_IN, max_out: int = MAX_OUT):
    def preprocess(ex):
        inputs = [PROMPT_INSTRUCTION + txt for txt in ex["prompt"]]
        model_in = tokenizer(inputs, max_length=max_in, truncation=True)
        labels = tokenizer(text_target=ex["completion"], max_length=max_out, truncation=True)
        model_in["labels"] = labels["input_ids"]
        return model_in
    return preprocess


def decode_tokens(tokenizer, seqs):
    """Decode token-id arrays, mapping the -100 label-pad to the real pad id
    and dropping ids outside the vocab."""
    seqs = np.asarray(seqs)
    seqs = np.where(seqs < 0, tokenizer.pad_token_id, seqs)
    vocab = tokenizer.vocab_size
    cleaned = [[int(t) for t in seq if 0 <= int(t) < vocab] for seq in seqs]
    return tokenizer.batch_decode(cleaned, skip_special_tokens=True)


def make_compute_metrics(tokenizer, verbose: bool = True):
    def compute_metrics(eval_preds):
        preds, labels = eval_preds
        dpred = decode_tokens(tokenizer, preds)
        dref = decode_tokens(tokenizer, labels)
        if verbose:
            print("\n── sample pred ─────────────────────────────────────────")
            print(dpred[0][:500])
            print("── sample ref  ─────────────────────────────────────────")
            print(dref[0][:500])
            print("────────────────────────────────────────────────────────\n")
        return score_examples(dpred, dref)   # shared field-level metric
    return compute_metrics


# ─────────────────────────────── train + eval ───────────────────────────────
def train_and_evaluate(ds: DatasetDict, out_dir, *, model_name=None, use_lora=None,
                       epochs: int | None = None, max_in: int = MAX_IN,
                       max_out: int = MAX_OUT, verbose: bool = True, save: bool = True):
    """Train on ds['train'], select on ds['validation'], report on ds['test'].

    Returns (test_metrics: dict, trainer). The single code path shared by main()
    and the learning-curve harness so they can never drift.
    """
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    epochs = int(os.environ.get("MINUTES_EPOCHS", "10")) if epochs is None else epochs

    model, tokenizer = load_model_and_tokenizer(model_name, use_lora)
    tokenized = ds.map(make_preprocess(tokenizer, max_in, max_out),
                       batched=True, remove_columns=["prompt", "completion"])
    collator = DataCollatorForSeq2Seq(tokenizer, model=model)

    args = Seq2SeqTrainingArguments(
        output_dir=str(out_dir),
        learning_rate=3e-4,
        per_device_train_batch_size=1,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=4,
        num_train_epochs=epochs,
        weight_decay=0.01,
        predict_with_generate=True,
        logging_strategy="epoch",
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="field_accuracy",   # field-level, not just valid JSON
        greater_is_better=True,
        generation_max_length=max_out,   # p90 completion ≈ 810 tokens
        generation_num_beams=1,
        seed=42,
        fp16=False,                              # ignored on M-series
        report_to=[],
        disable_tqdm=not verbose,
    )

    trainer = Seq2SeqTrainer(
        model=model, args=args,
        train_dataset=tokenized["train"],
        eval_dataset=tokenized["validation"],   # model selection on validation
        tokenizer=tokenizer, data_collator=collator,
        compute_metrics=make_compute_metrics(tokenizer, verbose=verbose),
    )
    trainer.train()
    if save:
        trainer.save_model(str(out_dir))
        tokenizer.save_pretrained(str(out_dir))

    test_metrics = trainer.evaluate(tokenized["test"], metric_key_prefix="test")
    return test_metrics, trainer


def predict_texts(trainer, tokenizer, records, max_in: int = MAX_IN, max_out: int = MAX_OUT):
    """Run a trained `trainer` over raw {"prompt","completion"} records and return
    (pred_texts, ref_texts) decoded strings — for per-field scoring outside the
    aggregate metric."""
    ds = Dataset.from_list(list(records))
    tok = ds.map(make_preprocess(tokenizer, max_in, max_out),
                 batched=True, remove_columns=ds.column_names)
    out = trainer.predict(tok, max_length=max_out)
    preds = decode_tokens(tokenizer, out.predictions)
    refs = [r["completion"] for r in records]
    return preds, refs


# ───────────────────────────────── main ──────────────────────────────────
def main():
    from paths import MEETING_MINUTES
    data = MEETING_MINUTES
    train_dir = data / "tagged" / "training"     # written by training_sample_create.py
    out = data / "processed" / "minutes_extractor"

    records = load_records(train_dir / "training.txt")
    ds = split_records(records, seed=42)
    print(f"split sizes: train={len(ds['train'])} val={len(ds['validation'])} test={len(ds['test'])}")

    test_metrics, _ = train_and_evaluate(ds, out)
    print("✓ training complete – model saved to", out)

    print("\n=== HELD-OUT TEST METRICS ===")
    for k, v in test_metrics.items():
        if k.startswith("test_"):
            print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")


if __name__ == "__main__":
    main()
