#!/usr/bin/env python3
"""
learning_curve.py — how many hand-labels does the T5 extractor actually need?

Fine-tunes the model (via train.py's shared code path) at increasing training-set
sizes against a SINGLE fixed held-out test set, so the resulting field-accuracy
curve is comparable point-to-point. When the curve flattens, that's your plateau —
stop labeling there instead of guessing a number.

Design choices that make the curve honest:
  • The test set and the validation set are carved ONCE (fixed seed) and held
    constant across every size — only the training subset grows.
  • Training subsets are NESTED (size-N ⊂ size-2N) and drawn round-robin by
    request_type, so rare classes enter early and small-N points aren't penalised
    for simply never having seen a class. This mirrors the labeling app's
    rare-class-first queue (queue_order.py).
  • Per-field accuracy at the largest size is reported too, so you can see *which*
    fields are still starving for labels (usually the rare enums + count fields).

Outputs (under processed/minutes_extractor/learning_curve/ by default):
  curve.csv          one row per (size, seed): aggregate metrics  (appended live)
  per_field.csv      per-field accuracy at the largest size
  learning_curve.png field_accuracy + exact_record_ratio vs. #labels

Usage:
  python learning_curve.py                      # auto sizes, flan-t5-base
  python learning_curve.py --sizes 50,100,150,200,269 --epochs 8
  python learning_curve.py --seeds 0,1,2        # reps → error bars
  python learning_curve.py --smoke              # tiny/fast plumbing check (flan-t5-small)

This is COMPUTE-HEAVY: each point is a full fine-tune. On an M-series CPU a single
flan-t5-base point (≈200 labels × 8 epochs, generate-eval) is ~tens of minutes;
a 5-point curve is best run overnight or on a GPU. The CSV is written after every
point and completed (size, seed) pairs are skipped on re-run, so it is resumable.
"""
from __future__ import annotations
import argparse, csv, json, sys
from collections import Counter, defaultdict, OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from datasets import Dataset, DatasetDict
from extraction_common import (FIELDS, parse_obj, field_match, is_empty)  # noqa: E402
import train as T                                                          # noqa: E402

CURVE_COLS = ["size", "seed", "train", "val", "test",
              "field_accuracy", "exact_record_ratio", "parseable_ratio", "valid_json_ratio"]


# ───────────────────────────── data partitioning ────────────────────────────
def _class_of(rec: dict) -> str:
    """request_type recorded in the completion (the binding-constraint class);
    'other'/'?' when the completion doesn't parse."""
    obj = parse_obj(rec.get("completion", "")) or {}
    return str(obj.get("request_type") or "other") or "other"


def round_robin_by_class(records, seed: int = 42):
    """Deterministic ordering that cycles through request_type classes, so taking
    the first N yields a class-balanced, rare-class-inclusive subset. Nested by
    construction: first-N ⊂ first-2N."""
    import random
    rng = random.Random(seed)
    buckets: dict[str, list] = defaultdict(list)
    for r in records:
        buckets[_class_of(r)].append(r)
    for b in buckets.values():
        rng.shuffle(b)
    # iterate classes in a fixed (rarest-first) order so rare classes lead
    order = sorted(buckets, key=lambda c: len(buckets[c]))
    queues = OrderedDict((c, list(buckets[c])) for c in order)
    out = []
    while any(queues.values()):
        for c in order:
            if queues[c]:
                out.append(queues[c].pop())
    return out


def partition(records, *, seed: int, test_frac: float, val_frac: float):
    """Shuffle once, carve a fixed test set and a fixed validation set, return
    (fixed_test, fixed_val, ordered_train_pool)."""
    import random
    recs = list(records)
    random.Random(seed).shuffle(recs)
    n = len(recs)
    n_test = max(1, round(n * test_frac))
    n_val = max(1, round(n * val_frac))
    test = recs[:n_test]
    val = recs[n_test:n_test + n_val]
    pool = round_robin_by_class(recs[n_test + n_val:], seed=seed)
    return test, val, pool


# ───────────────────────────── per-field scoring ────────────────────────────
def per_field_accuracy(pred_texts, ref_texts):
    hits, tot = Counter(), Counter()
    for p, r in zip(pred_texts, ref_texts):
        rr = parse_obj(r)
        if not isinstance(rr, dict):
            continue
        pp = parse_obj(p) if isinstance(parse_obj(p), dict) else {}
        for k in FIELDS:
            if is_empty(rr.get(k)):
                continue
            tot[k] += 1
            if field_match(pp, rr, k):
                hits[k] += 1
    return {k: (hits[k] / tot[k] if tot[k] else None, tot[k]) for k in FIELDS}


