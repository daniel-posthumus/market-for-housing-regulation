#!/usr/bin/env python3
import json,re,urllib.request,ssl,time
from urllib.parse import urlparse
ctx=ssl.create_default_context();ctx.check_hostname=False;ctx.verify_mode=ssl.CERT_NONE
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
def get(u,t=25):
    try:
        with urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":UA}),timeout=t,context=ctx) as r:
            return r.status,r.read().decode("utf-8","ignore")
    except Exception as e:
        return None, type(e).__name__
cp=json.load(open("/tmp/cp14.json"))
res={}
for c in cp:
    loc,url=c["locality"],c["url"]
    if loc=="Campbell":
        res[loc]=("2006","typed_minutes","high","AgendaCenter Search CID=6: minutes 2006-2016 continuous (2004=0)"); continue
    p=urlparse(url); base=f"{p.scheme}://{p.netloc}"
    cid=None
    for tryurl in (url, base+"/AgendaCenter"):
        st,h=get(tryurl)
        if st==200:
            m=re.search(r'/AgendaCenter/[A-Za-z0-9\-]*[Pp]lanning[A-Za-z0-9\-]*-(\d+)',h)
            if m: cid=m.group(1); break
        time.sleep(0.2)
    if not cid:
        res[loc]=("unknown","unknown","low","PC committee CID not discoverable via static read (newer CivicEngage; committee list JS-rendered)"); continue
    oldest=None; hits=[]
    for y in (2008,2010,2012,2014,2016):
        st,h=get(f"{base}/AgendaCenter/Search/?term=&CIDs={cid}&startDate=01/01/{y}&endDate=12/31/{y}")
        n=len(re.findall(r"ViewFile/Minutes",h or ""))
        if n>0:
            hits.append(f"{y}:{n}")
            if not oldest: oldest=str(y)
        time.sleep(0.2)
    if oldest:
        res[loc]=(oldest,"typed_minutes","high",f"AgendaCenter Search CID={cid}: minutes "+" ".join(hits))
    else:
        res[loc]=("unknown","unknown","low",f"CID={cid} found but Search returned no minutes 2008-2016")
json.dump(res,open("/tmp/cp14_final.json","w"))
for c in cp:
    r=res[c["locality"]]
    print(f"  {c['locality']:16s} -> {r[0]:8s} [{r[1]}] {r[3][:64]}")
