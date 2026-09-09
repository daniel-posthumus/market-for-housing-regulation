#!/usr/bin/env python3
"""
analyze_delay.py
----------------
Purpose : What predicts how long a case takes, and — the question that matters — how much of
          it is predictable at all from what a developer can observe at the first hearing.
          Descriptive predictive regressions on the case chain: OLS in levels, linear
          probability models for the tail, quantile regressions, and an out-of-sample test
          that fits on cases first heard through 2015 and forecasts 2016--2023.
Inputs  : $MFHR_DATA_ROOT/extraction/<RUN>/clean/*.jsonl
          $MFHR_DATA_ROOT/external/datasf/dbi_permits.csv.gz  (parcel → supervisor district)
          labels.db (items.block_text) — optional; supplies "arrived already continued"
Outputs : output/planning_commission_project/predicting_delay/figures/*.pdf
          output/planning_commission_project/predicting_delay/tables/delay_tables.tex
Author  : Dan Post
Created : 2026-09-08

Notes
-----
Chains reuse `chains()` from analyze_corpus: a case heard more than once is one project the
Commission did not finish with, and `days` is first hearing to last. Two-thirds of cases have
days = 0, so the outcome has a mass point at zero and log(days+1) is the natural transform.

Right-censoring is handled by dropping cases first heard within three years of the corpus
end (2026-03-26), so every case in the estimation sample has had three years to show a tail.

Geography is the supervisor district, recovered by joining the item's parcel to DBI's permit
table, which carries `supervisor_district` on 99.8% of its rows. That is cheaper and coarser
than 2,557 block fixed effects and it is the unit the politics actually runs on. Note it is
the *current* district for the parcel — DBI does not carry a historical boundary — so it is a
geographic control, not a time-varying political one.

No causal claim is made or intended anywhere in this file.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from paths import DATA_ROOT                                          # noqa: E402
from normalize import pad_key, parcel_keys                           # noqa: E402
from analyze_corpus import load, chains                              # noqa: E402
import analyze_permits as ap                                         # noqa: E402

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                      # noqa: E402
import statsmodels.api as sm                                         # noqa: E402
import statsmodels.formula.api as smf                                # noqa: E402
from patsy import dmatrices                                          # noqa: E402

warnings.filterwarnings("ignore")

MEMO = HERE.parents[1] / "output" / "planning_commission_project" / "predicting_delay"
FIG, TAB = MEMO / "figures", MEMO / "tables"
LABELS_DB = HERE / "labeling_app" / "labels.db"

CORPUS_END = pd.Timestamp("2026-03-26")     # last hearing date in the extraction
CENSOR_YEARS = 3                            # a case needs this long to reveal a tail
TRAIN_THROUGH = 2015                        # temporal split for the out-of-sample test

# Zoning-district codes, longest first so NCT is not eaten by NC. `type_district` has 681
# distinct printed values, most of them one district written several ways; the code prefix is
# the part that carries the regulatory content.
ZONES = ["WMUG", "WMUO", "RTO", "NCT", "PDR", "UMU", "SLR", "SLI", "SPD", "MUO", "MUG",
         "MUR", "DTR", "RH", "RM", "RC", "NC", "SB", "C", "M", "P"]

plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                     "figure.dpi": 150, "savefig.bbox": "tight"})

SPECS = [
    ("Request type only", "C(rt)"),
    ("$+$ staff recommendation", "C(rt)+C(prc)"),
    ("$+$ zoning district", "C(rt)+C(prc)+C(zone)"),
    ("$+$ supervisor district", "C(rt)+C(prc)+C(zone)+C(sdc)"),
    ("$+$ year of first hearing", "C(rt)+C(prc)+C(zone)+C(sdc)+C(yr)"),
    ("$+$ speakers at first hearing", "C(rt)+C(prc)+C(zone)+C(sdc)+C(yr)+lsp"),
    ("$+$ arrived already continued", "C(rt)+C(prc)+C(zone)+C(sdc)+C(yr)+lsp+cont"),
]
FULL = SPECS[-1][1]
FULL_NO_YEAR = "C(rt)+C(prc)+C(zone)+C(sdc)+lsp+cont"   # year FE cannot extrapolate forward
CATS = ["rt", "prc", "zone", "sdc"]


# ── build the case panel ─────────────────────────────────────────────────────
def supervisor_districts(items: pd.DataFrame) -> pd.Series:
    """Parcel → supervisor district, from DBI. Falls back to the block's modal district."""
    db = ap.load_dbi()
    x = db[db.supervisor_district.notna()]
    parcel = x.groupby("parcel").supervisor_district.agg(lambda s: s.mode().iat[0])
    block = (x.assign(b=x.block.fillna("").str.strip().str.upper())
              .groupby("b").supervisor_district.agg(lambda s: s.mode().iat[0]))

    # Both lookups go through `normalize.pad_key`, which pads the DIGITS and keeps the
    # letter. `zfill` on the token is a no-op once a letter makes it long enough, so the
    # earlier version missed every lettered parcel AND every lettered block — and the block
    # fallback silently missed them too, which is the failure that is hardest to notice.
    def one(r):
        b = str(r.assessor_block or "").strip()
        if not b:
            return None
        for k in parcel_keys(b, r.lot_number, ":"):
            if k in parcel.index:
                return parcel[k]
        return block.get(pad_key(b, 4))

    return items.apply(one, axis=1)


