#!/usr/bin/env python3
"""
collect_conditions.py
---------------------
Purpose : A census of Commission condition text for every case and every motion the item
          table records: find every place a draft motion (in a packet) or an adopted motion
          is published, probe every case and motion against every URL family, pull what
          exists, keep the motion section only, and parse it to one row per condition.
          Answers: what is the earliest hearing year for which condition text is
          retrievable, by source? Spec: .claude/instructions/
          claude_code_brief_conditions_permits_catalogue.md, Task 1.
Inputs  : $MFHR_DATA_ROOT/extraction/<RUN>/clean/*.jsonl         (the item table)
          $MFHR_DATA_ROOT/meeting_minutes/san_francisco/raw/      (the minutes, for their links)
          commissions.sfplanning.org, default.sfplanning.org, citypln-m-extnl.sfgov.org,
          sfplanning.org (hearing pages), web.archive.org (CDX)
Outputs : $MFHR_DATA_ROOT/external/cpc_packets/manifests/*        (discovery: listings, CDX,
                                                                   hearing pages, minutes links)
          $MFHR_DATA_ROOT/external/cpc_packets/availability_full.csv   (the census, resumable)
          $MFHR_DATA_ROOT/external/cpc_packets/docs/<doc_id>.{pdf,lines.json.gz}
          $MFHR_DATA_ROOT/external/cpc_packets/pull_log.csv
          $MFHR_DATA_ROOT/external/cpc_packets/conditions_long.{parquet,csv}, motion_sections.csv
          $MFHR_DATA_ROOT/external/cpc_packets/draft_vs_adopted{,_added}.csv
          $MFHR_DATA_ROOT/external/cpc_packets/validation/   (gold set, labels, frozen rounds, scores)
          $MFHR_DATA_ROOT/external/cpc_packets/census_summary.json, task1_summary.md
          output/planning_commission_project/conditions_content/{figures,tables}/*
Author  : Dan Post
Created : 2026-09-10

Usage
-----
  python collect_conditions.py discover            # 1a: listings, CDX, hearing pages, minutes links
  python collect_conditions.py probe               # 1b: HEAD every case and motion candidate
  python collect_conditions.py pull --host H [--n N] [--max-mb M] [--years 2022-2026]
                                  [--loop-minutes 15]   # 1c: one process per host
  python collect_conditions.py reextract [--network]    # pages read before /Rotate was honoured
  python collect_conditions.py parse               # 1d: one row per condition
  python collect_conditions.py diff                # 1d: draft vs adopted
  python collect_conditions.py gold                # 1d: draw the gold documents (once)
  python collect_conditions.py freeze|score|archive|sample --round R   # 1d: validation rounds
  python collect_conditions.py summary             # 1f: task1_summary.md
  python collect_conditions.py report              # Task 2 memo tables, macros, figures

Notes
-----
Every stage caches and resumes, like `analyze_conditions.py`, and `--refresh` redoes a
network stage on purpose rather than by accident. The parsing rules live in
`condition_parser.py`, separately, so they can be hashed and frozen for a validation round
without freezing this orchestration code.

What the conditions memo called "no packet before 2010" was a statement about two URL
patterns. Four facts found while building this change the search, and each is why a stage
exists:

  * The minutes themselves hyperlink the documents: the 1998--2014 HTML minutes link
    motions and packets on the old `sf-planning.org` tree and on `commissions.sfplanning.org`,
    and from 2017 the PDF minutes link every MOTION, RESOLUTION and DRA number to the adopted
    document in the department's M-Files vault. Those are exact URLs, keyed to an item, and
    they beat any constructed guess --- `discover` reads them out of the raw corpus locally.
  * `commissions.sfplanning.org` and `default.sfplanning.org` are two public faces of one S3
    bucket (same ETags), neither answers a directory listing, and both answer 404 for a
    missing key. The raw bucket host answers 403 instead, so URLs found on it are rewritten
    to the public hostname before probing, which keeps "absent" meaning one thing.
  * S3 keys are case-sensitive and the old site wrote suffixes in lower case
    (`2010.0970c.pdf`) where the minutes print upper (`2010.0970C`), so the old-tree family
    probes both.
  * Politeness is per host: at most `RATE` requests a second, exponential backoff on 429 and
    5xx, a User-Agent naming the project and a contact address.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import os
import queue
import random
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from paths import DATA_ROOT, MEETING_MINUTES                         # noqa: E402
# The shared normalisers live in acquire_external_data.py and are imported, never re-implemented:
# case-number normalisation, the suffix-stripped stem, two-digit-year expansion, the universe.
from acquire_external_data import (load_items, norm_case, case_stem,  # noqa: E402
                                   case_universe)

RUN = "corpus_v2_g3"
STORE = DATA_ROOT / "external" / "cpc_packets"
MANIFESTS = STORE / "manifests"
DISCOVERED = MANIFESTS / "discovered_urls.csv"
AVAIL_FULL = STORE / "availability_full.csv"
DOCS = STORE / "docs"
PULL_LOG = STORE / "pull_log.csv"
RAW_MINUTES = MEETING_MINUTES / "raw"

UA = "market-for-housing-regulation/collect_conditions (research; contact danpost@bu.edu)"
RATE = 2.0              # requests per second per host, the brief's ceiling
PROBE_RATE = 1.5        # the probe's share when `pull` runs beside it; the two sum to RATE
PULL_RATE = 0.5
WAYBACK_RATE = 0.5      # the Wayback Machine asks for less than a city host does
# Workers per host in the probe. They share the host's gate, so the rate is the gate's; the
# workers only overlap the waiting. One worker managed 0.6 requests a second on the
# citypln host, whose answers take ~1.6 s --- under half the rate the brief allows.
PROBE_WORKERS = 3
MAX_TRIES = 6

S3_PUBLIC = [           # raw-bucket prefix -> the public hostname that serves the same key
    ("https://sfplanning.s3.amazonaws.com/default/files/", "https://default.sfplanning.org/"),
    ("http://sfplanning.s3.amazonaws.com/default/files/", "https://default.sfplanning.org/"),
    ("https://sfplanning.s3.amazonaws.com/commissions/", "https://commissions.sfplanning.org/"),
    ("http://sfplanning.s3.amazonaws.com/commissions/", "https://commissions.sfplanning.org/"),
]
DEFAULT = "https://default.sfplanning.org/meetingarchive/planning_dept/"
OLD_TREE = DEFAULT + "sf-planning.org/"

# ── the URL families ──────────────────────────────────────────────────────────
# Constructed families: a key (a case variant or a motion number) is substituted into a
# pattern. `kind` says which key. The brief's five, plus the date-keyed city host the
# conditions memo already used.
FAMILIES = {
    "cpcmotions_year": ("motion", "https://commissions.sfplanning.org/cpcmotions/{Y}/{n}.pdf"),
    "cpcmotions_year_mirror": ("motion", DEFAULT + "commissions.sfplanning.org/cpcmotions/{Y}/{n}.pdf"),
    "cpcmotions_ftp": ("motion", OLD_TREE + "ftp/files/Commission/cpcmotions/{n}.pdf"),
    "cpcpackets": ("case", "https://commissions.sfplanning.org/cpcpackets/{case}.pdf"),
    "cpcpackets_ftp": ("case", OLD_TREE + "ftp/files/Commission/cpcpackets/{case}.pdf"),
    "citypln": ("case_date", "https://citypln-m-extnl.sfgov.org/Commissions/CPC/{m}_{d}_{y}/"
                             "Commission%20Packet/{case}.pdf"),
}
# Every URL, constructed or found, is assigned a family by pattern, first match wins. The
# discovered-only families are exact URLs no pattern would have produced.
FAMILY_RX = [
    ("minutes_vault", re.compile(r"(?i)citypln-m-extnl\.sfgov\.org/(?:External/)?link\.ashx|"
                                 r"citypln-m-extnl\.sfgov\.org/SharedLinks\.aspx")),
    ("citypln", re.compile(r"(?i)citypln-m-extnl\.sfgov\.org/Commissions/CPC/\d+_\d+_\d{4}/")),
    ("cpcmotions_year_mirror", re.compile(r"(?i)meetingarchive/planning_dept/commissions\."
                                          r"sfplanning\.org/cpcmotions/")),
    ("cpcpackets_mirror", re.compile(r"(?i)meetingarchive/planning_dept/commissions\."
                                     r"sfplanning\.org/cpcpackets/")),
    ("cpcdra_mirror", re.compile(r"(?i)meetingarchive/planning_dept/commissions\."
                                 r"sfplanning\.org/cpcdra/")),
    ("cpcmotions_ftp", re.compile(r"(?i)sf-planning\.org/ftp/files/Commission/cpcmotions/")),
    ("cpcpackets_ftp", re.compile(r"(?i)sf-planning\.org/ftp/files/Commission/cpcpackets/")),
    ("cpcdra_ftp", re.compile(r"(?i)sf-planning\.org/ftp/files/Commission/cpcdra/")),
    ("oldsite_modules", re.compile(r"(?i)sf-planning\.org/modules/")),
    # The pre-2003 city site served each adopted motion as an HTML page. Both hosts are
    # gone; only the Wayback Machine holds them, and only a handful.
    ("f_motion", re.compile(r"(?i)/planning/f_motion/|sf-planning\.org/f_motion/")),
    ("cpcmot_sfgov", re.compile(r"(?i)sfgov\.org/planning/cpcmot/")),
    ("cpcmotions_year", re.compile(r"(?i)commissions\.sfplanning\.org/cpcmotions/")),
    ("cpcpackets", re.compile(r"(?i)commissions\.sfplanning\.org/cpcpackets/")),
    ("cpcdra", re.compile(r"(?i)commissions\.sfplanning\.org/cpcdra/")),
]


# Families whose filename IS the motion or resolution number.
MOTION_FAMILIES = {"cpcmotions_year", "cpcmotions_year_mirror", "cpcmotions_ftp", "f_motion",
                   "cpcmot_sfgov"}


def family_of(url: str) -> str:
    for name, rx in FAMILY_RX:
        if rx.search(url):
            return name
    return "other"


def vault_public(u: str) -> str:
    """The 2017--2021 minutes print vault links in the staff form (`/link.ashx?...`), which
    redirects to a sign-in page. The same query on the department's public endpoint
    (`/External/link.ashx?...`) serves the document; it is the form the later minutes print.
    Same vault, object and file GUIDs --- a published document, reached the published way."""
    return re.sub(r"(?i)(citypln-m-extnl\.sfgov\.org)/link\.ashx", r"\1/External/link.ashx", u)


def public_url(u: str) -> str:
    """The raw-bucket host answers 403 for a missing key; the public hostnames answer 404.
    Rewrite to the public form so a status means the same thing whichever link found it."""
    for a, b in S3_PUBLIC:
        if u.startswith(a):
            u = b + u[len(a):]
    if u.startswith("http://commissions.sfplanning.org/"):
        u = "https://" + u[len("http://"):]
    if u.startswith("http://citypln-m-extnl.sfgov.org/"):
        u = "https://" + u[len("http://"):]
    return vault_public(u).replace(" ", "%20")


def host_of(u: str) -> str:
    return urlparse(u).netloc.lower()


# ═══════════════════════════════════════════════════════════════════════════
# polite HTTP
# ═══════════════════════════════════════════════════════════════════════════
class Host:
    """A per-host gate: request starts at least 1/rate seconds apart, and a 429 or 5xx
    pushes every later request back rather than only the one that failed."""

    def __init__(self, rate: float):
        self.gap = 1.0 / rate
        self.lock = threading.Lock()
        self.next_t = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next_t)
            self.next_t = t + self.gap
        time.sleep(max(0.0, t - now))

    def back_off(self, seconds: float):
        with self.lock:
            self.next_t = max(self.next_t, time.monotonic() + seconds)


_HOSTS: dict[str, Host] = {}
_HOSTS_LOCK = threading.Lock()


def gate(url: str, rate: float) -> Host:
    h = host_of(url)
    with _HOSTS_LOCK:
        if h not in _HOSTS:
            _HOSTS[h] = Host(WAYBACK_RATE if "archive.org" in h else rate)
        return _HOSTS[h]


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    return s


def polite(s: requests.Session, method: str, url: str, rate: float = RATE, **kw):
    """One request with the gate and exponential backoff on 429/5xx and transport errors.
    Returns the response, or raises the last exception after MAX_TRIES."""
    g = gate(url, rate)
    last = None
    for k in range(MAX_TRIES):
        g.wait()
        try:
            r = s.request(method, url, timeout=kw.pop("timeout", 60) if k == 0 else 90,
                          allow_redirects=True, **kw)
        except requests.RequestException as e:
            last = e
            g.back_off(min(300, 2 ** (k + 1)))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            ra = r.headers.get("Retry-After")
            wait = float(ra) if ra and ra.isdigit() else min(300, 2 ** (k + 2))
            g.back_off(wait)
            last = r
            continue
        return r
    if isinstance(last, requests.Response):
        return last
    raise last


# ═══════════════════════════════════════════════════════════════════════════
# stage 1a: discovery
# ═══════════════════════════════════════════════════════════════════════════
LISTING_URLS = [
    "https://commissions.sfplanning.org/cpcmotions/",
    "https://commissions.sfplanning.org/cpcmotions/2012/",
    "https://commissions.sfplanning.org/cpcmotions/2014/",
    "https://commissions.sfplanning.org/cpcpackets/",
    DEFAULT + "commissions.sfplanning.org/cpcmotions/2012/",
    OLD_TREE + "ftp/files/Commission/cpcmotions/",
    OLD_TREE + "ftp/files/Commission/cpcpackets/",
    "https://default.sfplanning.org/meetingarchive/",
    "https://commissions.sfplanning.org/?list-type=2&prefix=cpcmotions/",
    "https://default.sfplanning.org/?list-type=2&prefix=meetingarchive/",
]

# The CDX prefixes the brief names, plus the three the minutes' own links pointed at: the
# pre-2003 city host `ci.sf.ca.us`, and the old site's `f_motion` and `Modules` paths.
CDX_PREFIXES = [
    "sf-planning.org/ftp/files/Commission/cpcmotions/",
    "sf-planning.org/ftp/files/Commission/cpcpackets/",
    "sf-planning.org/ftp/files/Commission/cpcdra/",
    "commissions.sfplanning.org/cpcmotions/",
    "commissions.sfplanning.org/cpcpackets/",
    "sfgov.org/site/planning",
    "sfgov.org/planning/",
    "sfgov.org/site/uploadedfiles/planning/",
    "ci.sf.ca.us/planning/",
    "sf-planning.org/f_motion/",
    "sf-planning.org/Modules/",
]
CDX = "http://web.archive.org/cdx/search/cdx"
CDX_FIELDS = "original,timestamp,statuscode,mimetype,length"


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")


def discover_listings(s):
    """Does any directory return an index? Recorded either way, because 'no' is the
    reason every other route here exists."""
    out = []
    for u in LISTING_URLS:
        try:
            r = polite(s, "GET", u, timeout=30)
            body = r.text[:200_000] if "text" in r.headers.get("content-type", "") else ""
            hrefs = re.findall(r'(?i)href="([^"]+\.pdf)"', body)
            keys = re.findall(r"<Key>([^<]+)</Key>", body)
            out.append({"url": u, "status": r.status_code,
                        "content_type": r.headers.get("content-type"),
                        "pdf_links": len(hrefs), "s3_keys": len(keys),
                        "is_index": bool(hrefs or keys),
                        "title": (re.search(r"(?is)<title>(.*?)</title>", body) or
                                  [None, ""])[1].strip()[:120] if body else ""})
        except Exception as e:
            out.append({"url": u, "status": None, "error": f"{type(e).__name__}: {e}"})
    (MANIFESTS / "listings.json").write_text(json.dumps(out, indent=2) + "\n")
    print(f"  listings: {sum(o.get('is_index', False) for o in out)} of {len(out)} "
          f"return an index")
    return out


def discover_cdx(s, refresh: bool):
    """One manifest per prefix, paginated to exhaustion, collapsed on URL key so each
    distinct document appears once with its first capture. Written as JSON rows."""
    for p in CDX_PREFIXES:
        f = MANIFESTS / f"cdx_{slug(p)}.json"
        if f.exists() and not refresh:
            continue
        rows = []
        try:
            r = polite(s, "GET", CDX, params={"url": p, "matchType": "prefix",
                                              "showNumPages": "true"}, timeout=120)
            pages = int(r.text.strip() or 1)
        except Exception:
            pages = 1
        for page in range(pages):
            r = polite(s, "GET", CDX, params={"url": p, "matchType": "prefix",
                                              "output": "json", "fl": CDX_FIELDS,
                                              "collapse": "urlkey", "page": page},
                       timeout=300)
            if r.status_code != 200:
                print(f"  cdx {p} page {page}: HTTP {r.status_code}")
                continue
            try:
                j = r.json()
            except ValueError:
                j = []
            rows += j[1:] if j and j[0] and j[0][0] == "original" else j
        f.write_text(json.dumps({"prefix": p, "fields": CDX_FIELDS.split(","),
                                 "retrieved": dt.datetime.now().isoformat(timespec="seconds"),
                                 "pages": pages, "rows": rows}) + "\n")
        print(f"  cdx {p}: {len(rows):,} captured URLs over {pages} page(s)")


# ── hearing pages, 2015--2026 ───────────────────────────────────────────────
HEARING_ARCHIVE = "https://sfplanning.org/cpc-hearing-archives"
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june",
                                      "july", "august", "september", "october", "november",
                                      "december"], 1)}
DATE_TXT = re.compile(r"(?i)\b(January|February|March|April|May|June|July|August|September|"
                      r"October|November|December)\s+(\d{1,2}),?\s+(\d{4})")


def parse_date_txt(t: str):
    m = DATE_TXT.search(t or "")
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))
    except ValueError:
        return None


def discover_hearing_pages(s, refresh: bool):
    """Every 'Supporting' link on the archive table, and every document URL on the
    per-hearing page it points to, with its hearing date. Exact URLs beat constructed ones."""
    out_f = MANIFESTS / "hearing_pages.csv"
    if out_f.exists() and not refresh:
        return
    h = polite(s, "GET", HEARING_ARCHIVE, timeout=60).text
    rows = []
    for tr in re.findall(r"(?s)<tr>(.*?)</tr>", h):
        tds = re.findall(r"(?s)<td[^>]*>(.*?)</td>", tr)
        if len(tds) < 4:
            continue
        date = parse_date_txt(re.sub("<[^>]+>", " ", tds[0]))
        for href in re.findall(r'href="([^"]+)"', tds[3]):
            if href.startswith("http"):
                rows.append((date, href))
    docs = []
    seen = set()
    for i, (date, page) in enumerate(rows, 1):
        if page in seen:
            continue
        seen.add(page)
        try:
            r = polite(s, "GET", page, timeout=60)
        except Exception as e:
            docs.append({"hearing_date": date, "page": page, "url": "", "anchor": "",
                         "status": f"error {type(e).__name__}"})
            continue
        found = 0
        for m in re.finditer(r'(?is)<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', r.text):
            u = urljoin(r.url, m.group(1).strip())
            if not re.search(r"(?i)\.pdf(\?|$)|link\.ashx|SharedLinks", u):
                continue
            docs.append({"hearing_date": date, "page": page, "url": u,
                         "anchor": re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", m.group(2)))
                                     .strip()[:160], "status": r.status_code})
            found += 1
        if i % 50 == 0:
            print(f"  hearing pages: {i}/{len(rows)}", flush=True)
    pd.DataFrame(docs).drop_duplicates(["hearing_date", "url"]).to_csv(out_f, index=False)
    print(f"  hearing pages: {len(seen)} pages, {len(docs):,} document links → {out_f.name}")


# ── the archived old site (1998--2014): agenda pages link the packets ──────────
OLD_AGENDA_ROOTS = ["index.aspx-page=3480.html", "index.aspx-page=1837.html",
                    "index.aspx-page=4354.html"]
OLD_S3 = ("https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/"
          "sf-planning.org/")


def discover_old_site(s, refresh: bool, max_pages: int = 3000):
    """Breadth-first over the archived sf-planning.org agenda pages. The minutes pages are
    already in the corpus and are read locally; the agendas are not, and an agenda is what
    links the packet."""
    out_f = MANIFESTS / "oldsite_links.csv"
    if out_f.exists() and not refresh:
        return
    todo = list(OLD_AGENDA_ROOTS)
    seen, docs = set(), []
    while todo and len(seen) < max_pages:
        page = todo.pop(0)
        if page in seen:
            continue
        seen.add(page)
        u = OLD_S3 + page.replace("=", "%3D")
        try:
            r = polite(s, "GET", u, timeout=60)
        except Exception:
            continue
        if r.status_code != 200:
            continue
        t = r.text
        title = (re.search(r"(?is)<title>(.*?)</title>", t) or [None, ""])[1]
        title = re.sub(r"\s+", " ", title).replace("San Francisco Planning Department :", "")
        # Stay inside the Planning Commission agendas: year pages and meeting pages only.
        is_meeting = parse_date_txt(title) is not None
        for m in re.finditer(r'(?is)<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', t):
            href = m.group(1).strip()
            if re.match(r"index\.aspx-page=\d+\.html$", href) and not is_meeting:
                anchor = re.sub(r"<[^>]+>", " ", m.group(2))
                if re.search(r"\b(19|20)\d\d\b", anchor) or parse_date_txt(anchor):
                    todo.append(href)
            elif re.search(r"(?i)\.(pdf|htm)$", href) and "index.aspx" not in href:
                docs.append({"page": page, "page_title": title.strip(),
                             "hearing_date": parse_date_txt(title),
                             "url": public_url(urljoin(u, href)),
                             "anchor": re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", m.group(2)))
                                         .strip()[:160]})
        if len(seen) % 100 == 0:
            print(f"  old site: {len(seen)} pages, {len(docs):,} links", flush=True)
    pd.DataFrame(docs).drop_duplicates(["page", "url"]).to_csv(out_f, index=False)
    print(f"  old site: {len(seen)} pages crawled, {len(docs):,} document links → {out_f.name}")


# ── the minutes' own hyperlinks, read from the local corpus ─────────────────
INSTR = re.compile(r"(?i)\b(MOTION|RESOLUTION|DRA)\s*(?:NO\.?|#)?\s*:?\s*$")
# Case-sensitive on purpose: the capital letters are the case suffix the minutes print; a
# lower-case tail is the department's document suffix (`2007.0168Cc1` is case 2007.0168C,
# continuation 1; `2010.0970c` is the old site's lower-cased 2010.0970C).
CASE_IN = re.compile(r"(?<![\d.])((?:19|20)\d{2}|\d{2})([.\-])(\d{3,6})([A-Z]*)")
NUM_IN = re.compile(r"\b(?:M-?|R-?|DRA-?)?(\d{3,6})\b")


def _manifest_urls() -> dict[str, str]:
    p = RAW_MINUTES / "_manifest.json"
    if not p.exists():
        return {}
    return {k: v.get("url", "") for k, v in json.loads(p.read_text()).items()}


def _pdf_links(f: Path) -> list[dict]:
    import pymupdf
    out = []
    try:
        d = pymupdf.open(f)
    except Exception:
        return out
    for p in d:
        words = p.get_text("words")          # x0,y0,x1,y1,word,block,line,wordno
        for ln in p.get_links():
            u = ln.get("uri")
            if not u:
                continue
            r = ln["from"]
            anchor = " ".join(w[4] for w in words
                              if pymupdf.Rect(w[:4]).intersects(r)).strip()
            # The label ("MOTION:") sits on the line above or to the left of the number;
            # the nearest one in reading order is the one that belongs to this link.
            above = pymupdf.Rect(r.x0 - 260, r.y0 - 40, r.x1 + 5, r.y1)
            ctx = [w for w in words if pymupdf.Rect(w[:4]).intersects(above)
                   and not pymupdf.Rect(w[:4]).intersects(r)]
            ctx = " ".join(w[4] for w in sorted(ctx, key=lambda w: (round(w[1]), w[0])))
            m = None
            for m in re.finditer(r"(?i)\b(MOTION|RESOLUTION|DRA)\b", ctx):
                pass
            out.append({"url": u, "anchor": anchor[:160], "label": m.group(1).upper() if m
                        else "", "page": p.number + 1})
    return out


def _html_links(f: Path, base: str) -> list[dict]:
    from bs4 import BeautifulSoup
    h = f.read_text(errors="replace")
    soup = BeautifulSoup(h, "html.parser")
    out = []
    # The archived pages disable the live links by renaming the attribute `data-href`
    # (2014: <a data-href="http://commissions.sfplanning.org/cpcmotions/2014/19073.pdf">);
    # reading `href` alone silently dropped every one of them.
    for a in soup.find_all("a"):
        href = (a.get("href") or a.get("data-href") or "").strip()
        if not href:
            continue
        if not re.search(r"(?i)\.(pdf|htm)$|link\.ashx|SharedLinks", href) or \
                "index.aspx" in href:
            continue
        u = urljoin(base, href) if base else (href if href.startswith("http") else "")
        prev = ""
        node = a.previous_sibling
        while node is not None and len(prev) < 80:
            prev = (node.get_text(" ") if hasattr(node, "get_text") else str(node)) + " " + prev
            node = node.previous_sibling
        m = re.search(r"(?i)\b(MOTION|RESOLUTION|DRA)\s*(?:NO\.?)?\s*:?\s*$",
                      re.sub(r"\s+", " ", prev).strip())
        out.append({"url": u, "anchor": re.sub(r"\s+", " ", a.get_text(" ")).strip()[:160],
                    "label": m.group(1).upper() if m else "", "page": None,
                    "unresolved": "" if u else href})
    return out


def discover_minutes_links(refresh: bool):
    """Every hyperlink in the raw minutes that points at a document. The anchor text is
    usually the motion number or the case number itself, so the link is keyed to an item
    without guessing; the label before it says whether it is a motion, a resolution or a
    DRA."""
    out_f = MANIFESTS / "minutes_links.csv"
    if out_f.exists() and not refresh:
        return
    base = _manifest_urls()
    rows = []
    for f in sorted(RAW_MINUTES.glob("*/*")):
        if f.name.startswith("_"):
            continue
        rel = f"{f.parent.name}/{f.name}"
        if f.suffix.lower() == ".pdf":
            links = _pdf_links(f)
        else:
            links = _html_links(f, base.get(rel, ""))
        for l in links:
            l["source_file"] = rel
            l["year"] = int(f.parent.name) if f.parent.name.isdigit() else None
            rows.append(l)
    df = pd.DataFrame(rows)
    df["url"] = df.url.map(lambda u: public_url(u) if u else u)
    df.to_csv(out_f, index=False)
    print(f"  minutes links: {len(df):,} from {df.source_file.nunique()} documents "
          f"({int(df.url.eq('').sum())} relative links with no base) → {out_f.name}")


# ── one table of every exact URL found, keyed as far as the finding allows ────
def _case_of(text: str) -> str:
    m = CASE_IN.search((text or "").replace(" ", ""))
    return norm_case("".join(m.groups())) if m else ""


def _doc_name(u: str) -> str:
    return unquote(urlparse(u).path.rsplit("/", 1)[-1])


OLD_DIRS = re.compile(r"(?i)^https?://(?:www\.)?sf-planning\.org/ftp/files/commission/"
                      r"(cpcmotions|cpcpackets|cpcdra)/(.+)$")


def live_equivalent(orig: str) -> str:
    """Where a Wayback capture would live today, if anywhere. The old site's
    `ftp/files/Commission/...` tree survives on the S3 mirror; commissions.sfplanning.org
    is still served; the pre-2003 hosts are gone and have no equivalent."""
    m = OLD_DIRS.match(orig)
    if m:
        return OLD_TREE + "ftp/files/Commission/" + m.group(1).lower() + "/" + m.group(2)
    m = re.match(r"(?i)^https?://commissions\.sfplanning\.org(?::\d+)?/(.+)$", orig)
    if m:
        return "https://commissions.sfplanning.org/" + m.group(1)
    return ""


def build_discovered():
    """Stack the three exact-URL sources into one keyed table. What a URL is about comes
    from its own anchor or filename, never from proximity to something else."""
    parts = []
    ml = MANIFESTS / "minutes_links.csv"
    if ml.exists():
        d = pd.read_csv(ml, dtype=str).fillna("")
        d["source"] = "minutes"
        d["source_ref"] = d.source_file
        parts.append(d[["url", "anchor", "label", "source", "source_ref", "year"]])
    hp = MANIFESTS / "hearing_pages.csv"
    if hp.exists():
        d = pd.read_csv(hp, dtype=str).fillna("")
        d = d[d.url.ne("")]
        d["label"] = ""
        d["source"] = "hearing_page"
        d["source_ref"] = d.page
        d["year"] = d.hearing_date.str[:4]
        parts.append(d[["url", "anchor", "label", "source", "source_ref", "year",
                        "hearing_date"]])
    ol = MANIFESTS / "oldsite_links.csv"
    if ol.exists():
        d = pd.read_csv(ol, dtype=str).fillna("")
        d["label"] = ""
        d["source"] = "old_site_agenda"
        d["source_ref"] = d.page
        d["year"] = d.hearing_date.str[:4]
        parts.append(d[["url", "anchor", "label", "source", "source_ref", "year",
                        "hearing_date"]])
    # Wayback captures of documents: their live equivalent is probed like any other found
    # URL, so the census says whether the live host still serves what the archive saw.
    cdx = []
    for f in MANIFESTS.glob("cdx_*.json"):
        j = json.loads(f.read_text())
        for orig, ts, st, mime, ln in j.get("rows", []):
            live = live_equivalent(orig)
            if st == "200" and live and re.search(r"(?i)\.pdf$", live):
                cdx.append({"url": live, "anchor": "", "label": "", "source": "wayback_cdx",
                            "source_ref": f"{ts} {orig}", "year": ts[:4]})
    if cdx:
        parts.append(pd.DataFrame(cdx))
    d = pd.concat(parts, ignore_index=True).fillna("")
    d = d[d.url.str.startswith("http")].copy()
    d["url_printed"] = d.url
    d["url"] = d.url.map(public_url)
    d["family"] = d.url.map(family_of)
    d["docname"] = d.url.map(_doc_name)
    # What the document is about. A vault link's anchor is the motion number and its label
    # says which instrument; a packet's filename is the case number. The label is only
    # believed when the anchor is itself a bare number: the label box also catches the
    # previous item's "RESOLUTION:", and a packet link beneath it must not inherit it.
    bare = d.anchor.str.fullmatch(r"\s*(?:M-?|R-?|DRA-?)?\d{3,6}\s*")
    d.loc[~bare, "label"] = ""
    d["motion_no"] = np.where(bare & d.label.ne(""), d.anchor.map(_num), "")
    # An old-site module named `17525-documentid=4077.pdf` is motion 17525; one named
    # `DRA-0123-documentid=…` is a DRA and is keyed by the label instead.
    is_mot = d.family.isin(MOTION_FAMILIES) | (d.family.eq("oldsite_modules")
                                              & d.docname.str.match(r"\d{5}\b"))
    d.loc[is_mot, "motion_no"] = d.loc[is_mot, "docname"].map(_num)
    d["case_no"] = [(_case_of(n) or _case_of(a)) if not m else ""
                    for n, a, m in zip(d.docname, d.anchor, is_mot)]
    d["instrument"] = np.where(d.label.ne(""), d.label.str.lower(),
                               np.where(d.family.str.contains("dra"), "dra",
                                        np.where(is_mot, "motion", "")))
    # Only a document about a case or a motion is a census row; agendas, minutes,
    # presentations and code-library links stay in the manifest and are not probed.
    d["census"] = (d.family.ne("other") & (d.motion_no.ne("") | d.case_no.ne("") |
                                           d.family.eq("minutes_vault")))
    d.to_csv(DISCOVERED, index=False)
    print(f"  discovered: {len(d):,} links, {d.url.nunique():,} distinct URLs")
    print(d.drop_duplicates("url").groupby(["source", "family"]).size().to_string())
    return d


def _num(t: str) -> str:
    m = NUM_IN.search(t or "")
    return m.group(1).lstrip("0") or "0" if m else ""


STEPS = ["listings", "cdx", "hearing", "oldsite", "minutes", "build"]


def discover(refresh: bool, steps: list[str] | None = None, max_pages: int = 600):
    """The crawl cap is where the old site stops yielding: on the first full run the agenda
    crawl found its last new document link by page ~450 of 3,000 and walked the rest for
    nothing."""
    MANIFESTS.mkdir(parents=True, exist_ok=True)
    steps = steps or STEPS
    s = session()
    if "listings" in steps:
        print("1a.1 directory listings")
        discover_listings(s)
    if "cdx" in steps:
        print("1a.2 Wayback CDX")
        discover_cdx(s, refresh)
    if "hearing" in steps:
        print("1a.3 hearing pages 2015--2026")
        discover_hearing_pages(s, refresh)
    if "oldsite" in steps:
        print("1a.3b archived agenda pages 1998--2014")
        discover_old_site(s, refresh, max_pages)
    if "minutes" in steps:
        print("1a.3c hyperlinks in the minutes corpus")
        discover_minutes_links(refresh)
    if "build" in steps:
        build_discovered()


# ═══════════════════════════════════════════════════════════════════════════
# stage 1b: the probe
# ═══════════════════════════════════════════════════════════════════════════
AVAIL_COLS = ["key_type", "key", "candidate_url", "family", "host", "variant", "source",
              "status", "final_url", "content_type", "bytes", "last_modified", "method",
              "probed_at"]


def case_variants(it: pd.DataFrame) -> dict[str, set[str]]:
    """For each case in the universe: the case as printed (every printed form), its stem,
    and the stem with every suffix the minutes attach to any case sharing that stem."""
    it = it[it.cn.ne("")]
    suffixes = defaultdict(set)
    for c, st in zip(it.cn, it.stem):
        if st and c.startswith(st) and len(c) > len(st):
            suffixes[st].add(c[len(st):])
    raw = it.groupby("cn").cn_raw.agg(set)
    out = {}
    for cn in case_universe(it):
        v = {cn} | set(raw.get(cn, set()))
        st = case_stem(cn)
        if st:
            v.add(st)
            v |= {st + sfx for sfx in suffixes.get(st, ())}
        out[cn] = {x for x in v if x}
    return out


def motion_numbers(it: pd.DataFrame) -> pd.DataFrame:
    """Every motion and resolution number the item table records, with the hearing years
    of the items that carry it."""
    ino = it.action_instrument_no.astype(str).str.strip()
    m = it[it.action_instrument.isin(["motion", "resolution"]) & ino.str.fullmatch(r"\d{3,6}")
           & ino.ne("0")].assign(n=ino.str.lstrip("0"))
    return (m.groupby("n").agg(instrument=("action_instrument", "first"),
                               years=("year", lambda s: sorted(set(int(y) for y in s))))
            .reset_index())


def build_candidates(it: pd.DataFrame, disc: pd.DataFrame | None) -> list[dict]:
    cands = []
    add = cands.append
    # discovered exact URLs first: they are the census's backbone and the cheapest hits
    if disc is not None and len(disc):
        for r in disc[disc.census.astype(str).eq("True")].drop_duplicates("url").itertuples():
            if "archive.org" in r.url:
                continue
            key = r.motion_no or r.case_no or r.docname
            add(dict(key_type="discovered", key=key, candidate_url=r.url, family=r.family,
                     variant=r.instrument or "", source=r.source))
    variants = case_variants(it)
    dates = it[it.cn.ne("")].groupby("cn").meeting_date.agg(
        lambda s: sorted({d.date() for d in s.dropna()}))
    for cn, vs in variants.items():
        for v in sorted(vs):
            add(dict(key_type="case", key=cn, candidate_url=FAMILIES["cpcpackets"][1]
                     .format(case=v), family="cpcpackets", variant=v, source="constructed"))
            add(dict(key_type="case", key=cn, candidate_url=FAMILIES["cpcpackets_ftp"][1]
                     .format(case=v), family="cpcpackets_ftp", variant=v,
                     source="constructed"))
            low = re.sub(r"([A-Z]+)$", lambda m: m.group(1).lower(), v)
            if low != v:            # the old site wrote the suffix in lower case
                add(dict(key_type="case", key=cn, candidate_url=FAMILIES["cpcpackets_ftp"][1]
                         .format(case=low), family="cpcpackets_ftp", variant=low,
                         source="constructed"))
            for d in dates.get(cn, []):
                add(dict(key_type="case", key=cn, candidate_url=FAMILIES["citypln"][1].format(
                    case=v, m=d.month, d=d.day, y=d.year), family="citypln",
                    variant=f"{v}@{d.isoformat()}", source="constructed"))
    for r in motion_numbers(it).itertuples():
        forms = [r.n] + (["R" + r.n] if r.instrument == "resolution" else [])
        years = sorted({y + k for y in r.years for k in (0, -1, 1)})
        for f in forms:
            for Y in years:
                for fam in ("cpcmotions_year", "cpcmotions_year_mirror"):
                    add(dict(key_type="motion", key=r.n, candidate_url=FAMILIES[fam][1]
                             .format(Y=Y, n=f), family=fam, variant=f"{f}@{Y}",
                             source="constructed"))
            add(dict(key_type="motion", key=r.n, candidate_url=FAMILIES["cpcmotions_ftp"][1]
                     .format(n=f), family="cpcmotions_ftp", variant=f, source="constructed"))
    for c in cands:
        c["host"] = host_of(c["candidate_url"])
    return cands


def _done_urls() -> dict[str, dict]:
    if not AVAIL_FULL.exists():
        return {}
    d = pd.read_csv(AVAIL_FULL, dtype=str).fillna("")
    # 429 (too many requests) and 425 (too early) are "ask again later", not an answer.
    ok = d[d.status.str.fullmatch(r"[1-4]\d\d") & ~d.status.isin(["425", "429"])]
    return {u: True for u in ok.candidate_url}


def head_one(s: requests.Session, url: str, rate: float) -> dict:
    """HEAD, falling back to a ranged GET where HEAD is refused (405/501) or where the
    host answers HEAD with a 403 it might not mean. Records what the server said."""
    method = "HEAD"
    try:
        r = polite(s, "HEAD", url, rate=rate, timeout=30)
        if r.status_code in (403, 405, 501):
            method = "GET-range"
            r = polite(s, "GET", url, rate=rate, timeout=30,
                       headers={"Range": "bytes=0-1023"}, stream=True)
            r.close()
    except Exception as e:
        return {"status": f"error:{type(e).__name__}", "method": method}
    size = r.headers.get("content-length", "")
    cr = r.headers.get("content-range", "")
    if cr and "/" in cr:
        size = cr.rsplit("/", 1)[-1]
    status = str(r.status_code)
    if re.search(r"(?i)/login\.aspx", r.url):
        status = "login"            # a 200 that is a sign-in page is not a document
    return {"status": status, "final_url": r.url, "method": method,
            "content_type": r.headers.get("content-type", ""), "bytes": size,
            "last_modified": r.headers.get("last-modified", "")}


def probe(limit: int | None = None, hosts: list[str] | None = None):
    """One thread per host, each at PROBE_RATE, all appending to one CSV. Resumable: a URL
    with a definite answer (any 1xx--4xx other than 429) is never asked again; errors and
    5xx are retried on the next run."""
    STORE.mkdir(parents=True, exist_ok=True)
    it = load_items()
    disc = pd.read_csv(DISCOVERED, dtype=str).fillna("") if DISCOVERED.exists() else None
    cands = build_candidates(it, disc)
    done = _done_urls()
    seen, todo = set(), []
    for c in cands:
        if c["candidate_url"] in done or c["candidate_url"] in seen:
            continue
        seen.add(c["candidate_url"])
        todo.append(c)
    if hosts:
        todo = [c for c in todo if c["host"] in hosts]
    by_host = defaultdict(list)
    for c in todo:
        by_host[c["host"]].append(c)
    # Within a host: exact URLs, then case families, then motion families, and the mirror
    # of commissions.sfplanning.org's motions last --- it duplicates a host already probed.
    order = {"discovered": 0, "case": 1, "motion": 2}
    for h in by_host:
        by_host[h].sort(key=lambda c: (order[c["key_type"]],
                                       c["family"] == "cpcmotions_year_mirror"))
        if limit:
            by_host[h] = by_host[h][:limit]
    print(f"{len(cands):,} candidates, {len(done):,} already answered, "
          f"{sum(len(v) for v in by_host.values()):,} to probe:")
    for h, v in sorted(by_host.items(), key=lambda kv: -len(kv[1])):
        print(f"  {h:40s} {len(v):>8,}  (~{len(v)/PROBE_RATE/3600:.1f} h)")
    q: queue.Queue = queue.Queue()
    new_file = not AVAIL_FULL.exists()

    def writer():
        with AVAIL_FULL.open("a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=AVAIL_COLS, extrasaction="ignore")
            if new_file:
                w.writeheader()
            n = 0
            while True:
                row = q.get()
                if row is None:
                    break
                w.writerow(row)
                n += 1
                if n % 200 == 0:
                    fh.flush()

    def worker(h, items, counter):
        s = session()
        while True:
            with counter["lock"]:
                if counter["next"] >= len(items):
                    return
                c = items[counter["next"]]
                counter["next"] += 1
            res = head_one(s, c["candidate_url"], PROBE_RATE)
            q.put({**c, **res, "probed_at": dt.datetime.now().isoformat(timespec="seconds")})
            with counter["lock"]:
                counter["done"] += 1
                i = counter["done"]
            if i % 1000 == 0:
                el = time.time() - counter["t0"]
                print(f"  [{h}] {i:,}/{len(items):,}  {el/60:.0f} min, "
                      f"~{(len(items)-i)*el/i/3600:.1f} h left", flush=True)

    wt = threading.Thread(target=writer, daemon=True)
    wt.start()
    threads = []
    for h, v in by_host.items():
        counter = {"lock": threading.Lock(), "next": 0, "done": 0, "t0": time.time()}
        threads += [threading.Thread(target=worker, args=(h, v, counter))
                    for _ in range(PROBE_WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    q.put(None)
    wt.join()
    d = pd.read_csv(AVAIL_FULL, dtype=str).fillna("")
    print(f"probe done: {len(d):,} rows; hits {int(d.status.eq('200').sum()):,}")
    print(d[d.status.eq("200")].groupby("family").size().to_string())


# ═══════════════════════════════════════════════════════════════════════════
# stage 1c: pull — download every hit, keep the motion section, extract the text
# ═══════════════════════════════════════════════════════════════════════════
PACKET_FAMILIES = {"cpcpackets", "cpcpackets_ftp", "cpcpackets_mirror", "citypln"}
MOTION_DOC_FAMILIES = MOTION_FAMILIES | {"oldsite_modules", "minutes_vault"}
TESSDATA = STORE / "tessdata"
MAX_PAGES = 150             # a packet's motion is in its first pages; the rest is plans
KEEP_WHOLE_BYTES = 5_000_000    # an adopted motion under this is kept whole; over it, the
                                # vault copy carries its plan set and is cut like a packet
OCR_CAP = 40                # pages OCR'd per document at most; logged when it binds
PULL_COLS = ["doc_id", "url", "family", "key_type", "key", "instrument", "status", "bytes",
             "n_pages", "kept_pages", "ocr_pages", "n_sections", "n_with_exhibit_a",
             "wall_s", "pulled_at", "error"]


def load_census() -> pd.DataFrame:
    """The census as every stage reads it. A 200 whose final URL is the vault's sign-in page
    is recorded as `login`, and a 200 that is the vault's error page as `vault_not_public`:
    the probe records what the server said, and this is where it is read, so no stage can
    count a page that is not the document as a document."""
    a = pd.read_csv(AVAIL_FULL, dtype=str).fillna("")
    # A URL asked again after a transport error or a 425/429 keeps its first row in the
    # file; the latest answer is the answer.
    a = a.drop_duplicates("candidate_url", keep="last")
    a.loc[a.final_url.str.contains(r"(?i)/login\.aspx", regex=True), "status"] = "login"
    # A public vault link that answers with an HTML page is the vault's error page ("Error
    # executing child request for /External/openfile.aspx. Log In to M-Files"): the object is
    # not shared publicly. A SharedLinks URL is HTML by design and is not caught here.
    vault_err = (a.candidate_url.str.contains("link.ashx", regex=False) &
                 a.content_type.str.lower().str.startswith("text/html") & a.status.eq("200"))
    a.loc[vault_err, "status"] = "vault_not_public"
    # A dead host that redirects every old address to its new home page answers 200 with a
    # page that is not the document (the pre-2003 `ci.sf.ca.us/planning/f_motion/16070.htm`
    # lands on `https://www.sf.gov/`): a soft 404, counted as absent.
    root = a.final_url.map(lambda u: urlparse(u).path in ("", "/") if u else False)
    asked = a.candidate_url.map(lambda u: urlparse(u).path not in ("", "/"))
    a.loc[a.status.eq("200") & root & asked, "status"] = "soft_404"
    return a


def pull_targets() -> pd.DataFrame:
    """Every URL the probe answered 200 for that is a document about a case or a motion,
    plus the Wayback captures of documents no live URL still serves. DRAs are recorded in
    the census but not pulled: a Discretionary Review Action is not a conditioned
    authorisation, and the conditions memo found one DR packet in 41 with conditions."""
    if not AVAIL_FULL.exists():
        return pd.DataFrame()
    a = load_census()
    a = a[a.status.eq("200")]
    disc = pd.read_csv(DISCOVERED, dtype=str).fillna("") if DISCOVERED.exists() else None
    inst = {}
    if disc is not None:
        inst = dict(zip(disc.url, disc.instrument))
    a["instrument"] = a.candidate_url.map(inst).fillna("")
    a.loc[a.family.isin(MOTION_FAMILIES) & a.instrument.eq(""), "instrument"] = "motion"
    a = a[~a.family.str.contains("dra") & a.instrument.ne("dra")]
    a = a[a.family.isin(PACKET_FAMILIES | MOTION_DOC_FAMILIES)]
    ct = a.content_type.str.lower()
    a = a[ct.str.contains("pdf|octet|binary") | a.family.eq("minutes_vault") | ct.eq("")]
    a = a.drop_duplicates("candidate_url")
    a["url"] = a.candidate_url
    a["size"] = pd.to_numeric(a.bytes, errors="coerce")
    a["container"] = np.where(a.family.isin(PACKET_FAMILIES), "packet", "motion")
    w = wayback_targets(set(a.url))
    t = pd.concat([a[["url", "family", "key_type", "key", "instrument", "size", "container"]],
                   w], ignore_index=True)
    # adopted motions first (small, and the rarer object), then packets smallest first
    t["order"] = np.where(t.container.eq("motion"), 0, 1)
    return t.sort_values(["order", "size"], na_position="last").reset_index(drop=True)


def wayback_targets(live: set[str]) -> pd.DataFrame:
    """Captures of motions and packets that no live URL serves: the pre-2003 motion pages,
    whose hosts are gone, and old-tree PDFs whose live equivalent the census has probed and
    found missing. A capture whose live equivalent has not been probed yet waits for the
    probe; one the live host still serves is pulled from the live host instead."""
    answered = {}
    if AVAIL_FULL.exists():
        a = pd.read_csv(AVAIL_FULL, dtype=str, usecols=["candidate_url", "status"]).fillna("")
        answered = dict(zip(a.candidate_url, a.status))
    rows = []
    for f in MANIFESTS.glob("cdx_*.json"):
        j = json.loads(f.read_text())
        for orig, ts, st, mime, ln in j.get("rows", []):
            if st != "200":
                continue
            fam = family_of(orig)
            name = _doc_name(orig)
            if fam in ("f_motion", "cpcmot_sfgov") and re.match(r"\d{4,6}\.html?$", name):
                key, kt, cont = _num(name), "motion", "html"
            elif fam in ("cpcmotions_ftp", "cpcmotions_year") and re.match(r"R?\d{4,6}", name):
                key, kt, cont = _num(name), "motion", "motion"
            elif fam in ("cpcpackets_ftp", "cpcpackets") and _case_of(name):
                key, kt, cont = _case_of(name), "case", "packet"
            else:
                continue
            if cont != "html":
                eq = live_equivalent(orig)
                if not eq or eq in live or not re.fullmatch(r"4\d\d", answered.get(eq, "")):
                    continue            # still served, or not yet probed
            rows.append({"url": f"https://web.archive.org/web/{ts}id_/{orig}", "family":
                         f"wayback:{fam}", "key_type": kt, "key": key, "instrument": "motion"
                         if kt == "motion" else "", "size": pd.to_numeric(ln, errors="coerce"),
                         "container": cont, "orig": orig, "ts": ts})
    w = pd.DataFrame(rows)
    if not len(w):
        return pd.DataFrame(columns=["url", "family", "key_type", "key", "instrument", "size",
                                     "container"])
    w = w.sort_values("ts").drop_duplicates("orig")
    return w[["url", "family", "key_type", "key", "instrument", "size", "container"]]


def _resolve_vault(s, url: str) -> str:
    """A SharedLinks page is HTML naming the file; the bytes are at its REST endpoint.
    The older link.ashx form downloads directly."""
    if "SharedLinks" not in url:
        return url
    r = polite(s, "GET", url, rate=PULL_RATE, timeout=60)
    m = re.search(r"REST/sharedlinks/[^'\"]+/content", r.text)
    return urljoin(r.url, m.group(0)) if m else ""


def _download(s, url: str, tmp: Path, max_bytes: int) -> tuple[str, int]:
    """Stream to a temp file. A failed or truncated transfer leaves no file behind: an
    empty file was once read as 'no conditions'."""
    tmp.unlink(missing_ok=True)
    r = polite(s, "GET", url, rate=PULL_RATE, timeout=120, stream=True)
    if r.status_code != 200:
        r.close()
        return f"http {r.status_code}", 0
    size = int(r.headers.get("content-length") or 0)
    if max_bytes and size > max_bytes:
        r.close()
        return "skipped_size", size
    n = 0
    try:
        with tmp.open("wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
                n += len(chunk)
                if max_bytes and n > max_bytes:
                    raise ValueError("over --max-mb while streaming")
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return f"error {type(e).__name__}", n
    # Content-Length counts the bytes on the wire; iter_content yields them decoded. When
    # the response is compressed the two differ and only the wire count means anything.
    wire = r.raw.tell() if r.headers.get("content-encoding") else n
    if size and wire != size:
        tmp.unlink(missing_ok=True)
        return "error truncated", n
    head = tmp.open("rb").read(1024)
    if b"%PDF" not in head[:1024] and re.search(rb"(?i)<html|<!doctype", head) and \
            not url.lower().endswith((".htm", ".html")) and "id_/" not in url:
        tmp.unlink(missing_ok=True)
        return "error html not a document", n
    return "ok", n


def _html_pages(raw: bytes) -> list[dict]:
    """A pre-2003 motion served as HTML, laid out as pseudo-pages so the same parser reads
    it: one line per block of text, bold kept from <b>/<strong>."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(raw, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    lines = []
    for el in soup.find_all(["p", "div", "td", "li", "h1", "h2", "h3", "h4", "center", "pre"]):
        if el.find(["p", "div", "td", "li"]):
            continue
        for part in el.get_text("").split("\n"):
            t = re.sub(r"\s+", " ", part).strip()
            if t:
                bold = int(bool(el.find(["b", "strong"])) and
                           len(" ".join(b.get_text(" ") for b in el.find_all(["b", "strong"])))
                           >= 0.9 * len(t))
                lines.append({"t": t, "x": 72.0, "y": 0.0, "sp": [[t, bold, 0]]})
    pages, per = [], 45
    for k in range(0, len(lines), per):
        chunk = lines[k:k + per]
        for m, l in enumerate(chunk):
            l["y"] = 100.0 + 14 * m
        pages.append({"n": k // per + 1, "w": 612, "h": 792, "ocr": False, "lines": chunk})
    return pages


def extract_pdf(path: Path) -> dict:
    """Text for the pages that matter, OCR only where there is no text layer and only
    inside a motion, and the page range to keep."""
    import pymupdf
    import condition_parser as cp
    os.environ.setdefault("TESSDATA_PREFIX", str(TESSDATA))
    doc = pymupdf.open(path)
    n = doc.page_count
    pages = [cp.page_lines(doc[i]) for i in range(min(n, MAX_PAGES))]
    ocr_done: list[int] = []

    def ocr(i):
        # One Page object for both calls: the OCR text page holds only a weak reference to
        # the page it was made from, and a second `doc[i]` is a new object --- the first is
        # collected and reading the text page raises ReferenceError.
        pg = doc[i]
        tp = pg.get_textpage_ocr(language="eng", dpi=300, full=True)
        pages[i] = cp.page_lines(pg, textpage=tp)
        ocr_done.append(i + 1)

    secs = cp.find_sections(pages)
    # A scanned document: no motion found, and the early pages have no text layer. OCR from
    # the front until a motion with an Exhibit A has been read to its end, or the cap.
    if not any(s.exa for s in secs) and sum(not cp.has_text(p) for p in pages[:10]) >= 2:
        for i in range(min(len(pages), OCR_CAP)):
            if not cp.has_text(pages[i]) and not cp.is_plan_page(pages[i]):
                ocr(i)
                if i % 5 == 4:
                    secs = cp.find_sections(pages)
                    if any(s.exa and s.exa_end and s.exa_end[0] <= i for s in secs):
                        break
        secs = cp.find_sections(pages)
    # A text document with image-only pages inside a motion (a signed page, a scanned
    # exhibit): OCR exactly those.
    for s in secs:
        lo = s.start
        hi = min((s.exa_end[0] if s.exa_end else s.end), len(pages) - 1)
        for i in range(lo, hi + 1):
            if len(ocr_done) >= OCR_CAP:
                break
            if not cp.has_text(pages[i]) and not cp.is_plan_page(pages[i]) and \
                    (i + 1) not in ocr_done:
                ocr(i)
    if ocr_done:
        secs = cp.find_sections(pages)
    kept = sorted({i for s in secs for i in range(s.start, s.end + 1)})
    full_text = "\n".join(cp.page_text(p) for p in pages)
    markers = {
        "mentions_coa": bool(re.search(r"(?i)conditions\s+of\s+approval", full_text)),
        "mentions_exhibit_a": bool(re.search(r"(?i)exhibit\s+a\b", full_text)),
        "mentions_compliance_line": bool(re.search(r"(?i)for information about compliance",
                                                   full_text)),
        "subject_to_conditions": bool(re.search(r"(?is)approv\w*.{0,300}?subject\s+to\s+"
                                                r"(?:the\s+)?(?:following\s+)?conditions",
                                                full_text)),
    }
    # The page text worth caching: the kept pages, plus the other non-plan pages of the
    # first 60 so a better boundary rule can be tried without downloading again. Every
    # other page stays in the list as an empty stub, because a plan sheet is where an
    # Exhibit A ends and a parser re-run from the cache has to see it there.
    cache = []
    for i, p in enumerate(pages):
        plan = cp.is_plan_page(p)
        if i in kept or (i < 60 and not plan):
            cache.append(p)
        else:
            cache.append({"n": p["n"], "w": p["w"], "h": p["h"], "ocr": p["ocr"],
                          "lines": [], "stub": "plan" if plan else "skipped"})
    return {"doc": doc, "n_pages": n, "pages": cache, "kept": kept,
            "ocr": ocr_done, "markers": markers, "n_sections": len(secs),
            "n_exa": sum(1 for s in secs if s.exa)}


def pull(n: int | None, max_mb: int | None, only: str | None = None,
         host: str | None = None, years: tuple[int, int] | None = None):
    """One process per host keeps each host at PULL_RATE while the hosts download in
    parallel; the processes share the log (one short append per document) and nothing else.
    `years` restricts to targets whose case or motion was first heard in that span --- for
    reaching one era's format before the size-ordered queue gets to it."""
    DOCS.mkdir(parents=True, exist_ok=True)
    t = pull_targets()
    if only:
        t = t[t.container.eq(only)]
    if host:
        t = t[t.url.map(host_of).eq(host)]
    if years:
        it = load_items()
        first_case = it[it.cn.ne("")].groupby("cn").year.min()
        ino = it.action_instrument_no.astype(str).str.strip().str.lstrip("0")
        first_mot = it.assign(n=ino)[ino.ne("")].groupby("n").year.min()
        # as `census_years` reads a key: a discovered URL's key is a motion number when it is
        # all digits and a case number otherwise
        y = [first_case.get(k) if kt == "case" or (kt == "discovered" and not str(k).isdigit())
             else first_mot.get(k) for k, kt in zip(t.key, t.key_type)]
        y = pd.to_numeric(pd.Series(y, index=t.index), errors="coerce")
        t = t[y.between(*years)]
    done = set()
    if PULL_LOG.exists():
        lg = pd.read_csv(PULL_LOG, dtype=str).fillna("")
        # an answer that is not a transport failure is final; failures are retried, and so is
        # a document skipped for size that the cap of this run admits (the tail, pulled in
        # the morning with a larger --max-mb)
        lg = lg.drop_duplicates("url", keep="last")
        nb = pd.to_numeric(lg.bytes, errors="coerce").fillna(0)
        admit = lg.status.eq("skipped_size") & (nb <= (max_mb or 10**6) * 1_000_000)
        done = set(lg[~lg.status.str.startswith(("error", "http 5", "http 429")) & ~admit].url)
    todo = t[~t.url.isin(done)]
    if n:
        todo = todo.head(n)
    print(f"{len(t):,} pull targets ({int(t.container.eq('motion').sum()):,} motions, "
          f"{int(t.container.eq('packet').sum()):,} packets, "
          f"{int(t.container.eq('html').sum()):,} HTML); {len(todo):,} to pull")
    max_bytes = (max_mb or 0) * 1_000_000
    s = session()
    # The download goes to the machine's temp directory, not the store: the store is on
    # Dropbox, and a temp file killed mid-write there came back as "conflicted copy" files.
    import tempfile
    tmp = Path(tempfile.gettempdir()) / f"mfhr_pull_{host or 'all'}_{os.getpid()}.tmp"
    new = not PULL_LOG.exists()
    with PULL_LOG.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PULL_COLS, extrasaction="ignore")
        if new:
            w.writeheader()
        for k, r in enumerate(todo.itertuples(), 1):
            t0 = time.time()
            row = {"url": r.url, "family": r.family, "key_type": r.key_type, "key": r.key,
                   "instrument": r.instrument, "pulled_at":
                   dt.datetime.now().isoformat(timespec="seconds")}
            try:
                src = _resolve_vault(s, r.url) if r.family == "minutes_vault" else r.url
                if not src:
                    row.update(status="error no content link")
                else:
                    st, nb = _download(s, src, tmp, max_bytes)
                    row.update(status=st, bytes=nb)
                    if st == "ok":
                        row.update(_ingest(tmp, r))
            except Exception as e:
                row.update(status=f"error {type(e).__name__}", error=str(e)[:200])
            tmp.unlink(missing_ok=True)
            row["wall_s"] = f"{time.time() - t0:.1f}"
            w.writerow(row)
            fh.flush()
            if k % 10 == 0 or float(row["wall_s"]) > 60:
                print(f"  [{k}/{len(todo)}] {r.family} {r.key} {row['status']} "
                      f"{row.get('n_pages', '')}p ocr={row.get('ocr_pages', '')} "
                      f"{row['wall_s']}s", flush=True)


def _ingest(tmp: Path, r, redo: dict | None = None) -> dict:
    """Hash, extract, keep the motion section, cache the lines. The same bytes reached by
    two URLs (a mirror) are one document. `redo` is the cached record of a document being
    extracted again: its URLs and keys are kept, everything read from the bytes is new."""
    import pymupdf
    raw = tmp.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    doc_id = sha[:16]
    lines_f = DOCS / f"{doc_id}.lines.json.gz"
    meta = {"doc_id": doc_id}
    if lines_f.exists() and redo is None:
        j = json.loads(gzip.decompress(lines_f.read_bytes()))
        if r.url not in j["urls"]:
            j["urls"].append(r.url)
            j["families"].append(r.family)
            lines_f.write_bytes(gzip.compress(json.dumps(j).encode()))
        return {**meta, "status": "ok duplicate", "n_pages": j["n_pages"],
                "kept_pages": _ranges(j["kept"]), "ocr_pages": len(j["ocr"])}
    is_html = raw[:4] != b"%PDF"
    if is_html:
        if not re.search(rb"(?i)<html|<body|<p", raw[:5000]):
            return {**meta, "status": "error not a document"}
        import condition_parser as cp
        pages = _html_pages(raw)
        secs = cp.find_sections(pages)
        ex = {"n_pages": len(pages), "pages": pages, "kept": list(range(len(pages))),
              "ocr": [], "markers": {}, "n_sections": len(secs),
              "n_exa": sum(1 for s in secs if s.exa)}
        (DOCS / f"{doc_id}.html").write_bytes(raw)
    else:
        ex = extract_pdf(tmp)
        doc = ex.pop("doc")
        kept = ex["kept"]
        whole = r.container == "motion" and len(raw) <= KEEP_WHOLE_BYTES
        if whole:
            (DOCS / f"{doc_id}.pdf").write_bytes(raw)
            ex["kept_file"] = "whole"
        elif kept:
            out = pymupdf.open()
            for a_, b_ in _runs(kept):
                out.insert_pdf(doc, from_page=a_, to_page=b_)
            out.save(DOCS / f"{doc_id}.pdf", garbage=3, deflate=True)
            out.close()
            ex["kept_file"] = "motion section"
        else:
            ex["kept_file"] = "none"
        doc.close()
    j = {"doc_id": doc_id, "sha256": sha, "bytes": len(raw), "urls": [r.url],
         "families": [r.family], "container": "html" if is_html else r.container,
         "key_type": r.key_type, "key": r.key, "instrument": r.instrument,
         "pulled_at": dt.datetime.now().isoformat(timespec="seconds"), **ex}
    if redo is not None:
        j.update({k: redo[k] for k in ("urls", "families", "pulled_at") if k in redo})
        j["reextracted_at"] = dt.datetime.now().isoformat(timespec="seconds")
    part = lines_f.with_suffix(".part")         # renamed into place whole, never half-read
    part.write_bytes(gzip.compress(json.dumps(j).encode()))
    part.replace(lines_f)
    return {**meta, "status": "ok", "n_pages": ex["n_pages"], "kept_pages": _ranges(ex["kept"]),
            "ocr_pages": len(ex["ocr"]), "n_sections": ex["n_sections"],
            "n_with_exhibit_a": ex["n_exa"]}


def _garbled(j: dict) -> bool:
    """A cache written before `page_lines` honoured /Rotate: a rotated page's lines sit
    outside the page box, with x and y swapped and columns merged."""
    return any("rot" not in p and any(l["x"] > p["w"] + 5 or l["y"] > p["h"] + 5
                                      for l in p["lines"]) for p in j["pages"])


def reextract(network: bool):
    """Extract again every cached document whose lines were read from rotated pages
    without the rotation. From the kept PDF where it is the whole document; otherwise, with
    `--network`, from the original URL --- and only if the bytes are the bytes first read
    (same SHA-256), so the doc_id and every reference to it stay true."""
    from types import SimpleNamespace
    import tempfile
    todo = []
    for f in sorted(DOCS.glob("*.lines.json.gz")):
        try:
            j = json.loads(gzip.decompress(f.read_bytes()))
        except (OSError, EOFError, ValueError):
            continue
        if _garbled(j):
            todo.append(j)
    print(f"{len(todo)} documents read from rotated pages without the rotation")
    s = session()
    tmp = Path(tempfile.gettempdir()) / f"mfhr_reextract_{os.getpid()}.tmp"
    log = STORE / "reextract_log.csv"
    new = not log.exists()
    with log.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["doc_id", "source", "status", "at"])
        for j in todo:
            local = DOCS / f"{j['doc_id']}.pdf"
            r = SimpleNamespace(url=j["urls"][0], family=j["families"][0],
                                container=j["container"], key_type=j["key_type"],
                                key=j["key"], instrument=j["instrument"])
            src = "local"
            if j.get("kept_file") == "whole" and local.exists():
                tmp.write_bytes(local.read_bytes())
            elif network:
                src = "network"
                u = _resolve_vault(s, r.url) if r.family == "minutes_vault" else r.url
                st, _ = _download(s, u, tmp, 0) if u else ("error no content link", 0)
                if st != "ok":
                    w.writerow([j["doc_id"], src, st, dt.datetime.now().isoformat()])
                    continue
            else:
                w.writerow([j["doc_id"], "skipped", "needs --network",
                            dt.datetime.now().isoformat()])
                continue
            if hashlib.sha256(tmp.read_bytes()).hexdigest() != j["sha256"]:
                st = "bytes changed; not re-extracted"
            else:
                st = _ingest(tmp, r, redo=j)["status"]
            tmp.unlink(missing_ok=True)
            w.writerow([j["doc_id"], src, st, dt.datetime.now().isoformat()])
            fh.flush()
            print(f"  {j['doc_id']} {src} {st}", flush=True)


