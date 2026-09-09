#!/usr/bin/env python3
"""
build_adjudication.py
---------------------
Purpose : Queue every gold-vs-model disagreement for human adjudication. Some of what the
          bake-off counts as model error is gold error — five are already confirmed — and
          until that share is known, measured accuracy is a lower bound of unknown size and
          the gold set is being used to tune against its own mistakes.
Inputs  : labels.db (gold) and bakeoff/raw_haiku-4.5.json (the predictions).
Outputs : an `adjudications` table in labels.db: one row per disagreeing field, carrying
          both values and a verdict the app writes back.
Author  : Dan Post
Created : 2026-09-06

Notes
-----
Comparison is `compare_field`, not `field_match` — adjudicating a trailing full stop wastes
the reviewer's attention, which is the scarce input here. Only disagreements that survive
the field-aware comparison are queued.

The verdict vocabulary is deliberately three-valued. "gold" and "model" are the useful
outcomes; "both" catches the case where the disagreement revealed that neither value is
right, which is common when a block was mis-split.

`adjudications` stays the store of record for verdicts — it is what `--tally` reads and what
the memo cites. The rows are ALSO mirrored into `review_queue` (spec §7.2), which is the one
queue the app serves, so adjudication, migration review and new labelling can be worked in a
single sitting. The app write-through keeps both in step; `--mirror` re-syncs if they drift.

Usage:
  python build_adjudication.py            # build/refresh the queue, report its size
  python build_adjudication.py --tally    # what the adjudicated verdicts say so far
  python build_adjudication.py --mirror   # push the rows into the unified review_queue
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from extraction_common import (FIELDS, EXTRACTED_FIELDS, is_empty,   # noqa: E402
                               compare_field)
import bakeoff_extract as BX                                    # noqa: E402
import review_queue                                             # noqa: E402

DB = HERE / "labeling_app" / "labels.db"
# WHICH RUN the queue adjudicates. The default is the run the existing 143 unjudged rows
# were built from — a schema-v1, zero-shot pass — so re-running this script does not
# silently re-point settled work at a different model. It is NOT the current configuration:
# `haiku-4.5-g3` is few-shot, schema-v2 and scores 96.6% on the frozen test half against
# this run's 87.9%, so most of what is queued here is a disagreement the live extractor no
# longer has. Pass `--pred bakeoff/raw_haiku-4.5-g3.json --model haiku-4.5-g3` to build the
# queue against the live run; the verdicts already given are keyed by model and are kept.
PRED = HERE / "bakeoff" / "raw_haiku-4.5.json"
MODEL = "haiku-4.5"

SCHEMA = """
CREATE TABLE IF NOT EXISTS adjudications(
    item_id  INTEGER NOT NULL,
    field    TEXT    NOT NULL,
    model    TEXT    NOT NULL,
    gold     TEXT,
    pred     TEXT,
    verdict  TEXT,               -- NULL | 'gold' | 'model' | 'both'
    updated_at TEXT,
    PRIMARY KEY (item_id, field, model));
