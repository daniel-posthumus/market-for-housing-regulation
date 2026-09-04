#!/usr/bin/env python3
"""
api_probe.py — API depth-read: verify earliest MINUTES-type year per portal API.
Reads metadata only (file type + date); no downloads, no build. Unknown over guess.
Methods: CivicClerk OData, Legistar webapi, PrimeGov ListArchivedMeetings, Granicus minutes-RSS.
"""
from __future__ import annotations
import json, re, sys, urllib.request, urllib.parse, ssl
ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"

def get(url, timeout=30):
    req=urllib.request.Request(url, headers={"User-Agent":UA,"Accept":"application/json, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.status, r.read()

def yr(s):
    m=re.search(r"(19|20)\d{2}", str(s)); return m.group(0) if m else None

# ---------- CivicClerk (modern *.api.civicclerk.com) ----------
def civicclerk(inst):
    base=f"https://{inst}.api.civicclerk.com"
    # ascending events; scan for earliest with a Minutes-type published file
    q=urllib.parse.urlencode({"$orderby":"eventDate asc","$top":1000,
        "$select":"id,eventDate,publishedFiles,categoryName"})
    try:
        st,body=get(f"{base}/v1/Events?{q}")
    except Exception as e:
        return None,"no_api",f"civicclerk {inst}: {type(e).__name__}","endpoint_unreachable",base
    try: ev=json.loads(body).get("value",[])
    except Exception: return None,"no_api","civicclerk non-json","bad_response",base
    mins=[]; agendas=0
    for e in ev:
        for f in (e.get("publishedFiles") or []):
            t=(f.get("type") or "")
            if t=="Minutes": mins.append(str(e.get("eventDate"))[:10])
            elif t in ("Agenda","Agenda Packet"): agendas+=1
    if mins:
        mins.sort(); return yr(mins[0]),"yes",f"CivicClerk OData {len(mins)} minutes files, earliest {mins[0]}; {len(ev)} events scanned","",base
    if ev:
        floor=yr(min(str(e.get("eventDate")) for e in ev))
        return None,("agenda_only" if agendas else "absent"),f"CivicClerk OData: {len(ev)} events (agenda floor {floor}), 0 Minutes-type files","minutes_absent_in_api",base
    return None,"no_api","CivicClerk OData: 0 events","empty",base

# ---------- Legistar webapi ----------
def legistar(clients):
    for c in clients:
        base=f"https://webapi.legistar.com/v1/{c}"
        q=urllib.parse.urlencode({"$filter":"EventMinutesFile ne null","$orderby":"EventDate",
            "$top":5,"$select":"EventDate,EventBodyName,EventMinutesFile"})
        try: st,body=get(f"{base}/events?{q}")
        except Exception: continue
        try: rows=json.loads(body)
        except Exception: continue
        if isinstance(rows,list):
            if rows:
                d=min(r["EventDate"] for r in rows)
                return yr(d),"yes",f"Legistar webapi /{c}: earliest EventMinutesFile {d[:10]} ({rows[0].get('EventBodyName')})","",base
            # client valid but no minutes files -> check events exist at all
            try:
                st2,b2=get(f"{base}/events?"+urllib.parse.urlencode({"$top":1,"$select":"EventDate"}))
                ev=json.loads(b2)
                if isinstance(ev,list) and ev:
                    return None,"absent",f"Legistar webapi /{c}: events exist but EventMinutesFile null across all","minutes_absent_in_api",base
            except Exception: pass
            return None,"absent",f"Legistar webapi /{c}: no minutes files","minutes_absent_in_api",base
    return None,"no_api","Legistar webapi: no client slug resolved ("+",".join(clients)+")","client_unresolved",""

# ---------- PrimeGov ----------
def primegov(inst):
    base=f"https://{inst}.primegov.com"
    earliest=None; checked=[]
    for y in range(2008,2027):
        try: st,body=get(f"{base}/api/v2/PublicPortal/ListArchivedMeetings?year={y}",timeout=20)
        except Exception: continue
        try: ms=json.loads(body)
        except Exception: continue
        if isinstance(ms,list) and ms:
            # any meeting with a minutes-type document?
            has_min=False
            for m in ms:
                docs=m.get("documentList") or m.get("documents") or []
                for d in docs:
                    nm=(d.get("templateName") or d.get("name") or d.get("type") or "")
                    if "minute" in nm.lower(): has_min=True; break
                if has_min: break
            if has_min: earliest=y; break
            checked.append(y)
    if earliest: return str(earliest),"yes",f"PrimeGov ListArchivedMeetings: earliest year with Minutes doc {earliest}","",base
    if checked: return None,"agenda_only",f"PrimeGov: meetings exist (years {min(checked)}-{max(checked)}) but no Minutes-type doc matched","minutes_type_not_matched",base
    return None,"no_api","PrimeGov: ListArchivedMeetings empty/unreachable all years","empty_or_unreachable",base

# ---------- Granicus minutes RSS ----------
def granicus_rss(sub, view_ids):
    for vid in view_ids:
        url=f"https://{sub}.granicus.com/ViewPublisherRSS.php?view_id={vid}&mode=minutes"
        try: st,body=get(url)
        except Exception: continue
        x=body.decode("utf-8","ignore")
        items=re.findall(r"<title>(.*?)</title>", x, re.S)
        dates=re.findall(r"<pubDate>(.*?)</pubDate>", x, re.S)
        years=sorted({yr(t) for t in items if yr(t)} | {yr(d) for d in dates if yr(d)})
        years=[y for y in years if y]
        if len(years)>=1 and len(re.findall(r"<item>",x))>0:
            return min(years),"yes",f"Granicus minutes-RSS view_id={vid}: {len(re.findall(r'<item>',x))} minutes items, years {min(years)}-{max(years)}","granicus_rss_capped_80",url
    return None,"no_api",f"Granicus minutes-RSS: no usable feed ({sub}, views {view_ids})","rss_empty",""

JOBS=[
 ("Daly City","CivicClerk",lambda:civicclerk("dalycityca")),
 ("El Cerrito","CivicClerk",lambda:civicclerk("elcerritoca")),
 ("Foster City","CivicClerk",lambda:civicclerk("fostercityca")),
 ("Pinole","CivicClerk",lambda:civicclerk("pinoleca")),
 ("Pleasanton","CivicClerk",lambda:civicclerk("pleasantonca")),
 ("Santa Rosa","Legistar",lambda:legistar(["santarosa","santa-rosa"])),
 ("San Mateo County","Legistar",lambda:legistar(["sanmateocounty"])),
 ("Napa","Legistar",lambda:legistar(["napacity","cityofnapa"])),
 ("Burlingame","Legistar",lambda:legistar(["burlingameca","burlingame"])),
 ("Napa County","Legistar",lambda:legistar(["napa","napacounty","countyofnapa"])),
 ("Contra Costa County","Legistar",lambda:legistar(["contra-costa","contracosta"])),
 ("Cupertino","Legistar",lambda:legistar(["cupertino"])),
 ("Hercules","Legistar",lambda:legistar(["hercules","herculesca"])),
 ("Livermore","Legistar",lambda:legistar(["livermoreca","livermore"])),
 ("Albany","PrimeGov",lambda:primegov("albanyca")),
 ("San Carlos","PrimeGov",lambda:primegov("cityofsancarlos")),
 ("San Mateo","PrimeGov",lambda:primegov("cityofsanmateo")),
 ("Rohnert Park","Granicus",lambda:granicus_rss("rpcity",[4,1,2])),
 ("Danville","Granicus",lambda:granicus_rss("danville-ca",[9,1,2])),
 ("Richmond","Granicus",lambda:granicus_rss("richmond",[3,10])),
 ("Marin County","Granicus",lambda:granicus_rss("marin",[33,2,1])),
 ("Sonoma County","Granicus",lambda:granicus_rss("sonoma-county",[2,1,3])),
]

out=[]
for loc,fam,fn in JOBS:
    try: y,conf,method,flags,ep=fn()
    except Exception as e: y,conf,method,flags,ep=None,"no_api",f"{type(e).__name__}:{str(e)[:50]}","exception",""
    out.append({"locality":loc,"platform":fam,"earliest_minutes_year":y or "unknown",
                "minutes_type_confirmed":conf,"method":method,"flags":flags,"api_endpoint_used":ep})
    print(f"  {loc:20s} [{fam:10s}] -> {y or 'unknown':8s} ({conf}) {method[:75]}")
json.dump(out, open("/tmp/api_probe_results.json","w"))
print(f"\nDONE {len(out)} probed")
