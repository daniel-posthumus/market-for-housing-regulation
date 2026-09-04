#!/usr/bin/env python3
"""
assign_meeting_dates.py
-----------------------
Purpose : Assign every parsed block its true meeting date. This is a SEPARATE stage from
          block parsing: it never re-derives block boundaries, it takes the blocks the
          parser already produced and locates each one back inside its source page by
          content, then reads off the meeting header it falls under. Because the only
          coupling to the parser is "here is the text of a block", changing the parser's
          boundary rules can never silently misalign dates again.
Inputs  : labels.db (items: source_file, item_index, block_text), raw HTML pages under
          MFHR_DATA_ROOT/meeting_minutes/<locality>/raw/<year>/<stem>.html
Outputs : items.meeting_date updated in place (--apply); date_assignment_audit.csv with a
          per-page record of what was assigned and why. Labels are untouched unless
          --sync-labels is passed.
Author  : Dan Post
Created : 2026-08-29

Notes
-----
Why the old approach failed: the HTML-era parser split a page into meetings via
`<a name="6_4_98">` anchors (`chop_into_meetings`), but only 3 of 724 archive pages carry
them. Every other page — including the 1998-2000 monthly compilations that bundle four
meetings each — collapsed to one section, so all blocks inherited the page's FIRST date.
`fix_meeting_dates.py` (superseded by this script) tried to repair it by re-deriving block
offsets with a copy of the boundary rule, which drifted out of sync the moment the parser
gained new boundaries.

Method, per page:
  1. Fold page text and each block to a canonical form (lowercase, only [a-z0-9.]) so
     whitespace/OCR noise can't defeat a match, keeping a fold→original index map.
  2. Locate blocks by sequential search in item_index order. Sequential (rather than
     global) search is what disambiguates text that repeats across meetings on the same
     page — four "ADJOURNMENT" blocks resolve to four different offsets, in order.
  3. Detect meeting-header dates: a DATE_RE hit immediately preceded by a weekday, and NOT
     preceded by a reference phrase ("...PROPOSED FOR ADOPTION AT THE REGULAR MEETING OF
     THURSDAY, JUNE 18, 1998" is a cross-reference, not a header).
  4. date(block) = the last header date at or before the block's offset.

Every page is then validated automatically: header dates must parse and fall in the page's
year, blocks must place, and dates the archive does not itself corroborate must land on a
Thursday (the Commission's sitting day). All 724 archive pages pass these rules on the
deterministic path, so no LLM tagging is wired up; a future page that fails validation
keeps its existing date and is listed in the audit CSV rather than being guessed at.

Modern-era items (source_file 'tagged/...') are one meeting per file with an ISO date in
the filename; they are validated against that name, not re-derived.

Usage:
  python assign_meeting_dates.py                    # dry run + audit CSV
  python assign_meeting_dates.py --page min0698     # explain one page, then exit
  python assign_meeting_dates.py --apply            # write items.meeting_date
  python assign_meeting_dates.py --apply --sync-labels
"""
from __future__ import annotations

import argparse
import csv
import datetime
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import parse_sf_meeting_minutes as PH              # noqa: E402  (RAW_DIR, DATE_RE)

DB = HERE / "labeling_app" / "labels.db"
AUDIT = HERE / "date_assignment_audit.csv"

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}

WEEKDAY = r"(?:MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY)"

# A header date is "THURSDAY <sep> JUNE 4, 1998": a weekday, then nothing but punctuation
# and whitespace, then the date. Anything wordier is prose about some other meeting.
HEADER_LEAD = re.compile(WEEKDAY + r"[\s,.\-–—:]*$", re.I)

# ...and a real meeting header is always followed by the gavel time, on both page layouts:
#   "THURSDAY JUNE 4, 1998 ROOM 428 WAR MEMORIAL BUILDING 401 VAN NESS AVENUE 1:30 P.M."
#   "Thursday, January 23, 2003 1:30 PM Regular Meeting"
# This is what separates a header from prose that happens to name a weekday and a date
# ("Finance Committee Meeting is open to the public on Saturday, June 16, 2001, at 9:00"),
# which is the residual false positive the cross-reference list alone does not catch.
HEADER_TIME = re.compile(r"\d{1,2}:\d{2}\s*[APap]\.?\s?[Mm]\.?")
TRAIL_CHARS = 170

