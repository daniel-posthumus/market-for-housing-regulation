#!/usr/bin/env python3
"""
arcgis.py — minimal ArcGIS Feature Service downloader.

Several Bay Area control/backstop layers (CGS seismic hazard zones, Gov-OPR
statewide zoning, CPAD open space) are published only as ArcGIS Online hosted
feature services, not flat files. This helper pages a layer out as GeoJSON,
optionally clipped to an envelope (the Bay Area bounding box), respecting the
service's maxRecordCount via resultOffset paging.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .util import request_with_retry

# Bay Area bounding box (WGS84): covers the nine ABAG counties with margin.
BAY_AREA_BBOX = {"xmin": -123.7, "ymin": 36.8, "xmax": -121.1, "ymax": 38.95}


def _page(session, layer_url, offset, page_size, where, bbox):
    params = {
        "where": where,
        "outFields": "*",
        "f": "geojson",
        "outSR": "4326",
        "resultOffset": str(offset),
        "resultRecordCount": str(page_size),
        "returnGeometry": "true",
    }
    if bbox:
        params.update({
            "geometry": json.dumps(bbox),
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
        })
    resp = request_with_retry(session, "GET", layer_url + "/query",
                              params=params, timeout=180)
    resp.raise_for_status()
    return resp.json()


def download_layer(
    session,
    layer_url: str,
    dest: Path,
    *,
    where: str = "1=1",
    bbox: dict | None = None,
    page_size: int = 1000,
    max_pages: int = 400,
    max_seconds: float = 600.0,
) -> dict:
    """
    Download an ArcGIS feature layer to ``dest`` as a GeoJSON FeatureCollection.

    Returns {"features": n, "bytes": size, "status": ...} where status is
    ``downloaded`` | ``cached`` | ``partial`` (hit the page or time budget
    before the layer was exhausted — we save what we have and flag it).
    Idempotent: a non-empty existing file is not re-fetched. The time budget
    guarantees no single slow layer can hang the whole run.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        try:
            n = len(json.loads(dest.read_text()).get("features", []))
        except Exception:
            n = -1
        return {"features": n, "bytes": dest.stat().st_size, "status": "cached"}

    features: list = []
    offset = 0
    complete = False
    start = time.time()
    for _ in range(max_pages):
        if time.time() - start > max_seconds:
            break
        fc = _page(session, layer_url, offset, page_size, where, bbox)
        batch = fc.get("features", [])
        features.extend(batch)
        if len(batch) < page_size:   # short page => last page
            complete = True
            break
        offset += len(batch)

    out = {"type": "FeatureCollection", "features": features}
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_text(json.dumps(out))
    tmp.replace(dest)
    return {"features": len(features), "bytes": dest.stat().st_size,
            "status": "downloaded" if complete else "partial"}
