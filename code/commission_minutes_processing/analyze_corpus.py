#!/usr/bin/env python3
"""
analyze_corpus.py
-----------------
Purpose : Turn the extracted item-level table into the figures and tables the discretionary-
          review memo reports. One script, so no number in the memo is hand-placed.
Inputs  : $MFHR_DATA_ROOT/extraction/<run>/clean/*.jsonl
Outputs : output/planning_commission_project/discretionary_review_patterns/
          figures/fig_*.pdf and tables/corpus_tables.tex
Author  : Dan Post
Created : 2026-09-07

Notes
-----
House style for time series: faint raw annual series behind a bold smoothed line, with the
smoothing window stated in calendar time. Gaps are drawn, never interpolated.

2018 is no longer sparse (refilled from the S3 packet prefix on 2026-09-04), so no year is
suppressed; `SPARSE_YEARS` stays as the place to name one if it recurs.
"""
from __future__ import annotations

import glob
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from paths import DATA_ROOT                                          # noqa: E402
from normalize import parcel_keys                                    # noqa: E402

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                      # noqa: E402
import pandas as pd                                                  # noqa: E402

RUN = "corpus_v2_g3"
MEMO = (HERE.parents[1] / "output" / "planning_commission_project"
        / "discretionary_review_patterns")
FIG = MEMO / "figures"
TAB = MEMO / "tables"
SPARSE_YEARS: set[int] = set()
SMOOTH_YEARS = 3            # centred, in calendar years

plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                     "figure.dpi": 150, "savefig.bbox": "tight"})


def load() -> pd.DataFrame:
    rows = []
    for f in sorted(glob.glob(str(DATA_ROOT / "extraction" / RUN / "clean" / "*.jsonl"))):
        rows += [json.loads(l) for l in open(f)]
    df = pd.DataFrame(rows)
    df["meeting_date"] = pd.to_datetime(df["meeting_date"], errors="coerce")
    df["year"] = df["meeting_date"].dt.year.fillna(df["year"]).astype(int)
    # 2026 is a partial year: the corpus stops mid-year, so a rate computed on it is fine
    # but a COUNT is not comparable and the figures say so.
    return df


def smooth(s: pd.Series) -> pd.Series:
    return s.rolling(SMOOTH_YEARS, center=True, min_periods=1).mean()


def series_plot(ax, x, raw, label, colour):
    ax.plot(x, raw, color=colour, alpha=0.25, lw=0.9)
    ax.plot(x, smooth(pd.Series(raw, index=x)), color=colour, lw=2.0, label=label)


# ── 1. composition by request type ───────────────────────────────────────────
FAMILY = {
    "conditional_use": "Conditional use", "conditional_use_modification": "Conditional use",
    "discretionary_review": "Discretionary review",
    "variance": "Variance",
    "planning_code_amendment": "Legislative", "rezoning_map_amendment": "Legislative",
    "general_plan_amendment": "Legislative",
    "large_project_authorization": "Large project / downtown",
    "downtown_project": "Large project / downtown",
    "office_allocation": "Large project / downtown",
    "ceqa_environmental": "CEQA / appeals",
    "appeal_preliminary_negative_declaration": "CEQA / appeals", "appeal": "CEQA / appeals",
    "historic": "Historic", "coastal": "Other", "informational": "Informational",
    "other": "Other",
}
ORDER = ["Conditional use", "Discretionary review", "Variance", "Legislative",
         "Large project / downtown", "CEQA / appeals", "Historic", "Informational", "Other"]