# ───────────────────────────────── plotting ─────────────────────────────────
def plot_curve(csv_path: Path, png_path: Path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"  (matplotlib unavailable — skipping plot: {e})")
        return
    rows = list(csv.DictReader(csv_path.open()))
    if not rows:
        return
    # average reps per size
    agg: dict[int, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        s = int(r["size"])
        for m in ("field_accuracy", "exact_record_ratio"):
            agg[s][m].append(float(r[m]))
    sizes = sorted(agg)
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for m, label in (("field_accuracy", "field accuracy"),
                     ("exact_record_ratio", "exact-record ratio")):
        import statistics
        means = [statistics.mean(agg[s][m]) for s in sizes]
        ax.plot(sizes, means, marker="o", label=label)
        if any(len(agg[s][m]) > 1 for s in sizes):
            errs = [statistics.pstdev(agg[s][m]) for s in sizes]
            ax.errorbar(sizes, means, yerr=errs, fmt="none", capsize=3, alpha=.5)
    ax.set_xlabel("# hand-labeled training examples")
    ax.set_ylabel("held-out accuracy")
    ax.set_title("Minutes extractor — learning curve")
    ax.set_ylim(0, 1)
    ax.grid(alpha=.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(png_path, dpi=150)
    print(f"  ✓ plot → {png_path}")


# ───────────────────────────────── main ─────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", help="comma list of training sizes (default: auto, 5 points)")
    ap.add_argument("--seeds", default="0", help="comma list of rep seeds (default: 0)")
    ap.add_argument("--epochs", type=int, default=None, help="epochs per point (default: 8)")
    ap.add_argument("--model", default=None, help="base model (default: flan-t5-base)")
    ap.add_argument("--test-frac", type=float, default=0.20)
    ap.add_argument("--val-frac", type=float, default=0.10)
    ap.add_argument("--train-file", default=None, help="override consolidated training.txt")
    ap.add_argument("--out", default=None, help="output dir for csv/png")
    ap.add_argument("--smoke", action="store_true",
                    help="tiny/fast plumbing check: flan-t5-small, 1 epoch, sizes 16,24")
    args = ap.parse_args(argv)

    from paths import MEETING_MINUTES
    train_file = Path(args.train_file) if args.train_file else \
        MEETING_MINUTES / "tagged" / "training" / "training.txt"
    out_dir = Path(args.out) if args.out else \
        MEETING_MINUTES / "processed" / "minutes_extractor" / "learning_curve"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "curve.csv"
    png_path = out_dir / "learning_curve.png"
    pf_path = out_dir / "per_field.csv"

    records = T.load_records(train_file)
    print(f"loaded {len(records)} consolidated examples from {train_file}")

    model = args.model or ("google/flan-t5-small" if args.smoke else None)
    epochs = args.epochs if args.epochs is not None else (1 if args.smoke else 8)
    seeds = [int(s) for s in args.seeds.split(",") if s.strip() != ""]

    # one partition per seed (test/val fixed within a seed); pool defines size cap
    parts = {sd: partition(records, seed=sd, test_frac=args.test_frac,
                            val_frac=args.val_frac) for sd in seeds}
    pool_cap = min(len(p[2]) for p in parts.values())

    if args.sizes:
        sizes = [int(x) for x in args.sizes.split(",")]
    elif args.smoke:
        sizes = [16, 24]
    else:
        # 5 points spread from a small floor to the full pool
        lo = max(40, pool_cap // 5)
        sizes = sorted({round(lo + (pool_cap - lo) * i / 4) for i in range(5)})
    sizes = [s for s in sizes if 0 < s <= pool_cap]
    print(f"pool (max train size) = {pool_cap}; sizes = {sizes}; seeds = {seeds}; "
          f"model = {model or T.DEFAULT_MODEL}; epochs = {epochs}")

    # resume: skip (size, seed) already in the csv
    done = set()
    if csv_path.exists():
        for r in csv.DictReader(csv_path.open()):
            done.add((int(r["size"]), int(r["seed"])))
    else:
        with csv_path.open("w", newline="") as fh:
            csv.writer(fh).writerow(CURVE_COLS)

    last_pred = last_ref = None
    for sd in seeds:
        test, val, pool = parts[sd]
        for n in sizes:
            if (n, sd) in done:
                print(f"• size={n} seed={sd}: already in csv, skipping")
                continue
            train = pool[:n]
            ds = DatasetDict(train=Dataset.from_list(train),
                             validation=Dataset.from_list(val),
                             test=Dataset.from_list(test))
            print(f"\n=== size={n} seed={sd} (train={len(train)} val={len(val)} test={len(test)}) ===")
            point_out = out_dir / f"_run_n{n}_s{sd}"
            metrics, trainer = T.train_and_evaluate(
                ds, point_out, model_name=model, epochs=epochs,
                verbose=args.smoke, save=False)
            row = {"size": n, "seed": sd, "train": len(train), "val": len(val),
                   "test": len(test),
                   "field_accuracy": metrics.get("test_field_accuracy", 0.0),
                   "exact_record_ratio": metrics.get("test_exact_record_ratio", 0.0),
                   "parseable_ratio": metrics.get("test_parseable_ratio", 0.0),
                   "valid_json_ratio": metrics.get("test_valid_json_ratio", 0.0)}
            with csv_path.open("a", newline="") as fh:
                csv.DictWriter(fh, CURVE_COLS).writerow(row)
            print(f"  field_accuracy={row['field_accuracy']:.3f} "
                  f"exact={row['exact_record_ratio']:.3f}")
            # keep predictions from the largest point for the per-field table
            if n == max(sizes):
                last_pred, last_ref = T.predict_texts(trainer, trainer.tokenizer, test)

    # per-field table from the largest run
    if last_pred is not None:
        pf = per_field_accuracy(last_pred, last_ref)
        with pf_path.open("w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["field", "accuracy", "n_in_test"])
            for k in FIELDS:
                acc, ntot = pf[k]
                w.writerow([k, "" if acc is None else f"{acc:.3f}", ntot])
        print(f"\nper-field accuracy (largest size) → {pf_path}")
        worst = sorted((k for k in FIELDS if pf[k][0] is not None),
                       key=lambda k: pf[k][0])[:8]
        print("  weakest fields (these want more labels):")
        for k in worst:
            acc, ntot = pf[k]
            print(f"    {k:28s} {acc:.2f}  (n={ntot})")

    plot_curve(csv_path, png_path)
    print(f"\n✓ done. curve → {csv_path}")


if __name__ == "__main__":
    main()
