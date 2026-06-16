#!/usr/bin/env python3
"""
build.py — orchestrate the demand-side data collection + derived artifacts.

Run from the repo root:

    python -m demand_estimation.build              # collect + build everything
    python -m demand_estimation.build --collect-only
    python -m demand_estimation.build --build-only

Design: idempotent and FAILURE-ISOLATED. Each collector and each derived-build
step runs inside its own try/except; a failure logs ``status=failed`` (or
``partial``) to the manifest and the run continues, so one flaky portal source
never blocks the load-bearing artifacts. Every step writes to Dropbox via
``demand_paths`` (MFHR_DATA_ROOT-aware) and logs to ``demand/_manifest.csv``.

Build steps (brief §4):
  1. BG <-> jurisdiction crosswalk   (the load-bearing artifact, §2.4)
  2. Job access from LODES WAC       (§2.3)
  3. Soil engineering extract        (§2.5; BG areal-weight + cost index = TODO)
  4. Hazard + amenity BG controls    (§2.6, §2.8)
  5. ACS PUMS assembly               (§2.1)
  6. Residualization design matrix   (§5; regression left as TODO)
  7. Manual-source stubs             (§3)
"""
from __future__ import annotations

import argparse
import sys
import traceback
import zipfile

import numpy as np
import pandas as pd

from . import demand_paths as dp
from .manifest import Manifest
from .util import make_session, sha256_file
from .collectors import (
    acs, amenities, crime, hazard, lodes, migration_irs, ssurgo, ssurgo_spatial,
    tiger, zoning,
)
from . import stubs
from . import corelogic

EQUAL_AREA = "EPSG:3310"  # California Albers (meters) for area/distance math


# ===========================================================================
# Collection
# ===========================================================================
COLLECTORS = [
    ("tiger", tiger.collect),
    ("lodes", lodes.collect),
    ("acs", acs.collect),
    ("ssurgo", ssurgo.collect),
    ("ssurgo_spatial", ssurgo_spatial.collect),
    ("hazard", hazard.collect),
    ("zoning", zoning.collect),
    ("amenities", amenities.collect),
    ("migration_irs", migration_irs.collect),
    ("crime", crime.collect),
]


def run_collectors(session, manifest) -> dict:
    results = {}
    for name, fn in COLLECTORS:
        print(f"[collect] {name} ...", flush=True)
        try:
            results[name] = fn(session, manifest)
            print(f"[collect] {name}: {results[name].get('status', 'done')}", flush=True)
        except Exception as exc:
            manifest.record(name, status="failed")
            results[name] = {"source": name, "status": "failed", "error": str(exc)}
            print(f"[collect] {name}: FAILED — {exc}", flush=True)
            traceback.print_exc()
    return results


# ===========================================================================
# Derived artifacts
# ===========================================================================
def _read_tiger(zip_name):
    import geopandas as gpd
    path = dp.TIGER / zip_name
    if not path.exists():
        raise FileNotFoundError(f"missing TIGER file {path} (run collectors first)")
    return gpd.read_file(path)


