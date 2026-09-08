#!/usr/bin/env python3
"""
extract_all_meetings.py
-----------------------
Purpose : Run the validated meeting-level extraction over EVERY document in the corpus, not
          just the hand-labelled sample — detect each document's meetings, anchor a window on
          the body header, and read off the ten meeting-level fields.
Inputs  : the raw corpus under MFHR_DATA_ROOT/meeting_minutes/<locality>/raw/, and
          date_boundary_app/date_gold.db for the hand-labelled meetings (used only to mark
          which rows are human-verified).
Outputs : meetings_all.csv — one row per meeting, and the same rows in a `meetings_all`
          table inside date_gold.db for querying.
Author  : Dan Post
Created : 2026-09-04

Notes
-----
This is the configuration round 4 validated: boundaries placed by the machine (detected,
then snapped to the body header via `meeting_headers.snap_to_header`), fields read from the
resulting window, then the corpus-wide name reconciliation applied across every meeting at
once. Round 4 scored 96.0% on field values before correction and 98.4% after, with the
residual concentrated in 1998-2001 `staff`.

Accuracy is not uniform, and the per-row `era` column is there so downstream work can
respect that: from 2002 the measured field error rate is 3.8%, for 1999-2001 it is 11.1%
(5.7% excluding `staff`).

Usage:
  python extract_all_meetings.py              # extract, write CSV + table
  python extract_all_meetings.py --years 1998-2001
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "date_boundary_app"))
import assign_meeting_dates as AD                        # noqa: E402
import meeting_headers as M                              # noqa: E402
import app as BA                                         # noqa: E402
from paths import MEETING_MINUTES                        # noqa: E402

RAW = MEETING_MINUTES / "raw"
OUT_CSV = HERE / "meetings_all.csv"
FIELDS = M.MEETING_FIELDS


def documents(years: set[int] | None):
    for ydir in sorted(d for d in RAW.iterdir() if d.is_dir() and d.name.isdigit()):
        year = int(ydir.name)
        if years and year not in years:
            continue
        for f in sorted(ydir.iterdir()):
            if f.suffix.lower() in (".html", ".htm", ".pdf", ".txt"):
                yield year, f


def meetings_in(path: Path) -> list[dict]:
    """Every meeting in one document, with its fields."""
    try:
        text = BA.doc_text(path)
    except Exception as e:
        return [{"_error": f"read failed: {e}"}]
    lines = text.split("\n")
    starts, off = [], 0
    for line in lines:
        starts.append(off)
        off += len(line) + 1

    hdrs = AD.header_dates(text)
    if not hdrs:
        fb = AD.title_date(text) or AD.stem_date(str(path))
        hdrs = [(0, fb)] if fb else []
    if not hdrs:
        return []

    anchors = [max(i for i, s in enumerate(starts) if s <= o) for o, _d in hdrs]
    out = []
    for k, (a0, (_o, date)) in enumerate(zip(anchors, hdrs)):
        later = [x for x in anchors if x > a0]
        ln = M.snap_to_header(lines, a0, later[0] if later else None)
        win, date_line = M.window_for(lines, ln, 15, 15)
        ext = M._extended_for(lines, ln, anchors)
        rec = M.prefill(win, date_line, extended=ext)
        rec["meeting_date"] = date
        rec["line_no"] = ln
        # k is this meeting's index among the document's headers, in document order. It is
        # the join key to the item level: `assign_meeting_dates.py` walks the same
        # `header_dates()` output over the same text and stamps each item with the index of
        # the header it falls under. Two meetings on one day in one document (a joint
        # session then the regular one) are distinguishable this way and not by date.
        rec["ordinal"] = k
        out.append(rec)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", help="e.g. 1998-2001 or 2003,2004")
    a = ap.parse_args()
    years = None
    if a.years:
        years = set()
        for part in a.years.split(","):
            if "-" in part:
                lo, hi = part.split("-")
                years.update(range(int(lo), int(hi) + 1))
            else:
                years.add(int(part))

    gold = {}
    con = sqlite3.connect(M.DB)
    for src, ln, data in con.execute(
            "SELECT source_file, line_no, data FROM meetings WHERE status='done'"):
        gold[(src.rsplit(".", 1)[0] if src.endswith(('.html', '.htm')) else src, ln)] = data

    rows, errors = [], []
    docs = list(documents(years))
    for i, (year, path) in enumerate(docs, 1):
        if i % 100 == 0:
            print(f"  {i}/{len(docs)} documents…", flush=True)
        src = str(path.relative_to(MEETING_MINUTES))
        stem = src.rsplit(".", 1)[0] if src.endswith((".html", ".htm")) else src
        for rec in meetings_in(path):
            if "_error" in rec:
                errors.append((src, rec["_error"]))
                continue
            d = rec["meeting_date"]
            era = ("1998-2001" if d[:4] <= "2001"
                   else "2002-2014" if d[:4] <= "2014" else "2015+")
            # a gold row exists when a human confirmed a meeting at (or very near) this line
            verified = any(k[0] == stem and abs(k[1] - rec["line_no"]) < 40 for k in gold)
            rows.append({"source_file": src, "year": year, "meeting_date": d, "era": era,
                         "line_no": rec["line_no"], "ordinal": rec["ordinal"],
                         "hand_verified": int(verified),
                         **{f: rec.get(f) for f in FIELDS}})

    # names are reconciled across the whole corpus at once, as validated
    reduce = M.name_reducer(rows)
    for r in rows:
        M.apply_names(r, reduce)

    with OUT_CSV.open("w", newline="") as fh:
        w = csv.writer(fh)
        head = ["source_file", "year", "meeting_date", "era", "line_no", "ordinal",
                "hand_verified"] + FIELDS
        w.writerow(head)
        for r in rows:
            w.writerow([r["source_file"], r["year"], r["meeting_date"], r["era"],
                        r["line_no"], r["ordinal"], r["hand_verified"]] +
                       ["; ".join(r.get(f) or []) if isinstance(r.get(f), list)
                        else (r.get(f) or "") for f in FIELDS])

    con.execute("DROP TABLE IF EXISTS meetings_all")
    con.execute("CREATE TABLE meetings_all(source_file TEXT, year INTEGER, "
                "meeting_date TEXT, era TEXT, line_no INTEGER, ordinal INTEGER, "
                "hand_verified INTEGER, "
                + ", ".join(f"{f} TEXT" for f in FIELDS) + ")")
    con.executemany(
        "INSERT INTO meetings_all VALUES(" + ",".join("?" * (7 + len(FIELDS))) + ")",
        [[r["source_file"], r["year"], r["meeting_date"], r["era"], r["line_no"],
          r["ordinal"], r["hand_verified"]] +
         ["; ".join(r.get(f) or []) if isinstance(r.get(f), list) else (r.get(f) or "")
          for f in FIELDS] for r in rows])
    con.commit()

    print(f"\nextracted {len(rows)} meetings from {len(docs)} documents")
    print("by era:", dict(Counter(r["era"] for r in rows)))
    print("hand-verified:", sum(r["hand_verified"] for r in rows))
    if errors:
        print(f"read errors: {len(errors)}")
        for e in errors[:5]:
            print("   ", e)
    print("→", OUT_CSV)
    con.close()


if __name__ == "__main__":
    main()
