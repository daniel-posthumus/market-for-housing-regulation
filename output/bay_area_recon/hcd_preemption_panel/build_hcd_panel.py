#!/usr/bin/env python3
"""
build_hcd_panel.py — HCD preemption (builder's-remedy) treatment panel for the estimation sample.
Source: HCD Housing Element Review & Compliance Report (data.ca.gov/dataset/housing-element-compliance-report,
the file already pulled to output/bay_area_recon/_source_data/hcd.csv) + HCD Prohousing Designated Jurisdictions
list. Every date traces to an HCD record; unsourced -> to_verify, never guessed.
"""
from __future__ import annotations
import csv, json
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
HCD = HERE.parent / "_source_data" / "hcd.csv"
SAMPLE = json.load(open("/tmp/preperiod_sample.json"))
geoid = {s["locality"]: s["geoid"] for s in SAMPLE}

# ABAG (Bay Area) 6th-cycle statutory Housing-Element adoption deadline.
DEADLINE = date(2023,1,31)
SRC = "HCD HE Review & Compliance Report (data.ca.gov/dataset/housing-element-compliance-report)"
PH_SRC = "HCD Prohousing Designated Jurisdictions (hcd.ca.gov/.../prohousing/designated-jurisdictions, as of 2026-03-26)"

# Prohousing: designated (from HCD list) + sourced dates where an HCD/Gov news release gives one.
PROHOUSING = {  # locality: (designated?, date_or_None, date_source)
 "Mountain View":(True,"2024-01","HCD news release Jan 2024"),
 "Petaluma":(True,"2024-01","HCD news release Jan 2024"),
 "Walnut Creek":(True,"2024-08-01","HCD news release Aug 1 2024"),
 "Santa Rosa":(True,"2025","HCD/Gov 2025 announcement (exact date to_verify)"),
 "Sonoma County":(True,"2025","HCD/Gov 2025 announcement (exact date to_verify)"),
 "San Francisco":(True,None,None),"Alameda":(True,None,None),"El Cerrito":(True,None,None),
 "San Leandro":(True,None,None),"Oakland":(True,None,None),"Sunnyvale":(True,None,None),
 "Emeryville":(True,None,None),"Hayward":(True,None,None),
}

def pdate(s):
    s=(s or "")[:10]
    try: return date.fromisoformat(s)
    except Exception: return None
def months(a,b):
    if not a or not b: return None
    d=(b.year-a.year)*12+(b.month-a.month)+(b.day-a.day)/30.0
    return round(max(0.0,d),1)

# index HCD adopted records by jurisdiction (uppercase)
hcd={}
for r in csv.DictReader(open(HCD)):
    hcd.setdefault(r["Jurisdiction"].strip().upper(),[]).append(r)

COLS=["locality","fips_geoid","he_adoption_date","hcd_certification_date","prohousing_date",
      "status_sequence","exposure_window_inputs","source_url","confidence","flags"]
rows=[]
for s in SAMPLE:
    loc=s["locality"]; recs=hcd.get(loc.upper(),[])
    adopted=[r for r in recs if r["Record Type"].strip().lower()=="adopted"]
    flags=[]
    if not adopted:
        rows.append({"locality":loc,"fips_geoid":geoid[loc],"he_adoption_date":"to_verify",
            "hcd_certification_date":"to_verify","prohousing_date":"","status_sequence":"no HCD record matched",
            "exposure_window_inputs":"","source_url":SRC,"confidence":"low","flags":"no_hcd_match"})
        continue
    r=adopted[0]
    recv=pdate(r["Date Received"]); rev=pdate(r["Reviewed Date"]); status=r["Compliance Status"].strip()
    exp_to_finding=months(DEADLINE,rev); exp_to_recv=months(DEADLINE,recv)
    ph=PROHOUSING.get(loc)
    if ph and ph[0]:
        ph_date = ph[1] or "designated"
        if not ph[1]: flags.append("prohousing_date_to_verify(HCD tracker XLS)")
    else:
        ph_date="not_designated"
    exposure=(f"ABAG 6th-cycle statutory deadline 2023-01-31; HE received {recv}; HCD compliance finding "
              f"{rev} (status={status}); builder's-remedy exposure ~= deadline->finding = {exp_to_finding} months "
              f"(alt. deadline->HE-received = {exp_to_recv} months if self-adoption convention used)")
    rows.append({"locality":loc,"fips_geoid":geoid[loc],
        "he_adoption_date":str(recv) if recv else "to_verify",
        "hcd_certification_date":str(rev) if rev else "to_verify",
        "prohousing_date":ph_date,
        "status_sequence":f"6S Adopted -> {status} ({rev}); HCD current-status SNAPSHOT (pre-cert review/decert history not in this dataset)",
        "exposure_window_inputs":exposure,
        "source_url":SRC + ("; "+PH_SRC if ph_date!="not_designated" else ""),
        "confidence":"high",
        "flags":";".join(flags)})
rows.sort(key=lambda r:(r["hcd_certification_date"]))
with open(HERE/"hcd_preemption_panel.csv","w",newline="") as fh:
    w=csv.DictWriter(fh,fieldnames=COLS); w.writeheader(); w.writerows(rows)

# summary
clean=[r for r in rows if r["confidence"]=="high" and r["he_adoption_date"]!="to_verify"]
print(f"HCD panel: {len(rows)} sample localities; {len(clean)} with clean sourced compliance dates")
print(f"prohousing-designated: {sum(1 for r in rows if r['prohousing_date']!='not_designated')}")
print("\nbuilder's-remedy exposure (deadline 2023-01-31 -> HCD finding), sorted:")
def expmo(r):
    import re; m=re.search(r"deadline->finding = ([\d.]+)",r["exposure_window_inputs"]); return float(m.group(1)) if m else -1
for r in sorted(rows,key=expmo):
    import re; m=re.search(r"= ([\d.]+) months \(alt",r["exposure_window_inputs"])
    print(f"  {r['locality']:16s} cert={r['hcd_certification_date']}  exposure~{m.group(1) if m else '?':>4}mo  prohousing={r['prohousing_date']}")
