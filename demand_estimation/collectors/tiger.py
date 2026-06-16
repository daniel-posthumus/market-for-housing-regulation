#!/usr/bin/env python3
"""
TIGER/Line geography (brief §2.4).

Downloads the 2023 TIGER shapefiles that form the geographic spine every other
source joins on: counties, tracts, block groups, places (= jurisdictions) and
PUMAs. National COUNTY file is filtered to CA; the rest are state-06 files.

The derived block-group -> jurisdiction crosswalk (the single most load-bearing
artifact) is built in ``build.py`` from the BG + PLACE layers downloaded here.
"""
from __future__ import annotations

from .. import demand_paths as dp
from ..util import download

VINTAGE = "TIGER2023"
BASE = f"https://www2.census.gov/geo/tiger/{VINTAGE}"

# name -> (url, local filename)
FILES = {
    "county": (f"{BASE}/COUNTY/tl_2023_us_county.zip", "tl_2023_us_county.zip"),
    "tract": (f"{BASE}/TRACT/tl_2023_06_tract.zip", "tl_2023_06_tract.zip"),
    "bg": (f"{BASE}/BG/tl_2023_06_bg.zip", "tl_2023_06_bg.zip"),
    "place": (f"{BASE}/PLACE/tl_2023_06_place.zip", "tl_2023_06_place.zip"),
    "puma": (f"{BASE}/PUMA/tl_2023_06_puma20.zip", "tl_2023_06_puma20.zip"),
}


def collect(session, manifest) -> dict:
    dp.TIGER.mkdir(parents=True, exist_ok=True)
    got = {}
    for name, (url, fname) in FILES.items():
        dest = dp.TIGER / fname
        res = download(session, url, dest)
        manifest.record(
            "tiger", url=url, local_path=dest,
            bytes=res["bytes"], sha256=res["sha256"], status=res["status"],
        )
        got[name] = dest
    return {"source": "tiger", "status": "ok", "vintage": VINTAGE, "files": got}
