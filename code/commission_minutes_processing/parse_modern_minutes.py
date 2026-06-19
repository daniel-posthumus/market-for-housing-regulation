#!/usr/bin/env python3
"""
parse_modern_minutes.py — parse the MODERN-era SF Planning Commission minutes
(2015–present) into the same `<<Project Start>> … <<Project End>>` tagged blocks
the HTML-era parser produces, so everything downstream (ingest → labeling →
training_sample_create → train/llm_extract → inference) is unchanged.

Why a separate module: `parse_sf_meeting_minutes.py` is HTML/scraper-specific and
its item boundary is the dot-format case header (`98.226D`). The modern era is
delivered as text (2015–2017, scraped from HTML) and PDF (2018–present), prints
items as `13.  2021-002057DRP  (D. WINSLOW: …)`, and uses the dash case format —
none of which the HTML parser handles. (`autoextract.CASE_RE` already matches both
dot and dash, so case-number extraction downstream needs no change.)

Item boundary: a numbered agenda line (`13.` / `3a.` / `2.`) followed by either a
case number or an upper-case title — this captures both the spaced PDF form and
the space-stripped 2015–2017 text form (`1.2014.0956E(...)`), and keeps non-case
agenda items (Land Acknowledgement, Commission Comments) as their own blocks, just
like the HTML era's numbered-item fallback.

Outputs (active locality subtree):
    tagged/<year>/<YYYY-MM-DD>.txt            # <<Project>>-tagged blocks
    processed/modern_meetings_metadata.csv    # one row per meeting (present/absent/staff)

Usage:
    python parse_modern_minutes.py                 # all years ≥ 2015 in raw/
    python parse_modern_minutes.py --years 2018-2020
    python parse_modern_minutes.py --overwrite     # re-tag even if the .txt exists
"""
from __future__ import annotations
import argparse, csv, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import MEETING_MINUTES

RAW = MEETING_MINUTES / "raw"
TAG = MEETING_MINUTES / "tagged"
PROC = MEETING_MINUTES / "processed"
FIRST_MODERN_YEAR = 2015

# A numbered agenda item: "13. ", "3a.", "2." — with or without a following space
# (2015–2017 text is space-stripped). Lookahead requires a case number or an
# upper-case/"(" start so prose "…2. the applicant…" isn't mistaken for a header.
ITEM_RE = re.compile(
    r"(?m)^[ \t]*(\d{1,2}[a-z]?)\.[ \t]*(?=\d{2,4}[.\-]\d{3,}|[A-Z(])")

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}

# meeting-level header fields (modern wording)
PRESENT_RE = re.compile(r"COMMISSIONERS?\s+PRESENT:\s*(.+)", re.I)
ABSENT_RE = re.compile(r"COMMISSIONERS?\s+ABSENT:\s*(.+)", re.I)
STAFF_RE = re.compile(r"STAFF\s+IN\s+ATTENDANCE:\s*(.+)", re.I)


def extract_text(path: Path) -> str:
    """Raw text from a .txt (as-is) or .pdf (pdfplumber, page-joined)."""
    if path.suffix.lower() == ".pdf":
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return "\n".join(pg.extract_text() or "" for pg in pdf.pages)
    return path.read_text(encoding="utf-8", errors="replace")


def meeting_date_from_name(name: str, year: int) -> str:
    """ISO date from a filename like '20230105_cal_min.pdf' or 'april-16-2015.txt'.
    (Note: no \\b after the digits — these names run straight into '_', which is a
    word char, so \\b would never match.)"""
    m = re.search(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)", name)   # YYYYMMDD
    if m:
        y, mo, d = map(int, m.groups())
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    s = re.sub(r"\.(txt|pdf|html?)$", "", name, flags=re.I).replace("_", " ").replace("-", " ").lower()
    toks = s.split()
    mo = d = None
    for t in toks:
        if t in MONTHS:
            mo = MONTHS[t]
        elif t.isdigit() and 1 <= int(t) <= 31 and int(t) != year and d is None:
            d = int(t)
    if mo and d:
        return f"{year:04d}-{mo:02d}-{d:02d}"
    return ""


def output_stem(name: str, date: str) -> str:
    """Date-led, collision-resistant stem: '2023-05-11_jntbic_cal_min'. The
    qualifier (letters from the original name, digits dropped) disambiguates the
    several meetings that can share one date (joint hearings, closed sessions)."""
    stem = re.sub(r"\.(txt|pdf|html?)$", "", name, flags=re.I)
    qual = re.sub(r"[^a-z]+", "_", re.sub(r"\d", "", stem.lower())).strip("_")
    qual = qual or "min"
    return f"{date}_{qual}" if date else stem