def _pending(max_mb, only, host=None) -> bool:
    t = pull_targets()
    if only:
        t = t[t.container.eq(only)]
    if host:
        t = t[t.url.map(host_of).eq(host)]
    done = set(pd.read_csv(PULL_LOG, dtype=str).url) if PULL_LOG.exists() else set()
    return bool(len(t[~t.url.isin(done)]))


def _runs(idx: list[int]) -> list[tuple[int, int]]:
    out = []
    for i in sorted(idx):
        if out and i == out[-1][1] + 1:
            out[-1] = (out[-1][0], i)
        else:
            out.append((i, i))
    return out


def _ranges(idx: list[int]) -> str:
    return ";".join(f"{a + 1}-{b + 1}" if a != b else f"{a + 1}" for a, b in _runs(idx))


# ═══════════════════════════════════════════════════════════════════════════
# stage 1d: parse — one row per condition
# ═══════════════════════════════════════════════════════════════════════════
COND_LONG = STORE / "conditions_long"
SECTIONS = STORE / "motion_sections.csv"
# The brief's one stated renumbering, checked against what the documents themselves say.
STATED_RENUMBER = {"315": "415"}


def _iso(d: str) -> str:
    if not d:
        return ""
    try:
        t = pd.to_datetime(re.sub(r"\s+", " ", d), errors="coerce")
    except Exception:
        return ""
    # a date printed but not parseable ("September 31, 2014") is no date, not "NaT"; nor is
    # one no Commission hearing could have ("October 10, 1024", a 2024 motion's typo)
    if pd.isna(t) or not 1950 <= t.year <= dt.date.today().year + 1:
        return ""
    return t.date().isoformat()


