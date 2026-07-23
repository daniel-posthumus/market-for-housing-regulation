#!/usr/bin/env python3
"""
recover_labels_from_db.py
-------------------------
Purpose : Reconstruct the lost {year}_labeled.json hand-label files from the
          only surviving copy of the labels — the SQLite labeling DB — after the
          original corpus + labels were lost with an old machine.
Inputs  : labels.db.qa.bak  (the CLEAN, pre-QA-backfill labeling store; the
          post-backfill labels.db has fields corrupted by a wrong-block backfill)
Outputs : <out>/{year}_labeled.json   recovered hand-labels, grouped by year
          <out>/recovery_report.md     what was recovered / ambiguous / dropped
          <out>/recovery_provenance.csv per-record: matched block, score, flags
Author  : Dan Post
Created : 2026-07-02

Notes
-----
Why this is needed: `ingest.py` joined hand-labels to blocks by `case_number`,
which is NOT unique (a case recurs on the continuance calendar and at its
hearing). So a stored human label can sit on the wrong block, and its
roll-call/speakers match a *different* block with the same case number.

Recovery strategy (read-only; never mutates the DB):
  1. Pull every human-origin label (status prelabeled/done) from the clean backup.
  2. Dedupe true fan-out copies: key = the label JSON with `meeting_date` blanked
     (identical labels that differ only by an ingest-injected date collapse to one;
     genuinely different labels for different meetings stay separate).
  3. For each distinct record, score every candidate block sharing its case number
     by how well the block's AYES/ABSENT/NOES/RECUSED/EXCUSED/SPEAKERS/ACTION lines
     match the record, and place the record on the best block — recovering the
     correct meeting_date, source_file and item_index.
  4. Group by the matched block's year → {year}_labeled.json. Human field values are
     preserved verbatim; meeting_date is filled from the matched block only when the
     human left it blank (and any disagreement is flagged, never silently changed).

Any labels the *original* ingest silently overwrote before this DB was written are
gone — unrecoverable from here, and reported as a known floor on loss.

Usage
-----
  python recover_labels_from_db.py                       # → ./_label_recovery/
  python recover_labels_from_db.py --db PATH --out DIR
  python recover_labels_from_db.py --explain 98.274C     # show the match for one case
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from extraction_common import coerce_record, empty_record, FIELDS  # noqa: E402

DEFAULT_DB = HERE / "labeling_app" / "labels.db.qa.bak"
DEFAULT_OUT = HERE / "_label_recovery"

# roll-call / speaker lines we can fingerprint a block by
ROLL_LINES = {
    "ayes": r"AYES?", "noes": r"NO(?:ES|S)?", "absent": r"ABSENT",
    "recused": r"RECUSED", "excused": r"EXCUSED",
}


# ── block parsing ─────────────────────────────────────────────────────────────
def _names_after(label_re: str, block: str) -> set[str]:
    """Surnames on a labelled roll-call/speaker line, lower-cased.
    Grabs text after e.g. 'AYES:' up to the next ALL-CAPS label or blank run."""
    m = re.search(label_re + r"\s*:\s*(.*?)(?:\n\s*\n|\n\s*[A-Z]{3,}\s*:|\Z)",
                  block, re.S | re.I)
    if not m:
        return set()
    chunk = m.group(1)
    if re.search(r"\bnone\b", chunk, re.I):
        return set()
    parts = re.split(r"[,\n;]+", chunk)
    out = set()
    for p in parts:
        p = re.sub(r"\(.*?\)", "", p)            # drop "(– topic)" etc.
        p = re.sub(r"[^A-Za-z.\- ]", "", p).strip()
        if not p:
            continue
        surname = p.split()[-1].lower()          # last token = surname
        if len(surname) >= 2:
            out.add(surname)
    return out


def _action_text(block: str) -> str:
    m = re.search(r"ACTION\s*:\s*(.+)", block, re.I)
    return m.group(1).strip().lower() if m else ""


def _rec_surnames(vals) -> set[str]:
    out = set()
    for v in (vals or []):
        v = re.sub(r"\(.*?\)", "", str(v))
        v = re.sub(r"[^A-Za-z.\- ]", "", v).strip()
        if v:
            out.add(v.split()[-1].lower())
    return out


# ── scoring: how well does `rec` fit `block`? ─────────────────────────────────
def score(rec: dict, block: str) -> float:
    s = 0.0
    for field, line_re in ROLL_LINES.items():
        rset = _rec_surnames(rec.get(field))
        if not rset:
            continue
        bset = _names_after(line_re, block)
        s += 2.0 * len(rset & bset) - 1.0 * len(rset - bset)
    # speakers (names, order-independent)
    rspk = _rec_surnames(rec.get("speakers"))
    if rspk:
        bspk = _names_after(r"SPEAKERS", block)
        s += 1.5 * len(rspk & bspk) - 0.5 * len(rspk - bspk)
    # action family agreement (coarse keyword hit against the ACTION: line)
    act = str(rec.get("action") or "")
    atext = _action_text(block)
    if act and atext:
        kw = {
            "approved": "approv", "approved_with_conditions": "condition",
            "approved_as_modified": "modif", "disapproved": "disapprov",
            "continued": "continu", "continued_indefinitely": "indefinit",
            "withdrawn": "withdraw", "did_not_take_dr": "not take",
            "took_dr": "took dr", "took_dr_and_approved": "took dr", "filed": "filed",
        }.get(act)
        if kw and kw in atext:
            s += 1.0
    # continuance target date appearing in the ACTION line
    ct = str(rec.get("continued_to") or "")
    if ct and re.search(re.escape(ct[:4]), atext):
        s += 0.5
    return s


# ── load + dedupe ─────────────────────────────────────────────────────────────
def dedupe_key(data: dict) -> str:
    d = dict(data)
    d["meeting_date"] = ""            # collapse fan-out copies that differ only by date
    return json.dumps({k: d.get(k) for k in FIELDS}, sort_keys=True)


def build_review_db(src: Path, dst: Path, placements: list[tuple]):
    """Write a fresh, review-ready labels.db: all blocks from `src` (block_text
    preserved) with the recovered human labels attached to their matched blocks.
    placements = [(item_id, coerced_rec, confident_bool, note), ...]."""
    if dst.exists():
        dst.unlink()
    scon = sqlite3.connect(src); scon.row_factory = sqlite3.Row
    dcon = sqlite3.connect(dst)
    dcon.executescript("""
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
    rows = scon.execute("SELECT id, year, source_file, meeting_date, item_index, "
                        "case_number, block_text FROM items").fetchall()
    dcon.executemany("INSERT INTO items VALUES(?,?,?,?,?,?,?)",
                     [tuple(r) for r in rows])

    placed: dict[int, tuple] = {}
    collisions = 0
    for item_id, rec, confident, note in placements:
        if item_id in placed:                      # two records → same block; keep better
            collisions += 1
            if confident and not placed[item_id][1]:
                placed[item_id] = (rec, confident, note)
            continue
        placed[item_id] = (rec, confident, note)

    all_ids = [r["id"] for r in rows]
    label_rows = []
    for iid in all_ids:
        if iid in placed:
            rec, confident, note = placed[iid]
            status = "prelabeled" if confident else "flagged"
            flagged = 0 if confident else 1
            label_rows.append((iid, json.dumps(rec, ensure_ascii=False),
                               status, flagged, note, ""))
        else:
            label_rows.append((iid, json.dumps(empty_record(), ensure_ascii=False),
                               "todo", 0, "", ""))
    dcon.executemany(
        "INSERT INTO labels(item_id,data,status,flagged,notes,updated_at) "
        "VALUES(?,?,?,?,?,?)", label_rows)
    dcon.commit(); dcon.close(); scon.close()
    return {"placed": len(placed), "collisions": collisions, "items": len(all_ids)}


