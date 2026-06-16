#!/usr/bin/env python3
"""
USDA SSURGO soil survey (brief §2.5) — THE INSTRUMENT.

The cost-shifter instrument from the demand memo is the expansive-clay /
bearing-capacity / shrink-swell dimension of soil that shifts foundation cost.
This collector pulls the **engineering properties** that are the instrument's
raw material, via the USDA Soil Data Access (SDA) tabular REST service — no
auth, no multi-GB geodatabase download.

Route taken (brief offered gSSURGO-GDB vs survey-area): we use SDA tabular
queries per survey area. This is lighter and fully scriptable, and returns
exactly the chorizon/component/mapunit engineering fields we need:
    lep_r  (linear extensibility % -> shrink-swell / COLE proxy),
    pi_r   (plasticity index), ll_r (liquid limit / Atterberg),
    drainagecl, depth-to-restriction (via horizon depths), ksat, bulk density,
    taxorder / taxsubgrp.

The nine Bay Area counties are covered by 12 SSURGO survey areas, discovered at
runtime from the County-or-Parish overlap (symbols are NOT all FIPS-aligned —
Alameda/SF/San Mateo/Santa Clara use legacy survey symbols, so we must
discover, not hard-code).

Output: one CSV per survey area in ``soil/ssurgo/`` plus the discovered
survey-area list. ``build.py`` aggregates these to the mukey-level
``soil/derived/mapunit_engineering_properties.parquet``. The block-group
areal-weighted aggregation and the predicted-cost index are downstream and
documented as TODOs (the cost index is blocked on RS Means, §3.2).
"""
from __future__ import annotations

import csv
import io

from .. import demand_paths as dp
from ..util import request_with_retry

SDA_URL = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"

BAY_AREA_COUNTY_NAMES = [
    "Alameda", "Contra Costa", "Marin", "Napa", "San Francisco",
    "San Mateo", "Santa Clara", "Solano", "Sonoma",
]

# Columns we SELECT, in order (so we control the CSV header regardless of the
# SDA response format, which does not return names in plain JSON).
ENG_COLUMNS = [
    "areasymbol", "mukey", "muname", "cokey", "comppct_r", "compname",
    "majcompflag", "drainagecl", "taxorder", "taxsubgrp",
    "chkey", "hzname", "hzdept_r", "hzdepb_r",
    "lep_r", "pi_r", "ll_r", "ksat_r", "dbthirdbar_r", "awc_r",
]


def _sda(session, query: str) -> list[list]:
    """POST a query to SDA; return the list of rows (empty list if none)."""
    resp = request_with_retry(
        session, "POST", SDA_URL,
        json={"format": "JSON", "query": query}, timeout=180,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("Table", []) if isinstance(data, dict) else []


def discover_survey_areas(session) -> list[dict]:
    names = ",".join(f"'{n}'" for n in BAY_AREA_COUNTY_NAMES)
    q = (
        "SELECT DISTINCT l.areasymbol, l.areaname "
        "FROM legend l INNER JOIN laoverlap lo ON l.lkey=lo.lkey "
        "WHERE lo.areatypename='County or Parish' AND l.areasymbol LIKE 'CA%' "
        f"AND lo.areaname IN ({names}) ORDER BY l.areasymbol"
    )
    rows = _sda(session, q)
    return [{"areasymbol": r[0], "areaname": r[1]} for r in rows]


def _eng_query(areasymbol: str) -> str:
    cols = (
        "l.areasymbol, mu.mukey, mu.muname, c.cokey, c.comppct_r, c.compname, "
        "c.majcompflag, c.drainagecl, c.taxorder, c.taxsubgrp, "
        "ch.chkey, ch.hzname, ch.hzdept_r, ch.hzdepb_r, "
        "ch.lep_r, ch.pi_r, ch.ll_r, ch.ksat_r, ch.dbthirdbar_r, ch.awc_r"
    )
    return (
        f"SELECT {cols} "
        "FROM legend l "
        "INNER JOIN mapunit mu ON mu.lkey=l.lkey "
        "INNER JOIN component c ON c.mukey=mu.mukey "
        "LEFT OUTER JOIN chorizon ch ON ch.cokey=c.cokey "
        f"WHERE l.areasymbol='{areasymbol}'"
    )


def _write_csv(rows: list[list], dest) -> int:
    with open(dest, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(ENG_COLUMNS)
        w.writerows(rows)
    return dest.stat().st_size


def collect(session, manifest) -> dict:
    from ..util import sha256_file
    dp.SSURGO.mkdir(parents=True, exist_ok=True)

    areas = discover_survey_areas(session)
    if not areas:
        manifest.record("ssurgo", url=SDA_URL, local_path=dp.SSURGO, status="failed")
        return {"source": "ssurgo", "status": "failed", "note": "no survey areas discovered"}

    # persist the discovered survey-area list
    list_path = dp.SSURGO / "bay_area_survey_areas.csv"
    with open(list_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["areasymbol", "areaname"])
        for a in areas:
            w.writerow([a["areasymbol"], a["areaname"]])
    manifest.record("ssurgo", url=SDA_URL, local_path=list_path,
                    bytes=list_path.stat().st_size, sha256=sha256_file(list_path),
                    status="downloaded")

    n_areas = 0
    for a in areas:
        sym = a["areasymbol"]
        dest = dp.SSURGO / f"eng_props_{sym}.csv"
        if dest.exists() and dest.stat().st_size > 0:
            manifest.record("ssurgo", url=SDA_URL, local_path=dest,
                            bytes=dest.stat().st_size, sha256=sha256_file(dest),
                            status="cached")
            n_areas += 1
            continue
        rows = _sda(session, _eng_query(sym))
        size = _write_csv(rows, dest)
        manifest.record("ssurgo", url=SDA_URL, local_path=dest,
                        bytes=size, sha256=sha256_file(dest),
                        status="downloaded")
        n_areas += 1

    return {"source": "ssurgo", "status": "ok",
            "survey_areas": [a["areasymbol"] for a in areas], "n_areas": n_areas}
