#!/usr/bin/env python3
"""
analyze_permits.py
------------------
Purpose : How far the entitlement record can be followed to an actual building permit.
          Parses permit numbers out of the item-level extraction, matches them against
          DBI's Building Permits on DataSF, measures what the match buys, and validates a
          parcel-only fallback against the permit-number subset (which is ground truth).
Inputs  : $MFHR_DATA_ROOT/extraction/<RUN>/clean/*.jsonl   (the item table)
          DataSF Building Permits i98e-djp9, cached at
          $MFHR_DATA_ROOT/external/datasf/dbi_permits.csv.gz
Outputs : output/planning_commission_project/permit_linkage/figures/*.pdf
          output/planning_commission_project/permit_linkage/tables/permit_tables.tex
          $MFHR_DATA_ROOT/extraction/<RUN>/permit_summary.json   (read by analyze_corpus)
          $MFHR_DATA_ROOT/extraction/<RUN>/permit_matches.csv    (item ↔ permit, for reuse)
Author  : Dan Post
Created : 2026-09-08

Notes
-----
Four printed forms, not the three the earlier pilot knew about. The fourth --- the
three-group `2019.0618.3775`, year / MMDD / serial --- becomes the house style around 2017
and is invisible to a regex that insists on four groups. Missing it is why the permit route
looked dead after 2019: the minutes kept printing a permit number on ~98% of discretionary
reviews, we just were not reading it. See `fig_permit_coverage.pdf`.

DBI does NOT zero-pad the serial, so `2000/01/19/431` is permit `20000119431` (11 digits)
and plain digit-concatenation is the right normaliser. Letter suffixes ('9801703S') and
prefixes ('M658747') are stripped for the same reason.

`permit_number` is not unique in DBI --- 145,795 rows repeat one, once per lot the permit
touches --- so the permit table is collapsed to its earliest-filed row before joining.
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from paths import DATA_ROOT                                          # noqa: E402
from normalize import parcel_keys                                    # noqa: E402

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                      # noqa: E402

RUN = "corpus_v2_g3"
MEMO = HERE.parents[1] / "output" / "planning_commission_project" / "permit_linkage"
FIG, TAB = MEMO / "figures", MEMO / "tables"
CACHE = DATA_ROOT / "external" / "datasf" / "dbi_permits.csv.gz"

DBI_ID = "i98e-djp9"
DBI_FIELDS = ("permit_number,permit_type,permit_type_definition,block,lot,street_number,"
              "street_name,street_suffix,description,status,status_date,filed_date,"
              "issued_date,approved_date,first_construction_document_date,"
              "last_permit_activity_date,estimated_cost,revised_cost,proposed_units,"
              "existing_units,supervisor_district,site_permit")
PAGE = 50_000

# DBI permit_type codes that can plausibly be the permit an entitlement was about.
# 8 (over-the-counter alterations) is 75% of the table and is plumbing, electrical and
# small work; 4/7 are signs; 5 is grading. Excluding them is the whole parcel-route filter.
MAJOR_TYPES = {"1", "2", "3", "6"}   # new construction, NC wood frame, alterations, demolition
PARCEL_BACK, PARCEL_FWD = 1095, 365  # days before / after the hearing, tuned in tab:parcelrule

plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                     "figure.dpi": 150, "savefig.bbox": "tight"})
SMOOTH_YEARS = 3
MIN_RATE_N = 20      # fewest items a year needs before a rate is drawn for it


# ── permit numbers as the minutes print them ─────────────────────────────────
FORMS = [
    # 2001–2016 house style: year / month / day / serial, any of . - /
    ("YYYY.MM.DD.NNNN", re.compile(r"\b((?:19|20)\d\d)[.\-/](\d{2})[.\-/](\d{2})[.\-/](\d{3,5})\b")),
    # 2017– house style: year / MMDD / serial. Two separators, not three.
    ("YYYY.MMDD.NNNN", re.compile(r"\b((?:19|20)\d\d)[.\-/](\d{4})[.\-/](\d{3,5})\b")),
    # occasional unseparated printing of the same number
    ("packed", re.compile(r"\b((?:19|20)\d\d)(\d{2})(\d{2})(\d{4})\b")),
    # 1998–2000: DBI's pre-2001 seven-digit application number, always after "No."
    ("seven-digit", re.compile(r"\bNos?\.?\s*(\d{7,9})\b")),
]
# The seven-digit pattern is just "No. <digits>", so it also catches the other seven-to-nine
# digit numbers the minutes print after "No." — notices of violation, DBI complaints, a State
# Clearinghouse number. Those are not permits and must not enter the join, so the match is
# kept only when the run-up to it names a permit and does not name one of the impostors.
SEVEN_NEAR = 70
SEVEN_OK = re.compile(r"(?i)\bpermits?\b|\bBPA\b")
SEVEN_BAD = re.compile(r"(?i)notice of violation|\bNOV\b|complaint|clearinghouse|enforcement")
# "mentions a permit at all" — the denominator that separates a format change from a
# substantive change in what the Commission hears.
MENTIONS = re.compile(r"(?i)\b(?:building|demolition|site)\s+permits?\b|\bBPA\b"
                      r"|\bpermit\s+applications?\b")
UNITS = re.compile(r"(?i)\b(\d{1,4})\s+(?:new\s+)?(?:dwelling|residential|housing)\s+units?\b")


def parse_permits(text: str) -> dict[str, str]:
    """Every permit number in `text`, as digits → the printed form it came from.
    First form to claim a number keeps it, so the more specific patterns are listed first."""
    text = text or ""
    got: dict[str, str] = {}
    for name, rx in FORMS:
        for m in rx.finditer(text):
            if name == "seven-digit":
                lead = text[max(0, m.start() - SEVEN_NEAR):m.start()]
                if not SEVEN_OK.search(lead) or SEVEN_BAD.search(lead):
                    continue
            got.setdefault("".join(m.groups()), name)
    return got


def digits(s) -> str:
    return re.sub(r"[^0-9]", "", str(s or ""))


# ── data ─────────────────────────────────────────────────────────────────────
def fetch_dbi(refresh: bool = False) -> Path:
    """Cache DBI's Building Permits locally. 1.3M rows, ~26 pages, ~80 MB gzipped."""
    if CACHE.exists() and not refresh:
        return CACHE
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers["User-Agent"] = "market-for-housing-regulation/analyze_permits (research)"
    tok = _app_token()
    if tok:
        s.headers["X-App-Token"] = tok
    off, n = 0, 0
    with gzip.open(CACHE, "wt", encoding="utf-8") as fh:
        while True:
            for attempt in range(4):
                try:
                    r = s.get(f"https://data.sfgov.org/resource/{DBI_ID}.csv",
                              params={"$select": DBI_FIELDS, "$order": ":id",
                                      "$limit": PAGE, "$offset": off}, timeout=300)
                    r.raise_for_status()
                    break
                except requests.RequestException:
                    if attempt == 3:
                        raise
                    time.sleep(5 * (attempt + 1))
            lines = r.text.splitlines(True)
            if off:
                lines = lines[1:]                      # header once
            if not lines:
                break
            fh.writelines(lines)
            n += len(lines)
            if len(r.text.splitlines()) - 1 < PAGE:
                break
            off += PAGE
            time.sleep(1.0)                            # polite: no app token assumed
    print(f"cached {n:,} DBI permits → {CACHE}")
    return CACHE