class CaseKey:
    """Map a case number read from a document onto the reconciled universe. Exact first,
    then the two-digit year expanded against the hearing year, then the stem --- and a
    stem shared by several cases in the universe maps to none of them, because a blank is a
    question and a wrong case is a silent error."""

    def __init__(self, it: pd.DataFrame):
        from acquire_external_data import expand_yy
        self.expand_yy = expand_yy
        self.uni = set(case_universe(it))
        self.by_stem = defaultdict(set)
        for c in self.uni:
            st = case_stem(c)
            if st:
                self.by_stem[st].add(c)

    def __call__(self, cn: str, year: int | None) -> tuple[str, str]:
        c = norm_case(cn)
        if not c:
            return "", ""
        if c in self.uni:
            return c, "exact"
        # The old four-digit serial was sometimes printed unpadded ("2007.461C"); padding it
        # is a normalisation, the same case written two ways, not a match on similarity.
        m = re.match(r"^(\d{4})\.(\d{1,3})([A-Z]*)$", c)
        if m and f"{m.group(1)}.{m.group(2).zfill(4)}{m.group(3)}" in self.uni:
            return f"{m.group(1)}.{m.group(2).zfill(4)}{m.group(3)}", "serial padded"
        e = self.expand_yy(c, year) if year else c
        if e in self.uni:
            return e, "expanded"
        st = case_stem(e)
        hits = self.by_stem.get(st, set())
        if len(hits) == 1:
            return next(iter(hits)), "stem"
        return "", ("ambiguous stem" if hits else "not in universe")


def _url_date(u: str) -> str:
    m = re.search(r"/CPC/(\d{1,2})_(\d{1,2})_(\d{4})/", u)
    return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else ""


def parse_docs():
    """Re-parse every cached document from its lines. Nothing is downloaded: a parser
    change is a local re-run."""
    import condition_parser as cp
    it = load_items()
    ck = CaseKey(it)
    ino = it.action_instrument_no.astype(str).str.strip().str.lstrip("0")
    mot_items = (it[it.action_instrument.isin(["motion", "resolution"]) & ino.ne("")]
                 .assign(n=ino).groupby("n")
                 .agg(m_date=("meeting_date", "min"), m_case=("cn", "first")))
    secs_out, rows = [], []
    files = sorted(DOCS.glob("*.lines.json.gz"))
    for f in files:
        # A pull running beside the parse may be mid-write on its newest file; that one is
        # read on the next parse, not half-read now.
        try:
            j = json.loads(gzip.decompress(f.read_bytes()))
        except (OSError, EOFError, ValueError) as e:
            print(f"  {f.name}: unreadable ({type(e).__name__}), skipped this run")
            continue
        pages = j["pages"]
        try:
            secs = cp.find_sections(pages)
        except Exception as e:
            print(f"  {j['doc_id']}: {type(e).__name__}: {e}")
            continue
        url = j["urls"][0]
        for si, sec in enumerate(secs):
            m = sec.meta
            conds = cp.parse_section(pages, sec) if sec.exa else []
            number = m["number"] or (j["key"] if j["key_type"] == "motion" and not m["draft"]
                                     and si == 0 else "")
            hd = _iso(m["hearing_date"]) or _url_date(url)
            if not hd and number and number in mot_items.index and \
                    pd.notna(mot_items.loc[number, "m_date"]):      # an item can lack its date
                hd = mot_items.loc[number, "m_date"].date().isoformat()
            yr = int(hd[:4]) if hd else None
            doc_case = m["case_no"] or (j["key"] if j["key_type"] == "case" and si == 0 else "")
            if not doc_case and number in mot_items.index:
                doc_case = mot_items.loc[number, "m_case"]
            case_key, how = ck(doc_case, yr)
            # An adopted motion whose number the item table records names its case there.
            # Where the document's own case number does not resolve (an ambiguous stem, an
            # OCR-damaged number, or minutes and motion disagreeing on the year: motion 20118
            # prints 2016-004562CUA where the minutes print 2017-004562CUA), the motion number
            # --- the key this census is built on --- decides, and the match says so. The
            # document's own reading stays in `case_no`, so the disagreement stays visible.
            if how not in ("exact", "expanded", "serial padded") and number and \
                    number in mot_items.index and not m["draft"]:
                ic = mot_items.loc[number, "m_case"]
                if isinstance(ic, str) and ic:
                    case_key, how = ic, "motion number"
            doc_type = "draft_packet" if m["draft"] else "adopted_motion"
            # Embedded: an adopted motion that is not the document's own. In a packet that is
            # every adopted section; in an adopted-motion file it is a later section carrying a
            # different number --- a 2010 motion modifying a 2001 one attaches the original.
            own = secs[0].meta["number"] if secs else ""
            embedded = (not m["draft"]) and (j["container"] == "packet" or
                                              (si > 0 and number and number != own))
            # What the section says beside what was parsed from it (pitfall 5: a format change
            # looks like missing data unless the mention rate is measured next to the parse
            # rate). Read over the whole section, maps included, before any boundary cut.
            sec_text = "\n".join(cp.page_text(pages[k]) for k in
                                 range(sec.start, min(len(pages), (secs[si + 1].start
                                                                  if si + 1 < len(secs)
                                                                  else len(pages)))))
            rec = {"doc_id": j["doc_id"], "section": si, "doc_url": url,
                   "mentions_exhibit_a": bool(re.search(r"(?i)\bexhibit\s+a\b", sec_text)),
                   "mentions_coa": bool(re.search(r"(?i)conditions\s+of\s+approval", sec_text)),
                   "exhibit_a_not_conditions": m.get("exhibit_a_not_conditions", ""),
                   "families": ";".join(dict.fromkeys(j["families"])),
                   "container": j["container"], "doc_type": doc_type,
                   "embedded_in_packet": embedded, "instrument": m["instrument"],
                   "motion_no": number, "case_no": norm_case(doc_case),
                   "case_key": case_key, "case_match": how, "hearing_date": hd,
                   "adoption_date": _iso(m["adoption_date"]), "decides": m["decides"],
                   "page_first": pages[sec.start]["n"], "page_last": pages[sec.end]["n"],
                   "has_exhibit_a": sec.exa is not None, "n_conditions": len(conds),
                   "ocr_pages": len(j.get("ocr", [])), "n_pages": j["n_pages"],
                   **{f"doc_{k}": v for k, v in j.get("markers", {}).items()}}
            secs_out.append(rec)
            for c in conds:
                rows.append({**{k: rec[k] for k in ("doc_id", "section", "doc_url", "container",
                                                    "doc_type", "embedded_in_packet",
                                                    "instrument", "motion_no", "case_no",
                                                    "case_key", "case_match", "hearing_date",
                                                    "adoption_date")},
                             "case_stem": case_stem(rec["case_key"] or rec["case_no"]),
                             **c})
    sec = pd.DataFrame(secs_out)
    df = pd.DataFrame(rows)
    if not len(df):
        print("no conditions parsed yet")
        sec.to_csv(SECTIONS, index=False)
        return
    renum = {**cp.learn_renumbering(df.body), **STATED_RENUMBER}
    df["n_chars"] = df.body.str.len()
    df["has_dollar"] = df.body.str.contains(cp.HAS_DOLLAR)
    df["has_percent"] = df.body.str.contains(cp.HAS_PERCENT)
    df["has_numeric_quantity"] = df.body.str.contains(cp.QUANTITY)
    cited = df.body.map(cp.code_sections)
    df["code_sections_cited"] = cited.map(";".join)
    df["code_sections_norm"] = cited.map(
        lambda xs: ";".join(dict.fromkeys(renum.get(re.sub(r"\(.*$", "", x), x) for x in xs)))
    # One copy of each motion section is canonical: the same adopted motion arrives as a
    # standalone file, a mirror, and an exhibit in a later packet. Prefer the standalone,
    # then the text layer over OCR, then the fuller parse.
    sec["skey"] = np.where(sec.doc_type.eq("adopted_motion"),
                           "A|" + sec.motion_no.fillna("") + "|" + sec.case_key.fillna(""),
                           "D|" + sec.case_key.where(sec.case_key.ne(""), sec.case_no) + "|"
                           + sec.hearing_date.fillna(""))
    sec["rank"] = list(zip(sec.embedded_in_packet.astype(int), sec.ocr_pages.gt(0).astype(int),
                           -sec.n_conditions))
    order = sec.sort_values(["skey", "rank"])
    canon = set(order.drop_duplicates("skey")[["doc_id", "section"]].itertuples(index=False,
                                                                                 name=None))
    sec["canonical"] = [(d, s_) in canon for d, s_ in zip(sec.doc_id, sec.section)]
    df["canonical"] = [(d, s_) in canon for d, s_ in zip(df.doc_id, df.section)]
    cols = ["case_no", "case_key", "case_stem", "motion_no", "doc_type", "doc_url",
            "hearing_date", "adoption_date", "section_heading", "condition_no", "ordinal",
            "heading", "body", "compliance_contact", "page_start", "page_end", "n_chars",
            "has_dollar", "has_percent", "has_numeric_quantity", "code_sections_cited",
            "code_sections_norm", "parse_method", "parse_confidence", "part", "implicit",
            "sequence_gap", "style", "container", "embedded_in_packet", "instrument",
            "case_match", "canonical", "doc_id", "section"]
    df = df[cols]
    df.to_parquet(COND_LONG.with_suffix(".parquet"), index=False)
    df.to_csv(COND_LONG.with_suffix(".csv"), index=False)
    sec.drop(columns=["rank"]).to_csv(SECTIONS, index=False)
    (STORE / "renumbering_learned.json").write_text(json.dumps(renum, indent=2) + "\n")
    print(f"{len(files):,} documents, {len(sec):,} motion sections "
          f"({int(sec.has_exhibit_a.sum()):,} with an Exhibit A), {len(df):,} condition rows "
          f"({int(df.canonical.sum()):,} canonical) → {COND_LONG.name}.parquet")


# ═══════════════════════════════════════════════════════════════════════════
# stage 1d: validation --- a frozen gold set, and a hand check out of sample
# ═══════════════════════════════════════════════════════════════════════════
VALID = STORE / "validation"
GOLD_DOCS = VALID / "gold_docs.json"
GOLD_LABELS = VALID / "gold_labels.csv"
# Labels changed after checking the source, one row per decision, applied by `score` rather
# than by editing the labels: the labels stay as written, and the decision is on the record.
ADJUDICATIONS = VALID / "gold_adjudications.csv"
ROUND = 3                   # the current round; rounds 1 and 2 are kept to report what each
                            # later round changed
GOLD_N, CHECK_N, SEED = 20, 30, 20260911


def frozen_path(r: int) -> Path:
    return VALID / f"FROZEN_ROUND{r}.json"


def handcheck_path(r: int) -> Path:
    return VALID / f"handcheck_round{r}.csv"


def score_path(r: int) -> Path:
    return VALID / f"score_round{r}.json"


def round_archive(r: int) -> Path:
    """What a closed round keeps so its score can be reproduced after the rules move on: the
    frozen parser and its parse of the gold documents."""
    return VALID / f"round{r}"
ERAS = [(0, 2002, "1998--2002"), (2003, 2009, "2003--2009"), (2010, 2010, "2010"),
        (2011, 2014, "2011--2014"), (2015, 2017, "2015--2017"), (2018, 2021, "2018--2021"),
        (2022, 2100, "2022--2026")]


def era_of(y) -> str:
    try:
        y = int(y)
    except (TypeError, ValueError):
        return "undated"
    return next((lab for lo, hi, lab in ERAS if lo <= y <= hi), "undated")


def gold_select():
    """Twenty documents spanning eras and formats, drawn once with a fixed seed and written
    to disk; every later stage reads the list rather than re-drawing it. Stratified on era
    x container (adopted motion file, packet, HTML page), with the strata filled round-robin
    so no one format dominates, and at least two documents with no Exhibit A --- a section
    detector that finds conditions where there are none is an error the gold set must be
    able to see."""
    if GOLD_DOCS.exists():
        sys.exit(f"{GOLD_DOCS} exists; the gold set is drawn once")
    VALID.mkdir(parents=True, exist_ok=True)
    sec = pd.read_csv(SECTIONS, dtype=str).fillna("")
    sec["year"] = sec.hearing_date.str[:4].where(sec.hearing_date.ne(""),
                                                 sec.adoption_date.str[:4])
    docs = (sec.groupby("doc_id").agg(container=("container", "first"),
                                      year=("year", lambda s: min((y for y in s if y), default="")),
                                      exa=("has_exhibit_a", lambda s: (s == "True").any()))
            .reset_index())
    docs["era"] = docs.year.map(era_of)
    # A document still carrying lines read from rotated pages without the rotation is not
    # labelled: its lines will change when `reextract` reaches it.
    garbled = set()
    for d in docs.doc_id:
        j = json.loads(gzip.decompress((DOCS / f"{d}.lines.json.gz").read_bytes()))
        if _garbled(j):
            garbled.add(d)
    docs = docs[~docs.doc_id.isin(garbled)]
    rnd = random.Random(SEED)
    strata = {k: sorted(g.doc_id) for k, g in docs[docs.exa].groupby(["era", "container"])}
    for v in strata.values():
        rnd.shuffle(v)
    pick = []
    while len(pick) < GOLD_N - 2 and any(strata.values()):
        for k in sorted(strata):
            if strata[k] and len(pick) < GOLD_N - 2:
                pick.append(strata[k].pop())
    none = sorted(docs.loc[~docs.exa, "doc_id"])
    rnd.shuffle(none)
    pick += none[:GOLD_N - len(pick)]
    out = docs[docs.doc_id.isin(pick)].to_dict("records")
    GOLD_DOCS.write_text(json.dumps({"drawn": dt.datetime.now().isoformat(timespec="seconds"),
                                     "seed": SEED, "docs": out}, indent=2) + "\n")
    for d in out:
        dump_doc(d["doc_id"])
    print(f"gold set: {len(out)} documents → {GOLD_DOCS}; reading dumps in {VALID}/dumps/")
    print(pd.DataFrame(out).groupby(["era", "container"]).size().to_string())


def dump_doc(doc_id: str) -> Path:
    """The document as a reader checks it: every line of every cached page with its page,
    left position and a bold marker, so a label can be written against the source layout."""
    j = json.loads(gzip.decompress((DOCS / f"{doc_id}.lines.json.gz").read_bytes()))
    out = VALID / "dumps" / f"{doc_id}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    L = [f"doc {doc_id}  urls {j['urls']}  pages {j['n_pages']}  ocr {j.get('ocr', [])}"]
    for pg in j["pages"]:
        if not pg["lines"]:
            L.append(f"=== page {pg['n']} [{pg.get('stub', 'no text')}]")
            continue
        L.append(f"=== page {pg['n']}  {pg['w']}x{pg['h']}  {'OCR' if pg.get('ocr') else ''}")
        for l in pg["lines"]:
            b = "".join("B" if s[1] else ("i" if s[2] else ".") for s in l["sp"] if s[0].strip())
            L.append(f"{b[:5]:5s} x{l['x']:5.0f} y{l['y']:5.0f} | {l['t']}")
    out.write_text("\n".join(L) + "\n")
    return out


def freeze(r: int = ROUND):
    """Hash the rules before anyone scores against them. `score` refuses to run if the
    parser has changed since."""
    VALID.mkdir(parents=True, exist_ok=True)
    f = frozen_path(r)
    if f.exists():
        sys.exit(f"{f} exists; a round is frozen once")
    src = HERE / "condition_parser.py"
    f.write_text(json.dumps({
        "round": r,
        "frozen": dt.datetime.now().isoformat(timespec="seconds"),
        "files": {"condition_parser.py": hashlib.sha256(src.read_bytes()).hexdigest()},
        "gold_docs": [d["doc_id"] for d in json.loads(GOLD_DOCS.read_text())["docs"]],
        "note": "in-sample = the gold documents, drawn from the pool of documents the rules "
                "were written against" + (" and, from round 2, revised against after round 1 "
                                          "was scored" if r > 1 else "") +
                (". Round 3's rules were written against corpus-wide merge detectors run over "
                 "every document, gold included, with the gold documents themselves never "
                 "opened; its gold score is still in-sample" if r > 2 else "") +
                "; out-of-sample = the hand-checked rows, drawn after this freeze from "
                "documents outside the gold set that were pulled after it"},
        indent=2) + "\n")
    print(f"frozen → {f}")


def archive_round(r: int):
    """Close round `r`: keep its frozen parser, its parse of the gold documents, and its
    whole parse --- the last so that what the next round changes can be counted against it
    (the memo reports each round's effect on the numbers the previous one produced)."""
    fz = json.loads(frozen_path(r).read_text())
    d = round_archive(r)
    d.mkdir(parents=True, exist_ok=True)
    src = HERE / "condition_parser.py"
    if hashlib.sha256(src.read_bytes()).hexdigest() != fz["files"]["condition_parser.py"]:
        sys.exit("the parser has moved since this round was frozen; archive before revising")
    (d / "condition_parser.py").write_bytes(src.read_bytes())
    c = pd.read_parquet(COND_LONG.with_suffix(".parquet"))
    c[c.doc_id.isin(fz["gold_docs"])].to_parquet(d / "gold_rows.parquet", index=False)
    c.to_parquet(d / "conditions_long.parquet", index=False)
    if SECTIONS.exists():
        (d / "motion_sections.csv").write_bytes(SECTIONS.read_bytes())
    print(f"round {r} archived → {d}")


def _verify_frozen(r: int = ROUND, parser: Path | None = None) -> dict:
    fz = json.loads(frozen_path(r).read_text())
    now = hashlib.sha256((parser or HERE / "condition_parser.py").read_bytes()).hexdigest()
    if now != fz["files"]["condition_parser.py"]:
        sys.exit(f"the parser does not match round {r}'s freeze; scores against it would not be "
                 "scores. Re-freeze and re-draw, or restore the frozen version.")
    return fz


def sample_check(r: int = ROUND):
    """Thirty rows drawn at random, after the freeze, from canonical rows of documents
    outside the gold set, written with blank columns for the hand check."""
    fz = _verify_frozen(r)
    hc = handcheck_path(r)
    if hc.exists():
        sys.exit(f"{hc} exists; the sample is drawn once")
    c = pd.read_parquet(COND_LONG.with_suffix(".parquet"))
    c = c[c.canonical & ~c.doc_id.isin(fz["gold_docs"])]
    # Out of sample means unseen: documents pulled after the freeze, which no rule was
    # written against.
    after = []
    for d in c.doc_id.unique():
        j = json.loads(gzip.decompress((DOCS / f"{d}.lines.json.gz").read_bytes()))
        if j.get("pulled_at", "") > fz["frozen"]:
            after.append(d)
    c = c[c.doc_id.isin(after)]
    print(f"{len(after):,} documents pulled after the freeze, {len(c):,} canonical rows")
    smp = c.sample(CHECK_N, random_state=SEED)
    smp = smp[["doc_id", "section", "ordinal", "doc_url", "doc_type", "hearing_date",
               "page_start", "page_end", "section_heading", "condition_no", "heading", "body",
               "compliance_contact", "parse_method", "parse_confidence"]].copy()
    for col in ("start_ok", "end_ok", "heading_ok", "notes"):
        smp[col] = ""
    smp.to_csv(hc, index=False)
    for d in smp.doc_id.unique():
        dump_doc(d)
    print(f"{len(smp)} rows → {hc}; fill start_ok, end_ok, heading_ok (1/0; heading_ok "
          f"blank where the source has no heading) from the source pages")


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(t).lower()).strip()


def gold_labels() -> pd.DataFrame:
    """The labels as written, with the adjudications applied."""
    g = pd.read_csv(GOLD_LABELS, dtype=str).fillna("")
    if ADJUDICATIONS.exists():
        adj = pd.read_csv(ADJUDICATIONS, dtype=str).fillna("")
        drop = set(zip(adj.loc[adj.action.eq("drop"), "doc_id"], adj.loc[adj.action.eq("drop"), "seq"]))
        g = g[[(d, q) not in drop for d, q in zip(g.doc_id, g.seq)]]
    return g


