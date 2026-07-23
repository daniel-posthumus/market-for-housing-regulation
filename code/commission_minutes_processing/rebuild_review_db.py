#!/usr/bin/env python3
"""
rebuild_review_db.py
--------------------
Purpose : Rebuild the labeling review DB with CLEAN (un-merged) blocks after the
          HTML item-parser fix, re-attaching the recovered human labels — and
          preserving 'done' edits — to the correct blocks via content-matching.
Inputs  : re-scraped raw HTML (1998-2014) under MFHR_DATA_ROOT/.../raw/<year>/,
          the current labels.db (source of human labels + modern items to carry over).
Outputs : a fresh review-ready labels.db (default: labeling_app/labels.db.rebuilt).
Author  : Dan Post
Created : 2026-07-03

Notes
-----
Runs in-memory: reads raw HTML, re-parses with the FIXED parser, and writes only the
new SQLite DB — no intermediate tagged files (avoids churn on the Dropbox-synced corpus).

HTML era (1998-2014) items are rebuilt from clean blocks. Modern era (2015+) items are
copied verbatim from the current DB (the modern PDF parser never had the merge bug). All
recovered human labels are 1998-2014, so they re-match onto the new clean HTML blocks.

Label placement: each human label (status prelabeled/flagged/done in the current DB) is
scored against every new block sharing its case number (roll-call/speakers/action), and
attached to the best. `done` labels keep status 'done' (gold — user-confirmed); others
become 'prelabeled' if the match is confident, else 'flagged' for placement review.
Unmatched labels (case number has no block in the re-parse) are reported, not dropped.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import parse_sf_meeting_minutes as PH            # noqa: E402  (fixed HTML parser)
from autoextract import CASE_RE                  # noqa: E402
from extraction_common import coerce_record, empty_record  # noqa: E402
from recover_labels_from_db import score, dedupe_key        # noqa: E402

CUR_DB = HERE / "labeling_app" / "labels.db"
OUT_DB = HERE / "labeling_app" / "labels.db.rebuilt"

MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], 1)}
BLOCK_SPLIT = re.compile(r"<<Project Start>>\s*(.*?)\s*<<Project End>>", re.S)


def norm_case(s) -> str:
    return str(s or "").replace(" ", "").upper()


def to_iso(d: str | None, anchor: str = "") -> str:
    d = (d or "").strip()
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", d)
    if m and m.group(1).lower() in MONTHS:
        return f"{m.group(3)}-{MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", d)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.match(r"(\d{1,2})_(\d{1,2})_(\d{2})", anchor or "")
    if m:
        mo, da, yr = m.groups()
        cent = "19" if int(yr) > 50 else "20"
        return f"{cent}{yr}-{int(mo):02d}-{int(da):02d}"
    return ""


# ── (1) rebuild HTML-era items from clean re-parse ────────────────────────────
def html_items() -> list[dict]:
    items = []
    for ydir in sorted(PH.RAW_DIR.glob("[12][0-9][0-9][0-9]")):
        y = int(ydir.name)
        if not (1998 <= y <= 2014):
            continue
        n_files = 0
        for f in sorted(ydir.glob("*.html")):
            try:
                html = f.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                print(f"  ⚠ read {f.name}: {e}")
                continue
            n_files += 1
            soup = BeautifulSoup(html, "lxml")
            idx = 0
            for anchor, sect in PH.chop_into_meetings(soup):
                text = BeautifulSoup(sect, "lxml").get_text("\n")
                meta = PH.extract_header(text)
                mdate = to_iso(meta.get("date"), anchor)
                for blk in (b.strip() for b in BLOCK_SPLIT.findall(PH.add_project_tags(text))):
                    if not blk:
                        continue
                    m = CASE_RE.search(blk)
                    items.append(dict(year=y, source_file=f"raw/{y}/{f.stem}",
                                      meeting_date=mdate, item_index=idx,
                                      case_number=m.group(1) if m else "",
                                      block_text=blk))
                    idx += 1
        print(f"  {y}: {n_files} pages")
    return items


# ── (2) carry modern items over verbatim (no merge bug there) ─────────────────
def modern_items(cur: sqlite3.Connection) -> list[dict]:
    rows = cur.execute(
        "SELECT year, source_file, meeting_date, item_index, case_number, block_text "
        "FROM items WHERE year >= 2015").fetchall()
    return [dict(year=r[0], source_file=r[1], meeting_date=r[2], item_index=r[3],
                 case_number=r[4], block_text=r[5]) for r in rows]


# ── (3) human labels to re-attach ─────────────────────────────────────────────
def human_labels(cons: list[sqlite3.Connection]) -> list[dict]:
    """Distinct human labels merged across DBs in priority order (first connection
    wins on ties — pass the current DB first so the latest 'done' edits are kept, then
    a backup that still holds labels dropped by an earlier rebuild). 'done' beats
    prelabeled/flagged so a user-confirmed edit is never overwritten by a stale copy."""
    # NO identity dedup: two labels for the SAME case at DIFFERENT meetings are both real
    # gold and must both survive (they content-match to their two different blocks). True
    # duplicates — the same label appearing in two source DBs — collapse later at placement
    # time (they match the same block), where done>prelabeled>flagged and earlier-DB wins.
    out: list[dict] = []
    for rank, cur in enumerate(cons):
        for data_json, status in cur.execute(
                "SELECT data, status FROM labels WHERE status IN ('prelabeled','flagged','done')"):
            try:
                data = json.loads(data_json)
            except Exception:
                continue
            rec = coerce_record(data)
            # skip empty placeholders (e.g. representativeness-sample flags on todo items —
            # no case number / content to match; they're regenerated after the rebuild).
            if not norm_case(rec.get("case_number")):
                continue
            out.append({"rec": rec, "status": status, "rank": rank})
    return out


# ── build the DB ──────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cur", type=Path, default=CUR_DB, help="current labels.db (labels + modern items)")
    ap.add_argument("--also-labels-from", type=Path, action="append", default=[],
                    help="extra label source(s), lower priority than --cur (e.g. a backup "
                         "still holding labels dropped by an earlier rebuild)")
    ap.add_argument("--out", type=Path, default=OUT_DB)
    a = ap.parse_args()

    print(f"raw root: {PH.RAW_DIR}")
    print("re-parsing HTML era (1998-2014) …")
    items = html_items()
    cur = sqlite3.connect(a.cur)
    items += modern_items(cur)
    print(f"items: {len(items)} total "
          f"({sum(1 for i in items if i['year']<=2014)} html + "
          f"{sum(1 for i in items if i['year']>=2015)} modern)")

    # index new blocks by case number for matching
    by_case: dict[str, list[int]] = defaultdict(list)
    for i, it in enumerate(items):
        if it["case_number"]:
            by_case[norm_case(it["case_number"])].append(i)

    extra_cons = [sqlite3.connect(p) for p in a.also_labels_from]
    labels = human_labels([cur] + extra_cons)
    for c in extra_cons:
        c.close()
    cur.close()
    print(f"human labels to place: {len(labels)} "
          f"({sum(1 for l in labels if l['status']=='done')} done)")

    # place each label on the best-matching new block
    prio = {"done": 3, "prelabeled": 2, "flagged": 1}
    placed: dict[int, dict] = {}          # item_idx -> {rec, status, score, note}
    stats = {"confident": 0, "ambiguous": 0, "unmatched": 0, "unmatched_done": [], "collisions": 0}
    for L in labels:
        rec, status = L["rec"], L["status"]
        cands = by_case.get(norm_case(rec.get("case_number")), [])
        if not cands:
            stats["unmatched"] += 1
            if status == "done":
                stats["unmatched_done"].append(rec.get("case_number"))
            continue
        scored = sorted(((score(rec, items[c]["block_text"]), c) for c in cands),
                        key=lambda t: t[0], reverse=True)
        best_s, best_i = scored[0]
        second = scored[1][0] if len(scored) > 1 else float("-inf")
        confident = best_s >= 2.0 and (best_s - second) >= 2.0
        if status == "done":
            new_status, flagged = "done", 0
        elif confident:
            new_status, flagged = "prelabeled", 0
        else:
            new_status, flagged = "flagged", 1
        cand = {"rec": rec, "status": new_status, "flagged": flagged, "score": best_s,
                "rank": L["rank"],
                "note": f"[rebuilt: {new_status}; match {items[best_i]['source_file']}"
                        f"#{items[best_i]['item_index']} score {best_s:.1f}]"}
        cur_owner = placed.get(best_i)
        if cur_owner is None:
            placed[best_i] = cand
        else:                                   # collision: keep done>prelabeled>flagged,
            stats["collisions"] += 1            # then better match, then earlier (current) DB
            if (prio[new_status], best_s, -cand["rank"]) > \
               (prio[cur_owner["status"]], cur_owner["score"], -cur_owner["rank"]):
                placed[best_i] = cand
        if new_status != "done":
            stats["confident" if confident else "ambiguous"] += 1

    # write the DB
    if a.out.exists():
        a.out.unlink()
    con = sqlite3.connect(a.out)
    con.executescript("""
        CREATE TABLE items(
            id INTEGER PRIMARY KEY,
            year INTEGER, source_file TEXT, meeting_date TEXT,
            item_index INTEGER, case_number TEXT, block_text TEXT,
            UNIQUE(source_file, item_index));
        CREATE INDEX ix_items_case ON items(case_number);
        CREATE TABLE labels(
            item_id INTEGER PRIMARY KEY REFERENCES items(id),
            data TEXT, status TEXT DEFAULT 'todo', flagged INTEGER DEFAULT 0,
            notes TEXT DEFAULT '', updated_at TEXT DEFAULT '');
    """)
    label_rows = []
    for i, it in enumerate(items):
        con.execute("INSERT INTO items(id,year,source_file,meeting_date,item_index,"
                    "case_number,block_text) VALUES(?,?,?,?,?,?,?)",
                    (i, it["year"], it["source_file"], it["meeting_date"],
                     it["item_index"], it["case_number"], it["block_text"]))
        p = placed.get(i)
        if p:
            label_rows.append((i, json.dumps(p["rec"], ensure_ascii=False),
                               p["status"], p["flagged"], p["note"], ""))
        else:
            label_rows.append((i, json.dumps(empty_record(), ensure_ascii=False),
                               "todo", 0, "", ""))
    con.executemany("INSERT INTO labels(item_id,data,status,flagged,notes,updated_at) "
                    "VALUES(?,?,?,?,?,?)", label_rows)
    con.commit()
    dist = dict(con.execute("SELECT status, COUNT(*) FROM labels GROUP BY status").fetchall())
    con.close()

    print("\n─── rebuild summary ───")
    print(f"items written        : {len(items)}")
    print(f"labels placed        : {len(placed)}")
    print(f"  confident/prelabeled: {stats['confident']}")
    print(f"  ambiguous/flagged   : {stats['ambiguous']}")
    print(f"  done (preserved)    : {sum(1 for p in placed.values() if p['status']=='done')}")
    print(f"placement collisions : {stats['collisions']} (kept higher priority/score)")
    print(f"unmatched labels     : {stats['unmatched']}"
          + (f"  ⚠ incl DONE: {stats['unmatched_done']}" if stats['unmatched_done'] else ""))
    print(f"status distribution  : {dist}")
    print(f"\n✓ wrote {a.out}")


if __name__ == "__main__":
    main()
