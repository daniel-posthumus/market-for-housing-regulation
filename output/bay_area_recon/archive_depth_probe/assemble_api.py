#!/usr/bin/env python3
"""
assemble_api.py — build archive_depth_api.csv from the API depth-read results,
then merge verified years into archive_depth.csv and recompute the histogram.
Only years backed by an actual Minutes-type file/item returned by the API are 'verified'.
"""
from __future__ import annotations
import csv, json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
CENSUS = HERE.parent / "bay_area_census" / "bay_area_locality_census.csv"
census = {r["locality"]: r for r in csv.DictReader(open(CENSUS))}
targets = {t["locality"]: t for t in json.load(open("/tmp/api_targets.json"))}

# (locality): [earliest_year, minutes_type_confirmed, continuity, confidence, method, flags, endpoint]
V = {
 # --- CivicClerk OData (paginated ascending, scanned publishedFiles type=Minutes) ---
 "Daly City":["2009","yes","gaps","high","CivicClerk OData: earliest Minutes-type file 2009-03-23 (minutes sparse but present)","minutes_sparse","dalycityca.api.civicclerk.com"],
 "El Cerrito":["2008","yes","unknown","high","CivicClerk OData: earliest Minutes-type file 2008-10-06","","elcerritoca.api.civicclerk.com"],
 "Pinole":["2019","yes","unknown","high","CivicClerk OData: earliest Minutes-type file 2019-01-15 (scanned 450 events asc)","","pinoleca.api.civicclerk.com"],
 "Pleasanton":["2023","yes","continuous","high","CivicClerk OData: earliest Minutes 2023-11-07; CivicClerk archive itself starts 2023","civicclerk_migration_2023","pleasantonca.api.civicclerk.com"],
 "Foster City":["unknown","no_api","unknown","low","CivicClerk MODERN api absent (fostercityca uses legacy .civicclerk.com portal)","legacy_civicclerk_no_modern_api","fostercityca.civicclerk.com (legacy)"],
 # --- Legistar webapi (EventMinutesFile filter) ---
 "Santa Rosa":["2016","yes","continuous","high","Legistar webapi /santa-rosa: earliest EventMinutesFile 2016-02-02 (City Council). Prior shallow dropdown floor 1999 was AGENDA range.","prior_dropdown_was_agenda","webapi.legistar.com/v1/santa-rosa"],
 "San Mateo County":["2017","yes","continuous","high","Legistar webapi /sanmateocounty: earliest EventMinutesFile 2017-05-16","","webapi.legistar.com/v1/sanmateocounty"],
 "Napa":["2017","yes","continuous","high","Legistar webapi /napacity: earliest EventMinutesFile 2017-03-21 (City Council)","","webapi.legistar.com/v1/napacity"],
 "Burlingame":["2014","yes","continuous","high","Legistar webapi /burlingameca: earliest EventMinutesFile 2014-07-28 (Planning Commission)","","webapi.legistar.com/v1/burlingameca"],
 "Napa County":["2021","yes","continuous","high","Legistar webapi /napa: earliest EventMinutesFile 2021-05-04 (Board of Supervisors)","","webapi.legistar.com/v1/napa"],
 "Contra Costa County":["2023","yes","continuous","high","Legistar webapi /contra-costa: earliest EventMinutesFile 2023-09-05","","webapi.legistar.com/v1/contra-costa"],
 "Hercules":["2017","yes","continuous","high","Legistar webapi /hercules: earliest EventMinutesFile 2017-09-05 (Planning Commission)","","webapi.legistar.com/v1/hercules"],
 "Cupertino":["unknown","absent","unknown","high","Legistar webapi /cupertino: events exist (status Final to 2005) but EventMinutesFile NULL across all — minutes not posted as files to Legistar","minutes_absent_in_api","webapi.legistar.com/v1/cupertino"],
 "Livermore":["unknown","no_api","unknown","low","Legistar client slug unresolved (livermoreca/livermore); city uses Granicus GovAccess, no webapi client found","client_unresolved",""],
 # --- PrimeGov ListArchivedMeetings (Minutes doc per year) ---
 "Albany":["2009","yes","unknown","high","PrimeGov ListArchivedMeetings: earliest year with a Minutes-type doc = 2009","","albanyca.primegov.com/api"],
 "San Carlos":["2008","yes","unknown","high","PrimeGov ListArchivedMeetings: earliest year with a Minutes-type doc = 2008","","cityofsancarlos.primegov.com/api"],
 "San Mateo":["2019","yes","continuous","high","PrimeGov ListArchivedMeetings (slug 'sanmateo'): meetings 2019-2026, Minutes doc-type present from 2019","","sanmateo.primegov.com/api"],
 # --- Granicus minutes-RSS (capped ~80-100 items) ---
 "Sonoma County":["2015","yes","unknown","med","Granicus minutes-RSS view_id=2: real Minutes items 2015-2022 (older BoS system; 2022+ moved to new boards UI). Verified minutes >=2015 on that system.","granicus_old_system;true_earliest_maybe_older","sonoma-county.granicus.com RSS"],
 "Richmond":["unknown","no_api","unknown","low","Granicus minutes-RSS returned only 2 Minutes items (2004-2008) — sparse/capped, inconclusive; prior probe: oldest rows video-only","granicus_rss_sparse_inconclusive","richmond.granicus.com RSS"],
 "Rohnert Park":["unknown","no_api","unknown","low","Granicus minutes-RSS inconclusive (2 items); prior shallow probe confirmed minutes only 2024+","granicus_rss_inconclusive","rpcity.granicus.com RSS"],
 "Danville":["unknown","no_api","unknown","low","Granicus minutes-RSS recency-capped (only 2025-2026 returned); prior probe: 'Summary of Actions' from ~2024. True earliest unverified","granicus_rss_recency_capped","danville-ca.granicus.com RSS"],
 "Marin County":["unknown","no_api","unknown","low","~2024 platform/domain migration; current Granicus archive starts 2024; pre-2024 minutes on a prior system (not API-readable here)","migration_2024_pre_elsewhere","marin.granicus.com"],
 # --- IQM2 (year-nav HTML; archive depth visible but minutes-TYPE not cleanly verifiable) ---
 "Pacifica":["unknown","no_api","unknown","low","IQM2 year-nav shows meetings back to ~2008, but minutes-TYPE not verifiable via a clean API (column-label heuristic insufficient). Build-time row-level check needed","iqm2_archive_2008_minutes_type_unverified","pacificacityca.iqm2.com"],
 "Santa Clara County":["unknown","no_api","unknown","low","IQM2 archive to ~2008 PLUS 2024 'OneMeeting' migration for BoS; minutes-type not cleanly verifiable via API","iqm2_plus_2024_migration","sccgov.iqm2.com"],
 # --- CivicWeb ---
 "Sonoma":["unknown","yes","unknown","med","CivicWeb MeetingTypeList exposes a Planning-Commission Minutes bucket labeled 'prior to 2017' — minutes VERIFIED to pre-date 2017; exact earliest year not displayed","civicweb_minutes_pre_2017_exact_unknown","sonomacity.civicweb.net"],
 "American Canyon":["unknown","no_api","unknown","low","CivicWeb portal: no readable minutes-depth signal in a shallow API attempt","civicweb_no_readable_depth","cityofamericancanyon.civicweb.net"],
 "Calistoga":["unknown","no_api","unknown","low","City/CivicWeb endpoints unreachable (ECONNREFUSED) on repeated attempts; no readable depth","unreachable","ci.calistoga.ca.us"],
 # --- eScribe / NovusAgenda / OnBase / Municode-Meetings ---
 "Brentwood":["unknown","no_api","unknown","low","eScribe reachable; year selector goes to ~2002 but minutes-TYPE not verifiable via a clean API (no document-type/date endpoint)","escribe_archive_2002_minutes_type_unverified","pub-brentwood.escribemeetings.com"],
 "Union City":["unknown","no_api","unknown","low","NovusAgenda: no clean queryable document-type/date API; HTML noisy","novusagenda_no_clean_api","unioncity.novusagenda.com"],
 "Concord":["unknown","no_api","unknown","low","OnBase AgendaOnline: search-limited, no clean document-type/date API for archive depth","onbase_no_depth_api","stream.ci.concord.ca.us"],
 "Brisbane":["unknown","no_api","unknown","low","Municode Meetings (JS app): no clean public document-type/date API for archive depth","municode_meetings_no_api","brisbaneca.org"],
 "Los Gatos":["unknown","no_api","unknown","low","Municode Meetings (JS app): no clean public API for archive depth","municode_meetings_no_api","losgatos-ca.municodemeetings.com"],
}
# Everything else in the target set with no clean API: CivicPlus AgendaCenter, custom CMS, Newark
def default_for(loc, t):
    fam = t["family"]
    if fam == "CivicPlus":
        return ["unknown","no_api","unknown","low","CivicPlus AgendaCenter: no queryable document-type/date API for archive depth (year accordion is JS-rendered)","civicplus_no_depth_api",t["url"]]
    if fam == "custom_CMS":
        return ["unknown","no_api","unknown","low","Custom CMS: no queryable API for archive depth","custom_cms_no_api",t["url"]]
    return ["unknown","no_api","unknown","low",f"{fam}: no clean queryable API for archive depth","no_api",t["url"]]

