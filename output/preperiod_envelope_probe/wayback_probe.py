#!/usr/bin/env python3
"""
wayback_probe.py — locate & DATE a circa-2016 (2014-2018) zoning envelope per estimation-sample
locality via the Wayback Machine CDX API (capture timestamp = verifiable vintage).
Locate-and-date only; no download, no reconstruction. none_found/current_only over guess.
"""
from __future__ import annotations
import json, urllib.request, urllib.parse, time, re
from pathlib import Path

HERE = Path(__file__).resolve().parent
sample = json.load(open("/tmp/preperiod_sample.json"))
UA="Mozilla/5.0 AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
WIN_FROM,WIN_TO="20140101","20181231"

def cdx(params):
    base="http://web.archive.org/cdx/search/cdx?"+urllib.parse.urlencode(params)
    req=urllib.request.Request(base,headers={"User-Agent":UA})
    try:
        with urllib.request.urlopen(req,timeout=50) as r:
            data=json.loads(r.read() or b"[]")
    except Exception as e:
        return None  # error
    return data[1:] if data and isinstance(data,list) and len(data)>1 else []

def first_year(rows):
    yrs=sorted(r[1][:4] for r in rows if len(r)>1 and r[1][:4].isdigit())
    return yrs[0] if yrs else None

def code_root(url):
    """Broaden a deep code URL (with nodeId/query) to the code-library root for that locality,
    so Wayback prefix-matching catches any in-window capture of the code section."""
    u=url.split("?")[0].replace("https://","").replace("http://","").rstrip("/")
    m=re.match(r"(library\.municode\.com/[a-z]{2}/[^/]+)",u)            # Municode
    if m: return m.group(1)
    m=re.match(r"(codelibrary\.amlegal\.com/codes/[^/]+)",u)           # American Legal
    if m: return m.group(1)
    m=re.match(r"(library\.qcode\.us/lib/[^/]+)",u)                    # QCode
    if m: return m.group(1)
    m=re.match(r"(www\.codepublishing\.com/[A-Z]{2}/[^/]+)",u)         # CodePublishing
    if m: return m.group(1)
    m=re.match(r"(ecode360\.com/[A-Za-z0-9]+)",u)                      # eCode360
    if m: return m.group(1)
    m=re.match(r"([^/]+\.municipal\.codes)",u)                         # public.law
    if m: return m.group(1)
    return u

out=[]
for s in sample:
    loc=s["locality"]
    rec={"locality":loc,"fips_geoid":s["geoid"],"minutes_start_year":s["minutes_year"],
         "preperiod_source_found":"none_found","source_url":"","observed_vintage":"",
         "form":"","confidence":"low","notes":""}
    notes=[]
    # 1) city-domain zoning PDF (preferred: spatial map) in-window
    pdf_hit=None
    for d in s.get("city_domains",[]):
        rows=cdx({"url":d,"matchType":"domain","from":WIN_FROM,"to":WIN_TO,
                  "filter":r"original:.*[Zz]oning.*\.[pP][dD][fF].*","collapse":"urlkey",
                  "output":"json","limit":"40"})
        if rows:
            # prefer rows whose original looks like a zoning map pdf
            maps=[r for r in rows if re.search(r"zoning.*\.pdf",r[2],re.I)]
            pick=maps or rows
            if pick:
                y=first_year(pick); pdf_hit=(pick[0][2],y); break
        time.sleep(0.3)
    # 2) code page (ordinance text use-table) in-window
    code_hit=None
    if s.get("code_url"):
        cu=code_root(s["code_url"])
        rows=cdx({"url":cu+"*","from":WIN_FROM,"to":WIN_TO,"filter":"statuscode:200",
                  "collapse":"timestamp:6","output":"json","limit":"30"})
        if rows:
            code_hit=(s["code_url"], first_year(rows))
        time.sleep(0.3)
    # classify (prefer spatial pdf > ordinance text)
    if pdf_hit and pdf_hit[1]:
        rec.update(preperiod_source_found="zoning_pdf",source_url=pdf_hit[0],
                   observed_vintage=pdf_hit[1],form="spatial",confidence="high")
        notes.append(f"Wayback PDF capture {pdf_hit[1]}")
    elif code_hit and code_hit[1]:
        rec.update(preperiod_source_found="ordinance_text",source_url=code_hit[0],
                   observed_vintage=code_hit[1],form="text",confidence="high")
        notes.append(f"Wayback code-page capture {code_hit[1]} ({s.get('zoning_host')})")
    else:
        # NZLUD proxy for covered cities; else current_only/none
        if s.get("in_nzlud")=="yes":
            rec.update(preperiod_source_found="nzlud_proxy",source_url="github.com/mtmleczko/nzlud",
                       observed_vintage="2019-21",form="text",confidence="low")
            notes.append("NZLUD 2019-21 only (POST-SB35 vintage gap)")
        else:
            rec.update(preperiod_source_found="none_found")
            notes.append("no in-window Wayback capture of code or zoning PDF found")
    if s.get("in_nzlud")=="yes" and rec["preperiod_source_found"]!="nzlud_proxy":
        notes.append("also in NZLUD (2019-21 proxy avail)")
    rec["notes"]="; ".join(notes)
    out.append(rec)
    print(f"  {loc:18s} -> {rec['preperiod_source_found']:14s} {rec['observed_vintage']:8s} {rec['notes'][:60]}")

json.dump(out, open(HERE/"_wayback_results.json","w"))
print(f"\nDONE {len(out)} probed")
