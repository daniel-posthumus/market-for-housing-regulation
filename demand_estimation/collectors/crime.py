#!/usr/bin/env python3
"""
Crime (location disamenity / control).

Crime is a classic location (dis)amenity in a housing-demand model. Coverage is
assembled from three layers, finest first:

  * INCIDENT-LEVEL, geocoded to block group:
      - San Francisco PD incident reports (DataSF / Socrata `wg3w-h783`), with
        coordinates.
      - Oakland PD crime reports (data.oaklandca.gov `ppgh-7dqv`), address-only
        -> geocoded to block group via the free Census batch geocoder.
  * COUNTY-LEVEL, all nine counties (full coverage): FBI Crime Data Explorer
    (api.data.gov key) -- violent + property offenses summed over every agency
    in each county -> a county rate per 100k, broadcast to every block group.

San Jose's only open feed is "calls for service" (police activity, not crime
reports) behind a portal interstitial, so San Jose is covered by the FBI Santa
Clara county rate rather than incident-level.

The build writes `crime/bg_crime.parquet` (incident rate where available + the
county rate everywhere) and merges both into `bg_controls`.
"""
from __future__ import annotations

import csv
import io
import time

import pandas as pd

from .. import demand_paths as dp
from ..util import request_with_retry, resolve_data_gov_key, sha256_file

# ---- incident-level city feeds (Socrata) -----------------------------------
SF_BASE = "https://data.sfgov.org/resource/wg3w-h783.csv"
OAK_BASE = "https://data.oaklandca.gov/resource/ppgh-7dqv.csv"
SINCE = "2019-01-01"          # common window across cities
PAGE = 50_000

# ---- FBI Crime Data Explorer -----------------------------------------------
FBI_AGENCIES = "https://api.usa.gov/crime/fbi/cde/agency/byStateAbbr/CA"
FBI_AGENCY = "https://api.usa.gov/crime/fbi/cde/summarized/agency/{ori}/{offense}"
FBI_YEAR = 2022
# county name (as FBI returns it, upper) -> our 5-digit FIPS
FBI_COUNTIES = {
    "ALAMEDA": "06001", "CONTRA COSTA": "06013", "MARIN": "06041",
    "NAPA": "06055", "SAN FRANCISCO": "06075", "SAN MATEO": "06081",
    "SANTA CLARA": "06085", "SOLANO": "06095", "SONOMA": "06097",
}

CENSUS_BATCH = "https://geocoding.geo.census.gov/geocoder/geographies/addressbatch"

VIOLENT = {"Assault", "Robbery", "Homicide", "Rape", "Sex Offense", "Weapons Offense"}
PROPERTY = {"Larceny Theft", "Burglary", "Motor Vehicle Theft", "Vandalism",
            "Arson", "Stolen Property", "Fraud", "Embezzlement"}


# ===========================================================================
# Collectors
# ===========================================================================
def _socrata_pull(session, base, select, where, order=":id") -> pd.DataFrame:
    frames, offset = [], 0
    while True:
        params = {"$select": select, "$where": where, "$order": order,
                  "$limit": str(PAGE), "$offset": str(offset)}
        resp = request_with_retry(session, "GET", base, params=params, timeout=120)
        resp.raise_for_status()
        page = pd.read_csv(io.StringIO(resp.text))
        if page.empty:
            break
        frames.append(page)
        offset += len(page)
        if len(page) < PAGE:
            break
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def collect_sf(session, manifest) -> dict:
    dest = dp.CRIME / "sf_incidents.parquet"
    if dest.exists() and dest.stat().st_size > 0:
        manifest.record("crime", url=SF_BASE, local_path=dest, bytes=dest.stat().st_size,
                        sha256=sha256_file(dest), status="cached")
        return {"city": "SF", "status": "cached"}
    df = _socrata_pull(session, SF_BASE,
                       "incident_date,incident_category,latitude,longitude",
                       f"incident_date >= '{SINCE}' AND latitude IS NOT NULL")
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df = df.dropna(subset=["latitude", "longitude"])
    df.to_parquet(dest, index=False)
    manifest.record("crime", url=SF_BASE, local_path=dest, bytes=dest.stat().st_size,
                    sha256=sha256_file(dest), status="downloaded")
    return {"city": "SF", "status": "ok", "incidents": len(df)}