"""


def build(con, pred_path: Path = PRED, model: str = MODEL):
    preds = {int(k): v for k, v in json.loads(pred_path.read_text()).items()}
    items = BX.gold()
    print(f"adjudicating {model} ({pred_path.name}, {len(preds)} predictions) "
          f"against {len(items)} gold items")
    con.executescript(SCHEMA)
    rows, seen = [], 0
    for it in items:
        p = preds.get(it["id"])
        if not p:
            continue
        for f in FIELDS:
            if is_empty(it["gold"].get(f)):
                continue
            seen += 1
            if compare_field(p, it["gold"], f):
                continue
            rows.append((it["id"], f, model,
                         json.dumps(it["gold"].get(f), ensure_ascii=False),
                         json.dumps(p.get(f), ensure_ascii=False)))
    # never clobber a verdict already given
    con.executemany(
        "INSERT INTO adjudications(item_id,field,model,gold,pred) VALUES(?,?,?,?,?) "
        "ON CONFLICT(item_id,field,model) DO UPDATE SET gold=excluded.gold, pred=excluded.pred",
        rows)
    con.commit()
    done = con.execute("SELECT COUNT(*) FROM adjudications WHERE verdict IS NOT NULL").fetchone()[0]
    print(f"scored field-values: {seen}")
    print(f"disagreements queued: {len(rows)}  ({100*len(rows)/seen:.1f}% of scored values)")
    print(f"  already adjudicated: {done}")
    n_items = con.execute("SELECT COUNT(DISTINCT item_id) FROM adjudications").fetchone()[0]
    print(f"  spread over {n_items} items")
    top = Counter(f for _, f, *_ in rows).most_common(8)
    print("  worst fields: " + ", ".join(f"{f} ({n})" for f, n in top))


def mirror(con, model: str = MODEL):
    """Mirror the adjudication rows into the unified review queue, carrying the model's
    evidence span where the run recorded one — highlighting that span inside the block is
    what makes these fast to judge (§7.2)."""
    evf = HERE / "bakeoff" / f"evidence_{model}.json"
    ev = json.loads(evf.read_text()) if evf.exists() else {}
    rows = con.execute("SELECT item_id, field, model, gold, pred, verdict "
                       "FROM adjudications").fetchall()
    # Two classes of row are stale under schema v2 and are not worth a reviewer's attention:
    #   - fields the model is no longer asked for (`resolution_or_motion_no`, the derived
    #     speaker counts). Judging a disagreement about a field that no longer exists cannot
    #     change any number.
    #   - fields already queued as `field_redefined` for the same item. `project_descr` is
    #     being re-labelled against a new target on all 232 items; adjudicating the OLD
    #     target first would be the same work done twice, against a rule since replaced.
    rq = review_queue.connect()
    redefined = {(i, f) for i, f in rq.execute(
        "SELECT item_id, field FROM review_queue WHERE reason='field_redefined'")}
    rq.close()
    live = [r for r in rows
            if r[1] in EXTRACTED_FIELDS and (r[0], r[1]) not in redefined]
    dropped = len(rows) - len(live)
    q = [dict(item_id=i, field=f, reason="adjudication", model=m,
              detail="gold and model disagree — which is right?",
              old_value=g, proposed=pr,
              evidence=(ev.get(str(i)) or {}).get(f, ""))
         for i, f, m, g, pr, _ in live]
    n = review_queue.enqueue(q)
    # An already-given verdict is carried across so mirroring never re-asks a settled question
    rq = review_queue.connect()
    done = [(v, i, f, m) for i, f, m, _, _, v in live if v]
    rq.executemany("UPDATE review_queue SET verdict=?, status='done' "
                   "WHERE item_id=? AND field=? AND reason='adjudication' AND model=?", done)
    rq.commit()
    rq.close()
    print(f"review_queue: {n} adjudication rows added ({len(rows)} in the table, "
          f"{dropped} stale under schema v2, {len(done)} already settled)")
    if not ev:
        print(f"  no evidence spans yet ({evf.name} not written) — they arrive with the "
              f"next collect(), and the app degrades to showing the block unhighlighted.")


def tally(con):
    rows = con.execute("SELECT field, verdict FROM adjudications WHERE verdict IS NOT NULL").fetchall()
    if not rows:
        print("nothing adjudicated yet.")
        return
    c = Counter(v for _, v in rows)
    tot = sum(c.values())
    print(f"adjudicated {tot} disagreements:")
    for k in ("gold", "model", "both"):
        print(f"  {k:6s} was right: {c[k]:4d} ({100*c[k]/tot:5.1f}%)")
    print("\nby field:")
    byf = Counter((f, v) for f, v in rows)
    for f in sorted({f for f, _ in rows}):
        line = "  ".join(f"{k}={byf[(f,k)]}" for k in ("gold", "model", "both"))
        print(f"  {f:34s} {line}")
    print(f"\n→ if 'model' is a large share, measured accuracy understates the model by "
          f"roughly {100*c['model']/tot:.0f}% of its apparent error rate.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tally", action="store_true")
    ap.add_argument("--mirror", action="store_true")
    ap.add_argument("--pred", type=Path, default=PRED,
                    help="predictions to adjudicate (default: the run the existing queue "
                         "was built from, NOT the current configuration — see the note "
                         "beside PRED)")
    ap.add_argument("--model", default=None,
                    help="model tag for these predictions; defaults to the --pred stem")
    a = ap.parse_args()
    model = a.model or (a.pred.stem[4:] if a.pred.stem.startswith("raw_") else a.pred.stem)
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    if a.tally:
        tally(con)
    elif a.mirror:
        mirror(con, model)
    else:
        build(con, a.pred, model)
        mirror(con, model)
    con.close()


if __name__ == "__main__":
    main()
