#!/usr/bin/env python3
"""
build_exaction_panel.py
-----------------------
Purpose : tau --- the deterministic, dollar-denominated side of the regulatory wedge. A panel
          giving, for any residential project, the exaction obligation that VESTED for it: the
          inclusionary requirement (on-site, off-site, fee) and each separately assessed impact
          fee, by parcel x application date x tenure x unit-count tier x fee area. Every rate
          and every vesting rule is a claim with a primary-source citation and a verbatim quote
          that this script re-fetches and checks. Spec: .claude/instructions/
          mfhr_next_phase_brief.md, Part 1.
Inputs  : sfbos.org ordinance lists and ordinance PDFs; Legistar's web API
          (webapi.legistar.com/v1/sfgov); the Planning Department's Citywide Development
          Impact Fee Registers (cached by acquire_external_data.py under external/fees/);
          leginfo.legislature.ca.gov for the state rules that bind the vesting date
          output/planning_commission_project/exactions/exaction_sources.csv  (the claims)
          DBI permits, planning records, the parcel-year zoning panel (acquire_external_data)
Outputs : $MFHR_DATA_ROOT/external/legal/            (every primary source, cached with its text)
          $MFHR_DATA_ROOT/external/exactions/         (the schedule, the vesting table, the panel)
          output/planning_commission_project/exactions/{figures,tables}/
Author  : Dan Post
Created : 2026-09-11

Usage
-----
  python build_exaction_panel.py fetch [--refresh]   # ordinance lists; every cited source
  python build_exaction_panel.py claims              # write exaction_sources.csv from CLAIM_SPECS
  python build_exaction_panel.py probe               # verify every claim's quote in its source
  python build_exaction_panel.py build               # rate schedule, vesting table, the panel
  python build_exaction_panel.py report              # memo tables, figures, macros

Notes
-----
The brief's standard of proof governs the design. A legal fact --- a rate, a date, a
threshold, a grandfathering rule --- enters the panel only as a row of exaction_sources.csv
that names its primary source and quotes it. `fetch` caches the source; `probe` checks that
the quote is a substring of the cached text after normalising white space, quotes and dashes;
a claim whose quote is not found is marked unverified and carried into the memo as such
rather than dropped. Nothing is taken from memory or from a secondary summary.

The primary sources, and why each:
  * The Board of Supervisors' per-year ordinance lists (sfbos.org, now served from
    sfbos.archive.sf.gov) --- file number, ordinance number, effective date (from 2011), title
    and the enacted PDF. They are how an ordinance is found; the PDF is what is quoted.
  * The enacted ordinance PDFs: the amended Planning Code text, with the effective-date and
    operative-date clauses.
  * Legistar's JSON API for enactment and final-passage dates the lists do not carry
    (before 2011).
  * The Impact Fee Registers for rates in force by year (they print their own effective date).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from paths import DATA_ROOT                                          # noqa: E402

LEGAL = DATA_ROOT / "external" / "legal"
LEGAL_DOCS = LEGAL / "docs"
LEGAL_INDEX = LEGAL / "docs_index.csv"
ORD_INDEX = LEGAL / "sf_ordinance_index.csv"
LEGISTAR = LEGAL / "legistar"
EXA = DATA_ROOT / "external" / "exactions"
MEMO = HERE.parents[1] / "output" / "planning_commission_project" / "exactions"
FIG, TAB = MEMO / "figures", MEMO / "tables"
CLAIMS = MEMO / "exaction_sources.csv"
CLAIM_CHECK = EXA / "claims_checked.csv"

UA = "Mozilla/5.0 (market-for-housing-regulation research; contact danpost@bu.edu)"
PAUSE = 0.7                     # seconds between requests to one host
ORD_LIST = "https://sfbos.org/ordinances-{y}"
ORD_YEARS = range(1999, 2027)
LEGISTAR_API = "https://webapi.legistar.com/v1/sfgov/matters"


# ═══════════════════════════════════════════════════════════════════════════
# primary sources: fetch, cache, text, verify
# ═══════════════════════════════════════════════════════════════════════════
_S = None
_LAST: dict[str, float] = {}


def session() -> requests.Session:
    global _S
    if _S is None:
        _S = requests.Session()
        _S.headers["User-Agent"] = UA
    return _S


def _get(url: str, timeout: int = 120) -> requests.Response:
    """One polite GET: a pause per host, retries with backoff on 429, 5xx and transport
    errors. Returns the last response (the caller reads its status)."""
    from urllib.parse import urlparse
    host = urlparse(url).netloc
    last = None
    for k in range(5):
        wait = PAUSE - (time.time() - _LAST.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _LAST[host] = time.time()
        try:
            r = session().get(url, timeout=timeout, allow_redirects=True)
        except requests.RequestException as e:
            last = e
            time.sleep(min(60, 2 ** (k + 1)))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            last = r
            time.sleep(min(120, 2 ** (k + 2)))
            continue
        return r
    if isinstance(last, requests.Response):
        return last
    raise last


def norm_text(t: str) -> str:
    """The form a quote is compared in: typographic quotes, dashes and non-breaking spaces
    made plain, white space collapsed. Applied to the source and the quote alike, so neither
    side's typography decides a match."""
    t = (t.replace("‘", "'").replace("’", "'").replace("“", '"')
         .replace("”", '"').replace("–", "-").replace("—", "-")
         .replace("‑", "-").replace(" ", " ").replace("­", ""))
    return re.sub(r"\s+", " ", t).strip()


