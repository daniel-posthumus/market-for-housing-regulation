#!/usr/bin/env python3
"""
consolidate.py — assemble zoning_map_form.csv from the 8 per-county probe outputs.

Joins fips_geoid from the source census, adds the SF reference row, writes
zoning_map_form.csv, and prints the form distribution — with an honest split between
NATIVE/consortium GIS layers and the CA Statewide Zoning fallback (the universal layer
that covers ~all localities but is a 2022-23 aggregated snapshot).

Run: .venv/bin/python output/bay_area_recon/zoning_map_form_probe/consolidate.py
"""
from __future__ import annotations
import csv, json, glob
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
CENSUS = HERE.parent / "bay_area_census" / "bay_area_locality_census.csv"
COLUMNS = ["locality","fips_geoid","best_form","spatial_url","source_type",
           "download_apparent","confidence","notes"]

SF_ROW = {"locality":"San Francisco","best_form":"gis_layer",
          "spatial_url":"https://data.sfgov.org/Geographic-Locations-and-Boundaries/Zoning-Districts/3i4a-hu95",
          "source_type":"city_opendata","download_apparent":"yes","confidence":"high",
          "notes":"REFERENCE: SF has a DataSF Zoning Districts GIS layer (downloadable) plus the project's zoning_use_districts.pdf. Also covered by CA Statewide Zoning."}

STATEWIDE = "gis.data.ca.gov"


def norm_dl(v):
    s=str(v).strip().lower()
    if s in ("yes","true"): return "yes"
    if s in ("no","false"): return "no"
    return "unknown"


def main():
    census={r["locality"]:r for r in csv.DictReader(open(CENSUS))}
    rows=[]
    for f in sorted(glob.glob(str(HERE/"raw"/"*.json"))):
        for r in json.load(open(f)):
            loc=r["locality"].strip()
            rows.append({
                "locality":loc,
                "fips_geoid":census.get(loc,{}).get("fips_geoid","to_verify"),
                "best_form":r.get("best_form","unknown"),
                "spatial_url":r.get("spatial_url") or "",
                "source_type":r.get("source_type","unknown"),
                "download_apparent":norm_dl(r.get("download_apparent")),
                "confidence":r.get("confidence","low"),
                "notes":r.get("notes",""),
            })
    sf={**SF_ROW,"fips_geoid":"0667000"}
    rows.append({k:sf.get(k,"") for k in COLUMNS})

    # coverage_class: distinguish native/consortium gis_layer from statewide-only fallback
    def cclass(r):
        if r["best_form"]!="gis_layer": return r["best_form"]
        if STATEWIDE in r["spatial_url"]: return "gis_layer_STATEWIDE_only(native_unchecked)"
        return "gis_layer_native_or_consortium"
    for r in rows: r["_cc"]=cclass(r)
    rows.sort(key=lambda r:(r["best_form"],r["locality"]))

    with open(HERE/"zoning_map_form.csv","w",newline="") as fh:
        w=csv.DictWriter(fh,fieldnames=COLUMNS); w.writeheader()
        w.writerows([{k:r[k] for k in COLUMNS} for r in rows])

    n=len(rows)
    print(f"TOTAL ROWS: {n}  GEOID joined: {sum(1 for r in rows if r['fips_geoid'] not in ('','to_verify'))}/{n}")
    print("\n=== best_form (as reported) ===")
    for k,v in Counter(r['best_form'] for r in rows).most_common(): print(f"  {v:3d}  {k}")
    print("\n=== coverage class (native/consortium vs statewide-fallback) ===")
    for k,v in Counter(r['_cc'] for r in rows).most_common(): print(f"  {v:3d}  {k}")
    print("\n=== source_type ===")
    for k,v in Counter(r['source_type'] for r in rows).most_common(): print(f"  {v:3d}  {k}")
    dl=Counter(r['download_apparent'] for r in rows)
    print(f"\n=== download_apparent: {dict(dl)} ===")

    # native gis_layer localities (the authoritable-without-statewide set)
    native=[r for r in rows if r['_cc']=="gis_layer_native_or_consortium"]
    print(f"\n=== NATIVE/consortium gis_layer: {len(native)} localities ===")
    print("  "+", ".join(sorted(r['locality'] for r in native)))
    viewers=[r for r in rows if r['best_form']=='gis_viewer_only']
    pdfs=[r for r in rows if r['best_form']=='pdf_map']
    print(f"\n=== gis_viewer_only ({len(viewers)}): "+", ".join(sorted(r['locality'] for r in viewers)))
    print(f"\n=== pdf_map ({len(pdfs)}): "+", ".join(sorted(r['locality'] for r in pdfs)))
    nf=[r for r in rows if r['best_form'] in ('none_found','unknown')]
    print(f"\n=== none_found/unknown ({len(nf)}): "+", ".join(sorted(r['locality'] for r in nf)))


if __name__ == "__main__":
    main()