def _app_token() -> str | None:
    p = HERE.parents[1] / "api_keys" / "socrata_app_token.txt"
    return p.read_text().strip() if p.exists() and p.read_text().strip() else None


def load_items() -> pd.DataFrame:
    rows = []
    for f in sorted(glob.glob(str(DATA_ROOT / "extraction" / RUN / "clean" / "*.jsonl"))):
        rows += [json.loads(l) for l in open(f)]
    df = pd.DataFrame(rows)
    df["meeting_date"] = pd.to_datetime(df.meeting_date, errors="coerce")
    df["year"] = df.meeting_date.dt.year.fillna(df.year).astype(int)
    d = df.project_descr.fillna("").astype(str)
    df["permits"] = d.map(parse_permits)
    df["n_permits"] = df.permits.map(len)
    df["mentions_permit"] = d.map(lambda t: bool(MENTIONS.search(t)))
    df["units_text"] = d.map(lambda t: max((int(m.group(1)) for m in UNITS.finditer(t)),
                                           default=np.nan))
    df["parcel"] = df.apply(_parcel_keys, axis=1)
    return df


def _parcel_keys(r) -> set[str]:
    """DBI keys parcels as zero-padded block:lot; the minutes print neither padded.

    Via `normalize.parcel_keys`, which pads the DIGITS of each token and keeps the letter.
    This used to `zfill` the token, which is a no-op on '17A' — so lettered parcels built
    the key '0814:20A' against DBI's '0814:020A' and never joined. 1,156 of the 13,620
    parcel-bearing items (8.5%) are affected.
    """
    return parcel_keys(r.assessor_block, r.lot_number, ":")


