#!/usr/bin/env python3
"""
train.py — fine-tune a T5 model to extract structured JSON from minutes blocks.

Capacity options (set via environment variables):
  MINUTES_MODEL    base model      (default: google/flan-t5-base)
  MINUTES_USE_LORA "1" to fine-tune with LoRA/PEFT (recommended for -base/-large)
  MINUTES_EPOCHS   training epochs (default: 10)

Schema, prompt, and the scoring metric are imported from extraction_common so
they stay in sync with llm_extract.py.
"""

import os, sys, numpy as np
from pathlib import Path
from datasets import load_dataset, DatasetDict
from transformers import (
    AutoTokenizer, AutoModelForSeq2SeqLM,
    DataCollatorForSeq2Seq,
    Seq2SeqTrainingArguments, Seq2SeqTrainer
)

# shared schema + metric (sibling module)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from extraction_common import PROMPT_INSTRUCTION, EOJ_TOKEN, score_examples

# ───────────────────────────────── main ──────────────────────────────────
def main():
    # ── paths ────────────────────────────────────────────────────────────
    from paths import MEETING_MINUTES
    data = MEETING_MINUTES
    # training.txt is written by training_sample_create.py into tagged/training/
    train_dir = data / "tagged" / "training"
    out  = data / "processed" / "minutes_extractor"
    out.mkdir(parents=True, exist_ok=True)

    # ── dataset ──────────────────────────────────────────────────────────
    train_file = train_dir / "training.txt"
    if not train_file.exists():
        raise FileNotFoundError(
            f"{train_file} not found. Run training_sample_create.py first to build it."
        )
    ds = load_dataset("json", data_files={"train": str(train_file)})["train"]
    # Three-way split: 80% train / 10% validation (model selection) / 10% test
    # (held out, never trained or selected on — reported once at the end).
    s1 = ds.train_test_split(test_size=0.2, seed=42)
    s2 = s1["test"].train_test_split(test_size=0.5, seed=42)
    ds = DatasetDict(train=s1["train"], validation=s2["train"], test=s2["test"])
    print(f"split sizes: train={len(ds['train'])} val={len(ds['validation'])} test={len(ds['test'])}")

    # ── model / tokenizer ────────────────────────────────────────────────
    # Bigger default base than flan-t5-small; override with MINUTES_MODEL.
    MODEL_NAME = os.environ.get("MINUTES_MODEL", "google/flan-t5-base")
    USE_LORA   = os.environ.get("MINUTES_USE_LORA", "0") == "1"
    tokenizer  = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_NAME)
    model.gradient_checkpointing_enable()

    if USE_LORA:
        from peft import LoraConfig, get_peft_model, TaskType
        lora = LoraConfig(task_type=TaskType.SEQ_2_SEQ_LM, r=16, lora_alpha=32,
                          lora_dropout=0.05, target_modules=["q", "v"])
        model = get_peft_model(model, lora)
        model.print_trainable_parameters()

    # Token caps. Measured on the consolidated set: ~21% of (prompt+block) inputs
    # exceed 512 and ~86% of completions exceed 256 — i.e. the old 512/256 caps
    # truncated most JSON targets mid-object and many blocks before the ACTION/AYES
    # lines. Raised to cover ~p90 of the distribution.
    max_in, max_out = 1024, 1024

    # ── preprocessing ────────────────────────────────────────────────────
    def preprocess(ex):
        inputs = [PROMPT_INSTRUCTION + txt for txt in ex["prompt"]]
        model_in = tokenizer(inputs, max_length=max_in, truncation=True)
        labels = tokenizer(
            text_target=ex["completion"],          # no extra token added
            max_length=max_out,
        truncation=True,
        )
        model_in["labels"] = labels["input_ids"]
        return model_in

    tokenized = ds.map(preprocess, batched=True, remove_columns=["prompt", "completion"])

    # ── collator ─────────────────────────────────────────────────────────
    collator = DataCollatorForSeq2Seq(tokenizer, model=model)

    # ── training args ────────────────────────────────────────────────────
    args = Seq2SeqTrainingArguments(
        output_dir=str(out),
        learning_rate=3e-4,
        per_device_train_batch_size=1,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=4,
        num_train_epochs=int(os.environ.get("MINUTES_EPOCHS", "10")),
        weight_decay=0.01,
        predict_with_generate=True,
        logging_strategy="epoch",
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="field_accuracy",   # field-level, not just valid JSON
        greater_is_better=True,
        generation_max_length=1024,   # match max_out; p90 completion ≈ 810 tokens
        generation_num_beams=1,
        seed=42,
        fp16=False,                              # ignored on M-series
    )

    # ── metric fn ────────────────────────────────────────────────────────
    def _decode(seqs):
        """Decode token-id arrays, mapping the -100 label-pad to the real pad id
        and dropping ids outside the vocab."""
        seqs = np.asarray(seqs)
        seqs = np.where(seqs < 0, tokenizer.pad_token_id, seqs)
        vocab = tokenizer.vocab_size
        cleaned = [[int(t) for t in seq if 0 <= int(t) < vocab] for seq in seqs]
        return tokenizer.batch_decode(cleaned, skip_special_tokens=True)

    def compute_metrics(eval_preds):
        preds, labels = eval_preds
        dpred = _decode(preds)
        dref  = _decode(labels)
        #  ── DEBUG: show one prediction vs reference every epoch ──────────
        print("\n── sample pred ─────────────────────────────────────────")
        print(dpred[0][:500])
        print("── sample ref  ─────────────────────────────────────────")
        print(dref[0][:500])
        print("────────────────────────────────────────────────────────\n")
        return score_examples(dpred, dref)   # shared field-level metric

    # ── trainer ──────────────────────────────────────────────────────────
    trainer = Seq2SeqTrainer(
        model=model, args=args,
        train_dataset=tokenized["train"],
        eval_dataset=tokenized["validation"],   # model selection on validation
        tokenizer=tokenizer, data_collator=collator,
        compute_metrics=compute_metrics,
    )

    trainer.train()
    trainer.save_model(str(out))
    tokenizer.save_pretrained(str(out))
    print("✓ training complete – model saved to", out)

    # ── final, honest evaluation on the held-out test split ───────────────
    test_metrics = trainer.evaluate(tokenized["test"], metric_key_prefix="test")
    print("\n=== HELD-OUT TEST METRICS ===")
    for k, v in test_metrics.items():
        if k.startswith("test_"):
            print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")

if __name__ == "__main__":
    main()
