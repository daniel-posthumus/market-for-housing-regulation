#!/usr/bin/env python3
"""
LEHD LODES8 (brief §2.3) — job-access / labor-market access.

Downloads the California LODES8 WAC (workplace jobs by block), RAC (resident
jobs), OD (origin-destination) and the block crosswalk, for the panel year
(2022) plus a pre-period year (2015) for robustness. The block-group job-access
measure is built in ``build.py`` from WAC.

LODES8 uses 2020 block geography. No auth required.
"""
from __future__ import annotations

from .. import demand_paths as dp
from ..util import download

BASE = "https://lehd.ces.census.gov/data/lodes/LODES8/ca"
PANEL_YEAR = 2022
PRE_YEAR = 2015  # robustness / pre-period

# (key, relative url, filename)
def _files():
    files = []
    for yr in (PANEL_YEAR, PRE_YEAR):
        files.append((f"wac_{yr}", f"{BASE}/wac/ca_wac_S000_JT00_{yr}.csv.gz",
                      f"ca_wac_S000_JT00_{yr}.csv.gz"))
        files.append((f"rac_{yr}", f"{BASE}/rac/ca_rac_S000_JT00_{yr}.csv.gz",
                      f"ca_rac_S000_JT00_{yr}.csv.gz"))
    # OD only for the panel year (main + aux)
    files.append((f"od_main_{PANEL_YEAR}", f"{BASE}/od/ca_od_main_JT00_{PANEL_YEAR}.csv.gz",
                  f"ca_od_main_JT00_{PANEL_YEAR}.csv.gz"))
    files.append((f"od_aux_{PANEL_YEAR}", f"{BASE}/od/ca_od_aux_JT00_{PANEL_YEAR}.csv.gz",
                  f"ca_od_aux_JT00_{PANEL_YEAR}.csv.gz"))
    # block crosswalk
    files.append(("xwalk", f"{BASE}/ca_xwalk.csv.gz", "ca_xwalk.csv.gz"))
    return files


def collect(session, manifest) -> dict:
    dp.LODES.mkdir(parents=True, exist_ok=True)
    got = {}
    for key, url, fname in _files():
        dest = dp.LODES / fname
        res = download(session, url, dest)
        manifest.record(
            "lodes", url=url, local_path=dest,
            bytes=res["bytes"], sha256=res["sha256"], status=res["status"],
        )
        got[key] = dest
    return {"source": "lodes", "status": "ok",
            "panel_year": PANEL_YEAR, "pre_year": PRE_YEAR, "files": got}