def score(r: int = ROUND):
    """In-sample: the parser's rows on the gold documents against the hand labels ---
    boundary precision and recall, heading accuracy. Out-of-sample: the hand-checked rows.
    Reported apart, and said which is which. A closed round is scored from its archive."""
    arch = round_archive(r)
    if r < ROUND and (arch / "condition_parser.py").exists():
        fz = _verify_frozen(r, arch / "condition_parser.py")
        c = pd.read_parquet(arch / "gold_rows.parquet")
    else:
        fz = _verify_frozen(r)
        c = pd.read_parquet(COND_LONG.with_suffix(".parquet"))
    g = gold_labels()
    res = {"round": r, "frozen": fz["frozen"], "in_sample": {}, "out_of_sample": {}}
    pr_rows = c[c.doc_id.isin(fz["gold_docs"])]
    gl = g[g.seq.ne("0")]
    matched_p, matched_g, head_ok, head_n = set(), set(), 0, 0
    # Matched within the document, not within the parser's section index: a section-detection
    # error would otherwise misalign every label after it and be counted many times over.
    for d, gg in gl.groupby("doc_id"):
        pp = pr_rows[pr_rows.doc_id == d]
        for gi, gr in gg.iterrows():
            f, l_ = _norm(gr.first_words), _norm(gr.last_words)
            for pi, prow in pp.iterrows():
                if pi in matched_p:
                    continue
                full = _norm(prow.heading + " " + prow.body)
                body = _norm(prow.body)
                if (body.startswith(f) or full.startswith(f)) and body.endswith(l_):
                    matched_p.add(pi)
                    matched_g.add(gi)
                    if gr.heading or prow.heading:
                        head_n += 1
                        head_ok += int(_norm(gr.heading) == _norm(prow.heading))
                    break
    n_p, n_g = len(pr_rows), len(gl)
    docs_none = set(g.loc[g.seq.eq("0"), "doc_id"])
    res["in_sample"] = {
        "documents": len(fz["gold_docs"]), "gold_conditions": n_g, "parsed_rows": n_p,
        "boundary_precision": len(matched_p) / n_p if n_p else None,
        "boundary_recall": len(matched_g) / n_g if n_g else None,
        "heading_accuracy": head_ok / head_n if head_n else None, "heading_n": head_n,
        "no_conditions_docs": len(docs_none),
        "false_conditions_in_those": int(pr_rows.doc_id.isin(docs_none).sum()),
        "adjudicated": int(pd.read_csv(ADJUDICATIONS).shape[0]) if ADJUDICATIONS.exists() else 0}
    hc = handcheck_path(r)
    if hc.exists():
        h = pd.read_csv(hc, dtype=str).fillna("")
        h = h[h.start_ok.ne("")]
        both = (h.start_ok.eq("1") & h.end_ok.eq("1"))
        hh = h[h.heading_ok.ne("")]
        res["out_of_sample"] = {
            "rows": len(h), "boundary_precision": float(both.mean()) if len(h) else None,
            "start_ok": float(h.start_ok.eq("1").mean()) if len(h) else None,
            "end_ok": float(h.end_ok.eq("1").mean()) if len(h) else None,
            "heading_accuracy": float(hh.heading_ok.eq("1").mean()) if len(hh) else None,
            "heading_n": len(hh)}
    # What this round did to the previous round's out-of-sample rows: a row the current parse
    # reproduces exactly (same document, heading, body and compliance line) keeps its hand
    # judgment; one it no longer produces is listed, because its judgment no longer applies.
    prev = handcheck_path(r - 1)
    if r == ROUND and r > 1 and prev.exists():
        h0 = pd.read_csv(prev, dtype=str).fillna("")
        h0 = h0[h0.start_ok.ne("")]
        cur = c.assign(k=[f"{d}|{_norm(a_)}|{_norm(b_)}|{_norm(x_)}" for d, a_, b_, x_ in
                          zip(c.doc_id, c.heading.fillna(""), c.body.fillna(""),
                              c.compliance_contact.fillna(""))])
        keys = set(cur.k)
        h0["k"] = [f"{d}|{_norm(a_)}|{_norm(b_)}|{_norm(x_)}" for d, a_, b_, x_ in
                   zip(h0.doc_id, h0.heading, h0.body, h0.compliance_contact)]
        kept = h0.k.isin(keys)
        ok = h0.start_ok.eq("1") & h0.end_ok.eq("1")
        res["previous_round_rows"] = {
            "round": r - 1, "rows": len(h0), "reproduced": int(kept.sum()),
            "reproduced_correct": int((kept & ok).sum()),
            "previous_correct": int(ok.sum()),
            "not_reproduced": [{"doc_id": d, "ordinal": o, "heading": hd, "was_correct": bool(w)}
                               for d, o, hd, w in zip(h0.doc_id[~kept], h0.ordinal[~kept],
                                                      h0.heading[~kept], ok[~kept])]}
    score_path(r).write_text(json.dumps(res, indent=2) + "\n")
    print(json.dumps(res, indent=2))


# ═══════════════════════════════════════════════════════════════════════════
# the census, read back: sources, years, coverage
# ═══════════════════════════════════════════════════════════════════════════
SOURCE_LABEL = {
    "cpcmotions_year": "adopted motions, commissions.sfplanning.org (by year)",
    "cpcmotions_year_mirror": "adopted motions, the S3 mirror of the same",
    "cpcmotions_ftp": "adopted motions, the old site's FTP tree",
    "oldsite_modules": "adopted motions, the old site's document modules",
    "minutes_vault": "adopted motions, the department vault",
    "f_motion": "adopted motions, pre-2003 city pages (Wayback)",
    "cpcmot_sfgov": "adopted motions, pre-2008 city pages (Wayback)",
    "cpcpackets": "packets, commissions.sfplanning.org",
    "cpcpackets_ftp": "packets, the old site's FTP tree",
    "cpcpackets_mirror": "packets, the S3 mirror",
    "citypln": "packets, citypln (by hearing date)",
    "embedded": "adopted motions embedded in a later packet",
}


# The census table's columns: (family, label, what it holds, where it lives).
CENSUS_COLS = [
    ("cpcmotions_year", "A-yr", "adopted", r"\texttt{commissions.sfplanning.org/cpcmotions/<year>/<motion>.pdf}"),
    ("cpcmotions_year_mirror", "A-yr-m", "adopted", r"the same keys on the S3 mirror, \texttt{default.sfplanning.org/meetingarchive/planning\_dept/commissions.sfplanning.org/cpcmotions/...}"),
    ("cpcmotions_ftp", "A-ftp", "adopted", r"the old site's FTP tree on the mirror, \texttt{.../sf-planning.org/ftp/files/Commission/cpcmotions/<motion>.pdf}"),
    ("oldsite_modules", "A-mod", "adopted", r"the old site's document modules, \texttt{.../sf-planning.org/modules/<motion>-documentid=<n>.pdf}, linked from the archived agenda and minutes pages"),
    ("f_motion", "A-html", "adopted", r"the pre-2003 city site's HTML motion pages, \texttt{ci.sf.ca.us/planning/f\_motion/<motion>.htm}; Wayback captures only"),
    ("cpcmot_sfgov", "A-html", "adopted", r"the same, on \texttt{sfgov.org/planning/cpcmot/}; Wayback captures only"),
    ("minutes_vault", "A-vault", "adopted", r"the department's document vault, \texttt{citypln-m-extnl.sfgov.org/External/link.ashx?...} and \texttt{SharedLinks.aspx?...}, linked from the 2017-- minutes"),
    ("cpcpackets", "P", "draft", r"\texttt{commissions.sfplanning.org/cpcpackets/<case>.pdf}"),
    ("cpcpackets_mirror", "P-m", "draft", r"the same keys on the S3 mirror"),
    ("cpcpackets_ftp", "P-ftp", "draft", r"the old site's FTP tree, \texttt{.../ftp/files/Commission/cpcpackets/<case>.pdf}, live and in Wayback captures"),
    ("citypln", "P-date", "draft", r"\texttt{citypln-m-extnl.sfgov.org/Commissions/CPC/<M>\_<D>\_<YYYY>/Commission\%20Packet/<case>.pdf}, keyed by hearing date"),
    ("cpcdra", "DRA", "DR action", r"\texttt{commissions.sfplanning.org/cpcdra/}, its mirror and the old FTP tree"),
    ("cpcdra_mirror", "DRA", "DR action", ""),
    ("cpcdra_ftp", "DRA", "DR action", ""),
]


def source_of(families: str, embedded) -> str:
    if str(embedded) == "True":
        return "embedded"
    fam = str(families).split(";")[0]
    return fam.replace("wayback:", "")


def section_frame() -> pd.DataFrame:
    s = pd.read_csv(SECTIONS, dtype=str).fillna("")
    s["n_conditions"] = pd.to_numeric(s.n_conditions, errors="coerce").fillna(0).astype(int)
    s["year"] = pd.to_numeric(s.hearing_date.str[:4].where(s.hearing_date.ne(""),
                                                           s.adoption_date.str[:4]),
                              errors="coerce")
    s["source"] = [source_of(f, e) for f, e in zip(s.families, s.embedded_in_packet)]
    s["canonical"] = s.canonical.eq("True")
    s["wayback"] = s.families.str.contains("wayback:")
    return s


def census_years(it: pd.DataFrame) -> pd.DataFrame:
    """Each census hit with the hearing year of what it is about: a case's first hearing, a
    motion's hearing, or the year of the page that linked it."""
    a = load_census()
    a = a[a.status.eq("200")].copy()
    first_case = it[it.cn.ne("")].groupby("cn").year.min()
    ino = it.action_instrument_no.astype(str).str.strip().str.lstrip("0")
    first_mot = it.assign(n=ino)[ino.ne("")].groupby("n").year.min()
    disc = pd.read_csv(DISCOVERED, dtype=str).fillna("") if DISCOVERED.exists() else None
    dy = dict(zip(disc.url, pd.to_numeric(disc.year, errors="coerce"))) if disc is not None \
        else {}
    y = []
    for r in a.itertuples():
        if r.key_type == "case":
            y.append(first_case.get(r.key, np.nan))
        elif r.key_type == "motion":
            y.append(first_mot.get(r.key, np.nan))
        else:
            v = first_mot.get(r.key, np.nan) if r.key.isdigit() else first_case.get(r.key, np.nan)
            y.append(v if pd.notna(v) else dy.get(r.candidate_url, np.nan))
    a["year"] = y
    return a


# ═══════════════════════════════════════════════════════════════════════════
# stage 1f: the morning summary
# ═══════════════════════════════════════════════════════════════════════════
def summary():
    it = load_items()
    a_all = load_census()
    hits = census_years(it)
    s = section_frame()
    lg = pd.read_csv(PULL_LOG, dtype=str).fillna("") if PULL_LOG.exists() else pd.DataFrame()
    c = pd.read_parquet(COND_LONG.with_suffix(".parquet")) if COND_LONG.with_suffix(
        ".parquet").exists() else pd.DataFrame()
    L = ["# Task 1 — census of Commission condition text: morning summary", "",
         f"Written {dt.datetime.now().isoformat(timespec='minutes')} by "
         f"`collect_conditions.py summary`. Every number below is computed from the files in "
         f"this directory.", ""]
    probe_log = (STORE / "probe.log").read_text() if (STORE / "probe.log").exists() else ""
    L += ["## The probe", "",
          f"- Candidate URLs answered: {len(a_all):,}; the probe "
          f"{'has finished' if 'probe done' in probe_log[-3000:] else 'is still running'}.",
          "- Status: " + ", ".join(f"{k} {v:,}" for k, v in a_all.status.value_counts().items()),
          ""]
    lf = MANIFESTS / "listings.json"
    if lf.exists():
        ls_ = json.loads(lf.read_text())
        L += ["## Discovery", "",
              f"- Directory listings: {sum(bool(x.get('is_index')) for x in ls_)} of {len(ls_)} "
              f"URLs tried returned an index.",
              f"- Exact URLs found (minutes' links, hearing pages, archived agendas, CDX live "
              f"equivalents): {pd.read_csv(DISCOVERED, dtype=str).url.nunique():,} distinct.", ""]
    cdx = cdx_yield()
    if len(cdx):
        kinds = [k for k in CDX_KINDS if k in cdx.columns]
        L += ["### Wayback CDX yield by year of first capture (status 200, distinct URLs)", "",
              "| year | " + " | ".join(kinds) + " |", "|---|" + "---:|" * len(kinds)]
        for y_, r_ in cdx.iterrows():
            L.append(f"| {y_} | " + " | ".join(f"{int(r_[k]):,}" for k in kinds) + " |")
        L.append("")
    L += ["### Hits (HTTP 200, a document) by URL family and first hearing year", "",
          "| year | " + " | ".join(sorted(hits.family.unique())) + " | all |",
          "|---|" + "---:|" * (hits.family.nunique() + 1)]
    fams = sorted(hits.family.unique())
    for y_, g in hits.groupby(hits.year.fillna(-1).astype(int)):
        vc = g.family.value_counts()
        L.append(f"| {'undated' if y_ < 0 else y_} | " +
                 " | ".join(f"{vc.get(f_, 0):,}" for f_ in fams) + f" | {len(g):,} |")
    vc = hits.family.value_counts()
    L.append("| all | " + " | ".join(f"{vc.get(f_, 0):,}" for f_ in fams) + f" | {len(hits):,} |")
    L.append("")
    s_t = s[s.canonical & s.n_conditions.gt(0)]
    L += ["### Earliest hearing year with parsed condition text, by source", "",
          "| source | earliest year | motions / cases with text | conditions |", "|---|---:|---:|---:|"]
    for src, g in s_t.groupby("source"):
        lab_ = SOURCE_LABEL.get(src, src)
        L.append(f"| {lab_}{' (via Wayback)' if g.wayback.any() and 'Wayback' not in lab_ else ''} "
                 f"| {int(g.year.min()) if g.year.notna().any() else '—'} | {len(g):,} | "
                 f"{int(g.n_conditions.sum()):,} |")
    L.append("")
    if len(lg):
        fin = lg.drop_duplicates("url", keep="last")        # each document's final outcome
        ok = fin.status.str.startswith("ok")
        w = pd.to_numeric(lg.wall_s, errors="coerce")
        L += ["## The pull", "",
              f"- Documents attempted: {len(fin):,} ({len(lg):,} attempts, retries included); "
              f"read {int(ok.sum()):,} ({int(fin.status.eq('ok duplicate').sum()):,} of them "
              f"byte-identical to one already read); still over the size cap "
              f"{int(fin.status.eq('skipped_size').sum()):,} (the first pass capped at 60 MB, "
              f"the tail was pulled at 250 MB).",
              f"- Pages OCR'd: {int(pd.to_numeric(lg.ocr_pages, errors='coerce').fillna(0).sum()):,}"
              f" across {int(pd.to_numeric(lg.ocr_pages, errors='coerce').fillna(0).gt(0).sum()):,}"
              f" documents.",
              f"- Wall time per document: median {w.median():.1f} s, 95th percentile "
              f"{w.quantile(.95):.1f} s, max {w.max():.0f} s; total {w.sum()/3600:.1f} h "
              f"(summed over the per-host processes, which ran in parallel).", "",
              "Failures (not retried successfully):", ""]
        last = lg.drop_duplicates("url", keep="last")
        bad = last[~last.status.str.startswith("ok") & last.status.ne("skipped_size")]
        for st, n in bad.status.value_counts().items():
            L.append(f"- {st}: {n:,}")
        L.append("")
        slow = lg.assign(w=w).sort_values("w", ascending=False).head(10)
        L += ["Slowest documents:", ""] + [f"- {r.w:.0f} s — {r.family} {r.key} "
                                            f"({r.n_pages} pages, {r.ocr_pages} OCR)"
                                            for r in slow.itertuples()] + [""]
    if len(c):
        L += ["## The parse", "",
              f"- Motion sections: {len(s):,} ({int((s.n_conditions > 0).sum()):,} with parsed "
              f"conditions); condition rows {len(c):,}, canonical {int(c.canonical.sum()):,}.",
              "- Parse confidence (canonical rows): " + ", ".join(
                  f"{k} {v:,}" for k, v in c[c.canonical].parse_confidence.value_counts().items()),
              "- Parse method (canonical rows): " + ", ".join(
                  f"{k} {v:,}" for k, v in c[c.canonical].parse_method.value_counts().items()),
              "- Case matched to the reconciled universe: " + ", ".join(
                  f"{k or '(no case read)'} {v:,}" for k, v in s.case_match.value_counts().items()),
              ""]
    for r in range(1, ROUND + 1):
        if score_path(r).exists():
            sc = json.loads(score_path(r).read_text())
            L += [f"## Validation, round {r}" + (" (the rules as first frozen, before revision "
                                                 "against the gold set)" if r < ROUND else
                                                 " (the rules as used)"), "",
                  "```", json.dumps(sc, indent=2), "```", ""]
    L += ["## Recorded Notices of Special Restrictions (1e)", "",
          "See `nsr_feasibility.md` beside this file.", ""]
    (STORE / "task1_summary.md").write_text("\n".join(L) + "\n")
    print(f"→ {STORE / 'task1_summary.md'}")


# ═══════════════════════════════════════════════════════════════════════════
# Task 2: the conditions-content memo's tables, figures and macros
# ═══════════════════════════════════════════════════════════════════════════
MEMO2 = HERE.parents[1] / "output" / "planning_commission_project" / "conditions_content"
FIG2, TAB2 = MEMO2 / "figures", MEMO2 / "tables"
QUOTE_WORDS = 80
TAIL_N = 20
TOP_HEADINGS = 40
KIND_BOILER, KIND_NUMBER, KIND_BESPOKE = 0.80, 0.75, 0.50   # template / quantity shares
DRIFT_SAME, DRIFT_REWRITE = 0.99, 0.60
MIN_MOTIONS_YEAR = 10
CU_TYPES = {"conditional_use", "conditional_use_modification"}
SECTION_CANON = [("performance", "PERFORMANCE"), ("design", "DESIGN"),
                 ("parking", "PARKING AND TRAFFIC"), ("provision", "PROVISIONS"),
                 ("monitor", "MONITORING"), ("operation", "OPERATION"),
                 ("general condition", "GENERAL CONDITIONS"),
                 ("conditions of approval", "CONDITIONS OF APPROVAL (untitled list)"),
                 ("mitigation", "MITIGATION MEASURES"), ("improvement measure", "MITIGATION MEASURES"),
                 ("affordab", "AFFORDABLE HOUSING"), ("inclusionary", "AFFORDABLE HOUSING")]
PREAMBLE_CANON = [("authorization", "AUTHORIZATION"), ("recordation", "RECORDATION"),
                  ("printing", "PRINTING ON PLANS"), ("severab", "SEVERABILITY"),
                  ("changes and modification", "CHANGES AND MODIFICATIONS")]
UNI = {"‘": "`", "’": "'", "“": "``", "”": "''", "–": "--",
       "—": "---", "‐": "-", "‑": "-", "‒": "-", " ": " ",
       "­": "", "•": r"\textbullet{}", "§": r"\S{}", "≥": r"$\geq$",
       "≤": r"$\leq$", "×": r"$\times$", "ﬁ": "fi", "ﬂ": "fl",
       "…": r"\dots{}", "½": "1/2", "¼": "1/4", "°": " degrees",
       "′": "'", "″": "''", "�": "?", "é": r"\'e", "ñ": r"\~n"}


def tt_break(s: str) -> str:
    """Let a monospace path or host break after its slashes and dots, which a table's narrow
    column otherwise cannot."""
    return re.sub(r"\\texttt\{((?:[^{}]|\{\})*)\}",
                  lambda m_: r"\texttt{" + m_.group(1).replace("/", "/\\allowbreak{}")
                  .replace("?", "?\\allowbreak{}").replace(".", ".\\allowbreak{}") + "}", s)


def latex_text(s: str) -> str:
    """Source text made safe for pdflatex: LaTeX specials escaped, the typography the
    documents use mapped to its LaTeX spelling, and anything else outside ASCII dropped
    rather than left to break the build."""
    import unicodedata
    # A table of contents read as text carries dot leaders ("MEASURES ........ 81"); they are
    # never content, and a run of them cannot break across a line.
    t = re.sub(r"(?:\s*\.){4,}\s*", " ... ", str(s))
    t = (t.replace("\\", r"\textbackslash{}").replace("&", r"\&").replace("%", r"\%")
         .replace("$", r"\$").replace("#", r"\#").replace("_", r"\_").replace("{", r"\{")
         .replace("}", r"\}").replace("~", r"\textasciitilde{}").replace("^", r"\^{}")
         .replace("[", "{[}").replace("]", "{]}").replace("<", r"\textless{}")
         .replace(">", r"\textgreater{}").replace("|", r"\textbar{}"))
    for k, v in UNI.items():
        t = t.replace(k, v)
    t = unicodedata.normalize("NFKD", t)
    return t.encode("ascii", "ignore").decode()


def quote(body: str, n: int = QUOTE_WORDS) -> str:
    w = str(body).split()
    return latex_text(" ".join(w[:n])) + (r"~\dots" if len(w) > n else "")


def norm_heading(h: str) -> str:
    h = re.sub(r"\s*[-–—]\s*wts\b", " WTS", str(h), flags=re.I)
    return re.sub(r"[^a-z0-9]+", " ", h.lower()).strip()


def canon(h: str, table) -> str:
    n = norm_heading(h)
    return next((lab for pre, lab in table if n.startswith(pre) or pre in n), "")


def mask(t: str) -> str:
    """Body text with the project-specific parts taken out --- numbers, dates, addresses ---
    so two conditions drawn from one template compare equal."""
    t = str(t).lower()
    t = re.sub(r"\d[\d,./:-]*", "#", t)
    t = re.sub(r"\b(one|two|three|four|five|six|seven|eight|nine|ten|twelve|eighteen|"
               r"twenty|thirty|forty|sixty)\b", "#", t)
    t = re.sub(r"[^a-z#]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def sim(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


DUR = re.compile(r"(?i)\b(?:(one|two|three|four|five|six|ten|twelve|eighteen|twenty[- ]four|"
                 r"thirty[- ]six)\s*)?\(?\s*(\d{1,3})?\s*\)?\s*(years?|months?)\b")
WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "ten": 10,
           "twelve": 12, "eighteen": 18, "twenty four": 24, "twenty-four": 24,
           "thirty six": 36, "thirty-six": 36}


def durations(t: str) -> list[int]:
    """Every stated period in months: "three (3) years", "36 months", "ten (10) years"."""
    out = []
    for m in DUR.finditer(str(t)):
        w, d, unit = m.group(1), m.group(2), m.group(3).lower()
        n = int(d) if d else WORDNUM.get((w or "").lower().replace("-", " "), None)
        if not n:
            continue
        out.append(n * (12 if unit.startswith("year") else 1))
    return out


OPTION_MIN = 5              # motions a year needs for its own row in the clock table
OPTION_KIND = [("validity", re.compile(r"(?i)\bvalidity\b|\bvalid for\b|deemed void|void and "
                                       r"cancel|\bperformance\b")),
               ("diligent pursuit", re.compile(r"(?i)diligent(ly)? pursu")),
               ("expiration and renewal", re.compile(r"(?i)expiration|renewal")),
               ("extension", re.compile(r"(?i)\bextension\b|may be extended"))]


def option_kind(heading: str, body: str) -> str:
    h = str(heading)
    for k, rx in OPTION_KIND:
        if h and rx.search(h):
            return k
    if not h:
        for k, rx in OPTION_KIND:
            if rx.search(str(body)[:400]):
                return k
    return ""


def content_frames():
    """The item table, the canonical motion sections with conditions, and their condition
    rows, with the derived columns every Task 2 table uses."""
    import acquire_external_data as ax
    it = load_items()
    it["flag"] = it.conditions_imposed.astype(str).str.strip().str.lower().eq("yes")
    it["cond_text"] = ax.conditions_reach(it)
    s = section_frame()
    c = pd.read_parquet(COND_LONG.with_suffix(".parquet"))
    c = c[c.canonical].copy()
    c["year"] = pd.to_numeric(c.hearing_date.str[:4].where(c.hearing_date.ne(""),
                                                           c.adoption_date.str[:4]),
                              errors="coerce")
    c["strict"] = c.part.eq("conditions") & ~c.implicit
    c["hnorm"] = c.heading.map(norm_heading)
    c["section_canon"] = c.section_heading.map(lambda h: canon(h, SECTION_CANON) or
                                               (norm_heading(h).upper() if h else ""))
    c["pre_canon"] = c.heading.map(lambda h: canon(h, PREAMBLE_CANON))
    c["skey"] = c.doc_id + "|" + c.section.astype(str)
    rt = (it[it.cn.ne("")].groupby("cn").request_type
          .agg(lambda x: x.value_counts().index[0] if len(x) else ""))
    cu_cases = set(it.loc[it.request_type.isin(CU_TYPES) & it.cn.ne(""), "cn"])
    s_c = s[s.canonical & s.n_conditions.gt(0)].copy()
    s_c["skey"] = s_c.doc_id + "|" + s_c.section
    s_c["cu"] = s_c.case_key.isin(cu_cases)
    s_c["rtype"] = s_c.case_key.map(rt).fillna("")
    c = c.merge(s_c[["skey", "cu", "rtype", "source"]], on="skey", how="left")
    return it, s, s_c, c, cu_cases


def diff_stage():
    """1d: every case with a draft motion and its adopted motion, compared condition by
    condition. Written beside the census; the memo's tables are drawn from the same call."""
    it, _, s_c, c, _ = content_frames()
    dv = draft_vs_adopted(c, s_c, it)
    dv["pairs"].to_csv(STORE / "draft_vs_adopted.csv", index=False)
    dv["added_all"].to_csv(STORE / "draft_vs_adopted_added.csv", index=False)
    p = dv["pairs"]
    if not len(p):
        print("no draft--adopted pairs yet")
        return
    print(f"{len(p):,} draft--adopted pairs; {int(p.any_change.sum()):,} with any change; "
          f"conditions added {int(p.added.sum()):,}, removed {int(p.removed.sum()):,}, "
          f"changed {int(p.changed.sum()):,}, unchanged {int(p.unchanged.sum()):,}")
    print(pd.crosstab(p.mods.fillna("no item at that hearing"), p.any_change).to_string())


