#!/usr/bin/env python3
"""
resolve_speaker_refs.py
-----------------------
Purpose : Resolve the speaker cross-references. 1,402 corpus items (8.6%) record their
          speakers as "Same as those listed for item 22" rather than naming anyone, and a
          label that leaves them empty is right but incomplete — those hearings HAD public
          testimony, and dropping it biases any count of who shows up.
Inputs  : labels.db (items + labels).
Outputs : a report; with --apply, the referenced item's speakers copied onto the referring
          item, with the source recorded in `notes`.
Author  : Dan Post
Created : 2026-09-07

Notes
-----
POST-PROCESSING, deliberately: this is not part of extraction and the model is told to leave
such items empty (see the `speakers` help). A cross-reference is not a person — storing the
sentence as a speaker name inflates every derived count by one — so the reference lives in
the block text, which is where this script reads it from.

The referenced item is found by AGENDA NUMBER within the SAME SOURCE DOCUMENT. The agenda
number was dropped from the schema (it carries no analytic signal of its own), but it is
still printed at the head of every block, which is all this needs. A reference that resolves
to a block with no speakers of its own is reported, not applied: chains ("same as item 22",
whose block also says "same as item 15") are resolved iteratively, and a cycle is dropped.

Usage:
  python resolve_speaker_refs.py            # report what would resolve
  python resolve_speaker_refs.py --apply    # copy the speakers across
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from extraction_common import coerce_record                      # noqa: E402
from normalize import normalize_record                           # noqa: E402

DB = HERE / "labeling_app" / "labels.db"

# "Same as those listed for item 22." / "same as item 12a" / "Same as spoke under Item 28a"
REF = re.compile(r"same\s+as\s+(?:those\s+)?(?:listed\s+|spoke\s+|speakers\s+)?"
                 r"(?:for\s+|under\s+|on\s+)?item\s*#?\s*(\d{1,2}\s*[a-z]?)", re.I)
# The agenda number at the head of a block: "22.", "15a.", "17b."
AGENDA = re.compile(r"^\s*(\d{1,2}\s*[a-z]?)\s*\.")


def agenda_of(block: str) -> str:
    m = AGENDA.match(re.sub(r"\s+", " ", (block or "").lstrip()))
    return re.sub(r"\s+", "", m.group(1)).lower() if m else ""


def reference_in(block: str) -> str:
    m = REF.search(block or "")
    return re.sub(r"\s+", "", m.group(1)).lower() if m else ""


def load(db: Path = DB) -> list[dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute("""SELECT i.id, i.source_file, i.item_index, i.block_text,
                                 l.data, l.status
                          FROM items i LEFT JOIN labels l ON l.item_id = i.id
                          WHERE i.case_number != ''""").fetchall()
    con.close()
    return [dict(r) for r in rows]


def speakers_of(rec_json) -> list:
    if not rec_json:
        return []
    try:
        return coerce_record(json.loads(rec_json)).get("speakers") or []
    except (ValueError, TypeError):
        return []


def resolve(rows: list[dict]) -> tuple[list, Counter]:
    by_doc: dict[str, dict[str, dict]] = {}
    for r in rows:
        a = agenda_of(r["block_text"])
        if a:
            # A document can repeat an agenda number when an item is heard twice; the FIRST
            # occurrence is the one the reference means, since a later item points backwards.
            by_doc.setdefault(r["source_file"], {}).setdefault(a, r)

    out, tally = [], Counter()
    for r in rows:
        want = reference_in(r["block_text"])
        if not want:
            continue
        if speakers_of(r["data"]):
            tally["already has speakers of its own"] += 1
            continue
        seen, target, hops = {r["id"]}, by_doc.get(r["source_file"], {}).get(want), 0
        while target and not speakers_of(target["data"]) and hops < 5:
            nxt = reference_in(target["block_text"])
            if not nxt or target["id"] in seen:
                break
            seen.add(target["id"])
            target = by_doc.get(target["source_file"], {}).get(nxt)
            hops += 1
        if not target:
            tally["no block with that agenda number in the document"] += 1
            continue
        sp = speakers_of(target["data"])
        if not sp:
            tally["target found but it has no speakers labelled either"] += 1
            continue
        tally["resolved"] += 1
        out.append({"item_id": r["id"], "agenda_ref": want, "from_item": target["id"],
                    "hops": hops, "speakers": sp})
    return out, tally


def apply(hits: list, db: Path = DB) -> int:
    con = sqlite3.connect(db)
    n = 0
    for h in hits:
        row = con.execute("SELECT data FROM labels WHERE item_id=?",
                          (h["item_id"],)).fetchone()
        rec = coerce_record(json.loads(row[0]) if row and row[0] else {})
        rec["speakers"] = h["speakers"]
        con.execute("UPDATE labels SET data=?, notes=TRIM(COALESCE(notes,'') || ?) "
                    "WHERE item_id=?",
                    (json.dumps(normalize_record(rec), ensure_ascii=False),
                     f" [speakers copied from item {h['from_item']} "
                     f"(agenda {h['agenda_ref']})]", h["item_id"]))
        n += 1
    con.commit()
    con.close()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    # Filling an unlabelled item pre-empts the extraction that has not happened yet: the
    # corpus pass would then be scored against a value this script wrote. By default only
    # items that already carry a label are touched.
    ap.add_argument("--include-unlabelled", action="store_true",
                    help="also fill items with no label yet (use after the corpus pass)")
    a = ap.parse_args()
    rows = load()
    labelled = {r["id"] for r in rows
                if (r["status"] or "") in ("done", "flagged", "review")}
    hits, tally = resolve(rows)
    total = sum(tally.values())
    print(f"items whose speakers are a cross-reference: {total}")
    for k, v in tally.most_common():
        print(f"   {v:5d}  {k}  ({100*v/total:.1f}%)")
    if hits:
        print("\nexamples:")
        for h in hits[:6]:
            names = ", ".join(s["name"] or "(anon)" for s in h["speakers"][:4])
            print(f"   item {h['item_id']:6d} → agenda {h['agenda_ref']:>4s} "
                  f"= item {h['from_item']:6d}: {names}")
    todo = [h for h in hits if h["item_id"] not in labelled]
    if not a.include_unlabelled:
        hits = [h for h in hits if h["item_id"] in labelled]
        if todo:
            print(f"\n({len(todo)} more would resolve onto items with no label yet — "
                  f"those wait for the corpus pass; --include-unlabelled overrides.)")
    if a.apply:
        print(f"\napplied to {apply(hits)} items.")
    else:
        print(f"\n{len(hits)} would be filled. Re-run with --apply.")


if __name__ == "__main__":
    main()
