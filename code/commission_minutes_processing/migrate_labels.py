#!/usr/bin/env python3
"""
migrate_labels.py — migrate the existing hand-labelled *_labeled.json files into
the new full SCHEMA (extraction_common.SCHEMA).

For every record: canonicalise typo'd/renamed keys, coerce to a schema-complete,
correctly-typed record (all 35 fields present), derive request_type from the case
suffix when missing. Originals are backed up to tagged/training/_backup_pre_schema/.

Run once before opening the labeling app, so your prior work shows up (in the new
schema, with the new fields empty) for review.

Usage:  python migrate_labels.py [--dry-run]
"""
from __future__ import annotations
import argparse, json, shutil, sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extraction_common import FIELDS, coerce_record
from autoextract import derive_request_type
from training_sample_create import normalise_keys, train_dir

BACKUP = train_dir / "_backup_pre_schema"


def migrate_record(rec: dict) -> tuple[dict, set]:
    """Return (schema_complete_record, dropped_keys)."""
    canon = normalise_keys(rec)
    dropped = {k for k in canon if k not in FIELDS}
    out = coerce_record(canon)
    if not out.get("request_type"):
        out["request_type"] = derive_request_type(out.get("case_number", ""))
    return out, dropped


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    files = sorted(train_dir.glob("*_labeled.json"))
    if not files:
        print("no *_labeled.json files found in", train_dir)
        return

    if not args.dry_run:
        BACKUP.mkdir(exist_ok=True)

    all_dropped = Counter()
    total = 0
    for f in files:
        recs = json.load(f.open())
        migrated, dropped_here = [], Counter()
        for r in recs:
            m, dropped = migrate_record(r)
            migrated.append(m)
            for k in dropped:
                dropped_here[k] += 1
                all_dropped[k] += 1
        total += len(migrated)
        if not args.dry_run:
            shutil.copy2(f, BACKUP / f.name)
            f.write_text(json.dumps(migrated, ensure_ascii=False, indent=4) + "\n",
                         encoding="utf-8")
        drp = ("  dropped: " + ", ".join(f"{k}×{c}" for k, c in dropped_here.most_common())
               if dropped_here else "")
        print(f"{'[dry] ' if args.dry_run else ''}{f.name}: {len(migrated)} records{drp}")

    print(f"\n{'Would migrate' if args.dry_run else 'Migrated'} {total} records "
          f"across {len(files)} files into {len(FIELDS)}-field schema.")
    if all_dropped:
        print("Keys dropped (not in schema — content not carried over):")
        for k, c in all_dropped.most_common():
            print(f"  {k}: {c}")
    if not args.dry_run:
        print(f"Backups in {BACKUP}")


if __name__ == "__main__":
    main()
