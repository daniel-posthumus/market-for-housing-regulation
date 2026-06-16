#!/usr/bin/env python3
"""
California statewide zoning (brief §2.7) — Layer II by-right envelope backstop.

A single statewide aggregated zoning snapshot from Gov-OPR (the CA Governor's
Office of Planning & Research), published as ArcGIS feature services split into
"North" and "South" halves. The nine Bay Area counties fall in the North half;
we pull it clipped to the Bay Area bounding box.

Caveat (logged in the report): this is a 2022-23 aggregated SNAPSHOT, not a
panel, and per-city fidelity must be checked. It is grabbed now because it is
cheap while we are already pulling spatial layers; it feeds Layer II, not the
Layer I demand estimation.
"""
from __future__ import annotations

from .. import demand_paths as dp
from ..arcgis import download_layer
from ..util import sha256_file

# Gov-OPR California Statewide Zoning — North half (covers the Bay Area).
ZONING_NORTH = (
    "https://services8.arcgis.com/Xr1lDrwMv89PhjD9/arcgis/rest/services/"
    "California_Statewide_Zoning_North/FeatureServer/1"
)
# The layer's County field uses 3-letter codes (not FIPS, not full names). An
# attribute filter is far faster than a spatial bbox query on this large
# statewide parcel layer. Bay Area county codes:
BA_COUNTY_CODES = ["ALA", "CCO", "MRN", "NAP", "SFR", "SMA", "SCL", "SOL", "SON"]
WHERE_BA = "County IN (" + ",".join(f"'{c}'" for c in BA_COUNTY_CODES) + ")"


def collect(session, manifest) -> dict:
    dp.ZONING.mkdir(parents=True, exist_ok=True)
    dest = dp.ZONING / "ca_statewide_zoning_bayarea.geojson"
    try:
        res = download_layer(session, ZONING_NORTH, dest,
                             where=WHERE_BA, page_size=2000)
        sha = sha256_file(dest) if dest.exists() else ""
        manifest.record("zoning", url=ZONING_NORTH, local_path=dest,
                        bytes=res["bytes"], sha256=sha, status=res["status"])
        return {"source": "zoning", "status": res["status"],
                "features": res["features"],
                "note": "Gov-OPR North snapshot, 9-county attribute filter"}
    except Exception as exc:
        manifest.record("zoning", url=ZONING_NORTH, local_path=dest, status="partial")
        return {"source": "zoning", "status": "partial", "error": str(exc)[:160]}