def split_items(text: str) -> list[str]:
    """Split meeting text into agenda-item blocks on the numbered-item boundary."""
    ms = list(ITEM_RE.finditer(text))
    if not ms:
        return [text.strip()] if text.strip() else []
    spans = [m.start() for m in ms] + [len(text)]
    blocks = []
    for i in range(len(ms)):
        b = text[spans[i]:spans[i + 1]].strip()
        if b:
            blocks.append(b)
    return blocks


def tag_blocks(blocks: list[str]) -> str:
    out = []
    for b in blocks:
        out += ["<<Project Start>>", b.rstrip(), "<<Project End>>"]
    return "\n".join(out)


def meeting_metadata(text: str) -> dict:
    def first(rx):
        m = rx.search(text)
        return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    return {"present": first(PRESENT_RE), "absent": first(ABSENT_RE),
            "staff": first(STAFF_RE)}


def iter_raw_files(years: set[int] | None):
    for ydir in sorted(d for d in RAW.iterdir() if d.is_dir() and d.name.isdigit()):
        year = int(ydir.name)
        if year < FIRST_MODERN_YEAR or (years and year not in years):
            continue
        for f in sorted(ydir.iterdir()):
            if f.suffix.lower() not in (".txt", ".pdf"):
                continue
            if "closed" in f.name.lower():       # closed-session minutes: not land use
                continue
            yield year, f


def parse_years(years: set[int] | None, overwrite: bool) -> list[dict]:
    meta_rows = []
    written: set[Path] = set()
    for year, f in iter_raw_files(years):
        date = meeting_date_from_name(f.name, year)
        stem = output_stem(f.name, date)
        out = TAG / str(year) / f"{stem}.txt"
        i = 2                                   # disambiguate any same-stem collisions
        while out in written:
            out = TAG / str(year) / f"{stem}_{i}.txt"; i += 1
        written.add(out)
        if out.exists() and not overwrite:
            print(f"  · {year}/{f.name}: tagged exists ({out.name}), skip")
            continue
        try:
            text = extract_text(f)
        except Exception as e:
            print(f"  ⚠ {year}/{f.name}: extract failed ({e})")
            continue
        if not text.strip():
            print(f"  ⚠ {year}/{f.name}: no extractable text (scanned PDF?) — skipped")
            continue
        blocks = split_items(text)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(tag_blocks(blocks), encoding="utf-8")
        n_case = sum(1 for b in blocks if re.search(r"\d{2,4}[.\-]\d{3,}[A-Z]", b[:120]))
        md = meeting_metadata(text)
        md.update(year=year, meeting_date=date, source_file=f.name,
                  n_blocks=len(blocks), n_case_blocks=n_case)
        meta_rows.append(md)
        print(f"  ✓ {year}/{f.name} → {out.name}: {len(blocks)} blocks ({n_case} w/ case#)")
    return meta_rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", help="e.g. 2018-2020 or 2015,2023 (default: all ≥2015)")
    ap.add_argument("--overwrite", action="store_true", help="re-tag even if .txt exists")
    args = ap.parse_args(argv)

    years = None
    if args.years:
        years = set()
        for part in args.years.split(","):
            if "-" in part:
                a, b = part.split("-"); years.update(range(int(a), int(b) + 1))
            else:
                years.add(int(part))

    meta_rows = parse_years(years, args.overwrite)

    if meta_rows:
        csv_path = PROC / "modern_meetings_metadata.csv"
        PROC.mkdir(parents=True, exist_ok=True)
        cols = ["year", "meeting_date", "source_file", "n_blocks", "n_case_blocks",
                "present", "absent", "staff"]
        new = not csv_path.exists()
        with csv_path.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            if new:
                w.writeheader()
            for r in meta_rows:
                w.writerow({k: r.get(k, "") for k in cols})
        tot = sum(r["n_blocks"] for r in meta_rows)
        cas = sum(r["n_case_blocks"] for r in meta_rows)
        print(f"\n✓ parsed {len(meta_rows)} meetings → {tot} blocks ({cas} with case#)")
        print(f"  metadata → {csv_path}")
        print("  next: cd labeling_app && python ingest.py   # adds the new items")
    else:
        print("\n(nothing parsed — already tagged? use --overwrite to re-tag)")


if __name__ == "__main__":
    main()
