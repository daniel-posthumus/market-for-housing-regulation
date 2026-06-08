#!/usr/bin/env python3
"""
llm_extract.py — few-shot, schema-constrained extraction as an alternative to
fine-tuning flan-t5-small on ~270 examples (see processing_review.md, issue #7).

It builds a K-shot prompt from the consolidated training set and extracts the JSON
record for each held-out block, then scores with the SAME field-level metric as
train.py (imported from extraction_common). It deliberately reuses train.py's exact
held-out split so the two approaches are directly comparable.

Backends:
  --backend anthropic : Claude via the Anthropic API (needs ANTHROPIC_API_KEY and
                        `pip install anthropic`). Strongest; JSON forced by prefill.
  --backend hf        : a local HuggingFace seq2seq model (default flan-t5-base).
  --backend auto      : anthropic if available, else hf (default).

Examples:
  python llm_extract.py --backend anthropic --shots 5
  python llm_extract.py --backend hf --shots 3 --limit 20
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extraction_common import (
    PROMPT_INSTRUCTION, EOJ_TOKEN, FIELDS, parse_obj, score_examples,
)

from paths import MEETING_MINUTES
TRAIN_FILE = MEETING_MINUTES / "tagged" / "training" / "training.txt"


# ─────────────────────── data / split (mirrors train.py) ───────────────────────
def load_split():
    """Return (train_examples, test_examples) using train.py's exact split."""
    from datasets import load_dataset
    ds = load_dataset("json", data_files={"train": str(TRAIN_FILE)})["train"]
    s1 = ds.train_test_split(test_size=0.2, seed=42)
    s2 = s1["test"].train_test_split(test_size=0.5, seed=42)
    return list(s1["train"]), list(s2["test"])     # train pool, held-out test


def clean_completion(c: str) -> str:
    return c.split(EOJ_TOKEN)[0].strip()


def build_fewshot(train_pool, shots: int, block_cap: int = 1600) -> str:
    """A compact instruction + K worked examples."""
    parts = [PROMPT_INSTRUCTION.replace("Raw block:\n", "").strip(),
             "\nExamples:"]
    for ex in train_pool[:shots]:
        blk = ex["prompt"].strip()[:block_cap]
        parts.append(f"\nBLOCK:\n{blk}\nJSON:\n{clean_completion(ex['completion'])}")
    return "\n".join(parts) + "\n\nNow extract the JSON for this block.\nBLOCK:\n"


# ─────────────────────── backends ───────────────────────
def run_anthropic(prompts, model):
    import anthropic
    client = anthropic.Anthropic()           # reads ANTHROPIC_API_KEY
    outs = []
    for i, p in enumerate(prompts, 1):
        msg = client.messages.create(
            model=model, max_tokens=1500,
            messages=[{"role": "user", "content": p},
                      {"role": "assistant", "content": "{"}],   # prefill → force JSON
        )
        outs.append("{" + msg.content[0].text)
        if i % 10 == 0:
            print(f"  …{i}/{len(prompts)}")
    return outs


def run_hf(prompts, model_name):
    import torch
    from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
    tok = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model.to(device).eval()
    outs = []
    for i, p in enumerate(prompts, 1):
        ids = tok(p, return_tensors="pt", truncation=True, max_length=2048).to(device)
        with torch.no_grad():
            gen = model.generate(**ids, max_new_tokens=1024, num_beams=1)
        outs.append(tok.decode(gen[0], skip_special_tokens=True))
        if i % 10 == 0:
            print(f"  …{i}/{len(prompts)}")
    return outs


# ─────────────────────── main ───────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", choices=["auto", "anthropic", "hf"], default="auto")
    ap.add_argument("--shots", type=int, default=5, help="number of in-context examples")
    ap.add_argument("--limit", type=int, default=0, help="cap #test items (0 = all)")
    ap.add_argument("--model", default=None, help="override model id")
    ap.add_argument("--dump", default=None, help="write predictions JSONL to this path")
    args = ap.parse_args(argv)

    if not TRAIN_FILE.exists():
        raise FileNotFoundError(f"{TRAIN_FILE} not found — run training_sample_create.py first.")

    train_pool, test = load_split()
    if args.limit:
        test = test[: args.limit]
    print(f"few-shot={args.shots}  test items={len(test)}")

    backend = args.backend
    if backend == "auto":
        backend = "anthropic" if (os.environ.get("ANTHROPIC_API_KEY")
                                  and _has("anthropic")) else "hf"
    print(f"backend = {backend}")

    fewshot = build_fewshot(train_pool, args.shots)
    prompts = [fewshot + ex["prompt"].strip() + "\nJSON:\n" for ex in test]
    refs = [clean_completion(ex["completion"]) for ex in test]

    if backend == "anthropic":
        model = args.model or os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        preds = run_anthropic(prompts, model)
    else:
        model = args.model or "google/flan-t5-base"
        preds = run_hf(prompts, model)

    metrics = score_examples(preds, refs)
    print("\n=== FEW-SHOT EXTRACTION METRICS (held-out test) ===")
    print(f"  backend/model: {backend} / {model}")
    for k, v in metrics.items():
        print(f"  {k}: {v:.4f}")

    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as f:
            for ex, pred in zip(test, preds):
                obj = parse_obj(pred) or {}
                f.write(json.dumps({"case_number": obj.get("case_number", ""),
                                    "prediction": obj}, ensure_ascii=False) + "\n")
        print(f"  wrote predictions → {args.dump}")


def _has(mod: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(mod) is not None


if __name__ == "__main__":
    main()
