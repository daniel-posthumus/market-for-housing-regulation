#!/usr/bin/env python
"""
scrape_minutes.py — consolidated, idempotent scraper for SF Planning Commission
meeting minutes, 1998–present.

Replaces the three overlapping legacy scripts (minutes_scrape_1998_2014.py,
minutes_scrape_2018_2025.py, minutes_scrape_c.py). Two sources, both verified live
on 2026-06-05:

  • 1998–2014  (HTML)  — S3 archive year-index pages (YEAR_INDEX below). Each index
                         lists that year's meeting pages; we save each as raw HTML so
                         no information is lost to <p>-only text extraction.
  • 2015–now   (PDF)   — the live archive page https://sfplanning.org/cpc-hearing-archives
                         links every minutes PDF at a stable host
                         citypln-m-extnl.sfgov.org/.../YYYYMMDD_{cal,cpc}_min.pdf
  • 2018       (PDF)   — direct S3 keys. The archive page lists only two 2018 files
                         (against 44 for 2019); the rest are on S3 but unlinked, so
                         that year is fetched by probing dates, not by scraping links.

Design:
  • requests.Session with retry/backoff, timeout, real User-Agent, polite delay.
  • Content-hash manifest (raw/_manifest.json) → re-runs skip already-downloaded
    files; the existing ~18 GB corpus is never clobbered.
  • CLI flags for year selection, dry-run, refresh, and listing.

Usage:
  python scrape_minutes.py --list                 # show source coverage, fetch nothing
  python scrape_minutes.py --year 2010 --dry-run  # show what WOULD be fetched
  python scrape_minutes.py --year 2010            # fetch one year
  python scrape_minutes.py --era modern           # fetch all 2015+ PDFs (incl. direct-URL years)
  python scrape_minutes.py --year 2018            # fetch the unlisted 2018 minutes
  python scrape_minutes.py                         # fetch everything (idempotent)
  python scrape_minutes.py --year 2010 --refresh  # re-download even if present
"""
from __future__ import annotations
import argparse, datetime, hashlib, json, logging, re, sys, time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

try:                                   # urllib3 v1/v2 compatible import
    from urllib3.util.retry import Retry
except Exception:                      # pragma: no cover
    from requests.packages.urllib3.util.retry import Retry

# ── paths ────────────────────────────────────────────────────────────────────
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import MEETING_MINUTES
RAW = MEETING_MINUTES / "raw"
MANIFEST = RAW / "_manifest.json"

# ── sources ──────────────────────────────────────────────────────────────────
# Verified-live S3 year-index pages for the HTML era (1998–2014).
S3 = ("https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/"
      "planning_dept/sf-planning.org/")
YEAR_INDEX = {
    "1998": S3 + "index.aspx-page=1001.html", "1999": S3 + "index.aspx-page=1002.html",
    "2000": S3 + "index.aspx-page=1003.html", "2001": S3 + "index.aspx-page=1097.html",
    "2002": S3 + "index.aspx-page=1055.html", "2003": S3 + "index.aspx-page=1005.html",
    "2004": S3 + "index.aspx-page=1140.html", "2005": S3 + "index.aspx-page=1188.html",
    "2006": S3 + "index.aspx-page=1241.html", "2007": S3 + "index.aspx-page=1291.html",
    "2008": S3 + "index.aspx-page=1358.html", "2009": S3 + "index.aspx-page=1417.html",
    "2010": S3 + "index.aspx-page=2293.html", "2011": S3 + "index.aspx-page=2588.html",
    "2012": S3 + "index.aspx-page=3057.html", "2013": S3 + "index.aspx-page=3359.html",
    "2014": S3 + "index.aspx-page=3713.html",
}
# Live archive page that lists every modern minutes PDF (2015–present).
MODERN_ARCHIVE = "https://sfplanning.org/cpc-hearing-archives"
# A minutes PDF filename carries the meeting date and ends in _min.pdf; the archive
# links to them across several hosts/paths (citypln-m-extnl.sfgov.org/Agenda_or_Minutes
# for recent years, sfplanning.org/sites/default/files/… for older ones), so we
# identify minutes by the anchor text ("Minutes") or the _min.pdf suffix, NOT by host.
MIN_NAME_RE = re.compile(r"(20\d{2})(\d{2})(\d{2}).*_min\.pdf$", re.I)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TIMEOUT = 30
PAUSE = 0.5                            # polite delay between downloads (seconds)

