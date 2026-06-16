#!/usr/bin/env python3
"""
fill_numbers.py — substitute ACTUAL build counts into demand_data_report.tex.

Reads the derived parquet artifacts + the manifest and rewrites the
\\newcommand definitions at the top of the report so every number in the prose
is observed, not guessed.

    python -m demand_estimation.report.fill_numbers
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import pandas as pd

from .. import demand_paths as dp

REPORT = Path(__file__).resolve().parent / "demand_data_report.tex"


def _commas(n) -> str:
    return f"{int(n):,}"


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}\\,{unit}"
        n /= 1024
    return f"{n:.1f}\\,GB"


def compute() -> dict:
    vals: dict[str, str] = {}

    cw = dp.CROSSWALKS / "bg_to_jurisdiction.parquet"
    if cw.exists():
        d = pd.read_parquet(cw)
        vals["NBG"] = _commas(len(d))
        vals["NJURIS"] = _commas(d["jurisdiction"].nunique())

    soil = dp.SOIL_DERIVED / "mapunit_engineering_properties.parquet"
    if soil.exists():
        vals["NMAPUNITS"] = _commas(len(pd.read_parquet(soil)))

    # SSURGO spatial: polygon count + BG coverage
    spatial_csvs = list((dp.SSURGO / "spatial").glob("mupolygon_*.csv"))
    if spatial_csvs:
        npoly = sum(sum(1 for _ in open(c)) - 1 for c in spatial_csvs)
        vals["NSOILPOLY"] = _commas(npoly)
    bgsoil = dp.SOIL_DERIVED / "bg_soil_engineering.parquet"
    if bgsoil.exists():
        d = pd.read_parquet(bgsoil)
        vals["NSOILBG"] = _commas(int((d["coverage_fraction"] > 0).sum()))
        vals["NSOILCOV"] = f"{100 * d['coverage_fraction'].mean():.1f}\\%"

    hh = dp.ACS_PUMS / "ca_pums_household.parquet"
    if hh.exists():
        d = pd.read_parquet(hh, columns=["PUMA", "bay_area"])
        vals["NPUMSHH"] = _commas(len(d))
        ba = d[d["bay_area"]]
        vals["NPUMSHHBA"] = _commas(len(ba))
        vals["NPUMASBA"] = _commas(ba["PUMA"].nunique())

    ja = dp.LODES / "ca_job_access_bg_2022.parquet"
    if ja.exists():
        vals["NTOTJOBS"] = _commas(pd.read_parquet(ja)["jobs_in_bg"].sum())

    # transit: 511 regional stop count + BG coverage of the two measures
    import zipfile
    bay511 = dp.TRANSIT / "bay511" / "gtfs_bay511_regional.zip"
    if bay511.exists() and zipfile.is_zipfile(bay511):
        with zipfile.ZipFile(bay511) as zf:
            vals["NSTOPS"] = _commas(sum(1 for _ in zf.open("stops.txt")) - 1)
    ctrl = dp.CLEAN / "bg_controls.parquet"
    if ctrl.exists():
        c = pd.read_parquet(ctrl)
        if "transit_stops_1km_all" in c:
            vals["NTRANSITBG"] = _commas(int((c["transit_stops_1km_all"] > 0).sum()))
        if "transit_stops_1km_bartcaltrain" in c:
            vals["NTRANSITBGBC"] = _commas(int((c["transit_stops_1km_bartcaltrain"] > 0).sum()))

    # total bytes of present data from the manifest
    if dp.MANIFEST_CSV.exists():
        total = 0
        with open(dp.MANIFEST_CSV, newline="") as fh:
            for r in csv.DictReader(fh):
                if r["status"] in ("downloaded", "cached", "verified") and r["bytes"].isdigit():
                    total += int(r["bytes"])
        vals["TOTALSIZE"] = human(total)
    return vals


def apply(vals: dict) -> None:
    text = REPORT.read_text()
    for macro, value in vals.items():
        # replace \newcommand{\MACRO}{...anything...}
        text = re.sub(
            r"(\\newcommand\{\\" + macro + r"\}\{)[^}]*\}",
            lambda m: m.group(1) + value + "}",
            text,
        )
    REPORT.write_text(text)


def main():
    vals = compute()
    for k, v in vals.items():
        print(f"  {k} = {v}")
    apply(vals)
    print(f"\nupdated {REPORT}")


if __name__ == "__main__":
    main()
