#!/usr/bin/env python3
"""
pilot_scrape.py — reproducible retrieval probes for the multi-jurisdiction minutes pilot.

PILOT ARTIFACT (not pipeline code). Documents exactly what worked and what didn't, so the
findings in minutes_platform_pilot_report.md are reproducible. Network-dependent.

Of the 3 pilot cities, only Daly City (CivicClerk modern public API) yielded actual
minutes. San Jose (Legistar/Granicus) and Fremont (CivicPlus) are gated behind Akamai
bot-protection (HTTP 403) on every document route — see report §2.

Run:  python3 pilot_scrape.py        # lists Daly City PC 2024 meetings + minutes files
"""
from __future__ import annotations
import json
import urllib.request
import urllib.parse

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
DC_API = "https://dalycityca.api.civicclerk.com"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def daly_city_pc_events(start="2024-01-01", end="2025-01-01") -> list[dict]:
    """Daly City Planning Commission events in [start, end) via the CivicClerk OData API.

    WORKS. Note civic-scraper's CivicClerkSite does NOT (it targets the legacy ASPX portal,
    not this *.api.civicclerk.com React backend).
    """
    flt = (f"categoryName eq 'Planning Commission' and "
           f"eventDate gt {start}T00:00:00Z and eventDate lt {end}T00:00:00Z")
    qs = urllib.parse.urlencode({"$filter": flt, "$orderby": "eventDate asc", "$top": 60})
    url = f"{DC_API}/v1/Events?{qs}"
    return json.loads(_get(url)).get("value", [])


def minutes_files(events: list[dict]) -> list[dict]:
    """Extract Minutes-typed publishedFiles. CAUTION: minutes are published with the
    FOLLOWING meeting's packet, and most meetings expose NO minutes file at all
    (pilot: 1 Minutes across 12 meetings). See report §2 completeness check."""
    out = []
    for e in events:
        for f in (e.get("publishedFiles") or []):
            if f.get("type") == "Minutes":
                out.append({"meeting_date": str(e.get("eventDate"))[:10],
                            "fileId": f.get("fileId"), "name": f.get("name")})
    return out


def download_file(file_id: int, dest: str) -> str:
    """Download a CivicClerk file by id to dest. Returns dest."""
    url = f"{DC_API}/v1/Meetings/GetMeetingFileStream(fileId={file_id},plainText=false)"
    data = _get(url)
    with open(dest, "wb") as fh:
        fh.write(data)
    return dest


if __name__ == "__main__":
    evs = daly_city_pc_events()
    print(f"Daly City PC meetings 2024: {len(evs)}")
    from collections import Counter
    types = Counter(f.get("type") for e in evs for f in (e.get("publishedFiles") or []))
    print(f"published file types: {dict(types)}")
    mins = minutes_files(evs)
    print(f"Minutes docs exposed via API: {len(mins)}")
    for m in mins:
        print(f"  attached@{m['meeting_date']}  fileId={m['fileId']}  {m['name']!r}")