log = logging.getLogger("scrape_minutes")


# ── manifest helpers ─────────────────────────────────────────────────────────
def load_manifest() -> dict:
    if MANIFEST.exists():
        try:
            return json.loads(MANIFEST.read_text())
        except Exception:
            log.warning("manifest unreadable; starting fresh")
    return {}


def save_manifest(m: dict) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=2, sort_keys=True))


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def is_pdf(b: bytes) -> bool:
    """A real PDF starts with the %PDF magic. Corrupt/partial downloads (and HTML
    error pages served with a .pdf name) don't — this is what let 44 garbage files
    into the corpus undetected."""
    return b[:4] == b"%PDF"


# ── http session ─────────────────────────────────────────────────────────────
def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=1.0,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset({"GET", "HEAD"}))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent": UA})
    return s


def get(session: requests.Session, url: str) -> requests.Response | None:
    try:
        r = session.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        return r
    except requests.RequestException as e:
        log.error("GET failed %s — %s", url, e)
        return None


# ── download with manifest/idempotency ───────────────────────────────────────
def download(session, url: str, dest: Path, manifest: dict,
             dry: bool, refresh: bool) -> str:
    """Return one of: 'fetched', 'skipped', 'dry', 'error'."""
    rel = str(dest.relative_to(RAW))
    if dest.exists() and not refresh:
        if rel not in manifest:        # backfill manifest for pre-existing files
            try:
                manifest[rel] = {"url": url, "sha256": sha256(dest.read_bytes()),
                                 "bytes": dest.stat().st_size}
            except Exception:
                pass
        return "skipped"
    if dry:
        log.info("DRY would fetch %s -> %s", url, rel)
        return "dry"
    r = get(session, url)
    if r is None:
        return "error"
    if dest.suffix.lower() == ".pdf" and not is_pdf(r.content):
        log.error("not a PDF (corrupt source/redirect?) — not saving %s", url)
        return "error"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    manifest[rel] = {"url": url, "sha256": sha256(r.content), "bytes": len(r.content)}
    log.info("fetched %s (%d bytes)", rel, len(r.content))
    time.sleep(PAUSE)
    return "fetched"


# ── 1998–2014 HTML era ───────────────────────────────────────────────────────
def meeting_links(session, index_url: str) -> list[str]:
    r = get(session, index_url)
    if r is None:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    container = soup.find("div", id="ctl00_content_Screen") or soup
    links, seen = [], set()
    for a in container.find_all("a", href=True):
        u = urljoin(index_url, a["href"])
        # keep archive minutes pages (index.aspx-page / min####), drop nav/anchors
        if u in seen or u.startswith("#"):
            continue
        if re.search(r"(index\.aspx-page=\d+|min\d+|documentid=\d+)", u, re.I):
            seen.add(u)
            links.append(u)
    return links


def scrape_html_year(session, year: str, manifest, dry, refresh) -> dict:
    index_url = YEAR_INDEX[year]
    ydir = RAW / year
    # save the index page itself too
    stats = {"fetched": 0, "skipped": 0, "error": 0}
    links = meeting_links(session, index_url)
    if not links:
        log.error("year %s: no meeting links found at index (%s)", year, index_url)
        stats["error"] += 1
        return stats
    log.info("year %s: %d meeting pages listed", year, len(links))
    for u in links:
        name = re.sub(r'[\\/*?:"<>|]', "_", Path(urlparse(u).path).name) or "index.html"
        if not name.endswith(".html"):
            name += ".html"
        res = download(session, u, ydir / name, manifest, dry, refresh)
        if res in stats:
            stats[res] += 1
    return stats


