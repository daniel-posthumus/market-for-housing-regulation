#!/usr/bin/env python3
"""
ingest.py — build / refresh the labeling work-queue (SQLite) from the tagged
minutes corpus.

For every project block in data/meeting_minutes/<locality>/tagged/{year}/*.txt it creates an
`items` row (raw block text + parsed meeting date + case number). It then seeds a
`labels` row as a pre-fill so the human corrects rather than types:
  • if a migrated {year}_labeled.json record matches the case number → that
    (human-origin) record, status 'prelabeled';
  • otherwise the heuristic autoextract guess, status 'todo'.

Idempotent: existing items are not duplicated and existing label rows are NEVER
overwritten (your edits are safe). Use --reset to wipe and rebuild.

Usage:
  python ingest.py                 # add new items, keep edits
  python ingest.py --reset         # wipe DB and rebuild
  python ingest.py --years 2010-2014
"""
from __future__ import annotations
import argparse, json, re, sqlite3, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from extraction_common import FIELDS, coerce_record           # noqa: E402
from autoextract import extract, CASE_RE                       # noqa: E402

from paths import MEETING_MINUTES
BASE = MEETING_MINUTES
TAGGED = BASE / "tagged"
TRAIN_DIR = TAGGED / "training"
DB = HERE / "labels.db"

BLOCK_RE = re.compile(r"<<Project Start>>(.*?)<<Project End>>", re.S)
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}


def parse_meeting_date(filename: str, year: int) -> str:
    """Best-effort ISO date from a tagged filename like 'October_14_2004.txt',
    '02-12-1998.txt', 'JANUARY_8_1998.txt', or the modern '2023-01-05.txt'."""
    stem = re.sub(r"\.(txt|html)$", "", filename, flags=re.I)
    iso = re.match(r"(20\d{2}|19\d{2})-(\d{2})-(\d{2})", stem)    # modern ISO[_qual] names
    if iso:
        y, mo, d = map(int, iso.groups())
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    s = stem.replace("_", " ").replace("-", " ").strip().lower()
    toks = s.split()
    mon = day = None
    for t in toks:
        if t in MONTHS:
            mon = MONTHS[t]
        elif t.isdigit() and 1 <= int(t) <= 31 and day is None and int(t) != year:
            day = int(t)
    # numeric MM DD YYYY form
    nums = [int(t) for t in toks if t.isdigit()]
    if mon is None and len(nums) >= 2 and 1 <= nums[0] <= 12:
        mon, day = nums[0], nums[1]
    if mon and day:
        return f"{year:04d}-{mon:02d}-{day:02d}"
    return ""


def schema_for_db() -> dict:
    return {"fields": FIELDS}


def init_db(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS items(
        id INTEGER PRIMARY KEY,
        year INTEGER, source_file TEXT, meeting_date TEXT,
        item_index INTEGER, case_number TEXT, block_text TEXT,
        UNIQUE(source_file, item_index));
    CREATE TABLE IF NOT EXISTS labels(
        item_id INTEGER PRIMARY KEY REFERENCES items(id),
        data TEXT, status TEXT DEFAULT 'todo', flagged INTEGER DEFAULT 0,
        notes TEXT DEFAULT '', updated_at TEXT DEFAULT '');
    CREATE INDEX IF NOT EXISTS ix_items_case ON items(case_number);
    """)


def load_prelabels() -> dict:
    """case_number(upper, no spaces) -> migrated human record (schema-complete)."""
    out = {}
    for f in sorted(TRAIN_DIR.glob("*_labeled.json")):
        try:
            for r in json.load(f.open()):
                cn = str(r.get("case_number", "")).replace(" ", "").upper()
                if cn:
                    out[cn] = coerce_record(r)
        except Exception as e:
            print(f"  ⚠ {f.name}: {e}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--years", help="e.g. 2010-2014 or 1998,2014 (default: all tagged)")
    args = ap.parse_args(argv)

    if args.reset and DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)
    init_db(con)

    want = None
    if args.years:
        want = set()
        for part in args.years.split(","):
            if "-" in part:
                a, b = part.split("-"); want.update(range(int(a), int(b) + 1))
            else:
                want.add(int(part))

    prelabels = load_prelabels()
    new_items = new_labels = 0

    year_dirs = sorted(d for d in TAGGED.iterdir()
                       if d.is_dir() and d.name.isdigit())
    for ydir in year_dirs:
        year = int(ydir.name)
        if want and year not in want:
            continue
        for f in sorted(ydir.glob("*.txt")):
            text = f.read_text(encoding="utf-8", errors="replace")
            mdate = parse_meeting_date(f.name, year)
            blocks = [b.strip() for b in BLOCK_RE.findall(text)]
            for idx, blk in enumerate(blocks):
                if not blk.strip():
                    continue
                cm = CASE_RE.search(blk)
                case = cm.group(1) if cm else ""
                src = str(f.relative_to(BASE))
                cur = con.execute(
                    "INSERT OR IGNORE INTO items(year,source_file,meeting_date,"
                    "item_index,case_number,block_text) VALUES(?,?,?,?,?,?)",
                    (year, src, mdate, idx, case, blk))
                if cur.rowcount == 0:
                    continue                       # item already present
                new_items += 1
                item_id = cur.lastrowid
                # seed a label row (never overwrite an existing one)
                key = case.replace(" ", "").upper()
                if key and key in prelabels:
                    rec = dict(prelabels[key]); status = "prelabeled"
                    rec["meeting_date"] = rec.get("meeting_date") or mdate
                else:
                    rec = extract(blk, meeting_date=mdate); status = "todo"
                con.execute(
                    "INSERT OR IGNORE INTO labels(item_id,data,status) VALUES(?,?,?)",
                    (item_id, json.dumps(rec, ensure_ascii=False), status))
                new_labels += 1
    con.commit()

    total = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    by_status = dict(con.execute(
        "SELECT status, COUNT(*) FROM labels GROUP BY status").fetchall())
    con.close()
    print(f"ingest complete: +{new_items} items (+{new_labels} seeded labels)")
    print(f"total items: {total}; label status: {by_status}")
    print(f"DB: {DB}")


if __name__ == "__main__":
    main()
