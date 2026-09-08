#!/usr/bin/env python3
"""
scrape_and_parse_sf_pc_minutes.py
---------------------------------
End-to-end scraper + parser for San-Francisco Planning-Commission
meeting-minutes (1998-2014).

Outputs (under the active locality's subtree, e.g. san_francisco/)
-------
data/meeting_minutes/<locality>/
    ├── raw/<year>/<slug>.html                # frozen originals
    ├── tagged/<year>/<YYYY-MM-DD>.txt        # text with <<Project>> tags
    └── processed/
        └── all_meetings_metadata.csv         # tidy metadata
"""

import re
import csv
import time
import requests
from urllib.parse import urljoin, urlparse
from pathlib import Path
from bs4 import BeautifulSoup, NavigableString

###############################################################################
# --------------------------  CONFIGURATION --------------------------------- #
###############################################################################

# Where to put everything
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import MEETING_MINUTES
MINUTES_DIR = MEETING_MINUTES
RAW_DIR     = MINUTES_DIR / "raw"
PROC_DIR    = MINUTES_DIR / "processed"
TAG_DIR     = MINUTES_DIR / "tagged"
RAW_DIR.mkdir(parents=True, exist_ok=True)
TAG_DIR.mkdir(parents=True, exist_ok=True)

# Index pages that are still reliable (2001-2014 + the 1998-2000 pages)
YEAR_INDEX = {
    "2014": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=3713.html",
    "2013": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=3359.html",
    "2012": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=3057.html",
    "2011": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=2588.html",
    "2010": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=2293.html",
    "2009": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1417.html",
    "2008": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1358.html",
    "2007": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1291.html",
    "2006": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1241.html",
    "2005": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1188.html",
    "2004": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1140.html",
    "2003": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1005.html",
    "2002": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1055.html",
    "2001": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1097.html",
    "2000": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1003.html",
    "1999": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1002.html",
    "1998": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1001.html",
}

MANUAL_LINK_FILE = RAW_DIR / "raw_minutes_data_structure_guide.rtf"  # adjust name if needed

# Friendly delay between HTTP requests
REQUEST_PAUSE_SEC = 0.4

###############################################################################
# --------------------------  SCRAPER  -------------------------------------- #
###############################################################################

