#!/usr/bin/env python3
"""
inventory.py — turn the manifest into the report's source inventory.

Reads ``demand/_manifest.csv`` and emits BOTH a console summary and a LaTeX
``longtable`` body (``report/inventory_rows.tex``) that the report \\inputs, so
every count / byte total / status in the inventory is generated from the ACTUAL
manifest rather than transcribed by hand.

    python -m demand_estimation.report.inventory
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from .. import demand_paths as dp

# Curated metadata per §2 automated source (manifest-source -> display fields).
# Counts / bytes / status come from the manifest; these are the static columns.
SOURCE_META = {
    "acs_pums": dict(
        name="ACS 5-yr PUMS",
        role="Household micro-data (BLP random-coefficient moments)",
        vintage="2020--2024 ACS 5-yr",
        access="FTP bulk (keyless)",
        url="www2.census.gov/programs-surveys/acs/data/pums/2024/5-Year",
    ),
    "acs_tables": dict(
        name="ACS aggregate tables",
        role="Tract/BG shares: tenure, value, rent, income, structure",
        vintage="2023 ACS 5-yr",
        access="Census Data API (key)",
        url="api.census.gov/data/2023/acs/acs5",
    ),
    "lodes": dict(
        name="LEHD LODES8",
        role="Job access $JA_\\ell$ (agglomeration coupling)",
        vintage="LODES8 v8.3 (2022, 2015)",
        access="direct download",
        url="lehd.ces.census.gov/data/lodes/LODES8/ca",
    ),
    "tiger": dict(
        name="TIGER/Line geography",
        role="Geography spine; BG$\\leftrightarrow$jurisdiction overlay",
        vintage="TIGER2023",
        access="direct download",
        url="www2.census.gov/geo/tiger/TIGER2023",
    ),
    "ssurgo": dict(
        name="USDA SSURGO (tabular)",
        role="\\textbf{Instrument}: shrink-swell/plasticity/bearing (mukey)",
        vintage="SDA current (2025 saverest)",
        access="Soil Data Access REST",
        url="sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest",
    ),
    "ssurgo_spatial": dict(
        name="SSURGO (spatial)",
        role="Mapunit polygons --- the mukey$\\to$BG join",
        vintage="SDA current",
        access="Soil Data Access REST",
        url="sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest",
    ),
    "hazard": dict(
        name="CGS seismic hazard",
        role="Instrument \\emph{control} (seismic capitalization)",
        vintage="CGS current",
        access="ArcGIS Feature Service",
        url="services2.arcgis.com/Geohazards",
    ),
    "zoning": dict(
        name="Gov-OPR statewide zoning",
        role="By-right envelope backstop (Layer II)",
        vintage="2022--23 snapshot",
        access="ArcGIS Feature Service",
        url="services8.arcgis.com/California_Statewide_Zoning_North",
    ),
    "amenities_openspace": dict(
        name="CPAD open space",
        role="Amenity \\emph{control} (proximity to protected areas)",
        vintage="CPAD 2024a",
        access="ArcGIS Feature Service",
        url="services1.arcgis.com/cpad_2024a_unitsgdb",
    ),
    "amenities_schools": dict(
        name="CDE schools",
        role="Amenity: school enrollment / quality proxy",
        vintage="CDE current",
        access="bulk flat file",
        url="www3.cde.ca.gov/demo-downloads/ce/cenroll2324.txt",
    ),
    "amenities_transit": dict(
        name="GTFS transit",
        role="Amenity: transit access (all operators via 511)",
        vintage="511 + agency current",
        access="511 API + per-agency",
        url="api.511.org/transit/datafeeds",
    ),
    "migration_irs": dict(
        name="IRS SOI migration",
        role="Moving-cost backup (free; Infutor/Verisk upgrade)",
        vintage="2021--2022",
        access="direct download",
        url="irs.gov/pub/irs-soi/countyinflow2122.csv",
    ),
    "crime": dict(
        name="Crime (SF+Oak+FBI)",
        role="Location disamenity: incident BG (SF, Oakland) + FBI county",
        vintage="2019--24; FBI 2022",
        access="Socrata + FBI CDE + geocoder",
        url="data.sfgov.org; api.usa.gov/crime/fbi",
    ),
}

# Order rows as they appear in the brief.
ROW_ORDER = ["acs_pums", "acs_tables", "lodes", "tiger", "ssurgo",
             "ssurgo_spatial", "hazard", "zoning", "amenities_openspace",
             "amenities_schools", "amenities_transit", "migration_irs", "crime"]


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}\\,{unit}"
        n /= 1024
    return f"{n:.1f}\\,GB"


def load_by_source():
    by = defaultdict(list)
    if dp.MANIFEST_CSV.exists():
        with open(dp.MANIFEST_CSV, newline="") as fh:
            for r in csv.DictReader(fh):
                by[r["source"]].append(r)
    return by


def _agg(items):
    nbytes = sum(int(x["bytes"]) for x in items if x["bytes"].isdigit())
    statuses = sorted({x["status"] for x in items})
    return len(items), nbytes, statuses


def write_latex(by, out_path: Path):
    lines = []
    for key in ROW_ORDER:
        m = SOURCE_META[key]
        items = by.get(key, [])
        if items:
            n, nbytes, statuses = _agg(items)
            artifacts = str(n)
            size = human(nbytes) if nbytes else "--"
            status = ", ".join(statuses).replace("_", "\\_")  # escape for LaTeX
        else:
            artifacts, size, status = "0", "--", "not run"
        # checksum status: present if any row has a sha256
        has_sha = any(x.get("sha256") for x in items)
        chk = "sha256" if has_sha else ("--" if not items else "n/a")
        row = (f"{m['name']} & {m['role']} & {m['vintage']} & {m['access']} & "
               f"\\url{{{m['url']}}} & {artifacts} & {size} & {status} & {chk}")
        lines.append(row + " \\\\")
    out_path.write_text("\n".join(lines) + "\n")


def main():
    by = load_by_source()
    print(f"{'source':22s} {'#':>3s} {'bytes':>13s}  statuses")
    print("-" * 72)
    grand = 0
    for src in sorted(by):
        n, nbytes, statuses = _agg(by[src])
        grand += nbytes
        print(f"{src:22s} {n:>3d} {nbytes:>13d}  {','.join(statuses)}")
    print("-" * 72)
    total_rows = sum(len(v) for v in by.values())
    print(f"{'TOTAL':22s} {total_rows:>3d} {grand:>13d}")

    out = Path(__file__).resolve().parent / "inventory_rows.tex"
    write_latex(by, out)
    print(f"\nwrote LaTeX rows -> {out}")


if __name__ == "__main__":
    main()