def collect_oakland(session, manifest) -> dict:
    dest = dp.CRIME / "oakland_incidents.parquet"
    if dest.exists() and dest.stat().st_size > 0:
        manifest.record("crime", url=OAK_BASE, local_path=dest, bytes=dest.stat().st_size,
                        sha256=sha256_file(dest), status="cached")
        return {"city": "Oakland", "status": "cached"}
    df = _socrata_pull(session, OAK_BASE, "datetime,crimetype,address,city",
                       f"datetime >= '{SINCE}' AND address IS NOT NULL")
    df = df[df["address"].astype(str).str.strip() != ""]
    df.to_parquet(dest, index=False)
    manifest.record("crime", url=OAK_BASE, local_path=dest, bytes=dest.stat().st_size,
                    sha256=sha256_file(dest), status="downloaded")
    return {"city": "Oakland", "status": "ok", "incidents": len(df)}


def _fbi_get(session, url, params):
    for attempt in range(3):
        try:
            r = session.get(url, params=params, timeout=60)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(0.3 * (attempt + 1))
    return None


def collect_fbi(session, manifest) -> dict:
    key = resolve_data_gov_key()
    if not key:
        manifest.record("crime", url=FBI_AGENCIES, local_path=dp.CRIME / "_fbi",
                        status="needs_key")
        return {"layer": "fbi_county", "status": "needs_key"}
    agencies = _fbi_get(session, FBI_AGENCIES, {"API_KEY": key})
    if not isinstance(agencies, dict):
        return {"layer": "fbi_county", "status": "failed"}

    def sum_offenses(js):
        act = (js or {}).get("offenses", {}).get("actuals", {})
        pops = (js or {}).get("populations", {}).get("population", {})
        off = [k for k in act if k.endswith("Offenses")
               and not k.startswith(("California", "United States"))]
        pop = [k for k in pops if k not in ("California", "United States")]
        n = sum(v for k in off for v in act[k].values() if isinstance(v, (int, float)))
        p = max((v for k in pop for v in pops[k].values()
                 if isinstance(v, (int, float))), default=0)
        return n, p

    rows = []
    for cname, fips in FBI_COUNTIES.items():
        v_tot = p_tot = pop_tot = 0
        for ag in agencies.get(cname, []):
            ori = ag["ori"]
            agency_pop = 0
            for off in ("violent-crime", "property-crime"):
                js = _fbi_get(session, FBI_AGENCY.format(ori=ori, offense=off),
                              {"from": f"01-{FBI_YEAR}", "to": f"12-{FBI_YEAR}",
                               "API_KEY": key})
                n, p = sum_offenses(js)
                if off == "violent-crime":
                    v_tot += n
                else:
                    p_tot += n
                agency_pop = max(agency_pop, p)
                time.sleep(0.05)
            pop_tot += agency_pop
        rate = 1e5 * (v_tot + p_tot) / pop_tot if pop_tot else float("nan")
        rows.append({"county_fips": fips, "county": cname.title(), "year": FBI_YEAR,
                     "violent": v_tot, "property": p_tot, "population": pop_tot,
                     "crime_rate_per_100k": round(rate, 1)})
    out = pd.DataFrame(rows)
    dp.CRIME.mkdir(parents=True, exist_ok=True)
    path = dp.CRIME / "fbi_county_crime.csv"
    out.to_csv(path, index=False)
    manifest.record("crime", url=FBI_AGENCIES, local_path=path, bytes=path.stat().st_size,
                    sha256=sha256_file(path), status="downloaded")
    return {"layer": "fbi_county", "status": "ok", "counties": len(out)}