def build_crosswalk(manifest) -> dict:
    """BG -> jurisdiction (incorporated place or unincorporated remainder)."""
    import geopandas as gpd

    bg = _read_tiger("tl_2023_06_bg.zip")
    bg = bg[bg["COUNTYFP"].isin(dp.BAY_AREA_COUNTY_FIPS)].copy()
    place = _read_tiger("tl_2023_06_place.zip")

    bg = bg.to_crs(EQUAL_AREA)
    place = place.to_crs(EQUAL_AREA)
    bg["bg_area"] = bg.geometry.area

    # intersection of BGs with places; areal-weight to dominant place
    inter = gpd.overlay(
        bg[["GEOID", "COUNTYFP", "bg_area", "geometry"]],
        place[["GEOID", "NAME", "NAMELSAD", "geometry"]].rename(
            columns={"GEOID": "place_geoid", "NAME": "place_name"}),
        how="intersection", keep_geom_type=True,
    )
    inter["int_area"] = inter.geometry.area
    # dominant place per BG = max intersection area
    idx = inter.groupby("GEOID")["int_area"].idxmax()
    dom = inter.loc[idx, ["GEOID", "place_geoid", "place_name", "int_area"]].copy()

    out = bg[["GEOID", "COUNTYFP", "bg_area"]].merge(dom, on="GEOID", how="left")
    out["overlap_share"] = (out["int_area"] / out["bg_area"]).fillna(0.0)
    out["county_name"] = out["COUNTYFP"].map(dp.BAY_AREA_COUNTIES)

    # assign jurisdiction: dominant place if it covers a majority, else
    # unincorporated county remainder
    def juris(row):
        if pd.notna(row["place_name"]) and row["overlap_share"] >= 0.5:
            return row["place_name"]
        return f"Unincorporated {row['county_name']}"

    out["jurisdiction"] = out.apply(juris, axis=1)
    out["method"] = "areal_weight_dominant_place"
    out = out[["GEOID", "county_name", "COUNTYFP", "place_geoid", "place_name",
               "jurisdiction", "overlap_share", "method"]].rename(
        columns={"COUNTYFP": "county_fips"})

    dp.CROSSWALKS.mkdir(parents=True, exist_ok=True)
    path = dp.CROSSWALKS / "bg_to_jurisdiction.parquet"
    out.to_parquet(path, index=False)
    manifest.record("build_crosswalk", local_path=path, bytes=path.stat().st_size,
                    sha256=sha256_file(path), status="downloaded")
    n_juris = out["jurisdiction"].nunique()
    return {"status": "ok", "n_bg": len(out), "n_jurisdictions": n_juris, "path": str(path)}


def build_job_access(manifest, year=2022, decay_km=10.0) -> dict:
    """BG-level distance-decay job access from LODES WAC."""
    import geopandas as gpd

    wac_path = dp.LODES / f"ca_wac_S000_JT00_{year}.csv.gz"
    if not wac_path.exists():
        raise FileNotFoundError(f"missing {wac_path}")
    wac = pd.read_csv(wac_path, usecols=["w_geocode", "C000"],
                      dtype={"w_geocode": str})
    wac["bg"] = wac["w_geocode"].str[:12]
    wac = wac[wac["bg"].str[:5].isin(dp.BAY_AREA_FIPS5)]
    jobs = wac.groupby("bg")["C000"].sum().rename("jobs_in_bg")

    # BG centroids (lon/lat) for the distance-decay kernel
    bg = _read_tiger("tl_2023_06_bg.zip")
    bg = bg[bg["COUNTYFP"].isin(dp.BAY_AREA_COUNTY_FIPS)].copy()
    cent = bg.to_crs(EQUAL_AREA).geometry.centroid
    bg["x"] = cent.x.values
    bg["y"] = cent.y.values
    bg = bg[["GEOID", "x", "y"]].merge(jobs, left_on="GEOID", right_index=True, how="left")
    bg["jobs_in_bg"] = bg["jobs_in_bg"].fillna(0.0)

    xy = bg[["x", "y"]].to_numpy()
    w = bg["jobs_in_bg"].to_numpy()
    # pairwise distances in km (Albers meters -> km)
    d = np.sqrt(((xy[:, None, :] - xy[None, :, :]) ** 2).sum(-1)) / 1000.0
    kernel = np.exp(-d / decay_km)
    bg["job_access"] = kernel @ w
    bg["jobs_within_10km"] = (d <= 10.0) @ w
    bg["jobs_within_30km"] = (d <= 30.0) @ w

    out = bg[["GEOID", "jobs_in_bg", "job_access", "jobs_within_10km",
              "jobs_within_30km"]].copy()
    path = dp.LODES / f"ca_job_access_bg_{year}.parquet"
    out.to_parquet(path, index=False)
    manifest.record("build_job_access", local_path=path, bytes=path.stat().st_size,
                    sha256=sha256_file(path), status="downloaded")
    return {"status": "ok", "n_bg": len(out), "year": year,
            "total_jobs": float(w.sum()), "path": str(path)}


