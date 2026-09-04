#!/usr/bin/env python3
"""Task 1 (v2): CivicPlus depth via the server-rendered AgendaCenter Search endpoint
(/AgendaCenter/Search/?CIDs={cid}&startDate=..&endDate=..), which returns ViewFile/Minutes
links (definitively minutes-type) for a date range. Find the oldest minutes year. Listing-read only."""
from __future__ import annotations
import json, re, urllib.request, ssl, time
from pathlib import Path
from urllib.parse import urlparse, urlencode
ctx=ssl.create_default_context();ctx.check_hostname=False;ctx.verify_mode=ssl.CERT_NONE
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
def get(u,t=30):
    try:
        with urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":UA}),timeout=t,context=ctx) as r:
            return r.status, r.read().decode("utf-8","ignore")
    except Exception as e: return None, f"{type(e).__name__}"

cp=json.load(open("/tmp/cp14.json"))
out=[]
for c in cp:
    loc,url=c["locality"],c["url"]
    p=urlparse(url); base=f"{p.scheme}://{p.netloc}"
    rec={"locality":loc,"fips_geoid":c["geoid"],"earliest_minutes_year":"unknown","continuity":"unknown",
         "minutes_type_basis":"unknown","confidence":"low","method":"","flags":""}
    # 1) find the Planning Commission committee CID from the main AgendaCenter
    st,html=get(base+"/AgendaCenter")
    cid=None; cname=None
    if st==200:
        for m in re.finditer(r'/AgendaCenter/([A-Za-z0-9\-]*(?:Planning|Environmental-Planning|Planning-and-Transportation)[A-Za-z0-9\-]*)-(\d+)', html):
            cname, cid = m.group(1), m.group(2); break
    if not cid:
        # try the given committee URL directly for a CID
        m=re.search(r'-(\d+)/?$', url); cid=m.group(1) if m else None
        cname=cname or "Planning Commission"
    if not cid:
        rec["method"]=f"could not locate PC committee CID (AgendaCenter fetch {st})"; rec["flags"]="cid_not_found"
        out.append(rec); print(f"  {loc:16s} -> CID not found ({st})"); continue
    # 2) search a wide window for minutes-typed files
    q=urlencode({"term":"","CIDs":cid,"startDate":"01/01/2004","endDate":"12/31/2018"})
    st2,res=get(f"{base}/AgendaCenter/Search/?{q}")
    if st2!=200:
        rec["method"]=f"Search endpoint HTTP {st2} (CID {cid})"; rec["flags"]="search_failed"
        out.append(rec); print(f"  {loc:16s} -> Search HTTP {st2}"); continue
    mdates=sorted(re.findall(r"ViewFile/Minutes/_([0-9]{8})-\d+", res))
    ayears=sorted({d[4:8] for d in re.findall(r"ViewFile/(?:Agenda|AgendaPacket)/_([0-9]{8})-\d+", res)})
    if mdates:
        myears=sorted({d[4:8] for d in mdates})
        rec.update(earliest_minutes_year=myears[0],minutes_type_basis="typed_minutes",confidence="high",
            continuity="continuous" if len(myears)>=3 else "unknown",
            method=f"AgendaCenter Search CID={cid} ({cname}) 2004-2018: {len(mdates)} Minutes files, years {myears[0]}-{myears[-1]}",
            flags="" )
        if ayears and ayears[0]<myears[0]:
            rec["flags"]="agenda_minutes_gap"; rec["method"]+=f"; agendas back to {ayears[0]}"
    elif ayears:
        rec.update(method=f"Search CID={cid}: agendas {ayears[0]}-{ayears[-1]} but NO Minutes-type files in 2004-2018",
            minutes_type_basis="agendas_only",flags="minutes_absent_in_listing")
    else:
        rec.update(method=f"Search CID={cid}: no Agenda/Minutes files returned for 2004-2018",flags="empty_window")
    out.append(rec)
    print(f"  {loc:16s} -> minutes {rec['earliest_minutes_year']:8s} [{rec['minutes_type_basis']}] CID={cid} {rec['method'][:60]}")
    time.sleep(0.3)
json.dump(out,open(Path(__file__).resolve().parent / "_results.json","w"))
res=[r for r in out if r["earliest_minutes_year"]!="unknown"]
print(f"\nDONE {len(out)} probed; {len(res)} resolved to a verified minutes year")
print("resolved <=2016:", [r['locality'] for r in res if r['earliest_minutes_year']<='2016'])
