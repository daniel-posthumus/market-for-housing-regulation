#!/usr/bin/env python3
"""
corelogic_stats.py — first-pass summary stats + diagnostic plots for the
cleaned CoreLogic/Cotality extracts, for the demand-data report.

Writes LaTeX table fragments (report/corelogic_*.tex) and PNG figures
(report/figures/*.png), and prints headline numbers for the report macros.

    python -m demand_estimation.report.corelogic_stats
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .. import demand_paths as dp
from ..corelogic import CLEAN_DIR

REPORT = Path(__file__).resolve().parent
FIGS = REPORT / "figures"
COUNTY = dict(zip(dp.BAY_AREA_FIPS5, dp.BAY_AREA_COUNTIES.values()))

# headline analysis subset
YEAR_MIN = 1990
PRICE_LO, PRICE_HI = 10_000, 50_000_000


def _fmt(x, money=False):
    if pd.isna(x):
        return "--"
    if money:
        return f"\\${x:,.0f}"
    return f"{x:,.0f}"


def _analysis(txns: pd.DataFrame) -> pd.DataFrame:
    m = (txns["is_residential"].fillna(False)
         & txns["arms_length"].fillna(False)
         & txns["sale_year"].between(YEAR_MIN, 2024)
         & txns["sale_amount"].between(PRICE_LO, PRICE_HI))
    return txns[m]


def _wrap_tabular(colspec: str, header: str, rows: list[str], total: str = "") -> str:
    """Emit a complete booktabs tabular (avoids the \\input-into-tabular
    \\noalign boundary bug — the trailing newline lands after \\end{tabular})."""
    lines = ["\\begin{tabular}{" + colspec + "}", "\\toprule", header + " \\\\",
             "\\midrule", *rows]
    if total:
        lines += ["\\midrule", total]
    lines += ["\\bottomrule", "\\end{tabular}"]
    return "\n".join(lines) + "\n"


def table_by_county(a: pd.DataFrame) -> str:
    rows = []
    for fips, name in COUNTY.items():
        s = a[a["fips"] == fips]
        if not len(s):
            continue
        pps = (s["sale_amount"] / s["living_sqft"].replace(0, np.nan)).median()
        rows.append(f"{name} & {len(s):,} & {_fmt(s['sale_amount'].median(),1)} & "
                    f"{_fmt(s['sale_amount'].quantile(.25),1)} & "
                    f"{_fmt(s['sale_amount'].quantile(.75),1)} & {_fmt(pps,1)} \\\\")
    total = (f"\\textbf{{All 9 counties}} & {len(a):,} & {_fmt(a['sale_amount'].median(),1)} & "
             f"{_fmt(a['sale_amount'].quantile(.25),1)} & {_fmt(a['sale_amount'].quantile(.75),1)} & "
             f"{_fmt((a['sale_amount']/a['living_sqft'].replace(0,np.nan)).median(),1)} \\\\")
    header = "County & Sales & Median & P25 & P75 & Median \\$/sqft"
    return _wrap_tabular("lrrrrr", header, rows, total)


def table_chars(parcels: pd.DataFrame) -> str:
    res = parcels[parcels["is_residential"].fillna(False)]
    spec = [("Year built", "year_built", 0), ("Bedrooms", "bedrooms", 0),
            ("Bathrooms", "bathrooms", 0), ("Living area (sq ft)", "living_sqft", 0),
            ("Lot size (sq ft)", "land_sqft", 0),
            ("Assessed total value", "assessed_total", 1),
            ("Annual property tax", "tax_amount", 1)]
    rows = []
    for label, col, money in spec:
        if col not in res:
            continue
        v = res[col].dropna()
        v = v[v > 0] if col not in ("year_built",) else v[(v > 1800) & (v <= 2024)]
        if not len(v):
            continue
        rows.append(f"{label} & {v.notna().sum():,} & {_fmt(v.median(),money)} & "
                    f"{_fmt(v.mean(),money)} & {_fmt(v.quantile(.1),money)} & {_fmt(v.quantile(.9),money)} \\\\")
    header = "Variable & N & Median & Mean & P10 & P90"
    return _wrap_tabular("lrrrrr", header, rows)


def figures(txns: pd.DataFrame, a: pd.DataFrame) -> None:
    FIGS.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"figure.dpi": 130, "font.size": 9})

    # 1. sales per year
    yr = a["sale_year"].value_counts().sort_index()
    yr = yr[(yr.index >= YEAR_MIN) & (yr.index <= 2024)]
    fig, ax = plt.subplots(figsize=(5.4, 2.6))
    ax.bar(yr.index, yr.values, color="#3b6ea5", width=0.9)
    ax.set_title("Residential arms-length sales per year"); ax.set_xlabel("Sale year")
    ax.set_ylabel("transactions"); fig.tight_layout(); fig.savefig(FIGS / "cl_sales_by_year.png"); plt.close(fig)

    # 2. median sale price by year
    med = a.groupby("sale_year")["sale_amount"].median()
    med = med[(med.index >= YEAR_MIN) & (med.index <= 2024)]
    fig, ax = plt.subplots(figsize=(5.4, 2.6))
    ax.plot(med.index, med.values / 1e3, color="#a5343b", lw=1.8)
    ax.set_title("Median sale price by year"); ax.set_xlabel("Sale year")
    ax.set_ylabel("median price (\\$000s)".replace("\\", "")); ax.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(FIGS / "cl_median_price_by_year.png"); plt.close(fig)

    # 3. log10 sale price histogram
    fig, ax = plt.subplots(figsize=(5.4, 2.6))
    ax.hist(np.log10(a["sale_amount"]), bins=60, color="#3b8a5a")
    ax.set_title("Distribution of log10 sale price"); ax.set_xlabel("log10 sale price")
    ax.set_ylabel("transactions"); fig.tight_layout()
    fig.savefig(FIGS / "cl_price_hist.png"); plt.close(fig)

    # 4. median price by county (most recent 5 yrs)
    recent = a[a["sale_year"] >= 2019]
    by = recent.groupby("fips")["sale_amount"].median().sort_values()
    names = [COUNTY.get(f, f) for f in by.index]
    fig, ax = plt.subplots(figsize=(5.4, 2.8))
    ax.barh(names, by.values / 1e3, color="#6a51a3")
    ax.set_title("Median sale price by county (2019--2024)")
    ax.set_xlabel("median price (\\$000s)".replace("\\", "")); fig.tight_layout()
    fig.savefig(FIGS / "cl_price_by_county.png"); plt.close(fig)


def main():
    txns = pd.read_parquet(CLEAN_DIR / "corelogic_transactions_bg.parquet")
    parcels = pd.read_parquet(CLEAN_DIR / "corelogic_parcels_bg.parquet")
    a = _analysis(txns)

    (REPORT / "corelogic_tab_county.tex").write_text(table_by_county(a))
    (REPORT / "corelogic_tab_chars.tex").write_text(table_chars(parcels))
    figures(txns, a)

    # headline numbers for report macros
    out = {
        "CLNTX": f"{len(txns):,}",
        "CLNPARCEL": f"{len(parcels):,}",
        "CLNRESPARCEL": f"{int(parcels['is_residential'].fillna(False).sum()):,}",
        "CLNANALYSIS": f"{len(a):,}",
        "CLGEORATE": f"{100*txns['GEOID'].notna().mean():.1f}\\%",
        "CLMATCHRATE": f"{100*txns['clip'].isin(parcels['clip']).mean():.1f}\\%",
        "CLYEARMIN": str(int(txns['sale_year'].min())),
        "CLYEARMAX": str(int(txns['sale_year'].max())),
    }
    for k, v in out.items():
        print(f"  {k} = {v}")
    from .fill_numbers import apply as patch_macros
    patch_macros(out)
    print("patched report macros; wrote tables + figures/cl_*.png")


if __name__ == "__main__":
    main()
