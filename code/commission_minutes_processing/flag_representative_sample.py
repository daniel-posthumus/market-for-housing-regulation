#!/usr/bin/env python3
"""
flag_representative_sample.py
-----------------------------
Purpose : The gold hand-label set is temporally concentrated. To make it representative
          of the whole 1998-present corpus, flag a fixed random sample of N case-bearing
          items per year (that aren't already labeled) so they surface in the app's
          'flagged' queue for hand-labeling.
Inputs  : labels.db
Outputs : sets status='flagged' (with a '[sample: representativeness]' note) on the
          sampled items. --apply to write; default dry-run.
Author  : Dan Post
Created : 2026-07-03

Notes
-----
Samples only items with a case number (the land-use decisions that are the unit of
analysis) and status 'todo' (not already gold). Deterministic: seeded per year, so
re-running picks the same items. Existing 'flagged' recovered-label items are left
alone; the sample joins them in the same filterable queue, tagged so you can tell them
apart by the note.
"""
from __future__ import annotations
import argparse, random, sqlite3, sys
from pathlib import Path

DB = Path(__file__).resolve().parent / "labeling_app" / "labels.db"
NOTE = "[sample: representativeness]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-year", type=int, default=5)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--reset", action="store_true",
                    help="first revert previously-flagged, still-UNLABELED sample items "
                         "back to 'todo' (never touches items you've since marked done)")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    con = sqlite3.connect(DB)

    if a.reset:
        n = con.execute("SELECT COUNT(*) FROM labels WHERE status='flagged' AND notes LIKE ?",
                        (f"%{NOTE}%",)).fetchone()[0]
        print(f"reset: {n} unlabeled sample items → todo")
        if a.apply:
            con.execute("UPDATE labels SET status='todo', flagged=0, notes='' "
                        "WHERE status='flagged' AND notes LIKE ?", (f"%{NOTE}%",))
            con.commit()

    years = [y for (y,) in con.execute("SELECT DISTINCT year FROM items ORDER BY year")]
    picks = []
    for y in years:
        pool = [iid for (iid,) in con.execute(
            "SELECT i.id FROM items i JOIN labels l ON l.item_id=i.id "
            "WHERE i.year=? AND l.status='todo' AND TRIM(i.case_number)<>'' "
            "ORDER BY i.id", (y,))]
        rng = random.Random(a.seed * 100000 + y)     # deterministic per year
        k = min(a.per_year, len(pool))
        chosen = rng.sample(pool, k) if pool else []
        picks.extend((iid, y) for iid in chosen)
        print(f"  {y}: pool={len(pool):>4}  sampled={k}")

    print(f"\ntotal sampled: {len(picks)} across {len(years)} years")
    if a.apply and picks:
        con.executemany(
            "UPDATE labels SET status='flagged', flagged=1, "
            "notes=CASE WHEN notes='' THEN ? ELSE notes||' '||? END "
            "WHERE item_id=?",
            [(NOTE, NOTE, iid) for iid, _ in picks])
        con.commit()
        dist = dict(con.execute("SELECT status, COUNT(*) FROM labels GROUP BY status").fetchall())
        print(f"✓ flagged {len(picks)} sample items. status now: {dist}")
    elif picks:
        print("(dry-run — re-run with --apply to write)")
    con.close()


if __name__ == "__main__":
    main()