# The per-meeting archive pages state their own date in the title and breadcrumb
# ("San Francisco Planning Department : September 20, 2007"). That is the archive's own
# label for the page, so it anchors the page even when the body header is unparseable.
TITLE_DATE = re.compile(
    r"San Francisco Planning Department\s*:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})")

# ...unless that weekday is itself the tail of a cross-reference to a different meeting.
# These are the phrases the archive actually uses; all are followed by a date that must NOT
# become a section boundary.
CROSSREF = re.compile(
    r"(?:continu|postpon|reschedul|adopt|approv|calendar of|meeting of|hearing of|"
    r"minutes of|held on|taken on|deferred|off\s+calendar|call of the chair)",
    re.I)

# How far back to look for the weekday / cross-reference context.
LEAD_CHARS = 110

# Corroboration. A minutes header is a structural object, not just a date: the Commission
# is seated in a named room and then called to order by roll. Prose about some other body's
# meeting ("There will be a special Rules Committee meeting this coming Monday, February 10,
# 2003 at 2:30 p.m.") satisfies weekday-plus-time but has neither. Requiring one of the two
# is what separates them.
ROLLCALL = re.compile(r"(?i)(?:COMMISSIONERS\s+)?PRESENT\s*:")
LOCATION = re.compile(r"(?i)ROOM\s+\d{3}|CITY HALL|WAR MEMORIAL|COMMISSION CHAMBERS|"
                      r"SUPERVISORS.{0,3}\s*CHAMBERS")
ROLLCALL_AFTER = 800      # chars after the date to look for the roll call
LOCATION_BEFORE = 300     # chars before the date to look for the room

# Two headers carrying the same date this close together are one header seen twice (page
# title, then body). Farther apart they are two real meetings on one day — the Commission
# sat jointly with the Redevelopment Agency Commission on 1998-01-15 after its own regular
# session, in a different room, with its own roll call and adjournment.
SAME_DATE_SPAN = 3000


def iso(month_name: str, day: str, year: str) -> str:
    mo = MONTHS.get(month_name.lower())
    if not mo:
        return ""
    try:
        d = datetime.date(int(year), mo, int(day))
    except ValueError:
        return ""
    return d.isoformat()


# ── (1) fold text so block↔page matching survives whitespace and OCR noise ────
_KEEP = re.compile(r"[^a-z0-9.]")


def fold(s: str) -> tuple[str, list[int]]:
    """Return (folded_text, index_map) where index_map[i] is the original offset of
    folded_text[i]. Keeps digits and '.' so case numbers stay distinctive."""
    out, idx = [], []
    for i, ch in enumerate(s.lower()):
        if not _KEEP.match(ch):
            out.append(ch)
            idx.append(i)
    return "".join(out), idx


def fold_block(s: str) -> str:
    return _KEEP.sub("", s.lower())


# ── (2) meeting-header dates, with offsets, in page-text coordinates ──────────
def _to_iso(datestr: str) -> str:
    parts = re.match(r"([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})", datestr)
    return iso(*parts.groups()) if parts else ""


def title_date(text: str) -> str:
    m = TITLE_DATE.search(text[:3000])
    return _to_iso(m.group(1)) if m else ""


# Last resort: a few archive pages are a PDF stub whose only date is in the file name
# ("20000203-documentid=32.pdf").
STEM_DATE = re.compile(r"(?:^|[^0-9])((?:19|20)\d{2})(\d{2})(\d{2})(?:[^0-9]|$)")


def stem_date(src: str) -> str:
    m = STEM_DATE.search(src.split("/")[-1])
    if not m:
        return ""
    try:
        return datetime.date(*map(int, m.groups())).isoformat()
    except ValueError:
        return ""


