#!/usr/bin/env python3
"""
spatial_stats.py — block-group choropleth maps for the demand-data report.

Builds a grid of Bay Area block-group choropleths (one variable per panel,
county boundaries overlaid) so the spatial structure of prices, demand, job
access, the soil instrument and amenities is visible at a glance.

    python -m demand_estimation.report.spatial_stats
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
EQUAL_AREA = "EPSG:3310"
NULL = -1e8


def _bg_value_frame() -> pd.DataFrame:
    """One row per Bay Area BG GEOID with every mappable variable."""
    out = None

    # ACS block-group tables: owner share, median value, median income
    acs = pd.read_parquet(dp.ACS_TABLES / "bg_tenure_income_value.parquet")
    acs = acs[acs["_level"] == "bg"] if "_level" in acs else acs
    acs = pd.DataFrame({
        "GEOID": acs["GEOID"].astype(str),
        "owner_share": 100 * (acs["B25003_002E"] / acs["B25003_001E"]).where(acs["B25003_001E"] > 0),
        "median_income": acs["B19013_001E"].where(acs["B19013_001E"] > NULL),
    })
    out = acs

    # design matrix: job access, transit
    dm = pd.read_parquet(dp.CLEAN / "bg_design_matrix.parquet")[
        ["GEOID", "job_access", "transit_stops_1km_all"]]
    dm["GEOID"] = dm["GEOID"].astype(str)
    out = out.merge(dm, on="GEOID", how="outer")

    # soil expansive share
    soil = pd.read_parquet(dp.SOIL_DERIVED / "bg_soil_engineering.parquet")[
        ["GEOID", "shrink_swell_expansive_share"]]
    soil["GEOID"] = soil["GEOID"].astype(str)
    out = out.merge(soil, on="GEOID", how="outer")

    # CoreLogic: BG median residential arms-length sale price, 2015+
    cl_path = CLEAN_DIR / "corelogic_transactions_bg.parquet"
    if cl_path.exists():
        cl = pd.read_parquet(cl_path, columns=["GEOID", "sale_amount", "sale_year",
                                               "is_residential", "arms_length"])
        m = (cl["is_residential"].fillna(False) & cl["arms_length"].fillna(False)
             & cl["sale_year"].between(2015, 2024)
             & cl["sale_amount"].between(10_000, 50_000_000) & cl["GEOID"].notna())
        med = cl[m].groupby(cl.loc[m, "GEOID"].astype(str))["sale_amount"].median()
        out = out.merge(med.rename("median_sale_price").reset_index().rename(
            columns={"index": "GEOID"}), on="GEOID", how="left")
    return out


def main():
    import geopandas as gpd
    FIGS.mkdir(parents=True, exist_ok=True)

    bg = gpd.read_file(dp.TIGER / "tl_2023_06_bg.zip")
    bg = bg[bg["COUNTYFP"].isin(dp.BAY_AREA_COUNTY_FIPS)][["GEOID", "geometry"]].to_crs(EQUAL_AREA)
    bg["GEOID"] = bg["GEOID"].astype(str)
    cty = gpd.read_file(dp.TIGER / "tl_2023_us_county.zip")
    cty = cty[(cty["STATEFP"] == "06") & (cty["COUNTYFP"].isin(dp.BAY_AREA_COUNTY_FIPS))].to_crs(EQUAL_AREA)

    vf = _bg_value_frame()
    g = bg.merge(vf, on="GEOID", how="left")

    panels = [
        ("median_sale_price", "Median sale price, 2015--24 (\\$)", True, "viridis"),
        ("median_income", "ACS median household income (\\$)", False, "viridis"),
        ("owner_share", "Owner-occupancy share (\\%)", False, "cividis"),
        ("job_access", "LODES job access", True, "magma"),
        ("shrink_swell_expansive_share", "Soil expansive share (instrument)", False, "YlOrBr"),
        ("transit_stops_1km_all", "Transit stops $\\leq$1\\,km", False, "BuPu"),
    ]
    fig, axes = plt.subplots(2, 3, figsize=(9.2, 7.2))
    for ax, (col, title, logc, cmap) in zip(axes.ravel(), panels):
        s = g[col]
        vals = s[s.notna()]
        if logc:
            vals = vals[vals > 0]
            norm = matplotlib.colors.LogNorm(vmin=max(vals.quantile(.02), 1),
                                             vmax=vals.quantile(.98))
        else:
            norm = matplotlib.colors.Normalize(vmin=vals.quantile(.02),
                                               vmax=vals.quantile(.98))
        g.plot(column=col, ax=ax, cmap=cmap, norm=norm, linewidth=0,
               missing_kwds={"color": "0.9"})
        cty.boundary.plot(ax=ax, color="white", linewidth=0.4)
        ax.set_title(title.replace("\\", ""), fontsize=9)
        ax.axis("off")
        sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
        fig.colorbar(sm, ax=ax, shrink=0.7, fraction=0.046, pad=0.02)
    fig.tight_layout()
    fig.savefig(FIGS / "src_maps.png", dpi=150)
    plt.close(fig)
    print("wrote figures/src_maps.png")
    # quick coverage report
    for col, *_ in panels:
        print(f"  {col}: {g[col].notna().sum():,}/{len(g):,} BGs")


if __name__ == "__main__":
    main()