def report_content():
    """Everything the conditions-content memo reports, from conditions_long, the section
    table, the census and the item table. Writes tables/conditions_content_{tables,macros}.tex
    and the figures."""
    import acquire_external_data as ax
    import analyze_conditions as acond
    FIG2.mkdir(parents=True, exist_ok=True)
    TAB2.mkdir(parents=True, exist_ok=True)
    it, s, s_c, c, cu_cases = content_frames()
    universe = case_universe(it)
    M = {}
    L = ["% GENERATED BY collect_conditions.py report --- do not edit by hand."]
    a = L.append

    # ── 1. coverage ─────────────────────────────────────────────────────────
    hits = census_years(it)
    hits["era"] = hits.year.map(era_of)
    eras = [e for *_, e in ERAS]
    # the families, and what each holds
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The URL families probed. `Constructed' families were probed for every case or "
      r"motion number the item table records; the others are exact URLs found in the minutes' "
      r"own hyperlinks, on the hearing pages, on the archived agenda pages or in the Wayback "
      r"Machine's index. Column labels are the ones Table~\ref{tab:census} uses.}"
      r"\label{tab:families}")
    a(r"{\small\begin{tabular}{@{}llL{9.4cm}@{}}\toprule")
    a(r"Label & Holds & Where\\\midrule")
    seen_lab = set()
    for fam, lab, holds, where in CENSUS_COLS:
        if not where:
            continue
        where = tt_break(where)
        a(rf"{lab if lab not in seen_lab else ''} & {holds if lab not in seen_lab else ''} & "
          rf"{where}\\")
        seen_lab.add(lab)
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")
    hits["col"] = hits.family.map({f: lab for f, lab, *_ in CENSUS_COLS}).fillna("other")
    cols = [lab for _, lab, *_ in CENSUS_COLS]
    cols = [c_ for c_ in dict.fromkeys(cols) if (hits.col == c_).any()]
    yrs = sorted(hits.year.dropna().astype(int).unique())
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The census: URLs answering with a document, by the first hearing year of the "
      r"case or motion each belongs to and by URL family (Table~\ref{tab:families}). A document "
      r"reached at two addresses --- a mirror --- is counted at each. `A' columns are adopted "
      r"motions, `P' columns packets (draft motions), `DRA' Discretionary Review Actions, which "
      r"are counted and not pulled.}\label{tab:census}")
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{l" + "r" * (len(cols) + 1) + r"}\toprule")
    a(r"Year & " + " & ".join(cols) + r" & All\\\midrule")
    for y_ in yrs:
        g = hits[hits.year.eq(y_)]
        vc = g.col.value_counts()
        a(rf"{y_} & " + " & ".join(f0(vc.get(c_, 0)) if vc.get(c_, 0) else "" for c_ in cols) +
          rf" & {f0(len(g))}\\")
    g = hits[hits.year.isna()]
    if len(g):
        vc = g.col.value_counts()
        a(r"undated & " + " & ".join(f0(vc.get(c_, 0)) if vc.get(c_, 0) else "" for c_ in cols) +
          rf" & {f0(len(g))}\\")
    vc = hits.col.value_counts()
    a(r"\midrule All & " + " & ".join(f0(vc.get(c_, 0)) for c_ in cols) + rf" & {f0(len(hits))}\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")
    # 1a.2: what the Wayback Machine holds, by the year it was captured
    cdx = cdx_yield()
    if len(cdx):
        kinds = [k for k in CDX_KINDS if k in cdx.columns]
        a(r"\begin{table}[htbp]\centering")
        a(r"\caption{The Wayback Machine's yield: distinct document URLs captured with status "
          r"200 under the %s CDX prefixes, by the year of first capture and by kind. Adopted "
          r"motions are HTML pages (pre-2003 city site) or PDFs named by motion number; packets "
          r"are PDFs named by case. Everything else captured under the prefixes --- agendas, "
          r"notices, reports, and the old site's \texttt{ShowDocument} pages, whose URLs name "
          r"no motion or case --- is in `other'. A motion or packet capture's live equivalent is probed; a capture is pulled "
          r"only where the live copy is gone.}\label{tab:cdx}" % N(len(CDX_PREFIXES)))
        a(r"\begin{tabular}{l" + "r" * (len(kinds) + 1) + r"}\toprule")
        a(r"First captured & " + " & ".join(kinds) + r" & All\\\midrule")
        for y_, r_ in cdx.iterrows():
            a(rf"{y_} & " + " & ".join(f0(r_[k]) for k in kinds) + rf" & {f0(r_[kinds].sum())}\\")
        a(r"\midrule All & " + " & ".join(f0(cdx[k].sum()) for k in kinds) +
          rf" & {f0(cdx[kinds].values.sum())}\\")
        a(r"\bottomrule\end{tabular}\end{table}")
        a("")
        M["ccCdxMotionsHtml"] = N(cdx.get("motion (HTML)", pd.Series(dtype=int)).sum())
        M["ccCdxMotionsPdf"] = N(cdx.get("motion (PDF)", pd.Series(dtype=int)).sum())
        M["ccCdxPackets"] = N(cdx.get("packet (PDF)", pd.Series(dtype=int)).sum())
        firsts = {k: cdx.index[cdx[k] > 0].min() for k in kinds if (cdx[k] > 0).any()}
        M["ccCdxFirstMotion"] = str(min(v for k, v in firsts.items() if k.startswith("motion"))) \
            if any(k.startswith("motion") for k in firsts) else "---"
        M["ccCdxFirstPacket"] = str(firsts.get("packet (PDF)", "---"))

    # the earliest hearing year each family reaches
    first_fam = hits.groupby("family").year.min()
    for fam, lab, *_ in CENSUS_COLS:
        if fam in first_fam and pd.notna(first_fam[fam]):
            M[f"ccFirst{re.sub(r'[^A-Za-z]', '', fam.title())}"] = str(int(first_fam[fam]))

    s_c["era"] = s_c.year.map(era_of)
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Condition text actually read: motions (adopted) and draft motions (in "
      r"packets) with at least one parsed condition, by source and hearing year. One copy of "
      r"each motion is counted, whichever source reached it first in the canonical "
      r"order.}\label{tab:coverage}")
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{ll" + "r" * (len(eras) + 2) + r"}\toprule")
    a(r"Type & Source & " + " & ".join(eras) + r" & undated & Earliest\\\midrule")
    for dtp, lab in (("adopted_motion", "adopted"), ("draft_packet", "draft")):
        g0 = s_c[s_c.doc_type.eq(dtp)]
        first = True
        for src, g in g0.groupby("source"):
            vc = g.era.value_counts()
            nw = int(g.wayback.sum())
            lab_src = SOURCE_LABEL.get(src, src)
            if nw and "Wayback" not in lab_src:
                lab_src += " (Wayback)" if nw == len(g) else f" ({nw} via Wayback)"
            a(rf"{lab if first else ''} & {latex_text(lab_src)} & " +
              " & ".join(f0(vc.get(e, 0)) for e in eras) +
              rf" & {f0(vc.get('undated', 0))} & "
              rf"{int(g.year.min()) if g.year.notna().any() else '---'}\\")
            first = False
        a(r"\midrule")
    vc = s_c.era.value_counts()
    a(r"All & & " + " & ".join(f0(vc.get(e, 0)) for e in eras) +
      rf" & {f0(vc.get('undated', 0))} & {int(s_c.year.min())}\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")

    cond = it[it.flag]
    by = cond.groupby(cond.year.map(era_of))
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{How much of the conditioned docket now has condition text. `Conditioned' is "
      r"the item table's \texttt{conditions\_imposed} flag. An item has condition text when a "
      r"parsed motion section with at least one condition is keyed to its case, or is the "
      r"adopted motion whose number the item records.}\label{tab:reach}")
    a(r"\begin{tabular}{lrrrr}\toprule")
    a(r"Hearing years & \shortstack[r]{Conditioned\\items} & \shortstack[r]{With condition\\text} & "
      r"Share & \shortstack[r]{Motion number,\\no text}\\\midrule")
    num = pd.to_numeric(it.action_instrument_no, errors="coerce").fillna(0) > 0
    for e in eras + ["undated"]:
        if e not in by.groups:
            continue
        g = by.get_group(e)
        a(rf"{e} & {f0(len(g))} & {f0(g.cond_text.sum())} & {100*g.cond_text.mean():.1f}\% & "
          rf"{f0((~g.cond_text & num[g.index]).sum())}\\")
    a(rf"\midrule All & {f0(len(cond))} & {f0(cond.cond_text.sum())} & "
      rf"{100*cond.cond_text.mean():.1f}\% & {f0((~cond.cond_text & num[cond.index]).sum())}\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")
    # The residual, named: why a conditioned item has no text. A census hit is keyed to the
    # item's case or its motion number; "read" means the pull opened the document.
    res = residual(it, cond)
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The residual: conditioned items with no condition text, by why. `Found' "
      r"means some probed or discovered URL keyed to the item's case or motion number answered "
      r"with a document; `read', that the pull opened it. A document found and read that yields "
      r"no conditions is usually a staff report or a Discretionary Review Action rather than a "
      r"motion; one found and not read is over the size cap, failed, or a DRA, which is not "
      r"pulled.}\label{tab:residual}")
    a(r"\begin{tabular}{lrrrrr}\toprule")
    a(r"Hearing years & No text & \shortstack[r]{Found,\\read} & \shortstack[r]{Found,\\not read} & "
      r"\shortstack[r]{None found,\\number} & \shortstack[r]{None found,\\no number}\\\midrule")
    for e in eras + ["undated"]:
        g = res[res.era.eq(e)]
        if not len(g):
            continue
        vc = g.why.value_counts()
        a(rf"{e} & {f0(len(g))} & " + " & ".join(f0(vc.get(k, 0)) for k in RESIDUAL) + r"\\")
    vc = res.why.value_counts()
    a(rf"\midrule All & {f0(len(res))} & " + " & ".join(f0(vc.get(k, 0)) for k in RESIDUAL) +
      r"\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")
    for k, mac in zip(RESIDUAL, ("ccResFoundRead", "ccResFoundUnread", "ccResNoneNumber",
                                 "ccResNoneNoNumber")):
        M[mac] = N(vc.get(k, 0))
    early = res[res.year < 2010]
    M["ccResPreTen"] = N(len(early))
    M["ccResPreTenNone"] = N(early.why.isin(RESIDUAL[2:]).sum())

    # pitfall 5: what the sections say beside what was parsed
    sa = s[s.canonical].copy()
    sa["era"] = sa.year.map(era_of)
    for col in ("mentions_exhibit_a", "has_exhibit_a"):
        sa[col] = sa[col].eq("True")
    sa["rejected"] = sa.exhibit_a_not_conditions.ne("") if "exhibit_a_not_conditions" in sa \
        else False
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Mention rate beside parse rate, over every motion section read (one copy of "
      r"each). A section that mentions an Exhibit A but has none located is either a motion "
      r"that attaches no conditions and says so, one whose Exhibit A is another kind of "
      r"attachment (rejected: a draft resolution, an agreement, a mitigation table), or a "
      r"parser miss; the last is what the validation measures. `Located' is an Exhibit A heading "
      r"found and accepted as conditions; every line after it becomes at least one row, so a "
      r"located Exhibit A yields a parsed condition in all but %s sections, and the count of "
      r"sections with a condition is not shown as a column of its own.}\label{tab:mention}"
      % N(int((sa.has_exhibit_a & sa.n_conditions.eq(0)).sum())))
    a(r"\begin{tabular}{lrrrr}\toprule")
    a(r"Hearing years & Sections & \shortstack[r]{Mention\\Exhibit A} & \shortstack[r]{Exhibit A\\rejected} & "
      r"\shortstack[r]{Exhibit A\\located}\\\midrule")
    for e in eras + ["undated"]:
        g = sa[sa.era.eq(e)]
        if not len(g):
            continue
        a(rf"{e} & {f0(len(g))} & {f0(g.mentions_exhibit_a.sum())} & {f0(g.rejected.sum())} & "
          rf"{f0(g.has_exhibit_a.sum())}\\")
    a(rf"\midrule All & {f0(len(sa))} & {f0(sa.mentions_exhibit_a.sum())} & "
      rf"{f0(sa.rejected.sum())} & {f0(sa.has_exhibit_a.sum())}\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")
    loc = sa[sa.has_exhibit_a]
    M.update({"ccSecRead": N(len(sa)), "ccSecMention": N(sa.mentions_exhibit_a.sum()),
              "ccSecRejected": N(sa.rejected.sum()), "ccSecLocated": N(len(loc)),
              "ccSecLocatedNone": N(loc.n_conditions.eq(0).sum()),
              "ccSecMentionNotLocated": N((sa.mentions_exhibit_a & ~sa.has_exhibit_a &
                                           ~sa.rejected).sum())})
    earliest = s_c.groupby("source").year.min()
    M.update({
        "ccUniverse": N(len(universe)), "ccCondItems": N(len(cond)),
        "ccCondWithText": N(cond.cond_text.sum()),
        "ccCondWithTextPct": f"{100*cond.cond_text.mean():.1f}",
        "ccCondNoText": N((~cond.cond_text).sum()),
        "ccCondNoTextWithNumber": N((~cond.cond_text & num[cond.index]).sum()),
        "ccCondNoTextNoNumber": N((~cond.cond_text & ~num[cond.index]).sum()),
        "ccHits": N(len(hits)), "ccCandidates": N(len(load_census())),
        "ccSectionsWithText": N(len(s_c)),
        "ccAdoptedWithText": N(s_c.doc_type.eq("adopted_motion").sum()),
        "ccDraftWithText": N(s_c.doc_type.eq("draft_packet").sum()),
        "ccConditionRows": N(len(c)), "ccStrictRows": N(c.strict.sum()),
        "ccEarliestAny": str(int(s_c.year.min())),
        # A motion embedded in a later packet can predate the corpus (a 1989 motion attached
        # to a 1999 one); the earliest year that answers for an item is the one keyed to it.
        "ccEarliestInUniverse": str(int(s_c[s_c.case_key.ne("")].year.min())),
        # ... and the question the corpus asks: the earliest hearing, in the item table, of a
        # conditioned item that now has text (a motion read may be older than its item)
        "ccEarliestItemYear": str(int(cond[cond.cond_text].year.min())),
        "ccSectionsOutsideUniverse": N(s_c.case_key.eq("").sum()),
        "ccEarliestAdopted": str(int(s_c[s_c.doc_type.eq("adopted_motion")].year.min())),
        "ccEarliestDraft": str(int(s_c[s_c.doc_type.eq("draft_packet")].year.min()))
        if s_c.doc_type.eq("draft_packet").any() else "---",
        "ccEarliestEmbedded": str(int(earliest.get("embedded"))) if "embedded" in earliest
        and pd.notna(earliest.get("embedded")) else "---",
        "ccEarliestVault": str(int(earliest.get("minutes_vault", np.nan)))
        if pd.notna(earliest.get("minutes_vault", np.nan)) else "---",
        "ccEarliestModules": str(int(earliest.get("oldsite_modules", np.nan)))
        if pd.notna(earliest.get("oldsite_modules", np.nan)) else "---",
        "ccEarliestFtp": str(int(earliest.get("cpcmotions_ftp", np.nan)))
        if pd.notna(earliest.get("cpcmotions_ftp", np.nan)) else "---",
        "ccEarliestWayback": str(int(s_c[s_c.wayback].year.min())) if s_c.wayback.any()
        and s_c[s_c.wayback].year.notna().any() else "---",
        "ccEarliestPackets": str(int(s_c[s_c.source.isin(["cpcpackets", "cpcpackets_ftp",
                                                          "cpcpackets_mirror", "citypln"])
                                     & s_c.doc_type.eq("draft_packet")].year.min()))
        if (s_c.source.isin(["cpcpackets", "cpcpackets_ftp", "cpcpackets_mirror", "citypln"])
            & s_c.doc_type.eq("draft_packet")).any() else "---",
    })
    for e in eras:
        g = cond[cond.year.map(era_of).eq(e)]
        M[f"ccReach{ERA_NAME[e]}"] = f"{100*g.cond_text.mean():.1f}" if len(g) else "---"
    # the universe, stated where it is used: printed strings against distinct cases
    printed = it.loc[it.cn_raw.ne(""), "cn_raw"].nunique()
    M.update({"ccCasesPrinted": N(printed), "ccCaseMerges": N(printed - len(universe))})
    M.update(discovery_macros())
    old = STORE / "availability.csv"            # the conditions memo's sample, for contrast
    if old.exists():
        o = pd.read_csv(old, dtype=str).fillna("")
        M.update({"ccOldPerYear": N(o.groupby("year").size().max()), "ccOldProbed": N(len(o)),
                  "ccOldFound": N(o.found.eq("1").sum())})
    M.update({"ccGoldN": str(GOLD_N), "ccCheckN": str(CHECK_N), "ccRatePerHost": f"{RATE:g}",
              "ccPreambleBlocks": str(len({lab for _, lab in PREAMBLE_CANON})),
              "ccTaxCategories": str(len(acond.TAXONOMY))})

    # ── 2. anatomy of one motion ────────────────────────────────────────────
    anat = anatomy(c, s_c, cu_cases)
    if anat:
        a(r"\begin{table}[htbp]\centering")
        a(r"\caption{The anatomy of one conditional-use motion, Motion No.~%s (case "
          r"\texttt{%s}, adopted %s), page by page. The document is chosen by rule: among the "
          r"born-digital adopted conditional-use motions on the motion archive written in the "
          r"modern template, the one whose condition count is closest to their median, ties "
          r"broken by document identifier.}\label{tab:anatomy}"
          % (anat["motion"], latex_text(anat["case"]), anat["adopted"]))
        a(r"\begin{tabular}{@{}L{4.6cm}rL{8.6cm}@{}}\toprule")
        a(r"Part & Pages & What it is\\\midrule")
        for part, pages, what in anat["rows"]:
            a(rf"{part} & {pages} & {what}\\")
        a(r"\bottomrule\end{tabular}\end{table}")
        a("")
        M.update({"ccAnatMotion": anat["motion"], "ccAnatCase": latex_text(anat["case"]),
                  "ccAnatAdopted": anat["adopted"], "ccAnatPages": str(anat["pages"]),
                  "ccAnatConditions": str(anat["n"]), "ccAnatUrl": anat["url"]})

    # ── 3. standing sections and the top headings ───────────────────────────
    strict = c[c.strict]
    n_cu_motions = s_c.cu.sum()
    sect_rows = []
    for sec_name, g in strict[strict.section_canon.ne("")].groupby("section_canon"):
        mot = g.skey.nunique()
        if mot < 5:
            continue
        sect_rows.append((sec_name, g, mot))
    sect_rows.sort(key=lambda r: -r[2])
    pre = c[c.part.eq("preamble") & c.pre_canon.ne("")]
    a(r"{\small\begin{longtable}{@{}L{5.2cm}rrrrl@{}}")
    a(r"\caption{The standing blocks of Exhibit A. Preamble blocks open it; the sections group "
      r"the numbered conditions. `Motions' counts motion sections carrying the block; `CU share' "
      r"is the share of conditional-use motions with conditions that carry it; `Chars' the "
      r"median length of a condition's body under it.}\label{tab:sections}\\\toprule")
    hdr = r"Block & Motions & CU share & Chars & Conditions & Years\\\midrule"
    a(hdr + r"\endfirsthead")
    a(r"\toprule " + hdr + r"\endhead")
    a(r"\multicolumn{6}{@{}l}{\emph{Preamble blocks}}\\")
    for name, g in pre.groupby("pre_canon"):
        mot = g.skey.nunique()
        cu = g[g.cu.fillna(False)].skey.nunique()
        a(rf"{latex_text(name)} & {f0(mot)} & {100*cu/max(n_cu_motions, 1):.0f}\% & "
          rf"{f0(g.n_chars.median())} & {f0(len(g))} & {yr_range(g.year)}\\")
    a(r"\multicolumn{6}{@{}l}{\emph{Condition sections}}\\")
    for name, g, mot in sect_rows:
        cu = g[g.cu.fillna(False)].skey.nunique()
        a(rf"{latex_text(name)} & {f0(mot)} & {100*cu/max(n_cu_motions, 1):.0f}\% & "
          rf"{f0(g.n_chars.median())} & {f0(len(g))} & {yr_range(g.year)}\\")
    a(r"\bottomrule\end{longtable}}")
    a("")

    headed = strict[strict.hnorm.ne("")]
    counts = headed.groupby("hnorm").skey.nunique().sort_values(ascending=False)
    top = counts.head(TOP_HEADINGS)
    a(r"{\footnotesize\begin{longtable}{@{}L{3.7cm}rrrrrlL{2.7cm}@{}}")
    a(r"\caption{The %d most frequent condition headings, by the number of motions "
      r"carrying them. `CU' is the share of "
      r"conditional-use motions carrying it; `chars' the median body length; `template' the "
      r"share of its bodies identical to its most common body once numbers are masked --- the "
      r"part that never varies; `quantity' the share stating a number with a unit; `what "
      r"varies' the commonest stated quantities.}\label{tab:headings}\\\toprule"
      % TOP_HEADINGS)
    hdr = (r"Heading & Motions & CU & Chars & Template & Quantity & Years & "
           r"What varies\\\midrule")
    a(hdr + r"\endfirsthead")
    a(r"\toprule " + hdr + r"\endhead")
    head_stats = {}
    for h, mot in top.items():
        g = headed[headed.hnorm.eq(h)]
        mk = g.body.map(mask)
        modal = mk.value_counts().index[0]
        tshare = (mk == modal).mean()
        qn = g.has_numeric_quantity.astype(bool)
        quant = Counter(q for b in g.body for q in dict.fromkeys(quantities(b)))
        cu = g[g.cu.fillna(False)].skey.nunique()
        label = g.heading.value_counts().index[0]
        head_stats[h] = {"label": label, "g": g, "modal": modal, "template": tshare,
                         "quantity": qn.mean()}
        a(rf"{latex_text(label)} & {f0(mot)} & {100*cu/max(n_cu_motions, 1):.0f}\% & "
          rf"{f0(g.n_chars.median())} & {100*tshare:.0f}\% & {100*qn.mean():.0f}\% & "
          rf"{yr_range(g.year)} & "
          rf"{latex_text('; '.join(q for q, _ in quant.most_common(3))) or '---'}\\")
    a(r"\bottomrule\end{longtable}}")
    a("")
    kinds = {"boiler": [], "number": [], "bespoke": []}
    for h in top.index:
        st_ = head_stats[h]
        if st_["template"] >= KIND_BOILER:
            kinds["boiler"].append(st_["label"])
        elif st_["quantity"] >= KIND_NUMBER:
            kinds["number"].append(st_["label"])
        elif st_["template"] < KIND_BESPOKE:
            kinds["bespoke"].append(st_["label"])
    for k_, mac in (("boiler", "ccKindBoiler"), ("number", "ccKindNumber"),
                    ("bespoke", "ccKindBespoke")):
        M[mac] = latex_text("; ".join(kinds[k_])) or "none"
        M[mac + "N"] = str(len(kinds[k_]))
    M.update({"ccKindBoilerMin": f"{100*KIND_BOILER:.0f}", "ccKindNumberMin":
              f"{100*KIND_NUMBER:.0f}", "ccKindBespokeMax": f"{100*KIND_BESPOKE:.0f}"})

    # the verbatim examples: an early one, a typical one, the most unusual one
    a(r"{\small\begin{longtable}{@{}L{3.7cm}L{11.8cm}@{}}")
    a(r"\caption{Verbatim examples, three to a block or heading where they differ: the "
      r"earliest body, a typical one (the most common masked form, at median length), and "
      r"the one least like the rest. Bodies are cut at %d words; the reference gives the page "
      r"of the source document and links to it.}\label{tab:examples}\\\toprule" % QUOTE_WORDS)
    a(r"Block or heading & Example\\\midrule\endfirsthead")
    a(r"\toprule Block or heading & Example\\\midrule\endhead")
    groups = [(n, g) for n, g in pre.groupby("pre_canon")] + \
             [(n, g) for n, g, _ in sect_rows] + \
             [(head_stats[h]["label"], head_stats[h]["g"]) for h in top.index]
    for name, g in groups:
        ex = examples(g)
        first = True
        for kind, r in ex:
            a(rf"{latex_text(name) if first else ''} & \emph{{{kind}, {int(r.year) if pd.notna(r.year) else 'undated'}, "
              rf"p.~{r.page_start}, \href{{{url_tex(r.doc_url)}}}{{source}}:}} "
              rf"{quote(r.body)}\\[3pt]")
            first = False
    a(r"\bottomrule\end{longtable}}")
    a("")

    # drift: the most common body of a heading, era by era
    drift = []
    for h in top.index:
        g = head_stats[h]["g"]
        modal_by = {}
        for e, ge in g.groupby(g.year.map(era_of)):
            if len(ge) >= 5:
                modal_by[e] = ge.body.map(mask).value_counts().index[0]
        es = [e for e in eras if e in modal_by]
        if len(es) >= 2:
            sims = [sim(modal_by[x], modal_by[y]) for x, y in zip(es, es[1:])]
            drift.append((head_stats[h]["label"], es[0], es[-1], min(sims),
                          sim(modal_by[es[0]], modal_by[es[-1]])))
    drift.sort(key=lambda r: r[4])
    M.update({"ccDriftN": str(len(drift)),
              "ccDriftSame": str(sum(1 for r in drift if r[4] >= DRIFT_SAME)),
              "ccDriftRewritten": latex_text("; ".join(r[0] for r in drift if r[4] < DRIFT_REWRITE))
              or "none", "ccDriftRewrittenN": str(sum(1 for r in drift if r[4] < DRIFT_REWRITE)),
              "ccDriftRewriteMax": f"{DRIFT_REWRITE:.2f}",
              "ccDriftSameMin": f"{DRIFT_SAME:.2f}"})
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Wording drift. For each frequent heading with at least five bodies in two or "
      r"more eras, the similarity (0--1, on number-masked text) of its most common body in the "
      r"first and last era it appears in, and the lowest similarity between adjacent eras. "
      r"1 means the template did not change.}\label{tab:drift}")
    a(r"\begin{tabular}{@{}L{6.5cm}llrr@{}}\toprule")
    a(r"Heading & First era & Last era & First vs last & Lowest adjacent\\\midrule")
    for lab, e0, e1, mn, fl in drift:
        a(rf"{latex_text(lab)} & {e0} & {e1} & {fl:.2f} & {mn:.2f}\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    # ── 4. the long tail ────────────────────────────────────────────────────
    once = counts[counts.eq(1)].index
    tail = headed[headed.hnorm.isin(once)].sample(min(TAIL_N, len(once)), random_state=SEED) \
        if len(once) else headed.iloc[:0]
    a(r"{\small\begin{longtable}{@{}L{3.3cm}L{2.2cm}L{8.7cm}@{}}")
    a(r"\caption{The long tail: %d conditions whose heading appears in no other motion, drawn "
      r"at random (seed fixed), with the kind of case they belong to.}\label{tab:tail}\\\toprule"
      % len(tail))
    a(r"Heading & Case type & Body\\\midrule\endfirsthead")
    a(r"\toprule Heading & Case type & Body\\\midrule\endhead")
    for r in tail.sort_values("year").itertuples():
        a(rf"{latex_text(r.heading)} & {latex_text(str(r.rtype).replace('_', ' ') or 'not in the item table')} & "
          rf"{quote(r.body)} \emph{{({int(r.year) if pd.notna(r.year) else 'undated'}, "
          rf"p.~{r.page_start}, \href{{{url_tex(r.doc_url)}}}{{source}})}}\\[3pt]")
    a(r"\bottomrule\end{longtable}}")
    a("")
    M.update({"ccTopHeadings": str(len(top)), "ccHeadingsDistinct": N(len(counts)),
              "ccHeadingsOnce": N(len(once)),
              "ccHeadingsTopShare": f"{100*headed.hnorm.isin(top.index).mean():.1f}",
              "ccTailN": str(len(tail)), "ccHeadedRows": N(len(headed)),
              "ccUnheadedRows": N(len(strict) - len(headed))})

    # ── 5. counts as a variable ─────────────────────────────────────────────
    per = strict.groupby("skey").size().rename("n").to_frame().join(
        s_c.set_index("skey")[["doc_type", "year", "rtype", "case_key", "cu"]], how="left")
    units = ax.units_from_records(it)
    ucase = units[units.cn.ne("")].groupby("cn").units_prj.max()
    per["units"] = per.case_key.map(ucase)
    per["ubin"] = pd.cut(per.units, [-1, 0, 1, 9, 49, 10**9],
                         labels=["0", "1", "2--9", "10--49", "50+"])
    share_year = it.groupby("year").flag.mean()
    ad = per[per.doc_type.eq("adopted_motion")]
    yr = ad.groupby("year").n.agg(["size", "median", lambda x: x.quantile(.25),
                                   lambda x: x.quantile(.75)])
    yr.columns = ["motions", "median", "p25", "p75"]
    yr = yr[yr.motions >= MIN_MOTIONS_YEAR]
    both = yr.join(share_year.rename("share"), how="inner")
    rho = both["median"].corr(both["share"], method="spearman") if len(both) >= 3 else np.nan
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Conditions per motion (numbered or named conditions only, standing preamble "
      r"blocks excluded), adopted motions by hearing year (years with at least %d motions), "
      r"beside the share of items the item table flags as conditioned.}\label{tab:peryear}"
      % MIN_MOTIONS_YEAR)
    a(r"\begin{tabular}{lrrrrr}\toprule")
    a(r"Year & Motions & Median & p25 & p75 & Items conditioned\\\midrule")
    for y_, r in both.iterrows():
        a(rf"{int(y_)} & {f0(r.motions)} & {r['median']:.0f} & {r.p25:.0f} & {r.p75:.0f} & "
          rf"{100*r.share:.1f}\%\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Conditions per motion by the case's request type and by project scale "
      r"(proposed units from the Planning Projects table, reached through the case "
      r"stem). Adopted and draft motions pooled; one row per motion section.}"
      r"\label{tab:perkind}")
    a(r"\begin{tabular}{llrrr}\toprule")
    a(r"By & Group & Motions & Median & Mean\\\midrule")
    for rtp, g in per.groupby(per.rtype.replace("", "(not in the item table)")):
        if len(g) >= 10:
            a(rf"request type & {latex_text(rtp.replace('_', ' '))} & {f0(len(g))} & "
              rf"{g.n.median():.0f} & {g.n.mean():.1f}\\")
    a(r"\midrule")
    for ub, g in per.groupby("ubin", observed=True):
        a(rf"proposed units & {ub} & {f0(len(g))} & {g.n.median():.0f} & {g.n.mean():.1f}\\")
    g = per[per.units.isna()]
    a(rf"proposed units & unknown & {f0(len(g))} & {g.n.median():.0f} & {g.n.mean():.1f}\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")
    fig_counts(both)
    M["ccCountVerdict"] = ("is not rising with" if pd.isna(rho) or abs(rho) < 0.3 else
                           "rises with" if rho > 0 else "falls as")
    ub = per.groupby("ubin", observed=True).n.median()
    M.update({"ccPerUnitsNoneMed": f"{ub.get('0', np.nan):.0f}",
              "ccPerUnitsLargeMed": f"{ub.get('50+', np.nan):.0f}",
              "ccPerUnitsLargeN": N((per.ubin == "50+").sum())})
    M.update({"ccPerMotionMedian": f"{ad.n.median():.0f}", "ccPerMotionMean": f"{ad.n.mean():.1f}",
              "ccPerMotionYears": str(len(both)),
              "ccPerMotionRho": f"{rho:.2f}" if pd.notna(rho) else "---",
              "ccPerMotionFirstYear": str(int(both.index.min())) if len(both) else "---",
              "ccPerMotionLastYear": str(int(both.index.max())) if len(both) else "---",
              "ccPerMotionFirstMed": f"{both['median'].iloc[0]:.0f}" if len(both) else "---",
              "ccPerMotionLastMed": f"{both['median'].iloc[-1]:.0f}" if len(both) else "---",
              "ccMinMotionsYear": str(MIN_MOTIONS_YEAR)})

    # ── 6. draft against adopted ────────────────────────────────────────────
    dv = draft_vs_adopted(c, s_c, it)
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Draft against adopted, for every case with both a draft motion and the "
      r"adopted motion. Conditions are matched by heading where both carry headings (allowing "
      r"for OCR in the adopted copy), and what is left by text. `Reworded' is a matched condition "
      r"whose number-masked text is less than %.0f\%% similar to the draft's; `near-identical' "
      r"differs by less --- OCR, typography, a touched-up word --- and is not counted as a "
      r"change. An added condition is `template text' when its opening %d characters, numbers "
      r"masked, recur in the motions of at least %d other cases. The lower panel crosses the "
      r"change with the item table's record of the hearing, counting any change (reworded, "
      r"removed or added) and a change beyond template additions.}\label{tab:diff}"
      % (100 * DIFF_SAME, TEMPLATE_CHARS, TEMPLATE_MIN_CASES))
    a(r"\begin{tabular}{lrr}\toprule")
    a(r"Quantity & Value & \\\midrule")
    d_ = dv["pairs"]
    if len(d_):
        for lab, v in (("Draft--adopted pairs", f0(len(d_))),
                       ("\\quad with any change", f"{f0(d_.any_change.sum())} "
                        f"({100*d_.any_change.mean():.0f}\\%)"),
                       ("Conditions in the drafts", f0(d_.n_draft.sum())),
                       ("\\quad carried word for word", f0(d_.identical.sum())),
                       ("\\quad carried near-identical", f0(d_.near_identical.sum())),
                       ("\\quad reworded", f0(d_.changed.sum())),
                       ("\\quad removed", f0(d_.removed.sum())),
                       ("Conditions in the adopted motion, not the draft", f0(d_.added.sum())),
                       ("\\quad template text", f0(d_.added_template.sum())),
                       ("\\quad other text", f0(d_.added_other.sum()))):
            a(rf"{lab} & {v} & \\")
        a(r"\midrule & Any change & \shortstack[r]{Change beyond\\template additions}\\")
        for lab, m_ in (("Item records modifications", d_.mods.eq(True)),
                        ("No modifications recorded", d_.mods.eq(False)),
                        ("No item at that hearing", d_.mods.isna())):
            a(rf"{lab} & {f0((m_ & d_.any_change).sum())} & "
              rf"{f0((m_ & d_.change_beyond_template).sum())}\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")
    if len(dv["added"]):
        a(r"{\small\begin{longtable}{@{}L{3.3cm}L{10.9cm}@{}}")
        a(r"\caption{Conditions the adopted motion carries and the draft did not, drawn at random "
          r"from the %s whose text is not template text (Table~\ref{tab:diff}). Some are the "
          r"Commission's changes; some are staff completing the draft, or conditions the parse "
          r"missed on the draft side --- the table cannot tell which.}\label{tab:added}\\\toprule"
          % N(int(d_.added_other.sum())))
        a(r"Case & Added condition\\\midrule\endfirsthead")
        a(r"\toprule Case & Added condition\\\midrule\endhead")
        for r in dv["added"].itertuples():
            a(rf"\texttt{{{latex_text(r.case_key)}}} ({int(r.year) if pd.notna(r.year) else ''}) & "
              rf"\emph{{{latex_text(r.heading) or 'untitled'}.}} {quote(r.body, 60)} "
              rf"(\href{{{url_tex(r.doc_url)}}}{{source}})\\[3pt]")
        a(r"\bottomrule\end{longtable}}")
        a("")
    if len(d_):
        M.update({"ccDiffPairs": N(len(d_)), "ccDiffAnyChange": N(d_.any_change.sum()),
                  "ccDiffAnyChangePct": f"{100*d_.any_change.mean():.0f}",
                  "ccDiffAdded": N(d_.added.sum()), "ccDiffRemoved": N(d_.removed.sum()),
                  "ccDiffAddedTemplate": N(d_.added_template.sum()),
                  "ccDiffAddedOther": N(d_.added_other.sum()),
                  "ccDiffTemplateChars": str(TEMPLATE_CHARS),
                  "ccDiffTemplateCases": str(TEMPLATE_MIN_CASES),
                  "ccDiffBeyondTemplate": N(d_.change_beyond_template.sum()),
                  "ccDiffNoModsBeyond": N((d_.mods.eq(False) & d_.change_beyond_template).sum()),
                  "ccDiffChanged": N(d_.changed.sum()), "ccDiffUnchanged": N(d_.unchanged.sum()),
                  "ccDiffIdentical": N(d_.identical.sum()), "ccDiffNear": N(d_.near_identical.sum()),
                  "ccDiffSame": f"{100*DIFF_SAME:.0f}",
                  "ccDiffCarriedPct": f"{100*d_.unchanged.sum()/max(d_.n_draft.sum(), 1):.0f}",
                  "ccDiffDraftConds": N(d_.n_draft.sum()),
                  "ccDiffModsChange": N((d_.mods.eq(True) & d_.any_change).sum()),
                  "ccDiffModsNoChange": N((d_.mods.eq(True) & ~d_.any_change).sum()),
                  "ccDiffNoModsChange": N((d_.mods.eq(False) & d_.any_change).sum()),
                  "ccDiffNoModsNoChange": N((d_.mods.eq(False) & ~d_.any_change).sum()),
                  "ccDiffNoItem": N(d_.mods.isna().sum()),
                  "ccDiffMods": N(d_.mods.eq(True).sum()),
                  "ccDiffByText": N(d_.matched_by.eq("text").sum())})
    else:
        for k in ("ccDiffPairs", "ccDiffAnyChange", "ccDiffAnyChangePct", "ccDiffAdded",
                  "ccDiffRemoved", "ccDiffChanged", "ccDiffUnchanged", "ccDiffDraftConds",
                  "ccDiffIdentical", "ccDiffNear", "ccDiffSame", "ccDiffCarriedPct",
                  "ccDiffModsChange", "ccDiffModsNoChange", "ccDiffNoModsChange",
                  "ccDiffNoModsNoChange", "ccDiffNoItem", "ccDiffMods", "ccDiffByText",
                  "ccDiffAddedTemplate", "ccDiffAddedOther", "ccDiffBeyondTemplate",
                  "ccDiffNoModsBeyond"):
            M[k] = "0"
        M["ccDiffTemplateChars"], M["ccDiffTemplateCases"] = str(TEMPLATE_CHARS), str(TEMPLATE_MIN_CASES)

    # ── 7. the option-relevant conditions ───────────────────────────────────
    oc = c[c.part.eq("conditions")].copy()
    oc["kind"] = [option_kind(h, b) for h, b in zip(oc.heading, oc.body)]
    oc = oc[oc.kind.ne("")]
    oc["months"] = oc.body.map(durations)
    oc["first_period"] = oc.months.map(lambda x: x[0] if x else np.nan)
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The conditions that set the entitlement's clock, and the period each states, by "
      r"hearing year. A condition is classed by its heading or, in the eras that print none, by "
      r"the first words of its body; its period is the first the body states, in months. For "
      r"each kind: the motions carrying it, the modal period, and the share of those stating a "
      r"period that state the modal one. Years with fewer than %d such motions are pooled.}"
      r"\label{tab:option}" % OPTION_MIN)
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{l" + "rrr" * len(OPTION_KIND) + r"}\toprule")
    a(r"& " + " & ".join(rf"\multicolumn{{3}}{{c}}{{{k.capitalize()}}}" for k, _ in OPTION_KIND)
      + r"\\")
    a("".join(rf"\cmidrule(lr){{{2 + 3*i}-{4 + 3*i}}}" for i in range(len(OPTION_KIND))))
    a(r"Year & " + " & ".join(["Motions & Modal & At modal"] * len(OPTION_KIND)) + r"\\\midrule")
    per_year = oc.groupby("year").skey.nunique()
    ok_years = [int(y_) for y_, n_ in per_year.items() if n_ >= OPTION_MIN]
    oc["yband"] = oc.year.map(lambda y_: str(int(y_)) if pd.notna(y_) and int(y_) in ok_years
                              else ("undated" if pd.isna(y_) else
                                    f"other years (fewer than {OPTION_MIN})"))
    order = [str(y_) for y_ in sorted(ok_years)] + \
        [b_ for b_ in (f"other years (fewer than {OPTION_MIN})", "undated") if (oc.yband == b_).any()]
    for b_ in order:
        g_all = oc[oc.yband.eq(b_)]
        cells = []
        for k, _ in OPTION_KIND:
            g = g_all[g_all.kind.eq(k)].drop_duplicates("skey")
            per_ = g.first_period.dropna()
            if not len(g):
                cells += ["", "", ""]
                continue
            modal = per_.mode().iloc[0] if len(per_) else np.nan
            cells += [f0(len(g)), "---" if pd.isna(modal) else f"{int(modal)}",
                      "---" if pd.isna(modal) else f"{100*(per_ == modal).mean():.0f}\\%"]
        a(rf"{b_} & " + " & ".join(cells) + r"\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")
    fig_validity(oc)
    a("")
    val = oc[oc.kind.eq("validity") & oc.first_period.notna()].drop_duplicates("skey").first_period
    vv = oc[oc.kind.eq("validity") & oc.first_period.notna()].drop_duplicates("skey")
    vall = oc[oc.kind.eq("validity")].skey.nunique()
    M["ccValidityStatePct"] = f"{100*vv.skey.nunique()/max(vall, 1):.0f}"
    M.update({"ccValidityYears": str(int((vv.groupby("year").size() >= MIN_MOTIONS_YEAR).sum())),
              "ccValidityN": N(oc[oc.kind.eq("validity")].skey.nunique()),
              "ccExtensionN": N(oc[oc.kind.eq("extension")].skey.nunique()),
              "ccExtensionWithPeriod": N(oc[oc.kind.eq("extension") &
                                            oc.first_period.notna()].skey.nunique()),
              "ccValidityModal": str(int(val.mode().iloc[0])) if len(val) else "---",
              "ccValidityModalShare": f"{100*(val == val.mode().iloc[0]).mean():.0f}"
              if len(val) else "---",
              "ccOptionRows": N(len(oc))})

    # ── 8. the thirteen-category scheme on the full set ─────────────────────
    hd = headed.drop_duplicates("hnorm")
    pk = acond.classify(hd.heading.tolist())
    w = headed.hnorm.value_counts().reindex(hd.hnorm).values.astype(float)
    unh = strict[strict.hnorm.eq("")]
    ub = acond.classify(unh.body.tolist()) if len(unh) else pd.DataFrame(columns=acond.TAXONOMY)
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The conditions memo's thirteen-category scheme, run unchanged on the census. "
      r"Headings are scored by distinct heading and weighted by how often each is imposed; the "
      r"eras that print no headings are scored on the condition's text instead, which the "
      r"scheme was not written for. In-sample: the keywords were written against the headings "
      r"of the first 225 packets, with one round of revision.}\label{tab:taxonomy}")
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{llrrr}\toprule")
    a(r"Family & Category & Headings, distinct & Headings, weighted & Unheaded bodies\\\midrule")
    fams = (("Changes the project", acond.PROJECT_CHANGING),
            ("Changes its obligations", acond.OBLIGATION_IMPOSING),
            ("Neither", acond.PROCEDURAL))

    def wt(mask_):
        return 100 * w[mask_.values].sum() / w.sum() if w.sum() else np.nan
    for i, (fam, keys) in enumerate(fams):
        for j, k in enumerate(keys):
            a(rf"{fam if j == 0 else ''} & {latex_text(k)} & {100*pk[k].mean():.1f}\% & "
              rf"{wt(pk[k]):.1f}\% & {100*ub[k].mean() if len(ub) else float('nan'):.1f}\%\\")
        a(r"\midrule")
    anyh = pk.any(axis=1)
    a(rf"\multicolumn{{2}}{{l}}{{\textbf{{Any category}}}} & \textbf{{{100*anyh.mean():.1f}\%}} & "
      rf"\textbf{{{wt(anyh):.1f}\%}} & "
      rf"\textbf{{{100*ub.any(axis=1).mean() if len(ub) else float('nan'):.1f}\%}}\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")
    unc = hd.assign(w=w)[~anyh.values].sort_values("w", ascending=False).head(15)
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The most frequent headings the scheme does not reach.}\label{tab:uncovered}")
    a(r"\begin{tabular}{@{}L{9cm}r@{}}\toprule")
    a(r"Heading & Conditions\\\midrule")
    for r in unc.itertuples():
        a(rf"{latex_text(r.heading)} & {f0(r.w)}\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")
    M["ccUncoveredTop"] = latex_text("; ".join(unc.heading.head(6)))
    M.update({"ccTaxDistinct": f"{100*anyh.mean():.1f}", "ccTaxWeighted": f"{wt(anyh):.1f}",
              "ccTaxUnheaded": f"{100*ub.any(axis=1).mean():.1f}" if len(ub) else "---",
              "ccTaxProject": f"{wt(pk[list(acond.PROJECT_CHANGING)].any(axis=1)):.1f}",
              "ccTaxObligation": f"{wt(pk[list(acond.OBLIGATION_IMPOSING)].any(axis=1)):.1f}",
              "ccTaxHeadings": N(len(hd))})

    # ── negatives: searches that came back empty, so nobody runs them again ──────
    a_ = load_census()
    fam_hits = a_[a_.status.eq("200")].groupby("family").size()
    fam_asked = a_.groupby("family").size()
    cty = a_[a_.family.eq("citypln") & a_.source.eq("constructed")].copy()
    cty["y"] = pd.to_numeric(cty.variant.str.extract(r"@(\d{4})")[0], errors="coerce")
    cty_old = cty[cty.y < 2022]
    site_rows = []
    for f in ("cdx_sfgov_org_site_planning.json", "cdx_sfgov_org_site_uploadedfiles_planning.json"):
        if (MANIFESTS / f).exists():
            site_rows += [r for r in json.loads((MANIFESTS / f).read_text()).get("rows", [])
                          if r[2] == "200"]
    site_named = sum(1 for r in site_rows if family_of(r[0]) != "other")
    ml_ = pd.read_csv(MANIFESTS / "minutes_links.csv", dtype=str).fillna("") \
        if (MANIFESTS / "minutes_links.csv").exists() else pd.DataFrame(columns=["url", "year"])
    staff = pd.to_numeric(ml_.loc[ml_.url.str.contains(r"(?i)sfgov\.org/link\.ashx",
                                                        regex=True), "year"], errors="coerce")
    staff_span = f"{int(staff.min())}--{int(staff.max())}" if staff.notna().any() else "---"
    neg = [
        ("Directory listings", f"{M.get('ccListingsTried', '---')} directory and S3 "
         f"bucket-listing URLs on both hosts", f"{M.get('ccListingsIndex', '---')} returned an "
         f"index; every document has to be asked for by name"),
        ("Adopted motions by year on \\texttt{commissions.sfplanning.org}",
         f"{f0(fam_asked.get('cpcmotions_year', 0))} constructed URLs",
         f"{f0(fam_hits.get('cpcmotions_year', 0))} answered; the same keys on the S3 mirror "
         f"answered {f0(fam_hits.get('cpcmotions_year_mirror', 0))}"),
        ("Date-keyed packet host before 2022", f"{f0(len(cty_old))} constructed URLs for "
         f"hearings before 2022", f"{f0(cty_old.status.eq('200').sum())} answered"),
        (f"Vault links as the {staff_span} minutes print them", "the staff form "
         "\\texttt{/link.ashx}", "a sign-in page; the public form \\texttt{/External/link.ashx} "
         "serves the same object"),
        ("Vault objects not shared publicly", f"{f0(a_.status.isin(['login', 'vault_not_public']).sum())} "
         f"links", "the vault's sign-in or ``not public'' page; counted as absent, not as "
         "documents"),
        ("The pre-2003 city site's motion pages", "Wayback CDX under "
         "\\texttt{ci.sf.ca.us/planning} and \\texttt{sfgov.org/planning}",
         f"{M.get('ccCdxMotionsHtml', '---')} motion pages captured in all"),
        ("The city site's later planning pages", "Wayback CDX under \\texttt{sfgov.org/site/planning} and "
         "\\texttt{sfgov.org/site/uploadedfiles/planning}",
         f"{f0(len(site_rows))} captures, {f0(site_named)} named by a motion or case number"),
        ("Recorded Notices of Special Restrictions", "the Assessor-Recorder's public pages "
         "(read, not searched)", "an index by APN and document type from 1990; images by "
         "purchase; bulk access not addressed (\\S\\ref{sec:nsr})"),
    ]
    a(r"{\small\begin{longtable}{@{}L{4.3cm}L{4.6cm}L{6.2cm}@{}}")
    a(r"\caption{Negative results: searches that came back empty or closed, so nobody runs them "
      r"again.}\label{tab:negatives}\\\toprule")
    a(r"Search & What was tried & Result\\\midrule\endfirsthead")
    a(r"\toprule Search & What was tried & Result\\\midrule\endhead")
    for x, y_, z in neg:
        a(rf"{tt_break(x)} & {tt_break(y_)} & {tt_break(z)}\\")
    a(r"\bottomrule\end{longtable}}")
    a("")

    # ── appendix: the data dictionary ─────────────────────────────────────────
    call = pd.read_parquet(COND_LONG.with_suffix(".parquet"))
    a(r"{\small\begin{longtable}{@{}L{3.6cm}rL{10.2cm}@{}}")
    a(r"\caption{\texttt{conditions\_long}: every column, with its share of rows populated "
      r"(non-empty, or true for a flag) over all %s rows, canonical or not.}"
      r"\label{tab:dictionary}\\\toprule" % N(len(call)))
    a(r"Column & Filled & Meaning\\\midrule\endfirsthead")
    a(r"\toprule Column & Filled & Meaning\\\midrule\endhead")
    for col, desc in DICTIONARY:
        v = call[col]
        filled = (v.astype(bool) if v.dtype == bool else
                  v.notna() & v.astype(str).str.strip().ne("") & v.astype(str).ne("nan"))
        a(rf"\texttt{{{latex_text(col)}}} & {100*filled.mean():.0f}\% & {desc}\\")
    a(r"\bottomrule\end{longtable}}")
    a("")
    missing = [c_ for c_ in call.columns if c_ not in {d_ for d_, _ in DICTIONARY}]
    assert not missing, f"undocumented columns: {missing}"
    M["ccRowsAll"] = N(len(call))

    # ── validation scores, if the round has been scored ──────────────────────
    def pc(x):
        return f"{100*x:.1f}" if isinstance(x, (int, float)) else "---"
    M["ccValOutYearFrom"] = M["ccValOutYearTo"] = "---"
    for k in ("ccValGoldDocs", "ccValGoldConds", "ccValInPrec", "ccValInRecall", "ccValInHead",
              "ccValInHeadN", "ccValNoneDocs", "ccValNoneFalse", "ccValOutRows",
              "ccValOutPrec", "ccValOutStart", "ccValOutEnd", "ccValOutHead", "ccValOutHeadN",
              "ccValRoneInPrec", "ccValRoneInRecall", "ccValAdjudicated", "ccValFrozen",
              "ccValRoneFrozen", "ccValOutDocs"):
        M[k] = "---"                    # overwritten below once the rounds are scored
    if score_path(ROUND).exists():
        sc = json.loads(score_path(ROUND).read_text())
        ins, oos = sc.get("in_sample", {}), sc.get("out_of_sample", {})
        M.update({"ccValGoldDocs": str(ins.get("documents", "---")),
                  "ccValGoldConds": str(ins.get("gold_conditions", "---")),
                  "ccValInPrec": pc(ins.get("boundary_precision")),
                  "ccValInRecall": pc(ins.get("boundary_recall")),
                  "ccValInHead": pc(ins.get("heading_accuracy")),
                  "ccValInHeadN": str(ins.get("heading_n", "---")),
                  "ccValNoneDocs": str(ins.get("no_conditions_docs", "---")),
                  "ccValNoneFalse": str(ins.get("false_conditions_in_those", "---")),
                  "ccValAdjudicated": str(ins.get("adjudicated", 0)),
                  "ccValFrozen": sc["frozen"].replace("T", " ")[:16],
                  "ccValOutRows": str(oos.get("rows", "---")),
                  "ccValOutPrec": pc(oos.get("boundary_precision")),
                  "ccValOutStart": pc(oos.get("start_ok")),
                  "ccValOutEnd": pc(oos.get("end_ok")),
                  "ccValOutHead": pc(oos.get("heading_accuracy")),
                  "ccValOutHeadN": str(oos.get("heading_n", "---"))})
        hc = handcheck_path(ROUND)
        if hc.exists():
            hh = pd.read_csv(hc, dtype=str).fillna("")
            M["ccValOutDocs"] = str(hh.doc_id.nunique())
            yy = pd.to_numeric(hh.hearing_date.str[:4], errors="coerce").dropna()
            M["ccValOutYearFrom"] = str(int(yy.min())) if len(yy) else "---"
            M["ccValOutYearTo"] = str(int(yy.max())) if len(yy) else "---"
    if score_path(1).exists() and ROUND > 1:
        s1 = json.loads(score_path(1).read_text())
        M.update({"ccValRoneInPrec": pc(s1["in_sample"].get("boundary_precision")),
                  "ccValRoneInRecall": pc(s1["in_sample"].get("boundary_recall")),
                  "ccValRoneFrozen": s1["frozen"].replace("T", " ")[:16]})
    # the round before this one: its scores, and what this round did to its hand-checked rows
    for k in ("ccValRtwoInPrec", "ccValRtwoInRecall", "ccValRtwoFrozen", "ccValRtwoOutRows",
              "ccValRtwoOutPrec", "ccValRtwoOutCorrect", "ccValCarryRows", "ccValCarryReproduced",
              "ccValCarryCorrect", "ccValRound"):
        M[k] = "---"
    M["ccValRound"] = str(ROUND)
    if ROUND > 2 and score_path(ROUND - 1).exists():
        s2 = json.loads(score_path(ROUND - 1).read_text())
        o2 = s2.get("out_of_sample", {})
        M.update({"ccValRtwoInPrec": pc(s2["in_sample"].get("boundary_precision")),
                  "ccValRtwoInRecall": pc(s2["in_sample"].get("boundary_recall")),
                  "ccValRtwoFrozen": s2["frozen"].replace("T", " ")[:16],
                  "ccValRtwoOutRows": str(o2.get("rows", "---")),
                  "ccValRtwoOutPrec": pc(o2.get("boundary_precision")),
                  "ccValRtwoOutCorrect": str(round(o2["boundary_precision"] * o2["rows"]))
                  if o2.get("rows") else "---"})
    if score_path(ROUND).exists():
        pr = json.loads(score_path(ROUND).read_text()).get("previous_round_rows")
        if pr:
            M.update({"ccValCarryRows": str(pr["rows"]),
                      "ccValCarryReproduced": str(pr["reproduced"]),
                      "ccValCarryCorrect": str(pr["reproduced_correct"])})
    # ── what this round's rules changed, counted on the previous round's whole parse ────────
    a(round_effect_table(M))
    a("")
    M.update(correction_macros())
    lg = pd.read_csv(PULL_LOG, dtype=str).fillna("")
    M.update({"ccDocsRead": N(lg.status.str.startswith("ok").sum()),
              "ccOcrPages": N(pd.to_numeric(lg.ocr_pages, errors="coerce").fillna(0).sum()),
              "ccQuoteWords": str(QUOTE_WORDS)})
    fig_coverage_content(it, s_c)
    numbers_report(a, M)
    # One file holds every table; each is guarded by its label so the memo can place it in
    # its own section with \cctable{<label>} (\def\cctab{<label>}\input{...}).
    chunks = [ch for ch in "\n".join(L[1:]).split("\n\n") if ch.strip()]
    out = [L[0]]
    for ch in chunks:
        labs = re.findall(r"\\label\{tab:([A-Za-z]+)\}", ch)
        assert len(labs) == 1, f"one table per chunk: {labs} {ch[:120]}"
        lab = re.search(r"\\label\{tab:([A-Za-z]+)\}", ch)
        out.append(r"\ifnum\pdfstrcmp{\cctab}{%s}=0" % lab.group(1))
        out.append(ch)
        out.append(r"\fi")
    (TAB2 / "conditions_content_tables.tex").write_text("\n".join(out) + "\n")
    # The census's headline numbers, for the memos that report them without re-deriving them
    # (the conditions memo's dated corrections read this file).
    (STORE / "census_summary.json").write_text(json.dumps(
        {"written": dt.datetime.now().isoformat(timespec="seconds"), **M}, indent=2) + "\n")
    (TAB2 / "conditions_content_macros.tex").write_text(
        "% GENERATED BY collect_conditions.py report --- do not edit by hand.\n" +
        "\n".join(rf"\newcommand{{\{k}}}{{{v}}}" for k, v in sorted(M.items())) + "\n")
    print(f"→ {TAB2}")