def build_soil_extract(manifest) -> dict:
    """Aggregate SSURGO horizons -> mukey-level engineering properties."""
    csvs = sorted(dp.SSURGO.glob("eng_props_*.csv"))
    if not csvs:
        raise FileNotFoundError("no SSURGO eng_props_*.csv (run ssurgo collector)")
    df = pd.concat([pd.read_csv(c) for c in csvs], ignore_index=True)

    numeric = ["comppct_r", "hzdept_r", "hzdepb_r", "lep_r", "pi_r", "ll_r",
               "ksat_r", "dbthirdbar_r", "awc_r"]
    for c in numeric:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")

    # dominant component per mapunit (major component, else highest pct)
    df["majcompflag"] = df["majcompflag"].astype(str).str.lower()
    df["_rank"] = df["comppct_r"].fillna(0) + (df["majcompflag"] == "yes") * 1000
    dom = df.sort_values("_rank", ascending=False).drop_duplicates("mukey")
    dom_cokeys = set(dom["cokey"])

    # depth-weighted means of the engineering properties over the dominant
    # component's horizons within the top 150 cm (foundation-relevant zone)
    hz = df[df["cokey"].isin(dom_cokeys)].copy()
    hz["top"] = hz["hzdept_r"].clip(lower=0)
    hz["bot"] = hz["hzdepb_r"].clip(upper=150)
    hz["thick"] = (hz["bot"] - hz["top"]).clip(lower=0)
    hz = hz[hz["thick"] > 0]

    def wmean(g, col):
        wsum = g["thick"].sum()
        return float((g[col] * g["thick"]).sum() / wsum) if wsum > 0 else np.nan

    rows = []
    for cokey, g in hz.groupby("cokey"):
        rows.append({
            "cokey": cokey,
            "lep_wt": wmean(g, "lep_r"),      # linear extensibility -> shrink-swell
            "pi_wt": wmean(g, "pi_r"),        # plasticity index
            "ll_wt": wmean(g, "ll_r"),        # liquid limit (Atterberg)
            "ksat_wt": wmean(g, "ksat_r"),
            "db_wt": wmean(g, "dbthirdbar_r"),
            "n_horizons": int(len(g)),
            "depth_to_150_cm": float(g["bot"].max()),
        })
    prof = pd.DataFrame(rows)

    out = dom[["mukey", "areasymbol", "muname", "cokey", "compname", "comppct_r",
               "drainagecl", "taxorder", "taxsubgrp"]].merge(prof, on="cokey", how="left")

    # shrink-swell class from linear extensibility (LEP %), NRCS thresholds
    def ss_class(lep):
        if pd.isna(lep):
            return "unknown"
        if lep < 3:
            return "low"
        if lep < 6:
            return "moderate"
        if lep < 9:
            return "high"
        return "very high"

    out["shrink_swell_class"] = out["lep_wt"].map(ss_class)
    out = out.rename(columns={"comppct_r": "dom_comppct", "compname": "dom_compname"})

    dp.SOIL_DERIVED.mkdir(parents=True, exist_ok=True)
    path = dp.SOIL_DERIVED / "mapunit_engineering_properties.parquet"
    out.to_parquet(path, index=False)
    manifest.record("build_soil_extract", local_path=path, bytes=path.stat().st_size,
                    sha256=sha256_file(path), status="downloaded")

    # document the two downstream TODOs explicitly (no fabrication)
    todo = dp.SOIL_DERIVED / "TODO_bg_aggregation_and_cost_index.md"
    todo.write_text(
        "# SSURGO soil — staged downstream steps (NOT yet built)\n\n"
        "`mapunit_engineering_properties.parquet` (mukey-level, this directory) is\n"
        "the completed engineering-property extract — the instrument's raw material.\n\n"
        "## TODO 1 — block-group areal-weighted aggregation\n"
        "`bg_soil_engineering.parquet` requires SSURGO **spatial** mapunit polygons\n"
        "(gSSURGO CA file-geodatabase, or per-survey-area spatial shapefiles) to\n"
        "areal-weight mukey properties into each block group. The tabular SDA route\n"
        "used here does not return polygons; download gSSURGO CA and intersect with\n"
        "the TIGER block groups to produce this file.\n\n"
        "## TODO 2 — predicted construction-cost index (BLOCKED on RS Means, §3.2)\n"
        "`bg_predicted_construction_cost.parquet` joins the engineering properties\n"
        "(shrink-swell / plasticity / bearing) to the RS Means foundation-cost\n"
        "schedule (`foundation_cost_schedule.csv`, a manual hand-off). Until that\n"
        "licensed schedule lands, the cost index cannot be computed.\n"
    )
    manifest.record("build_soil_extract", local_path=todo, bytes=todo.stat().st_size,
                    status="partial")
    return {"status": "ok", "n_mapunits": len(out),
            "survey_areas": sorted(out["areasymbol"].dropna().unique().tolist()),
            "bg_aggregation": "TODO (needs gSSURGO spatial)",
            "cost_index": "TODO (blocked on RS Means)", "path": str(path)}


