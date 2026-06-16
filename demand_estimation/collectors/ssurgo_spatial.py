#!/usr/bin/env python3
"""
SSURGO spatial mapunit polygons (follow-up brief §1) — the map-unit -> block-group join.

The first run pulled SSURGO *engineering properties* via the tabular Soil Data
Access (SDA) service, which returns no geometry, so the soil instrument was
stuck at the map-unit (mukey) level and could not reach block groups. This
collector supplies the missing spatial ``MUPOLYGON`` mapunit polygons, keyed by
the same ``mukey`` already in the tabular extract.

Route: the brief's route (b) "SSURGO spatial by survey area", implemented via
the keyless SDA REST service rather than the Web Soil Survey per-survey zips
(whose download-cache URLs 404 without a per-survey target date). SDA's
``post.rest`` returns ``mupolygongeo.STAsText()`` (WKT) keyed by ``mukey`` for
the same 11 Bay Area survey areas the first run discovered. Polygons are pulled
with cursor pagination on ``mupolygonkey`` (SDA has no OFFSET), and written as
WKT CSVs under ``soil/ssurgo/spatial/``. ``build.py`` builds the BG aggregate.
"""
from __future__ import annotations

import csv

from .. import demand_paths as dp
from ..util import request_with_retry, sha256_file

SDA_URL = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"
PAGE = 2000  # polygons per request (WKT keeps responses well under SDA limits)

SPATIAL_DIR = dp.SSURGO / "spatial"


def _sda(session, query: str) -> list[list]:
    resp = request_with_retry(
        session, "POST", SDA_URL,
        json={"format": "JSON", "query": query}, timeout=300,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("Table", []) if isinstance(data, dict) else []


def _survey_areas() -> list[str]:
    """Reuse the exact 11 survey areas the first run discovered."""
    path = dp.SSURGO / "bay_area_survey_areas.csv"
    if path.exists():
        with open(path, newline="") as fh:
            return [r["areasymbol"] for r in csv.DictReader(fh)]
    # fall back to the eng_props files written by ssurgo.py
    return sorted({p.stem.replace("eng_props_", "")
                   for p in dp.SSURGO.glob("eng_props_*.csv")})


def _page_query(areasymbol: str, cursor: int) -> str:
    return (
        f"SELECT TOP {PAGE} mp.mupolygonkey, mp.mukey, mp.mupolygongeo.STAsText() AS wkt "
        "FROM legend l "
        "INNER JOIN mapunit mu ON mu.lkey=l.lkey "
        "INNER JOIN mupolygon mp ON mp.mukey=mu.mukey "
        f"WHERE l.areasymbol='{areasymbol}' AND mp.mupolygonkey > {cursor} "
        "ORDER BY mp.mupolygonkey"
    )


def _pull_area(session, areasymbol: str, dest) -> int:
    """Cursor-page every mapunit polygon for one survey area into a WKT CSV."""
    cursor = 0
    n = 0
    with open(dest, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["mupolygonkey", "mukey", "wkt"])
        while True:
            rows = _sda(session, _page_query(areasymbol, cursor))
            if not rows:
                break
            for r in rows:
                w.writerow([r[0], r[1], r[2]])
            n += len(rows)
            cursor = int(rows[-1][0])  # last mupolygonkey
            if len(rows) < PAGE:
                break
    return n


def collect(session, manifest) -> dict:
    SPATIAL_DIR.mkdir(parents=True, exist_ok=True)
    areas = _survey_areas()
    if not areas:
        manifest.record("ssurgo_spatial", url=SDA_URL, local_path=SPATIAL_DIR,
                        status="failed")
        return {"source": "ssurgo_spatial", "status": "failed",
                "note": "no survey areas (run ssurgo collector first)"}

    counts = {}
    for sym in areas:
        dest = SPATIAL_DIR / f"mupolygon_{sym}.csv"
        if dest.exists() and dest.stat().st_size > 0:
            manifest.record("ssurgo_spatial", url=SDA_URL, local_path=dest,
                            bytes=dest.stat().st_size, sha256=sha256_file(dest),
                            status="cached")
            # count rows (minus header) for the return summary
            with open(dest) as fh:
                counts[sym] = sum(1 for _ in fh) - 1
            continue
        n = _pull_area(session, sym, dest)
        manifest.record("ssurgo_spatial", url=SDA_URL, local_path=dest,
                        bytes=dest.stat().st_size, sha256=sha256_file(dest),
                        status="downloaded")
        counts[sym] = n

    return {"source": "ssurgo_spatial", "status": "ok",
            "survey_areas": areas, "polygons": counts,
            "total_polygons": sum(counts.values())}