COLS = ["locality","fips_geoid","platform","api_endpoint_used","earliest_minutes_year",
        "minutes_type_confirmed","continuity","gap_notes","confidence","method","flags"]
rows=[]
for loc,t in targets.items():
    v = V.get(loc) or default_for(loc,t)
    yr,mtc,cont,conf,method,flags,ep = v
    rows.append({"locality":loc,"fips_geoid":t["geoid"],"platform":t["family"],
        "api_endpoint_used":ep,"earliest_minutes_year":yr,"minutes_type_confirmed":mtc,
        "continuity":cont,"gap_notes":"","confidence":conf,"method":method,"flags":flags})
# Confirmatory deep-end checks: prior shallow DROPDOWN floors that claimed deep history,
# API-confirmed via Legistar webapi (the agenda-trap is real — Oakland/Hayward were inflated).
CONFIRM = {
 "Oakland":["2014","yes","continuous","high","Legistar webapi /oakland: earliest EventMinutesFile 2014-09-18. Prior shallow dropdown floor 2000 was the AGENDA/event range, NOT minutes.","supersedes_agenda_floor_2000","webapi.legistar.com/v1/oakland"],
 "Alameda":["2005","yes","unknown","high","Legistar webapi /alameda: earliest EventMinutesFile 2005-01-12 (Library Board) — confirms minutes from 2005.","","webapi.legistar.com/v1/alameda"],
 "Hayward":["2015","yes","continuous","high","Legistar webapi /hayward: earliest EventMinutesFile 2015-09-02. Prior dropdown floor 2013 was the agenda range.","supersedes_agenda_floor_2013","webapi.legistar.com/v1/hayward"],
 "Emeryville":["2015","yes","unknown","high","Legistar webapi /emeryville: earliest EventMinutesFile 2015-03-03 — confirms 2015.","","webapi.legistar.com/v1/emeryville"],
}
for loc,v in CONFIRM.items():
    yr,mtc,cont,conf,method,flags,ep=v
    rows.append({"locality":loc,"fips_geoid":census.get(loc,{}).get("fips_geoid","to_verify"),
        "platform":"Legistar/Granicus","api_endpoint_used":ep,"earliest_minutes_year":yr,
        "minutes_type_confirmed":mtc,"continuity":cont,"gap_notes":"","confidence":conf,
        "method":method,"flags":flags})