_SS_CLASSES = ["low", "moderate", "high", "very high"]


def build_bg_soil(manifest) -> dict:
    """
    Areal-weight SSURGO map-unit engineering properties into block groups
    (follow-up brief §1). Uses the spatial MUPOLYGON WKT pulled by
    ssurgo_spatial.py, joined by mukey to the tabular engineering extract.
    Resolves the BG-aggregation half of the soil TODO; the predicted-cost
    index (RS Means) remains a manual blocker.
    """
    import geopandas as gpd
    import shapely

    csvs = sorted((dp.SSURGO / "spatial").glob("mupolygon_*.csv"))
    if not csvs:
        raise FileNotFoundError("no SSURGO spatial CSVs (run ssurgo_spatial collector)")
    poly = pd.concat([pd.read_csv(c, dtype={"mukey": str, "mupolygonkey": str})
                      for c in csvs], ignore_index=True)
    soil = gpd.GeoDataFrame(
        poly[["mupolygonkey", "mukey"]],
        geometry=shapely.from_wkt(poly["wkt"].values), crs="EPSG:4326")

    props = pd.read_parquet(dp.SOIL_DERIVED / "mapunit_engineering_properties.parquet")
    props["mukey"] = props["mukey"].astype(str)

    # --- verify the key join (brief: assert spatial covers tabular mukeys) ---
    spatial_mukeys = set(soil["mukey"])
    tab_mukeys = set(props["mukey"])
    missing_in_spatial = sorted(tab_mukeys - spatial_mukeys)
    missing_in_tabular = sorted(spatial_mukeys - tab_mukeys)
    print(f"  [bg_soil] mukeys: spatial={len(spatial_mukeys)} tabular={len(tab_mukeys)} "
          f"| tabular-not-in-spatial={len(missing_in_spatial)} "
          f"spatial-not-in-tabular={len(missing_in_tabular)}", flush=True)

    prop_cols = ["lep_wt", "pi_wt", "ll_wt", "ksat_wt", "db_wt"]
    soil = soil.merge(props[["mukey", "shrink_swell_class"] + prop_cols],
                      on="mukey", how="left").to_crs(EQUAL_AREA)

    bg = _read_tiger("tl_2023_06_bg.zip")
    bg = bg[bg["COUNTYFP"].isin(dp.BAY_AREA_COUNTY_FIPS)].to_crs(EQUAL_AREA).copy()
    bg["bg_area"] = bg.geometry.area
    bg_area_by = bg.set_index("GEOID")["bg_area"]

    # intersect soil polygons with block groups (this also clips to the 9 counties)
    inter = gpd.overlay(soil, bg[["GEOID", "geometry"]], how="intersection")
    inter["a"] = inter.geometry.area
    inter = inter[inter["a"] > 0]

    def wmean(df, col):
        m = df[col].notna()
        wsum = df.loc[m, "a"].sum()
        return float((df.loc[m, col] * df.loc[m, "a"]).sum() / wsum) if wsum > 0 else np.nan

    rows = []
    for geoid, df in inter.groupby("GEOID"):
        tot = float(df["a"].sum())
        rec = {"GEOID": geoid}
        for c in prop_cols:
            rec[c.replace("_wt", "") + "_bg"] = wmean(df, c)
        cls_area = df[df["shrink_swell_class"].isin(_SS_CLASSES)].groupby(
            "shrink_swell_class")["a"].sum()
        classified = float(cls_area.sum())
        expansive = float(cls_area.reindex(["high", "very high"]).fillna(0).sum())
        rec["shrink_swell_expansive_share"] = expansive / classified if classified else np.nan
        by_class = df.groupby("shrink_swell_class")["a"].sum()
        rec["dominant_shrink_swell_class"] = by_class.idxmax() if len(by_class) else "unknown"
        miss = float(df[df["lep_wt"].isna()]["a"].sum())
        rec["missing_frac"] = miss / tot if tot > 0 else np.nan
        rec["soil_overlap_m2"] = tot
        rec["bg_area_m2"] = float(bg_area_by.get(geoid, np.nan))
        rec["coverage_fraction"] = tot / bg_area_by[geoid] if geoid in bg_area_by else np.nan
        rec["n_mukeys"] = int(df["mukey"].nunique())
        rows.append(rec)
    agg = pd.DataFrame(rows)

    # include every BG (BGs with no soil overlap -> coverage 0), for completeness
    out = bg[["GEOID"]].drop_duplicates().merge(agg, on="GEOID", how="left")
    out["coverage_fraction"] = out["coverage_fraction"].fillna(0.0)

    path = dp.SOIL_DERIVED / "bg_soil_engineering.parquet"
    out.to_parquet(path, index=False)
    manifest.record("build_bg_soil", local_path=path, bytes=path.stat().st_size,
                    sha256=sha256_file(path), status="downloaded")

    # rewrite the TODO: BG-aggregation half DONE; only RS Means cost index remains
    todo = dp.SOIL_DERIVED / "TODO_bg_aggregation_and_cost_index.md"
    todo.write_text(
        "# SSURGO soil — downstream status\n\n"
        "`mapunit_engineering_properties.parquet` (mukey-level) is the engineering-\n"
        "property extract; `bg_soil_engineering.parquet` is now the block-group\n"
        "areal-weighted aggregate.\n\n"
        "## DONE — block-group areal-weighted aggregation\n"
        "Built from SSURGO spatial MUPOLYGON polygons (pulled via SDA by\n"
        "`ssurgo_spatial.py`), areal-intersected with the TIGER block groups in\n"
        "CA-Albers (EPSG:3310). Each BG carries area-weighted `lep`/`pi`/`ll`/`ksat`/\n"
        "bulk-density, the expansive (high + very-high shrink-swell) area share, a\n"
        "coverage fraction, and an area-weighted missingness flag.\n\n"
        "## TODO — predicted construction-cost index (BLOCKED on RS Means, manual)\n"
        "`bg_predicted_construction_cost.parquet` joins these engineering properties\n"
        "to the RS Means foundation-cost schedule (`foundation_cost_schedule.csv`, a\n"
        "licensed hand-off). Until that schedule lands the cost index cannot be\n"
        "computed. This is the only remaining blocker on the soil instrument.\n"
    )
    manifest.record("build_bg_soil", local_path=todo, bytes=todo.stat().st_size,
                    status="partial")

    return {"status": "ok", "n_bg_with_soil": int((out["coverage_fraction"] > 0).sum()),
            "n_bg": len(out),
            "mean_coverage": round(float(out["coverage_fraction"].mean()), 3),
            "tabular_mukeys_missing_geometry": len(missing_in_spatial),
            "path": str(path)}


