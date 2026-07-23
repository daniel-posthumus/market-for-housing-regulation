#!/usr/bin/env python3
"""
datasf_records.py
-----------------
Purpose : Pull SF Planning Department case records from DataSF (the structured
          data behind the PIM Property Information Map) and join them to the
          item-level decisions we extract from Commission minutes — to enrich
          (clean parcel/applicant/date fields) and to ground-truth extraction.
Inputs  : DataSF Socrata API (live):
            y673-d69b  Planning Records – Non-Projects (case-level, by type)
            qvu5-m3a2  Planning Records – Projects      (PRJ umbrella, unit fields)
          MEETING_MINUTES/processed/{structured_data.jsonl|extracted_results.csv}
Outputs : <out>/planning_nonprojects.{jsonl,csv}
          <out>/planning_projects.{jsonl,csv}
          <out>/join_report.md         (match rate vs. extracted minutes items)
          <out>/joined_items.csv        (matched minutes-item ↔ DataSF record pairs)
Author  : Dan Post
Created : 2026-06-29

Notes
-----
PIM (https://sfplanninggis.org/pim/) is only a front-end; its data is published
on DataSF as Socrata datasets with a JSON/SoQL API, so no scraping is needed.

DataSF gives the *application record* (type, parcel, applicant, dates, status),
NOT the Commission's *decision* (action/vote/conditions). It is therefore a
complement to — not a replacement for — the minutes corpus. Join keys:
  • case_number ↔ record_id  (exact, for the modern "YYYY-NNNNNN<suffix>" format;
                              pre-~2008 minutes use the old "98.226D" format and
                              fall back to parcel matching),
  • assessor_block + lot_number ↔ block + lot  (parcel-level fallback).

Usage
-----
  python datasf_records.py fetch                 # pull discretionary record types
  python datasf_records.py fetch --all-types     # pull every non-project type
  python datasf_records.py join                  # join to extracted minutes items
  python datasf_records.py selftest              # verify key-matching logic
  # --out PATH overrides the default MEETING_MINUTES/external/datasf location.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from paths import MEETING_MINUTES, LOCALITY  # noqa: E402

# ── DataSF (Socrata) ─────────────────────────────────────────────────────────
DOMAIN = "https://data.sfgov.org"
NONPROJECTS_ID = "y673-d69b"   # Planning Records – Non-Projects (case-level)
PROJECTS_ID = "qvu5-m3a2"      # Planning Records – Projects (PRJ umbrella)
PAGE = 50000                   # Socrata page size (it permits large $limit)
PAUSE_S = 1.0                  # polite spacing between requests to one host

# Record types that reach the Planning Commission / Zoning Administrator and
# correspond to the discretionary decisions we code from minutes. Mapped to the
# minutes-schema `request_type` vocabulary so the join report reads cleanly.
DISCRETIONARY_TYPES = {
    "CUA": "conditional_use",
    "VAR": "variance",
    "ZAV": "variance",                    # ZA variance
    "ZAD": "other",                       # ZA determination/letter
    "DRP": "discretionary_review",        # DR – public
    "DRM": "discretionary_review",        # DR – mandatory
    "DNX": "downtown_project",            # Sec. 309 downtown exception
    "COA": "historic",                    # certificate of appropriateness
    "HRR": "historic",                    # historic resource review
    "DES": "historic",                    # landmark designation
    "PCA": "planning_code_amendment",
    "SHD": "other",                       # shadow (Sec. 295)
    "CND": "other",                       # condominium
    "PTA": "other",                       # planning / transportation
    "LBR": "other",                       # legacy business registry
    "OFA": "office_allocation",
}

# Fields we keep when flattening to CSV (superset across both datasets; missing
# keys are simply blank for a given row).
KEEP_COLS = [
    "record_id", "record_type", "project_name", "description", "record_status",
    "project_address", "block", "lot", "open_date", "close_date",
    "applicant", "applicant_org", "assigned_to_planner", "child_id",
    "building_permits", "number_of_units_net", "number_of_market_rate_units",
    "number_of_affordable_units", "number_of_units_exist", "number_of_units_prop",
]


# ── small helpers ─────────────────────────────────────────────────────────────
def app_token() -> str | None:
    """Optional Socrata app token (raises throttling limits). Resolution order:
    env SOCRATA_APP_TOKEN, then api_keys/socrata_app_token.txt at the repo root."""
    env = os.environ.get("SOCRATA_APP_TOKEN")
    if env and env.strip():
        return env.strip()
    cand = HERE.parent.parent / "api_keys" / "socrata_app_token.txt"
    if cand.exists() and cand.read_text().strip():
        return cand.read_text().strip()
    return None


def norm_case(s) -> str:
    """case_number / record_id → uppercase, spaceless (matches run_extraction)."""
    return str(s or "").replace(" ", "").upper()


def norm_parcel(block, lot) -> str:
    """(block, lot) → 'block:lot' with leading zeros stripped, '' if no block.
    DataSF zero-pads (block '0280', lot '008'); minutes labels usually don't."""
    b = str(block or "").strip().lstrip("0")
    l = str(lot or "").strip().lstrip("0")
    if not b:
        return ""
    return f"{b}:{l}"