def fig_composition(df):
    # Shares are computed over items that HAVE a request type (262 of 16,199 do not). The
    # table's item count must use the same denominator or the row does not add up, so the
    # filtered frame is returned rather than re-derived there.
    d = df[df.request_type != ""].copy()
    d["family"] = d.request_type.map(FAMILY).fillna("Other")
    tab = d.pivot_table(index="year", columns="family", values="item_id",
                        aggfunc="count").fillna(0)
    tab = tab.reindex(columns=[c for c in ORDER if c in tab.columns])
    share = tab.div(tab.sum(axis=1), axis=0) * 100
    fig, axes = plt.subplots(2, 1, figsize=(7.2, 6.4), sharex=True,
                             gridspec_kw={"height_ratios": [1, 1.25]})
    axes[0].bar(tab.index, tab.sum(axis=1), color="#5b7fa6", width=0.8)
    axes[0].set_ylabel("items heard")
    axes[0].set_title("Items with a case number heard by the Planning Commission", loc="left")
    axes[0].annotate("2026 partial", xy=(2026, tab.sum(axis=1).loc[2026]),
                     xytext=(-4, 6), textcoords="offset points", ha="right", fontsize=7,
                     color="#777")
    axes[1].stackplot(share.index, [share[c] for c in share.columns],
                      labels=list(share.columns), alpha=0.9,
                      colors=plt.cm.tab20.colors[:len(share.columns)])
    axes[1].set_ylabel("share of items (%)"); axes[1].set_ylim(0, 100)
    axes[1].legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=4, frameon=False,
                   fontsize=7.5)
    axes[1].set_xlabel("hearing year")
    fig.savefig(FIG / "fig_composition.pdf"); plt.close(fig)
    return tab, share, d


# ── 2. disapproval / conditions / modification ───────────────────────────────
def fig_outcomes(df):
    d = df[df.action != ""].copy()
    d["disapproved"] = d.action.isin(["disapproved", "intent_to_disapprove"])
    d["took_dr"] = d.action.isin(["took_dr", "took_dr_and_approved"])
    # `conditions_imposed` is an enum with three states — yes / no / blank — so the test is
    # `== "yes"`, not "is not blank". `.ne("")` counted the 151 items that explicitly say NO
    # as conditioned, which put the all-years rate at 26.4% against a true 25.4% and 2003 at
    # 27.1% against 24.0%. `project_modified` on the next line always had it right.
    d["conditions"] = d.conditions_imposed.astype(str).str.strip().str.lower().eq("yes")
    d["modified"] = d.project_modified.astype(str).str.lower().eq("yes")
    d["continued"] = d.action.isin(["continued", "continued_indefinitely"])
    g = d.groupby("year")[["disapproved", "conditions", "modified", "continued", "took_dr"]]
    rate = g.mean() * 100
    n = d.groupby("year").size()
    fig, ax = plt.subplots(figsize=(7.2, 3.9))
    for col, lab, c in (("continued", "continued", "#8c8c8c"),
                        ("conditions", "conditions imposed", "#2f6f4f"),
                        ("modified", "project modified", "#b07d2b"),
                        ("disapproved", "disapproved", "#a33"),
                        ("took_dr", "took DR", "#4a5fa5")):
        series_plot(ax, rate.index, rate[col].values, lab, c)
    ax.set_ylabel("% of items heard that year")
    ax.set_xlabel("hearing year")
    ax.set_title(f"Commission outcomes ({SMOOTH_YEARS}-year centred mean over the faint "
                 f"annual series)", loc="left")
    ax.legend(frameon=False, ncol=3, fontsize=8)
    fig.savefig(FIG / "fig_outcomes.pdf"); plt.close(fig)
    return rate, n


# ── 3. delay: continuance chains ─────────────────────────────────────────────
def chains(df, key: str | None = None):
    """A case heard more than once is one project seen several times. The chain is the
    ordered set of hearings sharing a case number; the delay is first hearing to last.
    `key` names a column already holding the case --- the reconciled universe's `cn`; without
    it the case is the printed number upper-cased and stripped, which is how this memo was
    first computed."""
    d = df[df.case_number.astype(str).str.strip() != ""].copy()
    if key:
        d["cn"] = d[key]
        d = d[d.cn.astype(str).ne("")]
    else:
        d["cn"] = d.case_number.astype(str).str.upper().str.replace(r"\s+", "", regex=True)
    g = d.sort_values("meeting_date").groupby("cn")
    out = pd.DataFrame({
        "hearings": g.size(),
        "first": g.meeting_date.first(),
        "last": g.meeting_date.last(),
        "n_continued": g.apply(lambda x: x.action.isin(
            ["continued", "continued_indefinitely"]).sum(), include_groups=False),
        "final_action": g.action.last(),
        "request_type": g.request_type.first(),
    })
    out["days"] = (out["last"] - out["first"]).dt.days
    out["year"] = out["first"].dt.year
    return out


