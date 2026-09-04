#!/usr/bin/env python3
"""
consolidate.py — assemble the Bay Area locality source census.

Reads the 8 per-county raw JSON files (raw/*.json) produced by the per-county survey,
adds the San Francisco reference row, attaches a real Census GEOID to every row
(place GEOID from the Census Gazetteer for cities/towns; standard county FIPS for
counties), writes the census CSV, and prints the roll-ups (solution classes, tiers,
REVIEW/to_verify list). No GEOID is fabricated: unmatched names get geoid="to_verify".

Run:  .venv/bin/python output/bay_area_recon/bay_area_census/consolidate.py
"""
from __future__ import annotations
import csv, json, glob, re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent

COLUMNS = ["locality","type","county","fips_geoid","regulating_body",
           "minutes_platform","minutes_url","minutes_access","minutes_posted",
           "zoning_host","zoning_url","zoning_access","zoning_structure",
           "height_in_code","in_nzlud","minutes_solution_class","zoning_solution_class",
           "tractability_tier","confidence","notes"]

# Standard CA county FIPS (GEOID = 06 + 3-digit county code).
COUNTY_GEOID = {
    "Alameda":"06001","Contra Costa":"06013","Marin":"06041","Napa":"06055",
    "San Francisco":"06075","San Mateo":"06081","Santa Clara":"06085",
    "Solano":"06095","Sonoma":"06097",
}

# SF reference row (already-built pipeline; not re-surveyed per the brief).
SF_ROW = {
    "locality":"San Francisco","type":"city","county":"San Francisco",
    "regulating_body":"Planning Commission",
    "minutes_platform":"custom CMS (SF Planning)","minutes_url":"https://sfplanning.org/cpc-hearing-archives",
    "minutes_access":"clean","minutes_posted":"yes",
    "zoning_host":"American Legal","zoning_url":"https://codelibrary.amlegal.com/codes/san_francisco/latest/sf_planning/",
    "zoning_access":"unknown","zoning_structure":"per_district_sections",
    "height_in_code":"unknown","in_nzlud":"unknown",
    "minutes_solution_class":"reference_done","zoning_solution_class":"reference_done",
    "tractability_tier":"clean","confidence":"high",
    "notes":"REFERENCE ROW — SF pipeline already built (scrape→parse→label). Consolidated city-county (place GEOID 0667000). Not re-surveyed."
}


def load_geoid_lookup():
    recs = json.load(open(HERE/"ca_place_geoid.json"))
    return {name.strip().lower(): geoid for name, geoid in recs}


def attach_geoid(row, lk):
    if row["type"] == "county":
        return COUNTY_GEOID.get(row["county"], "to_verify")
    name = row["locality"]
    for cand in (f"{name} city", f"{name} town", name, f"{name} CDP"):
        g = lk.get(cand.strip().lower())
        if g:
            return g
    return "to_verify"


def main():
    lk = load_geoid_lookup()
    rows = []
    for f in sorted(glob.glob(str(HERE/"raw"/"*.json"))):
        rows.extend(json.load(open(f)))
    rows.append(SF_ROW)

    # normalize: keep only schema columns, attach geoid
    out_rows = []
    for r in rows:
        r["fips_geoid"] = attach_geoid(r, lk)
        out_rows.append({c: r.get(c, "") for c in COLUMNS})

    # sort by county then type(county last) then locality
    out_rows.sort(key=lambda r: (r["county"], r["type"]=="county", r["locality"]))

    with open(HERE/"bay_area_locality_census.csv","w",newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS); w.writeheader(); w.writerows(out_rows)

    # ---- roll-ups ----
    n = len(out_rows)
    print(f"TOTAL ROWS: {n}  (cities/towns + counties + SF reference)")
    print(f"  cities/towns: {sum(1 for r in out_rows if r['type']!='county')}  counties: {sum(1 for r in out_rows if r['type']=='county')}")
    print(f"  GEOID attached: {sum(1 for r in out_rows if r['fips_geoid'] not in ('','to_verify'))}/{n}  (to_verify: {sum(1 for r in out_rows if r['fips_geoid']=='to_verify')})")

    def rollup(field):
        c = Counter(r[field] for r in out_rows)
        return c.most_common()

    print("\n=== MINUTES solution class roll-up ===")
    for k,v in rollup("minutes_solution_class"): print(f"  {v:3d}  {k}")
    print("\n=== ZONING solution class roll-up ===")
    for k,v in rollup("zoning_solution_class"): print(f"  {v:3d}  {k}")
    print("\n=== TRACTABILITY tier (overall) ===")
    for k,v in rollup("tractability_tier"): print(f"  {v:3d}  {k}")
    print("\n=== MINUTES access ===")
    for k,v in rollup("minutes_access"): print(f"  {v:3d}  {k}")
    print("\n=== ZONING access ===")
    for k,v in rollup("zoning_access"): print(f"  {v:3d}  {k}")

    # ---- REVIEW / to_verify extraction ----
    flagged = []
    for r in out_rows:
        nt = r["notes"]
        if ("REVIEW" in nt or "to_verify" in nt or r["confidence"]=="low"
                or "to_verify" in r["regulating_body"] or r["fips_geoid"]=="to_verify"):
            flagged.append(r)
    print(f"\n=== FLAGGED (REVIEW/to_verify/low-confidence): {len(flagged)} rows ===")
    for r in flagged:
        print(f"  [{r['confidence']}] {r['locality']} ({r['county']}): {r['notes'][:130]}")


if __name__ == "__main__":
    main()