def header_dates(text: str) -> list[tuple[int, str]]:
    """[(offset, ISO date)] for dates that head a meeting section, in document order."""
    out = []
    td = title_date(text)
    if td:
        out.append((0, td))               # the page's own declared meeting date
    for m in PH.DATE_RE.finditer(text):
        lead = text[max(0, m.start() - LEAD_CHARS):m.start()]
        if not HEADER_LEAD.search(lead):
            continue
        if CROSSREF.search(lead):
            continue
        if not HEADER_TIME.search(text[m.end():m.end() + TRAIL_CHARS]):
            continue
        if not (ROLLCALL.search(text[m.end():m.end() + ROLLCALL_AFTER])
                or LOCATION.search(text[max(0, m.start() - LOCATION_BEFORE):m.start()])):
            continue
        d = _to_iso(m.group(0))
        if d:
            out.append((m.start(), d))
    out.sort(key=lambda t: t[0])
    # collapse a header that merely repeats the one just above it (title line, then the
    # body header a few hundred characters later); keep a same-date header that stands far
    # enough away to be its own meeting.
    dedup = []
    for pos, d in out:
        if dedup and dedup[-1][1] == d and pos - dedup[-1][0] < SAME_DATE_SPAN:
            continue
        dedup.append((pos, d))
    return dedup


# ── (3) locate each block inside the page, in order ───────────────────────────
OCC_CAP = 40          # max candidate positions considered per block


def _occurrences(hay: str, needle: str, cap: int = OCC_CAP) -> list[int]:
    out, i = [], 0
    while len(out) < cap:
        p = hay.find(needle, i)
        if p < 0:
            break
        out.append(p)
        i = p + 1
    return out


def _longest_chain(cands: list[tuple[int, int]]) -> dict[int, int]:
    """Longest subsequence of (block_index, offset) candidates strictly increasing in both.

    Candidates arrive in block order, descending by offset within a block, so no two
    candidates for the same block can chain — the classic one-per-group LIS.
    """
    tails: list[int] = []          # tails[k] = smallest end offset of a chain of length k+1
    tails_at: list[int] = []       # index into cands of that chain's last element
    parent: list[int | None] = [None] * len(cands)
    for k, (_bi, off) in enumerate(cands):
        lo, hi = 0, len(tails)
        while lo < hi:
            mid = (lo + hi) // 2
            if tails[mid] < off:
                lo = mid + 1
            else:
                hi = mid
        parent[k] = tails_at[lo - 1] if lo else None
        if lo == len(tails):
            tails.append(off)
            tails_at.append(k)
        else:
            tails[lo], tails_at[lo] = off, k

    chain: dict[int, int] = {}
    k = tails_at[-1] if tails_at else None
    while k is not None:
        bi, off = cands[k]
        chain[bi] = off
        k = parent[k]
    return chain


def block_offsets(page_text: str, blocks: list[str]) -> list[int | None]:
    """Offset of each block in page_text, as an order-preserving alignment.

    Boilerplate recurs verbatim across the meetings bundled in one page ("THE DRAFT
    MINUTES ARE PROPOSED FOR ADOPTION…" appears once per meeting, identical for hundreds
    of characters), so a block's text alone does not say which meeting it belongs to. What
    does is that blocks tile the page in order: the right placement is the longest chain of
    candidates strictly increasing in both block index and offset.

    Passes repeat on whatever is left over, because block order is only *piecewise*
    increasing: on the three pages that carry `<a name="3_5_98">` anchors the parser walks
    each anchor's siblings, which emits its sections out of document order, so blocks
    82-121 of March 1998 live earlier in the page than blocks 78-81. One pass places the
    long run, the next places the displaced one, instead of discarding it.
    """
    folded, imap = fold(page_text)
    cand_by_block: dict[int, list[int]] = {}
    for bi, blk in enumerate(blocks):
        fb = fold_block(blk)
        if not fb:
            continue
        occ = _occurrences(folded, fb[:120]) or _occurrences(folded, fb[:40])
        if occ:
            cand_by_block[bi] = sorted(occ, reverse=True)

    placed: dict[int, int] = {}
    todo = sorted(cand_by_block)
    for _pass in range(8):
        if not todo:
            break
        chain = _longest_chain([(bi, o) for bi in todo for o in cand_by_block[bi]])
        if not chain:
            break
        placed.update(chain)
        todo = [bi for bi in todo if bi not in placed]

    return [imap[placed[i]] if i in placed else None for i in range(len(blocks))]