def fig_delay(ch):
    d = ch[(ch.hearings > 1) & ch.days.notna() & (ch.days >= 0)]
    by = d.groupby("year")["days"]
    med, p90 = by.median(), by.quantile(0.9)
    share = (ch.assign(multi=ch.hearings > 1).groupby("year")["multi"].mean() * 100)
    fig, axes = plt.subplots(2, 1, figsize=(7.2, 5.6), sharex=True)
    series_plot(axes[0], share.index, share.values, "share of cases heard more than once",
                "#5b7fa6")
    axes[0].set_ylabel("% of cases"); axes[0].legend(frameon=False, fontsize=8)
    axes[0].set_title("Repeat hearings and elapsed time, by year of first hearing", loc="left")
    series_plot(axes[1], med.index, med.values, "median days, first to last hearing", "#2f6f4f")
    series_plot(axes[1], p90.index, p90.values, "90th percentile", "#a33")
    axes[1].set_ylabel("days"); axes[1].set_xlabel("year of first hearing")
    axes[1].legend(frameon=False, fontsize=8)
    fig.savefig(FIG / "fig_delay.pdf"); plt.close(fig)
    return med, p90, share


# ── 4. commissioners ─────────────────────────────────────────────────────────
def commissioners(df):
    rows = []
    for _, r in df.iterrows():
        ayes = r.ayes if isinstance(r.ayes, list) else []
        noes = r.noes if isinstance(r.noes, list) else []
        if not ayes and not noes:
            continue
        for n in ayes:
            rows.append((str(n).strip().lower(), r.year, "aye", r.action))
        for n in noes:
            rows.append((str(n).strip().lower(), r.year, "no", r.action))
    v = pd.DataFrame(rows, columns=["name", "year", "vote", "action"])
    v = v[v.name.str.len() > 2]
    tab = v.pivot_table(index="name", columns="vote", values="year", aggfunc="count").fillna(0)
    tab["votes"] = tab.sum(axis=1)
    tab["dissent_rate"] = 100 * tab.get("no", 0) / tab["votes"]
    tab["first"] = v.groupby("name").year.min()
    tab["last"] = v.groupby("name").year.max()
    return v, tab.sort_values("votes", ascending=False)


def fig_commissioners(tab):
    d = tab[tab.votes >= 200].sort_values("dissent_rate", ascending=True).tail(25)
    fig, ax = plt.subplots(figsize=(7.2, 6.2))
    ax.barh(range(len(d)), d.dissent_rate, color="#a33", alpha=0.85)
    ax.set_yticks(range(len(d)))
    ax.set_yticklabels([f"{n.title()}  ({int(v):,} votes, {int(f)}–{int(l)})"
                        for n, v, f, l in zip(d.index, d.votes, d["first"], d["last"])],
                       fontsize=7.5)
    ax.set_xlabel("% of recorded votes cast against the prevailing motion")
    ax.set_title("Dissent rate, commissioners with 200+ recorded votes", loc="left")
    fig.savefig(FIG / "fig_commissioners.pdf"); plt.close(fig)
    return d


# ── 5. geography ─────────────────────────────────────────────────────────────
def parcels(df):
    d = df.copy()
    d["block"] = d.assessor_block.astype(str).str.strip().str.upper()
    d["has_parcel"] = d.block.ne("") & d.lot_number.apply(
        lambda x: isinstance(x, list) and len(x) > 0)
    # `normalize.parcel_keys` pads the DIGITS of each token and keeps the letter, which is
    # how DataSF writes a parcel (lot 17A is `017A`, block 452T is `0452T`). Padding the
    # whole token with zfill is a no-op on a lettered one and undercounts distinct parcels.
    d["apn"] = d.apply(
        lambda r: sorted(parcel_keys(r.block, r.lot_number)) if r.has_parcel else [], axis=1)
    return d