def out_dir(arg: str | None) -> Path:
    return Path(arg).expanduser() if arg else (MEETING_MINUTES / "external" / "datasf")


# ── fetch ─────────────────────────────────────────────────────────────────────
def fetch_dataset(dataset_id: str, where: str | None, label: str) -> list[dict]:
    """Page a Socrata dataset fully via $order=:id + $offset. Returns all rows."""
    sess = requests.Session()
    sess.headers["User-Agent"] = "market-for-housing-regulation/datasf_records (research)"
    tok = app_token()
    if tok:
        sess.headers["X-App-Token"] = tok
    url = f"{DOMAIN}/resource/{dataset_id}.json"
    rows: list[dict] = []
    offset = 0
    while True:
        params = {"$limit": PAGE, "$offset": offset, "$order": ":id"}
        if where:
            params["$where"] = where
        for attempt in range(3):
            try:
                r = sess.get(url, params=params, timeout=120)
                r.raise_for_status()
                break
            except requests.RequestException as e:
                if attempt == 2:
                    raise
                print(f"  ⚠ {label} page@{offset} retry {attempt+1}: {e}")
                time.sleep(2 ** attempt)
        batch = r.json()
        rows.extend(batch)
        print(f"  {label}: +{len(batch):>6}  (total {len(rows):>7})")
        if len(batch) < PAGE:
            break
        offset += PAGE
        time.sleep(PAUSE_S)
    return rows


def write_records(rows: list[dict], stem: Path) -> None:
    """Write rows as JSONL (full) + CSV (flattened to KEEP_COLS)."""
    stem.parent.mkdir(parents=True, exist_ok=True)
    with (stem.with_suffix(".jsonl")).open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    with (stem.with_suffix(".csv")).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=KEEP_COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            # pim_link is a nested {"url": ...}; flatten not needed (dropped).
            w.writerow({k: r.get(k, "") for k in KEEP_COLS})


def cmd_fetch(args) -> None:
    od = out_dir(args.out)
    if LOCALITY != "san_francisco":
        print(f"⚠ active locality is '{LOCALITY}'; DataSF planning records are "
              f"San Francisco-only. Writing under {od} anyway.")
    types = None if args.all_types else sorted(DISCRETIONARY_TYPES)
    where = None
    if types:
        where = "record_type in (" + ",".join(f"'{t}'" for t in types) + ")"
        print(f"Non-Projects filter: {len(types)} discretionary types")

    print("Fetching Planning Records – Non-Projects …")
    nonproj = fetch_dataset(NONPROJECTS_ID, where, "non-proj")
    write_records(nonproj, od / "planning_nonprojects")

    print("Fetching Planning Records – Projects (PRJ) …")
    proj = fetch_dataset(PROJECTS_ID, None, "projects")
    write_records(proj, od / "planning_projects")

    # type breakdown for the log
    counts: dict[str, int] = {}
    for r in nonproj:
        counts[r.get("record_type", "?")] = counts.get(r.get("record_type", "?"), 0) + 1
    print("\nNon-project record types pulled:")
    for t, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {t:<5} {n:>6}  ({DISCRETIONARY_TYPES.get(t, '—')})")
    print(f"\n✓ wrote {len(nonproj)} non-project + {len(proj)} project rows → {od}")


# ── join ──────────────────────────────────────────────────────────────────────
def load_minutes_items() -> list[dict]:
    """Load extracted minutes items from structured_data.jsonl (preferred) or
    extracted_results.csv. Returns [] (with a message) if neither exists."""
    proc = MEETING_MINUTES / "processed"
    jl = proc / "structured_data.jsonl"
    csv_path = proc / "extracted_results.csv"
    items: list[dict] = []
    if jl.exists():
        for line in jl.open():
            line = line.strip()
            if line:
                items.append(json.loads(line))
        print(f"Loaded {len(items)} minutes items from {jl}")
    elif csv_path.exists():
        with csv_path.open() as f:
            items = list(csv.DictReader(f))
        print(f"Loaded {len(items)} minutes items from {csv_path}")
    else:
        print(f"⚠ no extracted minutes found at {jl} or {csv_path}.\n"
              f"  Run run_extraction.py first, or use `selftest` to verify the "
              f"join logic without the corpus.")
    return items


def index_datasf(od: Path) -> tuple[dict, dict]:
    """Build (by_case, by_parcel) indexes over the fetched DataSF records.
    by_case:   norm_case(record_id) -> record
    by_parcel: norm_parcel(block,lot) -> list[record]"""
    by_case: dict[str, dict] = {}
    by_parcel: dict[str, list] = {}
    for stem in ("planning_nonprojects", "planning_projects"):
        p = od / f"{stem}.jsonl"
        if not p.exists():
            continue
        for line in p.open():
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            c = norm_case(r.get("record_id"))
            if c:
                by_case.setdefault(c, r)
            pk = norm_parcel(r.get("block"), r.get("lot"))
            if pk:
                by_parcel.setdefault(pk, []).append(r)
    return by_case, by_parcel


