#!/usr/bin/env python3
"""
corelogic.py — merge + clean the manually-pulled Cotality (CoreLogic) extracts.

Inputs (Bay Area filtered extracts the user pulled from Stanford's Redivis portal
and dropped on Dropbox; PII columns were excluded at query time):
  corelogic/cotality_owner_transfer_filtered.csv  -- deeds / sales (transaction grain)
  corelogic/cotality_property_filtered.csv        -- tax-assessor + characteristics (parcel grain)

Outputs (parquet, under corelogic/clean/):
  corelogic_parcels_bg.parquet        -- one row per parcel (CLIP), chars + tax + block group
  corelogic_transactions_bg.parquet   -- one row per sale, joined to parcel chars + block group

The transaction file is the demand price spine: each residential sale carries its
block-group GEOID (geocoded from parcel lat/long against the TIGER block groups),
characteristics, and assessed value/tax, so it joins straight onto the crosswalk,
job access, soil and controls.

Run standalone:  python -m demand_estimation.corelogic
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import demand_paths as dp
from .util import sha256_file

CORELOGIC = dp.DEMAND_ROOT / "corelogic"
OWNER_TRANSFER_CSV = CORELOGIC / "cotality_owner_transfer_filtered.csv"
PROPERTY_CSV = CORELOGIC / "cotality_property_filtered.csv"
CLEAN_DIR = CORELOGIC / "clean"

EQUAL_AREA = "EPSG:3310"
RESIDENTIAL_INDICATORS = {"10", "11"}  # 10 = residential/SFR, 11 = condominium

# Property columns we keep (of the 236) -> clean names.
PROPERTY_COLS = {
    "CLIP": "clip",
    "FIPS_CODE": "fips",
    "COMPOSITE_PROPERTY_LINKAGE_KEY": "linkage_key",
    "CENSUS_ID": "census_id",
    "MUNICIPALITY_NAME": "municipality",
    "PROPERTY_INDICATOR_CODE": "prop_indicator",
    "LAND_USE_CODE": "land_use",
    "ZONING_CODE": "zoning",
    "PARCEL_LEVEL_LATITUDE": "lat",
    "PARCEL_LEVEL_LONGITUDE": "lon",
    "ASSESSED_TOTAL_VALUE": "assessed_total",
    "ASSESSED_LAND_VALUE": "assessed_land",
    "ASSESSED_IMPROVEMENT_VALUE": "assessed_improvement",
    "TOTAL_TAX_AMOUNT": "tax_amount",
    "TAX_YEAR": "tax_year",
    "TOTAL_PROPERTY_TAX_RATE_PERCENT": "tax_rate_pct",
    "YEAR_BUILT": "year_built",
    "EFFECTIVE_YEAR_BUILT": "eff_year_built",
    "TOTAL_NUMBER_OF_BEDROOMS___ALL_BUILDINGS": "bedrooms",
    "TOTAL_NUMBER_OF_BATHROOMS___ALL_BUILDINGS": "bathrooms",
    "TOTAL_NUMBER_OF_ROOMS___ALL_BUILDINGS": "rooms",
    "UNIVERSAL_BUILDING_SQUARE_FEET": "building_sqft",
    "TOTAL_LIVING_AREA_SQUARE_FEET___ALL_BUILDINGS": "living_sqft",
    "TOTAL_LAND_SQUARE_FOOTAGE": "land_sqft",
    "TOTAL_NUMBER_OF_UNITS___ALL_BUILDINGS": "units",
    "TOTAL_NUMBER_OF_STORIES": "stories",
    "FOUNDATION_TYPE_CODE": "foundation_code",
    "OWNER_OCCUPANCY_CODE": "owner_occ_code",
}
PROP_NUMERIC = ["assessed_total", "assessed_land", "assessed_improvement",
                "tax_amount", "tax_year", "tax_rate_pct", "year_built",
                "eff_year_built", "bedrooms", "bathrooms", "rooms",
                "building_sqft", "living_sqft", "land_sqft", "units", "stories",
                "lat", "lon"]

OWNER_TRANSFER_COLS = {
    "CLIP": "clip",
    "fips_code": "fips",
    "OWNER_TRANSFER_COMPOSITE_TRANSACTION_ID": "txn_id",
    "SALE_AMOUNT": "sale_amount",
    "SALE_DERIVED_DATE": "sale_date_raw",
    "SALE_DOCUMENT_TYPE_CODE": "doc_type",
    "PRIMARY_CATEGORY_CODE": "primary_category",
    "DEED_CATEGORY_TYPE_CODE": "deed_category",
    "SALE_TYPE_CODE": "sale_type",
    "INTERFAMILY_RELATED_INDICATOR": "interfamily",
    "INVESTOR_PURCHASE_INDICATOR": "investor",
    "RESALE_INDICATOR": "resale",
    "NEW_CONSTRUCTION_INDICATOR": "new_construction",
    "RESIDENTIAL_INDICATOR": "residential_ind",
    "SHORT_SALE_INDICATOR": "short_sale",
    "FORECLOSURE_REO_INDICATOR": "reo",
    "FORECLOSURE_REO_SALE_INDICATOR": "reo_sale",
    "CASH_PURCHASE_INDICATOR": "cash",
    "MORTGAGE_PURCHASE_INDICATOR": "mortgage_purchase",
}


# ---------------------------------------------------------------------------
def load_property() -> pd.DataFrame:
    print(f"[corelogic] reading property ({PROPERTY_CSV.name}) ...", flush=True)
    df = pd.read_csv(PROPERTY_CSV, usecols=list(PROPERTY_COLS), dtype=str,
                     low_memory=False)
    df = df.rename(columns=PROPERTY_COLS)
    df["fips"] = df["fips"].str.zfill(5)
    for c in PROP_NUMERIC:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["is_residential"] = df["prop_indicator"].isin(RESIDENTIAL_INDICATORS)
    # one row per parcel already; guard anyway
    df = df.drop_duplicates("clip")
    print(f"[corelogic]   property parcels: {len(df):,}", flush=True)
    return df


def geocode_parcels(parcels: pd.DataFrame) -> pd.DataFrame:
    """Assign each parcel a block-group GEOID via lat/long -> TIGER BG sjoin."""
    import geopandas as gpd
    print("[corelogic] geocoding parcels to block groups ...", flush=True)
    bg = gpd.read_file(dp.TIGER / "tl_2023_06_bg.zip")
    bg = bg[bg["COUNTYFP"].isin(dp.BAY_AREA_COUNTY_FIPS)][["GEOID", "geometry"]]
    bg = bg.to_crs(EQUAL_AREA)

    has_xy = parcels["lat"].notna() & parcels["lon"].notna()
    pts = gpd.GeoDataFrame(
        parcels.loc[has_xy, ["clip"]],
        geometry=gpd.points_from_xy(parcels.loc[has_xy, "lon"],
                                    parcels.loc[has_xy, "lat"]),
        crs="EPSG:4326").to_crs(EQUAL_AREA)
    joined = gpd.sjoin(pts, bg, predicate="within", how="left")
    # a point can fall on a boundary -> dedupe to first match
    geoid = joined.drop_duplicates("clip").set_index("clip")["GEOID"]
    parcels = parcels.merge(geoid.rename("GEOID"), left_on="clip",
                            right_index=True, how="left")
    cov = parcels["GEOID"].notna().mean()
    print(f"[corelogic]   parcels geocoded to a BG: {cov:.1%}", flush=True)
    return parcels


def load_owner_transfer() -> pd.DataFrame:
    print(f"[corelogic] reading deeds ({OWNER_TRANSFER_CSV.name}) ...", flush=True)
    df = pd.read_csv(OWNER_TRANSFER_CSV, usecols=list(OWNER_TRANSFER_COLS),
                     dtype=str, low_memory=False)
    df = df.rename(columns=OWNER_TRANSFER_COLS)
    df["fips"] = df["fips"].str.zfill(5)
    df["sale_amount"] = pd.to_numeric(df["sale_amount"], errors="coerce")
    # SALE_DERIVED_DATE is YYYYMMDD; derive year + a real date (coerce bad days)
    df["sale_year"] = pd.to_numeric(df["sale_date_raw"].str[:4], errors="coerce")
    df["sale_date"] = pd.to_datetime(df["sale_date_raw"], format="%Y%m%d",
                                     errors="coerce")
    # indicator flags -> the arms-length screen (keep raw cols too)
    def is1(col):
        return df[col].astype(str).str.strip().isin({"1", "Y", "y"})
    df["arms_length"] = (
        ~is1("interfamily") & ~is1("reo_sale") & ~is1("short_sale")
        & (df["sale_amount"] >= 1000)
    )
    print(f"[corelogic]   transactions: {len(df):,}", flush=True)
    return df


def build(manifest=None) -> dict:
    if not (OWNER_TRANSFER_CSV.exists() and PROPERTY_CSV.exists()):
        msg = "CoreLogic CSVs not present in corelogic/ — skipping"
        print(f"[corelogic] {msg}", flush=True)
        return {"status": "skipped", "note": msg}

    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    parcels_out = CLEAN_DIR / "corelogic_parcels_bg.parquet"
    txns_out = CLEAN_DIR / "corelogic_transactions_bg.parquet"

    # idempotent: skip the heavy rebuild if outputs are newer than both inputs
    if parcels_out.exists() and txns_out.exists():
        out_mtime = min(parcels_out.stat().st_mtime, txns_out.stat().st_mtime)
        in_mtime = max(OWNER_TRANSFER_CSV.stat().st_mtime, PROPERTY_CSV.stat().st_mtime)
        if out_mtime >= in_mtime:
            print("[corelogic] outputs up to date — skipping rebuild", flush=True)
            if manifest is not None:
                for p in (parcels_out, txns_out):
                    manifest.record("corelogic", local_path=p, bytes=p.stat().st_size,
                                    sha256=sha256_file(p), status="cached")
            return {"status": "cached", "parcels_path": str(parcels_out),
                    "transactions_path": str(txns_out)}

    parcels = geocode_parcels(load_property())
    parcels_out = CLEAN_DIR / "corelogic_parcels_bg.parquet"
    parcels.to_parquet(parcels_out, index=False)

    deeds = load_owner_transfer()

    # bring parcel attributes onto each transaction (CLIP join)
    pcols = ["clip", "GEOID", "census_id", "municipality", "is_residential",
             "prop_indicator", "land_use", "year_built", "bedrooms", "bathrooms",
             "building_sqft", "living_sqft", "land_sqft", "units", "stories",
             "assessed_total", "tax_amount", "tax_year", "foundation_code",
             "owner_occ_code"]
    txns = deeds.merge(parcels[pcols], on="clip", how="left",
                       suffixes=("", "_parcel"))
    txns["matched_parcel"] = txns["GEOID"].notna() | txns["is_residential"].notna()
    txns["price_per_sqft"] = txns["sale_amount"] / txns["living_sqft"].replace(0, np.nan)

    txns_out = CLEAN_DIR / "corelogic_transactions_bg.parquet"
    txns.to_parquet(txns_out, index=False)

    match_rate = txns["clip"].isin(parcels["clip"]).mean()
    geo_rate = txns["GEOID"].notna().mean()
    print(f"[corelogic] transactions matched to a parcel: {match_rate:.1%} | "
          f"with a block group: {geo_rate:.1%}", flush=True)

    if manifest is not None:
        for p in (parcels_out, txns_out):
            manifest.record("corelogic", local_path=p, bytes=p.stat().st_size,
                            sha256=sha256_file(p), status="downloaded")
        # the manual stub is now (partially) fulfilled
        manifest.record("corelogic", url="Stanford Redivis (Cotality SDP)",
                        local_path=CORELOGIC, status="landed")

    return {"status": "ok", "n_parcels": len(parcels), "n_transactions": len(txns),
            "parcel_match_rate": round(float(match_rate), 4),
            "geocode_rate": round(float(geo_rate), 4),
            "parcels_path": str(parcels_out), "transactions_path": str(txns_out)}


def main():
    res = build(manifest=None)
    print("\n=== corelogic build ===")
    for k, v in res.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