def load_dbi() -> pd.DataFrame:
    db = pd.read_csv(fetch_dbi(), dtype=str, low_memory=False)
    db["stem"] = db.permit_number.fillna("").map(digits)
    db["parcel"] = db.block.fillna("").str.strip().str.upper() + ":" + \
                   db.lot.fillna("").str.strip().str.upper()
    for c in ("filed_date", "issued_date", "approved_date", "status_date",
              "first_construction_document_date", "last_permit_activity_date"):
        db[c] = pd.to_datetime(db[c], errors="coerce").dt.tz_localize(None)
    for c in ("estimated_cost", "revised_cost", "proposed_units", "existing_units"):
        db[c] = pd.to_numeric(db[c], errors="coerce")
    return db


# ── 1. the permit-number match ───────────────────────────────────────────────
def permit_pairs(it: pd.DataFrame, dbu: pd.DataFrame) -> pd.DataFrame:
    """One row per (item, cited permit number), with the DBI record where it matched."""
    recs = []
    for r in it.itertuples():
        for num, form in r.permits.items():
            recs.append({"item_id": r.item_id, "case_number": r.case_number,
                         "meeting_date": r.meeting_date, "year": r.year,
                         "request_type": r.request_type, "action": r.action,
                         "units_text": r.units_text, "num": num, "form": form})
    pr = pd.DataFrame(recs)
    return pr.merge(dbu, left_on="num", right_on="stem", how="left", suffixes=("", "_dbi"))


def fig_coverage(it: pd.DataFrame, old_has: pd.Series):
    """Why the permit route looked dead after 2019, and why it is not."""
    d = it[it.request_type == "discretionary_review"]
    g = pd.DataFrame({
        "mentions": d.groupby("year").mentions_permit.mean() * 100,
        "parsed": d.groupby("year").apply(lambda x: (x.n_permits > 0).mean() * 100,
                                          include_groups=False),
        "parsed_old": d.assign(o=old_has).groupby("year").o.mean() * 100,
    })
    # 2025 and 2026 hold 7 and 14 discretionary reviews: the archive stops before the
    # corpus does. Draw the gap rather than a rate computed on a dozen items.
    g = g.mask(d.groupby("year").size().reindex(g.index) < MIN_RATE_N)
    allg = pd.DataFrame({
        "items": it.groupby("year").size(),
        "with_permit": it.groupby("year").apply(lambda x: (x.n_permits > 0).sum(),
                                                include_groups=False)})
    fig, axes = plt.subplots(2, 1, figsize=(7.2, 5.8), sharex=True,
                             gridspec_kw={"height_ratios": [1.25, 1]})
    ax = axes[0]
    for col, lab, c in (("mentions", "mentions a building permit", "#5b7fa6"),
                        ("parsed", "permit number parsed (four printed forms)", "#2f6f4f"),
                        ("parsed_old", "permit number parsed (three forms, pre-fix)", "#a33")):
        ax.plot(g.index, g[col], color=c, alpha=0.25, lw=0.9)
        ax.plot(g.index, g[col].rolling(SMOOTH_YEARS, center=True, min_periods=1).mean(),
                color=c, lw=2.0, label=lab)
    ax.set_ylabel("% of discretionary-review items")
    ax.set_ylim(0, 105)
    ax.legend(frameon=False, fontsize=7.5, loc="lower left")
    ax.set_title(f"Discretionary review always cites its permit; only the printing changed\n"
                 f"({SMOOTH_YEARS}-year centred mean over the faint annual series)", loc="left")
    axes[1].bar(allg.index, allg.with_permit, color="#5b7fa6", width=0.8)
    axes[1].set_ylabel("items with a\nparsed permit number")
    axes[1].set_xlabel("hearing year")
    axes[1].set_ylim(0, allg.with_permit.max() * 1.42)   # headroom for the note
    axes[1].text(0.02, 0.97, "counts are not comparable across 1998–2000 and 2025–26:\n"
                 "the archive holds ~12 and ~8 meeting documents in those years",
                 transform=axes[1].transAxes, fontsize=7, color="#777", va="top")
    fig.savefig(FIG / "fig_permit_coverage.pdf")
    plt.close(fig)
    return g, allg