# conditions_long, column by column: what each holds and where it comes from.
DICTIONARY = [
    ("case_no", "The case number the document prints (the running header's most frequent), normalised; empty where none is printed."),
    ("case_key", "The case in the reconciled universe the row belongs to (see \\texttt{case\\_match}); empty where it does not resolve to exactly one."),
    ("case_stem", "\\texttt{case\\_key} (else \\texttt{case\\_no}) with its suffix letters removed, by the shared \\texttt{case\\_stem}."),
    ("motion_no", "The motion or resolution number from the title line; empty for a draft."),
    ("doc_type", "\\texttt{adopted\\_motion} or \\texttt{draft\\_packet} (a title reading ``Draft Motion'' or a placeholder number, with no real number in the title)."),
    ("doc_url", "The first URL the document was read from; the lines cache lists every URL that served the same bytes."),
    ("hearing_date", "ISO date from the ``Hearing Date'' line, else the date in a date-keyed URL, else the item table's date for the motion number."),
    ("adoption_date", "ISO date from ``ADOPTED \\dots\\ on <date>''; empty for drafts."),
    ("section_heading", "The standing block the condition sits under (PERFORMANCE, MONITORING, \\dots; in 1998--2009 the lettered part, e.g.\\ `The Approved Project')."),
    ("condition_no", "The printed number: \\texttt{7}, a letter \\texttt{C}, or \\texttt{B(2)} for a parenthesised item under lettered part B; empty for a named, unnumbered condition or a standing block."),
    ("ordinal", "Position of the row within its motion section, from 1."),
    ("heading", "The condition's name: its bold lead-in, or a short Title Case run before the first full stop where the document has no fonts; empty where none is printed."),
    ("body", "The condition's text with the heading, running headers, footers, page numbers and the compliance line removed; lines rejoined, hyphenation undone."),
    ("compliance_contact", "The ``For information about compliance, contact \\dots'' line(s) moved out of the body."),
    ("page_start", "Page of the source document (1-based, in the original file's numbering) where the condition starts."),
    ("page_end", "Page where it ends."),
    ("n_chars", "Length of \\texttt{body}."),
    ("has_dollar", "\\texttt{body} contains a dollar figure."),
    ("has_percent", "\\texttt{body} contains a percentage."),
    ("has_numeric_quantity", "\\texttt{body} contains a figure with a unit (units, feet, spaces, hours, days, months, years, a.m./p.m., seats, \\dots)."),
    ("code_sections_cited", "Planning Code sections cited, as printed, semicolon-separated."),
    ("code_sections_norm", "The same with the Article 4 recodification applied (\\S315 $\\to$ \\S415 and the other pairs the motions themselves state as ``formerly'')."),
    ("parse_method", "How the start was found: \\texttt{numbered}, \\texttt{numbered+bold}, \\texttt{numbered+text}, \\texttt{bold\\_heading}, \\texttt{lettered}, \\texttt{paren}, \\texttt{section\\_block}; \\texttt{+ocr} when the page was OCR'd, \\texttt{+ocrdigit} when an OCR-misread number was repaired."),
    ("parse_confidence", "\\texttt{high}, \\texttt{medium} or \\texttt{low}, by rule: low for an implausible length or an unheaded OCR row outside a numbered list; medium for OCR, implicit blocks, sequence gaps and text-read headings."),
    ("part", "\\texttt{preamble} for the standing blocks before the ``Conditions of Approval, Compliance, Monitoring, and Reporting'' line; \\texttt{conditions} otherwise."),
    ("implicit", "The row is a standing block with prose and no condition start (AUTHORIZATION, SEVERABILITY)."),
    ("sequence_gap", "The number skips one or two from the expected sequence."),
    ("style", "The section's numbering style: \\texttt{numbered}, \\texttt{bold\\_heading}, \\texttt{paren}, \\texttt{lettered}, \\texttt{unknown}."),
    ("container", "\\texttt{motion} (an adopted-motion file), \\texttt{packet}, or \\texttt{html} (a pre-2003 motion page)."),
    ("embedded_in_packet", "An adopted motion that is not the file's own: attached to a packet, or to a later motion that modifies it."),
    ("instrument", "\\texttt{motion} or \\texttt{resolution}."),
    ("case_match", "How \\texttt{case\\_key} was reached: \\texttt{exact}, \\texttt{serial padded}, \\texttt{expanded} (two-digit year), \\texttt{stem} (the only case with that stem), \\texttt{motion number} (the item table's case for the motion), or why not."),
    ("canonical", "The one copy of this motion section the analysis uses: a standalone file over an embedded copy, a text layer over OCR, then the fuller parse."),
    ("doc_id", "First 16 hex digits of the document's SHA-256; names its cache files."),
    ("section", "Index of the motion section within the document, from 0."),
]

CDX_KINDS = ["motion (HTML)", "motion (PDF)", "packet (PDF)", "DRA", "other"]


def cdx_yield() -> pd.DataFrame:
    """Distinct URLs captured with status 200, by first-capture year and kind."""
    rows = []
    for f in MANIFESTS.glob("cdx_*.json"):
        for orig, ts, st, mime, ln in json.loads(f.read_text()).get("rows", []):
            if st != "200":
                continue
            fam, name = family_of(orig), _doc_name(orig)
            if fam in ("f_motion", "cpcmot_sfgov") and re.match(r"\d{4,6}\.html?$", name):
                k = "motion (HTML)"
            elif (fam in ("cpcmotions_ftp", "cpcmotions_year") and
                  re.match(r"(?i)R?\d{4,6}.*\.pdf$", name)) or \
                    (fam == "oldsite_modules" and re.match(r"(?i)\d{5}\b.*\.pdf$", name)):
                k = "motion (PDF)"
            elif fam in ("cpcpackets_ftp", "cpcpackets") and re.search(r"(?i)\.pdf$", name):
                k = "packet (PDF)"
            elif "dra" in fam:
                k = "DRA"
            else:
                k = "other"
            rows.append((re.sub(r"^https?://(www\.)?", "", orig.lower()), ts[:4], k))
    if not rows:
        return pd.DataFrame()
    d = pd.DataFrame(rows, columns=["u", "y", "k"]).sort_values("y").drop_duplicates("u")
    return d.groupby(["y", "k"]).size().unstack(fill_value=0)


RESIDUAL = ["found, read", "found, not read", "none found, number", "none found, no number"]


def residual(it: pd.DataFrame, cond: pd.DataFrame) -> pd.DataFrame:
    """Each conditioned item without condition text, with the reason."""
    a = load_census()
    a = a[a.status.eq("200")]
    lg = pd.read_csv(PULL_LOG, dtype=str).fillna("") if PULL_LOG.exists() else pd.DataFrame()
    read_urls = set(lg.loc[lg.status.str.startswith("ok"), "url"]) if len(lg) else set()
    disc = pd.read_csv(DISCOVERED, dtype=str).fillna("") if DISCOVERED.exists() else None
    keys = {}
    for u, k in zip(a.candidate_url, a.key):
        keys.setdefault(u, set()).add(k)
    if disc is not None:
        for r in disc.itertuples():
            for k in (r.motion_no, r.case_no):
                if k:
                    keys.setdefault(r.url, set()).add(k)
    found, read = set(), set()
    for u in a.candidate_url:
        ks = keys.get(u, set())
        found |= ks
        if u in read_urls:
            read |= ks
    g = cond[~cond.cond_text].copy()
    ino = g.action_instrument_no.astype(str).str.strip().str.lstrip("0")
    f_ = g.cn.isin(found) | (ino.ne("") & ino.isin(found))
    r_ = g.cn.isin(read) | (ino.ne("") & ino.isin(read))
    num = ino.ne("") & ino.str.fullmatch(r"\d+")
    g["why"] = np.select([f_ & r_, f_, num], RESIDUAL[:3], RESIDUAL[3])
    g["era"] = g.year.map(era_of)
    return g