def fig_geography(d):
    hot = Counter()
    for _, r in d.iterrows():
        if r.block:
            hot[r.block] += 1
    top = pd.Series(hot).sort_values(ascending=False).head(25)
    fig, ax = plt.subplots(figsize=(7.2, 5.4))
    ax.barh(range(len(top)), top.values[::-1], color="#5b7fa6")
    ax.set_yticks(range(len(top)))
    ax.set_yticklabels(top.index[::-1], fontsize=7.5)
    ax.set_xlabel("items heard, 1998–2026")
    ax.set_title("Assessor blocks appearing most often before the Commission", loc="left")
    fig.savefig(FIG / "fig_geography.pdf"); plt.close(fig)
    return top


# ── 6. planning code citations ───────────────────────────────────────────────
SECTION = re.compile(r"(?i)\bSections?\s+((?:\d{1,4}(?:\.\d+)?[A-Za-z]?)"
                     r"(?:\s*(?:,|and|&|through|-)\s*\d{1,4}(?:\.\d+)?[A-Za-z]?)*)")


def citations(df):
    per_item, by_type = Counter(), defaultdict(Counter)
    for _, r in df.iterrows():
        txt = str(r.project_descr or "")
        secs = set()
        for m in SECTION.finditer(txt):
            for tok in re.split(r"\s*(?:,|and|&|through|-)\s*", m.group(1)):
                tok = tok.strip()
                if re.fullmatch(r"\d{1,4}(?:\.\d+)?[A-Za-z]?", tok) and len(tok) >= 2:
                    secs.add(tok)
        for s in secs:
            per_item[s] += 1
            by_type[s][r.request_type] += 1
    return per_item, by_type


def fig_citations(per_item):
    top = pd.Series(per_item).sort_values(ascending=False).head(20)
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.barh(range(len(top)), top.values[::-1], color="#2f6f4f")
    ax.set_yticks(range(len(top)))
    ax.set_yticklabels([f"§{s}" for s in top.index[::-1]], fontsize=8)
    ax.set_xlabel("items citing the section")
    ax.set_title("Planning Code sections cited in the request text", loc="left")
    fig.savefig(FIG / "fig_citations.pdf"); plt.close(fig)
    return top


def permit_stats(df):
    """Coverage of the building-permit join, for tab:linkage.

    analyze_permits.py does the real work (it queries DataSF) and caches its headline
    numbers next to the extraction. Read the cache when it is there; fall back to the
    parse-only counts, which need no network, and say so.
    """
    cache = DATA_ROOT / "extraction" / RUN / "permit_summary.json"
    if cache.exists():
        s = json.loads(cache.read_text())
        return {"items": s["items_with_permit"], "distinct": s["distinct_permits"],
                "match_rate": s["match_rate_pct"], "matched": True}
    import analyze_permits as ap
    hits = [ap.parse_permits(str(r)) for r in df.project_descr.fillna("")]
    return {"items": sum(1 for h in hits if h),
            "distinct": len({p for h in hits for p in h}),
            "match_rate": float("nan"), "matched": False}


def main():
    df = load()
    print(f"{len(df):,} items, {df.year.min()}–{df.year.max()}")
    FIG.mkdir(parents=True, exist_ok=True)
    TAB.mkdir(parents=True, exist_ok=True)
    tab, share, typed = fig_composition(df)
    rate, n = fig_outcomes(df)
    ch = chains(df)
    fig_delay(ch)
    v, ctab = commissioners(df)
    fig_commissioners(ctab)
    parc = parcels(df)
    fig_geography(parc)
    per_item, by_type = citations(df)
    fig_citations(per_item)
    print("figures written to", FIG)
    write_tables(df, typed, share, rate, n, ch, ctab, parc, per_item, by_type,
                 permit_stats(df))