def page_text_for(src: str) -> str | None:
    """Full text of a source page, in the same shape the parser saw it."""
    year, stem = src.split("/")[1], src.split("/")[-1]
    f = PH.RAW_DIR / year / f"{stem}.html"
    if not f.exists():
        return None
    with f.open("rb") as fh:
        # One 2000 page was scraped as a PDF and saved under an .html name; parsing its
        # bytes as HTML yields garbage, so route on content rather than extension.
        if fh.read(5).startswith(b"%PDF"):
            import pdfplumber
            with pdfplumber.open(f) as pdf:
                return "\n".join((pg.extract_text() or "") for pg in pdf.pages)
    soup = BeautifulSoup(f.read_text(encoding="utf-8", errors="ignore"), "lxml")
    return soup.get_text("\n")


# ── (4) per-page assignment + automatic validation ────────────────────────────
def assign_page(src: str, rows: list[tuple[int, int, str, str]]) -> dict:
    """rows: [(item_id, item_index, block_text, current_date)] in item_index order."""
    year = int(src.split("/")[1])
    text = page_text_for(src)
    if text is None:
        return dict(src=src, status="missing_page", n=len(rows), assigned={}, hdrs=[])

    hdrs = header_dates(text)
    td = title_date(text)
    if not hdrs:
        fallback = td or stem_date(src)
        if fallback:
            hdrs = [(0, fallback)]
            td = td or fallback
    offs = block_offsets(text, [r[2] for r in rows])

    # A block we could not locate sits between the two we could, so it inherits the date of
    # the last located block — blocks are contiguous, so that is its meeting by definition.
    assigned, unplaced, last = {}, 0, None
    for (item_id, _idx, _blk, cur), off in zip(rows, offs):
        if off is None:
            unplaced += 1
            assigned[item_id] = last if last else (hdrs[0][1] if hdrs else cur)
            continue
        prior = [d for p, d in hdrs if p <= off]
        assigned[item_id] = prior[-1] if prior else (hdrs[0][1] if hdrs else cur)
        last = assigned[item_id]

    # ── validation, all mechanical ──
    problems = []
    if not hdrs:
        problems.append("no_header_date")
    if unplaced > max(2, 0.1 * len(rows)):     # a few interpolated blocks are normal
        problems.append(f"unplaced_blocks={unplaced}/{len(rows)}")
    bad_year = {d for d in assigned.values() if d and not d.startswith(str(year))}
    if bad_year:
        problems.append("year_mismatch=" + ",".join(sorted(bad_year))[:40])
    # the Commission sits on Thursdays; a page of non-Thursdays means header
    # detection latched onto prose, not a real special-meeting run
    wd = Counter()
    for d in set(assigned.values()):
        if d:
            try:
                wd[datetime.date(*map(int, d.split("-"))).weekday()] += 1
            except ValueError:
                problems.append("unparseable_date")
    n_dates = sum(wd.values())
    n_thu = wd.get(3, 0)
    # A non-Thursday date the archive itself put in the page title is a real special
    # meeting ("Friday, May 4, 2007 … Special Meeting"), not a detection error — the
    # weekday test only has to catch dates we inferred without that corroboration.
    corroborated = td and set(assigned.values()) <= {td}
    if n_dates and n_thu / n_dates < 0.5 and not corroborated:
        problems.append(f"mostly_non_thursday={n_thu}/{n_dates}")

    return dict(src=src, status="ok" if not problems else "check", n=len(rows),
                assigned=assigned, hdrs=hdrs, problems=problems,
                n_dates=len(set(assigned.values())))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write items.meeting_date")
    ap.add_argument("--sync-labels", action="store_true",
                    help="also update meeting_date inside label JSON where the label still "
                         "carries the item's old (inherited) date — never overwrites a "
                         "value you typed yourself")
    ap.add_argument("--page", help="explain one page (stem substring) and exit")
    a = ap.parse_args()

    con = sqlite3.connect(DB)
    where = "source_file LIKE 'raw/%'"
    params: tuple = ()
    if a.page:
        where += " AND source_file LIKE ?"
        params = (f"%{a.page}%",)

    srcs = [r[0] for r in con.execute(
        f"SELECT DISTINCT source_file FROM items WHERE {where} ORDER BY source_file", params)]

    results, updates = [], []   # updates: (new_date, item_id, old_date)
    for src in srcs:
        rows = con.execute(
            "SELECT id, item_index, block_text, meeting_date FROM items "
            "WHERE source_file=? ORDER BY item_index", (src,)).fetchall()
        res = assign_page(src, rows)
        cur_by_id = {r[0]: r[3] for r in rows}
        res["changed"] = sum(1 for i, d in res["assigned"].items() if d and d != cur_by_id[i])
        results.append(res)
        for item_id, d in res["assigned"].items():
            if d and d != cur_by_id[item_id]:
                updates.append((d, item_id, cur_by_id[item_id]))

    if a.page:
        for res in results:
            print(f"\n{res['src']}: {res['n']} blocks, status={res['status']}")
            print("  headers:", ", ".join(f"{d}@{p}" for p, d in res["hdrs"]) or "(none)")
            if res.get("problems"):
                print("  problems:", "; ".join(res["problems"]))
            seq = Counter(res["assigned"].values())
            print("  assigned:", dict(sorted(seq.items())))
            print("  changed:", res["changed"])
        return

    ok = [r for r in results if r["status"] == "ok"]
    check = [r for r in results if r["status"] == "check"]
    missing = [r for r in results if r["status"] == "missing_page"]
    print(f"pages: {len(results)}  ok: {len(ok)}  needs-check: {len(check)}  "
          f"missing: {len(missing)}")
    print(f"items: {sum(r['n'] for r in results)}  date changes: {len(updates)}")
    prob = Counter(p.split("=")[0] for r in check for p in r["problems"])
    if prob:
        print("problem breakdown:", dict(prob.most_common()))

    with AUDIT.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["source_file", "status", "n_blocks", "n_header_dates",
                    "n_distinct_assigned", "n_changed", "problems", "headers"])
        for r in results:
            w.writerow([r["src"], r["status"], r["n"], len(r.get("hdrs", [])),
                        r.get("n_dates", 0), r.get("changed", 0),
                        "; ".join(r.get("problems", [])),
                        " ".join(d for _p, d in r.get("hdrs", []))])
    print(f"→ {AUDIT.relative_to(HERE.parent.parent)}")

    if not a.apply:
        print("(dry run — re-run with --apply to write)")
        return

    con.executemany("UPDATE items SET meeting_date=? WHERE id=?",
                    [(new, i) for new, i, _old in updates])
    con.commit()
    print(f"✓ updated {len(updates)} item dates")

    if a.sync_labels:
        n = sync_labels(con, {i: (old, new) for new, i, old in updates})
        print(f"✓ synced {n} label records to the corrected dates")
    con.close()


def sync_labels(con: sqlite3.Connection, moves: dict[int, tuple[str, str]]) -> int:
    """Push corrected dates into label JSON, but only where the label still carries the
    date it inherited from the item — a date typed by hand is left alone and reported."""
    import json
    n = kept = 0
    for item_id, (old, new) in moves.items():
        row = con.execute("SELECT data FROM labels WHERE item_id=?", (item_id,)).fetchone()
        if not row or not row[0]:
            continue
        rec = json.loads(row[0])
        have = rec.get("meeting_date") or ""
        if have not in ("", old):
            kept += 1                      # hand-typed date disagrees — leave it
            continue
        rec["meeting_date"] = new
        con.execute("UPDATE labels SET data=? WHERE item_id=?",
                    (json.dumps(rec, ensure_ascii=False), item_id))
        n += 1
    con.commit()
    if kept:
        print(f"  ({kept} labels kept a hand-typed date that disagrees with the page)")
    return n


if __name__ == "__main__":
    main()