# ── 2015–present PDF era ─────────────────────────────────────────────────────
def modern_minutes_links(session) -> dict[str, str]:
    """{filename: url} for every MINUTES PDF on the archive page, regardless of host.
    A link is minutes if its anchor text is 'Minutes' or its name ends _min.pdf —
    this catches the older /sites/default/files/… links the old host-specific regex
    missed (295 minutes vs 129)."""
    r = get(session, MODERN_ARCHIVE)
    if r is None:
        return {}
    soup = BeautifulSoup(r.text, "html.parser")
    out: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        u = urljoin(MODERN_ARCHIVE, a["href"])
        if not u.lower().endswith(".pdf"):
            continue
        from urllib.parse import unquote
        name = unquote(urlparse(u).path).split("/")[-1]
        is_min = a.get_text(strip=True).lower() == "minutes" or name.lower().endswith("_min.pdf")
        if is_min and "closed" not in name.lower():
            out.setdefault(name, u)           # first (page order) wins
    return out


def scrape_modern(session, want_years: set[str] | None, manifest, dry, refresh) -> dict:
    stats = {"fetched": 0, "skipped": 0, "error": 0}
    links = modern_minutes_links(session)
    if not links:
        log.error("modern archive: no minutes PDFs found at %s", MODERN_ARCHIVE)
        stats["error"] += 1
        return stats
    log.info("modern archive: %d minutes PDFs listed", len(links))
    for name, u in links.items():
        m = MIN_NAME_RE.search(name)
        if not m:
            continue
        year = m.group(1)
        if want_years and year not in want_years:
            continue
        res = download(session, u, RAW / year / name, manifest, dry, refresh)
        if res in stats:
            stats[res] += 1
    return stats


# ── direct-URL years (the 2018 hole) ─────────────────────────────────────────
# Why this path exists: the archive page at MODERN_ARCHIVE does not list 2018. It
# links 44 minutes PDFs for 2019 and 2 for 2018 — the year is simply missing from the
# page, not from the web. The files themselves are still on S3 under the same key
# pattern the two surviving 2018 documents came from (recorded in raw/_manifest.json):
#
#     https://sfplanning.s3.amazonaws.com/commissions/cpcpackets/YYYYMMDD_<suffix>.pdf
#
# The bucket denies ListObjects, so the keys can't be enumerated — they have to be
# guessed and probed. Two facts make that cheap. (1) The Commission sits on Thursdays:
# every one of the 318 modern minutes PDFs in the corpus is dated a Thursday, as is
# every 2018 document the parsed corpus remembers, so 52 candidate dates cover a year.
# (2) The suffix vocabulary is small, and observable from the neighbouring years.
# A missing key answers 403, not 404 — the bucket hides absence rather than reporting
# it — so a HEAD separates present from absent without downloading anything.
DIRECT_S3_PREFIX = "https://sfplanning.s3.amazonaws.com/commissions/cpcpackets/"
DIRECT_URL_YEARS = {"2018"}
# Every suffix that appears on a modern minutes PDF anywhere in the corpus, spelled
# exactly as the department spelled it. S3 keys are case-sensitive and the department
# was not consistent — 20180906_JntHealthCommission_min.pdf serves while the all-lower
# 20180906_jnthealthcommission_min.pdf answers 403 — so each observed casing is its own
# candidate. Downstream naming lowercases (parse_modern_minutes.output_stem), so the
# capitalisation here only has to satisfy S3.
DIRECT_SUFFIXES = (
    "cal_min", "cpc_min", "cal._min",        # the ordinary weekly sitting (212+88+1 files)
    "cal_offsite_min", "joint_offsite_min",  # sittings held away from City Hall
    "jntbic_min", "Jntbic_min", "jntbic_cal_min",     # joint w/ Building Inspection
    "JntHealthCommission_min", "JntHealth_cal_min",   # joint w/ Health
    "jnthealth_cal_min", "jnt_health_cal_min",
    "Jnthrgcpc_min", "jnthpc_cal_min",                # joint w/ Historic Preservation
    "Jnt_RecPark_min", "RecParkJnthrg_min",           # joint w/ Rec & Park
    "RecParkjnthrng_min", "cpc_jnt_RecPark_min",
)