def links_from_index_page(index_url: str) -> list[str]:
    """Return absolute URLs of individual minutes pages from one year index."""
    response = requests.get(index_url, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    container = soup.find(id="ctl00_content_Screen") or soup  # fallback to whole page
    links = []

    for a in container.find_all("a", href=True):
        href = a["href"].strip()
        # Skip nav anchors, PDFs, agendas, etc.
        if href.lower().endswith((".htm", ".html")) and "agenda" not in href.lower():
            links.append(urljoin(index_url, href))

    return sorted(set(links))


def links_from_manual_file(path: Path) -> dict[str, list[str]]:
    """Extract all http/https links and bucket them by four-digit year."""
    if not path.exists():
        return {}

    text = path.read_text(encoding="utf-8", errors="ignore")
    urls = re.findall(r"https?://\S+", text)
    by_year: dict[str, list[str]] = {}
    for u in urls:
        m = re.search(r"/(\d{4})/|(\d{4})-", u)  # crude year sniff
        year = m.group(1) or m.group(2) if m else None
        if year and 1998 <= int(year) <= 2001:
            by_year.setdefault(year, []).append(u.strip("{}<>"))
    return {y: sorted(set(v)) for y, v in by_year.items()}

###############################################################################
# --------------------------  PARSER ---------------------------------------- #
###############################################################################

# ----- Regexes (tweak here when the format shifts) ------------------------- #
DAY_RE        = re.compile(r"\b(Monday|Tuesday|Wednesday|Thursday|Friday)\b", re.I)
DATE_RE       = re.compile(
                        r"\b(?:January|February|March|April|May|June|July|August|"
                        r"September|October|November|December)\s+\d{1,2},\s+\d{4}\b",
                        re.I,
                )
MEET_TYPE_RE  = re.compile(r"(Regular|Special|Joint) Meeting", re.I)
PRESENT_RE    = re.compile(r"PRESENT:\s*(.+?)(?:\n|$)", re.I | re.S)
ABSENT_RE     = re.compile(r"ABSENT:\s*(.+?)(?:\n|$)",  re.I | re.S)
STAFF_RE      = re.compile(r"STAFF IN ATTENDANCE:\s*(.+?)(?:\n|$)", re.I | re.S)
ANCHOR_RE     = re.compile(r"^\d+_\d{1,2}_\d{2}$")
# Agenda-item numbers carry an optional letter suffix ("7a.", "12b."), so allow it —
# without the [a-z]? a sub-item like "7a." was missed and got merged into the item above.
AGENDA_ITEM_RE = re.compile(r"^\s*\d+[a-z]?[.)]\s", re.M)

# A numbered agenda item whose title is NOT a case number — a briefing, a "(PLANNER …)"
# header, or an ALL-CAPS title (e.g. "2.  (L. BADINER …)", "2. BRIEFING ON POLICY …").
# Requires the agenda number, so it can't match SPEAKER(S)/ACTION/AYES lines; requires
# '(' or two capitals after it, so it can't match prose like "1. First point". Case-bearing
# items are covered by CASE_HEADER_RE; the two match disjoint lines.
# The number and its title are frequently in DIFFERENT HTML elements, so get_text("\n")
# puts a newline (or a long &nbsp; run then a newline) between them:
#     "13.\n \n \n \n (E. WATTY: (415) 558-6620)"      (2009 pages)
#     "13.\xa0\xa0…\xa0\n       (E.WATTY (415) 558-6620)"    (2011 pages)
# The original same-line form required horizontal whitespace only, so it matched NOTHING on
# those pages and every case-less item (informational hearings, CPMC/Health Commission
# briefings) merged into the land-use item above it. Second branch: the number alone on its
# line, then up to six padding lines, then a planner paren or an ALL-CAPS title. Requiring
# the number to END its line is what keeps this off numbered conditions and findings lists,
# where the text runs on after the number on the same line.
AGENDA_NONCASE_RE = re.compile(
    r"(?m)^[^\S\r\n]*\d{1,2}[a-z]?[.)]"
    r"(?:[^\S\r\n]+(?=\(|[A-Z]{2})"
    r"|[^\S\r\n]*(?:\r?\n[^\S\r\n]*){1,6}(?=\(|[A-Z]{2}))")

# ...but an agenda number is not always a boundary: "SPEAKER(S): Same as those listed in
# Item / 12." wraps the cited number onto its own line, where it is indistinguishable from a
# header. It is a citation, so the preceding word decides.
CROSSREF_BEFORE = re.compile(r"\bitems?$", re.I)


def _is_crossref(text: str, pos: int) -> bool:
    """True when the agenda number at `pos` is cited by a cross-reference, not heading one."""
    pre = re.sub(r"[\s\xa0]+", " ", text[max(0, pos - 60):pos]).rstrip()
    return bool(CROSSREF_BEFORE.search(pre))

# Calendar SECTION dividers (NOT agenda items). SF minutes group items under lettered
# sections ("B. PUBLIC COMMENT", "F. REGULAR CALENDAR", "D. DIRECTOR'S REPORT"). Without a
# boundary here, the administrative tail of a calendar (public comment, commission/director
# matters, findings) merged into the LAST land-use item above it — e.g. 2000.078G swallowed
# the whole PUBLIC COMMENT → DIRECTOR'S REPORT → FINDINGS run (thousands of chars, and a
# stray later "ACTION:" the prefill could grab). The section TITLE is ALL-CAPS and stays
# intact in get_text("\n") even when the lettered prefix splits onto its own line, so we
# anchor on the title. The letter prefix ("F.", "C.COMMISSION") is optional. Match is
# case-SENSITIVE and vocabulary-bounded so prose ("public comment", "Discretionary Review")
# and masthead caps ("PLANNING COMMISSION", "BOARD OF SUPERVISORS") never match.
# The archive prints these titles with a CURLY apostrophe (U+2019) on 2010+ pages and a
# straight one earlier; some pages carry the mojibake "?" instead. A class covering all
# three is the difference between "C. COMMISSIONERS' QUESTIONS AND MATTERS" being a
# boundary and 2009.0464C swallowing 15,000 characters of commissioner comments, the
# director's report and the Board recap.
_APOS = r"['\u2018\u2019\u02bc?]?"

_SECTION_TITLES = [
    r"CONSENT CALENDAR", r"REGULAR CALENDAR",
    r"(?:SPECIAL )?DISCRETIONARY REVIEW (?:CALENDAR|HEARING)",
    r"(?:GENERAL )?PUBLIC COMMENT",
    r"COMMISSIONERS" + _APOS + r" QUESTIONS AND MATTERS", r"QUESTIONS AND MATTERS",
    r"COMMISSION MATTERS", r"DEPARTMENT MATTERS",
    r"DIRECTOR" + _APOS + r"S (?:REPORT|ANNOUNCEMENTS)",
    r"CONSIDERATION OF FINDINGS",
    r"PRELIMINARY (?:MATTERS|ITEMS)",
]
SECTION_HEADER_RE = re.compile(
    r"(?m)^[^\S\r\n]*(?:[A-Z][.)][^\S\r\n]*)?(?:"
    + "|".join(t.replace(" ", r"[^\S\r\n]+") for t in _SECTION_TITLES)
    + r")\b"
)

# Meeting ADJOURNMENT — the last agenda item otherwise swallows everything printed after
# its roll-call: the "Adjournment: 7:12 p.m." line, the draft-minutes-adoption note, and
# (on multi-meeting compilation pages, e.g. 1998) the entire masthead of the NEXT meeting
# (e.g. 98.254D in doc 4763 ran on into the June 11 1998 meeting header). "Adjournment"/
# "ADJOURNMENT" always starts its own line (optionally ":"/"–"/"-"/a time), so it's a clean
# boundary that ends the item above it.
ADJOURN_RE = re.compile(r"(?im)^[^\S\r\n]*ADJOURN(?:MENT|ED)?\b")

# Draft-minutes-adoption appendage — 1998-era pages tack a minutes-adoption / correction
# record ("THE DRAFT MINUTES ARE/WERE PROPOSED FOR ADOPTION AT THE REGULAR MEETING …",
# followed by its own ACTION:/AYES:/ABSENT:) onto the LAST agenda item, so that item
# swallowed a second, unrelated disposition (e.g. 98.350D/DD in doc 4763 absorbed a
# correction to Item #10, 97.629C). The marker starts its own line → clean boundary.
MINUTES_ADOPTION_RE = re.compile(
    r"(?im)^[^\S\r\n]*THE\s+DRAFT\s+MINUTES\s+(?:\w+\s+){0,3}PROPOSED\s+FOR\s+ADOPTION")

# NEW: Flexible case code/header support: 2-digit or 4-digit years.
# Example matches: "98.226D", "1999.668B", "2000.271E", "99.123"
# Suffix allows LOWERCASE letters: some items print the type suffix lowercase
# ("2004.1234d" for a Discretionary Review), which a uppercase-only suffix left undetected
# so the following item merged into the one above (e.g. 2003.0672CE swallowed 2004.1234d).
CASE_CODE_RE   = re.compile(r"\b(?:\d{2}|\d{4})\.\d{3,}(?:[A-Za-z0-9/]+)?\b")
# A header is a case code at line start, OPTIONALLY preceded by its agenda number
# ("6. 2002.0778E", "7a. 2002.0388"). Without the optional prefix, items printed as
# "<n>. <code>" weren't detected as boundaries and consecutive items merged.
# Leading indent uses [^\S\r\n] (any horizontal whitespace incl. non-breaking space
# U+00A0) — some archive pages indent headers with &nbsp;, which [ \t] missed, so those
# items weren't detected and merged into the item above.
# The prefix also allows a BARE letter ("a. 2001.1061CD", "b. 2001.1061CD"): addendum
# items ("THE FOLLOWING ITEMS WERE NOTICED ON AN ADDENDUM …") are lettered without a
# leading number, so a digit-only prefix missed them and they merged into the numbered
# item above. A case code must still follow, so this can't match condition sub-lists.
# Two archive quirks, both of which ran a header straight into its neighbours and so hid a
# second item inside a block. The agenda number may abut the case number ("1a.2013.1521DDV",
# "14.1999.653D"), and the case number may abut the planner parenthesis
# ("2013.1521DDV(T. CHANG: ...)"). Requiring whitespace at either seam missed 173 blocks —
# most of them 2019-2023, where the modern layout has no spaces at all, plus a cluster in
# 2000 where single blocks were holding nine agenda items each.
CASE_HEADER_RE = re.compile(
    r"(?m)^[^\S\r\n]*(?:(?:\d+[a-z]?|[a-z])[.)][^\S\r\n]*)?"
    # "2004. 0164D" — the archive sometimes puts a space after the separator too, which hid
    # a second agenda item inside a block that otherwise looked whole.
    r"(?P<code>(?:\d{2}|\d{4})[.\-][^\S\r\n]*\d{3,}(?:[A-Za-z0-9/]+)?)"
    r"(?:\s*[–-]\s*|\s*:\s*|\s+|(?=\())"
)

def _clean(val: str, multiline=False) -> str:
    if not val:
        return ""
    if multiline:
        val = re.sub(r"\s+", " ", val)
    return val.strip()

def read_page_text(path: Path) -> str:
    """Text of one scraped archive page, routed on CONTENT rather than file extension.

    The 2000-02-03 page was scraped as a PDF but saved as
    `20000203-documentid=32.pdf.html`; handing its bytes to the HTML parser produced one
    23 KB block of `%PDF-1.2 …` binary in place of that meeting's 15 items. Sniffing the
    magic bytes costs nothing and cannot be fooled by a misleading name.
    """
    with path.open("rb") as fh:
        if fh.read(5).startswith(b"%PDF"):
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                return "\n".join((pg.extract_text() or "") for pg in pdf.pages)
    return BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"),
                         "lxml").get_text("\n")