def fig_outcomes(m: pd.DataFrame):
    """What the far side of the hearing looks like once the permit is matched."""
    d = m[m.stem.notna()].copy()
    d["lag"] = (d.issued_date - d.meeting_date).dt.days
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.4))
    # The median is the table's: every permit issued after the hearing, untruncated. The
    # histogram is truncated at 2,000 days for display only. An earlier version took the
    # median over the displayed window instead --- which admits same-day issuance and drops
    # the tail --- and printed 244 days against the table's 245.
    lag_all = d.loc[d.lag > 0, "lag"]
    lag = lag_all[lag_all <= 2000]
    med = lag_all.median()
    axes[0].hist(lag, bins=40, color="#5b7fa6")
    axes[0].axvline(med, color="#a33", lw=1.5)
    axes[0].annotate(f"median {med:.0f} days", xy=(med, axes[0].get_ylim()[1]),
                     xytext=(6, -10), textcoords="offset points", color="#a33", fontsize=8)
    axes[0].set_xlabel("days, hearing → permit issued")
    axes[0].set_ylabel("matched permits")
    axes[0].set_title("Issuance follows the hearing", loc="left", fontsize=9)
    st = d.status.fillna("(none)").str.lower().value_counts().head(7)[::-1]
    axes[1].barh(range(len(st)), st.values, color="#2f6f4f")
    axes[1].set_yticks(range(len(st)))
    axes[1].set_yticklabels(st.index, fontsize=8)
    axes[1].set_xlabel("matched permits")
    axes[1].set_title("Terminal status in DBI", loc="left", fontsize=9)
    fig.savefig(FIG / "fig_permit_outcomes.pdf")
    plt.close(fig)
    return d


# ── 2. the parcel route ──────────────────────────────────────────────────────
def parcel_rules(it: pd.DataFrame, db: pd.DataFrame):
    """Validate a block+lot+window+type rule on the items where the permit number is
    known. That subset is ground truth: we know which permit the hearing was about."""
    by = {k: g for k, g in db.groupby("parcel")}
    gt = []
    for r in it.itertuples():
        if not r.n_permits or not r.parcel or pd.isna(r.meeting_date):
            continue
        parts = [by[k] for k in r.parcel if k in by]
        if not parts:
            continue
        cand = pd.concat(parts)
        truth = set(r.permits) & set(cand.stem)
        if truth:
            gt.append((r.meeting_date, cand, truth))

    def ev(back, fwd, types, topk=None):
        cov, prec, rec, size = [], [], [], []
        for dt, cand, truth in gt:
            w = cand[(cand.filed_date >= dt - pd.Timedelta(days=back)) &
                     (cand.filed_date <= dt + pd.Timedelta(days=fwd))]
            if types:
                w = w[w.permit_type.isin(types)]
            if topk:
                w = w.assign(g=(dt - w.filed_date).abs()).sort_values("g").head(topk)
            pick = set(w.stem)
            tp = len(pick & truth)
            rec.append(tp / len(truth))
            cov.append(bool(pick))
            if pick:
                prec.append(tp / len(pick))
                size.append(len(pick))
        return {"n": len(gt), "coverage": float(np.mean(cov)), "precision": float(np.mean(prec)),
                "recall": float(np.mean(rec)), "median_set": float(np.median(size))}

    rules = [("every permit at the parcel", ev(20_000, 20_000, None)),
             ("major types only, no date window", ev(20_000, 20_000, MAJOR_TYPES)),
             ("major types, filed within $[-1, +0.5]$ yr", ev(365, 180, MAJOR_TYPES)),
             ("major types, filed within $[-2, +1]$ yr", ev(730, 365, MAJOR_TYPES)),
             ("major types, filed within $[-3, +1]$ yr", ev(PARCEL_BACK, PARCEL_FWD, MAJOR_TYPES)),
             ("\\quad …\\ nearest filing only", ev(PARCEL_BACK, PARCEL_FWD, MAJOR_TYPES, 1)),
             ("\\quad …\\ nearest three filings", ev(PARCEL_BACK, PARCEL_FWD, MAJOR_TYPES, 3))]
    return rules, by