def build_bg_controls(manifest) -> dict:
    """BG-level hazard + amenity controls for residualizing the instrument."""
    import geopandas as gpd

    bg = _read_tiger("tl_2023_06_bg.zip")
    bg = bg[bg["COUNTYFP"].isin(dp.BAY_AREA_COUNTY_FIPS)].to_crs(EQUAL_AREA).copy()
    bg["bg_area"] = bg.geometry.area
    bg_area_by_geoid = bg.set_index("GEOID")["bg_area"]
    ctrl = bg[["GEOID"]].copy()
    built = []

    # seismic hazard zones -> share of BG area in a zone
    shz = dp.HAZARD / "cgs_seismic_hazard_zones.geojson"
    if shz.exists():
        try:
            zones = gpd.read_file(shz).to_crs(EQUAL_AREA)
            inter = gpd.overlay(bg[["GEOID", "bg_area", "geometry"]],
                                zones[["geometry"]], how="intersection")
            inter["a"] = inter.geometry.area
            zone_area = inter.groupby("GEOID")["a"].sum()
            ctrl["seismic_zone_share"] = (
                ctrl["GEOID"].map(zone_area).fillna(0.0)
                / ctrl["GEOID"].map(bg_area_by_geoid)
            ).clip(upper=1.0)
            built.append("seismic_zone_share")
        except Exception as exc:
            print(f"  [controls] seismic skipped: {exc}", flush=True)

    # open space (CPAD) -> distance from BG centroid to nearest protected area
    cpad = dp.OPENSPACE / "cpad_2024a_units_bayarea.geojson"
    if cpad.exists():
        try:
            os_gdf = gpd.read_file(cpad).to_crs(EQUAL_AREA)
            cent = gpd.GeoDataFrame(bg[["GEOID"]], geometry=bg.geometry.centroid,
                                    crs=EQUAL_AREA)
            near = gpd.sjoin_nearest(cent, os_gdf[["geometry"]],
                                     distance_col="dist_m")
            near = near.groupby("GEOID")["dist_m"].min()
            ctrl["openspace_dist_m"] = ctrl["GEOID"].map(near)
            built.append("openspace_dist_m")
        except Exception as exc:
            print(f"  [controls] openspace skipped: {exc}", flush=True)

    # transit -> count of GTFS stops within 1 km of BG centroid.
    # BART + Caltrain are the keyless feeds; the 511 regional bundle (all
    # operators) is added as a separate, clearly-named column ONLY when its
    # key-gated feeds are present, so the partial-vs-full coverage is auditable
    # (follow-up brief §2). With no 511 key, only the *_bartcaltrain column exists.
    try:
        cent_buf = gpd.GeoDataFrame(
            bg[["GEOID"]], geometry=bg.geometry.centroid.buffer(1000), crs=EQUAL_AREA)

        def _count_stops(zips, colname):
            sg = _gtfs_stops_gdf(zips)
            if sg is None or not len(sg):
                return
            joined = gpd.sjoin(sg.to_crs(EQUAL_AREA), cent_buf, predicate="within")
            ctrl[colname] = ctrl["GEOID"].map(
                joined.groupby("GEOID").size()).fillna(0).astype(int)
            built.append(colname)

        bc_zips = [z for z in (dp.TRANSIT / "gtfs_bart.zip",
                               dp.TRANSIT / "gtfs_caltrain.zip") if z.exists()]
        _count_stops(bc_zips, "transit_stops_1km_bartcaltrain")

        bay511_dir = dp.TRANSIT / "bay511"
        bay511_zips = sorted(bay511_dir.glob("*.zip")) if bay511_dir.exists() else []
        if bay511_zips:  # 511 key was present -> all-operator union column
            _count_stops(bc_zips + bay511_zips, "transit_stops_1km_all")
    except Exception as exc:
        print(f"  [controls] transit skipped: {exc}", flush=True)

    if not built:
        raise RuntimeError("no control layers available to build")

    dp.CLEAN.mkdir(parents=True, exist_ok=True)
    path = dp.CLEAN / "bg_controls.parquet"
    ctrl.to_parquet(path, index=False)
    manifest.record("build_bg_controls", local_path=path, bytes=path.stat().st_size,
                    sha256=sha256_file(path), status="downloaded")
    return {"status": "ok", "controls": built, "n_bg": len(ctrl), "path": str(path)}


