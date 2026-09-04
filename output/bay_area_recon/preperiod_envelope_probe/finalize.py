#!/usr/bin/env python3
"""finalize.py — apply verified corrections to the Wayback results and write preperiod_envelope.csv."""
import json, csv, os
from collections import Counter
from pathlib import Path
HERE = Path(__file__).resolve().parent
res = {r["locality"]: r for r in json.load(open(HERE/"_wayback_results.json"))}

# Corrections from manual verification of capture basenames (reject path/shared-host false positives):
CORR = {
 "Solano County":dict(preperiod_source_found="ordinance_text",source_url="https://www.codepublishing.com/CA/SolanoCounty/",observed_vintage="2015",form="text",confidence="high",notes="Wayback code-page capture 2015 (CodePublishing). [corrected: earlier domain search falsely matched an Oro-Valley-AZ PDF on the shared codepublishing.com host]"),
 "Walnut Creek":dict(preperiod_source_found="ordinance_text",source_url="https://www.codepublishing.com/CA/WalnutCreek/",observed_vintage="2014",form="text",confidence="high",notes="Wayback code-page capture 2014 (CodePublishing). [corrected: shared-host false positive rejected]"),
 "Palo Alto":dict(preperiod_source_found="none_found",source_url="",observed_vintage="",form="",confidence="low",notes="No in-window zoning MAP or code capture; earlier 'Symposium.pdf' match rejected (not a zoning map). Code on American Legal LEGACY platform (library.amlegal.com) not Wayback-queried; 2016 use-table likely recoverable from amlegal version history."),
 "San Francisco":dict(preperiod_source_found="gis_layer",source_url="https://data.sfgov.org/ (DataSF historical zoning) + project SF data",observed_vintage="~2016",form="spatial",confidence="high",notes="REFERENCE: SF circa-2016 zoning in hand via the project's own SF pipeline + DataSF historical zoning layers (not the SoMa guide the regex first matched)."),
 "Daly City":dict(form="text",notes="Wayback capture 2014 of 'Zoning Ordinance.pdf' (ordinance text as PDF, not a map). Valid pre-period envelope (text)."),
 "San Ramon":dict(confidence="med",notes="Wayback city-site capture 2015 of 'rezoning.pdf' — ambiguous (a rezoning map/exhibit, may not be the full citywide zoning map); to_verify it is the citywide envelope."),
 "Tiburon":dict(notes="Wayback capture 2015 of 'Tiburon-Zoning-Map-Effective-March-31-2006.pdf' — the 2006-effective map still posted in-window = the circa-2016 envelope (stable)."),
}
for loc,c in CORR.items():
    if loc in res: res[loc].update(c)

COLS=["locality","fips_geoid","minutes_start_year","preperiod_source_found","source_url",
      "observed_vintage","form","confidence","notes"]
rows=sorted(res.values(), key=lambda r:(r["preperiod_source_found"],r["locality"]))
with open(HERE/"preperiod_envelope.csv","w",newline="") as fh:
    w=csv.DictWriter(fh,fieldnames=COLS,extrasaction="ignore"); w.writeheader(); w.writerows(rows)

n=len(rows)
print(f"preperiod_envelope.csv: {n} estimation-sample localities\n")
print("=== preperiod_source_found ===")
for k,v in Counter(r["preperiod_source_found"] for r in rows).most_common(): print(f"  {v:3d}  {k}")
print("\n=== by form ===")
for k,v in Counter(r["form"] for r in rows if r["form"]).most_common(): print(f"  {v:3d}  {k}")
usable=[r for r in rows if r["preperiod_source_found"] in ("zoning_pdf","ordinance_text","gis_layer")]
print(f"\nUSABLE in-window pre-period source: {len(usable)}/{n}")
print("  spatial:", sum(1 for r in usable if r["form"]=="spatial"), "| text:", sum(1 for r in usable if r["form"]=="text"))
print("nzlud_proxy only (2019-21, post-SB35):", [r['locality'] for r in rows if r['preperiod_source_found']=='nzlud_proxy'])
print("none_found:", [r['locality'] for r in rows if r['preperiod_source_found']=='none_found'])
print("\nUSABLE list:")
for r in usable: print(f"  {r['observed_vintage']:8s} {r['form']:8s} {r['locality']:16s} {os.path.basename(r['source_url'][:70])}")
