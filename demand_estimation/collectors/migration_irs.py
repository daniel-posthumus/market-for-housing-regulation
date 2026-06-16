#!/usr/bin/env python3
"""
IRS SOI county-to-county migration flows (brief §3.3, free automatable backup).

This is the no-license, automatable first-pass moving-cost input. The licensed
upgrades (Infutor / Verisk) are stubbed separately in ``build.py`` / _stubs.

The IRS publishes inflow and outflow county migration CSVs at a stable path.
We grab the most recent available year-pair (2021-2022 as of this writing) and
filter to the nine Bay Area counties in ``build.py``.
"""
from __future__ import annotations

from .. import demand_paths as dp
from ..util import download

BASE = "https://www.irs.gov/pub/irs-soi"
YEARPAIR = "2122"  # 2021 -> 2022

FILES = {
    "inflow": (f"{BASE}/countyinflow{YEARPAIR}.csv", f"countyinflow{YEARPAIR}.csv"),
    "outflow": (f"{BASE}/countyoutflow{YEARPAIR}.csv", f"countyoutflow{YEARPAIR}.csv"),
}


def collect(session, manifest) -> dict:
    dp.MIGRATION_IRS.mkdir(parents=True, exist_ok=True)
    got = {}
    for key, (url, fname) in FILES.items():
        dest = dp.MIGRATION_IRS / fname
        res = download(session, url, dest)
        manifest.record(
            "migration_irs", url=url, local_path=dest,
            bytes=res["bytes"], sha256=res["sha256"], status=res["status"],
        )
        got[key] = dest
    return {"source": "migration_irs", "status": "ok", "yearpair": YEARPAIR, "files": got}