# A macro name cannot hold digits; each era gets a word.
ERA_NAME = {"1998--2002": "EarlyHtml", "2003--2009": "LateHtml", "2010": "TwentyTen",
            "2011--2014": "EarlyPdf", "2015--2017": "MidPdf", "2018--2021": "Vault",
            "2022--2026": "Recent"}


def discovery_macros() -> dict:
    """What discovery found, read back from the manifests."""
    out = {}
    lf = MANIFESTS / "listings.json"
    if lf.exists():
        L_ = json.loads(lf.read_text())
        out.update({"ccListingsTried": N(len(L_)),
                    "ccListingsIndex": N(sum(bool(x.get("is_index")) for x in L_))})
    cdx = [json.loads(f.read_text()) for f in sorted(MANIFESTS.glob("cdx_*.json"))]
    rows = [r for j in cdx for r in j.get("rows", [])]
    ok = [r for r in rows if r[2] == "200"]
    out.update({"ccCdxPrefixes": N(len(cdx)), "ccCdxUrls": N(len(rows)),
                "ccCdxOk": N(len(ok)),
                "ccCdxPdf": N(sum(1 for r in ok if re.search(r"(?i)\.pdf$", r[0]))),
                "ccCdxFirstYear": min((r[1][:4] for r in ok), default="---")})
    for name, f, col in (("ccHearingDocs", "hearing_pages.csv", "url"),
                         ("ccOldsiteLinks", "oldsite_links.csv", "url"),
                         ("ccMinutesLinks", "minutes_links.csv", "url")):
        if (MANIFESTS / f).exists():
            d = pd.read_csv(MANIFESTS / f, dtype=str).fillna("")
            out[name] = N(d[col].ne("").sum())
            if f == "hearing_pages.csv":
                out["ccHearingPages"] = N(d.page.nunique())
            if f == "oldsite_links.csv":
                out["ccOldsitePages"] = N(d.page.nunique())
            if f == "minutes_links.csv":
                out["ccMinutesLinkDocs"] = N(d.source_file.nunique())
    ml = MANIFESTS / "minutes_links.csv"
    if ml.exists():
        d = pd.read_csv(ml, dtype=str).fillna("")
        d["fam"] = d.url.map(lambda u: family_of(public_url(u)) if u else "")
        y = pd.to_numeric(d.year, errors="coerce")
        v = y[d.fam.eq("minutes_vault")]
        h = y[d.source_file.str.lower().str.endswith((".htm", ".html")) &
              d.fam.isin(["cpcmotions_ftp", "cpcpackets_ftp", "oldsite_modules",
                          "cpcmotions_year", "cpcpackets", "cpcmotions_year_mirror",
                          "cpcpackets_mirror", "f_motion", "cpcmot_sfgov"])]
        out.update({"ccVaultLinksFrom": f"{int(v.min())}" if v.notna().any() else "---",
                    "ccHtmlLinksFrom": f"{int(h.min())}" if h.notna().any() else "---",
                    "ccHtmlLinksTo": f"{int(h.max())}" if h.notna().any() else "---"})
    if DISCOVERED.exists():
        d = pd.read_csv(DISCOVERED, dtype=str).fillna("")
        out["ccDiscoveredUrls"] = N(d.url.nunique())
        out["ccDiscoveredCensus"] = N(d[d.census.eq("True")].url.nunique())
    a = load_census()
    out.update({"ccProbed": N(len(a)), "ccProbeHits": N(a.status.eq("200").sum()),
                "ccProbeMissing": N(a.status.eq("404").sum()),
                "ccProbeLogin": N(a.status.isin(["login", "vault_not_public"]).sum()),
                "ccProbeSoft": N(a.status.eq("soft_404").sum()),
                "ccProbeOther": N((~a.status.isin(["200", "404", "login", "vault_not_public",
                                                   "soft_404"])).sum())})
    plog = STORE / "probe.log"
    # a verb phrase, because the memo reads "The probe \ccProbeFinished." (the bare "still
    # running" once printed "The probe still running.")
    out["ccProbeFinished"] = "finished" if plog.exists() and \
        "probe done" in plog.read_text()[-3000:] else "was still running"
    return out


def f0(x):
    return "---" if x is None or pd.isna(x) else f"{x:,.0f}".replace(",", "{,}")


def N(x):
    """A count as a macro body: thousands separated, braced so LaTeX does not space them."""
    return f0(x)


def yr_range(y: pd.Series) -> str:
    y = y.dropna()
    return f"{int(y.min())}--{int(y.max())}" if len(y) else "---"


def url_tex(u: str) -> str:
    return str(u).replace("%", r"\%").replace("#", r"\#").replace("&", r"\&").replace("_", r"\_")


# "Class 1 spaces" names a kind of bicycle space and counts nothing.
QTY = re.compile(r"(?i)(?<![\d.:])(?<!class )(\d{1,2}:\d{2}|\d[\d,]*(?:\.\d+)?)\s*\)?\s*"
                 r"(years?|months?|days?|hours?|feet|foot|units?|spaces?|percent|%|"
                 r"a\.?\s?m\b\.?|p\.?\s?m\b\.?)")


def quantities(t: str) -> list[str]:
    """Each stated quantity as a number and a unit: "three (3) years" gives "3 years",
    "9:00a.m." gives "9:00 a.m.", "18 months" stays. The spelled-out word beside the
    figure is the same number and is not counted again."""
    out = []
    for m in QTY.finditer(str(t)):
        n, u = m.group(1), m.group(2).lower().replace(" ", "")
        u = {"%": "percent", "foot": "feet"}.get(u, u)
        if u.startswith(("a", "p")) and "m" in u and not u.startswith(("percent",)):
            u = "a.m." if u.startswith("a") else "p.m."
        elif u != "percent" and n.replace(",", "") == "1":
            u = u.rstrip("s") if u != "feet" else "foot"
        elif u in ("year", "month", "day", "hour", "unit", "space"):
            u += "s"
        out.append(f"{n} {u}")
    return out


def examples(g: pd.DataFrame) -> list[tuple[str, object]]:
    """An early body, a typical one and the most unusual one, without repeating a body."""
    g = g[g.body.str.len() > 0]
    if not len(g):
        return []
    out, seen = [], set()
    early = g.sort_values(["year", "doc_id", "ordinal"]).iloc[0]
    mk = g.body.map(mask)
    modal = mk.value_counts().index[0]
    same = g[mk.eq(modal)]
    typ = same.iloc[(same.n_chars - same.n_chars.median()).abs().argsort().iloc[0]]
    sims = mk.map(lambda x: sim(x[:600], modal[:600]))
    odd = g.loc[sims.idxmin()]
    for kind, r in (("early", early), ("typical", typ), ("most unusual", odd)):
        k = (r.doc_id, r.section, r.ordinal)
        if k in seen or mask(r.body) in {mask(x.body) for _, x in out}:
            continue
        seen.add(k)
        out.append((kind, r))
    return out


def anatomy(c: pd.DataFrame, s_c: pd.DataFrame, cu_cases: set) -> dict:
    # Born-digital motions from the motion archive only: the vault's copies are signed scans
    # whose OCR layer drops headings, and a page-by-page anatomy has to read them.
    cand = s_c[s_c.doc_type.eq("adopted_motion") & s_c.container.eq("motion") &
               s_c.ocr_pages.eq("0") & s_c.case_key.isin(cu_cases) &
               s_c.source.isin(["cpcmotions_year", "cpcmotions_year_mirror", "cpcmotions_ftp"])]
    modern = set(c[c.section_canon.isin(["PERFORMANCE", "MONITORING", "OPERATION"])].skey)
    cand = cand[cand.skey.isin(modern)]
    if not len(cand):
        return {}
    med = cand.n_conditions.median()
    cand = cand.assign(d=(cand.n_conditions - med).abs()).sort_values(["d", "doc_id"])
    for r in cand.itertuples():
        out = _anatomy_of(r, c)
        if out:
            return out
    return {}


def _anatomy_of(r, c: pd.DataFrame) -> dict:
    """The parts of one motion, or nothing if any of its four landmarks --- PREAMBLE,
    FINDINGS, DECISION, EXHIBIT A --- cannot be found on its own line."""
    j = json.loads(gzip.decompress((DOCS / f"{r.doc_id}.lines.json.gz").read_bytes()))
    pages = j["pages"]

    def first(rx, after=0):
        for pg in pages:
            if pg["n"] >= after and any(re.search(rx, l["t"]) for l in pg["lines"]):
                return pg["n"]
        return None

    def span(a_, b_):
        return str(a_) if b_ is None or b_ <= a_ else f"{a_}--{b_}"
    rows = c[c.skey.eq(r.skey)]
    last = pages[-1]["n"]
    p_pre = first(r"^PREAMBLE$")
    p_find = first(r"^FINDINGS$")
    # The discretion itself is one numbered finding ("7. Planning Code Section 303
    # establishes criteria ..."), not the first place the section is cited.
    p_303 = first(r"(?i)section\s+303(?:\(c\))?\s+establishes|necessary,?\s+(?:or|and)\s+"
                  r"desirable", p_find or 0)
    p_101 = first(r"(?i)101\.1(?:\(b\))?\s+establishes|priority.planning\s+policies", p_find or 0)
    p_dec = first(r"^DECISION$")
    p_adopt = first(r"(?i)ADOPTED the foregoing", p_dec or 0)
    p_exa = first(r"(?i)^EXHIBIT\s+A$")
    p_exb = first(r"(?i)^EXHIBIT\s+B$", (p_exa or 0) + 1)
    p_exb_cited = first(r"(?i)exhibit\s+b", p_dec or 0)
    if not (p_pre and p_find and p_dec and p_exa):
        return {}
    out = [("Title block and summary of the request", "1",
            "Motion number, hearing date, case number, address, zoning, sponsor, staff "
            "contact; then the one-sentence statement of what the motion adopts.")]
    if p_pre:
        out.append(("Preamble", span(p_pre, p_find),
                    "The application, its filing, the environmental determination and the "
                    "hearing, recited as ``whereas'' facts."))
    if p_find:
        out.append(("Findings", span(p_find, (p_dec - 1) if p_dec else None),
                    "The site, the surroundings, the project, public comment, and Planning "
                    "Code compliance item by item."))
    if p_303:
        out.append(("\\quad the conditional-use finding (\\S303)", str(p_303),
                    "Whether the use is necessary or desirable and compatible with the "
                    "neighbourhood, and not detrimental --- the discretion itself."))
    if p_101:
        out.append(("\\quad the priority policies (\\S101.1)", str(p_101),
                    "The priority-policy findings, one paragraph each."))
    if p_dec:
        out.append(("Decision", str(p_dec),
                    "The approval, made subject to the conditions attached as Exhibit A and "
                    "to the plans on file; the appeal period; and the adoption block with the "
                    "roll call" +
                    (f" (p.~{p_adopt})" if p_adopt else "") + "."))
    if p_exa:
        pre_rows = rows[rows.part.eq("preamble")]
        if len(pre_rows):
            out.append(("Exhibit A: standing blocks",
                        span(pre_rows.page_start.min(), pre_rows.page_end.max()),
                        ", ".join(latex_text(h.capitalize() if h.isupper() else h)
                                  for h in pre_rows.heading) + "."))
        for sec_name in rows[rows.strict].section_canon.drop_duplicates():
            g = rows[rows.strict & rows.section_canon.eq(sec_name)]
            out.append((f"Exhibit A: {latex_text(sec_name.title())}",
                        span(g.page_start.min(), g.page_end.max()),
                        f"{len(g)} condition{'s' if len(g) != 1 else ''}: " +
                        "; ".join(latex_text(h) for h in g.heading.head(6)) +
                        ("; \\dots" if len(g) > 6 else "") + "."))
    if p_exb:
        out.append(("Exhibit B", span(p_exb, last), "The plans."))
    elif p_exb_cited:
        out.append(("Exhibit B", "---", f"The plans, cited by date in the decision "
                    f"(p.~{p_exb_cited}); kept on file and not bound into the motion."))
    return {"motion": str(r.motion_no), "case": str(r.case_key), "adopted": r.adoption_date,
            "pages": last, "n": int(rows.strict.sum()), "url": url_tex(r.doc_url),
            "rows": out}


DIFF_SAME = 0.90             # masked-text similarity at or above which a condition is carried
DIFF_HEAD = 0.85             # heading similarity at or above which two headings are one


def draft_vs_adopted(c: pd.DataFrame, s_c: pd.DataFrame, it: pd.DataFrame) -> dict:
    """Every case with a draft motion and the adopted one: the latest draft whose hearing is
    on or before the adoption. Conditions are matched by heading where both carry headings
    (fuzzily: the adopted copy is often an OCR'd scan, "Extenslon" for "Extension"), else
    greedily by masked-text similarity (at least 0.6). A matched condition is `reworded' when
    its number-masked text is less than DIFF_SAME similar to the draft's --- below that the
    Commission changed what it says; above it the difference is OCR, typography or a
    touched-up word, counted apart as `near-identical' so it cannot pass for an amendment."""
    ad = s_c[s_c.doc_type.eq("adopted_motion") & s_c.case_key.ne("")]
    dr = s_c[s_c.doc_type.eq("draft_packet") & s_c.case_key.ne("")]
    strict = c[c.strict]
    # How many cases' motions carry each condition text: an addition whose text recurs across
    # many other cases is the template, and more likely staff completing the draft between the
    # packet and adoption, or a draft-side parse miss, than something the Commission wrote.
    tk_cases = strict.assign(tk=strict.body.map(template_key)).groupby("tk").case_key.nunique()
    mods = it[it.cn.ne("")].assign(
        m=it.modifications.astype(str).str.strip().ne("") |
        it.project_modified.astype(str).str.lower().eq("yes"))
    rows, added = [], []
    for r in ad.itertuples():
        cand = dr[dr.case_key.eq(r.case_key)]
        if r.adoption_date:
            cand = cand[cand.hearing_date.le(r.adoption_date) | cand.hearing_date.eq("")]
        if not len(cand):
            continue
        d = cand.sort_values("hearing_date").iloc[-1]
        A = strict[strict.skey.eq(r.skey)]
        D = strict[strict.skey.eq(d.skey)]
        if not len(A) or not len(D):
            continue
        use_head = A.hnorm.ne("").mean() > 0.5 and D.hnorm.ne("").mean() > 0.5
        am, dm = A.body.map(mask).tolist(), D.body.map(mask).tolist()
        ah, dh = A.hnorm.tolist(), D.hnorm.tolist()
        free = set(range(len(A)))
        identical = near = reworded = removed = 0
        pairs_ = {}
        # First by heading, where both documents carry them; then whatever is left by text,
        # because one side often lost a heading to OCR or to the parse ("Garbage, composting
        # and recycling storage." read as prose in one copy and as a name in the other).
        passes = ([("head", DIFF_HEAD)] if use_head else []) + \
            [("text", 0.8 if use_head else 0.6)]
        for how, floor in passes:
            for i in range(len(D)):
                if i in pairs_:
                    continue
                best, bs = None, 0.0
                for k in free:
                    sc_ = (sim(ah[k], dh[i]) if ah[k] and dh[i] else 0.0) if how == "head" \
                        else sim(dm[i][:800], am[k][:800])
                    if sc_ > bs:
                        best, bs = k, sc_
                if best is not None and bs >= floor:
                    pairs_[i] = best
                    free.discard(best)
        for i in range(len(D)):
            if i not in pairs_:
                removed += 1
                continue
            k = pairs_[i]
            if am[k] == dm[i]:
                identical += 1
            elif sim(am[k][:1500], dm[i][:1500]) >= DIFF_SAME:
                near += 1
            else:
                reworded += 1
        tmpl = 0
        for k in free:
            row = A.iloc[k].copy()
            # its own case is one of the cases carrying it, so it must recur in TEMPLATE_MIN_CASES
            # others
            row["template"] = tk_cases.get(template_key(row.body), 0) >= TEMPLATE_MIN_CASES + 1
            tmpl += int(row["template"])
            added.append(row)
        # The item heard on the adoption date is the one whose `modifications` speaks for
        # this hearing. No such item (a hearing the corpus lacks, or a date the motion and
        # the minutes disagree on) is unknown, not "no modifications".
        hd_ = r.hearing_date or r.adoption_date
        m_ = mods[mods.cn.eq(r.case_key)]
        m_ = m_[m_.meeting_date.dt.strftime("%Y-%m-%d").eq(hd_)] if hd_ else m_.iloc[:0]
        rows.append({"case_key": r.case_key, "adopted_doc": r.doc_id, "adopted_section":
                     r.section, "motion_no": r.motion_no, "draft_doc": d.doc_id,
                     "draft_section": d.section, "draft_hearing": d.hearing_date,
                     "adopted_hearing": hd_, "matched_by": "heading" if use_head else "text",
                     "n_draft": len(D), "n_adopted": len(A),
                     "unchanged": identical + near, "identical": identical,
                     "near_identical": near, "changed": reworded, "removed": removed,
                     "added": len(free), "added_template": tmpl,
                     "added_other": len(free) - tmpl,
                     "any_change": bool(reworded or removed or free),
                     "change_beyond_template": bool(reworded or removed or len(free) - tmpl),
                     "mods": bool(m_.m.any()) if len(m_) else None, "year": r.year})
    pairs = pd.DataFrame(rows)
    add_all = pd.DataFrame(added)
    # The quoted draw is of the non-template additions: the template ones are the same few
    # standard conditions (Revocation, Noise Control, Managing Traffic During Construction ...)
    # and a random draw over all of them is mostly those.
    other = add_all[~add_all.template] if len(add_all) else add_all
    add = other.sample(min(10, len(other)), random_state=SEED).sort_values("year") \
        if len(other) else other
    return {"pairs": pairs, "added": add, "added_all": add_all}


TEMPLATE_MIN_CASES = 10
TEMPLATE_CHARS = 200


def merge_detectors(c: pd.DataFrame) -> dict:
    """Three signs that a condition's body swallowed what follows it, counted over canonical
    rows. (a) The next condition's number and name inside the body ("... Standards. 10.
    Community Liaison. Prior to ..." in condition 9). (b) One of the frequent headings, as a
    name, after a sentence end in the body of another heading. (c) A letter's salutation or
    "Re:" line in a body: the Exhibit A ran into correspondence. (b) also counts real
    sub-parts that carry a name ("Unit Mix." inside "Affordable Units"), so its level is not
    an error count; its change between rounds is what it is for."""
    c = c[c.canonical]
    hc = c.heading.fillna("").str.strip()
    freq = hc[hc.ne("")].value_counts()
    heads = [h for h, n in freq.items() if n >= 30 and 2 <= len(h.split()) <= 6 and h[:1].isupper()]
    rx_h = re.compile(r"(?<=[a-z0-9)\]]\. )(" + "|".join(re.escape(h) for h in
                                                      sorted(heads, key=len, reverse=True))
                      + r")\. [A-Z]")

    def emb_num(no, body):
        try:
            n = int(str(no))
        except ValueError:
            return False
        return bool(re.search(r"(?:^|[.;:)] )%d\s?\.\s+[A-Z][A-Za-z,/&\-]+(?:\s+[A-Za-z,/&\-()]+){0,7}"
                              r"\.\s" % (n + 1), str(body)))
    a_ = pd.Series([emb_num(n, b) for n, b in zip(c.condition_no, c.body)], index=c.index)
    b_ = pd.Series([bool(m := rx_h.search(str(body))) and m.group(1).lower() != h.lower()
                    for body, h in zip(c.body, hc)], index=c.index)
    d_ = c.body.str.contains(r"(?i)(?:^|\s)dear\s+(?:president|commissioner|members|planning|mr|"
                             r"ms|mrs|sir|madam)", regex=True) | \
        c.body.str.contains(r"(?:^|\. )Re: ", regex=True)
    return {"next_number": int(a_.sum()), "frequent_heading": int(b_.sum()),
            "correspondence": int(d_.sum()), "any": int((a_ | b_ | d_).sum()),
            "docs": int(c[a_ | b_ | d_].doc_id.nunique())}


def parse_metrics(c: pd.DataFrame, s: pd.DataFrame, universe: set) -> dict:
    """The quantities a parser round moves, computed the same way on any round's parse."""
    cc = c[c.canonical]
    strict = cc[cc.part.eq("conditions") & ~cc.implicit]
    per = strict[strict.doc_type.eq("adopted_motion")].groupby(["doc_id", "section"]).size()
    s = s.copy()
    s["canon"] = s.canonical.astype(str).eq("True")
    s["nc"] = pd.to_numeric(s.n_conditions, errors="coerce").fillna(0)
    s["yr"] = pd.to_numeric(s.hearing_date.fillna("").astype(str).str[:4].where(
        s.hearing_date.fillna("").astype(str).ne(""), s.adoption_date.fillna("").astype(str).str[:4]),
        errors="coerce")
    sc = s[s.canon & s.nc.gt(0)]
    inu = sc[sc.case_key.fillna("").isin(universe)]
    return {"rows": len(cc), "sections": int(s.canon.sum()),
            "with_exhibit_a": int((s.canon & s.has_exhibit_a.astype(str).eq("True")).sum()),
            "with_conditions": len(sc), "section_blocks": int(cc.implicit.sum()),
            "headings": int(strict.heading.fillna("").map(norm_heading).replace("", np.nan).nunique()),
            "median_per_motion": float(per.median()) if len(per) else np.nan,
            "earliest_in_universe": int(inu.yr.min()) if inu.yr.notna().any() else np.nan,
            **merge_detectors(c)}


# The document behind the conditions memo's withdrawn "earliest motion ... 1994": a 2021 packet
# with a 1994 scanned motion and a 2013 motion bound into it, which round 2 read as one section.
CORRECTION_1994_DOC = "25d1dfc00f4c91a4"


def _long_date(d: str) -> str:
    try:
        t = pd.Timestamp(d)
    except (ValueError, TypeError):
        return "---"
    return f"{t:%B} {t.day}, {t.year}"


def correction_macros() -> dict:
    """The facts the memo's dated correction states, read from the current parse rather than
    typed: the packet's case, and each motion bound into it with its date and case."""
    out = {k: "---" for k in ("ccFixPacketCase", "ccFixOldMotion", "ccFixOldDate", "ccFixOldCase",
                              "ccFixNewMotion", "ccFixNewDate", "ccFixNewCase")}
    if not SECTIONS.exists():
        return out
    s = pd.read_csv(SECTIONS, dtype=str).fillna("")
    s = s[s.doc_id.eq(CORRECTION_1994_DOC)]
    if not len(s):
        return out
    s = s.assign(d=s.hearing_date.where(s.hearing_date.ne(""), s.adoption_date))
    adopted = s[s.doc_type.eq("adopted_motion") & s.motion_no.ne("")].sort_values("d")
    draft = s[s.doc_type.eq("draft_packet")]
    if len(draft):
        out["ccFixPacketCase"] = latex_text(draft.case_no.iloc[0])
    if len(adopted) >= 2:
        o, n_ = adopted.iloc[0], adopted.iloc[-1]
        out.update({"ccFixOldMotion": o.motion_no, "ccFixOldDate": _long_date(o.d),
                    "ccFixOldCase": latex_text(o.case_no), "ccFixNewMotion": n_.motion_no,
                    "ccFixNewDate": _long_date(n_.d), "ccFixNewCase": latex_text(n_.case_key or n_.case_no)})
    return out


def round_effect_table(M: dict) -> str:
    """Table: the previous round's whole parse against this round's, on the same measures.
    Written only when the previous round was archived with its full parse."""
    prev = round_archive(ROUND - 1)
    if not (prev / "conditions_long.parquet").exists():
        return ""
    it = load_items()
    uni = set(case_universe(it))
    c0, s0 = pd.read_parquet(prev / "conditions_long.parquet"), \
        pd.read_csv(prev / "motion_sections.csv", dtype=str)
    c1, s1 = pd.read_parquet(COND_LONG.with_suffix(".parquet")), pd.read_csv(SECTIONS, dtype=str)
    # Only the documents both parses read: the pull went on after the previous round closed,
    # and a document it added is not something this round's rules changed.
    both = set(s0.doc_id) & set(s1.doc_id)
    new_docs = len(set(s1.doc_id) - set(s0.doc_id))
    m0 = parse_metrics(c0[c0.doc_id.isin(both)], s0[s0.doc_id.isin(both)], uni)
    m1 = parse_metrics(c1[c1.doc_id.isin(both)], s1[s1.doc_id.isin(both)], uni)
    M["ccRoundDocs"], M["ccRoundNewDocs"] = N(len(both)), N(new_docs)
    labels = [("rows", "condition rows (one copy of each motion)"),
              ("sections", "motion sections read"), ("with_exhibit_a", "\\quad with an Exhibit A located"),
              ("with_conditions", "\\quad with at least one condition"),
              ("section_blocks", "standing blocks and unsplit sections (implicit rows)"),
              ("headings", "distinct condition headings"),
              ("median_per_motion", "median conditions per adopted motion"),
              ("earliest_in_universe", "earliest year of a motion read for a case in the universe"),
              ("next_number", "bodies holding the next condition's number and name"),
              ("frequent_heading", "bodies holding another frequent heading as a name"),
              ("correspondence", "bodies holding a letter's salutation or ``Re:'' line")]
    def fmt(v, k=""):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return "---"
        if k.startswith("earliest"):            # a year, not a count
            return str(int(v))
        return f"{v:.0f}" if isinstance(v, float) else f"{v:,}".replace(",", "{,}")
    L = [r"\begin{table}[htbp]\centering",
         r"\caption{What round %d's boundary rules changed. Each quantity is computed the same way "
         r"on round %d's whole parse (archived at its close) and on this one, over the %s "
         r"documents both read; the %s documents pulled after round %d closed are left out of "
         r"both columns. The last three rows "
         r"are the merge detectors the round was written against (the second also counts real "
         r"sub-parts that carry a name, so its level is not an error count). Nothing here is "
         r"a validation score; those are in the text.}\label{tab:roundeffect}"
         % (ROUND, ROUND - 1, N(len(both)), N(new_docs), ROUND - 1),
         r"\begin{tabular}{lrr}\toprule",
         rf"Quantity & Round {ROUND - 1} & Round {ROUND}\\\midrule"]
    for k, lab in labels:
        L.append(rf"{lab} & {fmt(m0[k], k)} & {fmt(m1[k], k)}\\")
    L.append(r"\bottomrule\end{tabular}\end{table}")
    for k in ("rows", "next_number", "frequent_heading", "correspondence", "section_blocks",
              "earliest_in_universe", "with_conditions", "headings"):
        tag = "".join(w.title() for w in k.split("_"))
        M[f"ccRprev{tag}"], M[f"ccRcur{tag}"] = fmt(m0[k], k), fmt(m1[k], k)
    return "\n".join(L)


def template_key(body) -> str:
    """The opening of a condition's text with its numbers masked: two conditions from one
    template share it whatever figures they state."""
    return mask(body)[:TEMPLATE_CHARS]