def direct_candidates(year: str) -> list[tuple[str, str]]:
    """[(filename, url)] for every Thursday of `year` x every known suffix."""
    d = datetime.date(int(year), 1, 1)
    out = []
    while d.year == int(year):
        if d.weekday() == 3:                       # Thursday
            for suf in DIRECT_SUFFIXES:
                name = f"{d:%Y%m%d}_{suf}.pdf"
                out.append((name, DIRECT_S3_PREFIX + name))
        d += datetime.timedelta(days=1)
    return out


def key_exists(session, url: str) -> bool:
    """HEAD the S3 key. 200 with a PDF content-type means the object is there; the
    bucket answers 403 AccessDenied for keys that do not exist, so anything else is
    'absent' and no GET is attempted."""
    try:
        r = session.head(url, timeout=TIMEOUT, allow_redirects=True)
    except requests.RequestException as e:
        log.error("HEAD failed %s — %s", url, e)
        return False
    return r.status_code == 200 and "pdf" in (r.headers.get("Content-Type") or "").lower()


def scrape_direct(session, want_years: set[str] | None, manifest, dry, refresh) -> dict:
    """Fetch the direct-URL years by date probe rather than by link scraping."""
    stats = {"fetched": 0, "skipped": 0, "error": 0}
    years = sorted(y for y in DIRECT_URL_YEARS if (not want_years or y in want_years))
    for year in years:
        ydir = RAW / year
        on_disk = {f.name for f in ydir.iterdir()} if ydir.is_dir() else set()
        # macOS is case-insensitive where S3 is not, so `dest.exists()` alone would let a
        # case-variant of a suffix silently shadow a file already held (and, on a
        # case-sensitive filesystem, save the same document twice). Match on the lowered
        # name instead: one document per key, whatever the department capitalised.
        lowered = {n.lower() for n in on_disk}
        cands = direct_candidates(year)
        log.info("direct %s: probing %d candidate keys on %s", year, len(cands),
                 DIRECT_S3_PREFIX)
        found = 0
        for name, url in cands:
            dest = ydir / name
            if name in on_disk and not refresh:
                download(session, url, dest, manifest, dry, refresh)   # backfills manifest
                stats["skipped"] += 1
                found += 1
                continue
            if name.lower() in lowered and not refresh:
                continue                           # same document, different casing
            if not key_exists(session, url):
                time.sleep(PAUSE)                  # polite between probes, not just fetches
                continue
            found += 1
            res = download(session, url, dest, manifest, dry, refresh)
            if res in stats:
                stats[res] += 1
            if res == "fetched":
                on_disk.add(name)
                lowered.add(name.lower())
        log.info("direct %s: %d keys present of %d probed", year, found, len(cands))
    return stats


# ── repair: re-download corrupt local PDFs ───────────────────────────────────
def find_corrupt_modern_pdfs() -> list[Path]:
    """Modern raw PDFs whose bytes aren't a real PDF (failed/partial downloads)."""
    bad = []
    for ydir in sorted(RAW.glob("[12][0-9][0-9][0-9]")):
        if not ydir.is_dir() or int(ydir.name) < 2015:
            continue
        for f in sorted(ydir.glob("*.pdf")):
            if "closed" in f.name.lower():        # closed sessions aren't parsed/used
                continue
            try:
                with open(f, "rb") as fh:
                    head = fh.read(5)
            except Exception:
                head = b""
            if not is_pdf(head):
                bad.append(f)
    return bad