def parcel_reach(it: pd.DataFrame, by: dict):
    """How much of the docket the parcel route reaches where no permit number is printed."""
    d = it[(it.n_permits == 0)]
    has_parcel = d.parcel.map(bool)
    n_cand, n_any = 0, 0
    for r in d[has_parcel].itertuples():
        parts = [by[k] for k in r.parcel if k in by]
        if not parts:
            continue
        n_any += 1
        cand = pd.concat(parts)
        w = cand[(cand.filed_date >= r.meeting_date - pd.Timedelta(days=PARCEL_BACK)) &
                 (cand.filed_date <= r.meeting_date + pd.Timedelta(days=PARCEL_FWD)) &
                 (cand.permit_type.isin(MAJOR_TYPES))]
        if len(w):
            n_cand += 1
    return {"no_number": int(len(d)), "with_parcel": int(has_parcel.sum()),
            "parcel_in_dbi": n_any, "with_candidate": n_cand}


# ── 3. does DBI see the Commission's conditions? ─────────────────────────────
CONDITION_TALK = re.compile(r"(?i)conditions? of approval|planning commission motion"
                            r"|per motion no|pursuant to motion")


def conditions_in_dbi(db: pd.DataFrame) -> dict:
    """Task 3 depends on the answer: DBI records the permit's own scope and status, not
    the Commission's conditions. Confirm rather than assume."""
    d = db.description.fillna("")
    hit = d.str.contains(CONDITION_TALK, regex=True)
    return {"rows": int(len(db)), "with_description": float(d.ne("").mean()),
            "condition_talk": int(hit.sum()), "condition_talk_pct": float(100 * hit.mean()),
            "fields": sorted(db.columns.tolist())}


# ── tables ───────────────────────────────────────────────────────────────────
def T(s):
    return str(s).replace("_", r"\_")


def _tex_n(n) -> str:
    """An integer with the file's LaTeX thousands separator. Exists so a count that appears
    in a CAPTION is computed like every other number in the table rather than transcribed —
    the DBI row count and the corpus size were both hand-typed here and both would have gone
    stale the next time either source moved."""
    return f"{int(n):,}".replace(",", "{,}")