def arrived_continued(items: pd.DataFrame) -> pd.Series:
    """The minutes print '(Continued from ...)' on an item that has been heard before. The
    extraction's project_descr is the cleaned request clause and drops it, so this reads the
    source block. Optional: labels.db is a local store and may not be present."""
    if not LABELS_DB.exists():
        print("labels.db absent — 'arrived already continued' will be all False")
        return pd.Series(False, index=items.index)
    con = sqlite3.connect(LABELS_DB)
    bt = pd.read_sql("SELECT id, block_text FROM items", con).set_index("id").block_text
    return items.item_id.map(bt).fillna("").str.contains(r"(?i)\(?continued from", regex=True)


def zone_of(t) -> str:
    t = str(t or "").upper().strip()
    if not t:
        return "missing"
    for z in ZONES:
        if t.startswith(z):
            return z
    return "other"


def build() -> pd.DataFrame:
    it = load()
    it["sd"] = supervisor_districts(it)
    it["cont_in"] = arrived_continued(it)
    d = it.copy()
    d["cn"] = d.case_number.astype(str).str.upper().str.replace(r"\s+", "", regex=True)
    d = d[d.cn.ne("")]
    # THE FIRST HEARING'S ROW, not the first non-missing value of each column. `groupby.first`
    # skips nulls per column, so a covariate missing at the first hearing was silently filled
    # from a LATER one — look-ahead in a panel whose whole framing is "what was observable at
    # the first hearing", and which is then tested out of sample. `sd` was the column it
    # actually reached (it returns None on a miss, where the extracted string fields carry ""),
    # and it is parcel-level and time-invariant so nothing moved; the mechanism is the problem.
    first = (d.sort_values("meeting_date")
              .drop_duplicates("cn", keep="first").set_index("cn"))
    X = chains(it).join(first[["preliminary_recommendation_category", "type_district",
                               "speakers", "sd", "cont_in"]])
    X["nsp"] = X.speakers.map(lambda x: len(x) if isinstance(x, list) else 0)
    X["zone"] = X.type_district.map(zone_of)
    X["rt"] = X.request_type.replace("", "missing")
    X["prc"] = X.preliminary_recommendation_category.replace("", "missing")
    X["sdc"] = X.sd.fillna("missing")
    X["yr"] = X.year.astype(str)
    X["cont"] = X.cont_in.fillna(False).astype(int)
    X["lsp"] = np.log1p(X.nsp)
    X["ly"] = np.log1p(X.days)
    X["d180"] = (X.days > 180).astype(int)
    X["d365"] = (X.days > 365).astype(int)
    return X


def estimation_sample(X: pd.DataFrame) -> pd.DataFrame:
    cut = CORPUS_END - pd.DateOffset(years=CENSOR_YEARS)
    return X[(X["first"] <= cut) & X.days.notna() & (X.days >= 0)].copy()