def _gtfs_stops_gdf(zips=None):
    """Pool stops.txt from the given GTFS zips (default: all gtfs_*.zip) into points."""
    import geopandas as gpd
    if zips is None:
        zips = list(dp.TRANSIT.glob("gtfs_*.zip"))
    rows = []
    for z in zips:
        try:
            with zipfile.ZipFile(z) as zf:
                if "stops.txt" not in zf.namelist():
                    continue
                with zf.open("stops.txt") as fh:
                    s = pd.read_csv(fh, usecols=lambda c: c in
                                    ("stop_id", "stop_lat", "stop_lon"))
                    s["agency"] = z.stem.replace("gtfs_", "")
                    rows.append(s)
        except Exception:
            continue
    if not rows:
        return None
    allstops = pd.concat(rows, ignore_index=True).dropna(subset=["stop_lat", "stop_lon"])
    return gpd.GeoDataFrame(
        allstops, geometry=gpd.points_from_xy(allstops.stop_lon, allstops.stop_lat),
        crs="EPSG:4326")


def _ba_pumas() -> set[str]:
    """2020 PUMA codes intersecting the nine Bay Area counties (from TIGER)."""
    import geopandas as gpd
    puma = _read_tiger("tl_2023_06_puma20.zip").to_crs(EQUAL_AREA)
    cty = _read_tiger("tl_2023_us_county.zip")
    cty = cty[(cty["STATEFP"] == "06") &
              (cty["COUNTYFP"].isin(dp.BAY_AREA_COUNTY_FIPS))].to_crs(EQUAL_AREA)
    code_col = "PUMACE20" if "PUMACE20" in puma.columns else "PUMACE"
    hit = gpd.sjoin(puma[[code_col, "geometry"]], cty[["geometry"]],
                    predicate="intersects")
    return set(hit[code_col].astype(str).str.zfill(5))