def cmd_join(args) -> None:
    od = out_dir(args.out)
    if not (od / "planning_nonprojects.jsonl").exists():
        print(f"⚠ no fetched DataSF data in {od}. Run `fetch` first.")
        return
    by_case, by_parcel = index_datasf(od)
    print(f"DataSF index: {len(by_case)} records by case_number, "
          f"{len(by_parcel)} distinct parcels")
    items = load_minutes_items()
    if not items:
        return

    n = with_case = exact = parcel_only = 0
    pairs = []
    for it in items:
        cn = norm_case(it.get("case_number"))
        pk = norm_parcel(it.get("assessor_block"), it.get("lot_number"))
        if cn:
            n += 1  # only count items that have a case_number to match on
            with_case += 1
        hit = by_case.get(cn) if cn else None
        how = ""
        if hit:
            exact += 1
            how = "case_id"
        elif pk and pk in by_parcel:
            hit = by_parcel[pk][0]
            parcel_only += 1
            how = "parcel"
        if hit:
            pairs.append({
                "case_number": it.get("case_number", ""),
                "meeting_date": it.get("meeting_date", ""),
                "match": how,
                "datasf_record_id": hit.get("record_id", ""),
                "datasf_record_type": hit.get("record_type", ""),
                "datasf_status": hit.get("record_status", ""),
                "datasf_address": hit.get("project_address", ""),
                "datasf_open_date": hit.get("open_date", ""),
                "datasf_applicant_org": hit.get("applicant_org", ""),
            })

    total = len(items)
    matched = exact + parcel_only
    report = [
        "# DataSF ↔ minutes join report", "",
        f"- minutes items                : {total}",
        f"- items with a case_number     : {with_case}",
        f"- exact case_id matches        : {exact}"
        + (f"  ({exact/with_case:.1%} of items-with-case)" if with_case else ""),
        f"- parcel-only matches          : {parcel_only}",
        f"- total matched                : {matched}"
        + (f"  ({matched/total:.1%} of all items)" if total else ""),
        "",
        "Exact matches use the modern `YYYY-NNNNNN<suffix>` case format; the "
        "pre-~2008 `98.226D` format won't hit `record_id` and relies on the "
        "parcel fallback. DataSF supplies the application record; the "
        "Commission's action/vote/conditions still come only from the minutes.",
    ]
    (od / "join_report.md").write_text("\n".join(report) + "\n")
    with (od / "joined_items.csv").open("w", newline="") as f:
        cols = list(pairs[0].keys()) if pairs else ["case_number"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(pairs)
    print("\n".join(report))
    print(f"\n✓ wrote {od/'join_report.md'} and {len(pairs)} pairs → "
          f"{od/'joined_items.csv'}")


# ── selftest ──────────────────────────────────────────────────────────────────
def cmd_selftest(args) -> None:
    """Verify normalization + matching against constructed cases (no corpus)."""
    ok = True

    def check(name, got, want):
        nonlocal ok
        flag = "✓" if got == want else "✗"
        if got != want:
            ok = False
        print(f"  {flag} {name}: {got!r} == {want!r}")

    print("norm_case:")
    check("spaces+case", norm_case(" 2022-001764cua "), "2022-001764CUA")
    print("norm_parcel (DataSF zero-pad vs. minutes plain):")
    check("block 0280/008 == 280/8", norm_parcel("0280", "008"), norm_parcel("280", "8"))
    check("empty block", norm_parcel("", "5"), "")

    # a tiny synthetic DataSF index + minutes items
    datasf = [
        {"record_id": "2022-001764CUA", "record_type": "CUA", "block": "1353", "lot": "003"},
        {"record_id": "2021-009999VAR", "record_type": "VAR", "block": "0280", "lot": "008"},
    ]
    by_case = {norm_case(r["record_id"]): r for r in datasf}
    by_parcel: dict[str, list] = {}
    for r in datasf:
        by_parcel.setdefault(norm_parcel(r["block"], r["lot"]), []).append(r)

    print("join:")
    # modern case number, slightly dirty → exact case match
    m1 = by_case.get(norm_case("2022-001764 CUA"))
    check("modern exact", m1 and m1["record_id"], "2022-001764CUA")
    # old-format case number, but parcel known → parcel fallback
    pk = norm_parcel("280", "8")
    m2 = by_parcel.get(pk, [None])[0]
    check("old-format parcel fallback", m2 and m2["record_id"], "2021-009999VAR")

    print("\n" + ("✓ selftest passed" if ok else "✗ selftest FAILED"))
    sys.exit(0 if ok else 1)


# ── cli ───────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="output dir (default MEETING_MINUTES/external/datasf)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pf = sub.add_parser("fetch", help="pull DataSF planning records")
    pf.add_argument("--all-types", action="store_true",
                    help="pull every non-project type, not just discretionary ones")
    pf.set_defaults(func=cmd_fetch)
    sub.add_parser("join", help="join to extracted minutes items").set_defaults(func=cmd_join)
    sub.add_parser("selftest", help="verify the matching logic").set_defaults(func=cmd_selftest)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
