#!/usr/bin/env python3
"""
analyze_conditions.py
---------------------
Purpose : Can we recover *what* the Commission conditioned, not merely *that* it did?
          `conditions_imposed` is a yes/no flag (median length: three characters), so the
          content has to come from somewhere else. This script measures how far the
          instrument number reaches, shows that the minutes themselves never enumerate a
          condition, and tests the one route that does work — the Commission packet PDF,
          which carries the draft motion and its Exhibit A, Conditions of Approval.
Inputs  : $MFHR_DATA_ROOT/extraction/<RUN>/clean/*.jsonl
          labels.db (items.block_text) for the regex sweep over the source blocks
          https://commissions.sfplanning.org/cpcpackets/<case_number>.pdf
Outputs : output/planning_commission_project/conditions_of_approval/figures/*.pdf
          output/planning_commission_project/conditions_of_approval/tables/condition_tables.tex
          $MFHR_DATA_ROOT/external/cpc_packets/availability.csv     (HEAD probe cache)
          $MFHR_DATA_ROOT/external/cpc_packets/conditions/<case>.txt (extracted Exhibit A)
Author  : Dan Post
Created : 2026-09-08

Usage
-----
  python analyze_conditions.py probe   [--per-year 20]   # is a packet published?
  python analyze_conditions.py fetch   [--n 150]         # pull + extract Exhibit A
  python analyze_conditions.py report                    # figures + tables

Notes
-----
Both network stages cache to disk and skip what they already have, so `report` is cheap and
re-runnable and neither stage is repeated by accident. Packets are 2--19 MB; only the
extracted conditions text is kept.

The packet is the document published *before* the hearing, so its motion is a DRAFT and its
conditions are what staff proposed, not necessarily what the Commission adopted. That is a
real limitation and the memo says so; it is not a reason to skip the measurement, because
the templated skeleton is identical either way and the Commission's own amendments are what
`modifications` in the item table already records.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import random
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from paths import DATA_ROOT                                          # noqa: E402

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                      # noqa: E402

RUN = "corpus_v2_g3"
MEMO = HERE.parents[1] / "output" / "planning_commission_project" / "conditions_of_approval"
FIG, TAB = MEMO / "figures", MEMO / "tables"
STORE = DATA_ROOT / "external" / "cpc_packets"
AVAIL = STORE / "availability.csv"
COND = STORE / "conditions"
LABELS_DB = HERE / "labeling_app" / "labels.db"

# Two hosts publish the per-case Commission packet, and neither covers the whole period.
# The S3-backed one is keyed on the case number alone; the city one is keyed on the case
# number *under the hearing date*, which the item table supplies. Try both.
PACKET_ROUTES = [
    ("s3", "https://commissions.sfplanning.org/cpcpackets/{case}.pdf"),
    ("citypln", "https://citypln-m-extnl.sfgov.org/Commissions/CPC/{m}_{d}_{y}/"
                "Commission%20Packet/{case}.pdf"),
]
UA = "market-for-housing-regulation/analyze_conditions (research)"
PAUSE = 0.4                                        # polite spacing; no API, a plain web host
# Packets run from 0.5 MB to 90 MB; the large ones are scanned plan sets whose pages take
# pdfplumber minutes apiece and which never contain an Exhibit A anyway. The probe already
# recorded content-length, so skip them by size rather than discovering it the hard way.
MAX_PACKET_BYTES = 40_000_000

plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                     "figure.dpi": 150, "savefig.bbox": "tight"})


def load_items() -> pd.DataFrame:
    rows = []
    for f in sorted(glob.glob(str(DATA_ROOT / "extraction" / RUN / "clean" / "*.jsonl"))):
        rows += [json.loads(l) for l in open(f)]
    df = pd.DataFrame(rows)
    df["meeting_date"] = pd.to_datetime(df.meeting_date, errors="coerce")
    df["year"] = df.meeting_date.dt.year.fillna(df.year).astype(int)
    df["cn"] = df.case_number.astype(str).str.replace(r"\s+", "", regex=True).str.upper()
    df["has_instrument"] = df.action_instrument.astype(str).str.strip().ne("")
    df["instrument_no"] = pd.to_numeric(df.action_instrument_no, errors="coerce").fillna(0)
    df["has_number"] = df.instrument_no > 0
    df["conditions"] = df.conditions_imposed.astype(str).str.strip().str.lower().eq("yes")
    df["modifications_text"] = df.modifications.astype(str).str.strip().ne("")
    df["project_mod"] = df.project_modified.astype(str).str.strip().str.lower().eq("yes")
    return df


def sess() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    return s


# ── stage 1: is a packet published for this case? ────────────────────────────
def probe(per_year: int, seed: int = 7):
    """HEAD a stratified random sample of case numbers, one sample per hearing year, and
    cache the verdict. Sampling rather than enumerating: 8,987 cases is more requests than
    a coverage question needs, and the sample is the estimate the memo reports."""
    it = load_items()
    STORE.mkdir(parents=True, exist_ok=True)
    done = {}
    if AVAIL.exists():
        done = {r["case_number"]: r for r in csv.DictReader(AVAIL.open())
                if r.get("route")}                      # rows from a one-route probe are stale
    rnd = random.Random(seed)
    want = []
    for y, g in it[it.cn.str.len() > 4].groupby("year"):
        cases = sorted(set(g.cn))
        pick = rnd.sample(cases, min(per_year, len(cases)))
        # the citypln route needs a hearing date, so carry the earliest one for each case
        dates = g.groupby("cn").meeting_date.min()
        want += [(y, c, dates[c]) for c in pick]
    s = sess()
    rows = list(done.values())
    todo = [(y, c, d) for y, c, d in want if c not in done]

    def save():                                     # write as we go: a probe is slow enough
        with AVAIL.open("w", newline="") as f:      # that losing it to an interrupt hurts
            w = csv.DictWriter(f, fieldnames=["case_number", "year", "date", "found",
                                              "route", "bytes"])
            w.writeheader()
            w.writerows(rows)

    for i, (y, cn, dt) in enumerate(todo, 1):
        route, size = "", ""
        for name, url in PACKET_ROUTES:
            u = url.format(case=cn, m=dt.month, d=dt.day, y=dt.year)
            try:
                r = s.head(u, timeout=20, allow_redirects=True)
            except requests.RequestException:
                continue
            finally:
                time.sleep(PAUSE)
            if r.status_code == 200:
                route, size = name, r.headers.get("content-length") or ""
                break
        rows.append({"case_number": cn, "year": y, "date": dt.date().isoformat(),
                     "found": int(bool(route)), "route": route or "none", "bytes": size})
        if i % 25 == 0:
            save()
            print(f"  [{i}/{len(todo)}] {y} {cn} → {route or 'none'}", flush=True)
    save()
    n = sum(int(r["found"]) for r in rows)
    print(f"probed {len(rows):,} cases, {n:,} packets found ({100*n/len(rows):.1f}%) → {AVAIL}")
    print(Counter(r["route"] for r in rows))


# ── stage 2: pull the packet, keep only Exhibit A ────────────────────────────
# Exhibit A opens with this heading in every motion seen. A packet for a discretionary
# review has no motion and no Exhibit A --- a DR produces a Discretionary Review Action, not
# a conditioned authorisation --- so an empty extraction there is the right answer, not a
# parser failure. `packet_kinds()` reports which is which.
START = re.compile(r"(?i)conditions of approval,?\s*(?:compliance|\n)")
# What START finds is a *conditions section*. In a conditional-use motion that section is
# Exhibit A and its conditions are numbered and named; on a legislative item or a resolution
# the same heading can introduce prose about someone else's conditions. The tables therefore
# report the section and the count of numbered conditions separately, and never treat the
# presence of the heading as proof of a motion.
STOP = re.compile(r"(?i)^\s*(exhibit\s+b\b|attachment\s+checklist|parcel\s+map\b)")
TITLE = re.compile(r"(?m)^\s*(\d{1,2})\.\s+([A-Z][A-Za-z0-9 ,/&'’\-]{2,58})[.:]")
HEADING = re.compile(r"(?m)^([A-Z][A-Za-z\- ]{3,45})\s*$")


def extract_conditions(pdf_path: Path) -> str:
    """The conditions live in the draft motion's Exhibit A, which opens with the heading
    'Conditions of Approval, Compliance, Monitoring, and Reporting'. Take from there to the
    next exhibit, over at most the first 60 pages (the plans and photos that follow are the
    bulk of the file and carry no text we want)."""
    import pdfplumber
    full = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages[:60]:
            full += (page.extract_text() or "") + "\n"
            # pdfplumber caches every page's objects on the parent; on a 200 MB scanned
            # packet that grows until the process is killed. Drop each page as we pass it.
            page.flush_cache()
            page.get_textmap.cache_clear()
            m = START.search(full)
            if m:                                   # stop as soon as the exhibit has ended:
                tail = full[m.start():]             # the rest of a packet is plans and photos
                stop = STOP.search(tail, 200)
                if stop:
                    return tail[:stop.start()]
    m = START.search(full)
    return full[m.start():][:40_000] if m else ""


def fetch(n: int):
    COND.mkdir(parents=True, exist_ok=True)
    if not AVAIL.exists():
        sys.exit("run `probe` first")
    found = [r for r in csv.DictReader(AVAIL.open()) if r["found"] == "1"]
    todo = [r for r in found if not (COND / f"{r['case_number']}.txt").exists()][:n]
    print(f"{len(found)} packets known, {len(todo)} to fetch")
    it = load_items()
    # the probe records the date it used; for rows written before it did, reproduce its rule
    # (earliest hearing of that case *within the sampled year*) rather than the global
    # earliest, or the citypln URL --- which is keyed on the hearing date --- will 404
    by_year = it.groupby(["cn", "year"]).meeting_date.min()
    when = it.groupby("cn").meeting_date.min()
    urls = dict(PACKET_ROUTES)
    s = sess()
    tmp = STORE / "_tmp.pdf"
    skipped = failed = 0
    for i, r in enumerate(todo, 1):
        cn = r["case_number"]
        dt = (pd.Timestamp(r["date"]) if r.get("date") else
              by_year.get((cn, int(r["year"])), when[cn]))
        size = int(r["bytes"]) if str(r.get("bytes", "")).isdigit() else 0
        if size > MAX_PACKET_BYTES:
            skipped += 1                            # leave no file: "read" is then exactly
            continue                                # "a file exists", with nothing to infer
        try:
            resp = s.get(urls[r["route"]].format(case=cn, m=dt.month, d=dt.day, y=dt.year),
                         timeout=300)
            resp.raise_for_status()
            tmp.write_bytes(resp.content)
            txt = extract_conditions(tmp)
        except requests.RequestException as e:      # transport failure: leave no file, so
            print(f"  [{i}/{len(todo)}] {cn}: {type(e).__name__} — will retry", flush=True)
            failed += 1                             # the next run retries rather than
            continue                                # recording it as "no Exhibit A"
        except Exception as e:                      # a bad PDF is data, not a crash
            txt = ""
            print(f"  [{i}/{len(todo)}] {cn}: {type(e).__name__}", flush=True)
        (COND / f"{cn}.txt").write_text(txt)
        if i % 10 == 0:
            print(f"  [{i}/{len(todo)}] {cn}: {len(txt):,} chars")
        time.sleep(PAUSE)
    tmp.unlink(missing_ok=True)
    if skipped:
        print(f"skipped {skipped} packets over {MAX_PACKET_BYTES/1e6:.0f} MB")
    if failed:
        print(f"{failed} packets failed to download; re-run `fetch` to retry them")


# ── stage 3: do the minutes themselves ever enumerate a condition? ───────────
SWEEP = {
    "names a motion or resolution number":
        re.compile(r"(?i)\b(?:motion|resolution|dra)\s*(?:no\.?|#|:)\s*\d"),
    "says `conditions of approval'": re.compile(r"(?i)conditions? of approval"),
    "says `subject to the following conditions'":
        re.compile(r"(?i)subject to the following conditions"),
    "refers to an Exhibit A": re.compile(r"(?i)\bexhibit\s+a\b"),
    "carries an enumerated list of three or more items":
        re.compile(r"(?m)^\s*(?:1[.)]|\(1\))\s+\S.*\n(?:.*\n)*?\s*(?:3[.)]|\(3\))\s+\S"),
    "enumerates a condition (list + condition language)":
        re.compile(r"(?i)(?:conditions? of approval|following conditions)[\s\S]{0,400}?"
                   r"(?m:^\s*(?:1[.)]|\(1\))\s+\S)"),
}


def sweep_blocks() -> list[tuple[str, int, int]]:
    if not LABELS_DB.exists():
        print("labels.db not present; skipping the block sweep")
        return []
    con = sqlite3.connect(LABELS_DB)
    rows = [b or "" for (b,) in con.execute("SELECT block_text FROM items")]
    out = [(lab, sum(1 for b in rows if rx.search(b)), len(rows))
           for lab, rx in SWEEP.items()]
    return out


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe"); p.add_argument("--per-year", type=int, default=20)
    f = sub.add_parser("fetch")
    f.add_argument("--n", type=int, default=150)
    f.add_argument("--max-mb", type=int, default=MAX_PACKET_BYTES // 1_000_000,
                   help="skip packets larger than this (scanned plan sets are slow and "
                        "carry no Exhibit A); raise it to read them anyway")
    sub.add_parser("report")
    a = ap.parse_args()
    if a.cmd == "probe":
        probe(a.per_year)
    elif a.cmd == "fetch":
        globals()["MAX_PACKET_BYTES"] = a.max_mb * 1_000_000
        fetch(a.n)
    else:
        report()



# ── stage 4: the taxonomy ────────────────────────────────────────────────────
# Two families, because they cost a project in different ways. A condition that changes the
# PROJECT moves the physical or programmatic thing being built; a condition that changes its
# OBLIGATIONS leaves the building alone and attaches a duty, a payment or a monitoring
# regime. `project_modified` in the item table already flags the first family in principle.
PROJECT_CHANGING = {
    "Massing and envelope": r"(?i)\b(?:heights?|setbacks?|penthouses?|stor(?:y|ies)|envelopes?|"
                            r"bulk|massing|rear yards?|lightwells?|roof ?decks?|parapets?|"
                            r"dormers?|reduc\w*|elimin\w*|remov\w*)\b",
    "Unit count and mix": r"(?i)\b(?:dwelling units?|unit counts?|merge\w*|number of units|"
                          r"studios?|bedrooms?|density)\b",
    "Design and materials": r"(?i)\b(?:designs?|fa[çc]ades?|material\w*|windows?|fenestration|"
                            r"architectur\w*|colou?rs?|siding|articulation|signage|signs?|"
                            r"light\w*|screening|plan drawings?|mechanical equipment|rooftop|"
                            r"antennas?|landscape plan)\b",
    "Use and operations": r"(?i)\b(?:hours of operation|hours|capacity|seats?|patrons?|"
                          r"entertainment|alcohol|liquor|licens\w*|outdoor activity|odou?rs?|"
                          r"noise|emission\w*|installation|out of service|"
                          r"transfer of operation|crime)\b",
}
OBLIGATION_IMPOSING = {
    "Affordability": r"(?i)\b(?:affordab\w*|inclusionary|BMR|below market)\b",
    "Fees and exactions": r"(?i)\b(?:fees?|in-?lieu|impact fees?|exactions?|"
                          r"implementation costs?)\b",
    "Transport and parking": r"(?i)\b(?:TDM|transport\w* demand|bicycles?|bikes?|parking|"
                             r"transit|loading|curb cuts?|driveways?|sidewalks?|traffic)\b",
    "Open space and streetscape": r"(?i)\b(?:street ?trees?|landscap\w*|open space|"
                                  r"public realm|greening|gardens?|recycl\w*|garbage|"
                                  r"composting)\b",
    "Monitoring and reporting": r"(?i)\b(?:monitor\w*|report\w*|"
                                r"notice of special restrictions?|\bNSR\b|annual review|"
                                r"liaison|complian\w*|enforce\w*|revocation|recordation)\b",
    "Environmental mitigation": r"(?i)\b(?:mitigat\w*|MMRP|shadows?|wind|air quality|CEQA|"
                                r"archaeolog\w*|stormwater|excavation|emergency services)\b",
    "Historic preservation": r"(?i)\b(?:historic\w*|preservation|landmarks?|Article 1[01]|"
                             r"Certificates? of Appropriateness)\b",
    "Validity and timing": r"(?i)\b(?:expirations?|expire|extend|extensions?|"
                           r"performance period|validity|valid for|diligent pursuit|sunset|"
                           r"time limit)\b",
}
# Neither family: the boilerplate a motion opens and closes with. Counted separately so that
# "covered by the scheme" does not quietly credit the taxonomy for procedural furniture.
PROCEDURAL = {
    "Procedural and findings": r"(?i)\b(?:conformity with current law|general plan "
                               r"consistency|severab\w*|project descriptions?|"
                               r"statements? of authorship|public comment|site description|"
                               r"surrounding propert\w*|planning code section|"
                               r"authorizations?|findings?|present use)\b",
}
TAXONOMY = {**PROJECT_CHANGING, **OBLIGATION_IMPOSING, **PROCEDURAL}


def not_read(avail: pd.DataFrame) -> set[str]:
    """Cases the probe found a packet for but `fetch` has not opened --- because it is over
    the size cap, or because the download failed. They are not evidence that the packet has
    no Exhibit A and must be kept out of that denominator. `fetch` writes no file for them,
    so their absence is the record."""
    if not len(avail):
        return set()
    found = set(avail.loc[avail.found == 1, "case_number"])
    return {c for c in found if not (COND / f"{c}.txt").exists()}


def condition_titles(skip: set[str] = frozenset()) -> tuple[Counter, int, int]:
    """Numbered condition headings across the fetched Exhibit A sections. The motions number
    and *name* each condition ('7. Rooftop Mechanical Equipment.'), which is what makes the
    template testable at all."""
    titles, n_files, n_with = Counter(), 0, 0
    if not COND.exists():
        return titles, 0, 0
    for f in sorted(COND.glob("*.txt")):
        if f.stem in skip:
            continue
        n_files += 1
        txt = f.read_text()
        found = {m.group(2).strip() for m in TITLE.finditer(txt)}
        if found:
            n_with += 1
        titles.update(found)
    return titles, n_files, n_with


DBI_ID = "i98e-djp9"                # Building Permits
PLANNING_IDS = {"y673-d69b": "Planning Department Records --- Non-Projects",
                "qvu5-m3a2": "Planning Department Records --- Projects"}
COUNTS = STORE / "datasf_counts.json"


def datasf_counts() -> dict:
    """Row counts for the DataSF tables checked for condition text, plus the share of DBI
    permit descriptions that mention a Commission condition or motion at all. Cached: this
    is three API calls and the answer does not move."""
    if COUNTS.exists():
        return json.loads(COUNTS.read_text())
    s = sess()
    out = {}
    for ds, name in PLANNING_IDS.items():
        r = s.get(f"https://data.sfgov.org/resource/{ds}.json",
                  params={"$select": "count(1)"}, timeout=120)
        out[ds] = {"name": name, "rows": int(r.json()[0]["count_1"]), "condition_rows": None}
        time.sleep(PAUSE)
    # DBI is answered from the local cache the permit memo already built, using the same
    # function, so the two memos cannot report different numbers for the same question.
    import analyze_permits as ap
    dbi = ap.conditions_in_dbi(ap.load_dbi())
    out[DBI_ID] = {"name": "Building Permits (DBI)", "rows": dbi["rows"],
                   "condition_rows": dbi["condition_talk"]}
    COUNTS.parent.mkdir(parents=True, exist_ok=True)
    COUNTS.write_text(json.dumps(out, indent=2) + "\n")
    return out


SUFFIX_FAMILY = [
    (re.compile(r"(?i)(D|DD+|DRP|DRM|DRD)$"), "Discretionary review"),
    (re.compile(r"(?i)(CUA|C|CV|CM)$"), "Conditional use"),
    (re.compile(r"(?i)(VAR|V)$"), "Variance"),
    (re.compile(r"(?i)(ENV|E|EIR)$"), "Environmental"),
    (re.compile(r"(?i)(PCA|Z|ZR|GPA|CWP|MAP)$"), "Legislative"),
]


def packet_kinds(avail: pd.DataFrame) -> pd.DataFrame:
    """Which kinds of case actually yield an Exhibit A. A discretionary review produces a
    Discretionary Review Action, not a conditioned authorisation, and its packet is a staff
    analysis with no motion in it --- so an empty extraction there is the correct answer.
    Built from the probe rather than from the files on disk, so packets that were found but
    not read are attributed to a family instead of vanishing."""
    rows = []
    for cn in sorted(avail.loc[avail.found == 1, "case_number"]):
        f = COND / f"{cn}.txt"
        fam = next((lab for rx, lab in SUFFIX_FAMILY if rx.search(cn)), "Other")
        txt = f.read_text() if f.exists() else ""
        rows.append({"case_number": cn, "family": fam, "chars": len(txt),
                     "skipped": int(not f.exists()),
                     "exhibit_a": int(len(txt) > 0),
                     "titles": len({m.group(2).strip() for m in TITLE.finditer(txt)})})
    return pd.DataFrame(rows)


def classify(strings) -> pd.DataFrame:
    s = pd.Series(list(strings), dtype=str)
    return pd.DataFrame({k: s.str.contains(rx, regex=True) for k, rx in TAXONOMY.items()})


# ── figures ──────────────────────────────────────────────────────────────────
def fig_coverage(it: pd.DataFrame, avail: pd.DataFrame):
    by = it.groupby("year")
    cond = by.conditions.mean() * 100
    inst = by.has_number.mean() * 100
    fig, axes = plt.subplots(2, 1, figsize=(7.2, 5.4), sharex=True)
    ax = axes[0]
    for s, lab, c in ((cond, "item records conditions imposed", "#2f6f4f"),
                      (inst, "item records a motion / resolution number", "#5b7fa6")):
        ax.plot(s.index, s.values, color=c, alpha=0.25, lw=0.9)
        ax.plot(s.index, s.rolling(3, center=True, min_periods=1).mean(), color=c, lw=2.0,
                label=lab)
    ax.set_ylabel("% of items heard that year")
    ax.set_ylim(0, 75)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    ax.set_title("The flag is recorded; the document number is recorded; the text is not\n"
                 "(3-year centred mean over the faint annual series)", loc="left")
    ax = axes[1]
    share = {r: (avail.route.eq(r).groupby(avail.year).mean() * 100)
             for r in ("s3", "citypln")}
    yrs = sorted(set(share["s3"].index) | set(share["citypln"].index))
    s3 = share["s3"].reindex(yrs).fillna(0)
    cp = share["citypln"].reindex(yrs).fillna(0)
    ax.bar(yrs, s3.values, width=0.8, color="#5b7fa6",
           label="commissions.sfplanning.org")
    ax.bar(yrs, cp.values, width=0.8, bottom=s3.values, color="#b07d2b",
           label="citypln-m-extnl.sfgov.org")
    ax.set_ylabel("% of sampled cases with\na packet published")
    ax.set_xlabel("hearing year")
    ax.legend(frameon=False, fontsize=7.5, title="published at", title_fontsize=7.5)
    ax.set_title("Commission packets, which carry the draft motion and its Exhibit A",
                 loc="left")
    fig.savefig(FIG / "fig_conditions_coverage.pdf")
    plt.close(fig)


def fig_taxonomy(pk: pd.DataFrame, mo: pd.DataFrame, w: np.ndarray):
    """The packet series is weighted by how often each heading is imposed, not by distinct
    heading: the question is what share of the conditions a project receives fall in each
    category, and a heading used once should not count as much as one used fifty times."""
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    cats = list(TAXONOMY)
    y = np.arange(len(cats))[::-1]
    ax.barh(y + 0.19, [100 * w[pk[c].values].sum() / w.sum() for c in cats], height=0.38,
            color="#5b7fa6", label="conditions imposed by the packet motions (weighted)")
    ax.barh(y - 0.19, [100 * mo[c].mean() for c in cats], height=0.38, color="#b07d2b",
            label="modifications recorded in the minutes")
    ax.set_yticks(y)
    ax.set_yticklabels(cats, fontsize=8)
    for cut in (len(OBLIGATION_IMPOSING) + len(PROCEDURAL) - 0.5, len(PROCEDURAL) - 0.5):
        ax.axhline(cut, color="#999", lw=0.8, ls=":")
    ax.set_xlabel("% of conditions / modifications matching the category")
    ax.set_title(f"A {len(TAXONOMY)}-category scheme against the two sources of condition "
                 f"text\nStaff write the obligations; the Commission reshapes the building",
                 loc="left")
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    fig.savefig(FIG / "fig_condition_taxonomy.pdf")
    plt.close(fig)


# ── tables ───────────────────────────────────────────────────────────────────
def T(s):
    return str(s).replace("_", r"\_").replace("&", r"\&")


def write_tables(it, sweep, avail, titles, n_files, n_with, pk, mo, unreach, kinds, w, ext):
    L = ["% GENERATED BY analyze_conditions.py — do not edit by hand."]
    a = L.append

    v = it.conditions_imposed.astype(str).str.strip().str.lower()
    both = int((it.conditions & it.project_mod).sum())
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The three fields in the item table that bear on conditions, over all %s "
      r"items. Only the third holds text.}\label{tab:fields}"
      % f"{len(it):,}".replace(",", "{,}"))
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{lrrl}\toprule")
    a(r"Field & Items & Share & What it holds\\\midrule")
    a(rf"\texttt{{conditions\_imposed}} & {int(v.ne('').sum()):,} & "
      rf"{100*v.ne('').mean():.1f}\% & a flag: {int(v.eq('yes').sum()):,} \texttt{{yes}}, "
      rf"{int(v.eq('no').sum()):,} \texttt{{no}}, median {v[v.ne('')].str.len().median():.0f} "
      rf"characters\\")
    a(rf"\texttt{{project\_modified}} & {int(it.project_mod.sum()):,} & "
      rf"{100*it.project_mod.mean():.1f}\% & a flag; {both:,} items are both conditioned and "
      rf"modified\\")
    mt = it.loc[it.modifications_text, "modifications"].astype(str)
    a(rf"\texttt{{modifications}} & {int(it.modifications_text.sum()):,} & "
      rf"{100*it.modifications_text.mean():.1f}\% & \textbf{{free text}}, median "
      rf"{mt.str.len().median():.0f} characters\\")
    a(rf"\quad \emph{{\dots\ with no conditions flag set}} & "
      rf"{int((it.modifications_text & ~it.conditions).sum()):,} & & a coding question worth a "
      rf"pass\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{What the item table records about conditions, and how far the document "
      r"number reaches. `Instrument' is \texttt{action\_instrument}; the number is "
      r"\texttt{action\_instrument\_no}. Eras split at the 2015 change of document "
      r"format.}\label{tab:instrument}")
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{lrrrr}\toprule")
    a(r"Items & $N$ & Conditions flag set & Instrument named & Number present\\\midrule")
    era = np.where(it.year <= 2014, "1998--2014 (HTML)", "2015--2026 (PDF)")
    for lab, g in [("All", it)] + list(it.groupby(era)):
        a(rf"{lab} & {len(g):,} & {100*g.conditions.mean():.1f}\% & "
          rf"{100*g.has_instrument.mean():.1f}\% & {100*g.has_number.mean():.1f}\%\\")
    c = it[it.conditions]
    a(r"\midrule")
    a(rf"With conditions imposed & {len(c):,} & --- & {100*c.has_instrument.mean():.1f}\% & "
      rf"{100*c.has_number.mean():.1f}\%\\")
    a(rf"\quad \emph{{unreachable: conditions, no number}} & {unreach:,} & & & "
      rf"{100*unreach/len(c):.1f}\% \emph{{of them}}\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{A regex sweep of all %s source blocks --- the same text the extraction read. "
      r"The minutes name the document that holds the conditions; they almost never hold the "
      r"conditions.}\label{tab:sweep}" % f"{sweep[0][2]:,}".replace(",", "{,}"))
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{lrr}\toprule")
    a(r"The block \dots & Blocks & Share\\\midrule")
    for lab, n, tot in sweep:
        a(rf"{lab} & {n:,} & {100*n/tot:.2f}\%\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Is a Commission packet published for the case? A stratified random sample of "
      r"20 cases per hearing year, HEAD-probed against both hosts. The packet carries the "
      r"staff report and the draft motion, whose Exhibit A is the conditions of "
      r"approval.}\label{tab:packets}")
    a(r"\begin{tabular}{lrrrr}\toprule")
    a(r"Hearing years & Probed & Packet found & Rate & Host\\\midrule")
    bands = [(1998, 2009), (2010, 2010), (2011, 2016), (2017, 2021), (2022, 2026)]
    for lo, hi in bands:
        g = avail[(avail.year >= lo) & (avail.year <= hi)]
        if not len(g):
            continue
        hosts = ", ".join(sorted(set(g[g.found == 1].route))) or "---"
        a(rf"{lo}--{hi} & {len(g):,} & {int(g.found.sum()):,} & "
          rf"{100*g.found.mean():.0f}\% & \texttt{{{T(hosts)}}}\\")
    m = avail[avail.year >= 2011]
    a(rf"\midrule 2011--2026 & {len(m):,} & {int(m.found.sum()):,} & "
      rf"\textbf{{{100*m.found.mean():.1f}\%}} & \\")
    a(rf"All years & {len(avail):,} & {int(avail.found.sum()):,} & "
      rf"{100*avail.found.mean():.0f}\% & \\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Of the packets pulled, which kinds of case carry a conditions section --- in "
      r"a conditional-use motion, Exhibit A. A discretionary review normally does not: its "
      r"packet is a staff analysis and its outcome is a Discretionary Review Action, not a "
      r"conditioned authorisation, so the empty cell is the correct answer rather than a "
      r"parser failure. `Not read' is packets the probe found but \texttt{fetch} has not "
      r"opened; they are excluded from the rate rather than counted as "
      r"negatives.}\label{tab:kinds}")
    a(r"\begin{tabular}{lrrrrr}\toprule")
    a(r"Case family & Pulled & Not read & Read & Conditions section & Median numbered\\"
      r"\midrule")
    for fam, g in kinds.groupby("family"):
        read = g[g.skipped == 0]
        med = read.loc[read.exhibit_a == 1, "titles"].median()
        a(rf"{T(fam)} & {len(g):,} & {int(g.skipped.sum()):,} & {len(read):,} & "
          rf"{int(read.exhibit_a.sum()):,} & "
          rf"{'---' if pd.isna(med) else f'{med:.0f}'}\\")
    rd = kinds[kinds.skipped == 0]
    a(rf"\midrule All & {len(kinds):,} & {int(kinds.skipped.sum()):,} & {len(rd):,} & "
      rf"{int(rd.exhibit_a.sum()):,} & \\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The twenty most frequent condition headings across the %d packets read, of "
      r"which %d yielded at least one numbered condition heading. The Commission's "
      r"conditions are written from a template, and the template is the "
      r"finding.}\label{tab:titles}" % (n_files, n_with))
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{lrlr}\toprule")
    a(r"Condition & Packets & Condition & Packets\\\midrule")
    # Ties broken by name: `most_common` keeps insertion order, and insertion order here is
    # the iteration order of a set of strings, which Python randomises per run --- so the
    # table reordered its tied rows every time it was regenerated.
    top = sorted(titles.items(), key=lambda kv: (-kv[1], kv[0]))[:20]
    for i in range(0, len(top), 2):
        left = rf"{T(top[i][0])} & {top[i][1]:,}"
        right = (rf"{T(top[i+1][0])} & {top[i+1][1]:,}" if i + 1 < len(top) else " & ")
        a(left + " & " + right + r"\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{A thirteen-category scheme, tested against the two sources of condition text "
      r"that exist: the numbered condition headings of the packet motions (%d distinct "
      r"headings over %d occurrences) and the \texttt{modifications} field of the item table "
      r"($N=%d$ items). Headings are reported both by distinct heading and weighted by how "
      r"often each is imposed; the weighted column is the share of \emph{conditions} the "
      r"scheme reaches. Categories are indicators, not a partition: one condition can carry "
      r"two.}\label{tab:taxonomy}" % (len(pk), int(w.sum()), len(mo)))
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{llrrr}\toprule")
    a(r"Family & Category & \multicolumn{2}{c}{Packet condition headings} & Minutes "
      r"modifications\\")
    a(r"\cmidrule(lr){3-4} & & distinct & weighted & \\\midrule")
    fams = (("Changes the project", PROJECT_CHANGING),
            ("Changes its obligations", OBLIGATION_IMPOSING),
            ("Neither", PROCEDURAL))
    def wt(mask):                       # weighted by how often the heading is imposed
        return 100 * w[mask.values].sum() / w.sum()

    for i, (fam, keys) in enumerate(fams):
        for j, k in enumerate(keys):
            a(rf"{fam if j == 0 else ''} & {T(k)} & {100*pk[k].mean():.1f}\% & "
              rf"{wt(pk[k]):.1f}\% & {100*mo[k].mean():.1f}\%\\")
        if i < len(fams) - 1:
            a(r"\midrule")
    a(r"\midrule")
    for lab, keys in (("Any project-changing", list(PROJECT_CHANGING)),
                      ("Any obligation-imposing", list(OBLIGATION_IMPOSING))):
        m = pk[keys].any(axis=1)
        a(rf"\multicolumn{{2}}{{l}}{{\emph{{{lab}}}}} & {100*m.mean():.1f}\% & "
          rf"{wt(m):.1f}\% & {100*mo[keys].any(axis=1).mean():.1f}\%\\")
    m = pk.any(axis=1)
    a(rf"\multicolumn{{2}}{{l}}{{\textbf{{Any category at all}}}} & "
      rf"\textbf{{{100*m.mean():.1f}\%}} & \textbf{{{wt(m):.1f}\%}} & "
      rf"\textbf{{{100*mo.any(axis=1).mean():.1f}\%}}\\")
    a(r"\bottomrule\end{tabular}}\end{table}")

    a("")
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The external sources checked for condition text, and what they hold. Neither "
      r"planning-records table has a field for conditions; DBI's \texttt{description} "
      r"describes the work.}\label{tab:elsewhere}")
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{llrl}\toprule")
    a(r"Dataset & What it is & Rows & Condition text\\\midrule")
    for ds, d in ext.items():
        hit = ("none: no such field" if d["condition_rows"] is None
               else rf"{d['condition_rows']:,} rows ({100*d['condition_rows']/d['rows']:.3f}\%) "
                    rf"mention one")
        a(rf"\texttt{{{T(ds)}}} & {d['name']} & {d['rows']:,} & {hit}\\")
    a(r"\bottomrule\end{tabular}}\end{table}")

    TAB.mkdir(parents=True, exist_ok=True)
    (TAB / "condition_tables.tex").write_text("\n".join(L) + "\n")
    print("→", TAB / "condition_tables.tex")


ACQ_SUMMARY = DATA_ROOT / "extraction" / RUN / "acquisition_summary.json"
CENSUS_SUMMARY = DATA_ROOT / "external" / "cpc_packets" / "census_summary.json"


def write_macros():
    """The numbers this memo's dated corrections quote from its successors, read from the
    summaries those scripts write: the case universe and the permit bridge from
    `acquire_external_data.py report`, the census from `collect_conditions.py report`. The
    corrections cite them by macro so they cannot drift from the memos that own them."""
    M = {}
    if ACQ_SUMMARY.exists():
        a = json.loads(ACQ_SUMMARY.read_text())
        n = lambda x: f"{x:,}".replace(",", "{,}")
        M.update({"condUniverse": n(a["cases"]), "condCasesPrinted": n(a["cases_printed"]),
                  "condCaseMerges": n(a["case_merges"]), "condCUItems": n(a["cu_items"]),
                  "condBridgeCUItems": n(a["bridge_cu_items"]),
                  "condBridgeCUPct": f"{a['bridge_cu_pct']:.1f}",
                  "condCondTextCU": n(a["cond_text_cu_items"]),
                  "condOverlapCU": n(a["overlap_cu_items"])})
    if CENSUS_SUMMARY.exists():
        c = json.loads(CENSUS_SUMMARY.read_text())
        for k in ("ccProbed", "ccProbeHits", "ccCondItems", "ccCondWithText", "ccCondWithTextPct",
                  "ccEarliestInUniverse", "ccEarliestItemYear", "ccEarliestAny",
                  "ccReachEarlyHtml", "ccReachLateHtml",
                  "ccReachEarlyPdf", "ccResPreTen", "ccResPreTenNone", "ccSectionsWithText",
                  "ccEarliestFtp", "ccEarliestModules", "ccEarliestWayback", "ccRowsAll"):
            if k in c:
                M["cond" + k[2:]] = c[k]
    (TAB / "condition_macros.tex").write_text(
        "% GENERATED BY analyze_conditions.py report --- do not edit by hand.\n" +
        "\n".join(rf"\newcommand{{\{k}}}{{{v}}}" for k, v in sorted(M.items())) + "\n")
    print("→", TAB / "condition_macros.tex")


def report():
    FIG.mkdir(parents=True, exist_ok=True)
    TAB.mkdir(parents=True, exist_ok=True)
    it = load_items()
    sweep = sweep_blocks()
    avail = pd.read_csv(AVAIL, dtype={"case_number": str, "bytes": str}) if AVAIL.exists() \
        else pd.DataFrame(columns=["case_number", "year", "found", "route", "bytes"])
    skip = not_read(avail)
    titles, n_files, n_with = condition_titles(skip)
    kinds = packet_kinds(avail)
    pk = classify(titles.keys())
    mo = classify(it.loc[it.modifications_text, "modifications"].astype(str))
    unreach = int((it.conditions & ~it.has_number).sum())
    w = np.array([titles[k] for k in titles], dtype=float)
    fig_coverage(it, avail)
    fig_taxonomy(pk, mo, w)
    write_tables(it, sweep, avail, titles, n_files, n_with, pk, mo, unreach, kinds,
                 w, datasf_counts())
    write_macros()
    print(f"{len(it):,} items | conditions flag {100*it.conditions.mean():.1f}% | "
          f"instrument no {100*it.has_number.mean():.1f}% | unreachable {unreach:,}")
    print(f"packets: {int(avail.found.sum())}/{len(avail)} probed; {n_files} read "
          f"({len(skip)} found but not read), {n_with} with numbered conditions, "
          f"{len(titles)} distinct condition headings")
    cov = pk.any(axis=1).values
    print(f"taxonomy covers {100*cov.mean():.1f}% of distinct headings, "
          f"{100*w[cov].sum()/w.sum():.1f}% weighted by occurrence, "
          f"{100*mo.any(axis=1).mean():.1f}% of modifications")
    print("figures written to", FIG)


if __name__ == "__main__":
    main()
