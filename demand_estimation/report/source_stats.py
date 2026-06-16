#!/usr/bin/env python3
"""
source_stats.py — first-pass summary stats + diagnostic plots for EVERY
demand-side source (ACS PUMS, ACS BG tables, LODES job access, TIGER/crosswalk,
SSURGO soil, CGS hazard, CPAD open space, GTFS transit, IRS migration, zoning,
CDE schools). Writes LaTeX table fragments (report/src_*.tex) and PNG figures
(report/figures/src_*.png) for the report's "Source diagnostics" section.

    python -m demand_estimation.report.source_stats
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .. import demand_paths as dp

REPORT = Path(__file__).resolve().parent
FIGS = REPORT / "figures"
COUNTY = dict(zip(dp.BAY_AREA_FIPS5, dp.BAY_AREA_COUNTIES.values()))
BA_NAMES = list(dp.BAY_AREA_COUNTIES.values())
NULL = -1e8  # Census sentinel guard


def _fmt(x, money=False, pct=False):
    if pd.isna(x):
        return "--"
    if pct:
        return f"{x:.1f}\\%"
    if money:
        return f"\\${x:,.0f}"
    return f"{x:,.1f}" if (abs(x) < 100 and x != int(x)) else f"{x:,.0f}"


def _tab(colspec, header, rows):
    out = ["\\begin{tabular}{" + colspec + "}", "\\toprule", header + " \\\\",
           "\\midrule", *rows, "\\bottomrule", "\\end{tabular}"]
    (REPORT).mkdir(exist_ok=True)
    return "\n".join(out) + "\n"


def _save(fig, name):
    FIGS.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(); fig.savefig(FIGS / name); plt.close(fig)


def _q(s):
    s = s.dropna()
    return (len(s), s.median(), s.mean(), s.quantile(.1), s.quantile(.9))


# ---------------------------------------------------------------------------
def _wq(v, w, q):
    v, w = np.asarray(v, float), np.asarray(w, float)
    m = ~np.isnan(v)
    v, w = v[m], w[m]
    if not len(v):
        return np.nan
    o = np.argsort(v)
    v, cw = v[o], np.cumsum(w[o])
    return float(v[np.searchsorted(cw, q * cw[-1])])


def _wmean(v, w):
    v, w = np.asarray(v, float), np.asarray(w, float)
    m = ~np.isnan(v)
    return float(np.average(v[m], weights=w[m])) if m.any() else np.nan


def acs_pums():
    hh = pd.read_parquet(dp.ACS_PUMS / "ca_pums_household.parquet")
    hh = hh[hh["bay_area"]].copy()
    w = hh["WGTP"]
    owner = hh["TEN"].isin([1, 2])
    renter = hh["TEN"].isin([3, 4])
    rows = []
    for label, s, wt in [("Household income (HINCP)", hh["HINCP"], w),
                         ("Home value, owners (VALP)", hh.loc[owner, "VALP"], w[owner]),
                         ("Gross rent, renters (RNTP)", hh.loc[renter, "RNTP"], w[renter]),
                         ("Bedrooms (BDSP)", hh["BDSP"], w),
                         ("Household size (NP)", hh["NP"], w)]:
        pos = s > 0 if "income" not in label else s.notna()
        v, ww = s[pos], wt[pos]
        money = any(k in label for k in ("income", "value", "rent"))
        rows.append(f"{label} & {ww.sum():,.0f} & {_fmt(_wq(v,ww,.5),money)} & "
                    f"{_fmt(_wmean(v,ww),money)} & {_fmt(_wq(v,ww,.1),money)} & "
                    f"{_fmt(_wq(v,ww,.9),money)} \\\\")
    rows.append("\\midrule")
    occ = w[owner | renter].sum()
    rows.append(f"Owner-occupied & \\multicolumn{{4}}{{r}}{{{100*w[owner].sum()/occ:.1f}\\% of occupied}} \\\\")
    rows.append(f"Renter-occupied & \\multicolumn{{4}}{{r}}{{{100*w[renter].sum()/occ:.1f}\\% of occupied}} \\\\")
    (REPORT / "src_pums.tex").write_text(
        _tab("lrrrrr",
             "Variable & Households (wtd) & Median & Mean & P10 & P90", rows))

    fig, ax = plt.subplots(1, 2, figsize=(5.6, 2.5))
    inc = (hh["HINCP"].clip(0, 600_000) / 1e3)
    mok = inc.notna()
    ax[0].hist(inc[mok], bins=60, weights=w[mok], color="#3b6ea5")
    ax[0].set_title("Household income (weighted)"); ax[0].set_xlabel("\\$000s".replace("\\", "")); ax[0].set_ylabel("households")
    ax[1].bar(["Own", "Rent"], [w[owner].sum(), w[renter].sum()], color=["#3b8a5a", "#a5343b"])
    ax[1].set_title("Tenure (weighted)"); ax[1].set_ylabel("households")
    _save(fig, "src_pums.png")
    return {"PUMSHHBA": f"{len(hh):,}"}


def acs_bg():
    d = pd.read_parquet(dp.ACS_TABLES / "bg_tenure_income_value.parquet")
    d = d[d["_level"] == "bg"] if "_level" in d else d
    tot, own = d["B25003_001E"], d["B25003_002E"]
    owner_share = 100 * (own / tot).where(tot > 0)
    val = d["B25077_001E"].where(d["B25077_001E"] > NULL)
    rent = d["B25064_001E"].where(d["B25064_001E"] > NULL)
    inc = d["B19013_001E"].where(d["B19013_001E"] > NULL)
    rows = []
    for label, s, money, pct in [("Owner-occupancy share (\\%)", owner_share, 0, 1),
                                  ("Median home value", val, 1, 0),
                                  ("Median gross rent", rent, 1, 0),
                                  ("Median household income", inc, 1, 0)]:
        n, med, mean, p10, p90 = _q(s)
        rows.append(f"{label} & {n:,} & {_fmt(med,money,pct)} & {_fmt(mean,money,pct)} & "
                    f"{_fmt(p10,money,pct)} & {_fmt(p90,money,pct)} \\\\")
    (REPORT / "src_acsbg.tex").write_text(
        _tab("lrrrrr", "Across block groups & N & Median & Mean & P10 & P90", rows))

    fig, ax = plt.subplots(1, 2, figsize=(5.6, 2.5))
    ax[0].hist(owner_share.dropna(), bins=40, color="#6a51a3")
    ax[0].set_title("Owner-occupancy share by BG"); ax[0].set_xlabel("\\%".replace("\\", "")); ax[0].set_ylabel("block groups")
    ax[1].hist((val / 1e3).dropna().clip(0, 2000), bins=40, color="#a5343b")
    ax[1].set_title("Median home value by BG"); ax[1].set_xlabel("\\$000s".replace("\\", ""))
    _save(fig, "src_acsbg.png")
    return {}


def bg_covariates():
    dm = pd.read_parquet(dp.CLEAN / "bg_design_matrix.parquet")
    soil = pd.read_parquet(dp.SOIL_DERIVED / "bg_soil_engineering.parquet")
    dm = dm.merge(soil[["GEOID", "lep_bg", "shrink_swell_expansive_share",
                        "coverage_fraction"]], on="GEOID", how="left")
    spec = [("Jobs located in BG (LODES)", "jobs_in_bg"),
            ("Job access (decay-weighted)", "job_access"),
            ("Jobs within 30 km", "jobs_within_30km"),
            ("Seismic-zone area share (CGS)", "seismic_zone_share"),
            ("Open-space distance, m (CPAD)", "openspace_dist_m"),
            ("Transit stops $\\leq$1\\,km, all ops", "transit_stops_1km_all"),
            ("Soil expansive share (SSURGO)", "shrink_swell_expansive_share"),
            ("Soil linear extensibility, lep", "lep_bg")]
    rows = []
    for label, col in spec:
        if col not in dm:
            continue
        n, med, mean, p10, p90 = _q(dm[col])
        rows.append(f"{label} & {n:,} & {_fmt(med)} & {_fmt(mean)} & {_fmt(p10)} & {_fmt(p90)} \\\\")
    (REPORT / "src_bgcov.tex").write_text(
        _tab("lrrrrr", "Block-group covariate & N & Median & Mean & P10 & P90", rows))

    # job access distribution
    fig, ax = plt.subplots(figsize=(5.4, 2.5))
    ja = dm["job_access"].replace(0, np.nan).dropna()
    ax.hist(np.log10(ja), bins=50, color="#3b6ea5")
    ax.set_title("Job access by block group"); ax.set_xlabel("log10 distance-decay job access")
    ax.set_ylabel("block groups"); _save(fig, "src_jobaccess.png")

    # controls small multiples
    fig, ax = plt.subplots(1, 3, figsize=(6.6, 2.2))
    ax[0].hist(dm["seismic_zone_share"].dropna(), bins=30, color="#a5343b")
    ax[0].set_title("Seismic-zone share"); ax[0].set_xlabel("share of BG area")
    ax[1].hist(dm["openspace_dist_m"].dropna().clip(0, 3000), bins=30, color="#3b8a5a")
    ax[1].set_title("Open-space distance"); ax[1].set_xlabel("m to nearest")
    ax[2].hist(dm["transit_stops_1km_all"].dropna().clip(0, 200), bins=30, color="#6a51a3")
    ax[2].set_title("Transit stops $\\leq$1 km"); ax[2].set_xlabel("count")
    _save(fig, "src_controls.png")

    # soil expansive share
    fig, ax = plt.subplots(figsize=(5.4, 2.5))
    ax.hist(dm["shrink_swell_expansive_share"].dropna(), bins=30, color="#8a5a3b")
    ax.set_title("Soil expansive (high+very-high shrink-swell) share by BG")
    ax.set_xlabel("area share"); ax.set_ylabel("block groups"); _save(fig, "src_soil.png")
    return {}


def geography():
    cw = pd.read_parquet(dp.CROSSWALKS / "bg_to_jurisdiction.parquet")
    rows = []
    for fips, name in COUNTY.items():
        s = cw[cw["county_fips"] == fips[2:]]
        if not len(s):
            s = cw[cw["county_name"] == name]
        uninc = s["jurisdiction"].str.startswith("Unincorporated").sum()
        rows.append(f"{name} & {len(s):,} & {s['jurisdiction'].nunique()} & {uninc} \\\\")
    rows.append("\\midrule")
    uninc = cw["jurisdiction"].str.startswith("Unincorporated").sum()
    rows.append(f"\\textbf{{All 9 counties}} & {len(cw):,} & {cw['jurisdiction'].nunique()} & {uninc} \\\\")
    (REPORT / "src_geo.tex").write_text(
        _tab("lrrr", "County & Block groups & Jurisdictions & Uninc.\\ BGs", rows))
    return {}


def irs_migration():
    base = dp.MIGRATION_IRS
    inflow = pd.read_csv(base / "countyinflow2122.csv", encoding="latin-1")
    outflow = pd.read_csv(base / "countyoutflow2122.csv", encoding="latin-1")
    for df in (inflow, outflow):
        for c in ("y2_statefips", "y2_countyfips", "y1_statefips", "y1_countyfips", "n1"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["n1"] = df["n1"].clip(lower=0)  # IRS marks suppressed cells -1

    def in_to(c):  # in-migration to CA county c: real origin state, exclude self (non-movers)
        m = ((inflow.y2_statefips == 6) & (inflow.y2_countyfips == c)
             & inflow.y1_statefips.between(1, 56)
             & ~((inflow.y1_statefips == 6) & (inflow.y1_countyfips == c)))
        return inflow.loc[m, "n1"].sum()

    def out_of(c):  # out-migration from CA county c: real destination state, exclude self
        m = ((outflow.y1_statefips == 6) & (outflow.y1_countyfips == c)
             & outflow.y2_statefips.between(1, 56)
             & ~((outflow.y2_statefips == 6) & (outflow.y2_countyfips == c)))
        return outflow.loc[m, "n1"].sum()

    rows = []
    tot_in = tot_out = 0
    nets, names = [], []
    for fips, name in COUNTY.items():
        c = int(fips[2:])
        ins, outs = in_to(c), out_of(c)
        tot_in += ins; tot_out += outs
        nets.append(ins - outs); names.append(name)
        rows.append(f"{name} & {ins:,.0f} & {outs:,.0f} & {ins-outs:+,.0f} \\\\")
    rows.append("\\midrule")
    rows.append(f"\\textbf{{All 9 counties}} & {tot_in:,.0f} & {tot_out:,.0f} & {tot_in-tot_out:+,.0f} \\\\")
    (REPORT / "src_irs.tex").write_text(
        _tab("lrrr", "County & In-returns & Out-returns & Net", rows))

    fig, ax = plt.subplots(figsize=(5.4, 2.6))
    order = np.argsort(nets)
    cols = ["#a5343b" if nets[i] < 0 else "#3b8a5a" for i in order]
    ax.barh([names[i] for i in order], [nets[i] for i in order], color=cols)
    ax.set_title("Net domestic migration by county (IRS, 2021--22)")
    ax.set_xlabel("net tax returns (households)"); ax.axvline(0, color="k", lw=.6)
    _save(fig, "src_irs.png")
    return {}


def zoning():
    import pyogrio
    g = pyogrio.read_dataframe(dp.ZONING / "ca_statewide_zoning_bayarea.geojson",
                              columns=["Code", "Description"], read_geometry=False)
    top = g["Code"].value_counts().head(15)
    fig, ax = plt.subplots(figsize=(5.6, 3.0))
    ax.barh(top.index[::-1].astype(str), top.values[::-1], color="#6a51a3")
    ax.set_title("Top zoning codes (Gov-OPR, 9-county)")
    ax.set_xlabel("parcels"); _save(fig, "src_zoning.png")
    return {"ZONEN": f"{len(g):,}", "ZONECODES": f"{g['Code'].nunique():,}"}


def schools():
    f = next(iter(dp.SCHOOLS.glob("*.txt")), None)
    if f is None:
        return {}
    df = pd.read_csv(f, sep="\t", dtype=str)
    df.columns = [c.strip().lstrip("﻿") for c in df.columns]
    sch = df[(df["AggregateLevel"] == "S") & (df["ReportingCategory"] == "TA")].copy()
    sch = sch[sch["CountyName"].isin(BA_NAMES)]
    sch["enr"] = pd.to_numeric(sch["CumulativeEnrollment"], errors="coerce")
    fig, ax = plt.subplots(1, 2, figsize=(6.4, 2.6))
    ax[0].hist(sch["enr"].dropna().clip(0, 3000), bins=40, color="#3b8a5a")
    ax[0].set_title("School enrollment (Bay Area)"); ax[0].set_xlabel("cumulative enrollment")
    ax[0].set_ylabel("schools")
    by = sch.groupby("CountyName")["SchoolCode"].nunique().sort_values()
    ax[1].barh(by.index, by.values, color="#3b6ea5")
    ax[1].set_title("Schools per county"); ax[1].set_xlabel("schools")
    _save(fig, "src_schools.png")
    return {"NSCHOOLS": f"{sch['SchoolCode'].nunique():,}"}


def crime():
    import geopandas as gpd
    c = pd.read_parquet(dp.CRIME / "bg_crime.parquet")
    fbi = pd.read_csv(dp.CRIME / "fbi_county_crime.csv", dtype={"county_fips": str})

    # county-level FBI table (full coverage)
    rows = []
    for _, r in fbi.sort_values("crime_rate_per_100k", ascending=False).iterrows():
        rows.append(f"{r['county']} & {r['violent']:,.0f} & {r['property']:,.0f} & "
                    f"{r['crime_rate_per_100k']:,.0f} \\\\")
    (REPORT / "src_crime.tex").write_text(
        _tab("lrrr", f"County (FBI {fbi['year'].iloc[0]}) & Violent & Property & Rate/100k", rows))

    inc_bg = int(c["incidents"].notna().sum())
    inc_tot = int(c["incidents"].fillna(0).sum())

    bg = gpd.read_file(dp.TIGER / "tl_2023_06_bg.zip")
    bg = bg[bg["COUNTYFP"].isin(dp.BAY_AREA_COUNTY_FIPS)][["GEOID", "geometry"]].to_crs("EPSG:3310")
    bg["GEOID"] = bg["GEOID"].astype(str)
    g = bg.merge(c, on="GEOID", how="left")
    cty = gpd.read_file(dp.TIGER / "tl_2023_us_county.zip")
    cty = cty[(cty["STATEFP"] == "06") & cty["COUNTYFP"].isin(dp.BAY_AREA_COUNTY_FIPS)].to_crs("EPSG:3310")

    fig, ax = plt.subplots(1, 2, figsize=(9.0, 3.6))
    g.plot(column="county_crime_rate_per_100k", ax=ax[0], cmap="OrRd", legend=True,
           linewidth=0, missing_kwds={"color": "0.9"})
    cty.boundary.plot(ax=ax[0], color="white", linewidth=0.4)
    ax[0].set_title("County crime rate /100k (FBI, all 9 counties)"); ax[0].axis("off")
    vmax = g["crime_per_1k_hh_yr"].quantile(.95)
    g.plot(column="crime_per_1k_hh_yr", ax=ax[1], cmap="Reds", vmax=vmax, legend=True,
           linewidth=0, missing_kwds={"color": "0.9"})
    cty.boundary.plot(ax=ax[1], color="0.5", linewidth=0.4)
    ax[1].set_title("Incident crime /1k hh/yr (SF + Oakland BGs)"); ax[1].axis("off")
    _save(fig, "src_crime.png")
    return {"CRIMEN": f"{inc_tot:,}", "CRIMEBG": f"{inc_bg:,}"}


def main():
    macros = {}
    for fn in (acs_pums, acs_bg, bg_covariates, geography, irs_migration, zoning,
               schools, crime):
        try:
            macros.update(fn() or {})
            print(f"  ok: {fn.__name__}")
        except Exception as exc:
            print(f"  FAILED {fn.__name__}: {exc}")
    from .fill_numbers import apply as patch
    if macros:
        patch(macros)
    print("source diagnostics: tables src_*.tex + figures/src_*.png")
    print("macros:", macros)


if __name__ == "__main__":
    main()