def recover(db: Path, out: Path, explain: str | None, write_db: Path | None = None):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    # candidate blocks per case number (any status — the right block may be 'todo')
    blocks_by_case: dict[str, list] = defaultdict(list)
    for r in con.execute("SELECT id, year, meeting_date, source_file, item_index, "
                          "case_number, block_text FROM items"):
        if r["case_number"]:
            blocks_by_case[r["case_number"]].append(r)

    # distinct human records (dedupe true fan-out)
    humans = con.execute(
        "SELECT i.case_number, i.meeting_date AS item_mdate, l.data "
        "FROM labels l JOIN items i ON i.id=l.item_id "
        "WHERE l.status IN ('prelabeled','done')").fetchall()

    seen: dict[str, dict] = {}
    for r in humans:
        try:
            data = json.loads(r["data"])
        except Exception:
            continue
        data.setdefault("case_number", r["case_number"])
        k = dedupe_key(data)
        if k not in seen:
            seen[k] = data
    records = list(seen.values())

    # place each record on its best-matching block
    per_year: dict[int, list] = defaultdict(list)
    prov_rows = []
    placements: list[tuple] = []       # (item_id, rec, confident_bool, note) for --write-db
    stats = {"records": len(records), "confident": 0, "ambiguous": 0,
             "no_block": 0, "date_conflict": 0}

    for data in records:
        cn = str(data.get("case_number") or "")
        cands = blocks_by_case.get(cn, [])
        rec = coerce_record(data)
        if not cands:
            stats["no_block"] += 1
            yr = _year_of(rec, cn)
            per_year[yr].append(rec)
            prov_rows.append({"case_number": cn, "matched_block": "", "score": "",
                              "runner_up": "", "confidence": "no_block",
                              "human_date": rec.get("meeting_date"), "matched_date": ""})
            continue
        scored = sorted(((score(rec, c["block_text"]), c) for c in cands),
                        key=lambda t: t[0], reverse=True)
        best_s, best = scored[0]
        second_s = scored[1][0] if len(scored) > 1 else float("-inf")
        confident = best_s >= 2.0 and (best_s - second_s) >= 2.0
        if confident:
            stats["confident"] += 1; conf = "confident"
        else:
            stats["ambiguous"] += 1; conf = "ambiguous"
            # ambiguous + no fingerprint: prefer the block matching the human's own date
            if best_s <= 0:
                for c in cands:
                    if c["meeting_date"] and c["meeting_date"] == rec.get("meeting_date"):
                        best = c; break

        matched_date = best["meeting_date"]
        human_date = rec.get("meeting_date")
        if not human_date:
            rec["meeting_date"] = matched_date            # fill only when blank
        elif matched_date and human_date != matched_date:
            stats["date_conflict"] += 1                   # flag, never overwrite

        per_year[int(best["year"])].append(rec)
        note = (f"[recovered: {conf}; match {best['source_file']}#{best['item_index']} "
                f"score {best_s:.1f}]")
        placements.append((best["id"], rec, confident, note))
        prov_rows.append({
            "case_number": cn, "matched_block": f'{best["source_file"]}#{best["item_index"]}',
            "score": round(best_s, 1), "runner_up": round(second_s, 1) if second_s > float("-inf") else "",
            "confidence": conf, "human_date": human_date or "", "matched_date": matched_date or "",
        })

        if explain and cn.replace(" ", "").upper() == explain.replace(" ", "").upper():
            _explain(rec, scored)

    if explain:
        return

    # write outputs
    out.mkdir(parents=True, exist_ok=True)
    for yr, recs in sorted(per_year.items()):
        (out / f"{yr}_labeled.json").write_text(
            json.dumps(recs, indent=2, ensure_ascii=False))
    with (out / "recovery_provenance.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(prov_rows[0].keys()))
        w.writeheader(); w.writerows(prov_rows)

    report = [
        "# Hand-label recovery from labels.db", "",
        f"- distinct human records recovered : **{stats['records']}**",
        f"- placed with confidence           : {stats['confident']}",
        f"- ambiguous placement (review)     : {stats['ambiguous']}",
        f"- case number had no block         : {stats['no_block']}",
        f"- human date ≠ matched block date  : {stats['date_conflict']} (flagged, not changed)",
        f"- years written                    : {min(per_year)}–{max(per_year)} "
        f"({sum(len(v) for v in per_year.values())} records across "
        f"{len(per_year)} files)",
        "",
        "Source: `labels.db.qa.bak` (clean, pre-QA-backfill). Field values are the "
        "human's, verbatim; only blank `meeting_date` was filled from the matched "
        "block. Ambiguous rows are placed best-effort — check `recovery_provenance.csv` "
        "(sort by confidence). Any labels the original ingest overwrote before this DB "
        "existed cannot be recovered from here.",
    ]
    if write_db:
        info = build_review_db(db, write_db, placements)
        report += ["",
                   f"Review DB rebuilt → `{write_db}`: {info['items']} blocks, "
                   f"{info['placed']} human labels attached "
                   f"({stats['confident']} prelabeled / {stats['ambiguous']} flagged), "
                   f"{info['collisions']} placement collisions resolved."]

    (out / "recovery_report.md").write_text("\n".join(report) + "\n")
    print("\n".join(report))
    print(f"\n✓ wrote {out}")


def _year_of(rec: dict, cn: str) -> int:
    md = str(rec.get("meeting_date") or "")
    if re.match(r"(19|20)\d{2}", md):
        return int(md[:4])
    m = re.match(r"(\d{2}|\d{4})[.\-]", cn)
    if m:
        y = m.group(1)
        return int(y) if len(y) == 4 else (1900 + int(y) if int(y) > 50 else 2000 + int(y))
    return 0


def _explain(rec: dict, scored):
    print(f"\n=== {rec.get('case_number')} — candidate blocks by match score ===")
    print(f"human ayes={rec.get('ayes')} absent={rec.get('absent')} "
          f"speakers={rec.get('speakers')} action={rec.get('action')}")
    for s, c in scored:
        head = c["block_text"][:90].replace("\n", " ")
        print(f"  score {s:6.1f}  {c['source_file']}#{c['item_index']} [{c['meeting_date']}]  {head}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--explain", help="print candidate-block scores for one case number")
    ap.add_argument("--write-db", type=Path, default=None,
                    help="also rebuild a review-ready labels.db at this path")
    a = ap.parse_args()
    recover(a.db, a.out, a.explain, a.write_db)


if __name__ == "__main__":
    main()
