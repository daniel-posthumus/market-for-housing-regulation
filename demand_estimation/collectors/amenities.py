#!/usr/bin/env python3
"""
Amenity layers (brief §2.8) — location attributes & controls.

  * Open space (CPAD) — California Protected Areas Database. An amenity CONTROL
    (like seismic): the soil instrument must be residualized against
    proximity-to-open-space. Pulled from the GreenInfo CPAD feature service,
    clipped to the Bay Area. (Highest-value of the three for the design.)
  * Schools (CDE) — California Dept of Education enrollment downloadable flat
    file (school-quality / demographics proxy). Discovered from the CDE data
    download page; degrades to ``partial`` if the file link cannot be resolved.
  * Transit (GTFS) — public per-agency static GTFS feeds (the 511 regional feed
    needs a free API key we don't have, so we fall back to per-agency feeds and
    note 511 as a needs_key upgrade).

build.py turns CPAD into a BG open-space-proximity control and GTFS into a
stop-density / transit-access measure.
"""
from __future__ import annotations

import zipfile

from .. import demand_paths as dp
from ..arcgis import BAY_AREA_BBOX, download_layer
from ..util import download, resolve_511_key, sha256_file

# --- open space (CPAD) ------------------------------------------------------
CPAD_UNITS = (
    "https://services1.arcgis.com/4ZKi1B1zTblbwgWB/arcgis/rest/services/"
    "cpad_2024a_unitsgdb/FeatureServer/0"
)

# --- schools (CDE) ----------------------------------------------------------
# Direct, stable CDE downloadable: statewide cumulative enrollment by school
# (the data-download landing page is JS-rendered and not scrapable).
CDE_ENROLLMENT_URL = "https://www3.cde.ca.gov/demo-downloads/ce/cenroll2324.txt"

# --- transit (GTFS) — public per-agency static feeds ------------------------
# Public static feeds with stable URLs (the 511 regional feed needs a key).
# NB: caltrain.com/files/google_transit.zip now serves an HTML page, so we use
# the Trillium mirror, which returns a valid GTFS zip.
GTFS_FEEDS = {
    "bart": "https://www.bart.gov/dev/schedules/google_transit.zip",
    "caltrain": "https://data.trilliumtransit.com/gtfs/caltrain-ca-us/caltrain-ca-us.zip",
}
GTFS_511_NOTE = ("511 Bay Area regional GTFS feed requires a free 511.org API "
                 "key (api.511.org/transit/datafeeds) — not provided; per-agency "
                 "feeds used instead.")
# 511 regional combined GTFS bundle (all operators): operator_id=RG.
BAY511_GTFS_URL = "https://api.511.org/transit/datafeeds?api_key={key}&operator_id=RG"


def _collect_openspace(session, manifest) -> dict:
    dp.OPENSPACE.mkdir(parents=True, exist_ok=True)
    dest = dp.OPENSPACE / "cpad_2024a_units_bayarea.geojson"
    try:
        res = download_layer(session, CPAD_UNITS, dest, bbox=BAY_AREA_BBOX)
        sha = sha256_file(dest) if dest.exists() else ""
        manifest.record("amenities_openspace", url=CPAD_UNITS, local_path=dest,
                        bytes=res["bytes"], sha256=sha, status=res["status"])
        return {"status": res["status"], "features": res["features"]}
    except Exception as exc:
        manifest.record("amenities_openspace", url=CPAD_UNITS, local_path=dest,
                        status="partial")
        return {"status": "partial", "error": str(exc)[:160]}


def _collect_schools(session, manifest) -> dict:
    dp.SCHOOLS.mkdir(parents=True, exist_ok=True)
    dest = dp.SCHOOLS / "cde_cumulative_enrollment_2324.txt"
    try:
        res = download(session, CDE_ENROLLMENT_URL, dest)
        manifest.record("amenities_schools", url=CDE_ENROLLMENT_URL, local_path=dest,
                        bytes=res["bytes"], sha256=res["sha256"], status=res["status"])
        return {"status": res["status"], "file": dest.name}
    except Exception as exc:
        manifest.record("amenities_schools", url=CDE_ENROLLMENT_URL,
                        local_path=dp.SCHOOLS, status="partial")
        return {"status": "partial", "error": str(exc)[:160]}


def _collect_transit(session, manifest) -> dict:
    dp.TRANSIT.mkdir(parents=True, exist_ok=True)
    got = {}
    any_ok = False
    for agency, url in GTFS_FEEDS.items():
        dest = dp.TRANSIT / f"gtfs_{agency}.zip"
        try:
            res = download(session, url, dest)
            # validate it is actually a GTFS zip (some agency URLs now serve an
            # HTML page with HTTP 200 — don't silently cache that as a feed)
            if not zipfile.is_zipfile(dest):
                dest.unlink(missing_ok=True)
                raise ValueError("downloaded file is not a valid zip (HTML page?)")
            manifest.record("amenities_transit", url=url, local_path=dest,
                            bytes=res["bytes"], sha256=res["sha256"], status=res["status"])
            got[agency] = res["status"]
            any_ok = True
        except Exception as exc:
            manifest.record("amenities_transit", url=url, local_path=dest, status="failed")
            got[agency] = f"failed: {str(exc)[:80]}"
    # 511 Bay Area regional feed (all operators) — key-gated (follow-up brief §2).
    got["bay511"] = _collect_511(session, manifest)
    has_511 = got["bay511"] in ("downloaded", "cached", "verified")
    return {"status": "ok" if any_ok else "partial", "agencies": got,
            "note": None if has_511 else GTFS_511_NOTE}


def _collect_511(session, manifest) -> str:
    """
    Fetch the 511 regional GTFS bundle if a key is available; otherwise mark the
    source needs_key cleanly (no scraping, no workaround). Returns the status.
    """
    key = resolve_511_key()
    bay511_dir = dp.TRANSIT / "bay511"
    if not key:
        # update the existing needs_key row in place (keyed by source+path)
        manifest.record("amenities_transit",
                        url="https://api.511.org/transit/datafeeds",
                        local_path=dp.TRANSIT / "_511_regional_feed",
                        status="needs_key")
        return "needs_key"
    bay511_dir.mkdir(parents=True, exist_ok=True)
    dest = bay511_dir / "gtfs_bay511_regional.zip"
    try:
        res = download(session, BAY511_GTFS_URL.format(key=key), dest)
        if not zipfile.is_zipfile(dest):
            dest.unlink(missing_ok=True)
            raise ValueError("511 response is not a valid zip (bad key / rate limit?)")
        manifest.record("amenities_transit",
                        url="https://api.511.org/transit/datafeeds?operator_id=RG",
                        local_path=dest, bytes=res["bytes"], sha256=res["sha256"],
                        status=res["status"])
        # retire the old needs_key placeholder row now that the feed has landed
        manifest.drop("amenities_transit", dp.TRANSIT / "_511_regional_feed")
        return res["status"]
    except Exception as exc:
        manifest.record("amenities_transit",
                        url="https://api.511.org/transit/datafeeds",
                        local_path=dest, status="partial")
        return f"partial: {str(exc)[:80]}"


def collect(session, manifest) -> dict:
    return {
        "source": "amenities",
        "openspace": _collect_openspace(session, manifest),
        "schools": _collect_schools(session, manifest),
        "transit": _collect_transit(session, manifest),
    }
