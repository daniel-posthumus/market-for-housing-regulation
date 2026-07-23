#!/usr/bin/env python3
"""
link_permits.py
---------------
Purpose : Link Commission decisions to other administrative records. Pilot pass:
          extract building-permit numbers + planning-code sections + cross-referenced
          cases from each item, and match the permits to DBI's Building Permits on
          DataSF — so a discretionary review is linked to what actually happened to the
          permit it reviewed (issued / completed / expired / withdrawn, + parcel history).
Inputs  : labels.db (items + labels); DataSF Building Permits API (i98e-djp9).
Outputs : <out>/permit_links.csv     one row per (item, matched permit)
          <out>/reference_index.csv   every extracted reference (permit / section / case)
Author  : Dan Post
Created : 2026-07-04

Notes
-----
Minutes cite the DBI *application* number (e.g. "9801703"); DBI's permit_number often
carries a trailing letter suffix ("9801703S"). We match on the digit-stem being equal
(permit_number stripped of letters == the cited number), via a starts_with query so it
scales without pulling a whole busy block. Modern 12-digit numbers ("200210239747")
match directly. Block+lot gives the parcel's full permit history for context.
"""
from __future__ import annotations
import argparse, csv, json, re, sys, time
from pathlib import Path
import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from extraction_common import coerce_record  # noqa: E402

DBI = "https://data.sfgov.org/resource/i98e-djp9.json"      # DBI building permits
PLAN = "https://data.sfgov.org/resource/y673-d69b.json"     # Planning Records (non-projects)
OUT = HERE / "_permit_links"

BPA_RE = re.compile(
    r"(?:Building\s+Permit\s+Application|Building\s+Permit|Permit\s+Application|BPA)"
    r"\s*(?:No\.?|Number|#)?\s*([0-9][0-9.\-]{3,}[A-Z]?)", re.I)
SEC_RE = re.compile(
    r"(?:Planning\s+Code\s+)?Section[s]?\s+"
    r"(\d{2,5}(?:\.\d+)?[A-Za-z]?(?:\([a-z0-9]+\))*)", re.I)
CASE_RE = re.compile(r"\b((?:\d{2}|\d{4})[.\-]\d{3,}[A-Z]{0,6})\b")
# Government Code sections are Brown-Act boilerplate, not planning code — drop them.
NON_PLANNING_SEC = {"54954.2", "54954", "54953", "67"}


def digits(s): return re.sub(r"[^0-9]", "", s or "")
def stem(pn): return re.sub(r"[A-Za-z]+$", "", (pn or "").strip())     # drop trailing letters


def sess():
    s = requests.Session()
    s.headers["User-Agent"] = "market-for-housing-regulation/link_permits (research)"
    return s


def match_permit(s, bpa):
    """Return the DBI permit whose digit-stem equals `bpa` (handles the 'S' suffix)."""
    try:
        r = s.get(DBI, params={"$where": f"starts_with(permit_number,'{bpa}')",
                               "$select": "permit_number,block,lot,filed_date,issued_date,"
                                          "completed_date,status,permit_type_definition,description",
                               "$limit": 50}, timeout=60)
        rows = r.json()
    except Exception:
        return None
    if not isinstance(rows, list):
        return None
    exact = [p for p in rows if digits(stem(p.get("permit_number", ""))) == bpa]
    return (exact or rows or [None])[0]


def _days(a, b):
    from datetime import date
    try:
        ya, yb = a[:10], b[:10]
        da = date(*map(int, ya.split("-"))); db = date(*map(int, yb.split("-")))
        return (db - da).days
    except Exception:
        return ""


def parcel_history(s, block, lot, hearing_date):
    """All DBI permits at a parcel (block+lot) → counts + downstream (post-hearing) sample."""
    lz = str(lot).zfill(3) if str(lot).isdigit() else str(lot or "")
    params = {"block": block, "$select": "permit_number,filed_date,status,description",
              "$order": "filed_date", "$limit": 1000}
    if lz:
        params["lot"] = lz
    try:
        rows = s.get(DBI, params=params, timeout=60).json()
    except Exception:
        rows = []
    if not isinstance(rows, list):
        rows = []
    after = [p for p in rows if (p.get("filed_date") or "")[:10] > hearing_date]
    sample = "; ".join(f"{(p.get('filed_date') or '')[:7]} {p.get('status')}: "
                       f"{(p.get('description') or '')[:30]}" for p in after[:4])
    return {"parcel_permits_total": len(rows),
            "parcel_permits_after_hearing": len(after),
            "downstream_permits_sample": sample}


def rid_variants(case):
    """Candidate Planning-Records record_ids for a minutes case number (handles the
    2-digit → 4-digit year shift: minutes '98.226D' == DataSF '1998.226D')."""
    c = str(case).replace(" ", "").upper()
    out = [c]
    m = re.match(r"(\d{2})\.(\d+[A-Z/]*)$", c)
    if m:
        yy = int(m.group(1)); yyyy = 1900 + yy if yy >= 50 else 2000 + yy
        out.append(f"{yyyy}.{m.group(2)}")
    return out


