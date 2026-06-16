#!/usr/bin/env python3
"""
CGS seismic & liquefaction hazard zones (brief §2.6) — CONTROL variables.

These are controls, not instruments: needed so the SSURGO soil instrument can
be residualized against the seismic-risk channel that may be capitalized into
prices (demand memo §5 / seismic-capitalization remark).

The CGS regulatory Seismic Hazard Zones (zones of required investigation,
incl. liquefaction & earthquake-induced landslides) are published as an ArcGIS
feature service. We pull the statewide layer clipped to the Bay Area bounding
box, plus the public Alquist-Priolo fault-rupture traces. Joined to block
groups in ``build.py``.
"""
from __future__ import annotations

from .. import demand_paths as dp
from ..arcgis import BAY_AREA_BBOX, download_layer
from ..util import sha256_file

# Statewide Seismic Hazard Zones (zones of required investigation) feature layer.
SEISMIC_HAZARD_ZONES = (
    "https://services2.arcgis.com/tcv2cMrq63AgvbHF/arcgis/rest/services/"
    "GeohazardsDataService2/FeatureServer/6"
)
# Public Alquist-Priolo fault traces (fault-rupture hazard).
FAULT_TRACES = (
    "https://gis.conservation.ca.gov/server/rest/services/"
    "CGS_Earthquake_Hazard_Zones/SHP_Fault_Traces/MapServer/0"
)

TARGETS = {
    "seismic_hazard_zones": (SEISMIC_HAZARD_ZONES, "cgs_seismic_hazard_zones.geojson"),
    "fault_traces": (FAULT_TRACES, "cgs_fault_traces.geojson"),
}


def collect(session, manifest) -> dict:
    dp.HAZARD.mkdir(parents=True, exist_ok=True)
    out = {}
    any_ok = False
    for key, (url, fname) in TARGETS.items():
        dest = dp.HAZARD / fname
        try:
            res = download_layer(session, url, dest, bbox=BAY_AREA_BBOX)
            sha = sha256_file(dest) if dest.exists() else ""
            manifest.record("hazard", url=url, local_path=dest,
                            bytes=res["bytes"], sha256=sha, status=res["status"])
            out[key] = {"features": res["features"], "status": res["status"]}
            any_ok = True
        except Exception as exc:  # portal layer may move / require token
            manifest.record("hazard", url=url, local_path=dest, status="partial")
            out[key] = {"status": "partial", "error": str(exc)[:160]}
    return {"source": "hazard", "status": "ok" if any_ok else "partial", "layers": out}
