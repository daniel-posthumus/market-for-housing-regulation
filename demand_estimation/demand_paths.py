#!/usr/bin/env python3
"""
demand_paths.py — where the demand-side data lives.

This module is the single place that resolves paths for the Layer I
demand-estimation data collection (ACS, LODES, TIGER, SSURGO, hazard, zoning,
amenities, migration). It reuses the repo's existing data-root convention by
importing ``DATA_ROOT`` from ``code/commission_minutes_processing/paths.py`` —
so ``MFHR_DATA_ROOT`` overrides apply uniformly and nothing is hard-coded.

Unlike the minutes corpus, demand data is **region-wide** (not per-locality):
it covers the nine-county ABAG Bay Area as a whole, so it lives at the data
root under ``demand/``, mirroring how ``crosswalks/``, ``shapefiles/`` and
``clean/`` already sit at the root (see STRUCTURE.md).

The data tree itself stays on Dropbox, out of git; only the *code* that writes
it lives in ``demand_estimation/``.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve DATA_ROOT from the existing minutes-pipeline paths.py (don't reinvent
# the MFHR_DATA_ROOT logic — import it). We load it by file path because the
# module is named the generic "paths" and lives in a sibling code tree.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
_PATHS_PY = _REPO_ROOT / "code" / "commission_minutes_processing" / "paths.py"

_spec = importlib.util.spec_from_file_location("_mfhr_paths", _PATHS_PY)
_mfhr_paths = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mfhr_paths)

DATA_ROOT: Path = _mfhr_paths.DATA_ROOT

# ---------------------------------------------------------------------------
# Demand-side data tree (region-wide; §1 of the brief).
# ---------------------------------------------------------------------------
DEMAND_ROOT = DATA_ROOT / "demand"

MANIFEST_CSV = DEMAND_ROOT / "_manifest.csv"

# ACS
ACS_PUMS = DEMAND_ROOT / "acs" / "pums"
ACS_TABLES = DEMAND_ROOT / "acs" / "tables"

# LEHD LODES8
LODES = DEMAND_ROOT / "lodes"

# Geography
TIGER = DEMAND_ROOT / "geography" / "tiger"
CROSSWALKS = DEMAND_ROOT / "geography" / "crosswalks"

# Soil (the instrument)
SSURGO = DEMAND_ROOT / "soil" / "ssurgo"
SOIL_DERIVED = DEMAND_ROOT / "soil" / "derived"

# Controls / backstops
HAZARD = DEMAND_ROOT / "hazard"
ZONING = DEMAND_ROOT / "zoning"

# Amenities
AMENITIES = DEMAND_ROOT / "amenities"
SCHOOLS = AMENITIES / "schools"
TRANSIT = AMENITIES / "transit"
OPENSPACE = AMENITIES / "openspace"

# Migration
MIGRATION_IRS = DEMAND_ROOT / "migration" / "irs"

# Crime (location disamenity / control)
CRIME = DEMAND_ROOT / "crime"

# Derived / assembled outputs
CLEAN = DEMAND_ROOT / "clean"

# Manual hand-off stubs (no data; one README per source)
STUBS = DEMAND_ROOT / "_stubs"

# All directories that should exist for a run.
ALL_DIRS = [
    DEMAND_ROOT, ACS_PUMS, ACS_TABLES, LODES, TIGER, CROSSWALKS,
    SSURGO, SOIL_DERIVED, HAZARD, ZONING, AMENITIES, SCHOOLS, TRANSIT,
    OPENSPACE, MIGRATION_IRS, CRIME, CLEAN, STUBS,
]

# ---------------------------------------------------------------------------
# Geographic scope: the nine-county ABAG Bay Area (FIPS county codes, no state
# prefix on the right column; full 5-digit GEOID prefix on the left).
# ---------------------------------------------------------------------------
STATE_FIPS = "06"  # California

# county FIPS (3-digit) -> name
BAY_AREA_COUNTIES = {
    "001": "Alameda",
    "013": "Contra Costa",
    "041": "Marin",
    "055": "Napa",
    "075": "San Francisco",
    "081": "San Mateo",
    "085": "Santa Clara",
    "095": "Solano",
    "097": "Sonoma",
}

# 5-digit GEOID prefixes (state+county), e.g. "06001"
BAY_AREA_FIPS5 = [STATE_FIPS + c for c in BAY_AREA_COUNTIES]

# 3-digit county codes only
BAY_AREA_COUNTY_FIPS = list(BAY_AREA_COUNTIES.keys())


def ensure_dirs() -> None:
    """Create every demand subdirectory (idempotent)."""
    for d in ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)


def in_bay_area(geoid: str) -> bool:
    """True if a Census GEOID (block, BG, tract, county) is in the 9 counties."""
    return str(geoid)[:5] in BAY_AREA_FIPS5


if __name__ == "__main__":
    print("DATA_ROOT  :", DATA_ROOT)
    print("DEMAND_ROOT:", DEMAND_ROOT)
    print("counties   :", ", ".join(BAY_AREA_FIPS5))
    ensure_dirs()
    print("ensured", len(ALL_DIRS), "directories")