def match_planning_record(s, case):
    for rid in rid_variants(case):
        try:
            rows = s.get(PLAN, params={
                "record_id": rid,
                "$select": "record_id,record_type,record_status,project_address,"
                           "open_date,close_date,applicant_org"}, timeout=60).json()
        except Exception:
            continue
        if isinstance(rows, list) and rows:
            return rows[0]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", default="done", help="label status to link (default done)")
    ap.add_argument("--db", default=str(HERE / "labeling_app" / "labels.db"))
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    import sqlite3
    con = sqlite3.connect(a.db)
    s = sess()
    a.out.mkdir(parents=True, exist_ok=True)

    def z4(x):
        x = str(x or "").strip(); return x.zfill(4) if x.isdigit() else x

    link_rows, ref_rows, plan_rows = [], [], []
    n_items = n_with_permit = n_linked = n_plan = 0
    for cn, md, data, blk in con.execute(
            "SELECT i.case_number,i.meeting_date,l.data,i.block_text FROM labels l "
            "JOIN items i ON i.id=l.item_id WHERE l.status=?", (a.status,)):
        rec = coerce_record(json.loads(data))
        txt = (rec.get("project_descr", "") or "") + " \n " + (blk or "")
        n_items += 1
        bpas = sorted({digits(m.group(1)) for m in BPA_RE.finditer(txt)
                       if len(digits(m.group(1))) >= 6})
        secs = sorted({m.group(1) for m in SEC_RE.finditer(txt)
                       if m.group(1).split("(")[0] not in NON_PLANNING_SEC})
        cites = sorted({m.group(1) for m in CASE_RE.finditer(txt)
                        if m.group(1).replace(" ", "").upper() != cn.replace(" ", "").upper()})
        for kind, vals in (("permit", bpas), ("planning_section", secs), ("cited_case", cites)):
            for v in vals:
                ref_rows.append({"case_number": cn, "meeting_date": md,
                                 "request_type": rec.get("request_type"),
                                 "ref_type": kind, "ref_value": v})
        if bpas:
            n_with_permit += 1
        for bpa in bpas:
            p = match_permit(s, bpa); time.sleep(0.3)
            row = {"case_number": cn, "meeting_date": md,
                   "request_type": rec.get("request_type"), "action": rec.get("action"),
                   "cited_permit": bpa, "dbi_permit_number": "", "dbi_status": "",
                   "dbi_type": "", "dbi_filed": "", "dbi_issued": "", "dbi_completed": "",
                   "days_filed_to_issued": "", "dbi_description": "",
                   "parcel_permits_total": "", "parcel_permits_after_hearing": "",
                   "downstream_permits_sample": ""}
            if p:
                n_linked += 1
                row.update(dbi_permit_number=p.get("permit_number"), dbi_status=p.get("status"),
                           dbi_type=p.get("permit_type_definition"),
                           dbi_filed=(p.get("filed_date") or "")[:10],
                           dbi_issued=(p.get("issued_date") or "")[:10],
                           dbi_completed=(p.get("completed_date") or "")[:10],
                           days_filed_to_issued=_days(p.get("filed_date", ""), p.get("issued_date", "")),
                           dbi_description=(p.get("description") or "")[:160])
                # (2) parcel history: all permits at the permit's parcel, post-hearing activity
                row.update(parcel_history(s, z4(p.get("block")), p.get("lot"), md)); time.sleep(0.3)
            link_rows.append(row)

        # (3) Planning-Records join: the item's OWN case + any cross-referenced cases
        for src, case in [("own", cn)] + [("cited", c) for c in cites]:
            pr = match_planning_record(s, case); time.sleep(0.25)
            if pr:
                n_plan += 1
                plan_rows.append({"case_number": cn, "meeting_date": md, "ref": src,
                                  "joined_case": case, "pr_record_id": pr.get("record_id"),
                                  "pr_record_type": pr.get("record_type"),
                                  "pr_status": pr.get("record_status"),
                                  "pr_address": pr.get("project_address"),
                                  "pr_open": (pr.get("open_date") or "")[:10],
                                  "pr_close": (pr.get("close_date") or "")[:10],
                                  "pr_applicant": pr.get("applicant_org")})

    with (a.out / "permit_links.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(link_rows[0].keys())); w.writeheader(); w.writerows(link_rows)
    with (a.out / "reference_index.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["case_number", "meeting_date", "request_type",
                                          "ref_type", "ref_value"]); w.writeheader(); w.writerows(ref_rows)
    with (a.out / "planning_record_links.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(plan_rows[0].keys())); w.writeheader(); w.writerows(plan_rows)

    from collections import Counter
    refc = Counter(r["ref_type"] for r in ref_rows)
    print(f"items: {n_items} | with a permit#: {n_with_permit} | permit rows: {len(link_rows)} "
          f"| linked to DBI: {n_linked}")
    print(f"planning-record joins: {n_plan} ({sum(1 for r in plan_rows if r['ref']=='own')} own, "
          f"{sum(1 for r in plan_rows if r['ref']=='cited')} cited)")
    print(f"references extracted: {dict(refc)}")
    print(f"✓ wrote permit_links.csv, reference_index.csv, planning_record_links.csv → {a.out}")


if __name__ == "__main__":
    main()