# ── tables ───────────────────────────────────────────────────────────────────
def write_tables(df, typed, share, rate, n, ch, ctab, parc, per_item, by_type, permits):
    """Every table in the memo, generated. Wide ones are \\resizebox'd."""
    L = []
    a = L.append
    a("% GENERATED BY analyze_corpus.py — do not edit by hand.")

    yrs = [1998, 2003, 2008, 2013, 2018, 2023]
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Composition of the Commission's item-level docket, per cent of items heard "
      r"that year. Families group the 17 \texttt{request\_type} values; `Legislative' is "
      r"Planning Code, map and General Plan amendments. `Items' counts the items carrying "
      r"a request type, which is the denominator the shares are taken over."
      r"}\label{tab:composition}")
    a(r"\resizebox{\textwidth}{!}{%")
    cols = [c for c in ORDER if c in share.columns]
    a(r"\begin{tabular}{l" + "r" * len(cols) + r"r}\toprule")
    a("Year & " + " & ".join(c.replace("/", r"/\allowbreak ") for c in cols) + r" & Items\\\midrule")
    for y in yrs:
        if y not in share.index:
            continue
        a(f"{y} & " + " & ".join(f"{share.loc[y, c]:.1f}" for c in cols)
          + rf" & {int(typed[typed.year == y].shape[0]):,}\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Commission outcomes, per cent of items heard that year. `Conditions' and "
      r"`modified' are the orthogonal flags, not dispositions: a project can be approved "
      r"with conditions and modified at once.}\label{tab:outcomes}")
    a(r"\begin{tabular}{lrrrrr}\toprule")
    a(r"Year & Continued & Conditions & Modified & Disapproved & Took DR\\\midrule")
    for y in yrs:
        if y not in rate.index:
            continue
        a(f"{y} & " + " & ".join(f"{rate.loc[y, c]:.1f}" for c in
                                 ("continued", "conditions", "modified", "disapproved",
                                  "took_dr")) + r"\\")
    w = rate.mul(n, axis=0).sum() / n.sum()
    a(r"\midrule All years & " + " & ".join(f"\\textbf{{{w[c]:.1f}}}" for c in
                                            ("continued", "conditions", "modified",
                                             "disapproved", "took_dr")) + r"\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    m = ch[(ch.hearings > 1) & ch.days.notna() & (ch.days >= 0)]
    a(r"\begin{table}[htbp]\centering")
    a(rf"\caption{{Elapsed time from first to last hearing, for cases heard more than once, "
      rf"by five-year cohort of first hearing. A case is a \texttt{{case\_number}}; the "
      rf"corpus ends {df.meeting_date.max():%Y-%m-%d}, so recent cohorts are right-censored "
      rf"and their tails are understated.}}\label{{tab:delay}}")
    a(r"\begin{tabular}{lrrrr}\toprule")
    a(r"First heard & Cases & Median days & 90th pct & Max\\\midrule")
    mm = m.assign(coh=(m.year // 5) * 5)
    for c, g in mm.groupby("coh"):
        if len(g) < 20:
            continue
        # label by the years the cohort actually holds: the corpus starts in 1998, so the
        # bin beginning 1995 is 1998--1999 and saying otherwise invents two years of coverage
        a(rf"{int(g.year.min())}--{int(g.year.max())} & {len(g):,} & {g.days.median():.0f} & "
          rf"{g.days.quantile(.9):.0f} & {g.days.max():.0f}\\")
    a(rf"\midrule All & {len(m):,} & {m.days.median():.0f} & {m.days.quantile(.9):.0f} & "
      rf"{m.days.max():.0f}\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    d = ctab[ctab.votes >= 500].sort_values("votes", ascending=False).head(20)
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Commissioners with 500 or more recorded votes. Dissent is a vote recorded "
      r"in the NOES column; the Commission votes by roll call and the minutes record both "
      r"sides, so this is a census of recorded votes rather than a "
      r"sample.}\label{tab:commissioners}")
    a(r"\begin{tabular}{lrrrl}\toprule")
    a(r"Commissioner & Votes & Noes & Dissent rate & Years\\\midrule")
    for name, r in d.iterrows():
        a(rf"{name.title()} & {int(r.votes):,} & {int(r.get('no', 0))} & "
          rf"{r.dissent_rate:.1f}\% & {int(r['first'])}--{int(r['last'])}\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    s = pd.Series(per_item).sort_values(ascending=False).head(18)
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Planning Code sections cited in the request text, with the two request "
      r"types that cite them most. The mapping recovers the Code's own structure without "
      r"being told it, which is a validity check on the extraction as much as a "
      r"finding.}\label{tab:citations}")
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{lrl}\toprule")
    a(r"Section & Items & Cited mainly by\\\midrule")
    for sec, cnt in s.items():
        tt = by_type[sec].most_common(2)
        a(rf"\S{sec} & {cnt:,} & " +
          ", ".join(f"\\texttt{{{k.replace('_', chr(92) + '_')}}} ({v:,})"
                    for k, v in tt if k) + r"\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{What the item table can be joined to, and how far it "
      r"reaches.}\label{tab:linkage}")
    a(r"\begin{tabular}{lrl}\toprule")
    a(r"Join key & Coverage & Reaches\\\midrule")
    a(rf"Assessor block & {100*parc.block.ne('').mean():.1f}\% & "
      rf"{parc[parc.block.ne('')].block.nunique():,} distinct blocks\\")
    a(rf"Block $+$ lot (APN) & {100*parc.has_parcel.mean():.1f}\% & "
      rf"{len({x for l in parc.apn for x in l}):,} distinct parcels\\")
    reach = (rf"{permits['distinct']:,} distinct permits, "
             rf"{permits['match_rate']:.1f}\% matched in DBI" if permits["matched"]
             else rf"{permits['distinct']:,} distinct permits (DBI match not cached)")
    a(rf"Building-permit number & {100*permits['items']/len(parc):.1f}\% & " + reach + r"\\")
    a(r"\bottomrule\end{tabular}\end{table}")

    (TAB / "corpus_tables.tex").write_text("\n".join(L) + "\n")
    print("→", TAB / "corpus_tables.tex")


def universe_macros():
    """The reconciliation, stated in this memo's own terms (2026-09-11). The memo chained
    items by the printed case number; the universe every memo now uses expands a two-digit
    year first (`acquire_external_data.case_universe`). Both counts, and the repeat-hearing
    figures under both, are written as macros for the dated note --- the printed-number ones
    must reproduce what the memo already says, which is the check that nothing else moved."""
    from acquire_external_data import load_items, case_universe
    df = load()
    it = load_items()
    df = df.merge(it[["item_id", "cn"]], on="item_id", how="left")
    df["cn"] = df.cn.fillna("")
    a, b = chains(df), chains(df, key="cn")
    n = lambda x: f"{x:,}".replace(",", "{,}")
    M = {"corpusCasesPrinted": n(len(a)), "corpusUniverse": n(len(case_universe(it))),
         "corpusCaseMerges": n(len(a) - len(b)),
         "corpusRepeatPrinted": f"{100*(a.hearings > 1).mean():.1f}",
         "corpusRepeatUniverse": f"{100*(b.hearings > 1).mean():.1f}",
         "corpusMaxDaysPrinted": n(int(a.days.max())), "corpusMaxDaysUniverse": n(int(b.days.max()))}
    assert len(b) == len(case_universe(it)), "the chains and the universe disagree"
    (TAB / "corpus_macros.tex").write_text(
        "% GENERATED BY analyze_corpus.py universe --- do not edit by hand.\n" +
        "\n".join(rf"\newcommand{{\{k}}}{{{v}}}" for k, v in sorted(M.items())) + "\n")
    print(M, "→", TAB / "corpus_macros.tex")


if __name__ == "__main__":
    if sys.argv[1:] == ["universe"]:
        universe_macros()
    else:
        main()