def write_tables(it, pr, m, rules, reach, cond, byform, bydec, bytype, buys, n_dbi):
    L = ["% GENERATED BY analyze_permits.py — do not edit by hand."]
    a = L.append

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Permit numbers as the minutes print them, and whether DataSF's Building "
      rf"Permits table (\texttt{{i98e-djp9}}, {_tex_n(n_dbi)} rows) holds them. Distinct "
      r"numbers, not items: a number cited at several hearings is counted "
      r"once.}\label{tab:permitforms}")
    a(r"\begin{tabular}{llrrr}\toprule")
    a(r"Printed form & Example & Distinct & Matched & Rate\\\midrule")
    for form, ex, n, k in byform:
        a(rf"\texttt{{{T(form)}}} & \texttt{{{T(ex)}}} & {n:,} & {k:,} & {100*k/n:.1f}\%\\")
    tot_n = sum(r[2] for r in byform); tot_k = sum(r[3] for r in byform)
    a(rf"\midrule All & & {tot_n:,} & {tot_k:,} & \textbf{{{100*tot_k/tot_n:.1f}\%}}\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Match rate by decade of the hearing at which the number was cited. The "
      r"pre-2001 seven-digit form is the \emph{best} covered, not the "
      r"worst.}\label{tab:permitdecade}")
    a(r"\begin{tabular}{lrrr}\toprule")
    a(r"Decade & Distinct permits & Matched & Rate\\\midrule")
    for dec, n, k in bydec:
        a(rf"{dec}s & {n:,} & {k:,} & {100*k/n:.1f}\%\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Which parts of the docket the permit route reaches. `Cites a permit' is "
      r"any mention of a building, site or demolition permit in the request text; `number "
      r"parsed' is a number recovered from it. Request types with fewer than 40 items are "
      r"omitted.}\label{tab:permitbytype}")
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{lrrrr}\toprule")
    a(r"Request type & Items & Cites a permit & Number parsed & Matched in DBI\\\midrule")
    for rt, n, ment, parsed, matched in bytype:
        a(rf"\texttt{{{T(rt)}}} & {n:,} & {100*ment/n:.1f}\% & {100*parsed/n:.1f}\% & "
          rf"{100*matched/n:.1f}\%\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{What the match buys: what is observable in DBI for a matched item, on the "
      r"far side of the hearing.}\label{tab:permitbuys}")
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{llr}\toprule")
    a(r"Observable & DBI field & Coverage\\\midrule")
    for lab, fld, val in buys:
        a(rf"{lab} & \texttt{{{T(fld)}}} & {val}\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The parcel route, scored against the permit-number subset. `Coverage' is "
      r"the share of those items for which the rule returns any candidate; precision is "
      r"computed on the items where it does; recall is the share of the truly cited "
      r"permits it retains. $n=%d$ items where the cited permit is present at the parcel "
      r"DBI records.}\label{tab:parcelrule}" % rules[0][1]["n"])
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{lrrrr}\toprule")
    a(r"Rule (block $+$ lot, then \dots) & Coverage & Precision & Recall & Median set\\\midrule")
    for lab, r in rules:
        a(rf"{lab} & {100*r['coverage']:.1f}\% & {r['precision']:.2f} & {r['recall']:.2f} & "
          rf"{r['median_set']:.0f}\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Reach of the parcel route on the part of the docket the permit-number "
      r"route cannot see.}\label{tab:parcelreach}")
    a(r"\begin{tabular}{lrr}\toprule")
    n_all = len(it)
    a(rf"Items & Count & Share of the {_tex_n(n_all)}\\\midrule")
    for lab, k in (("No permit number in the text", reach["no_number"]),
                   ("\\quad of which: block and lot present", reach["with_parcel"]),
                   ("\\quad\\quad parcel exists in DBI", reach["parcel_in_dbi"]),
                   ("\\quad\\quad\\quad a candidate permit in window and type",
                    reach["with_candidate"])):
        a(rf"{lab} & {k:,} & {100*k/n_all:.1f}\%\\")
    a(r"\bottomrule\end{tabular}\end{table}")

    TAB.mkdir(parents=True, exist_ok=True)
    (TAB / "permit_tables.tex").write_text("\n".join(L) + "\n")
    print("→", TAB / "permit_tables.tex")


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download the DBI cache")
    args = ap.parse_args()

    FIG.mkdir(parents=True, exist_ok=True)
    TAB.mkdir(parents=True, exist_ok=True)
    if args.refresh:
        fetch_dbi(refresh=True)

    it = load_items()
    db = load_dbi()
    print(f"{len(it):,} items; {len(db):,} DBI permit rows, {db.stem.nunique():,} distinct")
    dbu = db.sort_values("filed_date").drop_duplicates("stem", keep="first")

    # what the three-form parser saw, for the coverage figure
    old_forms = [rx for name, rx in FORMS if name != "YYYY.MMDD.NNNN"]
    old_has = it.project_descr.fillna("").astype(str).map(
        lambda t: any(rx.search(t) for rx in old_forms))

    pr = permit_pairs(it, dbu)
    m = pr[pr.stem.notna()]
    uniq = pr.drop_duplicates("num")
    print(f"items with a number: {pr.item_id.nunique():,}; distinct numbers: {len(uniq):,}; "
          f"matched {uniq.stem.notna().sum():,} ({100*uniq.stem.notna().mean():.1f}%)")

    byform = []
    for name, rx in FORMS:
        u = uniq[uniq.form == name]
        byform.append((name, _example(it, name), len(u), int(u.stem.notna().sum())))
    bydec = [(int(d), len(g), int(g.stem.notna().sum()))
             for d, g in uniq.assign(d=(uniq.year // 10) * 10).groupby("d")]

    bytype = []
    matched_items = set(m.item_id)
    for rt, g in it[it.request_type != ""].groupby("request_type"):
        if len(g) < 40:
            continue
        bytype.append((rt, len(g), int(g.mentions_permit.sum()), int((g.n_permits > 0).sum()),
                       int(g.item_id.isin(matched_items).sum())))
    bytype.sort(key=lambda r: -r[3] / r[1])

    d = fig_outcomes(m)
    fig_coverage(it, old_has)
    buys = _buys(d)
    rules, by = parcel_rules(it, db)
    reach = parcel_reach(it, by)
    cond = conditions_in_dbi(db)
    print(f"DBI descriptions naming a Commission condition or motion: "
          f"{cond['condition_talk']:,} ({cond['condition_talk_pct']:.4f}%)")
    write_tables(it, pr, m, rules, reach, cond, byform, bydec, bytype, buys, len(db))

    out = DATA_ROOT / "extraction" / RUN
    keep = ["item_id", "case_number", "meeting_date", "year", "request_type", "action",
            "num", "form", "permit_number", "permit_type_definition", "status",
            "filed_date", "issued_date", "first_construction_document_date",
            "estimated_cost", "revised_cost", "proposed_units", "existing_units",
            "supervisor_district", "block", "lot"]
    pr[keep].to_csv(out / "permit_matches.csv", index=False)
    (out / "permit_summary.json").write_text(json.dumps({
        "run": RUN, "items": int(len(it)),
        "items_mentioning_permit": int(it.mentions_permit.sum()),
        "items_with_permit": int(pr.item_id.nunique()),
        "distinct_permits": int(len(uniq)),
        "matched_permits": int(uniq.stem.notna().sum()),
        "match_rate_pct": float(100 * uniq.stem.notna().mean()),
        "matched_items": int(len(matched_items)),
        "parcel_rule": {"back_days": PARCEL_BACK, "fwd_days": PARCEL_FWD,
                        "types": sorted(MAJOR_TYPES), **rules[4][1]},
        "parcel_reach": reach, "dbi_conditions": {k: v for k, v in cond.items()
                                                  if k != "fields"},
    }, indent=2) + "\n")
    print("→", out / "permit_summary.json")


def _example(it, form):
    """A real printed example of `form`, so the table shows the archive rather than a
    made-up string."""
    rx = dict(FORMS)[form]
    for t in it.project_descr.fillna("").astype(str):
        m = rx.search(t)
        if m:
            return m.group(0)
    return ""


def _buys(d: pd.DataFrame):
    n = len(d)
    lag = d.loc[d.lag > 0, "lag"]
    started = d.first_construction_document_date.notna()
    dead = d.status.fillna("").str.lower().isin(["withdrawn", "expired", "cancelled",
                                                 "revoked", "disapproved"])
    done = d.status.fillna("").str.lower().eq("complete")
    both = d[d.proposed_units.notna() & d.units_text.notna()]
    agree = (both.proposed_units == both.units_text).mean() if len(both) else np.nan
    return [
        ("Permit ever issued", "issued_date", rf"{100*d.issued_date.notna().mean():.1f}\%"),
        ("\\quad median lag, hearing $\\to$ issuance", "issued_date",
         rf"{lag.median():.0f} days"),
        ("\\quad 90th percentile lag", "issued_date", rf"{lag.quantile(.9):.0f} days"),
        ("Construction started", "first_construction_document_date",
         rf"{100*started.mean():.1f}\%"),
        ("Permit completed", "status", rf"{100*done.mean():.1f}\%"),
        ("Withdrawn, expired, cancelled or revoked", "status", rf"{100*dead.mean():.1f}\%"),
        ("Cost recorded", "revised_cost / estimated_cost",
         rf"{100*(d.revised_cost.notna()|d.estimated_cost.notna()).mean():.1f}\%"),
        ("\\quad median revised cost", "revised_cost",
         rf"\${d.revised_cost.median():,.0f}"),
        ("Proposed dwelling units", "proposed_units",
         rf"{100*d.proposed_units.notna().mean():.1f}\%"),
        ("\\quad agrees with a unit count in the request text", "proposed_units",
         rf"{100*agree:.1f}\% of {len(both):,}"),
        ("Supervisor district", "supervisor_district",
         rf"{100*d.supervisor_district.notna().mean():.1f}\%"),
        ("Matched item--permit pairs", "---", rf"{n:,}"),
    ]


if __name__ == "__main__":
    main()
