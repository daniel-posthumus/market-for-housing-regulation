#!/usr/bin/env python3
"""
fix_meeting_dates.py
--------------------
Purpose : Correct items.meeting_date for HTML-era blocks. Some archive pages bundle
          several meetings (esp. 1998-2000 monthly compilations) but the parser stamped
          every item with the page's FIRST meeting date. Reassign each item block the
          date of the meeting it actually falls under, detected from the in-page
          meeting-header dates (a full date in header context: preceded by a weekday /
          "MEETING", not an inline continuance reference).
Inputs  : labels.db (items with source_file='raw/<year>/<stem>'), raw HTML under
          MFHR_DATA_ROOT/.../raw/<year>/<stem>.html
Outputs : updates items.meeting_date in place (labels untouched). --apply to write.
Author  : Dan Post
Created : 2026-07-03

Notes
-----
Only touches HTML-era items (source_file LIKE 'raw/%'); modern (PDF) dates were fine.
Block splitting mirrors rebuild_review_db exactly (chop_into_meetings → add_project_tags,
idx running per file) so item_index lines up with the DB.
"""
from __future__ import annotations
import argparse, re, sqlite3, sys, datetime
from pathlib import Path
from collections import Counter
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import parse_sf_meeting_minutes as P   # noqa: E402

DB = HERE / "labeling_app" / "labels.db"
MON = ["january","february","march","april","may","june","july","august",
       "september","october","november","december"]


def iso(dstr: str) -> str:
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", dstr.strip())
    if m and m.group(1).lower() in MON:
        return f"{m.group(3)}-{MON.index(m.group(1).lower())+1:02d}-{int(m.group(2)):02d}"
    return ""


def block_starts(text: str) -> list[int]:
    """Mirror add_project_tags boundary detection → block start offsets."""
    ms = list(P.CASE_HEADER_RE.finditer(text))
    if len(ms) < 2:
        ms = list(P.AGENDA_ITEM_RE.finditer(text))
    if not ms:
        return [0]
    return [m.start() for m in ms]


def header_dates(text: str) -> list[tuple[int, str]]:
    """Positions of in-page MEETING-header dates (not inline continuance dates)."""
    out = []
    for m in P.DATE_RE.finditer(text):
        pre = text[max(0, m.start() - 45):m.start()]
        is_header = re.search(r"(MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY)\b|\bMEETING\b", pre, re.I)
        is_continuance = re.search(r"contin|meeting of\b|\bto\b|\bfrom\b|\bof the\b", pre, re.I)
        if is_header and not is_continuance:
            d = iso(m.group(0))
            if d:
                out.append((m.start(), d))
    return out


def date_for_block(pos: int, hdrs: list[tuple[int, str]], fallback: str) -> str:
    prior = [d for p, d in hdrs if p <= pos]
    if prior:
        return prior[-1]
    return hdrs[0][1] if hdrs else fallback


def page_block_dates(f: Path, existing: str) -> list[str]:
    """Per-block meeting date for a raw page, block order == DB item_index order."""
    soup = BeautifulSoup(f.read_text(encoding="utf-8", errors="ignore"), "lxml")
    dates = []
    for _, sect in P.chop_into_meetings(soup):
        text = BeautifulSoup(sect, "lxml").get_text("\n")
        hdrs = header_dates(text)
        starts = block_starts(text)
        # only real (non-empty) blocks, matching add_project_tags' output
        tagged_blocks = [b for b in re.findall(r"<<Project Start>>\s*(.*?)\s*<<Project End>>",
                                               P.add_project_tags(text), re.S) if b.strip()]
        for i, _blk in enumerate(tagged_blocks):
            pos = starts[i] if i < len(starts) else (starts[-1] if starts else 0)
            dates.append(date_for_block(pos, hdrs, existing))
    return dates


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (else dry-run)")
    ap.add_argument("--test-page", help="print block→date for one stem (e.g. min0498) and exit")
    a = ap.parse_args()
    con = sqlite3.connect(DB)

    if a.test_page:
        row = con.execute("SELECT DISTINCT source_file FROM items WHERE source_file LIKE ?",
                          (f"%{a.test_page}%",)).fetchone()
        src = row[0]; year = src.split("/")[1]; stem = src.split("/")[-1]
        f = P.RAW_DIR / year / f"{stem}.html"
        ds = page_block_dates(f, "")
        print(f"{src}: {len(ds)} blocks")
        print("date sequence:", " ".join(d[5:] if d else "??" for d in ds))
        print("distribution:", dict(Counter(ds)))
        return

    rows = con.execute("SELECT DISTINCT source_file FROM items WHERE source_file LIKE 'raw/%'").fetchall()
    changed = same = missing = nonthu = 0
    updates = []
    for (src,) in rows:
        year = src.split("/")[1]; stem = src.split("/")[-1]
        f = P.RAW_DIR / year / f"{stem}.html"
        if not f.exists():
            missing += 1; continue
        items = con.execute("SELECT item_index, meeting_date FROM items WHERE source_file=? "
                            "ORDER BY item_index", (src,)).fetchall()
        try:
            ds = page_block_dates(f, items[0][1] if items else "")
        except Exception as e:
            print(f"  ⚠ {src}: {e}"); missing += 1; continue
        for (idx, old), new in zip(items, ds):
            if not new:
                continue
            try:
                y, mo, da = map(int, new.split("-"))
                if datetime.date(y, mo, da).weekday() != 3:   # not Thursday → distrust, skip
                    nonthu += 1; continue
            except Exception:
                continue
            if new != old:
                changed += 1
                updates.append((new, src, idx))
            else:
                same += 1

    print(f"pages: {len(rows)} | changed: {changed} | unchanged: {same} | "
          f"skipped non-Thursday: {nonthu} | missing pages: {missing}")
    if a.apply and updates:
        con.executemany("UPDATE items SET meeting_date=? WHERE source_file=? AND item_index=?", updates)
        con.commit()
        print(f"✓ applied {len(updates)} date corrections")
    elif updates:
        print("(dry-run — re-run with --apply to write)")
    con.close()


if __name__ == "__main__":
    main()