# ── models ───────────────────────────────────────────────────────────────────
def ladder(S: pd.DataFrame):
    """How much each block of observables adds. The point of the table is the ceiling."""
    out = []
    for lab, rhs in SPECS:
        m = smf.ols(f"ly ~ {rhs}", S).fit()
        out.append((lab, int(m.nobs), m.rsquared, m.rsquared_adj, float(np.sqrt(m.mse_resid))))
    return out


def tail_models(S: pd.DataFrame):
    res = {}
    for out in ("ly", "d180", "d365"):
        m = smf.ols(f"{out} ~ {FULL}", S).fit()
        res[out] = {"n": int(m.nobs), "r2": m.rsquared, "base": float(S[out].mean()),
                    "resid_sd": float(np.sqrt(m.mse_resid)), "dep_sd": float(S[out].std())}
    return res


def out_of_sample(S: pd.DataFrame):
    """Fit on cases first heard through TRAIN_THROUGH, forecast the rest. Year fixed effects
    are dropped: a model with them cannot say anything about a year it has not seen, and the
    question is what a developer could have forecast."""
    tr = S[S.year <= TRAIN_THROUGH].copy()
    te = S[S.year > TRAIN_THROUGH].copy()
    keep = np.ones(len(te), bool)
    for c in CATS:                                   # levels absent from training are not
        keep &= te[c].isin(set(tr[c].unique())).values   # forecastable; report how many
    dropped = int((~keep).sum())
    te = te[keep]
    rows = []
    for out in ("ly", "d180", "d365"):
        m = smf.ols(f"{out} ~ {FULL_NO_YEAR}", tr).fit()
        p = m.predict(te)
        if out != "ly":
            p = p.clip(0, 1)
        r_oos = 1 - ((te[out] - p) ** 2).sum() / ((te[out] - tr[out].mean()) ** 2).sum()
        rows.append((out, int(m.nobs), len(te), m.rsquared, float(r_oos),
                     float(te[out].mean())))
    return rows, dropped


def quantiles(S: pd.DataFrame):
    """Quantile regression on the cases that came back at all. Fitting quantiles through a
    two-thirds mass point at zero is not informative, so this conditions on days > 0."""
    M = S[S.days > 0]
    y, Xm = dmatrices(f"ly ~ {FULL}", M, return_type="dataframe")
    out = []
    for tau in (0.10, 0.25, 0.50, 0.75, 0.90):
        q = sm.QuantReg(y, Xm).fit(q=tau, max_iter=4000)
        obs = M.ly.quantile(tau)
        out.append((tau, float(q.prsquared), float(np.expm1(obs))))
    return out, len(M)


def calibration(S: pd.DataFrame):
    """Sort cases by predicted delay and look at what actually happened to each decile. This
    is the honest version of the R^2: it says how far apart the model can push two cases."""
    m = smf.ols(f"ly ~ {FULL}", S).fit()
    d = S.assign(fit=m.fittedvalues)
    d["dec"] = pd.qcut(d.fit, 10, labels=False, duplicates="drop")
    t = d.groupby("dec").agg(n=("days", "size"),
                             pred=("fit", lambda v: np.expm1(v.mean())),
                             med=("days", "median"),
                             p75=("days", lambda v: v.quantile(.75)),
                             p90=("days", lambda v: v.quantile(.90)),
                             over180=("d180", "mean"), over365=("d365", "mean"))
    return t, m


def returning(S: pd.DataFrame):
    """The same model on the cases that came back at all. Reported separately because the
    residual dispersion is the memo's headline and the mass point at zero distorts it."""
    M = S[S.days > 0]
    m = smf.ols(f"ly ~ {FULL}", M).fit()
    d = M.assign(fit=m.fittedvalues)
    d["dec"] = pd.qcut(d.fit, 10, labels=False, duplicates="drop")
    top = d[d.dec == d.dec.max()].days
    sd = float(np.sqrt(m.mse_resid))
    return {"n_cases": int(len(S)), "n_returning": int(len(M)),
            "share_multi_hearing": float((S.hearings > 1).mean()),
            "share_positive_days": float((S.days > 0).mean()),
            "r2": m.rsquared, "resid_sd": sd, "resid_factor": float(np.exp(sd)),
            "dep_sd": float(M.ly.std()), "top_p10": float(top.quantile(.10)),
            "top_med": float(top.median()), "top_p90": float(top.quantile(.90)),
            "top_n": int(len(top))}


