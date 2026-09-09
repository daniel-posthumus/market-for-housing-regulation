#!/usr/bin/env python3
"""
data_collect.py
---------------
Purpose : Flatten a run's JSONL records into one CSV for spreadsheet work.
Inputs  : a .jsonl of schema-v2 records — by default `processed/structured_data.jsonl`
          (written by run_extraction.py); pass --jsonl for an extract_corpus.py chunk.
Outputs : the matching .csv (default `processed/extracted_results.csv`).
Author  : Dan Post
Created : 2026-07-01

Notes
-----
Everything list-shaped is joined for display only; the JSONL stays the record of truth.
`speakers` is a list of OBJECTS under schema v2 ({name, stance, stance_basis}), so it is
rendered as "name (stance)" rather than string-joined — `", ".join` over dicts raises
TypeError, which is what this script did on every v2 record until 2026-09-09.

`vote` is derived here rather than stored: the tally is recoverable from the roll-call
lists and hand-entering it only ever produced stale mismatches (see extraction_common.
derive_vote, which this mirrors for the DataFrame path).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from paths import MEETING_MINUTES                                    # noqa: E402

PROC = MEETING_MINUTES / "processed"
DEFAULT_JSONL = PROC / "structured_data.jsonl"
DEFAULT_CSV = PROC / "extracted_results.csv"

# List-of-scalar columns: joined with a comma.
LIST_COLS = ("ayes", "noes", "absent", "recused", "excused", "lot_number")
# List-of-object columns: one renderer each, because the shape is not a string.
OBJ_COLS = ("speakers",)

# Preferred column order; anything else follows in its original order.
ORDER = [
    "item_id", "source_file", "meeting_date", "year", "case_number", "request_type",
    "project_address", "assessor_block", "lot_number", "project_descr",
    "type_district", "type_district_descr", "height_and_bulk_district",
    "special_use_district", "staff_planner",
    "preliminary_recommendation", "preliminary_recommendation_category",
    "action", "action_instrument", "action_instrument_no", "continued_to",
    "conditions_imposed", "project_modified", "modifications",
    "vote", "ayes", "noes", "absent",
    "speakers", "support_count", "oppose_count", "neutral_count",
]


def _n(v) -> int:
    if isinstance(v, list):
        return len(v)
    return len([s for s in str(v).split(",") if s.strip()]) if v else 0


def _speakers(v) -> str:
    """'Sue Hestor (oppose); (M) Speaker (support)'. Under v2 a speaker is an object; a v1
    record's bare strings still render, so an old file is not a crash."""
    if not isinstance(v, list):
        return "" if v is None else str(v)
    out = []
    for sp in v:
        if isinstance(sp, dict):
            name, stance = str(sp.get("name") or ""), str(sp.get("stance") or "")
            out.append(f"{name} ({stance})" if stance else name)
        else:
            out.append(str(sp))
    return "; ".join(x for x in out if x)


def jsonl_to_csv(jsonl_path: Path, csv_path: Path) -> pd.DataFrame:
    df = pd.read_json(jsonl_path, lines=True)

    # An inference script may nest the record under `extracted`; unpack it if so.
    if "extracted" in df.columns:
        df = pd.concat([df.drop(columns=["extracted"]),
                        pd.json_normalize(df["extracted"])], axis=1)

    # Derive the tally while ayes/noes are still lists, before they are joined below.
    if "ayes" in df.columns:
        df["vote"] = df.apply(
            lambda r: f"{_n(r.get('ayes'))}-{_n(r.get('noes'))}" if _n(r.get("ayes")) else "",
            axis=1)

    for col in OBJ_COLS:
        if col in df.columns:
            df[col] = df[col].apply(_speakers)
    for col in LIST_COLS:
        if col in df.columns:
            df[col] = df[col].apply(
                lambda x: ", ".join(str(i) for i in x) if isinstance(x, list) else x)

    present = [c for c in ORDER if c in df.columns]
    df = df[present + [c for c in df.columns if c not in present]]

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_path, index=False)
    return df


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL)
    ap.add_argument("--csv", type=Path, default=None,
                    help="default: the .jsonl path with a .csv suffix, or "
                         "processed/extracted_results.csv for the default input")
    a = ap.parse_args(argv)
    if not a.jsonl.exists():
        sys.exit(f"no such file: {a.jsonl}")
    out = a.csv or (DEFAULT_CSV if a.jsonl == DEFAULT_JSONL else a.jsonl.with_suffix(".csv"))
    df = jsonl_to_csv(a.jsonl, out)
    print(f"✓ wrote {len(df):,} rows to {out}")


if __name__ == "__main__":
    main()