def fig_validity(oc: pd.DataFrame):
    """The period the validity condition states, year by year: the share of motions whose
    validity condition gives the modal period, and the share giving something else."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    v = oc[oc.kind.eq("validity") & oc.first_period.notna()].drop_duplicates("skey")
    mode = int(v.first_period.mode().iloc[0]) if len(v) else 36
    n = v.groupby("year").size()
    yrs = n[n >= MIN_MOTIONS_YEAR].index
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    bottom = np.zeros(len(yrs))
    for lab, test, col in ((f"under {mode} months", lambda x: x < mode, "#b07d2b"),
                           (f"{mode} months", lambda x: x == mode, "#5b7fa6"),
                           (f"over {mode} months", lambda x: x > mode, "#2f6f4f")):
        sh = v[test(v.first_period)].groupby("year").size().reindex(yrs, fill_value=0) / n[yrs]
        ax.bar(yrs, 100 * sh.values, bottom=bottom, color=col, width=0.8, label=lab)
        bottom += 100 * sh.values
    ax.set_ylabel("% of motions whose validity\ncondition states a period")
    ax.set_xlabel("hearing year (years with fewer than %d such motions read are omitted)"
                  % MIN_MOTIONS_YEAR)
    ax.set_ylim(0, 100)
    ax.legend(frameon=False, fontsize=7.5, loc="lower left")
    ax.set_title("The period a validity condition gives the sponsor to obtain a permit",
                 loc="left", fontsize=9)
    fig.savefig(FIG2 / "fig_validity_period.pdf", bbox_inches="tight")
    plt.close(fig)


def fig_counts(both: pd.DataFrame):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    if len(both):
        ax.fill_between(both.index, both.p25, both.p75, color="#5b7fa6", alpha=0.2, lw=0)
        ax.plot(both.index, both["median"], color="#5b7fa6", lw=2, marker="o", ms=3,
                label="conditions per adopted motion (median, interquartile band)")
        ax2 = ax.twinx()
        ax2.plot(both.index, 100 * both.share, color="#a33", lw=1.5, ls="--",
                 label="items flagged as conditioned (%)")
        ax2.set_ylabel("% of items conditioned", color="#a33")
        ax2.spines["top"].set_visible(False)
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, frameon=False, fontsize=7.5, loc="upper left")
    ax.set_ylabel("conditions per motion")
    ax.set_xlabel("hearing year (years with fewer than %d adopted motions read are omitted)"
                  % MIN_MOTIONS_YEAR)
    ax.set_title("How many conditions a motion carries, beside how often items are conditioned",
                 loc="left", fontsize=9)
    fig.savefig(FIG2 / "fig_conditions_per_motion.pdf", bbox_inches="tight")
    plt.close(fig)


def fig_coverage_content(it: pd.DataFrame, s_c: pd.DataFrame):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 1, figsize=(7.2, 5.4), sharex=True)
    cond = it[it.flag]
    sh = cond.groupby("year").cond_text.mean() * 100
    n = cond.groupby("year").size()
    sh = sh.where(n >= 20)
    axes[0].bar(sh.index, sh.values, color="#2f6f4f", width=0.8)
    axes[0].set_ylabel("% of conditioned items\nwith condition text")
    axes[0].set_ylim(0, 100)
    axes[0].set_title("How much of the conditioned docket now has its condition text, and "
                      "from where", loc="left", fontsize=9)
    srcs = [("adopted_motion", "adopted motions", "#5b7fa6"),
            ("draft_packet", "draft motions (packets)", "#b07d2b")]
    bottom = None
    for dtp, lab, col in srcs:
        v = s_c[s_c.doc_type.eq(dtp)].groupby("year").size()
        yrs = sorted(set(s_c.year.dropna().astype(int)))
        v = v.reindex(yrs, fill_value=0)
        axes[1].bar(v.index, v.values, bottom=bottom, color=col, width=0.8, label=lab)
        bottom = v.values if bottom is None else bottom + v.values
    axes[1].set_ylabel("motion sections with\nparsed conditions")
    axes[1].set_xlabel("hearing year (top panel: years with fewer than 20 conditioned items "
                       "omitted)")
    axes[1].legend(frameon=False, fontsize=7.5)
    fig.savefig(FIG2 / "fig_census_coverage.pdf", bbox_inches="tight")
    plt.close(fig)


# ═══════════════════════════════════════════════════════════════════════════
# the numbers inside the conditions (brief of 2026-09-11, Part 6)
# ═══════════════════════════════════════════════════════════════════════════
# The census found that conditions are a template and that what varies is the figures. This
# stage reads those figures into typed columns instead of parsing more prose: one row per
# canonical condition that states at least one of them, with the matched text kept beside
# each value so any value can be checked against its sentence.
NUMERIC = STORE / "conditions_numeric.parquet"
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
         "twenty": 20, "twenty-five": 25, "thirty": 30, "forty": 40, "fifty": 50,
         "sixty": 60, "ninety": 90, "hundred": 100}
# a count as the template prints it: "21", "1,040", "one (1)", "two"
NUMTOK = r"(?:(?P<w>[A-Za-z\-]+)\s*\(\s*(?P<p>\d[\d,]*)\s*\)|(?P<d>\d[\d,]*)|(?P<w2>[A-Za-z\-]+))"


def _count(m) -> float | None:
    g = m.groupdict()
    for k in ("p", "d"):
        if g.get(k):
            return float(g[k].replace(",", ""))
    w = (g.get("w2") or g.get("w") or "").lower()
    return float(WORDS[w]) if w in WORDS else None


TIME = r"(\d{1,2})(?::(\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?|noon|midnight)"
TIME_RANGE = re.compile(rf"(?i){TIME}\s*(?:to|until|through|-|–|—)\s*{TIME}")
DAYS = re.compile(r"(?i)(daily|every day|seven days|7 days|monday|tuesday|wednesday|thursday|"
                  r"friday|saturday|sunday|weekday|weekend|holiday)")


def _hour(h, mnt, ap) -> float:
    h, mnt, ap = int(h), int(mnt or 0), ap.lower().replace(".", "").replace(" ", "")
    if ap == "noon":
        return 12.0
    if ap == "midnight":
        return 24.0
    if ap == "am" and h == 12:
        h = 0
    if ap == "pm" and h != 12:
        h += 12
    return h + mnt / 60


def hours_of(body: str) -> dict:
    """The first operating-hours range a condition states, as decimal hours; a closing time
    earlier than the opening runs past midnight and is written +24 (2 a.m. is 26). The day
    type is read from the forty characters around the range."""
    b = str(body)
    if re.search(r"(?i)24[\s-]*hours?(?: a| per)? day", b):
        return {"hours_open": 0.0, "hours_close": 24.0, "hours_days": "24 hours",
                "hours_ranges": 1, "hours_text": re.search(r"(?i).{0,30}24[\s-]*hours?.{0,20}", b).group(0)}
    ms = list(TIME_RANGE.finditer(b))
    if not ms:
        return {}
    m = ms[0]
    o = _hour(m.group(1), m.group(2), m.group(3))
    c = _hour(m.group(4), m.group(5), m.group(6))
    if c <= o:
        c += 24
    ctx = b[max(0, m.start() - 40): m.end() + 40]
    days = sorted({d.lower() for d in DAYS.findall(ctx)})
    kind = ("daily" if any(d in ("daily", "every day", "seven days", "7 days") for d in days)
            else "weekend" if days and all(d in ("saturday", "sunday", "weekend") for d in days)
            else "weekday" if days else "unspecified")
    return {"hours_open": o, "hours_close": c, "hours_days": kind, "hours_ranges": len(ms),
            "hours_text": m.group(0)}


BIKE = re.compile(rf"(?i){NUMTOK}\s+class\s*(?P<cls>1|2|i{{1,2}})\b\s+bicycle")
PARK_MAX = re.compile(rf"(?i)no more than\s+{NUMTOK}\s+(?:off[\s-]*street\s+|accessory\s+|"
                      r"vehicular\s+|residential\s+)*(?:parking|vehicle|car)")
INCL_PCT = re.compile(r"(?i)required to provide\s+(\d{1,2}(?:\.\d+)?)\s*%\s+of the (?:proposed |total )?"
                      r"(?:dwelling )?units")
INCL_UNITS = re.compile(r"(?i)the project contains\s+(\d[\d,]*)\s+(?:dwelling\s+)?units;?\s*"
                        r"therefore,?\s+(\d[\d,]*)\s+affordable units")
AMI = re.compile(r"(?i)(\d{2,3})\s*%\s+of\s+(?:the\s+)?area median income")
FEET = re.compile(rf"(?i){NUMTOK}\s*[-\s]?(?:foot|feet|ft\.?)\b")
NOTICE_RADIUS = re.compile(rf"(?i)within\s+{NUMTOK}\s*(?:foot|feet|ft\.?)\b")
NOTICE_DAYS = re.compile(rf"(?i){NUMTOK}\s+(?:calendar\s+|business\s+)?days?\s+(?:prior|before|in advance|"
                         r"notice|written notice|advance notice)")
INTERVAL = re.compile(r"(?i)\b(annual(?:ly)?|every year|each year|quarterly|semi[\s-]?annual(?:ly)?|"
                      r"every six months|every (?:two|2|three|3|five|5) years|biennial(?:ly)?|monthly)\b")
INTERVAL_MONTHS = {"annual": 12, "annually": 12, "every year": 12, "each year": 12, "quarterly": 3,
                   "semi annual": 6, "semi-annual": 6, "semiannual": 6, "semi annually": 6,
                   "semi-annually": 6, "semiannually": 6, "every six months": 6, "every two years": 24,
                   "every 2 years": 24, "biennial": 24, "biennially": 24, "every three years": 36,
                   "every 3 years": 36, "every five years": 60, "every 5 years": 60, "monthly": 1}


def numbers_of(heading: str, body: str) -> dict:
    """Every figure this stage types, from one condition. Each field is read only from the
    conditions of its kind (by heading, or the body's own words where the era prints no
    heading), so a figure is never borrowed from a neighbouring subject."""
    h, b = str(heading or "").lower(), str(body or "")
    lead = (h + " " + b[:200].lower())
    out = {}
    kind = option_kind(heading, body)
    if kind == "validity":
        ds = durations(b)
        if ds:
            out.update(validity_months=float(ds[0]),
                       validity_text=DUR.search(b).group(0))
    if "hours of operation" in lead or re.search(r"(?i)hours of operation|operating hours|"
                                                 r"shall be (?:open|closed)|limited to the "
                                                 r"following hours", b[:400]):
        out.update(hours_of(b))
    if "bicycle" in lead:
        c1 = c2 = 0.0
        found = []
        for m in BIKE.finditer(b):
            n = _count(m)
            if n is None:
                continue
            found.append(m.group(0))
            if m.group("cls").lower() in ("1", "i"):
                c1 += n
            else:
                c2 += n
        if found:
            out.update(bike_class1=c1, bike_class2=c2, bike_text="; ".join(found)[:300])
    if "parking maximum" in h or ("parking" in lead and "no more than" in b.lower()):
        m = PARK_MAX.search(b)
        if m and _count(m) is not None:
            out.update(parking_max=_count(m), parking_max_text=m.group(0))
    if re.search(r"(?i)affordable|inclusionary|below market", lead):
        m = INCL_PCT.search(b)
        if m:
            out.update(incl_pct=float(m.group(1)), incl_pct_text=m.group(0))
        m = INCL_UNITS.search(b)
        if m:
            out.update(units_total=float(m.group(1).replace(",", "")),
                       units_affordable=float(m.group(2).replace(",", "")),
                       units_text=m.group(0))
        m = AMI.search(b)
        if m:
            out.update(incl_ami_pct=float(m.group(1)), incl_ami_text=m.group(0))
    if "screen" in h and "wts" not in h and "fcc" not in b.lower()[:300]:
        m = FEET.search(b)
        if m and _count(m) is not None:
            out.update(screen_ft=_count(m), screen_text=m.group(0))
    if re.search(r"(?i)notif|notice|posted", lead):
        m = NOTICE_RADIUS.search(b)
        if m and _count(m) is not None:
            out.update(notice_radius_ft=_count(m), notice_radius_text=m.group(0))
        m = NOTICE_DAYS.search(b)
        if m and _count(m) is not None:
            out.update(notice_days=_count(m), notice_days_text=m.group(0))
    if re.search(r"(?i)monitor|report|periodic|certif", h):
        m = INTERVAL.search(b)
        if m:
            k = re.sub(r"\s+", " ", m.group(1).lower())
            out.update(monitor_months=float(INTERVAL_MONTHS.get(k, INTERVAL_MONTHS.get(k.replace(" ", "-"), np.nan))),
                       monitor_text=m.group(0))
    return out


NUMERIC_FIELDS = [("validity_months", "validity period (months)"),
                  ("hours_open", "opening hour (24h)"), ("hours_close", "closing hour (24h; +24 past midnight)"),
                  ("bike_class1", "bicycle spaces, Class 1"), ("bike_class2", "bicycle spaces, Class 2"),
                  ("parking_max", "parking maximum (spaces)"), ("incl_pct", "inclusionary share stated (%)"),
                  ("units_total", "dwelling units stated"), ("units_affordable", "affordable units stated"),
                  ("incl_ami_pct", "income level stated (% of AMI)"), ("screen_ft", "screening dimension (feet)"),
                  ("notice_radius_ft", "notice radius (feet)"), ("notice_days", "notice period (days)"),
                  ("monitor_months", "monitoring or reporting interval (months)")]


def numbers_stage():
    """conditions_numeric.parquet: the typed figures of every canonical condition."""
    c = pd.read_parquet(COND_LONG.with_suffix(".parquet"))
    c = c[c.canonical & c.part.eq("conditions")].copy()
    rows = []
    for r in c.itertuples():
        v = numbers_of(r.heading, r.body)
        if v:
            rows.append({"doc_id": r.doc_id, "section": r.section, "ordinal": r.ordinal,
                         "case_key": r.case_key, "motion_no": r.motion_no, "doc_type": r.doc_type,
                         "hearing_date": r.hearing_date, "adoption_date": r.adoption_date,
                         "heading": r.heading, **v})
    d = pd.DataFrame(rows)
    d["year"] = pd.to_numeric(d.hearing_date.str[:4].where(d.hearing_date.ne(""),
                                                           d.adoption_date.str[:4]), errors="coerce")
    d.to_parquet(NUMERIC, index=False)
    print(f"{len(d):,} conditions with a typed figure → {NUMERIC}")
    print(d[[f for f, _ in NUMERIC_FIELDS]].notna().sum().to_string())
    return d



NUMERIC_SPOTCHECK = 60        # values read against their matched sentence when the stage was written


def numbers_report(a, M: dict):
    """The two pages Part 6 asks for: every typed figure's distribution, and how it moved by
    hearing year. Reads conditions_numeric.parquet (the `numbers` stage)."""
    if not NUMERIC.exists():
        return
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    d = pd.read_parquet(NUMERIC)
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The figures inside the conditions, typed: one row per field, over the canonical "
      r"conditions that state it. `Conditions' counts those carrying the field; the years are the "
      r"first and last hearing year seen; the statistics are over the values. Hours are on a "
      r"24-hour clock, a closing time after midnight written past 24 (2 a.m.\ is 26).}"
      r"\label{tab:numeric}")
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{lrrrrrrrl}\toprule")
    a(r"Field & Conditions & Years & p10 & Median & p90 & Mean & Distinct & Most common\\\midrule")
    for f, lab in NUMERIC_FIELDS:
        x = d[f].dropna()
        if not len(x):
            a(rf"{lab} & 0 & & & & & & & \\")
            continue
        yrs = d.loc[x.index, "year"].dropna()
        mc = x.value_counts()
        g = lambda v: f"{v:,.1f}".rstrip("0").rstrip(".").replace(",", "{,}")
        a(rf"{lab} & {f0(len(x))} & {int(yrs.min()) if len(yrs) else ''}--{int(yrs.max()) if len(yrs) else ''} & "
          rf"{g(x.quantile(.1))} & {g(x.median())} & {g(x.quantile(.9))} & {g(x.mean())} & "
          rf"{f0(x.nunique())} & {g(mc.index[0])} ({100*mc.iloc[0]/len(x):.0f}\%)\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")
    # the figure: median and interquartile band by hearing year, one panel per field
    show = [(f, lab) for f, lab in NUMERIC_FIELDS if d[f].notna().sum() >= 50]
    n = len(show)
    cols = 3
    rows_ = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows_, cols, figsize=(7.4, 1.9 * rows_), sharex=True)
    for ax, (f, lab) in zip(axes.flat, show):
        g = d.dropna(subset=[f, "year"]).groupby("year")[f]
        k = g.size()
        ok = k.index[k >= MIN_NUMERIC_YEAR]
        med, lo, hi = g.median()[ok], g.quantile(.25)[ok], g.quantile(.75)[ok]
        ax.fill_between(ok, lo, hi, color="#5b7fa6", alpha=0.25, lw=0)
        ax.plot(ok, med, color="#5b7fa6", lw=1.6)
        ax.set_title(lab, fontsize=7, loc="left")
        ax.tick_params(labelsize=6.5)
    for ax in list(axes.flat)[n:]:
        ax.axis("off")
    fig.suptitle(f"The figures inside the conditions by hearing year: median and interquartile "
                 f"band; years with fewer than {MIN_NUMERIC_YEAR} conditions stating the field not "
                 f"drawn", fontsize=7.5, x=0.02, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(FIG2 / "fig_numeric.pdf")
    plt.close(fig)
    M.update({"ccNumConditions": N(len(d)), "ccNumSpot": str(NUMERIC_SPOTCHECK),
              "ccNumMinYear": str(MIN_NUMERIC_YEAR),
              "ccNumValidity": N(d.validity_months.notna().sum()),
              "ccNumHours": N(d.hours_open.notna().sum()),
              "ccNumHoursLate": f"{100*(d.hours_close.dropna() > 24).mean():.0f}",
              "ccNumBike": N(d.bike_class1.notna().sum()),
              "ccNumParkMax": N(d.parking_max.notna().sum()),
              "ccNumInclPct": N(d.incl_pct.notna().sum()),
              "ccNumInclPctFirst": str(int(d.loc[d.incl_pct.notna(), "year"].min())) if d.incl_pct.notna().any() else "---",
              "ccNumInclPctMode": f"{d.incl_pct.mode().iloc[0]:g}" if d.incl_pct.notna().any() else "---",
              "ccNumUnits": N(d.units_total.notna().sum()),
              "ccNumScreen": N(d.screen_ft.notna().sum()),
              "ccNumNotice": N(d.notice_radius_ft.notna().sum()),
              "ccNumMonitor": N(d.monitor_months.notna().sum())})


MIN_NUMERIC_YEAR = 10


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover")
    d.add_argument("--refresh", action="store_true")
    d.add_argument("--steps", nargs="*", choices=STEPS)
    d.add_argument("--max-pages", type=int, default=600)
    p = sub.add_parser("probe")
    p.add_argument("--limit", type=int, help="at most this many candidates per host (a test)")
    p.add_argument("--hosts", nargs="*")
    u = sub.add_parser("pull")
    u.add_argument("--n", type=int, help="pull at most this many documents this run")
    u.add_argument("--max-mb", type=int, default=60,
                   help="skip documents larger than this; raise it for the tail")
    u.add_argument("--only", choices=["motion", "packet", "html"])
    u.add_argument("--host", help="pull only from this host (run one process per host)")
    u.add_argument("--years", type=lambda v: tuple(int(x) for x in v.split("-")),
                   help="only targets first heard in this span, e.g. 2022-2026")
    u.add_argument("--loop-minutes", type=float, default=0,
                   help="keep pulling new hits as the probe finds them, re-reading the census "
                        "every this many minutes, until the probe has finished")
    sub.add_parser("parse")
    rx = sub.add_parser("reextract")
    rx.add_argument("--network", action="store_true",
                    help="re-download documents whose whole PDF was not kept")
    for c in ("diff", "gold", "summary", "report", "numbers", "records"):
        sub.add_parser(c)
    for c in ("freeze", "sample", "score", "archive"):
        x = sub.add_parser(c)
        x.add_argument("--round", type=int, default=ROUND)
    a = ap.parse_args()
    if a.cmd == "discover":
        discover(a.refresh, a.steps, a.max_pages)
    elif a.cmd == "probe":
        probe(a.limit, a.hosts)
    elif a.cmd == "pull":
        while True:
            pull(a.n, a.max_mb, a.only, a.host, a.years)
            if not a.loop_minutes:
                break
            log = STORE / "probe.log"
            if log.exists() and "probe done" in log.read_text()[-2000:] and \
                    not _pending(a.max_mb, a.only, a.host):
                break
            time.sleep(60 * a.loop_minutes)
    elif a.cmd == "parse":
        parse_docs()
    elif a.cmd == "gold":
        gold_select()
    elif a.cmd == "freeze":
        freeze(a.round)
    elif a.cmd == "sample":
        sample_check(a.round)
    elif a.cmd == "score":
        score(a.round)
    elif a.cmd == "archive":
        archive_round(a.round)
    elif a.cmd == "summary":
        summary()
    elif a.cmd == "reextract":
        reextract(a.network)
    elif a.cmd == "diff":
        diff_stage()
    elif a.cmd == "report":
        report_content()
    elif a.cmd == "numbers":
        numbers_stage()
    elif a.cmd == "records":
        records_request()


# ═══════════════════════════════════════════════════════════════════════════
# the records request package (next-phase brief, Part 7): a document to send, not analysis
# ═══════════════════════════════════════════════════════════════════════════
RR_DIR = HERE.parents[1] / "output" / "planning_commission_project" / "records_request"
CPRA = "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=GOV&sectionNum=7920.000"
CPRA_TIME = "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=GOV&sectionNum=7922.535"


def records_request():
    """records_request.md and its two attachment CSVs: the conditioned items heard before 2010
    whose adopted motion no address answered for, with and without a motion number. Every count
    in the letter is computed here from the census; the legal citations are quoted from
    leginfo and checked."""
    import build_exaction_panel as bx
    RR_DIR.mkdir(parents=True, exist_ok=True)
    it = content_frames()[0]
    it["year"] = pd.to_numeric(it.year, errors="coerce")
    cond = it[it.flag]
    res = residual(it, cond)
    early = res[res.year < 2010].copy()
    cols = {"cn": "case_number", "action_instrument_no": "motion_number", "meeting_date": "hearing_date",
            "request_type": "request_type", "project_address": "project_address",
            "assessor_block": "block", "lot_number": "lot"}
    early["meeting_date"] = pd.to_datetime(early.meeting_date).dt.date.astype(str)
    sel = early[list(cols) + ["why"]].rename(columns=cols)
    fmt = lambda v: ";".join(map(str, v)) if isinstance(v, (list, tuple, set, np.ndarray)) else (
        "" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v).strip("[]").replace(", ", ";"))
    sel["lot"] = sel.lot.map(fmt)
    sel["block"] = sel.block.map(fmt)
    num = sel[sel.why.eq("none found, number")][list(cols.values())]
    nonum = sel[sel.why.eq("none found, no number")][
        ["case_number", "hearing_date", "request_type", "project_address", "block", "lot"]]
    num = num.sort_values(["hearing_date", "case_number"])
    nonum = nonum.sort_values(["hearing_date", "case_number"])
    num.to_csv(RR_DIR / "pre2010_with_motion_number.csv", index=False)
    nonum.to_csv(RR_DIR / "pre2010_without_motion_number.csv", index=False)
    a = load_census()
    fam = a.groupby("family").agg(tried=("candidate_url", "nunique"),
                                  found=("status", lambda s: int(s.eq("200").sum())))
    sj = STORE / "census_summary.json"
    summ = json.loads(sj.read_text()) if sj.exists() else {}
    g = lambda k: str(summ.get(k, "?")).replace("{,}", ",")
    ok_cpra = bx.quote_found("This division shall be known and may be cited as the California Public Records Act", CPRA)
    ok_time = bx.quote_found("within 10 days from receipt of the request, determine whether the request", CPRA_TIME)
    first, last = early.meeting_date.min(), early.meeting_date.max()
    lines = [
        "# Records request: adopted Planning Commission motions, hearings before 2010",
        "",
        f"*Draft for Dan Post to edit and send. Nothing has been sent. Generated by "
        f"`collect_conditions.py records` on {pd.Timestamp.now().date()} from the conditions census; every "
        f"count below is computed from the census files, and the two statutory citations were "
        f"checked against leginfo ({'verified' if ok_cpra and ok_time else 'NOT verified'}).*",
        "",
        "---",
        "",
        "To: Custodian of Records, San Francisco Planning Department  ",
        "[address / email for public records requests — to be filled in]",
        "",
        "From: Dan Post  ",
        "[affiliation, mailing address, email — to be filled in]",
        "",
        f"Re: Request under the California Public Records Act (Gov. Code § 7920.000 et seq.) for "
        f"adopted Planning Commission motions, {first[:4]}–{last[:4]}",
        "",
        "Dear Custodian of Records,",
        "",
        "I am a researcher studying the San Francisco Planning Commission's decisions on land use "
        "since 1998. I have assembled, from the Commission's published minutes, a list of every "
        "item the Commission heard, and from the Department's own web hosts the adopted motions "
        "and conditions of approval for most of them. For items heard before 2010 the adopted "
        "motion is almost never online. I am writing to request copies of those motions.",
        "",
        "## What I am requesting",
        "",
        f"1. **Adopted motions with a known motion number.** For each of the {len(num):,} items "
        f"listed in the attached `pre2010_with_motion_number.csv` (case number, motion number and "
        f"hearing date, heard {num.hearing_date.min()} to {num.hearing_date.max()}), the adopted "
        f"Planning Commission motion, including its Exhibit A (conditions of approval), in "
        f"whatever form the Department holds it (paper, microfilm, scanned image or the case file).",
        f"2. **Adopted motions without a recorded motion number.** For each of the {len(nonum):,} "
        f"items listed in `pre2010_without_motion_number.csv` (case number and hearing date), the "
        f"same, or, if the Commission adopted no motion for the item, a note to that effect.",
        "",
        "Each of these items is one the minutes record as approved with conditions. I am not "
        "requesting staff reports, correspondence or other parts of the case file unless the "
        "adopted motion and its exhibits exist only within them.",
        "",
        "If it would reduce the burden, I would welcome (a) electronic copies of whatever is "
        "already scanned, first; (b) access to inspect the paper records in person; or (c) a "
        "conversation about narrowing the request, for example to a sample of years. I am happy "
        "to pay reasonable duplication costs; please tell me in advance if they will exceed "
        "[amount].",
        "",
        "## What I have already searched",
        "",
        "So that this request does not duplicate records that are publicly available, here is "
        "what I searched before writing. For every case number and motion number in the minutes "
        f"I constructed the addresses at which the Department has published motions and packets, "
        f"and requested each one: {int(fam.tried.sum()):,} addresses in all, of which "
        f"{int(fam.found.sum()):,} returned a document.",
        "",
        "| Where | Addresses tried | Documents found |",
        "|---|---:|---:|",
    ]
    FAMILY_LABEL = {
        "cpcmotions_year": "commissions.sfplanning.org motions by year", "cpcmotions_year_mirror":
        "the same keys on the Department's S3 mirror", "cpcmotions_ftp": "the old site's FTP tree (motions)",
        "cpcpackets": "hearing packets", "cpcpackets_mirror": "hearing packets, S3 mirror",
        "cpcpackets_ftp": "hearing packets, old FTP tree", "citypln": "citypln-m-extnl.sfgov.org",
        "minutes_vault": "the minutes vault", "oldsite_modules": "the old site's document modules",
        "cpcdra": "discretionary-review actions", "cpcdra_mirror": "discretionary-review actions, S3 mirror",
        "cpcdra_ftp": "discretionary-review actions, old FTP tree", "f_motion": "other motion URL patterns"}
    for f, r in fam.sort_values("tried", ascending=False).iterrows():
        lines.append(f"| {FAMILY_LABEL.get(f, f)} | {int(r.tried):,} | {int(r.found):,} |")
    lines += [
        "",
        f"I also collected every document address linked from the minutes themselves "
        f"({g('ccMinutesLinks')} links), from {g('ccHearingPages')} archived hearing pages and "
        f"{g('ccOldsitePages')} pages of the pre-2003 city site in the Wayback Machine "
        f"({g('ccOldsiteLinks')} links), and from the Wayback Machine's index of the Department's "
        f"hosts ({g('ccCdxUrls')} archived addresses under {g('ccCdxPrefixes')} prefixes): "
        f"{g('ccDiscoveredUrls')} distinct addresses in all. None answered for the items listed.",
        "",
        "## Timing",
        "",
        "I understand that the Act asks the Department to determine within 10 days of receipt "
        "whether the request seeks disclosable records, and to estimate when they will be "
        "available (Gov. Code § 7922.535). Given the number of items, I would be grateful for a "
        "rolling production.",
        "",
        "Thank you for your help.",
        "",
        "Sincerely,  ",
        "Dan Post",
        "",
        "---",
        "",
        "## Separate: questions for the Assessor-Recorder on Recorded Notices of Special Restrictions",
        "",
        "*Not part of the Planning request. To be sent, if at all, to the Office of the "
        "Assessor-Recorder. Based on a read-only check of the Recorder's public pages on "
        "2026-09-11 (`external/cpc_packets/nsr_feasibility.md`); nothing was searched or downloaded.*",
        "",
        "Conditions of approval are in some cases recorded against the parcel as a Notice of "
        "Special Restrictions (NSR); where one was recorded it would give the conditions for an "
        "item whose motion cannot be found. The public pages leave four questions open:",
        "",
        "1. Is \"Notice of Special Restrictions\" (or an equivalent) a selectable document type in "
        "the online official-records index? The search configuration listing document types is "
        "not served to an anonymous session, so this could not be confirmed.",
        "2. Is bulk or automated access to the index permitted — for example, an index extract "
        "by document type and date range, or a list of NSRs by Assessor's Parcel Number? The "
        "disclaimer says nothing about it, and the Terms of Service are readable only with an "
        "account.",
        "3. The online index begins January 1, 1990, and earlier documents can be searched only in "
        "the office. What are the terms for pre-1990 documents — is there an index by document "
        "type, and can NSRs be retrieved in batches?",
        "4. For a research project needing on the order of "
        f"{len(num) + len(nonum):,} documents, is there a research or bulk rate instead of the "
        "per-document online price, and does image purchase require the identity-verified "
        "registration the site describes?",
        "",
        "---",
        "",
        "## Attachments",
        "",
        f"- `pre2010_with_motion_number.csv` — {len(num):,} rows: case number, motion number, "
        f"hearing date, request type, address, block, lot.",
        f"- `pre2010_without_motion_number.csv` — {len(nonum):,} rows: case number, hearing date, "
        f"request type, address, block, lot.",
        "",
        f"*Scope note for Dan (delete before sending): the brief estimated about 1,040 items with a "
        f"motion number and about 590 without; the completed census gives {len(num):,} and "
        f"{len(nonum):,}. The brief's count of addresses (50,366) predates the full probe, which "
        f"tried {int(fam.tried.sum()):,}. Items are those the item table flags as conditioned, heard "
        f"before 2010, for which no probed or discovered address keyed to the case or motion "
        f"number returned a document.*",
    ]
    (RR_DIR / "records_request.md").write_text("\n".join(lines) + "\n")
    print(f"{len(num):,} with a motion number, {len(nonum):,} without → {RR_DIR}")


if __name__ == "__main__":
    main()
