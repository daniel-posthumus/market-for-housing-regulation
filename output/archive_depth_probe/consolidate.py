#!/usr/bin/env python3
"""
consolidate.py — assemble archive_depth.csv from the 8 per-county probe outputs.

Joins fips_geoid + minutes_url from the source census (by locality), adds the SF
reference row, normalizes fields, writes archive_depth.csv, and prints the depth
histogram + a verified-vs-dropdown-only split (many floors are honestly flagged as
unverified agenda ranges, not confirmed minutes).

Run: .venv/bin/python output/archive_depth_probe/consolidate.py
"""
from __future__ import annotations
import csv, json, glob, re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
CENSUS = HERE.parent / "bay_area_census" / "bay_area_locality_census.csv"
COLUMNS = ["locality","fips_geoid","minutes_url","earliest_minutes_year","continuity",
           "gap_notes","type_basis","confidence","method","flags"]

SF_ROW = {"locality":"San Francisco","earliest_minutes_year":"1998","continuity":"continuous",
          "gap_notes":"Known from the project pipeline (scrape→parse covers 1998-present).",
          "type_basis":"typed_minutes","confidence":"high",
          "method":"reference (SF Planning CPC hearing archives, project-verified)","flags":"reference"}


def norm_year(v):
    s = str(v).strip()
    if s in ("", "unknown", "none", "n/a", "None"): return "unknown"
    m = re.search(r"(19|20)\d{2}", s)
    return m.group(0) if m else "unknown"


def norm_cont(v):
    s = str(v).strip().lower()
    if s.startswith("continuous"): return "continuous"
    if s in ("gaps","gap"): return "gaps"
    return "unknown"


def main():
    census = {r["locality"]: r for r in csv.DictReader(open(CENSUS))}
    rows = []
    for f in sorted(glob.glob(str(HERE/"raw"/"*.json"))):
        for r in json.load(open(f)):
            loc = re.sub(r"\s*\[(city|town|county)\]$","",r["locality"]).strip()
            c = census.get(loc, {})
            rows.append({
                "locality": loc,
                "fips_geoid": c.get("fips_geoid","to_verify"),
                "minutes_url": c.get("minutes_url",""),
                "earliest_minutes_year": norm_year(r.get("earliest_minutes_year")),
                "continuity": norm_cont(r.get("continuity")),
                "gap_notes": r.get("gap_notes",""),
                "type_basis": r.get("type_basis",""),
                "confidence": str(r.get("confidence","")).strip() or "low",
                "method": r.get("method",""),
                "flags": r.get("flags",""),
            })
    sf = {**SF_ROW, "fips_geoid":"0667000",
          "minutes_url": census.get("San Francisco",{}).get("minutes_url","")}
    rows.append({k: sf.get(k,"") for k in COLUMNS})
    rows.sort(key=lambda r:(r["earliest_minutes_year"]=="unknown", r["earliest_minutes_year"], r["locality"]))

    with open(HERE/"archive_depth.csv","w",newline="") as fh:
        w=csv.DictWriter(fh,fieldnames=COLUMNS); w.writeheader(); w.writerows(rows)

    n=len(rows)
    print(f"TOTAL ROWS: {n}")
    print(f"GEOID joined: {sum(1 for r in rows if r['fips_geoid'] not in ('','to_verify'))}/{n}")

    def bucket(y):
        if y=="unknown": return "unknown"
        y=int(y)
        if y<=2005: return "<=2005"
        if y<=2010: return "2006-2010"
        if y<=2015: return "2011-2015"
        return "2016+"
    hist=Counter(bucket(r["earliest_minutes_year"]) for r in rows)
    print("\n=== earliest_minutes_year histogram (ALL rows, incl. unverified floors) ===")
    for k in ["<=2005","2006-2010","2011-2015","2016+","unknown"]:
        print(f"  {hist.get(k,0):3d}  {k}")

    # verified vs dropdown-only: 'verified' = confidence high/med AND year known AND not a
    # 'dropdown_range'/'render_limited'/'type_ambiguous' floor flag
    def verified(r):
        if r["earliest_minutes_year"]=="unknown": return False
        if r["confidence"] not in ("high","med","medium","medium-high"): return False
        bad=("dropdown","render_limited","minutes_type_unverified","not_confirmed","unverified",
             "minutes_unconfirmed","floor_unseen","range_slice")
        return not any(b in r["flags"] for b in bad)
    vrows=[r for r in rows if verified(r)]
    print(f"\n=== VERIFIED-minutes-year rows (high/med conf, not a dropdown/agenda floor): {len(vrows)}/{n} ===")
    vhist=Counter(bucket(r["earliest_minutes_year"]) for r in vrows)
    for k in ["<=2005","2006-2010","2011-2015","2016+"]:
        print(f"  {vhist.get(k,0):3d}  {k}")
    print("  verified rows:", ", ".join(f"{r['locality']}={r['earliest_minutes_year']}" for r in sorted(vrows,key=lambda x:x['earliest_minutes_year'])))

    print("\n=== continuity ===")
    for k,v in Counter(r["continuity"] for r in rows).most_common(): print(f"  {v:3d}  {k}")

    # REVIEW / to_verify
    flagged=[r for r in rows if r["earliest_minutes_year"]=="unknown" or "unverifiable" in r["flags"]
             or "agendas_only" in r["type_basis"] or "only_agendas" in r["flags"]]
    print(f"\n=== unknown / unverifiable / agendas-only: {len(flagged)} ===")
    for r in flagged:
        print(f"  {r['locality']:20s} [{r['flags'][:40]}] {r['gap_notes'][:60]}")


if __name__ == "__main__":
    main()
