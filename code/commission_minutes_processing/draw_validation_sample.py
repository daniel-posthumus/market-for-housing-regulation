#!/usr/bin/env python3
"""
draw_validation_sample.py
-------------------------
Purpose : Draw a validation sample of documents for meeting-level hand-labelling, weighted
          toward the years where the extractor's automatic anomaly rate is highest, and
          pre-mark each document's meeting boundaries by machine so the labeller only has to
          confirm the FIELDS.
Inputs  : date_boundary_app/date_gold.db (the document queue), the per-year anomaly rates
          measured by the corpus-wide sweep (recorded below).
Outputs : rows in `boundaries` tagged source='machine', and the drawn documents flagged
          in_sample=1 with the given sample_round.
Author  : Dan Post
Created : 2026-09-04

Notes
-----
Why weight rather than draw uniformly: a uniform draw spends most of its slots on years the
sweep already shows to be clean (2008-2014 run 0-13% anomalous) and few on the years that
carry the residual risk (1999 at 45%, 2004 at 35%, 2000-2001 at 29%). Weighting by the
measured rate points the same ten documents at the actual uncertainty.

Why machine-marked boundaries: this is the configuration that would run over all 818
meetings with no human marking — boundary detected, then snapped to the body header. Rounds
1-3 all used windows a human anchored, which is exactly how the anchoring gap stayed hidden
until the extractor was run over meetings nobody had marked. Machine marks are tagged
`source='machine'` so they can never be confused with the hand-marked date gold set.

The modern era (2015+) carries no measured rate — the sweep covered the HTML era, where the
item table gives a denominator — so it is given the mean weight rather than treated as
clean, and two slots are reserved for it as a control.
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "date_boundary_app"))
import assign_meeting_dates as AD                    # noqa: E402
import meeting_headers as M                          # noqa: E402
import app as BA                                     # noqa: E402

# Any-anomaly rate per year, from the corpus-wide sweep over all 818 detected meetings
# (empty roll call, empty staff, run-on name, unset type, missing time, unrecognised venue).
ANOMALY_RATE = {
    1998: .122, 1999: .450, 2000: .286, 2001: .286, 2002: .122, 2003: .083,
    2004: .354, 2005: .185, 2006: .137, 2007: .091, 2008: .034, 2009: .061,
    2010: .022, 2011: .132, 2012: .094, 2013: .109, 2014: .000,
}
FLOOR = 0.02                 # every year keeps some chance of being drawn
MODERN_SLOTS = 2             # reserved for 2015+, whose rate is unmeasured


def draw(con, n: int, seed: int, sample_round: int) -> list[str]:
    pool = [(r[0], r[1]) for r in con.execute(
        "SELECT source_file, year FROM docs WHERE in_sample=0 ORDER BY source_file")]
    mean_rate = sum(ANOMALY_RATE.values()) / len(ANOMALY_RATE)
    rng = random.Random(seed)

    modern = [s for s, y in pool if y >= 2015]
    legacy = [(s, y) for s, y in pool if y < 2015]

    picked = rng.sample(modern, min(MODERN_SLOTS, len(modern)))

    # Allocate slots by YEAR, then pick a document within the chosen year. Weighting
    # documents directly does not work: 1999 is the single most anomalous year (45%) but
    # holds only eleven unmarked documents, so a per-document weighting lets a large, clean
    # year like 2007 (61 documents at 9%) outvote it and the draw ends up near uniform.
    by_year: dict[int, list[str]] = {}
    for s, y in legacy:
        by_year.setdefault(y, []).append(s)
    for _ in range(n - len(picked)):
        years = [y for y, docs in by_year.items() if docs]
        if not years:
            break
        w = [ANOMALY_RATE.get(y, mean_rate) + FLOOR for y in years]
        y = rng.choices(years, weights=w, k=1)[0]
        picked.append(by_year[y].pop(rng.randrange(len(by_year[y]))))
    return sorted(picked)


def premark(con, srcs: list[str], sample_round: int) -> int:
    """Detect each document's meeting boundaries and write them as machine marks."""
    n = 0
    for src in srcs:
        path = BA.doc_path(src)
        if not path:
            print(f"  ⚠ no file for {src}")
            continue
        text = BA.doc_text(path)
        lines = text.split("\n")
        starts, off = [], 0
        for line in lines:
            starts.append(off)
            off += len(line) + 1
        hdrs = AD.header_dates(text)
        if not hdrs:
            fb = AD.title_date(text) or AD.stem_date(src)
            hdrs = [(0, fb)] if fb else []
        raw_lines = [max(i for i, s in enumerate(starts) if s <= o) for o, _d in hdrs]
        for l0, (_o, d) in zip(raw_lines, hdrs):
            later = [x for x in raw_lines if x > l0]
            ln = M.snap_to_header(lines, l0, later[0] if later else None)
            con.execute("INSERT OR REPLACE INTO boundaries"
                        "(source_file,line_no,meeting_date,span,source) VALUES(?,?,?,1,'machine')",
                        (src, ln, d))
            n += 1
        con.execute("UPDATE docs SET in_sample=1, sample_round=?, status='done' "
                    "WHERE source_file=?", (sample_round, src))
    con.commit()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=10)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--round", type=int, required=True, dest="sample_round")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    con = sqlite3.connect(M.DB)
    picked = draw(con, a.n, a.seed, a.sample_round)
    years = collections.Counter(int(s.split("/")[1]) for s in picked)
    mean_rate = sum(ANOMALY_RATE.values()) / len(ANOMALY_RATE)
    print(f"drew {len(picked)} documents (seed {a.seed}), weighted by measured anomaly rate")
    for s in picked:
        y = int(s.split("/")[1])
        r = ANOMALY_RATE.get(y)
        print(f"   {s:46s}  {y}  anomaly rate "
              + (f"{r*100:4.1f}%" if r is not None else "  n/a (modern)"))
    print("\nyear mix:", dict(sorted(years.items())))
    exp = sum(ANOMALY_RATE.get(int(s.split('/')[1]), mean_rate) for s in picked) / len(picked)
    print(f"mean anomaly rate of the draw: {exp*100:.1f}%  "
          f"(uniform draw would average {mean_rate*100:.1f}%)")

    if not a.apply:
        print("\n(dry run — re-run with --apply to write the machine boundaries)")
        return
    n = premark(con, picked, a.sample_round)
    print(f"\n✓ wrote {n} machine-derived boundaries across {len(picked)} documents")
    con.close()


if __name__ == "__main__":
    main()