def doc_id(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def _extract(raw: bytes, ctype: str) -> str:
    if raw[:4] == b"%PDF":
        import pymupdf
        d = pymupdf.open(stream=raw, filetype="pdf")
        return "\n".join(p.get_text() for p in d)
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(raw, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    return soup.get_text(" ")


def source_text(url: str, refresh: bool = False) -> str:
    """The normalised text of a primary source, fetched once and cached with its raw bytes
    and an index row (URL, final URL, status, content type, when, characters). A failed
    fetch caches nothing, so the next run tries again."""
    LEGAL_DOCS.mkdir(parents=True, exist_ok=True)
    d = doc_id(url)
    txt = LEGAL_DOCS / f"{d}.txt"
    if txt.exists() and not refresh:
        return txt.read_text()
    r = _get(url)
    if r.status_code != 200:
        raise RuntimeError(f"{url}: HTTP {r.status_code}")
    # The archive answers a request for one capture with the nearest capture it can serve,
    # silently: on 2026-09-11 the 2019 register's URL came back as the 2020 register. A source
    # is only the source if it is the capture that was cited.
    want = re.search(r"web\.archive\.org/web/(\d{14})", url)
    got = re.search(r"web\.archive\.org/web/(\d{14})", r.url)
    if want and (not got or got.group(1) != want.group(1)):
        raise RuntimeError(f"{url}: the archive served a different capture ({r.url})")
    t = norm_text(_extract(r.content, r.headers.get("content-type", "")))
    ext = "pdf" if r.content[:4] == b"%PDF" else "html"
    (LEGAL_DOCS / f"{d}.{ext}").write_bytes(r.content)
    txt.write_text(t)
    row = pd.DataFrame([{"doc_id": d, "url": url, "final_url": r.url, "status": r.status_code,
                         "content_type": r.headers.get("content-type", ""), "kind": ext,
                         "fetched": pd.Timestamp.now().isoformat(timespec="seconds"),
                         "chars": len(t), "sha256": hashlib.sha256(r.content).hexdigest()}])
    row.to_csv(LEGAL_INDEX, mode="a", header=not LEGAL_INDEX.exists(), index=False)
    return t


def seed_source(url: str, path: Path, note: str) -> str:
    """Cache a local copy of a source under its URL: for a file another stage of the pipeline
    already downloaded from that URL, when the URL no longer serves it."""
    LEGAL_DOCS.mkdir(parents=True, exist_ok=True)
    raw = Path(path).read_bytes()
    t = norm_text(_extract(raw, ""))
    d = doc_id(url)
    ext = "pdf" if raw[:4] == b"%PDF" else "html"
    (LEGAL_DOCS / f"{d}.{ext}").write_bytes(raw)
    (LEGAL_DOCS / f"{d}.txt").write_text(t)
    row = pd.DataFrame([{"doc_id": d, "url": url, "final_url": f"local copy: {path.name} ({note})",
                         "status": 200, "content_type": "", "kind": ext,
                         "fetched": pd.Timestamp.now().isoformat(timespec="seconds"),
                         "chars": len(t), "sha256": hashlib.sha256(raw).hexdigest()}])
    row.to_csv(LEGAL_INDEX, mode="a", header=not LEGAL_INDEX.exists(), index=False)
    return t


def quote_found(quote: str, url: str) -> bool:
    try:
        return norm_text(quote) in source_text(url)
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════
# the ordinance index
# ═══════════════════════════════════════════════════════════════════════════
def fetch_ordinance_index(refresh: bool = False) -> pd.DataFrame:
    """Every ordinance the Board lists, 1999--2026: file number, ordinance number, effective
    date where the list prints one (from 2011), title, and the enacted PDF. Lists before 2011
    have three columns and no effective date."""
    if ORD_INDEX.exists() and not refresh:
        return pd.read_csv(ORD_INDEX, dtype=str).fillna("")
    from bs4 import BeautifulSoup
    rows = []
    for y in ORD_YEARS:
        r = _get(ORD_LIST.format(y=y))
        if r.status_code != 200:
            print(f"  {y}: HTTP {r.status_code}")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        n0 = len(rows)
        for tr in soup.select("table tr"):
            td = tr.find_all("td")
            if len(td) not in (3, 4):
                continue
            a = td[1].find("a")
            cells = [c.get_text(" ", strip=True) for c in td]
            if not re.match(r"^\d{3,6}-\d{2}$", cells[1]):
                continue
            eff = cells[2] if len(td) == 4 else ""
            rows.append({"list_year": y, "file": cells[0], "ord": cells[1], "effective": eff,
                         "title": cells[-1],
                         "pdf": requests.compat.urljoin(r.url, a["href"]) if a and a.has_attr("href")
                         else ""})
        print(f"  {y}: {len(rows) - n0} ordinances")
    df = pd.DataFrame(rows)
    LEGAL.mkdir(parents=True, exist_ok=True)
    df.to_csv(ORD_INDEX, index=False)
    return df


# ── the codified Planning Code, as its publisher served it ──────────────────
# American Legal Publishing serves the Planning Code one section per page
# (codelibrary.amlegal.com/codes/san_francisco/latest/sf_planning/0-0-0-<node>). The live host
# refuses scripts; the Wayback Machine has captured about a thousand of those pages from 2020
# on, each with the section as then in force and its history note --- every ordinance that
# added or amended it, with approval and effective dates. That note is the version chain.
CODE_PREFIX = "codelibrary.amlegal.com/codes/san_francisco/latest/sf_planning/"
CODE_NODES = LEGAL / "planning_code_nodes.csv"
CDX = "http://web.archive.org/cdx/search/cdx"
SEC_TITLE = re.compile(r"SEC\. ?(\d+[A-Z]?(?:\.\d+[A-Z]?)*)\.\s+(.{0,160}?)\s+Annotations")


def wayback(url: str, ts: str) -> str:
    return f"https://web.archive.org/web/{ts}id_/{url}"


def fetch_code_index(refresh: bool = False) -> pd.DataFrame:
    """Every captured section page: node, capture timestamps, and the section number and
    title read from the earliest capture."""
    if CODE_NODES.exists() and not refresh:
        return pd.read_csv(CODE_NODES, dtype=str).fillna("")
    r = _get(f"{CDX}?url={CODE_PREFIX}*&output=json&filter=statuscode:200&fl=original,timestamp"
             f"&limit=200000", timeout=300)
    caps = pd.DataFrame(r.json()[1:], columns=["url", "ts"])
    caps["url"] = caps.url.str.replace(r"^http://", "https://", regex=True).str.split("?").str[0]
    caps["node"] = caps.url.str.extract(r"/(0-0-0-\d+)$")[0]
    caps = caps.dropna(subset=["node"])
    by = caps.groupby("node").agg(url=("url", "first"), first_ts=("ts", "min"),
                                  last_ts=("ts", "max"), n_caps=("ts", "size"),
                                  all_ts=("ts", lambda s: ";".join(sorted(set(s)))))
    rows = []
    for node, r_ in by.iterrows():
        try:
            t = source_text(wayback(r_.url, r_.first_ts))
        except Exception as e:
            rows.append({"node": node, "section": "", "title": f"({type(e).__name__})"})
            continue
        m = SEC_TITLE.search(t)
        rows.append({"node": node, "section": m.group(1) if m else "",
                     "title": m.group(2).strip() if m else ""})
    out = by.reset_index().merge(pd.DataFrame(rows), on="node", how="left")
    out.to_csv(CODE_NODES, index=False)
    return out


def code_history(t: str) -> list[dict]:
    """The section's history note as rows: (ordinance, file, approved, effective, verb)."""
    note = re.search(r"\((Added by .*?)\)\s*(?:\(|Disclaimer|$)", t)
    if not note:
        return []
    out = []
    for m in re.finditer(r"(added|amended|renumbered|repealed|redesignated)\s+(?:and\s+\w+\s+)?by\s+"
                         r"Ord\.\s*(\d+-\d+)(?:\s*,\s*File No\.\s*([\w-]+))?(?:\s*,\s*App\.\s*"
                         r"([\d/]+))?(?:\s*,\s*Eff\.\s*([\d/]+))?", note.group(1), re.I):
        out.append({"verb": m.group(1).lower(), "ord": m.group(2), "file": m.group(3) or "",
                    "approved": m.group(4) or "", "effective": m.group(5) or ""})
    return out


def legistar_matter(file_no: str, refresh: bool = False) -> dict:
    """Legistar's record for a Board file: enactment number and date, final action, title.
    Cached per file."""
    LEGISTAR.mkdir(parents=True, exist_ok=True)
    f = LEGISTAR / f"{file_no}.json"
    if f.exists() and not refresh:
        return json.loads(f.read_text())
    r = _get(f"{LEGISTAR_API}?$filter=MatterFile eq '{file_no}'")
    j = r.json() if r.status_code == 200 else []
    f.write_text(json.dumps(j, indent=1))
    return j


# ── the fee registers: the dollar rates, year by year ──────────────────────
# The Citywide Development Impact Fee Register prints every fee's rate for the year it is
# posted for ("rates effective as of January 1, <year>"). acquire_external_data caches 2019--
# 2026 from sfplanning.org. The earlier registers were posted by DBI and, for 2016, by
# Planning, and survive only in the Wayback Machine; the captures below were found through
# its CDX index on 2026-09-11. No register for 2017 was found under any of the three sites'
# URL patterns, so 2017 is a gap and is drawn as one.
WB = "https://web.archive.org/web/"
_DBI_REG = "https://sfdbi.org/sites/default/files/Documents/Permit_Review_Services/Citywide_Development_Fee_Register/"
HIST_REGISTERS = {
    2011: f"{WB}20230329175535id_/{_DBI_REG}Master_Impact_Fee_Schedule_2011_09_09_DBI_Register5.pdf",
    2012: f"{WB}20230329180816id_/{_DBI_REG}Master_Impact_Fee_Schedule_2011_12_01_DBI_Register.pdf",
    2013: f"{WB}20230329192448id_/{_DBI_REG}Master_Impact_Fee_Schedule_2012_12_01_DBI_Register.pdf",
    2014: f"{WB}20230329204010id_/{_DBI_REG}Master_Impact_Fee_Schedule_2014_11_26_DBI_Register.pdf",
    2015: f"{WB}20141206001035id_/http://sfdbi.org/sites/sfdbi.org/files/Master%20Impact%20Fee%20Schedule%202015%20DBI%20Register%20FINAL.pdf",
    2016: f"{WB}20160405222214id_/http://sf-planning.org/sites/default/files/Master_Impact_Fee_Schedule_2016_DBI_Register_FINAL_02-22-16_Posting.pdf",
    2018: f"{WB}20180802060552id_/https://sfdbi.org/sites/default/files/Impact_Fee_Schedule_2018_notification.pdf",
}
FEES_LOCAL = DATA_ROOT / "external" / "fees"


def registers() -> dict[int, str]:
    import acquire_external_data as ax
    return {**HIST_REGISTERS, **ax.FEE_REGISTERS}


def seed_registers() -> None:
    """The 2019--2026 registers acquire_external_data downloaded are the copies cited: the
    2019 URL is an archive capture that now resolves to the 2020 file, and the undated
    current-register URL is overwritten every January."""
    for y, u in registers().items():
        f = FEES_LOCAL / f"impact_fee_register_{y}.pdf"
        if f.exists():
            seed_source(u, f, "downloaded from this URL by acquire_external_data.py")


# The residential rates, one pattern per rate. The register changed layout three times
# (DBI's 2011--2013 grid, the 2014--2018 posting, Planning's 2019-- posting), so each rate
# has one alternative per layout; the named group `v` is the rate, and the match is kept as
# the rate's quotation. A rate whose patterns all miss in a year is blank for that year.
_N = r"(?P<v>[\d,]+(?:\.\d+)?)"
REG_FEES = [
    # key, label, Planning Code section, unit, patterns
    ("incl_psf", "Inclusionary fee per gsf of residential floor area", "415", "$ / gsf x applicable %",
     [rf"\${_N} per square foot of Gross Floor Area \(as defined in Section 401\) of Residential use"]),
    ("incl_studio", "Inclusionary fee per studio (affordability gap)", "415", "$ / unit",
     [rf"Section 415 .{{0,260}}?Studio - \${_N}"]),
    ("incl_1br", "Inclusionary fee per one-bedroom", "415", "$ / unit",
     [rf"Section 415 .{{0,300}}?1 Bedroom - \${_N}"]),
    ("incl_2br", "Inclusionary fee per two-bedroom", "415", "$ / unit",
     [rf"Section 415 .{{0,340}}?2 Bedroom - \${_N}"]),
    ("tsf_21_99", "Transportation Sustainability Fee, 21--99 units", "411A", "$ / gsf",
     [rf"21-99 Units: \${_N} per gross sq\. ?ft\."]),
    ("tsf_100", "Transportation Sustainability Fee, 100+ units", "411A", "$ / gsf",
     [rf">99 Units: \${_N} per gross sq\. ?ft\."]),
    ("cc_1_9", "Child care fee, residential, 1--9 units", "414A", "$ / gsf",
     [rf"1-9 Units: \${_N} per (?:gross )?square foot"]),
    ("cc_10", "Child care fee, residential, 10+ units", "414A", "$ / gsf",
     [rf"10 Units and Above: \${_N} per (?:gross )?square foot"]),
    ("en_t1", "Eastern Neighborhoods, Tier 1", "423", "$ / gsf",
     [rf"Tier 1: Residential: \${_N}/gsf", rf"Tier 1: \${_N}/square foot for Residential"]),
    ("en_t2", "Eastern Neighborhoods, Tier 2", "423", "$ / gsf",
     [rf"Tier 2: Residential: \${_N}/gsf", rf"Tier 2: \${_N}/square foot for Residential"]),
    ("en_t3", "Eastern Neighborhoods, Tier 3", "423", "$ / gsf",
     [rf"Tier 3: Residential: \${_N}/gsf", rf"Tier 3: \${_N}/square foot for Residential"]),
    ("mo_421", "Market and Octavia community improvements", "421", "$ / gsf",
     [rf"Section 421 Residential \+ Non- ?Residential (?:Gross Square Foot Impact fee or in- ?kind "
      rf"improvement )?(?:\* )?(?:Residential: )?\${_N}(?:/s\.f\. for Residential|/gsf)"]),
    ("mo_416_nct", "Market and Octavia affordable housing fee, NCT", "416", "$ / gsf",
     [rf"Section 416 Residential (?:Gross Square Foot Fee only )?\${_N}/sf for NCT"]),
    ("mo_416_vnm", "Market and Octavia affordable housing fee, Van Ness & Market SUD", "416", "$ / gsf",
     [rf"Section 416 Residential (?:Gross Square Foot Fee only )?\$[\d.]+/sf for NCT and \${_N}(?:/ ?sf| SF) for Van Ness"]),
    ("rh_418", "Rincon Hill community infrastructure", "418", "$ / gsf",
     [rf"Section 418 Residential (?:Gross Square Foot Impact fee or in- ?kind improvement )?\${_N} per "
      rf"gross square foot \(Table 418\.3A\)"]),
    ("soma_418", "SOMA community stabilization (Rincon Hill)", "418.3(d)", "$ / gsf",
     [rf"Community Stabilization Fee Rincon Hill.{{0,170}}?Residential (?:Gross Square Foot Impact fee or "
      rf"in- ?kind improvement )?\${_N} per gross square foot\."]),
    ("vv_420", "Visitacion Valley community facilities", "420", "$ / sf",
     [rf"Section 420 Residential (?:(?:Dwelling Units )?Net Square Foot Impact fee or in- ?kind improvement )?"
      rf"(?:\* )?\${_N} per square foot"]),
    ("bp_422", "Balboa Park community improvements", "422", "$ / gsf",
     [rf"Section 422 Residential \+ Non- ?Residential (?:Gross Square Foot Impact fee or in- ?kind "
      rf"improvement )?(?:\* )?(?:Residential: )?\${_N}(?:/sf for Residential|/gsf)"]),
    ("tc_4246", "Transit Center open space (base)", "424.6", "$ / gsf",
     [rf"Section 424\.6 All (?:Gross Square Foot Impact fee or in- kind improvement )?(?:\* )?"
      rf"Residential: \${_N} base fee"]),
    ("tc_4247", "Transit Center transportation and street (base)", "424.7", "$ / gsf",
     [rf"Section 424\.7 All (?:Gross Square Foot Impact fee or in- kind improvement )?(?:Includes only "
      rf"columns B, C and D of table 424\.7A: )?(?:\* )?Residential: (?:\$[\d.]+ Transit Delay "
      rf"Mitigation Fee \(TDMF\), PLUS )?\${_N} base fee"]),
    ("cs_432", "Central SoMa community services facilities", "432", "$ / gsf",
     [rf"Section 432 Residential \+ Non- Residential Residential: \${_N}/gsf"]),
    ("cs_433_condo", "Central SoMa infrastructure, Tier B, condominium", "433", "$ / gsf",
     [rf"Tier B - Condominium: \${_N}/gsf"]),
    ("cs_433_rental", "Central SoMa infrastructure, Tier B, rental", "433", "$ / gsf",
     [rf"Tier B - Rental: \${_N}/gsf"]),
    ("school", "School impact fee (SFUSD, Education Code 17620)", "EdCode", "$ / sf",
     [rf"Residential per square foot = \${_N}", rf"In-lieu fee \${_N}/ ?\$"]),
]
REG_HEADER = re.compile(r"(?i)updated as of [^)]{0,90}\)|effective january \d{1,2}, \d{4}")


def register_rates() -> pd.DataFrame:
    """Every residential rate every cached register prints: year, key, rate, the register's
    own statement of when its rates took effect, and the matched text as the quotation."""
    rows = []
    for y, u in sorted(registers().items()):
        try:
            t = source_text(u)
        except Exception as e:
            print(f"  register {y}: {e}")
            continue
        hdr = REG_HEADER.search(t)
        for key, label, sec, unit, pats in REG_FEES:
            m = next((m for p in pats for m in [re.search(p, t)] if m), None)
            rows.append({"year": y, "key": key, "label": label, "section": sec, "unit": unit,
                         "rate": float(m.group("v").replace(",", "")) if m else np.nan,
                         "quote": m.group(0) if m else "", "source_url": u,
                         "register_says": hdr.group(0) if hdr else ""})
    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════════════════
# claims
# ═══════════════════════════════════════════════════════════════════════════
CLAIM_COLS = ["claim_id", "object", "field", "value", "unit", "tenure", "tier", "area",
              "applies_from", "applies_to", "source_url", "quote", "note"]


def _claim_sources() -> dict[str, str]:
    """Short names for the cited documents: ordinances by number (their enacted PDF, from
    the Board's list), codified sections by number and capture date, the Board's per-year
    lists (which carry the effective dates), registers by year, and the state sources."""
    idx = pd.read_csv(ORD_INDEX, dtype=str).fillna("").set_index("ord")
    nodes = pd.read_csv(CODE_NODES, dtype=str).fillna("").set_index("section")

    def code(sec: str, ts: str) -> str:
        full = [x for x in nodes.loc[sec, "all_ts"].split(";") if x.startswith(ts)]
        assert full, (sec, ts)
        return wayback(nodes.loc[sec, "url"], full[0])

    src = {f"O{o.split('-')[0].lstrip('0')}_{o.split('-')[1]}": idx.loc[o, "pdf"]
           for o in ("0037-02", "0213-06", "0219-06", "0062-13", "0076-16", "0158-17",
                     "0193-23", "0201-23")}
    src.update({
        "C4153": code("415.3", "20201203"), "C4155": code("415.5", "20201126"),
        "C4156": code("415.6", "20201126"), "C4156b": code("415.6", "20220814"),
        "C402": code("402", "20201202"), "C409": code("409", "20210507"),
        "C411A3": code("411A.3", "20201127"), "C411A5": code("411A.5", "20201203"),
        "C414A3": code("414A.3", "20201126"), "C414A5": code("414A.5", "20201203"),
        "C413_3": code("413.3", "20201127"),
        "C4246": code("424.6", nodes.loc["424.6", "first_ts"][:8]),
        "C432": code("432", nodes.loc["432", "first_ts"][:8]),
        "C433": code("433", nodes.loc["433", "first_ts"][:8]),
        "L2013": ORD_LIST.format(y=2013), "L2016": ORD_LIST.format(y=2016),
        "L2017": ORD_LIST.format(y=2017), "L2023": ORD_LIST.format(y=2023),
        "LEG051685": f"{LEGISTAR_API}?$filter=MatterFile eq '051685'",
        "GOV655895": "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=GOV&sectionNum=65589.5",
        "SB330": "https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=201920200SB330",
    })
    src.update({f"R{y}": u for y, u in registers().items()})
    return src


# One row per legal fact: (claim_id, object, field, value, source, regex, keyword fields).
# The quotation is whatever the regex matches in the cached text of the source, so it is
# verbatim by construction; the regex was written by reading the passage, on the page image
# where the text layer garbles it (the note says so). Struck and inserted text both survive
# in an ordinance's text layer, so a quotation of an amended figure can carry both.
CLAIM_SPECS = [
    # ── the 2002 program (Ord. 37-02) ──
    ("inc2002_apply", "inclusionary", "applies to applications filed on or after", "2001-06-18", "O37_02",
     r"on or after June 18, 2001 for housing projects which", dict(applies_from="2001-06-18",
     note="Section 315.3(a)(1)-(3): building, site, CU and PUD applications filed on or after this date.")),
    ("inc2002_threshold", "inclusionary", "unit threshold", "10", "O37_02", r"consist often or more units; and do not require",
     dict(unit="units", note='OCR reads "of ten" as "often".')),
    ("inc2002_onsite_10", "inclusionary", "on-site share, no CU/PUD", "10", "O37_02",
     r"315\.3\(a\)\(1\), as a condition ofPlanning Department approval ofa project's building pennit, that 10%",
     dict(unit="% of units", tier="10+", applies_from="2001-06-18")),
    ("inc2002_onsite_12", "inclusionary", "on-site share, CU/PUD/live-work", "12", "O37_02",
     r"live/work project that 12 12% ofall units constructed on the project site",
     dict(unit="% of units", tier="10+", applies_from="2001-06-18", note="The line number 12 precedes the figure in the text layer.")),
    ("inc2002_offsite_15", "inclusionary", "off-site share, no CU/PUD", "15", "O37_02",
     r"For projects described in 315\.3\(a\)\(1\), 15% as follows", dict(unit="% of units", tier="10+", applies_from="2001-06-18")),
    ("inc2002_offsite_17", "inclusionary", "off-site share, CU/PUD/live-work", "17", "O37_02",
     r"For projects described in 315\.3\(a\)\(2\),\(3\), and \(4\),170/0 as follows",
     dict(unit="% of units", tier="10+", applies_from="2001-06-18", note='OCR reads "17%" as "170/0"; the page shows 17%.')),
    ("inc2002_fee_basis", "inclusionary", "in-lieu fee basis", "off-site unit count x affordability gap", "O37_02",
     r"The number ofunits required bv Section 315\.5 ifthe project applicant were to elect to 8 meet the requirements ofthis section bv off-site housing development",
     dict(note="Section 315.6(b)(1); (b)(2) is the affordability gap from the 1997 Jobs Housing Nexus Analysis.")),
    ("inc2002_transition", "inclusionary", "transitional on-site share, first 180 days, no CU/PUD", "5", "O37_02",
     r"from the effective date ofthis legislation and 180 days thereafter shall be 5%", dict(unit="% of units")),
    ("inc2002_effective", "inclusionary", "program effective date", "2002-04-05", "O62_13",
     r"\(b\) The effective date of these requirements shall be either April 5, 2002, which is the",
     dict(note="Stated by the 2013 ordinance (62-13) in Section 415.3(b).")),
    # ── 2006 (Ord. 213-06, 219-06) ──
    ("inc2006_5to9", "inclusionary", "threshold lowered to 5 units for first applications on or after", "2006-07-18", "O213_06",
     r"first application, including an environmental evaluation application or any other Planning 17 Department application on or after July 18, 2006",
     dict(tier="5-9", applies_from="2006-07-18")),
    ("inc2006_onsite_15", "inclusionary", "on-site share, all project types", "15", "O219_06",
     r"applicant must constructzc \./5 times the total number",
     dict(unit="% of units", tier="5+", note='The text layer reads ".15" as "./5"; the page (p. 32) shows "10%" struck and "15%" added, now applying also to CU/PUD/live-work projects.')),
    ("inc2006_offsite_20", "inclusionary", "off-site share", "20", "O219_06", r"H% 20% so that a project applicant must construct",
     dict(unit="% of units", tier="5+", note="The struck figure before 20% is garbled in the text layer.")),
    ("inc2006_vesting", "inclusionary", "vesting: first site or building permit before the effective date keeps prior requirements", "first site/building permit", "O219_06",
     r"projects that have received a first site or building permit prior to the effective date of this 3 legislation, the requirements in effect prior to the effective date of this ordinance shall apply",
     dict(note="Section 315.3(d) as amended; the struck text before it had tied the requirements to application dates.")),
    ("inc2006_passed", "inclusionary", "Ord. 219-06 finally passed", "2006-08-01", "O219_06", r"FINALLY PASSED on August 1,2006",
     dict(note="The Board's certification at the end of the enacted PDF.")),
    ("inc2006_sep9", "inclusionary", "the 2006 amendments operative from", "2006-09-09", "O62_13",
     r"approval on or after September 9, compliance option 2006",
     dict(note="Table 415.3 (p. 8), two cells interleaved in the text layer. The page shows the rows 'On-Site units must be priced and sold at 90% of AMI...' (first site or building permit on or after September 9, 2006) and 'Project sponsor must select Program compliance option upon project approval...' (Planning approval on or after September 9, 2006).")),
    ("inc2006_enacted", "inclusionary", "Ord. 219-06 passed date in Legistar", "2006-08-10", "LEG051685", r'"MatterPassedDate": ?"2006-08-10T00:00:00"',
     dict(note="Legistar's record for File 051685. With the standard 30-day clause the effective date is on or about 2006-09-09 (inferred; the Board's pre-2011 lists print no effective date).")),
    # ── 2013 (Ord. 62-13) ──
    ("inc2013_table_fee20", "inclusionary", "fee share, first application on or after 2006-07-18", "20", "O62_13", r"20% Fee",
     dict(unit="% of units", applies_from="2006-07-18", note='Table 415.3, row "Affordable Housing Percentages"; the effective or operative date column reads: all projects that submitted a first application on or after July 18, 2006.')),
    ("inc2013_table_onsite12", "inclusionary", "on-site share, first application on or after 2006-07-18 (as of 62-13)", "12", "O62_13", r"12% on-site\*",
     dict(unit="% of units", applies_from="2006-07-18", note='The page shows "15" struck and "12" added; the text layer garbles the struck figure.')),
    ("inc2013_table_offsite20", "inclusionary", "off-site share, first application on or after 2006-07-18", "20", "O62_13", r"20% off-site\*",
     dict(unit="% of units", applies_from="2006-07-18")),
    ("inc2013_table_july18", "inclusionary", "date column of the percentages row", "2006-07-18", "O62_13", r"application on or after July 18, 2006 \.J\.\.§\.12% on-site\*",
     dict(note="The table is two columns interleaved in the text layer; this is the date cell beside the percentages.")),
    ("inc2013_threshold10", "inclusionary", "threshold back to 10: 5-9 unit projects without a first construction document by", "2013-01-15", "O62_13",
     r"construction document as of January 15,2013", dict(tier="5-9", applies_to="2013-01-15")),
    ("inc2013_table_disclaimer", "inclusionary", "the ordinances prevail over the table", "yes", "O62_13", r"the 23 implementing ordinances shall prevail", {}),
    ("inc2013_effective", "inclusionary", "Ord. 62-13 effective", "2013-05-10", "L2013",
     r"121162 0062-13 05/10/2013 Planning Code - Inclusionary Affordable Housing Program, Updates, and Clarifications", {}),
    # ── 2016 (Ord. 76-16) ──
    ("inc2016_fee20", "inclusionary", "fee share, 10-24 units", "20", "O76_16",
     r"The applicable percentage shall be 20% percent \[or 19 housing development projects consisting of 10 dwelling units or more\. but less than 25 dwelling units",
     dict(unit="% of units", tier="10-24", note='"[or" is the OCR reading of "for".')),
    ("inc2016_fee33", "inclusionary", "fee share, 25+ units", "33", "O76_16", r"consisting of 25 dwelling units or more shall be 21 33%", dict(unit="% of units", tier="25+")),
    ("inc2016_onsite", "inclusionary", "on-site share, 10-24 / 25+ units", "12 / 25", "O76_16", r"that 12% or 25% percent, as applicable", dict(unit="% of units")),
    ("inc2016_fee_timing", "inclusionary", "fee due at first construction document", "first construction document", "O76_16",
     r"at the time of and in no 1 O event later than issuance of the first construction document", {}),
    ("propc2016", "inclusionary", "Proposition C (Charter 16.110) approved by the voters", "2016-06-07", "O158_17",
     r"following voter approval of Proposition Cat the June 7, 2016 election to revise the 13 City Charter's inclusionary affordable housing requirements",
     dict(note="Ord. 158-17, Section 2(a); the text layer runs 'Proposition C at' together.")),
    ("propc2016_contingent", "inclusionary", "76-16's new percentages effective only with the June 2016 Charter amendment", "Charter 16.110", "O76_16",
     r"will become effective only on the effective date of the 16 Charter amendment revising Section 16\.110 at the June 7, 2016 election", {}),
    ("propc2012", "inclusionary", "Proposition C (November 2012) codified in part as Charter 16.110", "2012-11", "O76_16",
     r"In ~V-ovember 2012 the voters amended the Charter by adopting Pr0J3osition C",
     dict(note="Struck text in Ord. 76-16's Section 415.1; the text layer garbles it. It names the Housing Trust Fund amendment; its terms were not read.")),
    ("ord_effective_30days", "procedure", "SF ordinances take effect 30 days after enactment", "30 days", "O76_16",
     r"This ordinance shall become effective 30 days after 16 enactment", dict(note="The standard effective-date clause, here in Ord. 76-16, Section 9.")),
    ("inc2016_effective", "inclusionary", "Ord. 76-16 effective", "2016-06-12", "L2016",
     r"160255 0076-16 06/12/2016 Planning, Administrative Codes - Inclusionary Affordable Housing Fee and Requirements", {}),
    # ── 2017 (Ord. 158-17) ──
    ("inc2017_effective", "inclusionary", "Ord. 158-17 effective", "2017-08-26", "L2017",
     r"161351 0158-17 08/26/2017 Planning Code - Inclusionary Affordable Housing Fee and Dwelling Unit Mix Requirements", {}),
    ("inc2017_cutoff", "inclusionary", "the keep-prior-requirements cutoff moved from 2013-01-01 to 2016-01-12", "2016-01-12", "O158_17",
     r"application prior to January 4, ~ 12, 2016 shall comply with the Affordable Housing Fee",
     dict(note='Section 415.3(b). The page (p. 6) shows "1, 2013" struck and "12, 2016" inserted; the text layer reads the struck part as "4, ~".')),
    ("inc2017_after2016", "inclusionary", "EEA after 2016-01-12: Sections 415.5-415.7 as applicable", "2016-01-12", "O158_17",
     r"Any development project that submits an Environmental Evaluation 18 application after January 12, 2016, shall comply with the requirements set forth in Planning 19 Code Sections 415\.5, 415\.6 and 415\.7, as applicable",
     dict(note="Unchanged text carried from Ord. 76-16. It does not say which version of 415.5-415.7 applies to a project filed between 2016-01-12 and 2017-08-26.")),
    # ── the codified program, 2020 ──
    ("cod_4153_pre2016", "inclusionary", "complete EEA before 2016-01-12 keeps the requirements in effect on 2016-01-12", "2016-01-12", "C4153",
     r"any development project that has submitted a complete Environmental Evaluation application prior to January 12, 2016 shall comply with the Affordable Housing Fee requirements", {}),
    ("cod_4153_25plus", "inclusionary", "grandfathering tiers apply to projects of 25+ units with EEA on or after 2013-01-01", "2013-01-01", "C4153",
     r"For development projects that have submitted a complete Environmental Evaluation application on or after January 1, 2013, the requirements set forth in Planning Code Sections 415\.5 , 415\.6 , and 415\.7 shall apply to certain development projects consisting of 25 dwelling units or more", {}),
    ("cod_4153_on13", "inclusionary", "on-site, 25+, EEA before 2014-01-01", "13", "C4153", r"prior to January 1, 2014 shall provide affordable units in the amount of 13% of the number of units constructed on-site",
     dict(unit="% of units", tier="25+", applies_from="2013-01-01", applies_to="2013-12-31")),
    ("cod_4153_on135", "inclusionary", "on-site, 25+, EEA before 2015-01-01", "13.5", "C4153", r"prior to January 1, 2015 shall provide affordable units in the amount of 13\.5% of the number of units constructed on-site",
     dict(unit="% of units", tier="25+", applies_from="2014-01-01", applies_to="2014-12-31")),
    ("cod_4153_on145", "inclusionary", "on-site, 25+, EEA on or before 2016-01-12", "14.5", "C4153", r"on or prior to January 12, 2016 shall provide affordable units in the amount of 14\.5% of the number of units constructed on-site",
     dict(unit="% of units", tier="25+", applies_from="2015-01-01", applies_to="2016-01-12")),
    ("cod_4153_fee25", "inclusionary", "fee/off-site, 25+, EEA before 2014-01-01", "25", "C4153", r"prior to January 1, 2014, shall pay a fee or provide off-site housing in an amount equivalent to 25% of the number of units constructed on-site",
     dict(unit="% of units", tier="25+", applies_from="2013-01-01", applies_to="2013-12-31")),
    ("cod_4153_fee275", "inclusionary", "fee/off-site, 25+, EEA before 2015-01-01", "27.5", "C4153", r"prior to January 1, 2015, shall pay a fee or provide off-site housing in an amount equivalent to 27\.5% of the number of units constructed on-site",
     dict(unit="% of units", tier="25+", applies_from="2014-01-01", applies_to="2014-12-31")),
    ("cod_4153_fee30", "inclusionary", "fee/off-site, 25+, EEA on or before 2016-01-12", "30", "C4153", r"on or prior to January 12, 2016 shall pay a fee or provide off-site housing in an amount equivalent to 30% of the number of units constructed on-site",
     dict(unit="% of units", tier="25+", applies_from="2015-01-01", applies_to="2016-01-12")),
    ("cod_4153_deadline", "inclusionary", "grandfathered tiers lapse if not approved on or before 2018-12-07", "2018-12-07", "C4153",
     r"In the event the project has not been approved, which shall mean approval following any administrative appeal to the relevant City board, on or before December 7, 2018", {}),
    ("cod_4153_exempt", "inclusionary", "projects with a final first discretionary approval on or before 2016-01-12 keep their existing approvals", "2016-01-12", "C4153",
     r"any housing development project that has procured a final first discretionary development entitlement approval, which shall mean approval following any administrative appeal to the relevant City board, on or before January 12, 2016", {}),
    ("cod_4153_threshold", "inclusionary", "unit threshold", "10", "C4153", r"shall apply to any housing project that consists of 10 or more units", dict(unit="units")),
    ("cod_4155_fee20", "inclusionary", "fee share, 10-24 units", "20", "C4155", r"For housing development projects consisting of 10 dwelling units or more, but less than 25 dwelling units, the applicable percentage shall be 20%",
     dict(unit="% of units", tier="10-24")),
    ("cod_4155_fee33", "inclusionary", "fee share, 25+ owned", "33", "C4155", r"the applicable percentage shall be 33% if such units are Owned Units", dict(unit="% of units", tier="25+", tenure="ownership")),
    ("cod_4155_fee30", "inclusionary", "fee share, 25+ rental", "30", "C4155", r"the applicable percentage shall be 30% if such units are Rental Units in a Rental Housing Project", dict(unit="% of units", tier="25+", tenure="rental")),
    ("cod_4155_vesting", "inclusionary", "vesting: fee fixed by the date of a complete EEA", "complete EEA date", "C4155",
     r"The applicable amount of the inclusionary housing fee shall be determined based upon the date that the project sponsor has submitted a complete Environmental Evaluation application", {}),
    ("cod_4155_30months", "inclusionary", "vesting lapses without a building or site permit within 30 months of approval", "30 months", "C4155",
     r"In the event the project sponsor does not procure a building permit or site permit for construction of the principal project within 30 months of the project's approval", {}),
    ("cod_4155_timing", "inclusionary", "fee paid at the time required by Section 402(d)", "402(d)", "C4155", r"at the time required by Section 402 \(d\)", {}),
    ("cod_4155_dbl", "inclusionary", "fee imposed on density-bonus units", "yes", "C4155",
     r"The fee shall be imposed on any additional units or square footage authorized and developed under California Government Code Sections 65915 et seq\.", {}),
    ("cod_4156_on12", "inclusionary", "on-site, 10-24 units", "12", "C4156", r"the number of affordable units constructed on-site shall generally be 12% of all units constructed on the project site",
     dict(unit="% of units", tier="10-24")),
    ("cod_4156_on20", "inclusionary", "on-site, 25+ owned", "20", "C4156", r"For any housing development project consisting of 25 or more Owned Units, the number of affordable units constructed on-site shall generally be 20%",
     dict(unit="% of units", tier="25+", tenure="ownership")),
    ("cod_4156_on18", "inclusionary", "on-site, 25+ rental", "18", "C4156", r"For any Rental Housing Project consisting of 25 or more Rental Units, the number of affordable units constructed on-site shall generally be 18%",
     dict(unit="% of units", tier="25+", tenure="rental")),
    ("cod_4156_esc_small", "inclusionary", "escalation 10-24: +0.5 each January 1 from 2018 to 15%", "0.5", "C4156",
     r"Starting on January 1, 2018, and no later than January 1 of each year thereafter, MOHCD shall increase the percentage of units required on-site for projects consisting of 10 - 24 units, as set forth in Section 415\.6 \(a\)\(1\), by increments of 0\.5% each year, until such requirement is 15%",
     dict(unit="percentage points a year", tier="10-24", applies_from="2018-01-01")),
    ("cod_4156_esc_large1", "inclusionary", "escalation 25+: +1.0 for 2018 and 2019", "1.0", "C4156",
     r"For all development projects with 25 or more Owned or Rental Units, the required on-site affordable ownership housing to satisfy this Section 415\.6 shall increase by 1\.0% annually for two consecutive years starting January 1, 2018",
     dict(unit="percentage points a year", tier="25+", applies_from="2018-01-01", applies_to="2019-12-31")),
    ("cod_4156_esc_large2", "inclusionary", "escalation 25+: +0.5 each year from 2020", "0.5", "C4156",
     r"Starting January 1, 2020, the increase to on-site rental and ownership developments with 25 or more units shall increase by 0\.5% annually",
     dict(unit="percentage points a year", tier="25+", applies_from="2020-01-01")),
    ("cod_4156_caps", "inclusionary", "caps: 26 owned, 24 rental", "26 / 24", "C4156",
     r"The total on-site inclusionary affordable housing requirement shall not exceed 26% for development projects consisting of Owned Units or 24% for development projects consisting of Rental Units",
     dict(unit="% of units", tier="25+")),
    ("cod_4156_vesting", "inclusionary", "vesting: on-site share fixed by the date of a complete EEA", "complete EEA date", "C4156",
     r"The applicable amount of the percentage required for the on-site housing units shall be determined based upon the date that the project sponsor has submitted a complete Environmental Evaluation application", {}),
    ("cod_4156b_same", "inclusionary", "the 2022 text keeps the rates (Ownership/Rental Housing Project wording)", "20 / 18", "C4156b",
     r"For any Ownership Housing Project consisting of 25 or more units, the number of Affordable Units constructed on-site shall generally be 20%", {}),
    # ── 2023 (Ord. 201-23, which restates 187-23): pipeline relief, the 2023-26 window, the 2026 schedule ──
    ("inc2023_effective", "inclusionary", "Ord. 201-23 effective", "2023-11-12", "L2023",
     r"230855 0201-23 11/12/2023 Planning, Administrative Codes - Development Impact Fee Reductions", {}),
    ("a415_pipeline", "inclusionary", "Pipeline Project: approved before 2023-11-01 with no first construction document by then", "2023-11-01", "O201_23",
     r"\"Pipeline Proiect\" means a residential or live/work proiect that .{0,200}?Finally Approved prior to November 1, 2023, and \(3\) has not been issued a First Construction Document prior to November 1, 2023", {}),
    ("a415_final_approval", "inclusionary", "Final Approval: approval of the first Development Application, unless appealed", "first development application", "O201_23",
     r"\"Finally Approved\" or \"Final Approval\" shall mean\(\]\) approval of a proiect's first Development Application, unless such approval is appealed", {}),
    ("a415_request", "inclusionary", "pipeline sponsors may request the reduction on or before 2026-11-01", "2026-11-01", "O201_23",
     r"On or before November 1, 2026, pro;ect sponsors of Pipeline Pro;ects shall be entitled to request a modification", {}),
    ("a415_fee164", "inclusionary", "pipeline fee share, 10+ units", "16.4", "O201_23",
     r"Ownership or Rental Housing Pipeline Pro;ects consisting of:J:a 10 units or more\. the applicable percentage shall be 16\. 4%",
     dict(unit="% of units", tier="10+", note='Section 415A.4(a)(1). The page (p. 7) shows "25" struck and "10" inserted.')),
    ("a415_onsite12_small", "inclusionary", "pipeline on-site share, 10-24 units", "12", "O201_23",
     r"For Pipeline Projects consisting of 10 units or more, but less than 25 units, the applicable percentage shall be 12%", dict(unit="% of units", tier="10-24")),
    ("a415_onsite12_large", "inclusionary", "pipeline on-site share, 25+ units", "12", "O201_23",
     r"For Pipeline Proiects consisting of 25 units or more\. the number of Affordable Units constructed on-site shall be 12% of all units constructed on the proiect site",
     dict(unit="% of units", tier="25+")),
    ("a415_offsite164", "inclusionary", "pipeline off-site share, 25+ units", "16.4", "O201_23",
     r"For Pipeline Proiects consisting of 25 units or more\. the applicable percentage shall be 16\. 4%", dict(unit="% of units", tier="25+")),
    ("b415_window", "inclusionary", "415B: approved 2023-11-01 to 2026-11-01, first construction document within 30 months", "2023-11-01 to 2026-11-01", "O201_23",
     r"Finally Approved, as defined in Planning Code Section 415A\.2, between November 1, 2023 and November 1\. 2026, provided that such proiects receive a First Construction Document within 30 months from Final Approval", {}),
    ("b415_fee205", "inclusionary", "415B fee share, 25+ units", "20.5", "O201_23",
     r"SB\.1\. AFFORDABLE HOUSING FEE\. If a proiect sponsor elects to pay the a/fordable housing fee under Section 415\. 5.{0,260}?the applicable percentage shall be 20\. 5%",
     dict(unit="% of units", tier="25+")),
    ("b415_onsite15", "inclusionary", "415B on-site share, 25+ units", "15", "O201_23",
     r"consisting of ?25 or more units, the number of Affordable Units constructed on-site shall be 15% of all units constructed on the proiect site",
     dict(unit="% of units", tier="25+")),
    ("area_specific_rates", "inclusionary", "areas with their own requirement: SUDs, area plans, 415.3(d), 419 (UMU), 428", "area-specific", "O201_23",
     r"located in an area with a specific affordable housing requirement set forth in a Special Use District, Area Plan\. or in any other section ofthe Code, including 415\.3\(d\), 419, or 428", {}),
    ("b415_sunset", "inclusionary", "415B expires 2026-11-01 unless extended", "2026-11-01", "O201_23",
     r"This section 415B shall expire by operation o\[law on November 1, 2026", {}),
    ("o201_operative", "inclusionary", "Section 7 (the new 415.3-415.7) operative", "2026-11-21", "O201_23",
     r"Section 7 of this ordinance, amending Planning Code Section 415\.3, 415\.5, 415\.6, 415\.7, 419\.3, 428 and 428\.3, shall become operative on November 21, 2026",
     dict(note="Unless the City changes the operative date or the amendments before then.")),
    ("o201_2026_fee27", "inclusionary", "2026 schedule: fee share, 25+ owned", "27", "O201_23",
     r"For development projects consisting of 25 units or more, the applicable percentage shall be 27%J\.:J-% if such units are Owned Units",
     dict(unit="% of units", tier="25+", tenure="ownership", applies_from="2026-11-21", note='The text layer garbles the struck "33%".')),
    ("o201_2026_fee245", "inclusionary", "2026 schedule: fee share, 25+ rental", "24.5", "O201_23",
     r"applicable percentage shall be 24\.5o/oJ\.f\)!#, if the development project is a Rental Housing Project",
     dict(unit="% of units", tier="25+", tenure="rental", applies_from="2026-11-21", note='The page (p. 26) shows "24.5%" inserted and "30%" struck.')),
    ("o201_2026_onsite15", "inclusionary", "2026 schedule: on-site share, 10-24 units", "15", "O201_23",
     r"consisting of IO dwelling units or more, but less than 25 dwelling units, the number of affordable units constructed on-site shall generally be 15% of all units",
     dict(unit="% of units", tier="10-24", applies_from="2026-11-21")),
    ("o201_2026_fee20", "inclusionary", "2026 schedule: fee share, 10-24 units", "20", "O201_23",
     r"\(A\) For housing development projects consisting of 10 units or more, but less than 25 units, the applicable percentage shall be 20%\. \(B\) For development projects consisting of 25 units or more, the applicable percentage shall be 27%",
     dict(unit="% of units", tier="10-24", applies_from="2026-11-21")),
    ("o201_2026_on20o", "inclusionary", "2026 schedule: on-site share, 25+ ownership", "20", "O201_23",
     r"\(2\) For any Ownership Housing Proiect consisting of ?25 or more units, the number of Affordable Units constructed on-site shall generally be 20% of all units",
     dict(unit="% of units", tier="25+", tenure="ownership", applies_from="2026-11-21")),
    ("o201_2026_on18r", "inclusionary", "2026 schedule: on-site share, 25+ rental", "18", "O201_23",
     r"Rental Housing Proiect consisting of ?25 or more units, the number of Affordable Units constructed on-site shall generally be I 8% of all units",
     dict(unit="% of units", tier="25+", tenure="rental", applies_from="2026-11-21", note='OCR reads "18%" as "I 8%".')),
    ("o201_2026_off27o", "inclusionary", "2026 schedule: off-site share, 25+ ownership", "27", "O201_23",
     r"Ownership Housing Project consisting of 25 or more units, the number of Affordable Units constructed off-site shall be \.JJ\.\.%27% of all units",
     dict(unit="% of units", tier="25+", tenure="ownership", applies_from="2026-11-21", note='The text layer garbles the struck "33%".')),
    ("o201_2026_esc", "inclusionary", "2026 schedule: the 25+ escalation restarts on 2028-01-01", "2028-01-01", "O201_23",
     r"Starting on January 1, ~2028, and no later than January 1 of ea",
     dict(note='The page (p. 31) shows "2018" struck and "2028" inserted, and the 10-24 escalation struck.')),
    # ── impact fees: timing, indexing, application ──
    ("fee_402d_timing", "impact fees", "all impact fees due at the first construction document", "first construction document", "C402",
     r"All impact fees are due and payable to the Development Fee Collection Unit at DBI at the time of, and in no event later than, issuance of the \"first construction document\"", {}),
    ("fee_409_index", "impact fees", "annual January 1 adjustment by the Annual Infrastructure Construction Cost Inflation Estimate (not the inclusionary fee)", "January 1", "C409",
     r"shall adjust the dollar amount of any development fee on an annual basis every January 1 based solely on the Annual Infrastructure Construction Cost Inflation Estimate", {}),
    ("fee_rate_at_payment", "impact fees", "before 2023-10-16: the rate in force when the fee is paid, whatever the filing date", "payment date", "R2019",
     r"The adjusted fee rates apply to development impact fees paid on or after the effective date of any such fee adjustments, regardless of the date of permit filing",
     dict(note="The register's own introduction; the 2016 and 2018 registers carry the same sentence.")),
    ("fee_193_approval", "impact fees", "from 2023-10-16: types and rates fixed at Final Approval, no later increase (not the inclusionary fee)", "final approval", "O193_23",
     r"with the exception of the Inclusionary Housing Fee as set forth in Section 415 et seq\., the assessment shall be based on the types of fees and the rates o\{those fees in effect at the time of Final Approval", {}),
    ("fee_193_effective", "impact fees", "Ord. 193-23 effective", "2023-10-16", "L2023",
     r"230764 0193-23 10/16/2023 Planning, Building Codes - Development Impact Fee Indexing, Deferral, and Waivers; Adoption of Nexus Study", {}),
    ("fee_tfr33", "impact fees", "fees assessed by 2026-11-01 cut by 33% for projects with a first construction document within 30 months of Final Approval", "33", "O201_23",
     r"The following development fees assessed on or before November 1, 2026 shall be reduced by 33% for \(I\) proiects that receive a First Construction Document",
     dict(unit="% of the fee", note="Section 403; the list that follows includes 411A, 414A, 416, 418, 420-424, 432 and 433, and not 415.")),
    ("fee_tfr_tsf", "impact fees", "the reduction covers the TSF", "411A", "O201_23", r"The Transportation Sustainability Fee \(Section 411A\)", {}),
    ("tsf_threshold", "TSF", "residential: more than 20 new units", "21", "C411A3", r"More than twenty new dwelling units", dict(unit="units")),
    ("tsf_base", "TSF", "residential base rates at adoption, 21-99 / 100+ units", "7.74 / 8.74", "C411A5",
     r"Residential, 21-99 units \$7\.74 for all gsf of Residential use in the first 99 dwelling units", dict(unit="$ per gsf")),
    ("tsf_created", "TSF", "created by Ord. 200-15, effective 2015-12-25", "2015-12-25", "C411A5", r"Added by Ord\. 200-15 , File No\. 150790, App\. 11/25/2015, Eff\. 12/25/2015", {}),
    ("cc_threshold", "child care", "residential: at least one net new unit", "1", "C414A3", r"At least one net new dwelling unit", dict(unit="units")),
    ("cc_created", "child care", "created by Ord. 2-16, effective 2016-02-18", "2016-02-18", "C414A3", r"Added by Ord\. 2-16 , File No\. 150793, App\. 1/19/2016, Eff\. 2/18/2016", {}),
    ("cc_base", "child care", "base rates at adoption, 10+ / up to 9 units", "1.83 / 0.91", "C414A5",
     r"Residential projects of 10 or more units Residential Projects of up to 9 units \$1\.83/gsf \$0\.91/gsf", dict(unit="$ per gsf")),
    ("jhl_nonres", "jobs-housing linkage", "applies only to projects adding 25,000+ gsf of commercial uses", "non-residential", "C413_3",
     r"that increases by 25,000 or more gross square feet the total amount of any combination of the following uses; entertainment, hotel, Integrated PDR, office, research and development, retail, and/or Small Enterprise Workspace",
     dict(note="Section 413.3(a)(1); (b)(1) excludes any other space. A residential project owes it only on such commercial space.")),
    # ── area fees: when each began (the effective-date column of the 2012 register; the code notes for later ones) ──
    ("area_en_start", "area fees", "Eastern Neighborhoods infrastructure fee in effect from", "2008-12-19", "R2012", r"\(Table 423\.3B\)\. 12/19/2008", dict(area="Eastern Neighborhoods")),
    ("area_mo421_start", "area fees", "Market and Octavia community infrastructure fee in effect from", "2008-04-03", "R2012", r"\(Table 421\.3B\)\. 4/3/2008", dict(area="Market and Octavia")),
    ("area_mo416_start", "area fees", "Market and Octavia affordable housing fee in effect from", "2008-05-30", "R2012", r"\(Table 416\.3A\)\. 5/30/2008", dict(area="Market and Octavia")),
    ("area_rh_start", "area fees", "Rincon Hill community infrastructure fee in effect from", "2005-08-19", "R2012", r"\(Table 418\.3B\)\. 8/19/2005", dict(area="Rincon Hill")),
    ("area_soma_start", "area fees", "SOMA community stabilization fee in effect from", "2005-08-19", "R2012", r"\$11\.65 per gross square foot\. 8/19/2005", dict(area="Rincon Hill")),
    ("area_vv_start", "area fees", "Visitacion Valley fee in effect from", "2005-11-18", "R2012", r"\$4\.87 per square foot 11/18/2005", dict(area="Visitacion Valley")),
    ("area_bp_start", "area fees", "Balboa Park fee in effect from", "2009-04-17", "R2012", r"\(Table 422\.3B\)\. 4/17/2009", dict(area="Balboa Park")),
    ("area_tc_start", "area fees", "Transit Center fees in effect from", "2012-09-07", "C4246", r"Added by Ord\. 182-12 , File No\. \d+, App\. [\d/]+, Eff\. 9/7/2012", dict(area="Transit Center")),
    ("area_cs_start", "area fees", "Central SoMa fees in effect from", "2019-01-12", "C433", r"Added by Ord\. 296-18 , File No\. \d+, App\. [\d/]+, Eff\. 1/12/2019", dict(area="Central SoMa")),
    # ── state rule ──
    ("sb330_freeze", "state", "a housing project is subject only to the standards in effect at a complete preliminary application", "preliminary application", "SB330",
     r"a housing development project shall be subject only to the ordinances, policies, and standards adopted and in effect when a preliminary application including all of the information required by subdivision \(a\) of Section 65941\.1 was submitted", {}),
    ("sb330_index_exception", "state", "exception: automatic annual fee adjustment by a cost index", "cost index", "SB330",
     r"an increase resulting from an automatic annual adjustment based on an independently published cost index that is referenced in the ordinance or resolution establishing the fee or other monetary exaction", {}),
    ("sb330_chapter", "state", "SB 330 chaptered", "Stats. 2019, ch. 654", "SB330", r"Senate Bill No\. 330 CHAPTER 654", {}),
    ("sb330_approved", "state", "SB 330 approved by the Governor", "2019-10-09", "SB330", r"Approved by Governor October 09, 2019\.", {}),
    ("gov_standards_fees", "state", '"ordinances, policies, and standards" includes development impact fees', "yes", "GOV655895",
     r"including those relating to development impact fees, capacity or connection fees or charges, permit or processing fees, and other exactions", {}),
]


def curate_claims() -> pd.DataFrame:
    """Write exaction_sources.csv from CLAIM_SPECS and the register rates. A spec whose regex
    finds nothing is still written, with an empty quotation, so `probe` reports it unverified
    rather than the claim vanishing."""
    src = _claim_sources()
    rows = []
    for cid, obj, field, value, s, rx, kw in CLAIM_SPECS:
        url = src[s]
        try:
            m = re.search(rx, source_text(url))
        except Exception as e:
            print(f"  {cid}: {e}")
            m = None
        if not m:
            print(f"  MISSING {cid}: {s}")
        rows.append({"claim_id": cid, "object": obj, "field": field, "value": value,
                     "unit": kw.get("unit", ""), "tenure": kw.get("tenure", ""),
                     "tier": kw.get("tier", ""), "area": kw.get("area", "citywide"),
                     "applies_from": kw.get("applies_from", ""), "applies_to": kw.get("applies_to", ""),
                     "source_url": url, "quote": m.group(0) if m else "", "note": kw.get("note", "")})
    reg = register_rates()
    for r in reg[reg.rate.notna()].itertuples():
        rows.append({"claim_id": f"reg{r.year}_{r.key}", "object": "register rate", "field": r.label,
                     "value": f"{r.rate:g}", "unit": r.unit, "tenure": "", "tier": "", "area": "",
                     "applies_from": str(r.year), "applies_to": "", "source_url": r.source_url,
                     "quote": r.quote, "note": f"Register: {r.register_says}"})
    c = pd.DataFrame(rows, columns=CLAIM_COLS)
    MEMO.mkdir(parents=True, exist_ok=True)
    c.to_csv(CLAIMS, index=False)
    print(f"{len(c)} claims ({(c.object == 'register rate').sum()} register rates) → {CLAIMS}")
    return c


def load_claims() -> pd.DataFrame:
    return pd.read_csv(CLAIMS, dtype=str, keep_default_na=False)


def probe_claims() -> pd.DataFrame:
    """Every claim checked against its cached source: `verified` is yes when the quote is a
    substring of the normalised source text, no when the source was fetched and the quote is
    not in it, and `unfetched` when the source could not be read."""
    c = load_claims()
    out = []
    for r in c.itertuples():
        if not r.source_url:
            status = "no source"
        else:
            try:
                t = source_text(r.source_url)
                status = "yes" if r.quote and norm_text(r.quote) in t else "no"
            except Exception as e:
                status = f"unfetched ({type(e).__name__})"
        out.append(status)
    c["verified"] = out
    EXA.mkdir(parents=True, exist_ok=True)
    c.to_csv(CLAIM_CHECK, index=False)
    print(c.verified.value_counts().to_string())
    bad = c[c.verified.ne("yes")]
    if len(bad):
        print(bad[["claim_id", "verified", "source_url"]].to_string())
    return c


# ═══════════════════════════════════════════════════════════════════════════
# the schedule: which version of the law vested for a project
# ═══════════════════════════════════════════════════════════════════════════
class Law:
    """The verified claims, read by id. Every date and percentage the schedule uses comes
    through here, so a figure in the code that disagrees with its source cannot run: the
    claim must be verified, and the value the code reads is the claim's value."""

    def __init__(self):
        c = pd.read_csv(CLAIM_CHECK, dtype=str, keep_default_na=False).set_index("claim_id")
        self.c = c
        self.used: set[str] = set()

    def v(self, cid: str) -> str:
        r = self.c.loc[cid]
        if r.verified != "yes":
            raise RuntimeError(f"claim {cid} is not verified ({r.verified})")
        self.used.add(cid)
        return r.value

    def d(self, cid: str) -> pd.Timestamp:
        return pd.Timestamp(self.v(cid))

    def f(self, cid: str, i: int = 0) -> float:
        return float(self.v(cid).split("/")[i])


def key_dates(L: Law) -> dict:
    D = {"app2002": L.d("inc2002_apply"), "eff2002": L.d("inc2002_effective"),
         "app2006": L.d("inc2006_5to9"),
         # 219-06's effective date is inferred: Legistar's passed date plus the standard
         # 30-day clause. The Board's lists before 2011 print no effective date.
         "eff2006": L.d("inc2006_sep9"),
         "eff2006_inferred": L.d("inc2006_enacted") + pd.Timedelta(days=int(L.v("ord_effective_30days").split()[0])),
         "fcd2013": L.d("inc2013_threshold10"), "eff2013": L.d("inc2013_effective"),
         "gf_from": L.d("cod_4153_25plus"), "cut2016": L.d("cod_4153_pre2016"),
         "eff2016": L.d("inc2016_effective"), "gf_deadline": L.d("cod_4153_deadline"),
         "eff2017": L.d("inc2017_effective"), "pipe": L.d("a415_pipeline"),
         "b415_end": L.d("b415_sunset"), "oper2026": L.d("o201_operative"),
         "fee193": L.d("fee_193_effective"), "tsf": L.d("tsf_created"), "cc": L.d("cc_created"),
         "tfr_end": L.d("a415_request")}
    return D


def esc_onsite(L: Law, year: int, tier: str, tenure: str) -> float:
    """158-17's on-site share for a complete EEA filed in `year` (Section 415.6 as codified:
    the percentage is the one in force on the EEA date). Computed from the rule; MOHCD's own
    published table was not fetched."""
    if tier == "10-24":
        base, step, cap = L.f("cod_4156_on12"), L.f("cod_4156_esc_small"), 15.0
        return min(base + step * max(0, year - 2017), cap)
    base = L.f("cod_4156_on18") if tenure == "rental" else L.f("cod_4156_on20")
    cap = L.f("cod_4156_caps", 1) if tenure == "rental" else L.f("cod_4156_caps", 0)
    s1, s2 = L.f("cod_4156_esc_large1"), L.f("cod_4156_esc_large2")
    esc = 0.0 if year <= 2017 else s1 * min(year - 2017, 2) + s2 * max(0, year - 2019)
    return min(base + esc, cap)


def inclusionary_terms(A, P, F, n, cu: bool, L: Law, D: dict) -> dict:
    """The requirement that vested, as (fee, on-site, off-site) shares of units, for a rental
    and for an ownership project (they differ only for 25+ units under 158-17). A is the
    application date (the complete EEA, or its proxy), P the approval date, F the first
    construction document. Each branch names the claims it rests on; where the law is silent
    the branch says what rule was adopted, as `rule`."""
    out = {"regime": "", "threshold": np.nan, "rule": "", "exempt": False}
    def rates(fee, on, off, fee_o=None, on_o=None, off_o=None):
        out.update(fee_r=fee, on_r=on, off_r=off, fee_o=fee if fee_o is None else fee_o,
                   on_o=on if on_o is None else on_o, off_o=off if off_o is None else off_o)
    rates(np.nan, np.nan, np.nan)
    if pd.isna(A) or pd.isna(n):
        out["regime"] = "undated"
        return out
    # A project not yet approved is held to the version in force today (flagged)
    Pv = P if pd.notna(P) else (F if pd.notna(F) else D["today"])
    if pd.isna(P) and pd.isna(F):
        out["rule"] = "pending: the version in force at retrieval"
    if A < D["app2002"] or (pd.notna(Pv) and Pv < D["eff2002"]):
        out.update(regime="before the program", exempt=True)
        return out
    if A < D["app2006"] or Pv < D["eff2006"] or (pd.notna(F) and F < D["eff2006"]):
        # 37-02. The 2006 percentages bind projects whose first application was on or after
        # 2006-07-18 (62-13's Table 415.3: inc2013_table_july18), and not a project with a
        # first site or building permit before the amendments took effect (inc2006_vesting,
        # inc2006_sep9). The same table's percentages row is what 62-13 amended, so a project
        # filed before 2006-07-18 keeps the 2002 percentages whenever it is approved.
        out.update(regime="2002 (Ord. 37-02)", threshold=L.f("inc2002_threshold"))
        rates(L.f("inc2002_offsite_17") if cu else L.f("inc2002_offsite_15"),
              L.f("inc2002_onsite_12") if cu else L.f("inc2002_onsite_10"),
              L.f("inc2002_offsite_17") if cu else L.f("inc2002_offsite_15"))
        out["rule"] = (out["rule"] + "; " if out["rule"] else "") + "fee = the off-site unit count (inc2002_fee_basis)"
    elif Pv < D["eff2013"]:
        out.update(regime="2006 (Ords. 213-06, 219-06)", threshold=5.0)
        rates(L.f("inc2006_offsite_20"), L.f("inc2006_onsite_15"), L.f("inc2006_offsite_20"))
    elif A < D["cut2016"]:
        big = n >= 25 and A >= D["gf_from"] and Pv >= D["eff2016"]
        if big and Pv <= D["gf_deadline"]:
            if A < pd.Timestamp("2014-01-01"):
                on, fee = L.f("cod_4153_on13"), L.f("cod_4153_fee25")
            elif A < pd.Timestamp("2015-01-01"):
                on, fee = L.f("cod_4153_on135"), L.f("cod_4153_fee275")
            else:
                on, fee = L.f("cod_4153_on145"), L.f("cod_4153_fee30")
            out.update(regime="2016 grandfathered tiers (Ord. 76-16, 415.3(b))", threshold=10.0)
            rates(fee, on, fee)
        elif big:
            out.update(regime="2017 (Ord. 158-17), grandfathering lapsed", threshold=10.0)
            out["rule"] = "approved after 2018-12-07: the current Sections 415.5-415.7 (cod_4153_deadline)"
            rates(L.f("cod_4155_fee30"), esc_onsite(L, A.year, "25+", "rental"), L.f("cod_4155_fee30"),
                  L.f("cod_4155_fee33"), esc_onsite(L, A.year, "25+", "ownership"), L.f("cod_4155_fee33"))
        else:
            out.update(regime="2013 (Ord. 62-13)", threshold=L.f("cod_4153_threshold"))
            rates(L.f("inc2013_table_fee20"), L.f("inc2013_table_onsite12"), L.f("inc2013_table_offsite20"))
    elif Pv < D["eff2016"]:
        out.update(regime="2013 (Ord. 62-13)", threshold=L.f("cod_4153_threshold"))
        rates(L.f("inc2013_table_fee20"), L.f("inc2013_table_onsite12"), L.f("inc2013_table_offsite20"))
    elif Pv < D["eff2017"]:
        # The law does not say which version binds a project filed after 2016-01-12 and
        # approved before 158-17 took effect (inc2017_after2016); the version in force at
        # approval is used.
        out.update(regime="2016 (Ord. 76-16)", threshold=10.0,
                   rule="EEA after 2016-01-12 approved before 2017-08-26: the version at approval")
        if n >= 25:
            rates(L.f("inc2016_fee33"), L.f("inc2016_onsite", 1), L.f("inc2016_fee33"))
        else:
            rates(L.f("inc2016_fee20"), L.f("inc2016_onsite", 0), L.f("inc2016_fee20"))
    elif Pv >= D["oper2026"]:
        # 201-23 Section 7, operative 2026-11-21 unless amended first; its escalation
        # restarts only in 2028 (o201_2026_esc). The rental off-site share was not read.
        out.update(regime="2026 (Ord. 201-23, s. 7)", threshold=L.f("cod_4153_threshold"),
                   rule="operative 2026-11-21 unless the City amends it first")
        if n >= 25:
            rates(L.f("o201_2026_fee245"), L.f("o201_2026_on18r"), np.nan,
                  L.f("o201_2026_fee27"), L.f("o201_2026_on20o"), L.f("o201_2026_off27o"))
        else:
            rates(L.f("o201_2026_fee20"), L.f("o201_2026_onsite15"), L.f("o201_2026_fee20"))
    else:
        out.update(regime="2017 (Ord. 158-17)", threshold=L.f("cod_4153_threshold"))
        if n >= 25:
            rates(L.f("cod_4155_fee30"), esc_onsite(L, A.year, "25+", "rental"), L.f("cod_4155_fee30"),
                  L.f("cod_4155_fee33"), esc_onsite(L, A.year, "25+", "ownership"), L.f("cod_4155_fee33"))
            if D["pipe"] <= Pv < D["b415_end"]:
                out.update(regime="2023 window (Ord. 201-23, 415B)",
                           rule="415B applies only with a first construction document within 30 months")
                rates(L.f("b415_fee205"), L.f("b415_onsite15"), L.f("b415_fee205"))
        else:
            rates(L.f("cod_4155_fee20"), esc_onsite(L, A.year, "10-24", ""), L.f("cod_4155_fee20"))
    if n < out["threshold"]:
        out["exempt"] = True
    # 5-9 unit projects without a first construction document by 2013-01-15 left the program
    if out["regime"].startswith("2006") and n < 10 and (pd.isna(F) or F >= D["fcd2013"]):
        out["exempt"] = True
    return out


def pipeline_relief(P, F, n, L: Law, D: dict) -> dict:
    """415A: a project approved before 2023-11-01 without a first construction document by
    then may ask, until 2026-11-01, for the reduced requirement. It is an option the sponsor
    takes or not; the panel carries it beside the vested requirement, not in place of it."""
    ok = pd.notna(P) and P < D["pipe"] and (pd.isna(F) or F >= D["pipe"]) and n >= 10
    if not ok:
        return {"pipe_eligible": False, "pipe_fee": np.nan, "pipe_on": np.nan}
    return {"pipe_eligible": True, "pipe_fee": L.f("a415_fee164"),
            "pipe_on": L.f("a415_onsite12_large") if n >= 25 else L.f("a415_onsite12_small")}


# ── dollar rates by year, from the registers ───────────────────────────────
RATE_GAP_YEARS = (2017,)


def rate_table() -> pd.DataFrame:
    """Year x rate key from the verified register claims. A year with no register (2017) is
    the geometric mean of its neighbours and is flagged; before the first register there is
    no rate."""
    c = pd.read_csv(CLAIM_CHECK, dtype=str, keep_default_na=False)
    c = c[c.object.eq("register rate") & c.verified.eq("yes")]
    c["year"] = c.applies_from.astype(int)
    c["key"] = c.claim_id.str.replace(r"^reg\d{4}_", "", regex=True)
    w = c.pivot_table(index="year", columns="key", values="value", aggfunc="first").astype(float)
    for y in RATE_GAP_YEARS:
        if y not in w.index and y - 1 in w.index and y + 1 in w.index:
            w.loc[y] = np.sqrt(w.loc[y - 1] * w.loc[y + 1])
    return w.sort_index()


# ── fee areas ────────────────────────────────────────────────────────────────
# Names in the Neighborhood-Specific Impact Fee Areas layer (ntc3-dd64, joined to parcels by
# acquire_external_data's `spatial` stage) → the register rate that area charges. The layer
# is today's; an area is charged only from the date its fee took effect (the area_* claims).
AREA_RULES = [
    (r"^Eastern Neighborhoods Infrastructure Impact Fee - Tier 1(?: if residential.*)?$", "en_t1", "area_en_start", ""),
    (r"^Eastern Neighborhoods Infrastructure Impact Fee - Tier 2$", "en_t2", "area_en_start", ""),
    (r"^Eastern Neighborhoods Infrastructure Impact Fee - Tier 3$", "en_t3", "area_en_start", ""),
    (r"^Eastern Neighborhoods Infrastructure Impact Fee - Tier 1 for", "en_t1", "area_en_start",
     "tier set by height: Tier 1 charged, a lower bound"),
    (r"^Eastern Neighborhoods Infrastructure Impact Fee$", "en_t1", "area_en_start",
     "tier not in the layer: Tier 1 charged, a lower bound"),
    (r"^Market and Octavia Community Infrastructure Impact Fee$", "mo_421", "area_mo421_start", ""),
    (r"^Market and Octavia Inclusionary Affordable Housing Fee$", "mo_416", "area_mo416_start", ""),
    (r"^Rincon Hill Community Infrastructure Impact Fee$", "rh_418", "area_rh_start", ""),
    (r"^South of Market Area Community Stabilization Fee$", "soma_418", "area_soma_start", ""),
    (r"^Visitacion Valley", "vv_420", "area_vv_start", ""),
    (r"^Balboa Park", "bp_422", "area_bp_start", ""),
    (r"^Transit Center Open Space Fee$", "tc_4246", "area_tc_start", ""),
    (r"^Transit Center Transportation and Street Improvement Fee$", "tc_4247", "area_tc_start", ""),
    (r"^Central SoMa Community Services Facilities Fee", "cs_432", "area_cs_start", ""),
    (r"^Central SoMa Infrastructure Impact Fee - Tier B$", "cs_433", "area_cs_start", ""),
]
# Layers the panel reads but does not charge, and why: 419 (UMU) and 424 change the
# inclusionary requirement or depend on FAR above 6:1; the Central SoMa CFD is a special tax;
# the Downtown Park and Union Square fees are office fees; North of Market's applies only to
# height exceptions above 80 feet; Central SoMa 433 prints a residential rate for Tier B only.
AREA_NOT_CHARGED = r"UMU District|Van Ness and Market|Facilities District Tax|Downtown Park|Union Square|North of Market|Infrastructure Impact Fee - Tier [AC]$"


def parcel_areas() -> pd.DataFrame:
    """blklot → the fee-area keys its polygon carries, with notes."""
    pan = pd.read_parquet(DATA_ROOT / "external" / "zoning" / "parcel_zoning_panel.parquet",
                          columns=["blklot", "year", "feearea"])
    pan = pan[pan.year.eq(pan.year.max()) & pan.feearea.notna()]
    rows = []
    for b, fa in zip(pan.blklot, pan.feearea):
        keys, notes, vnm = [], [], "Van Ness and Market" in fa
        for name in fa.split("; "):
            for rx, key, start, note in AREA_RULES:
                if re.search(rx, name):
                    keys.append((key, start))
                    if note:
                        notes.append(note)
                    break
        rows.append({"blklot": b, "keys": sorted(set(keys)), "area_note": "; ".join(sorted(set(notes))),
                     "vnm_sud": vnm, "umu": "UMU District" in fa, "feearea": fa})
    return pd.DataFrame(rows)


# ── the project frame ───────────────────────────────────────────────────────
DENSITY_BONUS = re.compile(r"(?i)density bonus|\b65915\b|home[- ]sf|\bsdb\b|state density")
OWNERSHIP = re.compile(r"(?i)\bcondo(?:minium)?s?\b|\bownership\b|for[- ]sale")
RENTAL = re.compile(r"(?i)\brental\b|\bfor[- ]rent\b")
ALL_AFFORDABLE = re.compile(r"(?i)100\s*% (?:affordable|housing)|supportive housing|homeless|"
                            r"interim housing|\bmohcd\b|\bhope sf\b")
APPROVE = {"approved", "did_not_take_dr", "took_dr_and_approved", "took_dr"}
APP_TYPES = {"ENV", "PRJ", "PPA"}
GSF_BOUNDS = (300, 3000)          # a gsf-per-unit outside this is a record error, not a building
ALT_NOT_A_PROJECT = re.compile(r"(?i)\brevision|\baddend|\bref\.?\s*(?:pa|app|#|permit|\d)|crane|fire only|"
                               r"legaliz|\bgenerator|radio|sprinkler|fire alarm|parking stall")


def project_frame() -> tuple[pd.DataFrame, dict]:
    """One row per residential project filed from the program's start: every development-
    relevant DBI permit that adds at least one unit, with a Commission case collapsed to its
    principal permit. Dates, units, floor area, tenure and density-bonus flags, each with the
    source it came from."""
    import analyze_permit_content as apc
    import acquire_external_data as ax
    from normalize import blklot
    B = apc.load_permits(inventory=False)
    p, lk, it = B["p"], B["lk"], B["it"]
    p = p.assign(net_units=p.proposed_units - p.existing_units.fillna(0))
    p["estimated_cost"] = p.estimated_cost.where(p.estimated_cost.gt(1))
    # An alteration counts only when it records both unit counts and adds units: a fire-alarm
    # or crane-tie permit in a 62-unit building carries proposed_units = 62 and no existing
    # count, and read as "62 new units" it priced a radio system at $2.7 million.
    newc = p.permit_type.isin(["1", "2"])
    alt = p.permit_type.eq("3") & p.existing_units.notna()
    res = p[p.dev & (newc | alt) & p.proposed_units.ge(1) & p.net_units.ge(1)
            & p.filed_date.ge("2001-06-18")].copy()
    # Revisions, addenda and equipment permits on a building filed as alterations repeat the
    # building's unit count; legalisations of existing units are not new housing.
    res = res[~(res.permit_type.eq("3") & res.description.fillna("").str.contains(ALT_NOT_A_PROJECT))]
    # Even after that screen the alteration rows are shoring, fire-main and elevator permits
    # for new buildings as often as they are conversions, so they are counted and set aside:
    # the panel prices new construction.
    n_alt = int(res.permit_type.eq("3").sum())
    res = res[res.permit_type.isin(["1", "2"])].copy()
    res["kind"] = "new construction"
    res["units"] = res.proposed_units
    rec = ax.build_records()
    # planning records → permits (the records' own `building_permits` field)
    br = rec[["record_id", "record_type", "open_date", "stem", "building_permits", "description",
              "residential_prop", "residential_exist", "number_of_units_prop", "sb330"]].copy()
    br["permits"] = br.building_permits.map(ax.permits_from)
    brx = br.explode("permits").dropna(subset=["permits"])
    brx = brx[brx.permits.isin(set(res.stem))]
    # the Commission side: each case's linked residential permits → its principal permit
    link = lk[lk.tier.isin(["t1", "t2"]) & lk.stem.isin(set(res.stem))]
    p_idx = res.set_index("stem")
    items = it[it.cn.ne("")]
    appr = items[items.action.isin(APPROVE)].groupby("cn").meeting_date.min()
    rtype = items.sort_values("meeting_date").groupby("cn").request_type.agg(lambda s: set(s))
    descr = items.groupby("cn").project_descr.agg(lambda s: " ".join(map(str, s)))
    cstem = items.groupby("cn").stem.first()
    case_rows = []
    for cn, g in link.groupby("cn"):
        ps = apc.principal_permit(set(g.stem), p_idx, earliest=False)
        if ps:
            case_rows.append({"stem": ps, "cn": cn, "case_approved": appr.get(cn, pd.NaT),
                              "cu": bool({"conditional_use", "planned_unit_development"} & rtype.get(cn, set())),
                              "case_descr": descr.get(cn, ""), "case_stem": cstem.get(cn, "")})
    cases = pd.DataFrame(case_rows).sort_values("case_approved").drop_duplicates("stem")
    f = res.merge(cases, on="stem", how="left")
    f["linked_case"] = f.cn.notna()
    # application date: the earliest ENV/PRJ/PPA record of the case, else of any record that
    # lists the permit, else the DBI filing (flagged)
    app = rec[rec.record_type.isin(APP_TYPES)].dropna(subset=["open_date"])
    by_stem = app[app.stem.ne("")].groupby("stem").open_date.min()
    by_permit = brx[brx.record_type.isin(APP_TYPES)].groupby("permits").open_date.min()
    a_case = f.case_stem.map(by_stem)
    a_perm = f.stem.map(by_permit)
    f["app_date"] = a_case.fillna(a_perm).fillna(f.filed_date)
    f["app_source"] = np.where(a_case.notna(), "planning record (case)",
                               np.where(a_perm.notna(), "planning record (permit)", "DBI filing"))
    f["app_date"] = f[["app_date", "filed_date"]].min(axis=1)
    # approval: the Commission's approving action, else DBI's approval, else issuance
    f["approval"] = f.case_approved.fillna(f.approved_date).fillna(f.issued_date)
    f["approval_source"] = np.where(f.case_approved.notna(), "Commission action",
                                    np.where(f.approved_date.notna(), "DBI approval",
                                             np.where(f.issued_date.notna(), "DBI issuance", "")))
    f["fcd"] = f.first_construction_document_date.fillna(f.issued_date)
    f["fcd_source"] = np.where(f.first_construction_document_date.notna(), "DBI first construction document",
                               np.where(f.issued_date.notna(), "DBI issuance", ""))
    # floor area: a PRJ record's residential floor area where one is linked and plausible;
    # otherwise units x the median gsf per unit of PRJ records with 10+ units (assumed)
    prj = br[br.record_type.eq("PRJ")].copy()
    for c in ("residential_prop", "residential_exist", "number_of_units_prop"):
        prj[c] = pd.to_numeric(prj[c], errors="coerce")
    prj["gpu"] = prj.residential_prop / prj.number_of_units_prop
    ok = prj.gpu.between(*GSF_BOUNDS) & prj.number_of_units_prop.ge(10)
    gpu_med = float(prj.loc[ok, "gpu"].median())
    gpu_q = (float(prj.loc[ok, "gpu"].quantile(.25)), float(prj.loc[ok, "gpu"].quantile(.75)))
    prj_ok = prj[prj.gpu.between(*GSF_BOUNDS)]
    g_case = prj_ok[prj_ok.stem.ne("")].groupby("stem")[["residential_prop", "residential_exist"]].first()
    g_perm = (prj_ok.explode("permits").dropna(subset=["permits"])
              .groupby("permits")[["residential_prop", "residential_exist"]].first())
    gp = f.case_stem.map(g_case.residential_prop).fillna(f.stem.map(g_perm.residential_prop))
    ge = f.case_stem.map(g_case.residential_exist).fillna(f.stem.map(g_perm.residential_exist))
    # a PRJ record can cover a whole multi-building site while the permit is one building:
    # its floor area is used only when it is plausible for this permit's unit count
    plaus = (gp / f.units).between(*GSF_BOUNDS)
    gp, ge = gp.where(plaus), ge.where(plaus)
    f["gfa"] = gp.where(gp.notna(), f.units * gpu_med)
    f["gfa_net"] = (gp - ge.fillna(0)).clip(lower=0).where(gp.notna(), f.net_units * gpu_med)
    f["gfa_source"] = np.where(gp.notna(), "PRJ record", "assumed (units x median)")
    # SB 330 preliminary applications, density bonus, tenure words
    sb = set(rec.loc[rec.sb330.eq("CHECKED"), "stem"]) - {""}
    f["sb330"] = f.case_stem.isin(sb)
    rdesc = rec[rec.stem.ne("")].groupby("stem").description.agg(lambda s: " ".join(map(str, s)))
    txt = (f.description.fillna("") + " " + f.case_descr.fillna("") + " " +
           f.case_stem.map(rdesc).fillna(""))
    dp = pd.read_csv(DATA_ROOT / "external" / "pipeline" / "development_pipeline.csv.gz", dtype=str)
    dp_sd = set(dp.loc[dp.state_density.eq("True"), "case_no"].map(ax.case_stem)) - {""}
    f["density_bonus"] = txt.str.contains(DENSITY_BONUS) | f.case_stem.isin(dp_sd)
    # MOHCD's pipeline states tenure and the 415 compliance choice for the projects it
    # monitors; Housing Production states the choice (or 100% affordable) by permit
    mo = pd.read_csv(DATA_ROOT / "external" / "pipeline" / "mohcd_affordable_pipeline.csv.gz", dtype=str)
    mo["stem"] = mo.planning_case_number.map(ax.case_stem)
    mo = mo[mo.stem.ne("")].drop_duplicates("stem").set_index("stem")
    hp = pd.read_csv(DATA_ROOT / "external" / "pipeline" / "housing_production.csv.gz", dtype=str)
    hp["stem"] = hp.bpa.map(ax.digits)
    hp = hp[hp.stem.ne("")].drop_duplicates("stem").set_index("stem")
    f["hp_affordability"] = f.stem.map(hp.project_affordability_type).fillna("")
    f["mohcd_tenure"] = f.case_stem.map(mo.housing_tenure).fillna("").str.lower()
    f["mohcd_415"] = f.case_stem.map(mo.section_415_declaration).fillna("")
    aff_pct = pd.to_numeric(f.case_stem.map(mo.affordable_percent), errors="coerce")
    f["affordable_100"] = (f.hp_affordability.eq("100% Affordable") | aff_pct.ge(100)
                           | txt.str.contains(ALL_AFFORDABLE))
    own, rent = txt.str.contains(OWNERSHIP), txt.str.contains(RENTAL)
    words = np.where(own & ~rent, "ownership", np.where(rent & ~own, "rental", ""))
    f["tenure"] = np.where(f.mohcd_tenure.isin(["ownership", "rental"]), f.mohcd_tenure, words)
    f["tenure_source"] = np.where(f.mohcd_tenure.isin(["ownership", "rental"]), "MOHCD pipeline",
                                  np.where(words != "", "description words", ""))
    f["blklot"] = [blklot(b_, l_) for b_, l_ in zip(f.block, f.lot)]
    meta = {"n_alterations_set_aside": n_alt, "gpu_med": gpu_med, "gpu_q": gpu_q, "gpu_n": int(ok.sum()), "retrieved": B["retrieved"]}
    return f, meta


def obligations(f: pd.DataFrame, L: Law, D: dict, R: pd.DataFrame, areas: pd.DataFrame) -> pd.DataFrame:
    """The vested requirement and its dollar value, per project. The inclusionary requirement
    is priced at the fee (the price at which a sponsor may buy out of building the units: an
    upper bound on what the requirement costs a sponsor who builds instead). Impact fees are
    gross: credits for existing uses on the site are not taken."""
    ymin, ymax = int(R.index.min()), int(R.index.max())

    def rate(key, when):
        if pd.isna(when) or key not in R.columns:
            return np.nan
        y = int(min(when.year, ymax))
        return R.at[y, key] if y >= ymin and y in R.index else np.nan

    amap = areas.set_index("blklot")
    rows = []
    start = {cid: L.d(cid) for cid in {s for _, _, s, _ in AREA_RULES}}
    tfr = L.f("fee_tfr33") / 100
    for r in f.itertuples():
        n = r.units
        inc = inclusionary_terms(r.app_date, r.approval, r.fcd, n, bool(r.cu) if pd.notna(r.cu) else False, L, D)
        if r.affordable_100:
            inc.update(regime="100% affordable (outside the program)", exempt=True)
        pr = pipeline_relief(r.approval, r.fcd, n, L, D)
        o = {"stem": r.stem, **inc, **pr}
        # when the inclusionary fee is priced: paid at the first construction document, at the
        # rate then in force (cod_4155_timing); a project without one is priced at the latest
        # register and flagged
        pay = r.fcd if pd.notna(r.fcd) else pd.Timestamp(f"{ymax}-01-01")
        o["incl_priced_at"] = pay.year
        o["rate_interpolated"] = pay.year in RATE_GAP_YEARS
        o["incl_priced_unpaid"] = pd.isna(r.fcd)
        for t in ("r", "o"):
            pct = inc[f"fee_{t}"]
            if inc["exempt"] or pd.isna(pct):
                o[f"incl_{t}"] = 0.0 if inc["exempt"] else np.nan
                continue
            if pay.year >= 2019:
                o[f"incl_{t}"] = rate("incl_psf", pay) * r.gfa * pct / 100
            else:
                o[f"incl_{t}"] = pct / 100 * n * rate("incl_1br", pay)
        o["incl_studio"] = (np.nan if inc["exempt"] or pay.year >= 2019 or pd.isna(inc["fee_r"])
                            else inc["fee_r"] / 100 * n * rate("incl_studio", pay))
        o["incl_2br"] = (np.nan if inc["exempt"] or pay.year >= 2019 or pd.isna(inc["fee_r"])
                         else inc["fee_r"] / 100 * n * rate("incl_2br", pay))
        # impact fees: the rate in force at payment until 193-23, then the rate at approval
        # (fee_rate_at_payment, fee_193_approval)
        v = r.approval if pd.notna(r.approval) and r.approval >= D["fee193"] else (
            r.fcd if pd.notna(r.fcd) else r.approval)
        o["fee_vest"] = v
        if pd.notna(v) and v.year in RATE_GAP_YEARS:
            o["rate_interpolated"] = True
        o["fee_vest_rule"] = ("approval (193-23)" if pd.notna(r.approval) and r.approval >= D["fee193"]
                              else "first construction document" if pd.notna(r.fcd) else "approval (unpaid)")
        g, gn = r.gfa, r.gfa_net
        fees = {}
        if pd.notna(v) and v >= D["tsf"] and r.net_units > 20:
            per = g / n
            fees["tsf"] = per * (min(n, 99) * rate("tsf_21_99", v) + max(n - 99, 0) * rate("tsf_100", v))
        if pd.notna(v) and v >= D["cc"]:
            fees["childcare"] = gn * rate("cc_10" if n >= 10 else "cc_1_9", v)
        fees["school"] = gn * rate("school", v)
        note = ""
        if r.blklot in amap.index:
            a = amap.loc[r.blklot]
            a = a.iloc[0] if isinstance(a, pd.DataFrame) else a
            note = a.area_note
            for key, st in a["keys"]:
                if pd.isna(v) or v < start[st]:
                    continue
                if key == "mo_416":
                    if inc["exempt"]:
                        continue
                    key = "mo_416_vnm" if a.vnm_sud else "mo_416_nct"
                if key == "vv_420" and v.year >= 2016 and n < 20:
                    continue           # the 2016-- registers: projects of 20 or more units
                if key == "cs_433":
                    fees["cs_433_r"] = gn * rate("cs_433_rental", v)
                    fees["cs_433_o"] = gn * rate("cs_433_condo", v)
                    continue
                fees[key] = gn * rate(key, v)
        # 201-23's temporary reduction: fees assessed by 2026-11-01 for a project with a first
        # construction document within 30 months of Final Approval (fee_tfr33)
        red = (pd.notna(v) and D["pipe"] <= v <= D["tfr_end"] and pd.notna(r.fcd) and pd.notna(r.approval)
               and (r.fcd - r.approval).days <= 913)
        o["tfr_applied"] = bool(red)
        for k in list(fees):
            if red and k != "school":
                fees[k] *= (1 - tfr)
            o[f"fee_{k}"] = fees[k]
        base = sum(val for k, val in fees.items() if not k.startswith("cs_433") and pd.notna(val))
        o["impact_r"] = base + fees.get("cs_433_r", 0.0)
        o["impact_o"] = base + fees.get("cs_433_o", 0.0)
        o["impact_complete"] = all(pd.notna(val) for val in fees.values()) and pd.notna(v) and v.year >= ymin
        o["area_note"] = note
        # 419 replaces the citywide percentages in the UMU districts (area_specific_rates);
        # the citywide figures are kept for these parcels but flagged
        o["umu"] = bool(r.blklot in amap.index and amap.loc[[r.blklot], "umu"].any())
        rows.append(o)
    ob = pd.DataFrame(rows)
    out = f.merge(ob, on="stem", how="left")
    for t in ("r", "o"):
        out[f"total_{t}"] = out[f"incl_{t}"] + out[f"impact_{t}"].where(out.impact_complete)
    out["total_lo"] = out[["total_r", "total_o"]].min(axis=1, skipna=False)
    out["total_hi"] = out[["total_r", "total_o"]].max(axis=1, skipna=False)
    known = out.tenure.isin(["rental", "ownership"])
    out["total_known"] = np.where(out.tenure.eq("rental"), out.total_r,
                                  np.where(out.tenure.eq("ownership"), out.total_o, np.nan))
    out.loc[~known, "total_known"] = np.nan
    # DBI's valuation: the revised cost where DBI revised it upward (a site permit is often
    # filed at a nominal valuation and revised when the addenda are priced)
    # the brief: a density bonus can modify the local obligation; such projects, and those
    # whose citywide figures an area rule or an SB 330 freeze may displace, are marked uncertain
    out["obligation_uncertain"] = out.density_bonus | out.umu.fillna(False) | out.sb330
    out["cost"] = out[["estimated_cost", "revised_cost"]].where(lambda d: d.gt(1)).max(axis=1)
    out["share_lo"] = out.total_lo / out.cost
    out["share_hi"] = out.total_hi / out.cost
    return out


def build():
    L = Law()
    f, meta = project_frame()
    D = key_dates(L)
    D["today"] = pd.Timestamp(meta["retrieved"])
    R = rate_table()
    areas = parcel_areas()
    ob = obligations(f, L, D, R, areas)
    EXA.mkdir(parents=True, exist_ok=True)
    keep = [c for c in ob.columns if c not in ("parcels",)]
    ob[keep].to_parquet(EXA / "exaction_projects.parquet", index=False)
    R.to_csv(EXA / "rate_table.csv")
    areas.assign(keys=areas["keys"].map(lambda k: ";".join(f"{a}@{b}" for a, b in k))).to_parquet(
        EXA / "parcel_fee_areas.parquet", index=False)
    (EXA / "build_meta.json").write_text(json.dumps(
        {**{k: (str(v) if isinstance(v, pd.Timestamp) else v) for k, v in meta.items()},
         "claims_used": sorted(L.used), "dates": {k: str(v.date()) for k, v in D.items()}}, indent=1))
    print(f"{len(ob):,} projects; {int(ob.linked_case.sum()):,} Commission-linked; "
          f"{L.used.__len__()} claims used → {EXA}")
    return ob


# ═══════════════════════════════════════════════════════════════════════════
# report: the memo's figures, tables and macros
# ═══════════════════════════════════════════════════════════════════════════
def _n(x) -> str:
    return "---" if x is None or pd.isna(x) else f"{x:,.0f}".replace(",", "{,}")


def _p(x, d=0) -> str:
    return "---" if x is None or pd.isna(x) else f"{100 * x:.{d}f}"


def _usd(x, k=0) -> str:
    """Dollars, with thousands (k) or millions (m) when asked."""
    if x is None or pd.isna(x):
        return "---"
    if k == "k":
        return rf"\${x / 1e3:,.0f}k".replace(",", "{,}")
    if k == "m":
        return rf"\${x / 1e6:,.1f}m".replace(",", "{,}")
    return rf"\${x:,.0f}".replace(",", "{,}")


def _t(s) -> str:
    return (str(s).replace("\\", r"\textbackslash{}").replace("&", r"\&").replace("%", r"\%")
            .replace("$", r"\$").replace("#", r"\#").replace("_", r"\_"))


ORD_MARKS = [("inc2002_effective", "37-02"), ("inc2006_sep9", "213/219-06"),
             ("inc2013_effective", "62-13"), ("inc2016_effective", "Prop C, 76-16"),
             ("inc2017_effective", "158-17"), ("inc2023_effective", "201-23")]
BUNCH_WEEKS = 12
ESC_YEARS = range(2018, 2024)          # January 1 escalations of the on-site share, by EEA date
PRE_ESC_YEARS = range(2011, 2017)      # the same calendar weeks without an escalation
NOTCH_PERIODS = [("2001-06-18", "2006-07-17", "threshold 10 (37-02)"),
                 ("2006-07-18", "2013-01-14", "threshold 5 (213-06)"),
                 ("2013-01-15", "2016-06-11", "threshold 10 (62-13)"),
                 ("2016-06-12", "2026-12-31", "threshold 10; 25+ tier (76-16, 158-17)")]


def application_weeks(rec: pd.DataFrame) -> pd.DataFrame:
    """One row per planning project stem: the first ENV/PRJ record's open date, and the unit
    count a PRJ record states (PRJ records start in 2018)."""
    r = rec[rec.record_type.isin(APP_TYPES) & rec.stem.ne("")].dropna(subset=["open_date"])
    r = r.assign(units=pd.to_numeric(r.number_of_units_prop, errors="coerce"))
    g = r.sort_values("open_date").groupby("stem").agg(opened=("open_date", "first"),
                                                       units=("units", "max"))
    return g


def bunching(apps: pd.DataFrame) -> pd.DataFrame:
    """Applications by week relative to each date whose rules key on the application date."""
    rows = []
    events = [(pd.Timestamp(f"{y}-01-01"), "escalation" if y in ESC_YEARS else "no escalation", y)
              for y in list(ESC_YEARS) + list(PRE_ESC_YEARS)]
    events += [(pd.Timestamp("2016-01-12"), "2016-01-12 cutoff", 2016)]
    for day, kind, y in events:
        d = (apps.opened - day).dt.days
        w = np.floor(d / 7).astype("Int64")
        sel = w.between(-BUNCH_WEEKS, BUNCH_WEEKS - 1)
        for big, m in (("all", sel), ("10+ units (PRJ)", sel & apps.units.ge(10))):
            vc = w[m].value_counts().reindex(range(-BUNCH_WEEKS, BUNCH_WEEKS), fill_value=0)
            for k, v in vc.items():
                rows.append({"event": str(day.date()), "kind": kind, "year": y, "subset": big,
                             "week": int(k), "n": int(v)})
    return pd.DataFrame(rows)


def report():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import acquire_external_data as ax
    FIG.mkdir(parents=True, exist_ok=True)
    TAB.mkdir(parents=True, exist_ok=True)
    L = Law()
    ob = pd.read_parquet(EXA / "exaction_projects.parquet")
    meta = json.loads((EXA / "build_meta.json").read_text())
    D = {k: pd.Timestamp(v) for k, v in meta["dates"].items()}
    R = rate_table()
    claims = pd.read_csv(CLAIM_CHECK, dtype=str, keep_default_na=False)
    rec = ax.build_records()
    apps = application_weeks(rec)
    bn = bunching(apps)
    bn.to_csv(EXA / "bunching_weeks.csv", index=False)
    M: dict[str, str] = {}
    T: list[str] = ["% GENERATED BY build_exaction_panel.py report --- do not edit by hand."]

    # ── the frame and its coverage ──
    big = ob[ob.units.ge(10)]
    priced = big[~big.affordable_100 & big.total_lo.notna()]
    M.update(exProjects=_n(len(ob)), exLinked=_n(ob.linked_case.sum()), exBig=_n(len(big)),
             exBigLinked=_n(big.linked_case.sum()), exAlterations=_n(meta["n_alterations_set_aside"]),
             exAffordable=_n(ob.affordable_100.sum()), exPriced=_n(len(priced)),
             exPricedLinked=_n(priced.linked_case.sum()),
             exGpu=_n(meta["gpu_med"]), exGpuLo=_n(meta["gpu_q"][0]), exGpuHi=_n(meta["gpu_q"][1]),
             exGpuN=_n(meta["gpu_n"]), exClaims=_n(len(claims)),
             exClaimsVerified=_n(claims.verified.eq("yes").sum()),
             exRegisterClaims=_n(claims.object.eq("register rate").sum()),
             exLegalClaims=_n(claims.object.ne("register rate").sum()),
             exRegisters=_n(R.index.nunique() - len([y for y in RATE_GAP_YEARS if y in R.index])),
             exRegFirst=str(int(R.index.min())), exRegLast=str(int(R.index.max())),
             exClaimsUsed=_n(len(meta["claims_used"])),
             exEffSixInferred=str(D["eff2006_inferred"].date()), exEffSix=str(D["eff2006"].date()))
    cov = [("Residential new-construction projects filed since 2001-06-18", len(ob), len(big)),
           ("  of which Commission-linked (T1/T2)", ob.linked_case.sum(), big.linked_case.sum()),
           ("Application date from a planning record", ob.app_source.ne("DBI filing").sum(),
            big.app_source.ne("DBI filing").sum()),
           ("Application date = the DBI filing (no planning record found)", ob.app_source.eq("DBI filing").sum(),
            big.app_source.eq("DBI filing").sum()),
           ("Approval = a Commission action", ob.approval_source.eq("Commission action").sum(),
            big.approval_source.eq("Commission action").sum()),
           ("Approval = DBI approval or issuance", ob.approval_source.isin(["DBI approval", "DBI issuance"]).sum(),
            big.approval_source.isin(["DBI approval", "DBI issuance"]).sum()),
           ("Not yet approved (held to the version in force)", ob.approval_source.eq("").sum(),
            big.approval_source.eq("").sum()),
           ("First construction document recorded by DBI", ob.fcd_source.eq("DBI first construction document").sum(),
            big.fcd_source.eq("DBI first construction document").sum()),
           ("  issuance used in its place", ob.fcd_source.eq("DBI issuance").sum(), big.fcd_source.eq("DBI issuance").sum()),
           ("Floor area from a PRJ record", ob.gfa_source.eq("PRJ record").sum(), big.gfa_source.eq("PRJ record").sum()),
           ("Floor area assumed (units x median)", ob.gfa_source.ne("PRJ record").sum(), big.gfa_source.ne("PRJ record").sum()),
           ("Tenure known (MOHCD pipeline)", ob.tenure_source.eq("MOHCD pipeline").sum(), big.tenure_source.eq("MOHCD pipeline").sum()),
           ("Tenure from description words only", ob.tenure_source.eq("description words").sum(),
            big.tenure_source.eq("description words").sum()),
           ("100\\% affordable (outside the program)", ob.affordable_100.sum(), big.affordable_100.sum()),
           ("Density bonus mentioned (state or HOME-SF)", ob.density_bonus.sum(), big.density_bonus.sum()),
           ("Obligation marked uncertain (density bonus, UMU or SB 330)", ob.obligation_uncertain.sum(),
            big.obligation_uncertain.sum()),
           ("SB 330 preliminary application flagged", ob.sb330.sum(), big.sb330.sum()),
           ("In a UMU district (Section 419 replaces the citywide rates)", ob.umu.sum(), big.umu.sum()),
           ("Eligible for the 2023 pipeline relief (415A)", ob.pipe_eligible.sum(), big.pipe_eligible.sum()),
           ("Under the 2023--26 window (415B)", ob.regime.str.startswith("2023").sum(),
            big.regime.str.startswith("2023").sum()),
           ("Impact fees cut 33\\% (201-23's temporary reduction)", ob.tfr_applied.sum(), big.tfr_applied.sum()),
           ("Rate year 2017 (no register: interpolated)", ob.rate_interpolated.fillna(False).sum(),
            big.rate_interpolated.fillna(False).sum()),
           ("Priced in full (inclusionary and every impact fee)", ob.total_lo.notna().sum(), big.total_lo.notna().sum())]
    T.append(r"\begin{table}[htbp]\centering\small")
    T.append(r"\caption{Coverage: what each project's dates, floor area and tenure come from, and "
             r"how many are priced. New construction (DBI permit types 1 and 2) adding at least one "
             r"unit, one row per project (a Commission case collapsed to its principal permit).}"
             r"\label{tab:excoverage}")
    T.append(r"\begin{tabular}{L{10.2cm}rr}\toprule & All & 10+ units\\\midrule")
    for lab, a_, b_ in cov:
        T.append(rf"{lab.replace('  ', r'\quad ')} & {_n(a_)} & {_n(b_)}\\")
    T.append(r"\bottomrule\end{tabular}\end{table}")

    # ── the version chain ──
    V = [("2002", "Ord.\\ 37-02", "inc2002_effective", "first application from 2001-06-18",
          "10 (12 CU/PUD)", "15 (17)", "= off-site", "10+"),
         ("2006", "Ords.\\ 213-06, 219-06", "inc2006_sep9", "first application from 2006-07-18",
          "15", "20", "20", "5+"),
         ("2013", "Ord.\\ 62-13", "inc2013_effective", "same; 5--9 units out without a first construction document by 2013-01-15",
          "12", "20", "20", "10+"),
         ("2016", "Prop.\\ C; Ord.\\ 76-16", "inc2016_effective", "complete EEA; EEA 2013--2016-01-12 grandfathered (25+: 13/13.5/14.5 on-site, 25/27.5/30 fee) if approved by 2018-12-07",
          "12 / 25", "20 / 33", "20 / 33", "10--24 / 25+"),
         ("2017", "Ord.\\ 158-17", "inc2017_effective", "complete EEA after 2016-01-12; on-site share rises each January 1 by EEA year",
          "12$\\to$15 / 18$\\to$24 R, 20$\\to$26 O", "20 / 30 R, 33 O", "20 / 30 R, 33 O", "10--24 / 25+"),
         ("2023 pipeline", "Ord.\\ 201-23, 415A", "inc2023_effective", "approved before 2023-11-01, no first construction document; on request by 2026-11-01",
          "12", "16.4", "16.4", "10+"),
         ("2023 window", "Ord.\\ 201-23, 415B", "inc2023_effective", "approved 2023-11-01 to 2026-11-01, first construction document within 30 months",
          "15 (25+)", "20.5 (25+)", "20.5 (25+)", "25+"),
         ("2026", "Ord.\\ 201-23, s.\\ 7", "o201_operative", "operative 2026-11-21 unless amended",
          "15 / 20 O", "20 / 24.5 R, 27 O", "---", "10--24 / 25+")]
    T.append(r"\begin{table}[htbp]\centering\small")
    T.append(r"\caption{The inclusionary versions, as the enacted ordinances state them. Shares are "
             r"percent of the project's units; R rental, O ownership; a slash separates the 10--24 and "
             r"25+ unit tiers. Every figure is a verified claim in \texttt{exaction\_sources.csv} "
             r"(Section~\ref{sec:exsources}).}\label{tab:exversions}")
    T.append(r"\resizebox{\textwidth}{!}{\begin{tabular}{llL{2.1cm}L{5.4cm}L{2.9cm}L{2.2cm}L{2.2cm}l}\toprule")
    T.append(r"Version & Source & In force from & Which projects & On-site & Fee & Off-site & Units\\\midrule")
    for v, src, cid, who, on, fee, off, tier in V:
        T.append(rf"{v} & {src} & {L.v(cid)} & {who} & {on} & {fee} & {off} & {tier}\\")
    T.append(r"\bottomrule\end{tabular}}\end{table}")

    # ── the registers ──
    keys = [("incl_1br", "Inclusionary, per 1-bedroom (gap)", 0), ("incl_psf", "Inclusionary, per gsf", 2),
            ("tsf_21_99", "TSF, 21--99 units", 2), ("tsf_100", "TSF, 100+ units", 2),
            ("cc_10", "Child care, 10+ units", 2), ("cc_1_9", "Child care, 1--9 units", 2),
            ("school", "School (SFUSD)", 2), ("en_t1", "Eastern Neighborhoods, Tier 1", 2),
            ("en_t3", "Eastern Neighborhoods, Tier 3", 2), ("mo_421", "Market \\& Octavia (421)", 2),
            ("mo_416_nct", "Market \\& Octavia affordable (416, NCT)", 2), ("rh_418", "Rincon Hill", 2),
            ("soma_418", "SOMA stabilization", 2), ("vv_420", "Visitacion Valley", 2),
            ("bp_422", "Balboa Park", 2), ("cs_433_condo", "Central SoMa 433, Tier B condo", 2)]
    yrs = [y for y in R.index if y not in RATE_GAP_YEARS]
    T.append(r"\begin{table}[htbp]\centering\scriptsize")
    T.append(r"\caption{Residential rates by register year, dollars per gross square foot except the "
             r"inclusionary affordability gap (dollars per unit, used until the 2019 register). Each "
             r"cell is a verified claim quoting the register. No 2017 register was found.}\label{tab:exregisters}")
    T.append(r"\resizebox{\textwidth}{!}{\begin{tabular}{l" + "r" * len(yrs) + r"}\toprule")
    T.append("Rate & " + " & ".join(str(y) for y in yrs) + r"\\\midrule")
    for k, lab, dgt in keys:
        cells = []
        for y in yrs:
            v = R.at[y, k] if k in R.columns else np.nan
            cells.append("" if pd.isna(v) else (f"{v:,.0f}".replace(",", "{,}") if dgt == 0 else f"{v:.2f}"))
        T.append(lab + " & " + " & ".join(cells) + r"\\")
    T.append(r"\bottomrule\end{tabular}}\end{table}")

    # ── vesting ──
    T.append(r"\begin{table}[htbp]\centering\small")
    T.append(r"\caption{When each part of the obligation is fixed, and the claim that says so.}\label{tab:exvesting}")
    T.append(r"\begin{tabular}{L{3.6cm}L{8.2cm}L{3.4cm}}\toprule Component & Fixed at & Claims\\\midrule")
    VEST = [("Inclusionary share (\\%)", "the first application (2002--2013; 62-13's Table 415.3); the complete "
             "EEA from 2016, with the on-site escalation read at the EEA date; approval-date windows in 2016--18 "
             "(grandfathering deadline) and 2023--26 (415A, 415B); lapses without a site or building permit "
             "within 30 months of approval", "inc2013\\_table\\_july18, cod\\_4155\\_vesting, cod\\_4156\\_vesting, "
             "cod\\_4153\\_deadline, cod\\_4155\\_30months"),
            ("Inclusionary fee (\\$)", "the share x MOHCD's rate at payment, due at the first construction "
             "document; the fee is outside 193-23's approval-date freeze", "cod\\_4155\\_timing, inc2016\\_fee\\_timing, "
             "fee\\_193\\_approval"),
            ("Impact fees (types and \\$)", "before 2023-10-16: the rate in force when paid, whatever the filing "
             "date; from 2023-10-16: the types and rates in force at Final Approval", "fee\\_rate\\_at\\_payment, "
             "fee\\_402d\\_timing, fee\\_193\\_approval, fee\\_193\\_effective"),
            ("Everything, for a housing project with an SB 330 preliminary application (2020--)",
             "the ordinances, policies and standards in force at the complete preliminary application, "
             "fees included, except automatic index adjustments", "sb330\\_freeze, gov\\_standards\\_fees, "
             "sb330\\_index\\_exception")]
    for a_, b_, c_ in VEST:
        T.append(rf"{a_} & {b_} & \scriptsize {c_}\\")
    T.append(r"\bottomrule\end{tabular}\end{table}")

    # ── the obligation distribution ──
    pr = priced.assign(pu_lo=priced.total_lo / priced.units, pu_hi=priced.total_hi / priced.units,
                       incl_pu=priced.incl_r / priced.units, imp_pu=priced.impact_r / priced.units,
                       vy=priced.fee_vest.dt.year)
    pr["period"] = pd.cut(pr.vy, [2010, 2015, 2019, 2023, 2027], labels=["2011--15", "2016--19", "2020--23", "2024--26"])
    T.append(r"\begin{table}[htbp]\centering\small")
    T.append(r"\caption{The priced obligation of new buildings of 10 or more units, per unit and as a share of "
             r"DBI's valuation, by the year the impact fees vested. Lower bound: rental rates where tenure "
             r"matters; upper: ownership. Quantiles across projects.}\label{tab:exobligation}")
    T.append(r"\begin{tabular}{llrrrrrrr}\toprule")
    T.append(r"Vested & Group & $n$ & \multicolumn{3}{c}{\$ per unit (lower bound)} & \multicolumn{3}{c}{Share of valuation (\%)}\\"
             r"\cmidrule(lr){4-6}\cmidrule(lr){7-9} & & & p25 & median & p75 & p25 & median & p75\\\midrule")
    for per, g0 in pr.groupby("period", observed=True):
        for grp, g in (("all", g0), ("Commission-linked", g0[g0.linked_case]), ("not linked", g0[~g0.linked_case])):
            if len(g) < 5:
                continue
            q = g.pu_lo.quantile([.25, .5, .75])
            s = g.share_lo.dropna().quantile([.25, .5, .75])
            T.append(rf"{per if grp == 'all' else ''} & {grp} & {_n(len(g))} & {_usd(q[.25], 'k')} & "
                     rf"{_usd(q[.5], 'k')} & {_usd(q[.75], 'k')} & {_p(s[.25])} & {_p(s[.5])} & {_p(s[.75])}\\")
    T.append(r"\bottomrule\end{tabular}\end{table}")
    q = pr.pu_lo.quantile([.25, .5, .75])
    M.update(exPuMed=_usd(q[.5], "k"), exPuLo=_usd(q[.25], "k"), exPuHi=_usd(q[.75], "k"),
             exShareMed=_p(pr.share_lo.median()), exShareLo=_p(pr.share_lo.quantile(.25)),
             exShareHi=_p(pr.share_lo.quantile(.75)),
             exInclShare=_p((pr.incl_r / pr.total_lo).median()),
             exPuMedLinked=_usd(pr.loc[pr.linked_case, "pu_lo"].median(), "k"),
             exPuMedNot=_usd(pr.loc[~pr.linked_case, "pu_lo"].median(), "k"),
             exShareMedLinked=_p(pr.loc[pr.linked_case, "share_lo"].median()),
             exShareMedNot=_p(pr.loc[~pr.linked_case, "share_lo"].median()),
             exTenureGapMed=_usd((pr.pu_hi - pr.pu_lo)[pr.units.ge(25)].median(), "k"),
             exTenureGapMax=_usd((pr.pu_hi - pr.pu_lo).max(), "k"))
    for per in pr.period.cat.categories:
        g = pr[pr.period.eq(per)]
        tag = {"2011--15": "A", "2016--19": "B", "2020--23": "C", "2024--26": "D"}[per]
        M[f"exPuMed{tag}"] = _usd(g.pu_lo.median(), "k")
        M[f"exN{tag}"] = _n(len(g))

    # ── cross-check against the percentage the conditions print ──
    cn = pd.read_parquet(DATA_ROOT / "external" / "cpc_packets" / "conditions_numeric.parquet")
    cn = cn[cn.incl_pct.notna()].copy()
    cn["cstem"] = cn.case_key.map(ax.case_stem)
    mm = cn.merge(ob[ob.case_stem.notna()], left_on="cstem", right_on="case_stem", how="inner")
    def _hit(r):
        cand = [r.on_r, r.on_o, r.fee_r, r.fee_o, r.off_r, r.off_o, r.pipe_fee, r.pipe_on]
        return any(abs(r.incl_pct - v) < 0.01 for v in cand if pd.notna(v))
    mm["hit"] = mm.apply(_hit, axis=1) if len(mm) else []
    gg = mm.groupby("cstem").agg(hit=("hit", "max"), regime=("regime", "first"), umu=("umu", "first"),
                                 aff=("affordable_100", "first"))
    gg.to_csv(EXA / "crosscheck_conditions.csv")
    T.append(r"\begin{table}[htbp]\centering\small")
    T.append(r"\caption{The schedule against the Commission's own conditions: cases whose conditions print "
             r"an inclusionary percentage (\texttt{conditions\_numeric.parquet}), and whether that percentage "
             r"equals one the schedule assigns the case (on-site, fee or off-site, either tenure, or the "
             r"415A option). A case agrees if any of its printed percentages does.}\label{tab:excross}")
    T.append(r"\begin{tabular}{lrrr}\toprule Version assigned & Cases & Agree & \%\\\midrule")
    for reg, g in gg.groupby("regime"):
        T.append(rf"{_t(reg)} & {_n(len(g))} & {_n(g.hit.sum())} & {_p(g.hit.mean())}\\")
    T.append(rf"\midrule All & {_n(len(gg))} & {_n(gg.hit.sum())} & {_p(gg.hit.mean())}\\")
    g2 = gg[~gg.umu & ~gg.aff]
    T.append(rf"Outside UMU, not 100\% affordable & {_n(len(g2))} & {_n(g2.hit.sum())} & {_p(g2.hit.mean())}\\")
    T.append(r"\bottomrule\end{tabular}\end{table}")
    M.update(exCrossN=_n(len(gg)), exCrossAgree=_p(gg.hit.mean()), exCrossNtwo=_n(len(g2)),
             exCrossAgreeTwo=_p(g2.hit.mean()), exCrossRows=_n(len(mm)))
    for reg, tag in (("2002", "Two"), ("2006", "Six"), ("2013", "Thirteen"), ("2016 grandfathered", "Gf"),
                     ("2017 (Ord. 158-17)", "Seventeen"), ("2017 (Ord. 158-17), grandfathering lapsed", "Lapsed")):
        g = gg[gg.regime.eq(reg) if "(" in reg else gg.regime.str.startswith(reg)]
        M[f"exCross{tag}"] = _p(g.hit.mean())
        M[f"exCross{tag}N"] = _n(len(g))

    # ── the notch ──
    nc = ob.copy()
    nrows = []
    for lo, hi, lab in NOTCH_PERIODS:
        g = nc[nc.filed_date.between(lo, hi) & ~nc.affordable_100]
        vc = g.units.round().value_counts()
        nrows.append({"period": lab, **{int(k): int(vc.get(k, 0)) for k in range(2, 41)}, "n": len(g)})
    nt = pd.DataFrame(nrows).set_index("period")
    nt.to_csv(EXA / "notch_counts.csv")
    T.append(r"\begin{table}[htbp]\centering\small")
    T.append(r"\caption{New buildings by unit count around the thresholds, by filing period (100\% affordable "
             r"projects excluded). The inclusionary threshold is 10 units except 2006--2013, when it was 5; from "
             r"2016 a second tier begins at 25; the TSF begins above 20.}\label{tab:exnotch}")
    cols = [4, 5, 6, 8, 9, 10, 11, 19, 20, 21, 22, 23, 24, 25, 26]
    T.append(r"\resizebox{\textwidth}{!}{\begin{tabular}{l" + "r" * len(cols) + r"}\toprule")
    T.append("Filed & " + " & ".join(str(c) for c in cols) + r"\\\midrule")
    for per, r in nt.iterrows():
        T.append(_t(per) + " & " + " & ".join(_n(r[c]) for c in cols) + r"\\")
    T.append(r"\bottomrule\end{tabular}}\end{table}")
    post = nt.loc[NOTCH_PERIODS[3][2]]
    six = nt.loc[NOTCH_PERIODS[1][2]]
    M.update(exNineA=_n(post[9]), exTenA=_n(post[10]), exElevenA=_n(post[11]),
             exTwentyFourA=_n(post[24]), exTwentyFiveA=_n(post[25]), exTwentyA=_n(post[20]),
             exTwentyOneA=_n(post[21]), exFourSix=_n(six[4]), exFiveSix=_n(six[5]),
             exNineSix=_n(six[9]), exTenSix=_n(six[10]))
    # the dollar step at each threshold, for a project vesting in the latest register year
    yl = int(R.index.max())
    gpu = meta["gpu_med"]
    step10 = L.f("cod_4155_fee20") / 100 * 10 * gpu * R.at[yl, "incl_psf"]
    step25_r = (L.f("cod_4155_fee30") - L.f("cod_4155_fee20")) / 100 * 25 * gpu * R.at[yl, "incl_psf"]
    step25_415b = (L.f("b415_fee205") - L.f("cod_4155_fee20")) / 100 * 25 * gpu * R.at[yl, "incl_psf"]
    tsf21 = 21 * gpu * R.at[yl, "tsf_21_99"]
    M.update(exStepTen=_usd(step10, "k"), exStepTwentyFive=_usd(step25_r, "k"),
             exStepTwentyFiveB=_usd(step25_415b, "k"), exStepTsf=_usd(tsf21, "k"), exStepYear=str(yl),
             exStepTenPerUnit=_usd(step10 / 10, "k"))

    # ── bunching ──
    def ratio(sub, kind):
        b = bn[bn.subset.eq(sub) & bn.kind.eq(kind)]
        pre = b[b.week.between(-4, -1)].n.sum()
        post = b[b.week.between(0, 3)].n.sum()
        return pre, post, (pre / post if post else np.nan)
    pe, po, re_ = ratio("all", "escalation")
    ne, no, rn = ratio("all", "no escalation")
    pe10, po10, re10 = ratio("10+ units (PRJ)", "escalation")
    M.update(exBunchEscPre=_n(pe), exBunchEscPost=_n(po), exBunchEscRatio=f"{re_:.2f}",
             exBunchNoPre=_n(ne), exBunchNoPost=_n(no), exBunchNoRatio=f"{rn:.2f}",
             exBunchTenPre=_n(pe10), exBunchTenPost=_n(po10),
             exBunchTenRatio=f"{re10:.2f}" if pd.notna(re10) else "---",
             exBunchWeeks=str(BUNCH_WEEKS))
    b16 = bn[bn.kind.eq("2016-01-12 cutoff") & bn.subset.eq("all")]
    M.update(exCutPre=_n(b16[b16.week.between(-4, -1)].n.sum()), exCutPost=_n(b16[b16.week.between(0, 3)].n.sum()))

    # ── figures ──
    fig, axs = plt.subplots(1, 2, figsize=(11, 4.2))
    a0 = axs[0]
    days = pd.date_range("2002-01-01", "2026-09-01", freq="MS")
    lag = (ob.approval - ob.app_date).dt.days.median()
    series = {"on-site, 10--24": ("on_r", 12), "on-site, 25+ rental": ("on_r", 30), "on-site, 25+ ownership": ("on_o", 30),
              "fee, 10--24": ("fee_r", 12), "fee, 25+ rental": ("fee_r", 30), "fee, 25+ ownership": ("fee_o", 30)}
    styles = {"on-site": "-", "fee": "--"}
    colors = {"10--24": "#2f6f4f", "25+ rental": "#5b7fa6", "25+ ownership": "#a33"}
    for lab, (col, n) in series.items():
        ys = []
        for d0 in days:
            t_ = inclusionary_terms(d0, d0 + pd.Timedelta(days=lag), pd.NaT, n, False, L, {**D, "today": d0 + pd.Timedelta(days=lag)})
            ys.append(t_[col] if not t_["exempt"] else 0)
        kind, grp = lab.split(", ")
        a0.step(days, ys, where="post", ls=styles[kind], color=colors[grp], lw=1.6, label=lab.replace("--", "–"))
    for cid, lab in ORD_MARKS:
        x = L.d(cid)
        a0.axvline(x, color="0.75", lw=0.8)
        a0.text(x, a0.get_ylim()[1] if False else 34.5, lab, rotation=90, fontsize=7, va="top", ha="right", color="0.4")
    a0.set_ylim(0, 35)
    a0.set_ylabel("percent of units")
    a0.set_title(f"(a) Inclusionary share by application date\n(approval {lag / 365.25:.1f} years later, the median lag)", fontsize=9)
    a0.legend(fontsize=7, loc="lower right")
    a1 = axs[1]
    Rp = R.copy()
    for y in RATE_GAP_YEARS:
        if y in Rp.index:
            Rp.loc[y] = np.nan                 # a gap is drawn as a gap
    Rp = Rp.reindex(range(int(Rp.index.min()), int(Rp.index.max()) + 1))
    for k, lab, c in (("tsf_21_99", "TSF, 21–99 units", "#b07d2b"), ("cc_10", "Child care, 10+ units", "#2f6f4f"),
                      ("school", "School (SFUSD)", "0.5"), ("en_t1", "Eastern Neighborhoods, Tier 1", "#5b7fa6"),
                      ("en_t3", "Eastern Neighborhoods, Tier 3", "#1f3f66"), ("mo_421", "Market & Octavia (421)", "#a33")):
        a1.plot(Rp.index, Rp[k], marker="o", ms=3, lw=1.6, color=c, label=lab)
    a1.set_ylabel("dollars per gross square foot")
    a1.set_title("(b) Impact-fee rates by register year (no 2017 register)", fontsize=9)
    a1.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(FIG / "fig_exaction_rates.pdf")
    plt.close(fig)

    fig, axs = plt.subplots(1, 2, figsize=(11, 4.0))
    for grp, c, m_ in ((True, "#a33", "o"), (False, "#5b7fa6", "s")):
        g = pr[pr.linked_case.eq(grp)]
        axs[0].scatter(g.vy + (0.12 if grp else -0.12), g.pu_lo / 1e3, s=8, alpha=0.35, color=c, marker=m_,
                       label="Commission-linked" if grp else "not linked")
        med = g.groupby("vy").pu_lo.median()
        axs[0].plot(med.index, med / 1e3, color=c, lw=2)
    axs[0].set_yscale("log")
    axs[0].set_xlabel("year the impact fees vested")
    axs[0].set_ylabel("obligation per unit, \\$000 (lower bound, log)")
    axs[0].set_title("(a) Obligation per unit, 10+ units; lines are yearly medians", fontsize=9)
    axs[0].legend(fontsize=7)
    sh = pr.share_lo.clip(upper=1.0)
    axs[1].hist([sh[pr.linked_case], sh[~pr.linked_case]], bins=np.linspace(0, 1, 26), stacked=False,
                color=["#a33", "#5b7fa6"], alpha=0.7, label=["Commission-linked", "not linked"])
    axs[1].set_xlabel("obligation / DBI valuation (lower bound; capped at 1)")
    axs[1].set_ylabel("projects")
    axs[1].set_title("(b) Obligation as a share of the declared valuation", fontsize=9)
    axs[1].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(FIG / "fig_exaction_obligation.pdf")
    plt.close(fig)

    fig, axs = plt.subplots(1, 2, figsize=(11, 3.8))
    for kind, c in (("escalation", "#a33"), ("no escalation", "#5b7fa6")):
        b = bn[bn.subset.eq("all") & bn.kind.eq(kind)].groupby("week").n.sum()
        nyear = len(ESC_YEARS) if kind == "escalation" else len(PRE_ESC_YEARS)
        axs[0].plot(b.index, b / nyear, marker="o", ms=3, color=c,
                    label=f"January 1, {min(ESC_YEARS)}–{max(ESC_YEARS)} (on-site share rises)" if kind == "escalation"
                    else f"January 1, {min(PRE_ESC_YEARS)}–{max(PRE_ESC_YEARS)} (no change)")
    axs[0].axvline(-0.5, color="0.6", lw=0.8)
    axs[0].set_xlabel("weeks from January 1")
    axs[0].set_ylabel("applications opened per week (mean across years)")
    axs[0].set_title("(a) ENV/PRJ applications around January 1", fontsize=9)
    axs[0].legend(fontsize=7)
    b = bn[bn.kind.eq("2016-01-12 cutoff") & bn.subset.eq("all")].groupby("week").n.sum()
    axs[1].bar(b.index, b.values, color="#b07d2b", width=0.8)
    axs[1].axvline(-0.5, color="0.4", lw=0.8)
    axs[1].set_xlabel("weeks from 2016-01-12")
    axs[1].set_ylabel("applications opened")
    axs[1].set_title("(b) Around the 2016-01-12 grandfathering cutoff", fontsize=9)
    fig.tight_layout()
    fig.savefig(FIG / "fig_exaction_bunching.pdf")
    plt.close(fig)

    fig, axs = plt.subplots(2, 2, figsize=(10, 5.2), sharex=True)
    ks = np.arange(4, 31)
    thr = {0: [10], 1: [5], 2: [10], 3: [10, 25]}
    for i, (per, r) in enumerate(nt.iterrows()):
        a_ = axs.flat[i]
        a_.bar(ks, [r[k] for k in ks], color="#5b7fa6", width=0.8)
        for x_ in thr[i]:
            a_.axvline(x_ - 0.5, color="#a33", lw=1.0)
        if i == 3:
            a_.axvline(20.5, color="#b07d2b", lw=1.0, ls="--")
        a_.set_title(f"filed {per}", fontsize=8)
        a_.set_ylabel("new buildings", fontsize=8)
    for a_ in axs[1]:
        a_.set_xlabel("units in the building (red: inclusionary threshold; dashed: TSF)", fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG / "fig_exaction_notch.pdf")
    plt.close(fig)

    # ── the claims list, for the appendix ──
    lc = claims[claims.object.ne("register rate")]
    T.append(r"{\scriptsize\begin{longtable}{L{3.0cm}L{4.6cm}L{2.0cm}L{4.6cm}}")
    T.append(r"\caption{Every legal claim the schedule reads, with its value and the first words of its "
             r"quotation. The register rates (" + _n(claims.object.eq("register rate").sum()) + r" further "
             r"claims) are in \texttt{exaction\_sources.csv}.}\label{tab:exclaims}\\\toprule")
    T.append(r"Claim & Field & Value & Quotation (start)\\\midrule\endfirsthead\toprule Claim & Field & Value & Quotation (start)\\\midrule\endhead")
    for r in lc.itertuples():
        qt = r.quote if len(r.quote) < 110 else r.quote[:107] + "..."
        brk = lambda x: _t(x).replace(r"\_", r"\_\allowbreak{}").replace(":", r":\allowbreak{}")
        T.append(rf"{brk(r.claim_id)}{'' if r.verified == 'yes' else ' (UNVERIFIED)'} & {_t(r.field)} & {_t(r.value)} & \textit{{{brk(qt)}}}\\")
    T.append(r"\bottomrule\end{longtable}}")

    # one tables file; the memo's \extable{<label>} places each where the text needs it
    blocks, cur = [], []
    for line in T[1:]:
        if line.startswith((r"\begin{table}", r"{\scriptsize\begin{longtable}")) and cur:
            blocks.append(cur)
            cur = []
        cur.append(line)
    blocks.append(cur)
    out = [T[0]]
    for b in blocks:
        lab = re.search(r"\\label\{tab:(\w+)\}", "\n".join(b)).group(1)
        out += [rf"\ifnum\pdfstrcmp{{\extab}}{{{lab}}}=0", *b, r"\fi"]
    (TAB / "exactions_tables.tex").write_text("\n".join(out) + "\n")
    # every legal claim's value, addressable in the memo as \cl{<claim_id>}
    cl = [r"\makeatletter"]
    for r in lc.itertuples():
        cl.append(rf"\expandafter\def\csname cl@{r.claim_id}\endcsname{{{_t(r.value)}}}")
    cl += [r"\makeatother", r"\newcommand{\cl}[1]{\csname cl@#1\endcsname}"]
    has = [y for y, u in sorted(registers().items())
           if "regardless of the date of permit filing" in source_text(u)]
    hasnt = [y for y in sorted(registers()) if y not in has]
    M.update(exPaySentenceFirst=str(min(has)), exPaySentenceLast=str(max(has)),
             exPaySentenceAbsent=", ".join(str(y) for y in hasnt) if hasnt else "none",
             exVersions=_n(len(V)))
    M.update(exDensity=_n(ob.density_bonus.sum()), exDensityBig=_n(big.density_bonus.sum()),
             exUmu=_n(ob.umu.sum()), exSbThreeThirty=_n(ob.sb330.sum()),
             exPipe=_n(ob.pipe_eligible.sum()), exWindow=_n(ob.regime.str.startswith("2023").sum()),
             exTfr=_n(ob.tfr_applied.sum()), exMedLag=f"{(ob.approval - ob.app_date).dt.days.median() / 365.25:.1f}",
             exAssumedGfa=_p(ob.gfa_source.ne("PRJ record").mean()),
             exDbiApp=_p(ob.app_source.eq("DBI filing").mean()))
    (TAB / "exactions_macros.tex").write_text(
        "% GENERATED BY build_exaction_panel.py report --- do not edit by hand.\n" +
        "\n".join(rf"\newcommand{{\{k}}}{{{v}}}" for k, v in sorted(M.items())) + "\n" +
        "\n".join(cl) + "\n")
    print(f"{len(M)} macros, {len(T)} table lines → {TAB}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--refresh", action="store_true")
    sub.add_parser("claims")
    sub.add_parser("probe")
    sub.add_parser("build")
    sub.add_parser("report")
    a = ap.parse_args()
    if a.cmd == "fetch":
        fetch_ordinance_index(a.refresh)
        if CLAIMS.exists():
            for u in sorted(set(load_claims().source_url) - {""}):
                try:
                    source_text(u, a.refresh)
                except Exception as e:
                    print(f"  {u}: {e}")
    elif a.cmd == "claims":
        seed_registers()
        curate_claims()
    elif a.cmd == "probe":
        probe_claims()
    elif a.cmd == "build":
        build()
    elif a.cmd == "report":
        report()


if __name__ == "__main__":
    main()