def by_recommendation(S: pd.DataFrame):
    t = S.groupby("prc").days.agg(n="size", median="median",
                                  p90=lambda v: v.quantile(.9),
                                  over365=lambda v: (v > 365).mean())
    return t[t.n >= 60].sort_values("p90", ascending=False)


def cohorts(X: pd.DataFrame):
    """The stylised facts, on all cases rather than the estimation sample, with the censored
    cohorts marked rather than dropped."""
    d = X[X.days.notna() & (X.days >= 0)]
    d = d.assign(coh=(d.year // 5) * 5)
    c = d.groupby("coh").days.agg(n="size", multi=lambda v: (v > 0).mean(), median="median",
                                  p90=lambda v: v.quantile(.9),
                                  p99=lambda v: v.quantile(.99), max="max")
    # label each cohort by the years it actually holds: the corpus starts in 1998, so the
    # bin beginning 1995 is 1998--1999 and saying otherwise invents two years of coverage
    c["lo"] = d.groupby("coh").year.min()
    c["hi"] = d.groupby("coh").year.max()
    c["censored"] = c["hi"] > (CORPUS_END.year - CENSOR_YEARS)
    return c[c.n >= 20]


# ── figures ──────────────────────────────────────────────────────────────────
def fig_predictability(S: pd.DataFrame, cal: pd.DataFrame):
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.5))
    ax = axes[0]
    x = np.arange(len(cal)) + 1
    ax.fill_between(x, cal.med, cal.p90, color="#5b7fa6", alpha=0.25,
                    label="median to 90th percentile")
    ax.plot(x, cal.p90, color="#a33", lw=1.8, label="actual 90th percentile")
    ax.plot(x, cal.med, color="#2f6f4f", lw=1.8, label="actual median")
    ax.plot(x, cal.pred, color="#333", lw=1.2, ls="--", label="model's expected delay")
    ax.set_xticks(x)
    ax.set_xlabel("decile of predicted delay")
    ax.set_ylabel("days, first hearing to last")
    ax.set_title("The model sorts the median; the spread stays", loc="left", fontsize=9)
    ax.legend(frameon=False, fontsize=7)
    ax = axes[1]
    ax.bar(x - 0.19, cal.over180 * 100, width=0.38, color="#5b7fa6", label="$>$180 days")
    ax.bar(x + 0.19, cal.over365 * 100, width=0.38, color="#a33", label="$>$365 days")
    ax.set_xticks(x)
    ax.set_xlabel("decile of predicted delay")
    ax.set_ylabel("% of cases")
    ax.set_title("Even the worst-looking decile mostly finishes", loc="left", fontsize=9)
    ax.legend(frameon=False, fontsize=7.5)
    fig.savefig(FIG / "fig_delay_predictability.pdf")
    plt.close(fig)


