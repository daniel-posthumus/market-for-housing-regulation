#!/usr/bin/env python3
"""
merge_split_tails.py
--------------------
Purpose : Put back the ends of blocks that the item splitter cut off. A numbered line is how
          this archive starts an agenda item, so the splitter cuts at one — but a numbered
          line is ALSO how it writes a date ("...on December\\n4. The Project is located...")
          and how it enumerates the modifications attached to an ACTION ("ACTION: Approved
          with recommendations to include:\\n1. Staff Modifications;"). Those cuts truncate
          the item: the vote and the modifications end up in a separate, case-less row.
Inputs  : labels.db.
Outputs : with --apply, the fragments merged back into their parent block and removed.
Author  : Dan Post
Created : 2026-09-07

Notes
-----
Two rules, both deliberately narrow, because the same shape usually IS a real boundary:

  date        the parent's text ends on a MONTH NAME and the next block opens with a day
              number. "…heard at the Historic Preservation Commission on December" + "4. The
              Project is located within a MUG…". Absorbs exactly one fragment.

  action_list the parent's LAST LINE starts with "ACTION:" and ends with a colon, so what
              follows is the enumerated list the action refers to. Absorbs consecutive
              numbered fragments, stopping at anything that looks like an item in its own
              right (a case number, or its own ACTION/AYES line).

A fragment is only ever absorbed if it carries no case number and directly follows its
parent. Everything else — "DIRECTOR'S ANNOUNCEMENTS" followed by "6. REVIEW OF PAST WEEKS'
EVENTS" — is a real boundary and is left alone; an earlier, looser version of this rule
proposed 1,682 merges, nearly all of them wrong.

Usage:
  python merge_split_tails.py            # dry run
  python merge_split_tails.py --apply
"""
from __future__ import annotations

import argparse
import collections
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "labeling_app" / "labels.db"

MONTH = re.compile(r"(?i)\b(?:January|February|March|April|May|June|July|August|September|"
                   r"October|November|December)\s*$")
OPEN = re.compile(r"^\s*(\d{1,2})[a-z]?\.\s")
# What ends an enumerated list: a fragment with its OWN disposition. AYES / NOES / ABSENT /
# RESOLUTION do NOT end it — the last clause of the list is where the parent's vote is
# printed, so stopping there loses exactly the vote the repair exists to recover.
ITEM_OF_ITS_OWN = re.compile(r"(?im)^\s*ACTION\s*:")
MAX_FRAG = {"date": 1400, "action_list": 700, "orphan_vote": 700}


def last_line(t: str) -> str:
    ls = [x for x in (t or "").split("\n") if x.strip()]
    return ls[-1] if ls else ""


def plan(con) -> list:
    rows = [dict(r) for r in con.execute(
        """SELECT i.id, i.source_file, i.item_index, i.case_number, i.block_text, l.status
           FROM items i JOIN labels l ON l.item_id = i.id
           ORDER BY i.source_file, i.item_index""")]
    by = collections.defaultdict(list)
    for r in rows:
        by[r["source_file"]].append(r)

    out = []
    for lst in by.values():
        i = 0
        while i < len(lst) - 1:
            a = lst[i]
            flat = re.sub(r"\s+", " ", a["block_text"] or "").rstrip()
            ll = last_line(a["block_text"]).rstrip()
            # A block that states a disposition but never records the vote has had its tail
            # cut off; the numbered clauses that follow are the rest of it. This is the same
            # break as `action_list`, seen from the other side — needed because repairing a
            # list in one pass can leave the block ending mid-list rather than on "ACTION:".
            orphan_vote = (a["case_number"] or "").strip() and \
                re.search(r"(?im)^\s*ACTION\s*:", a["block_text"] or "") and \
                not re.search(r"(?im)^\s*(?:AYES|NAYS|NOES)\s*:", a["block_text"] or "")
            why = ("date" if MONTH.search(flat) else
                   "action_list" if re.match(r"(?i)^\s*ACTION\s*:", ll) and ll.endswith(":")
                   else "orphan_vote" if orphan_vote else None)
            if not why:
                i += 1
                continue
            frags, j = [], i + 1
            saw_vote = False
            while j < len(lst):
                f = lst[j]
                # Adjacency in DOCUMENT ORDER, not item_index + 1: an earlier repair pass
                # deletes absorbed rows and leaves gaps in the numbering, and a strict
                # successor test then refuses to see the fragment sitting right next to it.
                if ((f["case_number"] or "").strip()
                        or not OPEN.match(f["block_text"] or "")
                        or len(f["block_text"] or "") > MAX_FRAG[why]
                        # Only for `action_list`: there, a fragment with its own ACTION line
                        # is a real item and the list has ended. In a DATE split the fragment
                        # is the remainder of the parent item, so it carries the parent's
                        # ACTION and AYES by definition — disqualifying on that would refuse
                        # to repair exactly the case the rule exists for.
                        or (why == "action_list"
                            and ITEM_OF_ITS_OWN.search(f["block_text"] or ""))):
                    break
                frags.append(f)
                j += 1
                if why == "date":
                    break
                # the list ends at the clause carrying the vote
                if re.search(r"(?im)^\s*(?:AYES|NAYS|NOES)\s*:", f["block_text"] or ""):
                    saw_vote = True
                if saw_vote:
                    break
            if frags:
                out.append((a, frags, why))
                i = j
            else:
                i += 1
    return out


def apply(con, plans) -> int:
    n = 0
    for a, frags, _ in plans:
        merged = (a["block_text"] or "").rstrip() + " " + " ".join(
            (f["block_text"] or "").strip() for f in frags)
        con.execute("UPDATE items SET block_text=? WHERE id=?", (merged, a["id"]))
        ids = [f["id"] for f in frags]
        con.executemany("DELETE FROM labels WHERE item_id=?", [(i,) for i in ids])
        con.executemany("DELETE FROM items WHERE id=?", [(i,) for i in ids])
        n += len(ids)
    con.commit()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    plans = plan(con)
    print(f"parents to repair : {len(plans)}")
    print(f"fragments absorbed: {sum(len(f) for _, f, _ in plans)}")
    print("  by rule   :", dict(collections.Counter(w for _, _, w in plans)))
    print("  parent status:", dict(collections.Counter(p[0]["status"] for p in plans)))
    hand = [p for p in plans if p[0]["status"] in ("done", "flagged", "review")]
    print(f"\n  {len(hand)} of them carry a hand label:")
    for a_, frags, why in hand:
        print(f"    [{why}] item {a_['id']} [{a_['status']}] {a_['case_number']}"
              f"  +{len(frags)} fragment(s), "
              f"{len(a_['block_text'])} -> "
              f"{len(a_['block_text']) + sum(len(f['block_text']) for f in frags)} chars")
    if a.apply:
        print(f"\nmerged {apply(con, plans)} fragments back into {len(plans)} blocks.")
    else:
        print("\n(dry run — re-run with --apply)")
    con.close()


if __name__ == "__main__":
    main()