def build_pums(manifest) -> dict:
    """Filter CA PUMS to the analysis variables; tag Bay Area PUMAs."""
    ba = _ba_pumas()
    results = {}
    specs = {
        "household": ("csv_hca.zip",
                      ["SERIALNO", "ST", "PUMA", "WGTP", "TEN", "HINCP", "VALP",
                       "RNTP", "BDSP", "NP", "BLD", "YRBLT"],
                      "ca_pums_household.parquet"),
        "person": ("csv_pca.zip",
                   ["SERIALNO", "SPORDER", "ST", "PUMA", "PWGTP", "AGEP", "RAC1P",
                    "SCHL", "HISP", "SEX"],
                   "ca_pums_person.parquet"),
    }
    for kind, (zname, cols, outname) in specs.items():
        zpath = dp.ACS_PUMS / zname
        if not zpath.exists():
            results[kind] = {"status": "missing_zip"}
            continue
        with zipfile.ZipFile(zpath) as zf:
            csv_name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
            frames = []
            with zf.open(csv_name) as fh:
                for chunk in pd.read_csv(fh, usecols=lambda c: c in cols,
                                         dtype={"PUMA": str, "ST": str},
                                         chunksize=200_000, low_memory=False):
                    chunk["PUMA"] = chunk["PUMA"].astype(str).str.zfill(5)
                    frames.append(chunk)
        df = pd.concat(frames, ignore_index=True)
        df["bay_area"] = df["PUMA"].isin(ba)
        out_path = dp.ACS_PUMS / outname
        df.to_parquet(out_path, index=False)
        manifest.record("build_pums", local_path=out_path, bytes=out_path.stat().st_size,
                        sha256=sha256_file(out_path), status="downloaded")
        results[kind] = {"status": "ok", "rows": len(df),
                         "bay_area_rows": int(df["bay_area"].sum())}
    return {"status": "ok", "n_ba_pumas": len(ba), **results}