def fig_recommendation(S: pd.DataFrame, rec: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    d = rec.sort_values("p90")
    y = np.arange(len(d))
    ax.barh(y, d.p90, color="#5b7fa6", alpha=0.85, label="90th percentile")
    ax.barh(y, d["median"], color="#2f6f4f", height=0.45, label="median")
    ax.set_yticks(y)
    ax.set_yticklabels([f"{i.replace('_', ' ')}  (n={int(n):,})"
                        for i, n in zip(d.index, d.n)], fontsize=7.5)
    ax.set_xlabel("days, first hearing to last")
    ax.set_title("Delay by the staff recommendation published before the hearing", loc="left")
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    fig.savefig(FIG / "fig_delay_recommendation.pdf")
    plt.close(fig)


# ── tables ───────────────────────────────────────────────────────────────────
def T(s):
    return str(s).replace("_", r"\_")


def write_tables(X, S, coh, lad, tails, oos, dropped, qs, nq, cal, rec, ret):
    L = ["% GENERATED BY analyze_delay.py — do not edit by hand."]
    a = L.append

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Elapsed days from first hearing to last, by five-year cohort of first "
      r"hearing, over all %s cases. `Returned' is the share heard on more than one day. "
      r"Cohorts marked $\dagger$ are right-censored --- the corpus ends %s --- and are shown "
      r"for completeness only; the regressions drop every case first heard within %d years "
      r"of that date.}\label{tab:cohorts}"
      % (f"{len(X):,}".replace(",", "{,}"), CORPUS_END.date().isoformat(), CENSOR_YEARS))
    a(r"\begin{tabular}{lrrrrrr}\toprule")
    a(r"First heard & Cases & Returned & Median & 90th pct & 99th pct & Max\\\midrule")
    for _, r in coh.iterrows():
        lab = f"{int(r.lo)}--{int(r.hi)}" + (r"$^\dagger$" if r.censored else "")
        a(rf"{lab} & {int(r.n):,} & {100*r.multi:.0f}\% & {r['median']:.0f} & "
          rf"{r.p90:.0f} & {r.p99:.0f} & {r['max']:.0f}\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{OLS of $\log(\text{days}+1)$ on what is observable at the first hearing, "
      r"added one block at a time. Estimation sample: cases first heard on or before "
      r"%s. The dependent variable's standard deviation is %.3f.}\label{tab:ladder}"
      % ((CORPUS_END - pd.DateOffset(years=CENSOR_YEARS)).date().isoformat(), S.ly.std()))
    a(r"\begin{tabular}{lrrrr}\toprule")
    a(r"Specification & $N$ & $R^2$ & Adj.\ $R^2$ & Residual s.d.\\\midrule")
    for lab, n, r2, ar2, sd in lad:
        a(rf"{lab} & {n:,} & {r2:.3f} & {ar2:.3f} & {sd:.3f}\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The full specification on three outcomes. The level is fitted two to three "
      r"times as well as the tail indicators, and no outcome gets past a fifth of its "
      r"variance.}\label{tab:tails}")
    a(r"\begin{tabular}{lrrrr}\toprule")
    a(r"Outcome & $N$ & Mean & $R^2$ & Unexplained variance\\\midrule")
    names = {"ly": r"$\log(\text{days}+1)$", "d180": r"$\mathbb{1}[\text{days}>180]$",
             "d365": r"$\mathbb{1}[\text{days}>365]$"}
    for k, v in tails.items():
        a(rf"{names[k]} & {v['n']:,} & {v['base']:.3f} & {v['r2']:.3f} & "
          rf"{100*(1-v['r2']):.1f}\%\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Out of sample. The model is fitted on cases first heard through %d and used "
      r"to forecast cases first heard from %d on; year fixed effects are dropped because they "
      r"cannot extrapolate. %s excluded for carrying a category level that never appears in "
      r"the training years. Out-of-sample $R^2$ is computed against the training mean, so a "
      r"negative value would mean the model forecasts worse than that "
      r"constant.}\label{tab:oos}"
      % (TRAIN_THROUGH, TRAIN_THROUGH + 1,
         "One test case is" if dropped == 1 else f"{dropped:,} test cases are"))
    a(r"\begin{tabular}{lrrrrr}\toprule")
    a(r"Outcome & Train $N$ & Test $N$ & In-sample $R^2$ & Out-of-sample $R^2$ & Test mean\\"
      r"\midrule")
    for out, ntr, nte, r2, roos, mean in oos:
        a(rf"{names[out]} & {ntr:,} & {nte:,} & {r2:.3f} & {roos:.3f} & {mean:.3f}\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Quantile regressions of $\log(\text{days}+1)$ on the full specification, "
      r"conditional on the case returning at all ($N=%d$). Pseudo-$R^2$ does not fall in the "
      r"upper tail --- the model fits the 90th percentile about as well as the median, in "
      r"relative terms. What grows is the quantity being fitted.}\label{tab:quantiles}" % nq)
    a(r"\begin{tabular}{lrr}\toprule")
    a(r"Quantile & Pseudo-$R^2$ & Unconditional value (days)\\\midrule")
    for tau, pr, obs in qs:
        a(rf"$\tau={tau:.2f}$ & {pr:.3f} & {obs:.0f}\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Cases sorted into deciles of predicted delay, against what actually "
      r"happened. The model separates the deciles by a factor of a hundred in expectation "
      r"and by a factor of about ten in realised median, but the tail risk in the worst "
      r"decile is still only one case in twenty.}\label{tab:calibration}")
    a(r"\begin{tabular}{lrrrrrr}\toprule")
    a(r"Decile & Cases & Expected & Median & 75th & 90th & $>$365 d\\\midrule")
    for i, r in cal.iterrows():
        a(rf"{int(i)+1} & {int(r.n):,} & {r.pred:.1f} & {r.med:.0f} & {r.p75:.0f} & "
          rf"{r.p90:.0f} & {100*r.over365:.1f}\%\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The same specification on the cases that came back at all. The residual "
      r"spread, not the fit, is the quantity of interest: a one-standard-deviation surprise "
      r"multiplies realised delay by %.1f, and inside the decile the model expects to take "
      r"longest the realised distribution still runs from %.0f to %.0f "
      r"days.}\label{tab:returning}" % (ret["resid_factor"], ret["top_p10"], ret["top_p90"]))
    a(r"\begin{tabular}{lr}\toprule")
    a(r"Quantity & Value\\\midrule")
    for lab, val in (
        ("Cases in the estimation sample", f"{ret['n_cases']:,}"),
        (r"\quad heard on more than one day", rf"{100*ret['share_multi_hearing']:.1f}\%"),
        (r"\quad with a positive elapsed time", rf"{100*ret['share_positive_days']:.1f}\%"),
        ("Cases used here (days $>$ 0)", f"{ret['n_returning']:,}"),
        ("$R^2$", f"{ret['r2']:.3f}"),
        (r"Dependent-variable s.d.\ (log points)", f"{ret['dep_sd']:.3f}"),
        (r"\textbf{Residual s.d.\ (log points)}", rf"\textbf{{{ret['resid_sd']:.3f}}}"),
        (r"\quad as a multiplicative factor", rf"{ret['resid_factor']:.1f}$\times$"),
        (rf"Top predicted decile ($n={ret['top_n']:,}$): 10th pct",
         rf"{ret['top_p10']:.0f} days"),
        (r"\quad median", rf"{ret['top_med']:.0f} days"),
        (r"\quad 90th pct", rf"{ret['top_p90']:.0f} days"),
    ):
        a(rf"{lab} & {val}\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Delay by the staff recommendation published before the first hearing. It is "
      r"the single most informative thing a developer can read, and it is public and dated. "
      r"Categories with fewer than 60 cases omitted.}\label{tab:recommendation}")
    a(r"\begin{tabular}{lrrrr}\toprule")
    a(r"Preliminary recommendation & Cases & Median & 90th pct & $>$365 days\\\midrule")
    for i, r in rec.iterrows():
        a(rf"\texttt{{{T(i)}}} & {int(r.n):,} & {r['median']:.0f} & {r.p90:.0f} & "
          rf"{100*r.over365:.1f}\%\\")
    a(r"\bottomrule\end{tabular}\end{table}")

    TAB.mkdir(parents=True, exist_ok=True)
    (TAB / "delay_tables.tex").write_text("\n".join(L) + "\n")
    print("→", TAB / "delay_tables.tex")


def main():
    FIG.mkdir(parents=True, exist_ok=True)
    TAB.mkdir(parents=True, exist_ok=True)
    X = build()
    S = estimation_sample(X)
    print(f"{len(X):,} cases; {len(S):,} in the estimation sample "
          f"(first heard on or before {(CORPUS_END - pd.DateOffset(years=CENSOR_YEARS)).date()})")
    coh = cohorts(X)
    lad = ladder(S)
    tails = tail_models(S)
    oos, dropped = out_of_sample(S)
    qs, nq = quantiles(S)
    cal, m = calibration(S)
    rec = by_recommendation(S)
    ret = returning(S)
    fig_predictability(S, cal)
    fig_recommendation(S, rec)
    write_tables(X, S, coh, lad, tails, oos, dropped, qs, nq, cal, rec, ret)
    print(f"R2 {tails['ly']['r2']:.3f}; unexplained {100*(1-tails['ly']['r2']):.1f}%; "
          f"OOS R2 {oos[0][4]:.3f}")
    print("figures written to", FIG)


if __name__ == "__main__":
    main()