def repair_modern(session, manifest, dry: bool) -> dict:
    """Find corrupt modern PDFs on disk and re-download each from the archive,
    matching by filename and validating the replacement is a real PDF before it
    overwrites the bad file. Self-healing and idempotent."""
    stats = {"repaired": 0, "still_bad": 0, "unlisted": 0}
    corrupt = find_corrupt_modern_pdfs()
    if not corrupt:
        log.info("repair: no corrupt modern PDFs found — nothing to do")
        return stats
    log.info("repair: %d corrupt modern PDFs on disk", len(corrupt))
    index = modern_minutes_links(session)
    log.info("repair: %d minutes links available on archive", len(index))
    for f in corrupt:
        url = index.get(f.name)
        if not url:
            log.warning("repair: NO archive link for %s — needs manual sourcing", f.name)
            stats["unlisted"] += 1
            continue
        if dry:
            log.info("DRY would repair %s ← %s", f.name, url)
            stats["repaired"] += 1
            continue
        r = get(session, url)
        if r is None or not is_pdf(r.content):
            log.error("repair: replacement for %s is missing/not-a-PDF (%s)", f.name, url)
            stats["still_bad"] += 1
            continue
        f.write_bytes(r.content)
        rel = str(f.relative_to(RAW))
        manifest[rel] = {"url": url, "sha256": sha256(r.content), "bytes": len(r.content)}
        log.info("repaired %s (%d bytes) ← %s", rel, len(r.content), url)
        stats["repaired"] += 1
        time.sleep(PAUSE)
    return stats


# ── cli ──────────────────────────────────────────────────────────────────────
def parse_years(spec: str | None) -> set[str] | None:
    if not spec:
        return None
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.update(str(y) for y in range(int(a), int(b) + 1))
        elif part:
            out.add(part)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", help="year or range/list, e.g. 2005 or 2003-2008 or 1998,2014")
    ap.add_argument("--era", choices=["html", "modern", "all"], default="all",
                    help="html=1998-2014, modern=2015+, all=both (default)")
    ap.add_argument("--dry-run", action="store_true", help="list actions, fetch nothing")
    ap.add_argument("--refresh", action="store_true", help="re-download even if present")
    ap.add_argument("--repair", action="store_true",
                    help="re-download only the corrupt modern PDFs already on disk "
                         "(validates each replacement is a real PDF); then exit")
    ap.add_argument("--list", action="store_true", help="print source coverage and exit")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if args.list:
        print("HTML era (S3 index pages): " + ", ".join(sorted(YEAR_INDEX)))
        print(f"Modern era (PDF): 2015–present via {MODERN_ARCHIVE}")
        print("Direct-URL years (archive page omits them): "
              + ", ".join(sorted(DIRECT_URL_YEARS)) + f" via {DIRECT_S3_PREFIX}")
        return 0

    want = parse_years(args.year)
    session = make_session()
    manifest = load_manifest()
    totals = {"fetched": 0, "skipped": 0, "error": 0}

    if args.repair:
        stats = repair_modern(session, manifest, args.dry_run)
        if not args.dry_run:
            save_manifest(manifest)
        log.info("REPAIR DONE — repaired=%(repaired)d still_bad=%(still_bad)d "
                 "unlisted=%(unlisted)d", stats)
        return 0

    do_html = args.era in ("html", "all")
    do_modern = args.era in ("modern", "all")

    if do_html:
        years = sorted(y for y in YEAR_INDEX if (not want or y in want))
        for y in years:
            for k, v in scrape_html_year(session, y, manifest, args.dry_run, args.refresh).items():
                totals[k] = totals.get(k, 0) + v
            if not args.dry_run:
                save_manifest(manifest)

    if do_modern:
        for k, v in scrape_modern(session, want, manifest, args.dry_run, args.refresh).items():
            totals[k] = totals.get(k, 0) + v
        if not args.dry_run:
            save_manifest(manifest)
        # years the archive page omits are fetched by direct S3 key probe instead
        for k, v in scrape_direct(session, want, manifest, args.dry_run, args.refresh).items():
            totals[k] = totals.get(k, 0) + v
        if not args.dry_run:
            save_manifest(manifest)

    log.info("DONE — fetched=%(fetched)d skipped=%(skipped)d error=%(error)d", totals)
    return 0


if __name__ == "__main__":
    sys.exit(main())
