#!/usr/bin/env python3
"""
resplit_blocks.py
-----------------
Purpose : Apply the parser's fixed block boundaries to labels.db WITHOUT losing a single
          hand label. Some stored blocks hold more than one agenda item — the archive puts
          a newline between an agenda number and its title, so `AGENDA_NONCASE_RE` used to
          match nothing and a case-less item (an informational presentation, a briefing)
          merged into the land-use item above it. The label belongs to the FIRST item in
          such a block; the rest were never labelled.
Inputs  : labeling_app/labels.db (items.block_text), and the CURRENT boundary rules in
          parse_sf_meeting_minutes.add_project_tags().
Outputs : items re-split in place (--apply): the existing row keeps fragment 1 and its
          label; fragments 2..n become new items seeded 'todo' (or 'not_an_item' when they
          carry no case number). A backup is written next to the DB first.
Author  : Dan Post
Created : 2026-09-06

Notes
-----
It re-splits the STORED TEXT rather than re-parsing the source pages. That is the whole
safety argument: re-parsing renumbers every block on a page and every label would have to
be re-matched to a block by content — which is how labels got attached to sibling cases in
the first place. Splitting stored text touches only blocks that actually split, and an
existing item_id (which is what `labels` keys on) never moves.

`item_index` IS renumbered, because `assign_meeting_dates.py` reads a page's blocks
ORDER BY item_index and aligns them against the page by sequential search — appending new
fragments at the end would break that alignment. Renumbering happens in two passes because
of UNIQUE(source_file, item_index).

Usage:
  python resplit_blocks.py              # dry run: what would change
  python resplit_blocks.py --show 5     # ...and print sample splits
  python resplit_blocks.py --apply
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import parse_sf_meeting_minutes as P                     # noqa: E402
from autoextract import CASE_RE, extract                 # noqa: E402
from extraction_common import coerce_record              # noqa: E402

DB = HERE / "labeling_app" / "labels.db"
ACTION_RE = re.compile(r"(?m)^\s*ACTION\s*:", re.I)


def boundaries(block: str) -> list[int]:
    """Offsets in `block` where a SECOND item starts.

    Deliberately NOT `add_project_tags()`. That function is written for a whole page and
    ends with a last-resort fallback — "if fewer than two boundaries were found, split on
    every numbered line" — which is correct for a page of unheaded text and catastrophic
    for a single block: a block has exactly one header, so the fallback always fires and
    cuts at every "270." Planning Code cite and "(… 1998)" in the prose. Here we ask the
    narrower question the migration actually needs: does a real header appear AFTER the
    start of this block?
    """
    pos = set(m.start() for m in P.CASE_HEADER_RE.finditer(block))
    pos |= set(m.start() for m in P.AGENDA_NONCASE_RE.finditer(block)
               if not P._is_crossref(block, m.start()))
    pos |= set(m.start() for m in P.SECTION_HEADER_RE.finditer(block))
    pos |= set(m.start() for m in P.ADJOURN_RE.finditer(block))
    pos |= set(m.start() for m in P.MINUTES_ADOPTION_RE.finditer(block))
    return sorted(p for p in pos if p > 0)


CASE_HEAD_RE = re.compile(
    r"^[^\S\r\n]*(?:(?:\d+[a-z]?|[a-z])[.)][^\S\r\n]+)?(?:\d{2}|\d{4})[.\-]\d{3,}")


def fragments(block: str) -> list[str]:
    """The block cut at every second-item header. One element == no change."""
    cuts = boundaries(block)
    if not cuts:
        return [block.strip()]
    bounds = [0] + cuts + [len(block)]
    out = [b for b in (block[bounds[i]:bounds[i + 1]].strip()
                       for i in range(len(bounds) - 1)) if b]
    # Never cut an item away from its own disposition. On 11 blocks the first cut lands
    # ABOVE the ACTION line, which would leave a case-headed item with no recorded
    # outcome — a worse error than leaving two items merged, because the merge is visible
    # and the missing disposition is not. Those stay whole and keep their `_block` flag.
    if (CASE_HEAD_RE.match(block.lstrip("\n")) and ACTION_RE.search(block)
            and not ACTION_RE.search(out[0])):
        return [block.strip()]
    return out


def plan(con) -> tuple[dict, list]:
    """{source_file: [(item_id|None, index_in_doc, text)]} for pages that change."""
    rows = con.execute("SELECT id, source_file, item_index, block_text, meeting_date, year, "
                       "meeting_ordinal FROM items ORDER BY source_file, item_index").fetchall()
    by_src = defaultdict(list)
    for r in rows:
        by_src[r[1]].append(r)
    changed, samples = {}, []
    for src, items in by_src.items():
        ordered, split_any = [], False
        for iid, _s, _idx, text, mdate, year, mord in items:
            frs = fragments(text)
            if len(frs) > 1:
                split_any = True
                if len(samples) < 40:
                    samples.append((iid, text, frs))
            # fragment 1 stays on the existing row; the rest become new items
            ordered.append((iid, frs[0], mdate, year, mord))
            for extra in frs[1:]:
                ordered.append((None, extra, mdate, year, mord))
        if split_any:
            changed[src] = ordered
    return changed, samples


# ── mode 2: one item, several independently-numbered decisions ────────────────
# A block can hold ONE case and TWO Commission votes — a Planning Code text amendment and
# the zoning map amendment that goes with it, each carried by its own motion with its own
# roll call. Those are two requests heard, so they are two rows. The tell is that each
# ACTION line has a MOTION/RESOLUTION number of its own before the next ACTION; a failed
# motion followed by a successful one (199 blocks) does NOT have that shape and stays whole.
ACT_RE = re.compile(r"(?im)^[^\S\r\n]*ACTION[^\S\r\n]*:")
MOTION_RE = re.compile(r"(?im)^[^\S\r\n]*(?:MOTION|RESOLUTION)[^\S\r\n]*"
                       r"(?:No\.?)?[^\S\r\n]*:?[^\S\r\n]*\d{3,}")


def decision_cuts(block: str) -> list[int]:
    """Offsets of the 2nd..nth decision in a block that records several."""
    acts = [m.start() for m in ACT_RE.finditer(block)]
    if len(acts) < 2:
        return []
    own = [s for k, s in enumerate(acts)
           if MOTION_RE.search(block[s:acts[k + 1] if k + 1 < len(acts) else len(block)])]
    return own[1:] if len(own) >= 2 else []


def decision_fragments(block: str) -> list[str]:
    cuts = decision_cuts(block)
    if not cuts:
        return [block.strip()]
    bounds = [0] + cuts + [len(block)]
    return [b for b in (block[bounds[i]:bounds[i + 1]].strip()
                        for i in range(len(bounds) - 1)) if b]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--show", type=int, default=0)
    ap.add_argument("--decisions", action="store_true",
                    help="split blocks that record several separately-numbered decisions")
    a = ap.parse_args()
    if a.decisions:
        return split_decisions(a.apply)

    con = sqlite3.connect(DB)
    con.row_factory = None
    changed, samples = plan(con)

    n_new = sum(1 for v in changed.values() for iid, *_ in v if iid is None)
    n_split = len(samples) if len(samples) < 40 else None
    split_ids = [iid for v in changed.values() for iid, *_ in v if iid is not None]
    # how many of the splits carry a hand label?
    lab = {i: (s, d) for i, s, d in con.execute(
        "SELECT item_id, status, data FROM labels WHERE status IN ('done','flagged','review')")}
    truly_split = set()
    for src, ordered in changed.items():
        prev = None
        for iid, text, *_ in ordered:
            if iid is None and prev is not None:
                truly_split.add(prev)
            prev = iid if iid is not None else prev
    labelled_hit = sorted(i for i in truly_split if i in lab)

    print(f"pages affected      : {len(changed)}")
    print(f"blocks that re-split: {len(truly_split)}")
    print(f"new items created   : {n_new}")
    print(f"of the re-split blocks, {len(labelled_hit)} carry a hand label:")
    for i in labelled_hit:
        st, d = lab[i]
        cn = json.loads(d).get("case_number") if d else ""
        print(f"    id={i:<6} [{st}] case={cn!r}")

    if a.show:
        for iid, text, frs in samples[:a.show]:
            print(f"\n--- id={iid}: {len(text)} chars, "
                  f"{len(ACTION_RE.findall(text))} ACTION -> {len(frs)} fragments")
            for k, f in enumerate(frs, 1):
                print(f"    [{k}] {len(f):5d} chars, {len(ACTION_RE.findall(f))} ACTION | "
                      f"{re.sub(r'[\s]+', ' ', f)[:88]}")

    if not a.apply:
        print("\n(dry run — re-run with --apply to write)")
        return

    bak = DB.with_suffix(f".db.presplit_{datetime.now():%Y%m%d_%H%M%S}.bak")
    shutil.copy2(DB, bak)
    print(f"\nbackup: {bak.name}")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    created = 0
    for src, ordered in changed.items():
        # two passes: UNIQUE(source_file,item_index) makes an in-place renumber collide
        con.execute("UPDATE items SET item_index = item_index + 1000000 WHERE source_file=?", (src,))
        for new_idx, (iid, text, mdate, year, mord) in enumerate(ordered):
            cm = CASE_RE.search(text)
            case = cm.group(1) if cm else ""
            if iid is not None:
                con.execute("UPDATE items SET item_index=?, block_text=?, case_number=? WHERE id=?",
                            (new_idx, text, case, iid))
            else:
                cur = con.execute(
                    "INSERT INTO items(year,source_file,meeting_date,item_index,case_number,"
                    "block_text,meeting_ordinal) VALUES(?,?,?,?,?,?,?)",
                    (year, src, mdate, new_idx, case, text, mord))
                # a fragment with no case number is agenda scaffolding, by the same rule
                # that excluded 6,940 blocks; anything else joins the labelling queue.
                status = "todo" if case else "not_an_item"
                rec = coerce_record(extract(text)) if case else coerce_record({})
                note = "" if case else "[not an item] no case number — agenda scaffolding, excluded"
                con.execute("INSERT INTO labels(item_id,data,status,notes,updated_at) "
                            "VALUES(?,?,?,?,?)",
                            (cur.lastrowid, json.dumps(rec, ensure_ascii=False), status, note, now))
                created += 1
    con.commit()
    print(f"✓ re-split {len(truly_split)} blocks; created {created} new items")
    print("  status:", dict(con.execute("SELECT status,COUNT(*) FROM labels GROUP BY status")))
    con.close()


def split_decisions(apply: bool):
    """Give each separately-numbered decision its own row, keeping the label on the first."""
    con = sqlite3.connect(DB)
    rows = con.execute("""SELECT i.id, i.source_file, i.item_index, i.block_text, i.meeting_date,
                                 i.year, i.meeting_ordinal, i.case_number, l.status, l.data
                          FROM items i JOIN labels l ON l.item_id = i.id
                          WHERE l.status != 'not_an_item'""").fetchall()
    todo = [r for r in rows if len(decision_fragments(r[3])) > 1]
    print(f"blocks recording several decisions: {len(todo)}")
    for r in todo:
        frs = decision_fragments(r[3])
        print(f"  id={r[0]} {r[7] or '(no case)'} [{r[8]}] {len(r[3])} chars -> {len(frs)} decisions "
              f"{[len(f) for f in frs]}")
    if not apply:
        print("\n(dry run — add --apply to write)")
        return
    bak = DB.with_suffix(f".db.predecisions_{datetime.now():%Y%m%d_%H%M%S}.bak")
    shutil.copy2(DB, bak)
    print(f"\nbackup: {bak.name}")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    created = 0
    for iid, src, idx, text, mdate, year, mord, case, status, data in todo:
        frs = decision_fragments(text)
        con.execute("UPDATE items SET block_text=? WHERE id=?", (frs[0], iid))
        # Make room after this item so document order survives. Two passes: a single
        # ascending UPDATE collides with itself against UNIQUE(source_file,item_index).
        shift = len(frs) - 1
        con.execute("UPDATE items SET item_index = item_index + 1000000 "
                    "WHERE source_file=? AND item_index > ?", (src, idx))
        con.execute("UPDATE items SET item_index = item_index - 1000000 + ? "
                    "WHERE source_file=? AND item_index >= 1000000", (shift, src))
        parent = json.loads(data) if data else {}
        for k, extra in enumerate(frs[1:], 1):
            cm = CASE_RE.search(extra)
            # the 2nd decision rarely repeats the case header — it is the same case
            ecase = cm.group(1) if cm else (case or "")
            seed = coerce_record({**{f: parent.get(f) for f in
                                     ("case_number", "request_type", "project_address",
                                      "assessor_block", "lot_number", "type_district",
                                      "type_district_descr", "height_and_bulk_district",
                                      "special_use_district", "project_descr", "staff_planner")},
                                  **{k2: v for k2, v in extract(extra).items()
                                     if k2 in ("action", "ayes", "noes", "absent", "excused",
                                               "recused", "resolution_or_motion_no",
                                               "conditions_imposed", "project_modified")}})
            cur = con.execute(
                "INSERT INTO items(year,source_file,meeting_date,item_index,case_number,"
                "block_text,meeting_ordinal) VALUES(?,?,?,?,?,?,?)",
                (year, src, mdate, idx + k, ecase, extra, mord))
            con.execute("INSERT INTO labels(item_id,data,status,notes,updated_at) VALUES(?,?,?,?,?)",
                        (cur.lastrowid, json.dumps(seed, ensure_ascii=False), "todo",
                         f"[review] separate decision split out of item {iid} — same case, its own "
                         f"motion and roll call; identity fields copied from the parent, confirm the "
                         f"disposition [CHECK: action,resolution_or_motion_no]", now))
            created += 1
    con.commit()
    print(f"✓ split {len(todo)} blocks; created {created} new items")
    con.close()


if __name__ == "__main__":
    main()