def build_design_matrix(manifest) -> dict:
    """
    Assemble the BG design matrix that will residualize predicted construction
    cost against {job access, seismic, open space, transit} (demand memo §5).
    The regression itself is LEFT AS A TODO — the predicted-cost index it
    residualizes is blocked on RS Means (§3.2).
    """
    pieces = {}
    cw = dp.CROSSWALKS / "bg_to_jurisdiction.parquet"
    if cw.exists():
        pieces["crosswalk"] = pd.read_parquet(cw)[
            ["GEOID", "county_name", "jurisdiction"]]
    ja = dp.LODES / "ca_job_access_bg_2022.parquet"
    if ja.exists():
        pieces["job_access"] = pd.read_parquet(ja)
    ctrl = dp.CLEAN / "bg_controls.parquet"
    if ctrl.exists():
        pieces["controls"] = pd.read_parquet(ctrl)

    if "crosswalk" not in pieces:
        raise RuntimeError("crosswalk required to anchor the design matrix")

    dm = pieces["crosswalk"]
    for key in ("job_access", "controls"):
        if key in pieces:
            dm = dm.merge(pieces[key], on="GEOID", how="left")

    dp.CLEAN.mkdir(parents=True, exist_ok=True)
    path = dp.CLEAN / "bg_design_matrix.parquet"
    dm.to_parquet(path, index=False)
    manifest.record("build_design_matrix", local_path=path, bytes=path.stat().st_size,
                    sha256=sha256_file(path), status="downloaded")

    note = dp.CLEAN / "TODO_residualization.md"
    note.write_text(
        "# Residualization regression — staged, NOT estimated\n\n"
        "`bg_design_matrix.parquet` assembles the block-group covariates that the\n"
        "demand memo (§5) residualizes the **predicted construction-cost index**\n"
        "against: job access (LODES), seismic-zone share (CGS), open-space\n"
        "distance (CPAD), transit-stop density (GTFS). Distance-to-shore and\n"
        "slope/elevation are still to be added (TIGER coastline + a DEM).\n\n"
        "The regression is intentionally left unrun: its dependent variable, the\n"
        "predicted foundation-cost index, is blocked on the RS Means schedule\n"
        "(manual, §3.2). Once `bg_predicted_construction_cost.parquet` exists,\n"
        "regress it on these covariates and keep the residual as the instrument.\n"
    )
    manifest.record("build_design_matrix", local_path=note, bytes=note.stat().st_size,
                    status="partial")
    return {"status": "ok", "n_bg": len(dm), "columns": list(dm.columns),
            "regression": "TODO (blocked on RS Means cost index)", "path": str(path)}


BUILD_STEPS = [
    ("build_crosswalk", build_crosswalk),
    ("build_job_access", build_job_access),
    ("build_soil_extract", build_soil_extract),
    ("build_bg_soil", build_bg_soil),
    ("build_bg_controls", build_bg_controls),
    ("build_pums", build_pums),
    ("build_design_matrix", build_design_matrix),
    ("build_corelogic", corelogic.build),  # manual extracts; skips if absent / up-to-date
    ("build_crime", crime.build),
]


def run_builds(manifest) -> dict:
    results = {}
    for name, fn in BUILD_STEPS:
        print(f"[build] {name} ...", flush=True)
        try:
            results[name] = fn(manifest)
            print(f"[build] {name}: {results[name].get('status')}", flush=True)
        except Exception as exc:
            manifest.record(name, status="failed")
            results[name] = {"status": "failed", "error": str(exc)}
            print(f"[build] {name}: FAILED — {exc}", flush=True)
            traceback.print_exc()
    return results


# ===========================================================================
# Entry point
# ===========================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(description="Demand-side data collection + build")
    ap.add_argument("--collect-only", action="store_true")
    ap.add_argument("--build-only", action="store_true")
    args = ap.parse_args(argv)

    dp.ensure_dirs()
    manifest = Manifest()
    session = make_session()

    print(f"DATA_ROOT  = {dp.DATA_ROOT}")
    print(f"DEMAND_ROOT= {dp.DEMAND_ROOT}\n")

    if not args.build_only:
        run_collectors(session, manifest)
    # manual stubs are cheap + always written
    stubs.write_all(manifest)
    if not args.collect_only:
        run_builds(manifest)

    print("\n=== manifest summary ===")
    for status, n in sorted(manifest.summary().items()):
        print(f"  {status:16s} {n}")
    print(f"\nmanifest: {manifest.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