rows.sort(key=lambda r:(r["earliest_minutes_year"]=="unknown", r["earliest_minutes_year"], r["locality"]))
with open(HERE/"archive_depth_api.csv","w",newline="") as fh:
    w=csv.DictWriter(fh,fieldnames=COLS); w.writeheader(); w.writerows(rows)

verified = {r["locality"]:r for r in rows if r["minutes_type_confirmed"]=="yes" and r["earliest_minutes_year"]!="unknown"}
print(f"archive_depth_api.csv: {len(rows)} probed rows")
print(f"NEWLY VERIFIED (Minutes-type file returned): {len(verified)}")
for loc,r in sorted(verified.items(), key=lambda x:x[1]['earliest_minutes_year']):
    print(f"  {loc:20s} {r['earliest_minutes_year']}  ({r['platform']})")
print(f"minutes_absent_in_api: {[r['locality'] for r in rows if r['minutes_type_confirmed']=='absent']}")
print(f"no_api_depth_readable: {sum(1 for r in rows if r['minutes_type_confirmed']=='no_api')}")

# ---- merge into archive_depth.csv ----
depth = list(csv.DictReader(open(HERE/"archive_depth.csv")))
dcols = depth[0].keys()
updated=0
for r in depth:
    v = verified.get(r["locality"])
    if v:  # API minutes-verified year supersedes any prior (often agenda-range) dropdown floor
        prior=r["earliest_minutes_year"]
        r["earliest_minutes_year"]=v["earliest_minutes_year"]
        r["continuity"]=v["continuity"] if v["continuity"]!="unknown" else r["continuity"]
        r["confidence"]=v["confidence"]
        note=v["method"][:90]
        if prior not in ("unknown", v["earliest_minutes_year"]):
            note=f"(supersedes prior dropdown floor {prior}) "+note
        r["method"]="[API depth-read] "+note
        if "api_verified" not in r["flags"]:
            r["flags"]=(r["flags"]+";api_verified").strip(";")
        updated+=1
with open(HERE/"archive_depth.csv","w",newline="") as fh:
    w=csv.DictWriter(fh,fieldnames=list(dcols)); w.writeheader(); w.writerows(depth)
print(f"\nMerged: {updated} 'unknown' rows in archive_depth.csv replaced with API-verified years")

# ---- recomputed histogram ----
def bucket(y):
    if y=="unknown": return "unknown"
    y=int(y)
    return "<=2005" if y<=2005 else "2006-2010" if y<=2010 else "2011-2015" if y<=2015 else "2016+"
hist=Counter(bucket(r["earliest_minutes_year"]) for r in depth)
print("\n=== UPDATED earliest_minutes_year histogram (109 localities) ===")
for k in ["<=2005","2006-2010","2011-2015","2016+","unknown"]:
    print(f"  {hist.get(k,0):3d}  {k}")
# panel breadth: localities with a known minutes-start <= candidate year
known=[r for r in depth if r["earliest_minutes_year"]!="unknown"]
print(f"\nKNOWN minutes-start: {len(known)}/109")
for bar in (2010,2014,2016,2018,2020):
    print(f"  continuous-capable from <= {bar}: {sum(1 for r in known if int(r['earliest_minutes_year'])<=bar)} localities")
