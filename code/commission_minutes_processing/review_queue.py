#!/usr/bin/env python3
"""
review_queue.py
---------------
Purpose : ONE queue for every kind of re-review the schema v2 migration creates, so the work
          can be done in a single sitting instead of three parallel flows (spec §7.2).
Inputs  : labels.db.
Outputs : a `review_queue` table in that file, plus the helpers the app and the migration
          script both use.
Author  : Dan Post
Created : 2026-09-07

Notes
-----
Queue items are FIELD-LEVEL wherever possible. There is no reason to re-review all 28 fields
on 232 items; only the fields the schema change actually touches need re-review, and a
whole-item row (field IS NULL) is reserved for the genuinely new items.

`sort_key` exists so that same-field work lands consecutively — labelling 232 `project_descr`
values in a row is faster and more consistent than context-switching per item. It is stored
rather than computed in the query so the ordering is stable and inspectable.

The four reasons:
  field_redefined      the field's target changed under v2; old label shown, new rule inline
  migration_ambiguous  the migration could not derive a v2 value; proposal shown, accept/edit
  adjudication         a gold-vs-model disagreement; three-way verdict
  new_item             a modern item with no hand label yet; full form, model pre-fill
"""
from __future__ import annotations

import datetime
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DB = HERE / "labeling_app" / "labels.db"

REASONS = ("field_redefined", "migration_ambiguous", "adjudication", "new_item")
_RANK = {r: i for i, r in enumerate(REASONS)}

# Within a reason, work the fields in the order that gets the most value soonest rather than
# alphabetically. `project_descr` is 232 near-mechanical accepts and is what the schema
# change actually costs; `project_address` is validation-only (§2.4) and can wait, so it
# sits at the back where an unfinished session leaves the least damage.
FIELD_ORDER = ["project_descr", "action_instrument", "action_instrument_no", "speakers",
               "type_district", "type_district_descr", "special_use_district",
               "conditions_imposed", "project_modified", "modifications",
               "request_type", "action", "project_address"]
_FRANK = {f: i for i, f in enumerate(FIELD_ORDER)}

DDL = """
CREATE TABLE IF NOT EXISTS review_queue(
    id         INTEGER PRIMARY KEY,
    item_id    INTEGER NOT NULL REFERENCES items(id),
    field      TEXT,             -- NULL only for whole-item work (new_item)
    reason     TEXT NOT NULL,    -- see REASONS
    detail     TEXT,             -- why this is in the queue, shown inline
    old_value  TEXT,             -- v1 label, or the gold value in an adjudication
    proposed   TEXT,             -- v2 proposal, or the model value in an adjudication
    evidence   TEXT,             -- the model's evidence span, where one exists
    model      TEXT,
    rule       TEXT,             -- how `proposed` was derived (request_for, opener, ...)
    status     TEXT DEFAULT 'open',   -- open | done | skipped
    verdict    TEXT,             -- adjudication only: gold | model | both
    resolved   TEXT,             -- what the labeller settled on, JSON-encoded
    sort_key   TEXT,
    updated_at TEXT,
    UNIQUE(item_id, field, reason, model)
);
CREATE INDEX IF NOT EXISTS ix_rq_open ON review_queue(status, sort_key);
CREATE INDEX IF NOT EXISTS ix_rq_item ON review_queue(item_id);
"""


def connect(db: Path = DB) -> sqlite3.Connection:
    con = sqlite3.connect(db)
    con.executescript(DDL)
    return con


def sort_key(reason: str, field: str | None, item_id: int) -> str:
    return "%d:%02d:%s:%07d" % (_RANK.get(reason, 9), _FRANK.get(field, 90),
                                field or "~", item_id)


def enqueue(rows: list[dict], db: Path = DB, replace_reason: str | None = None) -> int:
    """Insert queue rows. Rows already present are left alone — re-running the migration
    must never wipe a verdict a human has already given.

    `replace_reason` clears the OPEN rows of one reason first, which is how a re-run of the
    migration replaces stale proposals without touching finished work.
    """
    con = connect(db)
    if replace_reason:
        con.execute("DELETE FROM review_queue WHERE reason=? AND status='open'",
                    (replace_reason,))
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    n = 0
    for r in rows:
        cur = con.execute(
            """INSERT OR IGNORE INTO review_queue
               (item_id, field, reason, detail, old_value, proposed, evidence, model,
                rule, sort_key, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (r["item_id"], r.get("field"), r["reason"], r.get("detail", ""),
             _enc(r.get("old_value")), _enc(r.get("proposed")), r.get("evidence", ""),
             r.get("model", ""), r.get("rule", ""),
             sort_key(r["reason"], r.get("field"), r["item_id"]), now))
        n += cur.rowcount
    con.commit()
    con.close()
    return n


def _enc(v):
    return v if v is None or isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def seed_new_items(db: Path = DB) -> int:
    """Queue the modern-era draw as whole-item work (§7.4).

    These carry a model PRE-FILL, not a hand label, and the pre-fill was produced with
    HTML-era examples because no modern examples exist yet. The detail says so: the labeller
    should not anchor on a pre-fill that is era-mismatched by construction.
    """
    con = connect(db)
    rows = con.execute("""SELECT i.id, i.year FROM items i JOIN labels l ON l.item_id = i.id
                          WHERE l.status = 'review'
                            AND l.notes LIKE '%NOT yet labelled%'""").fetchall()
    con.close()
    return enqueue([dict(item_id=i, field=None, reason="new_item",
                         detail=f"modern-era ({y}) item, never hand-labelled. The pre-fill "
                                f"was produced from HTML-era examples — era-mismatched by "
                                f"construction, so trust it less than usual.",
                         rule="prefill_era_mismatch")
                    for i, y in rows], db=db)


def tally(db: Path = DB) -> list[tuple]:
    con = connect(db)
    rows = con.execute("""SELECT reason, COALESCE(field,'(whole item)'), status, COUNT(*)
                          FROM review_queue GROUP BY 1,2,3
                          ORDER BY MIN(sort_key)""").fetchall()
    con.close()
    return rows


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-new-items", action="store_true",
                    help="queue the modern-era draw as whole-item work")
    if ap.parse_args().seed_new_items:
        print(f"queued {seed_new_items()} new_item rows")
    rows = tally()
    if not rows:
        print("review_queue is empty.")
    else:
        print("%-20s %-34s %-8s %6s" % ("reason", "field", "status", "n"))
        for r in rows:
            print("%-20s %-34s %-8s %6d" % r)
        print("%-20s %-34s %-8s %6d" % ("TOTAL", "", "", sum(r[3] for r in rows)))
        print("%-20s %-34s %-8s %6d"
              % ("  of which open", "", "", sum(r[3] for r in rows if r[2] == "open")))