def collect(session, manifest) -> dict:
    dp.CRIME.mkdir(parents=True, exist_ok=True)
    return {"source": "crime",
            "sf": collect_sf(session, manifest),
            "oakland": collect_oakland(session, manifest),
            "fbi": collect_fbi(session, manifest)}


# ===========================================================================
# Geocoding + build
# ===========================================================================
def _census_batch_geocode(session, frame, batch=3000) -> pd.Series:
    """frame: columns [id, street, city, state, zip]. Returns id -> bg GEOID.
    The Census batch geocoder is finicky, so batches are small and each is
    retried on the connection drops it is prone to."""
    import requests
    out = {}
    data = {"benchmark": "Public_AR_Current", "vintage": "Census2020_Current"}
    for i in range(0, len(frame), batch):
        chunk = frame.iloc[i:i + batch]
        buf = io.StringIO()
        w = csv.writer(buf)
        for _, r in chunk.iterrows():
            w.writerow([r["id"], r["street"], r["city"], r["state"], r["zip"]])
        payload = buf.getvalue()
        for attempt in range(4):
            try:
                resp = requests.post(
                    CENSUS_BATCH, files={"addressFile": ("addr.csv", payload)},
                    data=data, timeout=600)
                resp.raise_for_status()
                for row in csv.reader(io.StringIO(resp.text)):
                    if len(row) >= 12 and row[2] == "Match":
                        st, co, tract, block = row[8], row[9], row[10], row[11]
                        if st and co and tract and block:
                            out[row[0]] = f"{st}{co}{tract}{block[0]}"
                break
            except Exception as exc:
                if attempt == 3:
                    print(f"  [geocode] batch {i} failed after retries: {exc}", flush=True)
                else:
                    time.sleep(3 * (attempt + 1))
    return pd.Series(out, name="GEOID")


def _sf_bg_counts() -> pd.DataFrame:
    import geopandas as gpd
    df = pd.read_parquet(dp.CRIME / "sf_incidents.parquet")
    bg = gpd.read_file(dp.TIGER / "tl_2023_06_bg.zip")
    bg = bg[bg["COUNTYFP"] == "075"][["GEOID", "geometry"]].to_crs("EPSG:3310")
    pts = gpd.GeoDataFrame(df[[]], geometry=gpd.points_from_xy(df.longitude, df.latitude),
                           crs="EPSG:4326").to_crs("EPSG:3310")
    j = gpd.sjoin(pts, bg, predicate="within", how="inner")
    return j.groupby("GEOID").size().rename("incidents").reset_index()


def _oakland_bg_counts(session) -> pd.DataFrame:
    df = pd.read_parquet(dp.CRIME / "oakland_incidents.parquet")
    by_addr = df.groupby("address").size().rename("incidents").reset_index()
    geo = by_addr.reset_index(names="id").assign(
        id=lambda d: d["id"].astype(str), street=lambda d: d["address"],
        city="Oakland", state="CA", zip="")
    bg = _census_batch_geocode(session, geo[["id", "street", "city", "state", "zip"]])
    by_addr = by_addr.reset_index(names="id").assign(id=lambda d: d["id"].astype(str))
    by_addr = by_addr.merge(bg, left_on="id", right_index=True, how="inner")
    return by_addr.groupby("GEOID")["incidents"].sum().reset_index()


def _merge_into_controls(out: pd.DataFrame) -> None:
    """Add the crime columns to bg_controls (replacing any prior ones)."""
    ctrl_path = dp.CLEAN / "bg_controls.parquet"
    if not ctrl_path.exists():
        return
    crime_cols = [c for c in ("crime_per_1k_hh_yr", "county_crime_rate_per_100k")
                  if c in out.columns]
    ctrl = pd.read_parquet(ctrl_path)
    ctrl = ctrl.drop(columns=[c for c in crime_cols if c in ctrl])
    ctrl = ctrl.merge(out[["GEOID"] + crime_cols], on="GEOID", how="left")
    ctrl.to_parquet(ctrl_path, index=False)


