#!/usr/bin/env python3
"""
ACS 5-year data (brief §2.1 + §2.2).

Two products:

  * PUMS micro-data (household + person), California, via the **keyless FTP
    bulk** path — the household side of BLP (income, tenure, structure, age,
    race). Downloaded as the official CA zips; filtered to Bay Area PUMAs and
    written to parquet in ``build.py``.

  * ACS aggregate tables at tract + block-group level for the nine counties,
    via the **Census Data API** (needs a key — resolved from the environment /
    CENSUS_API_KEY.txt). Tenure, value, rent, income, structure: the market
    shares + outside-option denominators for demand.

If no valid key is available, the tables collector degrades gracefully: it
records ``status=needs_key`` in the manifest and the run continues. PUMS is
unaffected (keyless).
"""
from __future__ import annotations

import pandas as pd

from .. import demand_paths as dp
from ..util import download, request_with_retry, resolve_census_key

# --- vintages ---------------------------------------------------------------
PUMS_YEAR = 2024          # 2020-2024 ACS 5-year PUMS
PUMS_BASE = f"https://www2.census.gov/programs-surveys/acs/data/pums/{PUMS_YEAR}/5-Year"
TABLES_YEAR = 2023        # newest released 5-year detailed tables
API_BASE = f"https://api.census.gov/data/{TABLES_YEAR}/acs/acs5"

PUMS_FILES = {
    "housing": (f"{PUMS_BASE}/csv_hca.zip", "csv_hca.zip"),
    "person": (f"{PUMS_BASE}/csv_pca.zip", "csv_pca.zip"),
}

# Tables to pull (whole groups). Comments give the Layer-I role.
TABLE_GROUPS = {
    "B25003": "tenure (owner/renter counts) — core tenure choice",
    "B25077": "median home value",
    "B25064": "median gross rent",
    "B19013": "median household income",
    "B25024": "units in structure (SF vs MF)",
    "B25118": "tenure by household income",
    "B25034": "year structure built",
    "B25040": "house heating fuel",
}


# ---------------------------------------------------------------------------
# §2.1 PUMS — keyless FTP bulk
# ---------------------------------------------------------------------------
def collect_pums(session, manifest) -> dict:
    dp.ACS_PUMS.mkdir(parents=True, exist_ok=True)
    got = {}
    for key, (url, fname) in PUMS_FILES.items():
        dest = dp.ACS_PUMS / fname
        res = download(session, url, dest)
        manifest.record(
            "acs_pums", url=url, local_path=dest,
            bytes=res["bytes"], sha256=res["sha256"], status=res["status"],
        )
        got[key] = dest
    return {"source": "acs_pums", "status": "ok", "vintage": f"{PUMS_YEAR} 5-yr", "files": got}


# ---------------------------------------------------------------------------
# §2.2 ACS tables — API (key-gated)
# ---------------------------------------------------------------------------
def _is_invalid_key(resp) -> bool:
    ctype = resp.headers.get("content-type", "")
    return "html" in ctype.lower() or "Invalid Key" in resp.text[:200]


def _fetch_group(session, key, table, level, county) -> list[dict]:
    """Fetch one table group for one county at tract or block-group level."""
    params = [("get", f"group({table})"), ("key", key)]
    if level == "tract":
        params += [("for", "tract:*"), ("in", "state:06"), ("in", f"county:{county}")]
    elif level == "bg":
        params += [("for", "block group:*"), ("in", "state:06"),
                   ("in", f"county:{county}"), ("in", "tract:*")]
    else:
        raise ValueError(level)
    resp = request_with_retry(session, "GET", API_BASE, params=params, timeout=90)
    if resp.status_code != 200 or _is_invalid_key(resp):
        raise PermissionError("census api rejected request (invalid/missing key?)")
    rows = resp.json()
    header, *data = rows
    out = []
    for r in data:
        d = dict(zip(header, r))
        # build GEOID
        geoid = d.get("state", "") + d.get("county", "") + d.get("tract", "")
        if level == "bg":
            geoid += d.get("block group", "")
        d["GEOID"] = geoid
        d["_level"] = level
        out.append(d)
    return out


def _tidy(records: list[dict], level: str) -> pd.DataFrame:
    """Keep estimate (E) columns; drop annotation/margin columns and metadata."""
    df = pd.DataFrame(records)
    if df.empty:
        return df
    keep_meta = ["GEOID", "_level", "NAME"]
    est_cols = [c for c in df.columns
                if c.endswith("E") and c[:-1].replace("_", "").isalnum()
                and c not in ("NAME",)]
    # estimate columns look like B25003_001E
    est_cols = [c for c in df.columns if c[-1] == "E" and "_" in c and c[0] == "B"]
    cols = [c for c in keep_meta if c in df.columns] + sorted(set(est_cols))
    df = df[cols].copy()
    for c in est_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def collect_tables(session, manifest) -> dict:
    key = resolve_census_key()
    if not key:
        manifest.record("acs_tables", url=API_BASE, local_path=dp.ACS_TABLES,
                        status="needs_key")
        return {"source": "acs_tables", "status": "needs_key",
                "note": "no CENSUS_API_KEY found (env / CENSUS_API_KEY.txt)"}

    dp.ACS_TABLES.mkdir(parents=True, exist_ok=True)
    out_files = {
        "tract": dp.ACS_TABLES / "tract_tenure_income_value.parquet",
        "bg": dp.ACS_TABLES / "bg_tenure_income_value.parquet",
    }
    # idempotent: if both tidy parquets already exist, don't re-hit the API
    if all(p.exists() and p.stat().st_size > 0 for p in out_files.values()):
        for p in out_files.values():
            manifest.record("acs_tables", url=API_BASE, local_path=p,
                            bytes=p.stat().st_size, status="cached")
        return {"source": "acs_tables", "status": "cached", "vintage": f"{TABLES_YEAR} 5-yr"}
    try:
        for level, out_name in (("tract", "tract_tenure_income_value.parquet"),
                                ("bg", "bg_tenure_income_value.parquet")):
            merged: pd.DataFrame | None = None
            for table in TABLE_GROUPS:
                recs = []
                for county in dp.BAY_AREA_COUNTY_FIPS:
                    recs.extend(_fetch_group(session, key, table, level, county))
                tdf = _tidy(recs, level)
                if tdf.empty:
                    continue
                # drop NAME for all but first merge to avoid dup
                if merged is None:
                    merged = tdf
                else:
                    cols = [c for c in tdf.columns if c not in ("NAME", "_level")]
                    merged = merged.merge(tdf[cols], on="GEOID", how="outer")
            out_path = dp.ACS_TABLES / out_name
            merged.to_parquet(out_path, index=False)
            manifest.record("acs_tables", url=API_BASE, local_path=out_path,
                            bytes=out_path.stat().st_size, status="downloaded")
    except PermissionError:
        manifest.record("acs_tables", url=API_BASE, local_path=dp.ACS_TABLES,
                        status="needs_key")
        return {"source": "acs_tables", "status": "needs_key",
                "note": "key present but rejected by Census API (Invalid Key)"}

    return {"source": "acs_tables", "status": "ok", "vintage": f"{TABLES_YEAR} 5-yr"}


def collect(session, manifest) -> dict:
    pums = collect_pums(session, manifest)
    tables = collect_tables(session, manifest)
    return {"source": "acs", "pums": pums, "tables": tables}