def chop_into_meetings(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """Return [(anchor_name, inner_html)] for each meeting in one page."""
    anchors = [a for a in soup.find_all("a", href=False) if ANCHOR_RE.match(a.get("name", ""))]
    if not anchors:   # single-meeting page, treat whole doc as one
        return [("single", str(soup))]
    sections = []
    for i, a in enumerate(anchors):
        nxt = anchors[i + 1] if i + 1 < len(anchors) else None
        bits = []
        for el in a.next_siblings:
            if el is nxt:
                break
            bits.append(str(el))
        sections.append((a["name"], "".join(bits)))
    return sections

def _split_by_headers(text: str, header_re: re.Pattern) -> list[str]:
    """Split text into blocks using header_re as the boundary marker."""
    matches = list(header_re.finditer(text))
    if not matches:
        return []
    spans = [m.start() for m in matches] + [len(text)]
    blocks = []
    for i in range(len(spans) - 1):
        start = spans[i]
        end   = spans[i+1]
        block = text[start:end].strip()
        if block:
            blocks.append(block)
    return blocks

def add_project_tags(text: str) -> str:
    """
    Wrap project sections with <<Project Start>> / <<Project End>>.

    Preference order for boundaries:
      1) Case headers like "1999.668B – ..." or "98.226D: ..."
      2) Numbered agenda items ("1. ...", "2. ...")
      3) Whole document as one block
    """
    # Normalize source/OCR noise wedged *inside* a case code — a stray '!'/'?' anywhere in
    # the suffix (e.g. "1999.668!BEK" → "1999.668BEK", "2001.1039E!KBMXZ" →
    # "2001.1039EKBMXZ") — otherwise the header isn't detected and the item merges upward.
    text = re.sub(r"((?:\d{2}|\d{4})\.\d{3,})([A-Za-z0-9/!?]+)",
                  lambda m: m.group(1) + re.sub(r"[!?]", "", m.group(2)), text)

    # 1) Boundaries = case-code headers UNION non-case numbered agenda headers (briefings,
    #    "(planner)" items). The union means a case-less agenda item (e.g. "2. BRIEFING ON
    #    POLICY …") no longer merges into the item above it. The two regexes match disjoint
    #    lines (CASE_HEADER_RE needs a case code; AGENDA_NONCASE_RE needs '(' or two capitals
    #    right after the agenda number), so their positions never collide on one line.
    positions = sorted(set(m.start() for m in CASE_HEADER_RE.finditer(text))
                       | set(m.start() for m in AGENDA_NONCASE_RE.finditer(text)
                             if not _is_crossref(text, m.start()))
                       | set(m.start() for m in SECTION_HEADER_RE.finditer(text))
                       | set(m.start() for m in ADJOURN_RE.finditer(text))
                       | set(m.start() for m in MINUTES_ADOPTION_RE.finditer(text)))
    if len(positions) < 2:
        # 2) Fallback: any numbered agenda item
        positions = sorted(m.start() for m in AGENDA_ITEM_RE.finditer(text))
    if positions:
        bounds = positions + [len(text)]
        blocks = [text[bounds[i]:bounds[i + 1]].strip() for i in range(len(bounds) - 1)]
        blocks = [b for b in blocks if b]
    else:
        # 3) Fallback: single block
        blocks = [text.strip()]

    # Emit with tags
    tagged_chunks = []
    for b in blocks:
        tagged_chunks.append("<<Project Start>>")
        tagged_chunks.append(b.rstrip())
        tagged_chunks.append("<<Project End>>")
    return "\n".join(tagged_chunks)

def extract_header(text: str) -> dict:
    day        = _clean(next(iter(DAY_RE.findall(text)), ""), False)
    date       = _clean(next(iter(DATE_RE.findall(text)), ""), False)
    meet_type  = _clean(next(iter(MEET_TYPE_RE.findall(text)), ""), False)
    present    = _clean(next(iter(PRESENT_RE.findall(text)), ""), True)
    absent     = _clean(next(iter(ABSENT_RE.findall(text)), ""), True)
    staff      = _clean(next(iter(STAFF_RE.findall(text)), ""), True)

    # location: first ALL-CAPS line with "ROOM" or "HALL"
    loc = ""
    for line in text.splitlines()[:20]:        # header lives near top
        if line.isupper() and ("ROOM" in line or "HALL" in line or "BUILDING" in line):
            loc = _clean(line, False)
            break

    return dict(
        date=date,
        day_of_week=day,
        meeting_type=meet_type,
        location=loc,
        present=present,
        absent=absent,
        staff=staff
    )

def parse_minutes_page(html: str,
                       origin_url: str,
                       year: str,
                       slug: str,
                       meta_rows: list[dict]):
    """Split into meetings, extract metadata, save tagged text files."""
    soup = BeautifulSoup(html, "lxml")
    meetings = chop_into_meetings(soup)

    for i, (anchor_name, sect_html) in enumerate(meetings, 1):
        text = BeautifulSoup(sect_html, "lxml").get_text("\n")
        meta = extract_header(text)
        if not meta["date"]:
            # fallback: derive date from anchor or slug e.g., 1_08_98
            m = re.search(r"(\d{1,2})_(\d{1,2})_(\d{2})", anchor_name)
            if m:
                month, day, yr = m.groups()
                meta["date"] = f"{int(month):02}/{int(day):02}/19{yr}"
        meta["source_url"] = origin_url
        meta_rows.append(meta)

        # save tagged text
        date_for_file = meta["date"].replace(",", "").replace("/", "-").replace(" ", "_") or f"{year}_{slug}_{i}"
        txt_path = TAG_DIR / year / f"{date_for_file}.txt"
        txt_path.parent.mkdir(parents=True, exist_ok=True)
        txt_path.write_text(add_project_tags(text), encoding="utf-8")

###############################################################################
# --------------------------  DRIVER ---------------------------------------- #
###############################################################################

def main():
    manual_links = links_from_manual_file(MANUAL_LINK_FILE)

    meta_rows: list[dict] = []

    for year, index_url in YEAR_INDEX.items():
        print(f"\n📆 Year {year}")
        year_raw = RAW_DIR / year
        year_raw.mkdir(parents=True, exist_ok=True)

        # Decide where to get the meeting URLs
        if year in manual_links:
            meeting_urls = manual_links[year]
            print(f"   → using {len(meeting_urls)} URLs from manual list")
        else:
            try:
                meeting_urls = links_from_index_page(index_url)
                print(f"   → scraped {len(meeting_urls)} URLs from index page")
            except Exception as e:
                print(f"   ❌ failed to fetch index page: {e}")
                continue

        for j, url in enumerate(meeting_urls, 1):
            try:
                print(f"      [{j}/{len(meeting_urls)}] {url}")
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()

                # persist raw html
                slug = Path(urlparse(url).path).stem or f"page_{j}"
                raw_path = year_raw / f"{slug}.html"
                raw_path.write_bytes(resp.content)

                # parse & tag
                parse_minutes_page(resp.text, url, year, slug, meta_rows)

                time.sleep(REQUEST_PAUSE_SEC)
            except Exception as e:
                print(f"         ⚠️  skipped ({e})")

    # write master CSV
    csv_path = PROC_DIR / "all_meetings_metadata.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if meta_rows:
        fieldnames = list(meta_rows[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(meta_rows)

    print("\n✅ Finished. "
          f"Raw HTML in {RAW_DIR}, tagged text in {TAG_DIR}, metadata CSV → {csv_path}")

###############################################################################
# --------------------------  ENTRY-POINT ----------------------------------- #
###############################################################################

if __name__ == "__main__":
    main()