def build(manifest=None) -> dict:
    from ..util import make_session
    out_path = dp.CRIME / "bg_crime.parquet"
    inputs = [dp.CRIME / f for f in ("sf_incidents.parquet",
              "oakland_incidents.parquet", "fbi_county_crime.csv")]
    # idempotent: if the BG measure is newer than every input, just re-merge it
    # into the controls (cheap) and skip the expensive re-geocoding.
    if out_path.exists() and all(p.exists() for p in inputs) and \
            out_path.stat().st_mtime >= max(p.stat().st_mtime for p in inputs):
        out = pd.read_parquet(out_path)
        _merge_into_controls(out)
        if manifest is not None:
            manifest.record("crime", local_path=out_path, bytes=out_path.stat().st_size,
                            sha256=sha256_file(out_path), status="cached")
        return {"status": "cached",
                "bg_incident_level": int(out["incidents"].notna().sum()),
                "bg_county_level": int(out["county_crime_rate_per_100k"].notna().sum())}

    session = make_session()
    n_years = 6  # 2019-2024 window

    # incident-level counts per BG (SF coords + Oakland geocoded)
    parts = []
    if (dp.CRIME / "sf_incidents.parquet").exists():
        parts.append(_sf_bg_counts())
    oak_n = 0
    if (dp.CRIME / "oakland_incidents.parquet").exists():
        try:
            oak = _oakland_bg_counts(session)
            oak_n = int(oak["incidents"].sum())
            parts.append(oak)
        except Exception as exc:  # geocoder is finicky -- don't sink the whole build
            print(f"  [crime] Oakland geocoding failed, continuing with SF + FBI: {exc}",
                  flush=True)
    inc = (pd.concat(parts).groupby("GEOID")["incidents"].sum().reset_index()
           if parts else pd.DataFrame(columns=["GEOID", "incidents"]))
    inc["incidents_per_yr"] = inc["incidents"] / n_years

    # per-1k-household incident rate (ACS occupied households)
    acs = pd.read_parquet(dp.ACS_TABLES / "bg_tenure_income_value.parquet")
    acs = acs[acs["_level"] == "bg"] if "_level" in acs else acs
    hh = acs.assign(GEOID=acs["GEOID"].astype(str))[["GEOID", "B25003_001E"]]
    inc = inc.merge(hh, on="GEOID", how="left")
    inc["crime_per_1k_hh_yr"] = (1000 * inc["incidents_per_yr"]
                                 / inc["B25003_001E"].where(inc["B25003_001E"] > 0))

    # county-level FBI rate, broadcast to every BG
    fbi_path = dp.CRIME / "fbi_county_crime.csv"
    county = pd.read_csv(fbi_path, dtype={"county_fips": str}) if fbi_path.exists() else None

    bg_all = hh[["GEOID"]].copy()
    bg_all["county_fips"] = bg_all["GEOID"].str[:5]
    out = bg_all.merge(inc[["GEOID", "incidents", "incidents_per_yr",
                            "crime_per_1k_hh_yr"]], on="GEOID", how="left")
    if county is not None:
        out = out.merge(county[["county_fips", "crime_rate_per_100k"]],
                        on="county_fips", how="left").rename(
            columns={"crime_rate_per_100k": "county_crime_rate_per_100k"})

    out.to_parquet(out_path, index=False)
    _merge_into_controls(out)

    if manifest is not None:
        manifest.record("crime", local_path=out_path, bytes=out_path.stat().st_size,
                        sha256=sha256_file(out_path), status="downloaded")
    return {"status": "ok",
            "bg_incident_level": int(out["incidents"].notna().sum()),
            "bg_county_level": int(out["county_crime_rate_per_100k"].notna().sum())
            if county is not None else 0,
            "oakland_geocoded_incidents": oak_n}


def main():
    from ..manifest import Manifest
    from ..util import make_session
    m = Manifest()
    print("collect:", collect(make_session(), m))
    print("build:", build(m))


if __name__ == "__main__":
    main()
