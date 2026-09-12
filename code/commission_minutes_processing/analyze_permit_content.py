#!/usr/bin/env python3
"""
analyze_permit_content.py
-------------------------
Purpose : What DBI's building-permit record contains, and whether the projects that reach
          the Planning Commission look different from observably similar projects that do
          not. Field inventory built from the rows, types and statuses, the permit clock, a
          development-relevant universe, linkage tiers T1/T2/T3 kept apart, the
          Commission-linked comparison raw and within cells, three targeted comparisons,
          bunching at unit thresholds, and the developer's clock on the DR track.
          Spec: .claude/instructions/claude_code_brief_conditions_permits_catalogue.md, Task 3.
Inputs  : DataSF Building Permits i98e-djp9, every column, cached at
          $MFHR_DATA_ROOT/external/datasf/dbi_permits_full.csv.gz (this script's `fetch`)
          $MFHR_DATA_ROOT/extraction/<RUN>/clean/*.jsonl          (the item table)
          $MFHR_DATA_ROOT/external/{planning_records,zoning,parcels,fees}/  (acquire_external_data)
Outputs : output/planning_commission_project/permits_content/figures/*.pdf
          output/planning_commission_project/permits_content/tables/permits_content_{macros,tables}.tex
Author  : Dan Post
Created : 2026-09-11

Usage
-----
  python analyze_permit_content.py fetch [--refresh]   # every column of i98e-djp9, resumable
  python analyze_permit_content.py report              # figures, tables, macros

Notes
-----
`analyze_permits.py` cached 22 chosen columns, which was right for matching permit numbers
and is wrong for a field inventory: the brief asks what the record contains, and a column
list chosen in advance cannot answer that. `fetch` therefore takes every column the resource
endpoint returns and the inventory is built from the rows, not from any field list.

The download pages on the system `:id` (`$where=:id > last`) rather than on `$offset`: the
table is updated daily, and an offset page shifts under an insert while an id page does not.
Each page is its own file, so an interrupted fetch resumes where it stopped.

The permit-number normaliser, the printed-permit parser and the DR parcel rule are imported
from `analyze_permits.py`; the case normalisers and the `building_permits` bridge from
`acquire_external_data.py`. Nothing is re-implemented.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from paths import DATA_ROOT                                          # noqa: E402

RUN = "corpus_v2_g3"
MEMO = HERE.parents[1] / "output" / "planning_commission_project" / "permits_content"
FIG, TAB = MEMO / "figures", MEMO / "tables"
DBI_ID = "i98e-djp9"
HOST = "data.sf.gov"
FULL = DATA_ROOT / "external" / "datasf" / "dbi_permits_full.csv.gz"
PARTS = DATA_ROOT / "external" / "datasf" / "dbi_full_parts"
UA = "market-for-housing-regulation/analyze_permit_content (research; contact danpost@bu.edu)"
PAGE = 50_000
PAUSE = 1.0             # one request a second: under the brief's two-per-host ceiling


def _get(s: requests.Session, url: str, params: dict) -> requests.Response:
    for k in range(7):
        try:
            r = s.get(url, params=params, timeout=600)
        except requests.RequestException:
            time.sleep(min(300, 2 ** (k + 2)))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            ra = r.headers.get("Retry-After")
            time.sleep(float(ra) if ra and ra.isdigit() else min(300, 2 ** (k + 2)))
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f"gave up on {url} {params}")


def fetch(refresh: bool = False):
    """Every column, every row, one gzipped CSV per page, then stacked."""
    if FULL.exists() and not refresh:
        print(f"{FULL} exists; --refresh to re-download")
        return
    PARTS.mkdir(parents=True, exist_ok=True)
    if refresh:
        for p in PARTS.glob("page_*.csv.gz"):
            p.unlink()
    s = requests.Session()
    s.headers["User-Agent"] = UA
    url = f"https://{HOST}/resource/{DBI_ID}.csv"
    done = sorted(PARTS.glob("page_*.csv.gz"))
    last = ""
    if done:
        tail = pd.read_csv(done[-1], dtype=str, usecols=[":id"])
        last = tail[":id"].iloc[-1]
    k = len(done)
    while True:
        params = {"$select": ":*, *", "$order": ":id", "$limit": PAGE}
        if last:
            params["$where"] = f":id > '{last}'"
        r = _get(s, url, params)
        df = pd.read_csv(io.StringIO(r.text), dtype=str, low_memory=False)
        if not len(df):
            break
        out = PARTS / f"page_{k:05d}.csv.gz"
        tmp = out.with_suffix(".part")
        df.to_csv(tmp, index=False, compression="gzip")
        tmp.replace(out)
        last = df[":id"].iloc[-1]
        k += 1
        print(f"  page {k}: {len(df):,} rows, {df.shape[1]} columns, last :id {last}",
              flush=True)
        if len(df) < PAGE:
            break
        time.sleep(PAUSE)
    parts = sorted(PARTS.glob("page_*.csv.gz"))
    frames = [pd.read_csv(p, dtype=str, low_memory=False) for p in parts]
    full = pd.concat(frames, ignore_index=True)
    dup = int(full[":id"].duplicated().sum())
    full = full.drop_duplicates(":id")
    tmp = FULL.with_suffix(".part")
    full.to_csv(tmp, index=False, compression="gzip")
    tmp.replace(FULL)
    meta = {"rows": int(len(full)), "columns": list(full.columns), "duplicate_ids_dropped": dup,
            "retrieved": pd.Timestamp.today().isoformat(timespec="seconds"), "host": HOST,
            "id": DBI_ID}
    FULL.with_name("dbi_permits_full_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"cached {len(full):,} rows x {full.shape[1]} columns → {FULL}")


# ═══════════════════════════════════════════════════════════════════════════
# report
# ═══════════════════════════════════════════════════════════════════════════
import matplotlib                                                    # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                      # noqa: E402

plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                     "figure.dpi": 150, "savefig.bbox": "tight"})

DATES = ["permit_creation_date", "filed_date", "approved_date", "issued_date",
         "first_construction_document_date", "completed_date", "status_date",
         "last_permit_activity_date"]
NUMS = ["estimated_cost", "revised_cost", "existing_units", "proposed_units",
        "number_of_existing_stories", "number_of_proposed_stories", "plansets"]
EXITS = {"expired", "withdrawn", "cancelled", "disapproved", "revoked", "suspend"}
TYPE_SHORT = {"1": "new construction", "2": "new construction, wood frame",
              "3": "additions, alterations or repairs", "4": "sign --- erect",
              "5": "grade or excavate", "6": "demolition", "7": "wall or painted sign",
              "8": "over-the-counter alterations", "9": "(type 9, undefined)"}
DECADES = [(0, 1979, "before 1980"), (1980, 1989, "1980s"), (1990, 1999, "1990s"),
           (2000, 2009, "2000s"), (2010, 2019, "2010s"), (2020, 2100, "2020s")]
YEAR_BIN = 5
COST_Q = 0.95                    # the alteration cost rule: top 5% of the filing year
COST_Q_ALT = (0.90, 0.98)        # ...and the sensitivity checks around it
COST_NOMINAL = 1_000_000         # ...and a nominal threshold, which inflation tilts
UNIT_BINS = [(0, 0, "0"), (1, 1, "1"), (2, 4, "2--4"), (5, 9, "5--9"), (10, 19, "10--19"),
             (20, 49, "20--49"), (50, 10**9, "50+")]
INCLUSIONARY_UNITS = 10          # Planning Code §415.3: the program applies at ten units
ORDINANCE_2026_UNITS = 25        # the brief's figure for the July 2026 ordinance; not checked
PROP_C_YEAR = 2016               # Proposition C (June 2016) raised the inclusionary requirement
MIN_CELL = 1


def decade(y) -> str:
    if pd.isna(y):
        return "no filed date"
    for lo, hi, lab in DECADES:
        if lo <= y <= hi:
            return lab
    return "no filed date"


def N(x) -> str:
    return f"{int(round(x)):,}".replace(",", "{,}")


def P(x, d=1) -> str:
    return "---" if x is None or pd.isna(x) else f"{100*x:.{d}f}"


def TF(s) -> str:
    """A field name in typewriter face with a break point after every underscore: the long
    ones (`existing_construction_type_description`) otherwise run into the next column."""
    return r"\texttt{" + T(s).replace(r"\_", r"\_\allowbreak{}") + "}"


def T(s) -> str:
    return (str(s).replace("\\", r"\textbackslash{}").replace("&", r"\&").replace("%", r"\%")
            .replace("$", r"\$").replace("#", r"\#").replace("_", r"\_").replace("[", "{[}")
            .replace("]", "{]}"))


# ── 1. the field inventory, from the rows ────────────────────────────────────
PLANNING_RECORD = re.compile(r"^\d{4}[.\-]\d{3,6}")


def infer_kind(col: str, s: pd.Series) -> str:
    if col.startswith(":"):
        return "system"
    head = s.head(20000).astype(str)
    if head.str.startswith("POINT (").mean() > 0.9:
        return "geometry"
    if head.str.match(r"^\d{4}-\d{2}-\d{2}T").mean() > 0.95:
        return "date"
    vals = set(head.unique())
    if len(vals) <= 3 and vals <= {"Y", "N", "y", "n", "true", "false", "P", "R", "E"}:
        return "flag"
    num = pd.to_numeric(head, errors="coerce").notna().mean()
    if num > 0.95:
        return "code" if re.search(r"(?i)number|block|lot|id$|type$|district|zip|code",
                                   col) else "number"
    return "text"


def field_inventory(db: pd.DataFrame, retrieved: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    """Every column: type, populated share overall and by filing decade, distinct values,
    the three most frequent values, and --- where the rows do not deliver what the name
    promises --- a note saying so, with the number that shows it."""
    fy = pd.to_datetime(db.filed_date, errors="coerce").dt.year
    dec = fy.map(decade)
    rows = []
    comp = db.status.eq("complete")
    nc = db.permit_type.isin(["1", "2"])
    for c in db.columns:
        v = db[c]
        filled = v.notna() & v.astype(str).str.strip().ne("")
        s = v[filled]
        kind = infer_kind(c, s)
        # A date is counted by calendar day, not as a timestamp string: counted as strings, the
        # one value DBI stored at exactly midnight (2019-12-31T00:00:00, 23 rows of
        # first_construction_document_date) came out "most frequent" and read as a placeholder.
        top = (s.astype(str).str[:10] if kind == "date" else s.astype(str)).value_counts().head(3)
        ex = "; ".join(x[:28] for x in top.index)
        byd = filled.groupby(dec).mean()
        note = ""
        pop = filled.mean()
        pct = (lambda x: f"{100*x:.2f}" if x < 0.01 else f"{100*x:.1f}")
        # A field whose coverage depends on the era: the name promises it for the whole
        # record and the rows deliver it for part of it.
        dv = byd.reindex([lab for *_, lab in DECADES[1:]]).dropna()
        if len(dv) >= 2 and dv.max() - dv.min() > 0.5:
            note = (f"filled on {pct(dv.min())}\\% of permits filed in the {dv.idxmin()}, "
                    f"{pct(dv.max())}\\% in the {dv.idxmax()}")
        if kind == "flag" and s.nunique() == 1:
            note = (f"a flag with one value ({s.iloc[0]}) on {pct(pop)}\\% of rows; "
                    f"blank means no, or not recorded")
        elif pop < 0.01:
            note = f"filled on {pct(pop)}\\% of rows"
        if c == "record_id":
            m = s.astype(str).str.match(PLANNING_RECORD).mean()
            note = (f"not a Planning record: {100*m:.1f}\\% of values have a case-number "
                    f"shape; DBI's own identifier")
        if c == "completed_date":
            note = (f"filled on {100*filled[comp].mean():.1f}\\% of permits with status "
                    f"`complete'")
        if c == "first_construction_document_date":
            iss = nc & db.issued_date.notna()
            byd_nc = filled[iss].groupby(dec[iss]).mean()
            note = (f"on issued new-construction permits: filled on "
                    f"{pct(byd_nc.get('1980s', 0))}\\% of those filed in the 1980s, "
                    f"{pct(byd_nc.get('1990s', 0))}\\% in the 1990s, "
                    f"{pct(byd_nc.get('2000s', 0))}\\% in the 2000s")
        if c == "proposed_units":
            note = f"filled on {pct(filled[nc].mean())}\\% of new-construction permits"
        if c == "existing_units":
            alt = db.permit_type.eq("3")
            note = (f"filled on {pct(filled[alt].mean())}\\% of additions and alterations "
                    f"(blank on new construction, where nothing exists)")
        if c == "estimated_cost":
            z = pd.to_numeric(s, errors="coerce")
            note = (f"\\$1 or less on {pct((z <= 1).mean())}\\% of filled rows; the most "
                    f"frequent value is \\${z.mode().iloc[0]:,.0f}")
        if kind == "date":
            d = pd.to_datetime(s, errors="coerce")
            fut = (d > retrieved + pd.Timedelta(days=365)).mean()
            old = (d < pd.Timestamp("1900-01-01")).mean()
            if fut > 0.0001 or old > 0.0001:
                note = (f"{100*fut:.2f}\\% dated more than a year after retrieval, "
                        f"{100*old:.2f}\\% before 1900")
        if c == "permit_creation_date":
            same = (pd.to_datetime(db.permit_creation_date, errors="coerce").dt.date ==
                    pd.to_datetime(db.filed_date, errors="coerce").dt.date)
            note = f"the same day as filed\\_date on {100*same.mean():.1f}\\% of rows"
        rows.append({"field": c, "kind": kind, "populated": pop,
                     **{f"dec_{lab}": byd.get(lab, np.nan) for *_, lab in DECADES},
                     "dec_none": byd.get("no filed date", np.nan),
                     "distinct": int(s.nunique()), "examples": ex, "note": note})
    z = pd.to_numeric(db.estimated_cost, errors="coerce")
    return pd.DataFrame(rows), {"n_rows": len(db), "n_cols": db.shape[1],
                                "n_system": int(sum(c.startswith(":") for c in db.columns)),
                                "cost_le1_rows": float((z <= 1).sum() / z.notna().sum())}


# ── the permit table, typed and collapsed ────────────────────────────────────
def typed(db: pd.DataFrame) -> pd.DataFrame:
    import analyze_permits as ap
    d = db.copy()
    for c in DATES:
        d[c] = pd.to_datetime(d[c], errors="coerce").dt.tz_localize(None)
    for c in NUMS:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["stem"] = d.permit_number.fillna("").map(ap.digits)
    d["parcel"] = (d.block.fillna("").str.strip().str.upper() + ":" +
                   d.lot.fillna("").str.strip().str.upper())
    d["fy"] = d.filed_date.dt.year
    d["decade"] = d.fy.map(decade)
    d["ybin"] = (d.fy // YEAR_BIN) * YEAR_BIN
    d["status"] = d.status.fillna("").str.lower()
    return d


def collapse(d: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """One row per permit number, the earliest filed (pitfall 6: permit_number repeats once
    per lot). DBI's own `primary_address_flag` marks one row per permit too; how often the
    two rules pick different rows is reported, not assumed away."""
    first = d.sort_values(["filed_date", ":id"]).drop_duplicates("stem", keep="first")
    prim = d[d.primary_address_flag.eq("Y")].drop_duplicates("stem")
    both = first[["stem", ":id"]].merge(prim[["stem", ":id"]], on="stem", suffixes=("_f", "_p"))
    rep = d.stem.duplicated(keep=False)
    info = {"rows": len(d), "permits": int(d.stem.nunique()),
            "rows_repeating": int(d.stem.duplicated().sum()),
            "permits_repeating": int(d.loc[rep, "stem"].nunique()),
            "primary_rows": int(d.primary_address_flag.eq("Y").sum()),
            "primary_permits": int(prim.stem.nunique()),
            "rules_agree": float((both[":id_f"] == both[":id_p"]).mean()),
            "rules_compared": int(len(both))}
    # Whether the row chosen matters: the share of repeated permits whose value of each
    # permit-level field differs between its rows, and the largest such share.
    g = d[rep].groupby("stem")
    info["fields_differ_max"] = float(max(
        (g[c].nunique(dropna=False) > 1).mean()
        for c in ("permit_type", "estimated_cost", "revised_cost", "proposed_units",
                  "existing_units", "filed_date", "issued_date", "status")))
    return first, info


# ── 2. types, statuses, the clock ────────────────────────────────────────────
def clocks(p: pd.DataFrame) -> pd.DataFrame:
    p = p.copy()
    p["t_issue"] = (p.issued_date - p.filed_date).dt.days
    # first_construction_document_date is not used as an outcome: it is filled on 1.2% of
    # permits, near-empty before the 1990s, and its fill rate on new construction moves with
    # the era (the field inventory shows both), so a "construction started" share measures
    # the field, not construction.
    p["t_done"] = (p.completed_date - p.issued_date).dt.days
    p["t_total"] = (p.completed_date - p.filed_date).dt.days
    p["issued"] = p.issued_date.notna()
    p["completed"] = p.completed_date.notna() | p.status.eq("complete")
    p["exited"] = p.status.isin(EXITS)
    return p


def clock_table(p: pd.DataFrame) -> list[dict]:
    out = []
    groups = [("new construction (types 1, 2)", p.permit_type.isin(["1", "2"])),
              ("additions and alterations (3)", p.permit_type.eq("3")),
              ("demolition (6)", p.permit_type.eq("6")),
              ("over-the-counter (8)", p.permit_type.eq("8"))]
    for lab, m in groups:
        for *_, dl in DECADES:
            g = p[m & p.decade.eq(dl)]
            if len(g) < 50:
                continue
            ti = g.loc[g.t_issue >= 0, "t_issue"]
            td = g.loc[g.t_done >= 0, "t_done"]
            out.append({"type": lab, "decade": dl, "n": len(g), "issued": g.issued.mean(),
                        "issue_med": ti.median(), "issue_p90": ti.quantile(.9),
                        "completed": g.completed.mean(), "done_med": td.median(),
                        "exited": g.exited.mean()})
    return out


# ── 3. the development-relevant universe ────────────────────────────────────
def universe(p: pd.DataFrame, q: float = COST_Q, nominal: float | None = None) -> pd.DataFrame:
    """New construction, demolitions, site permits, and additions or alterations that change
    the unit count or whose cost is in the top (1-q) of their filing year. The cost rule is a
    within-year percentile so that it means the same thing in 1985 and 2025; the nominal
    alternative is reported beside it because inflation tilts it toward recent years.
    Over-the-counter alterations are excluded outright."""
    alt = p.permit_type.eq("3")
    if nominal is None:
        thr = p[alt].groupby("fy").estimated_cost.quantile(q)
        cut = p.fy.map(thr)
    else:
        cut = pd.Series(nominal, index=p.index)
    rules = pd.DataFrame({
        "new construction": p.permit_type.isin(["1", "2"]),
        "demolition": p.permit_type.eq("6"),
        "site permit": p.site_permit.eq("Y") & p.permit_type.ne("8"),
        "alteration changing units": alt & p.existing_units.notna() & p.proposed_units.notna()
                                     & p.existing_units.ne(p.proposed_units),
        "alteration over the cost threshold": alt & p.estimated_cost.ge(cut) & cut.notna(),
    })
    return rules


# ── 4. the linkage tiers ─────────────────────────────────────────────────────
def linkage(p_all: pd.DataFrame, p: pd.DataFrame) -> dict:
    """T1: a permit number printed in the minutes. T2: the Planning records'
    `building_permits` bridge. T3: the parcel + type + window rule, whose 0.72 precision was
    measured on discretionary reviews only. Commission-linked means T1 or T2; T3 is always
    reported apart."""
    import acquire_external_data as ax
    import analyze_permits as ap
    it = ax.load_items()
    stems = set(p.stem)
    it["printed"] = it.project_descr.fillna("").astype(str).map(lambda t: set(ap.parse_permits(t)))
    it["t1"] = it.printed.map(lambda s: s & stems)
    rec = ax.build_records()
    it = ax.permit_bridge(it, rec, stems)["frame"]
    it["t2"] = it.bridge.map(lambda s: s & stems)
    # T3: analyze_permits' rule, on the items with no printed number, against every row
    # (a multi-lot permit has a row per lot, and that is what makes it findable by parcel)
    from normalize import parcel_keys
    it["pk"] = [parcel_keys(b, l, ":") for b, l in zip(it.assessor_block, it.lot_number)]
    by = {k: g for k, g in p_all[p_all.permit_type.isin(ap.MAJOR_TYPES)]
          [["parcel", "stem", "filed_date"]].groupby("parcel")}
    t3 = []
    for r in it.itertuples():
        if r.printed or not r.pk or pd.isna(r.meeting_date):
            t3.append(set())
            continue
        parts = [by[k] for k in r.pk if k in by]
        if not parts:
            t3.append(set())
            continue
        c = pd.concat(parts)
        w = c[(c.filed_date >= r.meeting_date - pd.Timedelta(days=ap.PARCEL_BACK)) &
              (c.filed_date <= r.meeting_date + pd.Timedelta(days=ap.PARCEL_FWD))]
        t3.append(set(w.stem) & stems)
    it["t3"] = t3
    links = []
    for r in it.itertuples():
        for tier in ("t1", "t2", "t3"):
            for s_ in getattr(r, tier):
                links.append((s_, tier, r.item_id, r.cn, r.request_type, r.meeting_date))
    lk = pd.DataFrame(links, columns=["stem", "tier", "item_id", "cn", "request_type",
                                      "meeting_date"])
    return {"items": it, "links": lk}


def tier_flags(p: pd.DataFrame, lk: pd.DataFrame) -> pd.DataFrame:
    p = p.copy()
    for tier in ("t1", "t2", "t3"):
        p[tier] = p.stem.isin(set(lk.loc[lk.tier.eq(tier), "stem"]))
    p["linked"] = p.t1 | p.t2
    p["t3_only"] = p.t3 & ~p.linked
    p["group"] = np.where(p.linked, "Commission-linked",
                          np.where(p.t3_only, "T3 only", "not linked"))
    return p


# ── 5. the comparison, raw and within cells ──────────────────────────────────
def zoning_at_filing(p: pd.DataFrame) -> pd.Series:
    from normalize import blklot
    pan = pd.read_parquet(DATA_ROOT / "external" / "zoning" / "parcel_zoning_panel.parquet",
                          columns=["blklot", "year", "zoning"])
    key = pd.DataFrame({"blklot": [blklot(b, l) for b, l in zip(p.block, p.lot)],
                        "year": p.fy.clip(1998, 2026)}, index=p.index)
    key["year"] = key.year.where(p.fy >= 1998)
    m = key.reset_index().merge(pan, on=["blklot", "year"], how="left").drop_duplicates("index")
    return m.set_index("index").zoning.reindex(p.index)


def zoning_class(z: str) -> str:
    if not isinstance(z, str) or not z.strip():
        return ""
    z = z.strip().upper()
    for pre, lab in (("RH", "RH (house)"), ("RM", "RM (mixed)"), ("RTO", "RTO (transit)"),
                     ("RC", "RC (res-commercial)"), ("NC", "NC / NCT (neighbourhood commercial)"),
                     ("C-3", "C-3 (downtown)"), ("C-2", "C-2 / C (commercial)"),
                     ("P", "P (public)"), ("M-", "M / PDR (industrial)"),
                     ("PDR", "M / PDR (industrial)"), ("UMU", "MU (mixed use)"),
                     ("MU", "MU (mixed use)"), ("SPD", "MU (mixed use)"), ("RED", "MU (mixed use)"),
                     ("SLI", "SoMa SLI / SSO / RSD"), ("SSO", "SoMa SLI / SSO / RSD"),
                     ("RSD", "SoMa SLI / SSO / RSD"), ("CMUO", "MU (mixed use)"),
                     ("WMUG", "MU (mixed use)")):
        if z.startswith(pre):
            return lab
    if re.match(r"^[A-Z\- ]+ NCD$|NCD", z) or "NEIGHBORHOOD COMMERCIAL" in z:
        return "NC / NCT (neighbourhood commercial)"
    return "other"


def unit_bin(u) -> str:
    if pd.isna(u):
        return "unknown"
    for lo, hi, lab in UNIT_BINS:
        if lo <= u <= hi:
            return lab
    return "unknown"


def reweight(d: pd.DataFrame, cell: list[str], treat: str = "a") -> tuple[pd.Series, float]:
    """Exact-cell reweighting of the comparison group to the treated group's distribution
    over `cell`: w = (n_treated in cell / N_treated) / (n_control in cell / N_control),
    on cells that hold both. Returns the weights (treated 1, controls w, unsupported 0) and
    the share of the treated group inside the common support."""
    key = cell_key(d, cell)
    t = d[treat]
    nt = key[t].value_counts()
    nc = key[~t].value_counts()
    common = nt.index.intersection(nc.index)
    sup_t = key.isin(common) & t
    sup_c = key.isin(common) & ~t
    Nt, Nc = sup_t.sum(), sup_c.sum()
    w = pd.Series(0.0, index=d.index)
    w[sup_t] = 1.0
    w[sup_c] = key[sup_c].map((nt[common] / Nt) / (nc[common] / Nc)).values
    return w, float(sup_t.sum() / t.sum()) if t.sum() else np.nan


def cell_key(d: pd.DataFrame, cell: list[str]) -> pd.Series:
    """One string per row naming its cell. A missing value is its own cell, `(none)`: under
    pandas 2 `astype(str)` leaves NaN a float, and a float in the join is a TypeError."""
    return d[cell].apply(lambda c: c.map(lambda v: "(none)" if pd.isna(v) else str(v))) \
        .agg("|".join, axis=1)


def wstats(x: pd.Series, w: pd.Series) -> tuple[float, float]:
    ok = x.notna() & (w > 0)
    if not ok.any():
        return np.nan, np.nan
    xv, wv = x[ok].astype(float), w[ok]
    m = np.average(xv, weights=wv)
    return m, np.average((xv - m) ** 2, weights=wv)


def std_diff(x: pd.Series, t: pd.Series, w: pd.Series | None = None) -> float:
    w = w if w is not None else pd.Series(1.0, index=x.index)
    mt, vt = wstats(x[t], w[t])
    mc, vc = wstats(x[~t], w[~t])
    den = np.sqrt((vt + vc) / 2)
    return (mt - mc) / den if den and not np.isnan(den) else np.nan


def comparison(d: pd.DataFrame, variables: list[tuple[str, str]]) -> list[dict]:
    """Means, medians and standardised differences, raw and after two reweightings:
    type x filing-year bin x neighbourhood, and type x filing-year bin x unit bin."""
    t = d.a
    w1, s1 = reweight(d, ["permit_type", "ybin", "neighborhoods_analysis_boundaries"])
    w2, s2 = reweight(d, ["permit_type", "ybin", "ubin"])
    out = []
    for col, lab in variables:
        x = d[col].astype(float)
        out.append({"var": lab, "mean_a": x[t].mean(), "med_a": x[t].median(),
                    "mean_b": x[~t].mean(), "med_b": x[~t].median(),
                    "n_a": int(x[t].notna().sum()), "n_b": int(x[~t].notna().sum()),
                    "sd_raw": std_diff(x, t), "sd_cell": std_diff(x, t, w1),
                    "sd_units": std_diff(x, t, w2),
                    "mean_b_cell": wstats(x[~t], w1[~t])[0]})
    return out, s1, s2


# ── 5b. the landmark-anchored comparison ─────────────────────────────────────
LANDMARK_SEEDS = (20260911, 1, 2, 3, 4)       # the first is reported; the rest bound it
LANDMARK_CELL = ["permit_type", "ybin", "neighborhoods_analysis_boundaries"]


def wmedian(x: pd.Series, w: pd.Series) -> float:
    """The weighted median: the smallest value at which the cumulative weight reaches half."""
    ok = x.notna() & (w > 0)
    if not ok.any():
        return np.nan
    xv, wv = x[ok].astype(float).values, w[ok].values
    o = np.argsort(xv)
    cw = np.cumsum(wv[o])
    return float(xv[o][np.searchsorted(cw, cw[-1] / 2)])


def alive_at(d: pd.DataFrame, L: pd.Series) -> pd.Series:
    """Filed on or before the landmark and not yet issued, completed or exited by it."""
    exit_day = d.status_date.where(d.status.isin(EXITS))
    return (d.filed_date.le(L) & ~d.issued_date.le(L) & ~d.completed_date.le(L)
            & ~exit_day.le(L) & L.notna())


def landmark(comp: pd.DataFrame, lk: pd.DataFrame, seed: int = LANDMARK_SEEDS[0]) -> dict:
    """Immortal time. A permit is Commission-linked through a hearing, and a permit linked
    through a hearing held while it was pending had to survive to that hearing and could not
    issue before it --- so part of its longer filed-to-issued time and of its lower
    abandonment is mechanical. The landmark version measures from the hearing forward, on
    permits alive at it.

    Linked permits: the landmark is the first linked hearing on or after the filing date; a
    permit filed after all of its hearings has no such landmark and is counted apart (its
    hearing did not condition its survival). Unlinked permits have no hearing, so each is given
    one: a lag drawn at random from the filing-to-hearing lags of the linked permits in its
    own cell (type x filing bin x neighbourhood), added to its filing date. Both groups are
    then restricted to permits alive at their landmark, and the unlinked group is reweighted
    to the linked group's cells, as in the within-cell comparison."""
    rng = np.random.default_rng(seed)
    # the within-cell column's median, on the same weights as its mean
    w0, _ = reweight(comp, LANDMARK_CELL)
    cell_med_b = wmedian(comp.t_issue_pos[~comp.a], w0[~comp.a])
    hear = (lk[lk.tier.isin(["t1", "t2"])].dropna(subset=["meeting_date"])
            .groupby("stem").meeting_date.apply(lambda x: sorted(set(x))))
    d = comp.copy()
    lin = d[d.a]
    L = pd.Series(pd.NaT, index=d.index, dtype="datetime64[ns]")
    after = pd.Series(False, index=d.index)
    for i, st, f in zip(lin.index, lin.stem, lin.filed_date):
        hs = hear.get(st, [])
        nxt = [h for h in hs if pd.notna(f) and h >= f]
        if nxt:
            L[i] = nxt[0]
        elif hs and pd.notna(f):
            after[i] = True
    d["L"] = L
    d["cellk"] = cell_key(d, LANDMARK_CELL)
    lag = (d.L - d.filed_date).dt.days
    lags = {k: g.values for k, g in lag[d.a & lag.notna()].groupby(d.cellk[d.a & lag.notna()])}
    un = d.index[~d.a & d.filed_date.notna()]
    draw = [rng.choice(lags[k]) if k in lags else np.nan for k in d.loc[un, "cellk"]]
    d.loc[un, "L"] = d.loc[un, "filed_date"] + pd.to_timedelta(pd.Series(draw, index=un), unit="D")
    d["alive"] = alive_at(d, d.L)
    g = d[d.alive].copy()
    g["t_from_L"] = (g.issued_date - g.L).dt.days
    g["issued_L"] = g.issued_date.notna().astype(float)
    g["exited_L"] = g.status.isin(EXITS).astype(float)
    w, sup = reweight(g, LANDMARK_CELL)
    t = g.a
    x = g.t_from_L.where(g.issued_L.eq(1))
    out = {"n_linked": int(d.a.sum()), "linked_with_landmark": int((d.a & d.L.notna()).sum()),
           "linked_filed_after": int(after.sum()),
           "linked_no_hearing_date": int((d.a & d.L.isna() & ~after).sum()),
           "linked_alive": int(t.sum()), "unlinked_given": int(np.isfinite(np.array(draw, float)).sum()),
           "unlinked_alive": int((~t).sum()), "support": sup,
           "lag_median": float(lag[d.a].median()),
           "days_med_a": float(x[t].median()), "days_med_b": wmedian(x[~t], w[~t]),
           "cell_days_med_b": cell_med_b,
           "days_mean_a": wstats(x[t], w[t])[0], "days_mean_b_cell": wstats(x[~t], w[~t])[0],
           "sd_days": std_diff(x, t, w), "sd_days_raw": std_diff(x, t),
           "issued_a": wstats(g.issued_L[t], w[t])[0], "issued_b": wstats(g.issued_L[~t], w[~t])[0],
           "exit_a": wstats(g.exited_L[t], w[t])[0], "exit_b": wstats(g.exited_L[~t], w[~t])[0],
           "sd_exit": std_diff(g.exited_L, t, w), "sd_issued": std_diff(g.issued_L, t, w)}
    return out


def landmark_all(comp: pd.DataFrame, lk: pd.DataFrame) -> dict:
    runs = [landmark(comp, lk, s_) for s_ in LANDMARK_SEEDS]
    main = runs[0]
    for k in ("sd_days", "sd_exit", "sd_issued"):
        v = [r[k] for r in runs]
        main[k + "_range"] = (float(np.nanmin(v)), float(np.nanmax(v)))
    return main


# ── 6. three targeted comparisons ────────────────────────────────────────────
def dr_track(p: pd.DataFrame, rec: pd.DataFrame) -> dict:
    """The records carry no §311 notification record, so the population of noticed permits
    cannot be drawn. The proxy: permits a DR request named --- a DRP record names its permit
    only in its description, and reliably only in the years where most descriptions carry a
    number --- against the other major permits (new construction, alteration, demolition)
    filed over the same years in residential districts, where §311 notice applies. A first
    version compared against permits named by PRL review records; those are overwhelmingly
    over-the-counter permits, and the two sets barely met (three permits)."""
    import analyze_permits as ap
    npj = rec[rec.table.eq("non-project")]
    drp = npj[npj.record_type.eq("DRP")].copy()
    drp["nums"] = drp.description.fillna("").map(lambda t: set(ap.parse_permits(t)))
    drp["yr"] = drp.open_date.dt.year
    parse_rate = drp.groupby("yr").nums.apply(lambda s: s.map(bool).mean())
    good = sorted(int(y) for y, r in parse_rate.items() if r >= 0.8 and y >= 2012)
    drp_stems = {n for s in drp.loc[drp.yr.isin(good), "nums"] for n in s}
    named = p[p.stem.isin(drp_stems) & p.permit_type.isin(["1", "2", "3", "6"])]
    lo, hi = (int(named.fy.min()), int(named.fy.max())) if len(named) else (0, 0)
    g = p[p.permit_type.isin(["1", "2", "3", "6"]) & p.fy.between(lo, hi) &
          (p.stem.isin(drp_stems) | p.zclass.isin(RESIDENTIAL))].copy()
    g["a"] = g.stem.isin(drp_stems)
    return {"frame": g, "years": good, "filed": (lo, hi),
            "drp_n": int(drp.yr.isin(good).sum()),
            "drp_named": int(drp.loc[drp.yr.isin(good), "nums"].map(bool).sum()),
            "named_in_dbi": int(len(named)), "parse_rate": parse_rate}


RESIDENTIAL = {"RH (house)", "RM (mixed)", "RTO (transit)", "RC (res-commercial)"}


def cu_track(p: pd.DataFrame, lk: pd.DataFrame) -> pd.DataFrame:
    cu = set(lk.loc[lk.tier.isin(["t1", "t2"]) & lk.request_type.isin(
        ["conditional_use", "conditional_use_modification"]), "stem"])
    d = p[p.dev & (p.stem.isin(cu) | ~p.linked)].copy()
    d["a"] = d.stem.isin(cu)
    return d


def ministerial(p: pd.DataFrame, rec: pd.DataFrame, lk: pd.DataFrame) -> dict:
    """SB 35 projects are flagged in the Projects table; SB 423 has no field and is found only
    where a record's text names it. Their permits reach DBI through `building_permits`."""
    import acquire_external_data as ax
    pr = rec[rec.table.eq("project")]
    sb35 = pr[pr.sb35.eq("CHECKED")] if "sb35" in pr else pr.iloc[:0]
    txt = rec.description.fillna("") + " " + rec.project_name.fillna("")
    sb423 = rec[txt.str.contains(r"(?i)\bSB[\s\-]?423\b|senate bill 423", regex=True)]
    sb35_txt = rec[txt.str.contains(r"(?i)\bSB[\s\-]?35\b|senate bill 35\b", regex=True)]
    stems = {n for f in pd.concat([sb35, sb423]).building_permits for n in ax.permits_from(f)}
    com = set(lk.loc[lk.tier.isin(["t1", "t2"]) & lk.request_type.ne("discretionary_review"),
                     "stem"])
    d = p[p.dev & (p.fy >= 2018) & p.permit_type.isin(["1", "2"]) &
          (p.stem.isin(stems) | p.stem.isin(com))].copy()
    d = d[~(d.stem.isin(stems) & d.stem.isin(com))]
    d["a"] = d.stem.isin(stems)
    return {"frame": d, "sb35_projects": len(sb35), "sb423_records": len(sb423),
            "sb35_text_records": len(sb35_txt), "ministerial_permits": int(d.a.sum()),
            "commission_permits": int((~d.a).sum())}


# ── 7. bunching ──────────────────────────────────────────────────────────────
def tsf_residential_bands() -> list[int]:
    """The Transportation Sustainability Fee's residential tiers, read from the fee register
    rather than typed: the first unit count each §411A residential band applies to. A band
    printed "21-99 Units" starts at 21; one printed ">99 Units" starts at 100."""
    import acquire_external_data as ax
    fees = ax.parse_fee_registers()
    f = fees[fees.section.eq("411A") & fees.label.str.contains(r"(?i)\bunits?\b", na=False)]
    starts = set()
    for lab in f.label.unique():
        m = re.match(r"^\s*(\d[\d,]*)\s*[-–—]\s*(\d[\d,]*)\s*units?", lab, re.I)
        if m:
            starts.add(int(m.group(1).replace(",", "")))
            continue
        m = re.match(r"^\s*([>≥])\s*(\d[\d,]*)\s*units?", lab, re.I)
        if m:
            n = int(m.group(2).replace(",", ""))
            starts.add(n + 1 if m.group(1) == ">" else n)
    return sorted(starts)


# ── 8. the DR clock ──────────────────────────────────────────────────────────
def dr_clock(it: pd.DataFrame, lk: pd.DataFrame, p: pd.DataFrame, rec: pd.DataFrame) -> dict:
    import acquire_external_data as ax
    dr = it[it.request_type.eq("discretionary_review") & it.cn.ne("")]
    first = dr.groupby("cn").meeting_date.min()
    l = lk[lk.tier.isin(["t1", "t2"]) & lk.cn.isin(first.index)]
    filed = p.set_index("stem").filed_date
    l = l.assign(filed=l.stem.map(filed))
    per = l.groupby("cn").filed.min()
    dbi = (first.reindex(per.index) - per).dt.days.dropna()
    lag = ax.filing_lag(it, rec)
    rec_clock = lag[lag.request_type.eq("discretionary_review")].set_index("cn").lag.dropna()
    both = pd.concat([dbi.rename("dbi"), rec_clock.rename("rec")], axis=1).dropna()
    return {"dbi": dbi, "rec": rec_clock, "both": both, "dr_cases": int(first.size)}


def dr_negative_split(it: pd.DataFrame, lk: pd.DataFrame, p: pd.DataFrame, rec: pd.DataFrame,
                      dclk: dict) -> dict:
    """The DR cases whose linked permit was filed after the Planning record opened (a negative
    DBI-minus-Planning difference), sorted by why. A neighbour files a DR request against a
    pending permit, so the permit should come first. Three explanations, tested in order:

      * the Planning date is not the DR request's: the case number has no record of its own
        and the clock fell back to the earliest record sharing its stem, or the record it found
        is not a DR record (`record`);
      * the permit was reached only through a sibling or parent record sharing the stem --- a
        permit for another phase, parcel or revision of the project (`stem`);
      * the permit is the one the minutes print or the DR record itself names, and it really
        was filed after the request opened (`own`).

    Each case also says whether the permit sits on the item's parcel."""
    import acquire_external_data as ax
    both = dclk["both"]
    neg = both[(both.dbi - both.rec) < 0].index
    rec = rec.copy()
    rec["nums"] = rec.building_permits.map(ax.permits_from)
    own_nums = {c: set(n) for c, n in zip(rec.cn, rec.nums) if n}
    rtype = rec.dropna(subset=["open_date"]).sort_values("open_date").drop_duplicates("cn") \
        .set_index("cn").record_type
    has_rec = set(rec.dropna(subset=["open_date"]).cn)
    first_filed = p.set_index("stem").filed_date
    parcel = p.set_index("stem").parcel
    items = it[it.cn.isin(neg)]
    pk = items.groupby("cn").pk.agg(lambda x: set().union(*[set(v) for v in x]))
    rows = []
    for cn in neg:
        l = lk[lk.cn.eq(cn) & lk.tier.isin(["t1", "t2"])].assign(f=lambda x: x.stem.map(first_filed))
        if not len(l):
            continue
        st = l.sort_values("f").stem.iloc[0]          # the permit the DBI clock started from
        tiers = set(l[l.stem.eq(st)].tier)
        if cn not in has_rec or str(rtype.get(cn, "")) not in ("DRP", "DRM"):
            why = "record"
        elif "t1" in tiers or st in own_nums.get(cn, set()):
            why = "own"
        else:
            why = "stem"
        on_parcel = parcel.get(st, "") in pk.get(cn, set())
        rows.append({"cn": cn, "why": why, "on_parcel": on_parcel,
                     "record_type": str(rtype.get(cn, "")) if cn in has_rec else "(none)",
                     "tier": "T1" if "t1" in tiers else "T2"})
    d = pd.DataFrame(rows)
    return {"frame": d, "n_both": int(len(both)), "n_neg": int(len(neg))}


# ── the report ───────────────────────────────────────────────────────────────
def describe(s: pd.Series) -> dict:
    s = s.dropna()
    return {"n": len(s), "p25": s.quantile(.25), "med": s.median(), "p75": s.quantile(.75),
            "p90": s.quantile(.9), "neg": (s < 0).mean() if len(s) else np.nan}


def load_permits(inventory: bool = True) -> dict:
    """The permit table every stage reads: typed, one row per permit, with its clock, the
    development-relevant flag, the linkage tiers and the cost placeholders dropped. `report`
    also wants the field inventory, which needs the raw rows; `clocks` does not."""
    meta = json.loads(FULL.with_name("dbi_permits_full_meta.json").read_text())
    retrieved = pd.Timestamp(meta["retrieved"][:10])
    raw = pd.read_csv(FULL, dtype=str, low_memory=False)
    inv, inv_meta = field_inventory(raw, retrieved) if inventory else (None, None)
    d = typed(raw)
    del raw
    p, col = collapse(d)
    p = clocks(p)
    rules = universe(p)
    p["dev"] = rules.any(axis=1) & p.permit_type.ne("8")
    cache = FULL.with_name("_permit_content_linkage.pkl")
    if cache.exists() and cache.stat().st_mtime > FULL.stat().st_mtime:
        L = pd.read_pickle(cache)
    else:                           # the tiers take minutes; cache them beside the DBI file
        L = linkage(d, p)
        pd.to_pickle(L, cache)
    it, lk = L["items"], L["links"]
    p = tier_flags(p, lk)
    return {"meta": meta, "retrieved": retrieved, "inv": inv, "inv_meta": inv_meta, "d": d,
            "p": p, "col": col, "rules": rules, "it": it, "lk": lk}


def report():
    FIG.mkdir(parents=True, exist_ok=True)
    TAB.mkdir(parents=True, exist_ok=True)
    B = load_permits()
    meta, inv, inv_meta, d, p, col, rules, it, lk = (B[k] for k in (
        "meta", "inv", "inv_meta", "d", "p", "col", "rules", "it", "lk"))
    ct = clock_table(p)
    sens = {}
    for q in COST_Q_ALT:
        r_ = universe(p, q=q)
        sens[f"top {100*(1-q):.0f}\\%"] = r_.any(axis=1) & p.permit_type.ne("8")
    r_ = universe(p, nominal=COST_NOMINAL)
    sens[f"nominal \\${COST_NOMINAL/1e6:.0f}M"] = r_.any(axis=1) & p.permit_type.ne("8")
    p["net_units"] = p.proposed_units - p.existing_units
    p["ubin"] = p.proposed_units.map(unit_bin)
    # A cost of $1 or less is a placeholder (10% of filled rows), not a cost: it is dropped from
    # every cost comparison rather than logged as ~0, which pulled both groups' log means down
    # by different amounts.
    p["cost_placeholder"] = p.estimated_cost.le(1)
    p["estimated_cost"] = p.estimated_cost.where(~p.cost_placeholder)
    p["revised_cost"] = p.revised_cost.where(p.revised_cost.gt(1))
    p["log_cost"] = log_cost(p.estimated_cost)
    p["log_rcost"] = log_cost(p.revised_cost)

    dev = p[p.dev].copy()
    dev["zoning"] = zoning_at_filing(dev)
    dev["zclass"] = dev.zoning.map(zoning_class)
    comp_base = dev[dev.group.isin(["Commission-linked", "not linked"])].copy()
    comp_base["a"] = comp_base.group.eq("Commission-linked")
    for u in ("1 family dwelling", "2 family dwelling", "apartments", "office", "retail sales"):
        comp_base[f"ex_{u}"] = comp_base.existing_use.fillna("").str.lower().eq(u).astype(float)
        comp_base[f"pr_{u}"] = comp_base.proposed_use.fillna("").str.lower().eq(u).astype(float)
    for z in sorted(set(comp_base.zclass) - {""}):
        comp_base[f"z_{z}"] = comp_base.zclass.eq(z).astype(float)
    comp_base["ct_wood"] = comp_base.proposed_construction_type_description.fillna("").str.contains(
        "wood").astype(float)
    comp_base["ct_1"] = comp_base.proposed_construction_type_description.fillna("").eq(
        "constr type 1").astype(float)
    for c in ("issued", "completed", "exited"):
        comp_base[c] = comp_base[c].astype(float)
    comp_base["t_issue_pos"] = comp_base.t_issue.where(comp_base.t_issue >= 0)
    VARS = [("log_cost", "estimated cost (log)"), ("estimated_cost", "estimated cost ($)"),
            ("log_rcost", "revised cost (log)"), ("existing_units", "existing units"),
            ("proposed_units", "proposed units"), ("net_units", "net units"),
            ("number_of_existing_stories", "existing stories"),
            ("number_of_proposed_stories", "proposed stories"), ("fy", "filing year"),
            ("ex_1 family dwelling", "existing use: one-family dwelling"),
            ("ex_apartments", "existing use: apartments"),
            ("pr_1 family dwelling", "proposed use: one-family dwelling"),
            ("pr_apartments", "proposed use: apartments"),
            ("pr_office", "proposed use: office"), ("pr_retail sales", "proposed use: retail"),
            ("ct_wood", "construction: wood frame"), ("ct_1", "construction: type 1"),
            *[(f"z_{z}", f"zoning at filing: {z}") for z in sorted(set(comp_base.zclass) - {""})
              if comp_base[f"z_{z}"].mean() > 0.02],
            ("issued", "outcome: issued"), ("t_issue_pos", "outcome: days filed to issued"),
            ("completed", "outcome: completed"), ("exited", "outcome: expired or withdrawn")]
    cmp, sup1, sup2 = comparison(comp_base, VARS)
    lmk = landmark_all(comp_base, lk)
    # the same comparison within unit-count bins, for the scale-sensitive rows
    by_units = []
    for lo, hi, lab in UNIT_BINS[1:]:
        g = comp_base[comp_base.ubin.eq(lab)]
        if g.a.sum() < 20:
            continue
        w, s_ = reweight(g, ["permit_type", "ybin", "neighborhoods_analysis_boundaries"])
        row = {"bin": lab, "n_a": int(g.a.sum()), "n_b": int((~g.a).sum()), "support": s_}
        for c in ("issued", "t_issue_pos", "completed", "exited", "log_cost"):
            row[c] = (wstats(g.loc[g.a, c].astype(float), w[g.a])[0],
                      wstats(g.loc[~g.a, c].astype(float), w[~g.a])[0])
        by_units.append(row)

    rec = __import__("acquire_external_data").build_records()
    major = p[p.permit_type.isin(["1", "2", "3", "6"]) & p.fy.between(2005, 2026)].copy()
    major["zoning"] = zoning_at_filing(major)
    major["zclass"] = major.zoning.map(zoning_class)
    drt = dr_track(major, rec)
    drc = comparison_small(drt["frame"], ["permit_type", "fy", "neighborhoods_analysis_boundaries"])
    cut = cu_track(p.assign(dev=p.dev), lk)
    cut = cut.merge(dev[["stem", "zoning"]], on="stem", how="left")
    cuc = cell_outcomes(cut, ["proposed_use", "zoning", "ybin"])
    mn = ministerial(p, rec, lk)
    mnc = cell_outcomes(mn["frame"], ["ubin"])
    tsf = tsf_residential_bands()
    dclk = dr_clock(it, lk, p, rec)
    drneg = dr_negative_split(it, lk, p, rec, dclk)

    ctx = dict(inv=inv, inv_meta=inv_meta, col=col, ct=ct, rules=rules, p=p, dev=dev,
               sens=sens, lk=lk, it=it, cmp=cmp, sup1=sup1, sup2=sup2, by_units=by_units,
               drt=drt, drc=drc, cut=cut, cuc=cuc, mn=mn, mnc=mnc, tsf=tsf, dclk=dclk,
               comp_base=comp_base, meta=meta, lmk=lmk, drneg=drneg)
    figures(ctx)
    write_tables(ctx)
    write_macros(ctx)
    print(f"permits {len(p):,}; dev universe {int(p.dev.sum()):,}; linked "
          f"{int(dev.linked.sum()):,}; T3 only {int(dev.t3_only.sum()):,} → {TAB}")


def log_cost(x: pd.Series) -> pd.Series:
    """log(1 + cost) for a real cost; a placeholder of $1 or less is missing, not zero."""
    return np.log1p(x.where(x.gt(1)))


def comparison_small(g: pd.DataFrame, cell: list[str] | None = None) -> dict:
    """Outcomes and scale for a two-group comparison, raw and within the cells given
    (default type x year bin)."""
    g = g.copy()
    g["log_cost"] = log_cost(g.estimated_cost)
    g["t_issue_pos"] = g.t_issue.where(g.t_issue >= 0)
    for c in ("issued", "completed", "exited"):
        g[c] = g[c].astype(float)
    w, s_ = reweight(g, cell or ["permit_type", "ybin"])
    out = {"n_a": int(g.a.sum()), "n_b": int((~g.a).sum()), "support": s_}
    for c in ("log_cost", "proposed_units", "issued", "t_issue_pos", "completed", "exited"):
        x = g[c].astype(float)
        out[c] = (x[g.a].mean(), x[~g.a].mean(), wstats(x[~g.a], w[~g.a])[0],
                  std_diff(x, g.a), std_diff(x, g.a, w))
    return out


def cell_outcomes(g: pd.DataFrame, cell: list[str]) -> dict:
    g = g.copy()
    for c in cell:
        g[c] = g[c].fillna("(none)") if g[c].dtype == object else g[c]
    if "ubin" in cell and "ubin" not in g:
        g["ubin"] = g.proposed_units.map(unit_bin)
    g["log_cost"] = log_cost(g.estimated_cost)
    g["t_issue_pos"] = g.t_issue.where(g.t_issue >= 0)
    for c in ("issued", "completed", "exited"):
        g[c] = g[c].astype(float)
    w, s_ = reweight(g, cell)
    out = {"n_a": int(g.a.sum()), "n_b": int((~g.a).sum()), "support": s_,
           "cells": int(cell_key(g[g.a], cell).nunique())}
    for c in ("log_cost", "proposed_units", "issued", "t_issue_pos", "completed", "exited"):
        x = g[c].astype(float)
        out[c] = (wstats(x[g.a], w[g.a])[0], x[~g.a].mean(), wstats(x[~g.a], w[~g.a])[0],
                  std_diff(x, g.a), std_diff(x, g.a, w), x[g.a].median(),
                  x[~g.a].median())
    return out


# ── figures ──────────────────────────────────────────────────────────────────
SMOOTH_YEARS = 3
MIN_YEAR_N = 50


def figures(ctx):
    p = ctx["p"]
    fig, axes = plt.subplots(2, 1, figsize=(7.2, 5.6), sharex=True)
    groups = [("new construction (1, 2)", p.permit_type.isin(["1", "2"]), "#a33"),
              ("additions and alterations (3)", p.permit_type.eq("3"), "#5b7fa6"),
              ("demolition (6)", p.permit_type.eq("6"), "#b07d2b"),
              ("over-the-counter (8)", p.permit_type.eq("8"), "#2f6f4f")]
    for lab, m, c in groups:
        g = p[m & p.fy.between(1980, 2026)]
        n = g.groupby("fy").size()
        med = g[g.t_issue >= 0].groupby("fy").t_issue.median().where(n >= MIN_YEAR_N)
        ex = g.groupby("fy").exited.mean().where(n >= MIN_YEAR_N) * 100
        # Over-the-counter permits issue the day they are filed (median 0), which a log
        # axis cannot draw; they appear in the exit panel only.
        panels = ((axes[1], ex),) if lab.startswith("over-the-counter") else \
            ((axes[0], med), (axes[1], ex))
        for ax, s in panels:
            ax.plot(s.index, s.values, color=c, alpha=0.25, lw=0.9)
            ax.plot(s.index, s.rolling(SMOOTH_YEARS, center=True, min_periods=1).mean(),
                    color=c, lw=2, label=lab)
    axes[0].set_yscale("log")
    axes[0].set_ylabel("median days, filed → issued\n(issued permits, log scale)")
    axes[0].set_title("The permit clock by type and filing year\n"
                      f"({SMOOTH_YEARS}-year centred mean over the faint annual series; years "
                      f"with fewer than {MIN_YEAR_N} permits of a type not drawn)", loc="left")
    axes[0].legend(frameon=False, fontsize=7.5, ncol=3)
    axes[1].legend(frameon=False, fontsize=7.5, ncol=2, loc="upper right")
    axes[1].set_ylabel("% expired, withdrawn,\ncancelled or disapproved")
    axes[1].set_xlabel("filing year (recent years are right-censored: a permit filed last "
                       "year has not had time to expire)")
    fig.savefig(FIG / "fig_permit_clock.pdf")
    plt.close(fig)

    dev = ctx["dev"]
    fig, axes = plt.subplots(2, 1, figsize=(7.2, 5.6), sharex=True)
    ks = np.arange(1, 41)
    for ax, (lab, m) in zip(axes, ((f"filed before {PROP_C_YEAR}", dev.fy < PROP_C_YEAR),
                                   (f"filed {PROP_C_YEAR} or later", dev.fy >= PROP_C_YEAR))):
        g = dev[m & dev.proposed_units.between(1, 40)]
        allc = g.proposed_units.round().value_counts().reindex(ks, fill_value=0)
        lnk = g[g.linked].proposed_units.round().value_counts().reindex(ks, fill_value=0)
        ax.bar(ks - 0.2, 100 * allc / max(allc.sum(), 1), width=0.4, color="#5b7fa6",
               label=f"all development-relevant (n = {int(allc.sum()):,})")
        ax.bar(ks + 0.2, 100 * lnk / max(lnk.sum(), 1), width=0.4, color="#a33",
               label=f"Commission-linked (n = {int(lnk.sum()):,})")
        ax.set_yscale("log")
        marks = [(INCLUSIONARY_UNITS, "inclusionary (§415)", "-")]
        marks += [(t, "TSF residential tier", "--") for t in ctx["tsf"] if 1 < t <= 40]
        marks += [(ORDINANCE_2026_UNITS, "2026 ordinance (per brief)", ":")]
        for x, name, ls in marks:
            ax.axvline(x - 0.5, color="#555", lw=0.9, ls=ls)
            ax.text(x - 0.4, ax.get_ylim()[1] * 0.6, name, fontsize=6.5, rotation=90,
                    va="top", color="#555")
        ax.set_ylabel("% of permits, log scale")
        ax.set_title(lab, loc="left", fontsize=9)
        ax.legend(frameon=False, fontsize=7, loc="upper right")
    axes[1].set_xlabel("proposed dwelling units on the permit (a line sits just below the "
                       "first count a rule applies to)")
    fig.savefig(FIG / "fig_bunching.pdf")
    plt.close(fig)

    dc = ctx["dclk"]
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    bins = np.arange(-400, 1501, 50)
    for s, lab, c in ((dc["dbi"], "DBI permit filed → first DR hearing", "#a33"),
                      (dc["rec"], "Planning record opened → first DR hearing", "#5b7fa6")):
        ax.hist(s.clip(-400, 1500), bins=bins, histtype="step", lw=1.6, color=c,
                label=f"{lab} (n = {len(s):,}, median {s.median():.0f} days)")
        ax.axvline(s.median(), color=c, lw=1, ls="--")
    ax.set_xlabel("days (display clipped to −400 … 1,500; medians use every observation)")
    ax.set_ylabel("DR cases")
    ax.set_title("Two clocks for a discretionary review", loc="left")
    ax.legend(frameon=False, fontsize=7.5, loc="upper right")
    fig.savefig(FIG / "fig_dr_clock.pdf")
    plt.close(fig)


# ── tables ───────────────────────────────────────────────────────────────────
def f0(x):
    return "---" if x is None or pd.isna(x) else f"{x:,.0f}".replace(",", "{,}")


def f2(x):
    return "---" if x is None or pd.isna(x) else f"{x:.2f}"


def fmt_val(var: str, x):
    if x is None or pd.isna(x):
        return "---"
    if var.startswith(("outcome: issued", "outcome: completed", "outcome: expired",
                       "outcome: construction", "existing use", "proposed use",
                       "construction", "zoning")):
        return f"{100*x:.1f}\\%"
    if "(log)" in var:
        return f"{x:.2f}"
    if var == "filing year":
        return f"{x:.0f}"
    if abs(x) >= 1000:
        return f0(x)
    return f"{x:.1f}"


def write_tables(ctx):
    L = ["% GENERATED BY analyze_permit_content.py --- do not edit by hand."]
    a = L.append
    inv, p, dev = ctx["inv"], ctx["p"], ctx["dev"]
    labs = [lab for *_, lab in DECADES]

    a(r"{\small\setlength{\tabcolsep}{3.5pt}")
    a(r"\begin{longtable}{@{}L{3.9cm}lrrrrrrrr@{}}")
    a(r"\caption{Every column of the Building Permits table as it is actually filled: the "
      r"share of rows with a value, overall and by the decade the permit was filed, and the "
      r"number of distinct values. `Kind' is inferred from the values, not the "
      r"documentation.}\label{tab:fields}\\\toprule")
    hdr = (r"Field & Kind & All & $<$1980 & 1980s & 1990s & 2000s & 2010s & 2020s & "
           r"Distinct\\\midrule")
    a(hdr + r"\endfirsthead")
    a(r"\toprule " + hdr + r"\endhead")
    # iterrows, not itertuples: `dec_before 1980` has a space, and itertuples renames such a
    # column to a positional `_3` --- which silently blanked a table in the acquisition memo.
    for _, r in inv.iterrows():
        dec = " & ".join(P(r[f"dec_{lab}"], 0) for lab in labs)
        a(rf"{TF(r['field'])} & {r['kind']} & {P(r['populated'], 1)} & {dec} & "
          rf"{f0(r['distinct'])}\\")
    a(r"\bottomrule\end{longtable}}")
    a("")

    a(r"{\small")
    a(r"\begin{longtable}{@{}L{3.6cm}L{5.4cm}L{5.3cm}@{}}")
    a(r"\caption{The three most frequent values of every column, and where the rows do not "
      r"deliver what the column's name promises, the measurement that shows "
      r"it.}\label{tab:fieldnotes}\\\toprule")
    a(r"Field & Most frequent values & What the rows deliver\\\midrule\endfirsthead")
    a(r"\toprule Field & Most frequent values & What the rows deliver\\\midrule\endhead")
    for _, r in inv.iterrows():
        a(rf"{TF(r['field'])} & {T(r['examples'])} & {r['note']}\\")
    a(r"\bottomrule\end{longtable}}")
    a("")

    tab = pd.crosstab(p.permit_type.map(TYPE_SHORT).fillna("(blank)"), p.decade)
    tab = tab.reindex(columns=[l for l in labs + ["no filed date"] if l in tab.columns])
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Permits by type and filing decade, one row per permit number (the "
      r"earliest-filed row of each).}\label{tab:types}")
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{l" + "r" * (len(tab.columns) + 1) + r"}\toprule")
    a(r"Permit type & " + " & ".join(T(c) for c in tab.columns) + r" & All\\\midrule")
    for idx, row in tab.iterrows():
        a(rf"{T(idx)} & " + " & ".join(f0(v) for v in row.values) + rf" & {f0(row.sum())}\\")
    a(r"\midrule All & " + " & ".join(f0(v) for v in tab.sum().values) +
      rf" & {f0(tab.values.sum())}\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")

    st = p.status.replace("", "(blank)").value_counts()
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Status, one row per permit. The exits --- expired, withdrawn, cancelled, "
      r"disapproved, revoked, suspended --- are the trace of a project that did not "
      r"proceed.}\label{tab:status}")
    a(r"\begin{tabular}{lrr}\toprule")
    a(r"Status & Permits & Share\\\midrule")
    for k, v in st.items():
        a(rf"{T(k)}{' $\\dagger$' if k in EXITS else ''} & {f0(v)} & {100*v/len(p):.1f}\%\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The permit clock by type and filing decade: the share reaching each stage, "
      r"and the median days between stages among permits that reached both. Negative "
      r"intervals are excluded from the medians and counted in the text. Recent decades are "
      r"right-censored.}\label{tab:clock}")
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{llrrrrrrr}\toprule")
    a(r"Type & Filed & Permits & Issued & \multicolumn{2}{c}{Filed $\to$ issued} & "
      r"Completed & Issued $\to$ done & Exited\\")
    a(r"\cmidrule(lr){5-6} & & & & median & p90 & & median & \\\midrule")
    last = None
    for r in ctx["ct"]:
        lab = "" if r["type"] == last else T(r["type"])
        last = r["type"]
        a(rf"{lab} & {r['decade']} & {f0(r['n'])} & {P(r['issued'])}\% & {f0(r['issue_med'])} & "
          rf"{f0(r['issue_p90'])} & {P(r['completed'])}\% & {f0(r['done_med'])} & "
          rf"{P(r['exited'])}\%\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")

    rules = ctx["rules"][p.permit_type.ne("8")]
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The development-relevant universe, rule by rule, over the %s permits that are "
      r"not over-the-counter alterations. `Alone' is what the rule keeps by itself; "
      r"`added' is what it adds to the rules above it. The cost rule is the top %d\%% of "
      r"additions-and-alterations estimated cost within the filing year; the sensitivity "
      r"rows swap it for the alternatives.}\label{tab:universe}"
      % (N(int(p.permit_type.ne("8").sum())), round(100 * (1 - COST_Q))))
    a(r"\begin{tabular}{lrr}\toprule")
    a(r"Rule & Alone & Added\\\midrule")
    seen = pd.Series(False, index=rules.index)
    for c in rules.columns:
        a(rf"{T(c)} & {f0(rules[c].sum())} & {f0((rules[c] & ~seen).sum())}\\")
        seen |= rules[c]
    a(rf"\midrule Universe & & \textbf{{{f0(seen.sum())}}}\\")
    a(rf"\quad of which Commission-linked (T1 or T2) & & {f0(dev.linked.sum())}\\")
    a(rf"\quad of which T3 only & & {f0(dev.t3_only.sum())}\\")
    a(rf"Over-the-counter permits excluded & {f0(p.permit_type.eq('8').sum())} & \\")
    a(r"\midrule \multicolumn{3}{l}{\emph{Sensitivity: the cost rule replaced by}}\\")
    for lab, m in ctx["sens"].items():
        lk_share = p.loc[m, "linked"].mean()
        a(rf"\quad {lab} & {f0(m.sum())} & {100*lk_share:.1f}\% linked\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    lk = ctx["lk"]
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The linkage tiers. Items is item-table rows that reach at least one permit "
      r"by the tier; permits is distinct DBI permits reached, all and within the "
      r"development-relevant universe. T3's precision was measured on discretionary "
      r"reviews, where the printed number is ground truth, and is unmeasured "
      r"elsewhere.}\label{tab:tiers}")
    a(r"\begin{tabular}{lrrr}\toprule")
    a(r"Tier & Items & Permits & In the universe\\\midrule")
    for tier, lab in (("t1", "T1: permit number printed in the minutes"),
                      ("t2", "T2: Planning records' \\texttt{building\\_permits}"),
                      ("t3", "T3: parcel $+$ type $+$ window")):
        s = set(lk.loc[lk.tier.eq(tier), "stem"])
        a(rf"{lab} & {f0(lk.loc[lk.tier.eq(tier), 'item_id'].nunique())} & {f0(len(s))} & "
          rf"{f0(dev.stem.isin(s).sum())}\\")
    s1, s2 = set(lk.loc[lk.tier.eq("t1"), "stem"]), set(lk.loc[lk.tier.eq("t2"), "stem"])
    a(rf"\midrule T1 and T2 both & & {f0(len(s1 & s2))} & {f0(dev.stem.isin(s1 & s2).sum())}\\")
    a(rf"Commission-linked (T1 or T2) & & {f0(len(s1 | s2))} & {f0(dev.linked.sum())}\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    a(r"{\small")
    a(r"\begin{longtable}{@{}L{4.5cm}rrrrrrr@{}}")
    a(r"\caption{Commission-linked permits against development-relevant permits no tier "
      r"reaches. Standardised difference is the difference in means over the pooled "
      r"standard deviation; `cells' reweights the unlinked group to the linked group's "
      r"distribution over permit type $\times$ %d-year filing bin $\times$ analysis "
      r"neighbourhood (%.1f\%% of linked permits have a counterpart cell), `units' over type "
      r"$\times$ bin $\times$ unit-count bin (%.1f\%%). T3-only permits are in neither "
      r"group.}\label{tab:compare}\\\toprule"
      % (YEAR_BIN, 100 * ctx["sup1"], 100 * ctx["sup2"]))
    hdr = (r" & \multicolumn{2}{c}{Linked} & \multicolumn{2}{c}{Not linked} & "
           r"\multicolumn{3}{c}{Std.\ difference}\\ \cmidrule(lr){2-3}\cmidrule(lr){4-5}"
           r"\cmidrule(lr){6-8} Variable & mean & median & mean & median & raw & cells & "
           r"units\\\midrule")
    a(hdr + r"\endfirsthead")
    a(r"\toprule" + hdr + r"\endhead")
    for r in ctx["cmp"]:
        a(rf"{T(r['var'])} & {fmt_val(r['var'], r['mean_a'])} & "
          rf"{fmt_val(r['var'], r['med_a']) if not r['var'].startswith(('outcome: i', 'outcome: c', 'outcome: e', 'existing use', 'proposed use', 'construction', 'zoning')) else ''} & "
          rf"{fmt_val(r['var'], r['mean_b'])} & "
          rf"{fmt_val(r['var'], r['med_b']) if not r['var'].startswith(('outcome: i', 'outcome: c', 'outcome: e', 'existing use', 'proposed use', 'construction', 'zoning')) else ''} & "
          rf"{f2(r['sd_raw'])} & {f2(r['sd_cell'])} & {f2(r['sd_units'])}\\")
    a(r"\bottomrule\end{longtable}}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Outcomes within unit-count bins: linked against unlinked permits with the "
      r"same number of proposed units, the unlinked group reweighted to the linked group's "
      r"type $\times$ filing bin $\times$ neighbourhood distribution. Bins with fewer than "
      r"twenty linked permits are omitted.}\label{tab:byunits}")
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{lrrrrrrrrrr}\toprule")
    a(r"Proposed units & Linked & Unlinked & Support & \multicolumn{2}{c}{Issued} & "
      r"\multicolumn{2}{c}{Days to issue} & \multicolumn{2}{c}{Expired or withdrawn}\\")
    a(r"\cmidrule(lr){5-6}\cmidrule(lr){7-8}\cmidrule(lr){9-10} & & & & linked & unlinked & "
      r"linked & unlinked & linked & unlinked\\\midrule")
    for r in ctx["by_units"]:
        a(rf"{r['bin']} & {f0(r['n_a'])} & {f0(r['n_b'])} & {P(r['support'])}\% & "
          rf"{P(r['issued'][0])}\% & {P(r['issued'][1])}\% & {f0(r['t_issue_pos'][0])} & "
          rf"{f0(r['t_issue_pos'][1])} & {P(r['exited'][0])}\% & {P(r['exited'][1])}\%\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")

    lm, cm = ctx["lmk"], {r["var"]: r for r in ctx["cmp"]}
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Immortal time. `Raw' and `within cells' are Table~\ref{tab:compare}'s rows. "
      r"`Landmark' keeps only permits alive at their hearing --- for a linked permit the first "
      r"linked hearing on or after its filing; for an unlinked permit a date its filing plus a "
      r"lag drawn from the linked permits' filing-to-hearing lags in the same cell --- and "
      r"measures from that date: days from the hearing to issuance, and the share issued or "
      r"expired or withdrawn afterwards. Unlinked means and medians are reweighted to the linked cells "
      r"(type $\times$ %d-year filing bin $\times$ neighbourhood; %s\%% of the linked landmark "
      r"sample has a counterpart). The standardised difference in brackets is its range over "
      r"%d draws of the lags.}\label{tab:landmark}" % (YEAR_BIN, P(lm["support"]), len(LANDMARK_SEEDS)))
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{lrrr}\toprule")
    a(r" & Raw & Within cells & Landmark, within cells\\\midrule")
    rng_ = lambda k: f" [{f2(lm[k + '_range'][0])}, {f2(lm[k + '_range'][1])}]"
    dd, ex, iss = cm["outcome: days filed to issued"], cm["outcome: expired or withdrawn"], \
        cm["outcome: issued"]
    a(r"\emph{Days to issuance (issued permits)} & filing $\to$ issued & filing $\to$ issued & "
      r"hearing $\to$ issued\\")
    a(rf"\quad linked, mean & {f0(dd['mean_a'])} & {f0(dd['mean_a'])} & {f0(lm['days_mean_a'])}\\")
    a(rf"\quad unlinked, mean & {f0(dd['mean_b'])} & {f0(dd['mean_b_cell'])} & "
      rf"{f0(lm['days_mean_b_cell'])}\\")
    a(rf"\quad linked, median & {f0(dd['med_a'])} & {f0(dd['med_a'])} & {f0(lm['days_med_a'])}\\")
    a(rf"\quad unlinked, median & {f0(dd['med_b'])} & {f0(lm['cell_days_med_b'])} & "
      rf"{f0(lm['days_med_b'])}\\")
    a(rf"\quad standardised difference & {f2(dd['sd_raw'])} & {f2(dd['sd_cell'])} & "
      rf"{f2(lm['sd_days'])}{rng_('sd_days')}\\")
    a(r"\emph{Expired or withdrawn} & & & after the hearing\\")
    a(rf"\quad linked & {P(ex['mean_a'])}\% & {P(ex['mean_a'])}\% & {P(lm['exit_a'])}\%\\")
    a(rf"\quad unlinked & {P(ex['mean_b'])}\% & {P(ex['mean_b_cell'])}\% & {P(lm['exit_b'])}\%\\")
    a(rf"\quad standardised difference & {f2(ex['sd_raw'])} & {f2(ex['sd_cell'])} & "
      rf"{f2(lm['sd_exit'])}{rng_('sd_exit')}\\")
    a(r"\emph{Issued} & & & after the hearing\\")
    a(rf"\quad linked & {P(iss['mean_a'])}\% & {P(iss['mean_a'])}\% & {P(lm['issued_a'])}\%\\")
    a(rf"\quad unlinked & {P(iss['mean_b'])}\% & {P(iss['mean_b_cell'])}\% & {P(lm['issued_b'])}\%\\")
    a(rf"\quad standardised difference & {f2(iss['sd_raw'])} & {f2(iss['sd_cell'])} & "
      rf"{f2(lm['sd_issued'])}{rng_('sd_issued')}\\")
    a(r"\midrule \emph{Samples} & & & \\")
    a(rf"\quad linked permits & {f0(lm['n_linked'])} & & {f0(lm['linked_alive'])}\\")
    a(rf"\quad unlinked permits & {f0(int((~ctx['comp_base'].a).sum()))} & & {f0(lm['unlinked_alive'])}\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")

    def targeted_rows(res, lab_a, lab_b):
        rows = []
        for c, lab in (("log_cost", "estimated cost (log)"),
                       ("proposed_units", "proposed units"), ("issued", "issued"),
                       ("t_issue_pos", "days filed to issued"), ("completed", "completed"),
                       ("exited", "expired or withdrawn")):
            v = res[c]
            fm = (lambda x: f"{100*x:.1f}\\%") if c in ("issued", "completed", "exited") \
                else (lambda x: f"{x:.2f}" if c == "log_cost" else f0(x))
            rows.append(rf"\quad {lab} & {fm(v[0]) if pd.notna(v[0]) else '---'} & "
                        rf"{fm(v[1]) if pd.notna(v[1]) else '---'} & "
                        rf"{fm(v[2]) if pd.notna(v[2]) else '---'} & {f2(v[3])} & "
                        rf"{f2(v[4])}\\")
        return rows

    drc, cuc, mnc = ctx["drc"], ctx["cuc"], ctx["mnc"]
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Three targeted comparisons. In each, the first group is the one named; the "
      r"second is reweighted to it over the cells shown (`support' is the share of the first "
      r"group with a counterpart cell). The DR comparison is a proxy: the Planning records "
      r"carry no \S311 notification record, so it compares the permits a DR request named "
      r"with the other major permits in residential districts, where notice "
      r"applies.}\label{tab:targeted}")
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{lrrrrr}\toprule")
    a(r" & First group & Second, raw & Second, reweighted & Std.\ diff.\ raw & "
      r"reweighted\\\midrule")
    a(rf"\multicolumn{{6}}{{l}}{{\emph{{DR track (proxy): major permits a DR request named vs "
      rf"other major permits in residential districts filed "
      rf"{ctx['drt']['filed'][0]}--{ctx['drt']['filed'][1]}; cells type $\times$ filing "
      rf"year $\times$ neighbourhood; {f0(drc['n_a'])} vs {f0(drc['n_b'])}, support "
      rf"{P(drc['support'])}\%}}}}\\")
    for row in targeted_rows(drc, "DR", "no DR"):
        a(row)
    a(rf"\multicolumn{{6}}{{l}}{{\emph{{CU track: CU-linked vs same proposed use and zoning "
      rf"district, no Commission link; cells use $\times$ zoning $\times$ filing bin; "
      rf"{f0(cuc['n_a'])} vs {f0(cuc['n_b'])}, support {P(cuc['support'])}\%}}}}\\")
    for row in targeted_rows(cuc, "CU", "no CU"):
        a(row)
    a(rf"\multicolumn{{6}}{{l}}{{\emph{{Ministerial vs Commission, new construction filed "
      rf"2018 on: SB 35 / SB 423 vs Commission-linked; cells unit bin; {f0(mnc['n_a'])} vs "
      rf"{f0(mnc['n_b'])}, support {P(mnc['support'])}\%}}}}\\")
    for row in targeted_rows(mnc, "SB", "Commission"):
        a(row)
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")

    ks = list(range(7, 13)) + list(range(18, 23)) + list(range(23, 28))
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Permits at the unit counts around the thresholds. Counts, not shares; the "
      r"figure gives the full distribution. The %d-unit line is the brief's figure for the "
      r"July 2026 ordinance and has not been checked against the ordinance here; permits "
      r"filed in 2026 are too few to show a response to it.}\label{tab:bunch}"
      % ORDINANCE_2026_UNITS)
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{ll" + "r" * len(ks) + r"}\toprule")
    a(r"Group & Filed & " + " & ".join(str(k) for k in ks) + r"\\\midrule")
    for glab, gm in (("all development-relevant", dev.proposed_units.notna()),
                     ("Commission-linked", dev.linked)):
        for plab, pm in ((f"$<${PROP_C_YEAR}", dev.fy < PROP_C_YEAR),
                         (f"$\\geq${PROP_C_YEAR}", dev.fy >= PROP_C_YEAR)):
            vc = dev[gm & pm].proposed_units.round().value_counts()
            a(rf"{glab} & {plab} & " + " & ".join(f0(vc.get(k, 0)) for k in ks) + r"\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")

    dc = ctx["dclk"]
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The developer's clock on the DR track. `DBI' runs from the filing of the "
      r"permit the DR is about (reached by T1 or T2) to the first DR hearing; `Planning "
      r"record' from the opening of the case's record, the clock the data-acquisition memo "
      r"used, to the same hearing. One row per DR case.}\label{tab:drclock}")
    a(r"\begin{tabular}{lrrrrrr}\toprule")
    a(r"Clock & Cases & p25 & Median & p75 & p90 & Negative\\\midrule")
    for lab, s in (("DBI permit filed $\\to$ first hearing", dc["dbi"]),
                   ("Planning record opened $\\to$ first hearing", dc["rec"]),
                   ("\\quad DBI clock, cases with both", dc["both"].dbi),
                   ("\\quad Planning clock, cases with both", dc["both"].rec),
                   ("\\quad difference (DBI $-$ Planning)", dc["both"].dbi - dc["both"].rec)):
        ds = describe(s)
        a(rf"{lab} & {f0(ds['n'])} & {f0(ds['p25'])} & {f0(ds['med'])} & {f0(ds['p75'])} & "
          rf"{f0(ds['p90'])} & {P(ds['neg'])}\%\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")
    dn = ctx["drneg"]["frame"]
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The DR cases whose linked permit was filed after the Planning record opened "
      r"(the negative differences in Table~\ref{tab:drclock}), by why. `Planning date from "
      r"another record': the case number has no DR record of its own and the clock fell back to "
      r"the earliest record sharing its stem. `Permit through a sibling or parent record': a T2 "
      r"link read from another record sharing the stem, not from the DR record itself or the "
      r"minutes. `The DR's own permit': printed in the minutes (T1) or named by the DR record, and "
      r"filed after the request opened. `On the item's parcel': the permit's block and lot are "
      r"among the item's.}\label{tab:drneg}")
    a(r"\begin{tabular}{lrr}\toprule")
    a(r"Why & Cases & On the item's parcel\\\midrule")
    for k, lab in (("record", "Planning date from another record"),
                   ("stem", "Permit through a sibling or parent record"),
                   ("own", "The DR's own permit, filed after the request")):
        g = dn[dn.why.eq(k)] if len(dn) else dn
        a(rf"{lab} & {f0(len(g))} & {f0(g.on_parcel.sum()) if len(g) else '0'}\\")
    a(rf"\midrule All & {f0(len(dn))} & {f0(dn.on_parcel.sum()) if len(dn) else '0'}\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    (TAB / "permits_content_tables.tex").write_text("\n".join(L) + "\n")
    print("→", TAB / "permits_content_tables.tex")


def write_macros(ctx):
    p, dev, col, inv, lk = ctx["p"], ctx["dev"], ctx["col"], ctx["inv"], ctx["lk"]
    dc, mn, drt = ctx["dclk"], ctx["mn"], ctx["drt"]
    summ = json.loads((DATA_ROOT / "extraction" / RUN / "permit_summary.json").read_text())
    s1, s2 = set(lk.loc[lk.tier.eq("t1"), "stem"]), set(lk.loc[lk.tier.eq("t2"), "stem"])
    cmp = {r["var"]: r for r in ctx["cmp"]}

    def c(var, k):
        return cmp[var][k]
    noted = inv[inv.note.ne("")]
    m = {
        "pcRetrieved": ctx["meta"]["retrieved"][:10], "pcRows": N(col["rows"]),
        "pcCols": N(inv.shape[0]), "pcSystemCols": N(ctx["inv_meta"]["n_system"]),
        "pcDataCols": N(inv.shape[0] - ctx["inv_meta"]["n_system"]),
        "pcNoted": N(len(noted)), "pcPermits": N(col["permits"]),
        "pcRowsRepeating": N(col["rows_repeating"]), "pcPermitsRepeating": N(col["permits_repeating"]),
        "pcPrimaryRows": N(col["primary_rows"]), "pcRulesAgree": P(col["rules_agree"]),
        "pcRulesCompared": N(col["rules_compared"]),
        "pcFieldsDiffer": P(col["fields_differ_max"], 2),
        "pcDupIds": N(ctx["meta"].get("duplicate_ids_dropped", 0)),
        "pcFiledFirst": str(int(p.fy.min())), "pcFiledLast": str(int(p.fy.max())),
        "pcNoFiled": N(int(p.filed_date.isna().sum())),
        "pcOTC": N(int(p.permit_type.eq("8").sum())),
        "pcOTCShare": P(p.permit_type.eq("8").mean()),
        "pcUniverse": N(int(p.dev.sum())), "pcUniverseShare": P(p.dev.mean()),
        "pcCostQ": str(round(100 * (1 - COST_Q))),
        "pcLinked": N(int(dev.linked.sum())), "pcLinkedShare": P(dev.linked.mean()),
        "pcTthreeOnly": N(int(dev.t3_only.sum())),
        "pcTone": N(len(s1)), "pcTtwo": N(len(s2)), "pcTboth": N(len(s1 & s2)),
        "pcToneUniv": N(int(dev.t1.sum())), "pcTtwoUniv": N(int(dev.t2.sum())),
        "pcTthreePrecision": f"{summ['parcel_rule']['precision']:.2f}",
        "pcTthreeRecall": f"{summ['parcel_rule']['recall']:.2f}",
        "pcSupportCells": P(ctx["sup1"]), "pcSupportUnits": P(ctx["sup2"]),
        "pcIssueNeg": P((p.t_issue < 0).mean(), 2),
        "pcCompleteNoDate": P((p.status.eq("complete") & p.completed_date.isna()).mean(), 2),
        "pcDrCases": N(dc["dr_cases"]), "pcDrDbiN": N(len(dc["dbi"])),
        "pcDrDbiMed": f0(dc["dbi"].median()), "pcDrDbiPninety": f0(dc["dbi"].quantile(.9)),
        "pcDrRecN": N(len(dc["rec"])), "pcDrRecMed": f0(dc["rec"].median()),
        "pcDrRecPninety": f0(dc["rec"].quantile(.9)), "pcDrBothN": N(len(dc["both"])),
        "pcDrDiffMed": f0((dc["both"].dbi - dc["both"].rec).median()),
        "pcDrDbiNeg": P((dc["dbi"] < 0).mean()),
        "pcSbThirtyFive": N(mn["sb35_projects"]), "pcSbFourTwoThree": N(mn["sb423_records"]),
        "pcMinisterialPermits": N(mn["ministerial_permits"]),
        "pcMinCommissionPermits": N(mn["commission_permits"]),
        "pcDrpN": N(drt["drp_n"]), "pcDrpNamed": N(drt["drp_named"]),
        "pcDrpYears": (f"{min(drt['years'])}--{max(drt['years'])}" if drt["years"] else "---"),
        "pcTsfBands": ", ".join(str(int(x)) for x in ctx["tsf"]) or "---",
        "pcInclusionary": str(INCLUSIONARY_UNITS), "pcOrdinanceUnits": str(ORDINANCE_2026_UNITS),
        "pcPropC": str(PROP_C_YEAR), "pcYearBin": str(YEAR_BIN),
        "pcCostQAltLo": str(round(100 * (1 - max(COST_Q_ALT)))),
        "pcCostQAltHi": str(round(100 * (1 - min(COST_Q_ALT)))),
        "pcMinYearN": str(MIN_YEAR_N), "pcSmoothYears": str(SMOOTH_YEARS),
        "pcCostNominal": f"{COST_NOMINAL/1e6:.0f}",
        "pcLinkedCostMed": f0(c("estimated cost ($)", "med_a")),
        "pcUnlinkedCostMed": f0(c("estimated cost ($)", "med_b")),
        "pcSdLogCostRaw": f2(c("estimated cost (log)", "sd_raw")),
        "pcSdLogCostCell": f2(c("estimated cost (log)", "sd_cell")),
        "pcSdLogCostUnits": f2(c("estimated cost (log)", "sd_units")),
        "pcSdUnitsRaw": f2(c("proposed units", "sd_raw")),
        "pcSdUnitsCell": f2(c("proposed units", "sd_cell")),
        "pcLinkedIssued": P(c("outcome: issued", "mean_a")),
        "pcUnlinkedIssued": P(c("outcome: issued", "mean_b")),
        "pcLinkedDays": f0(c("outcome: days filed to issued", "med_a")),
        "pcUnlinkedDays": f0(c("outcome: days filed to issued", "med_b")),
        "pcSdDaysRaw": f2(c("outcome: days filed to issued", "sd_raw")),
        "pcSdDaysCell": f2(c("outcome: days filed to issued", "sd_cell")),
        "pcSdDaysUnits": f2(c("outcome: days filed to issued", "sd_units")),
        "pcLinkedExit": P(c("outcome: expired or withdrawn", "mean_a")),
        "pcUnlinkedExit": P(c("outcome: expired or withdrawn", "mean_b")),
        "pcSdExitCell": f2(c("outcome: expired or withdrawn", "sd_cell")),
        "pcSdIssuedCell": f2(c("outcome: issued", "sd_cell")),
        "pcLinkedFy": f"{c('filing year', 'mean_a'):.0f}",
        "pcUnlinkedFy": f"{c('filing year', 'mean_b'):.0f}",
        "pcDrNamedInDbi": N(drt["named_in_dbi"]),
        "pcDrFiled": f"{drt['filed'][0]}--{drt['filed'][1]}",
        "pcCostPlaceholder": P(ctx["inv_meta"]["cost_le1_rows"]),
        "pcCostPlaceholderLinked": P(ctx["comp_base"].cost_placeholder[ctx["comp_base"].a].mean()),
        "pcCostPlaceholderUnlinked": P(ctx["comp_base"].cost_placeholder[~ctx["comp_base"].a].mean()),
        "pcFcdFilled": P(ctx["inv"].set_index("field").populated.get(
            "first_construction_document_date", np.nan)),
        "pcLmLinkedAlive": N(ctx["lmk"]["linked_alive"]),
        "pcLmLinkedAfter": N(ctx["lmk"]["linked_filed_after"]),
        "pcLmLinkedWith": N(ctx["lmk"]["linked_with_landmark"]),
        "pcLmUnlinkedAlive": N(ctx["lmk"]["unlinked_alive"]),
        "pcLmLagMed": f0(ctx["lmk"]["lag_median"]),
        "pcLmSdDays": f2(ctx["lmk"]["sd_days"]), "pcLmSdExit": f2(ctx["lmk"]["sd_exit"]),
        "pcLmSdIssued": f2(ctx["lmk"]["sd_issued"]),
        "pcLmSdDaysLo": f2(ctx["lmk"]["sd_days_range"][0]),
        "pcLmSdDaysHi": f2(ctx["lmk"]["sd_days_range"][1]),
        "pcLmDaysMedA": f0(ctx["lmk"]["days_med_a"]), "pcLmDaysMedB": f0(ctx["lmk"]["days_med_b"]),
        "pcCellDaysMedB": f0(ctx["lmk"]["cell_days_med_b"]),
        "pcCellMedGap": f0(c("outcome: days filed to issued", "med_a") - ctx["lmk"]["cell_days_med_b"]),
        "pcLmMedGap": f0(ctx["lmk"]["days_med_a"] - ctx["lmk"]["days_med_b"]),
        "pcLmMedSurvive": f"{100 * (ctx['lmk']['days_med_a'] - ctx['lmk']['days_med_b']) / (c('outcome: days filed to issued', 'med_a') - ctx['lmk']['cell_days_med_b']):.0f}",
        "pcLmExitA": P(ctx["lmk"]["exit_a"]), "pcLmExitB": P(ctx["lmk"]["exit_b"]),
        "pcLmSurviveDays": f"{100 * ctx['lmk']['sd_days'] / c('outcome: days filed to issued', 'sd_cell'):.0f}",
        "pcLmSurviveExit": f"{100 * ctx['lmk']['sd_exit'] / c('outcome: expired or withdrawn', 'sd_cell'):.0f}",
        "pcLmSupport": P(ctx["lmk"]["support"]), "pcLmDraws": str(len(LANDMARK_SEEDS)),
        "pcSdExitRaw": f2(c("outcome: expired or withdrawn", "sd_raw")),
        "pcDrNegN": N(ctx["drneg"]["n_neg"]),
        "pcDrNegRecord": N(int(ctx["drneg"]["frame"].why.eq("record").sum())),
        "pcDrNegStem": N(int(ctx["drneg"]["frame"].why.eq("stem").sum())),
        "pcDrNegOwn": N(int(ctx["drneg"]["frame"].why.eq("own").sum())),
        "pcDrNegStemOff": N(int((ctx["drneg"]["frame"].why.eq("stem")
                                 & ~ctx["drneg"]["frame"].on_parcel).sum())),
        "pcDrNegOffParcel": N(int((~ctx["drneg"]["frame"].on_parcel).sum())),
        "pcDrNegPct": P(ctx["drneg"]["n_neg"] / max(ctx["drneg"]["n_both"], 1)),
    }
    (TAB / "permits_content_macros.tex").write_text(
        "% GENERATED BY analyze_permit_content.py --- do not edit by hand.\n" +
        "\n".join(rf"\newcommand{{\{k}}}{{{v}}}" for k, v in sorted(m.items())) + "\n")
    print("→", TAB / "permits_content_macros.tex")



# ═══════════════════════════════════════════════════════════════════════════
# clocks: the full distribution of every duration, by track (the clocks memo)
# ═══════════════════════════════════════════════════════════════════════════
CLK_MEMO = HERE.parents[1] / "output" / "planning_commission_project" / "clocks"
CLK_FIG, CLK_TAB = CLK_MEMO / "figures", CLK_MEMO / "tables"
CLK_OUT = DATA_ROOT / "external" / "clocks"
# What ends a case at the Commission. A continuance, an intent to approve (the final motion is
# adopted weeks later) and "no action" do not; everything here does.
FINAL_ACTIONS = {"approved", "did_not_take_dr", "took_dr_and_approved", "took_dr", "disapproved",
                 "withdrawn", "adopted", "certified", "upheld"}
TRACKS = {"conditional use": {"conditional_use", "conditional_use_modification"},
          "discretionary review": {"discretionary_review"},
          "large project authorisation": {"large_project_authorization"}}
MINISTERIAL = "ministerial / by-right"
TRACK_ORDER = ["conditional use", "discretionary review", "large project authorisation",
               MINISTERIAL]
CLOCKS = [("app_hearing", "application to first hearing"),
          ("hearing_final", "first hearing to final action"),
          ("filed_issued", "permit filed to issued"),
          ("final_issued", "final action to permit issued"),
          ("issued_done", "permit issued to completion"),
          ("total", "first filing to completion (the developer's clock)")]
QUANTS = [0.10, 0.25, 0.50, 0.75, 0.90, 0.99]
VALUE_Q = 5                       # parcel-value bins: city-wide quintiles of the roll year
MATURE_BEFORE = 2013              # cohorts with at least a decade of follow-up at retrieval
MIN_CELL_N = 20                   # fewest observations a cell needs for its statistics
CLOCK_FROM = 1998                 # the first year of the corpus, and of the ministerial track


def km(t: pd.Series, e: pd.Series) -> pd.DataFrame:
    """Kaplan--Meier: S(t) at each distinct event time, from durations `t` (days, >= 0) and
    event flags `e` (True = the clock stopped; False = censored at t). Hand-rolled because the
    project has no survival library and the estimator is five lines."""
    d = pd.DataFrame({"t": t.astype(float), "e": e.astype(bool)}).dropna()
    d = d[d.t >= 0]
    if not len(d):
        return pd.DataFrame(columns=["t", "at_risk", "events", "S"])
    g = d.groupby("t").agg(events=("e", "sum"), n=("e", "size")).sort_index()
    at_risk = g.n[::-1].cumsum()[::-1]
    S = (1 - g.events / at_risk).cumprod()
    return pd.DataFrame({"t": g.index, "at_risk": at_risk.values, "events": g.events.values,
                         "S": S.values})


def km_quantile(k: pd.DataFrame, q: float) -> float:
    """The smallest time at which the survival curve has fallen to 1 - q; NaN when it never
    falls that far (too many spells still running)."""
    hit = k[k.S <= 1 - q]
    return float(hit.t.iloc[0]) if len(hit) else np.nan


def km_hazard(t: pd.Series, e: pd.Series, width: int = 90, upto: int = 3650) -> pd.DataFrame:
    """Discrete hazard in `width`-day intervals: events in the interval over spells at risk at
    its start, among spells that reached it."""
    d = pd.DataFrame({"t": t.astype(float), "e": e.astype(bool)}).dropna()
    d = d[d.t >= 0]
    rows = []
    for lo in range(0, upto, width):
        at = (d.t >= lo).sum()
        ev = ((d.t >= lo) & (d.t < lo + width) & d.e).sum()
        rows.append({"lo": lo, "at_risk": int(at), "events": int(ev),
                     "h": ev / at if at >= MIN_CELL_N else np.nan})
    return pd.DataFrame(rows)


def dist_stats(x: pd.Series) -> dict:
    """The distribution of completed spells. Negative intervals are kept: they enter the
    quantiles and the mean, and are counted; the log-scale dispersion uses the positive ones."""
    x = x.dropna().astype(float)
    out = {"n": int(len(x)), "neg": int((x < 0).sum())}
    if len(x) < MIN_CELL_N:
        return out
    for q in QUANTS:
        out[f"p{int(round(100*q))}"] = float(x.quantile(q))
    out["mean"] = float(x.mean())
    out["sd"] = float(x.std())
    pos = x[x > 0]
    out["sd_log"] = float(np.log(pos).std()) if len(pos) >= MIN_CELL_N else np.nan
    out["cv"] = out["sd"] / out["mean"] if out["mean"] > 0 else np.nan
    out["iqr_med"] = (out["p75"] - out["p25"]) / out["p50"] if out["p50"] > 0 else np.nan
    return out


def assessed_value(years: pd.Series, parcels: pd.Series) -> pd.DataFrame:
    """Assessed value (land + improvements) per square foot of lot --- of building where the
    roll records no lot area, as for condominium parcels --- in the roll year nearest the
    filing year, and its city-wide quintile in that roll year. The roll runs 2007--2025; a
    case filed before 2007 is valued at 2007 and flagged. Prop. 13 holds assessed values at
    the acquisition price plus two percent a year, so this ranks parcels by what was paid for
    them as much as by what they are worth; the clocks memo says so where it uses it."""
    a = pd.read_csv(DATA_ROOT / "external" / "assessor" / "assessor_secured_roll.csv.gz",
                    dtype=str, usecols=["closed_roll_year", "parcel_number", "lot_area",
                                        "property_area", "assessed_land_value",
                                        "assessed_improvement_value"])
    for c in ("lot_area", "property_area", "assessed_land_value", "assessed_improvement_value"):
        a[c] = pd.to_numeric(a[c], errors="coerce")
    a["yr"] = pd.to_numeric(a.closed_roll_year, errors="coerce")
    area = a.lot_area.where(a.lot_area > 0, a.property_area.where(a.property_area > 0))
    a["vpsf"] = (a.assessed_land_value.fillna(0) + a.assessed_improvement_value.fillna(0)) / area
    a = a[a.vpsf.gt(0) & np.isfinite(a.vpsf)]
    a["vq"] = a.groupby("yr").vpsf.transform(lambda v: pd.qcut(v.rank(method="first"), VALUE_Q,
                                                                labels=False) + 1)
    lo, hi = int(a.yr.min()), int(a.yr.max())
    idx = a.set_index(["parcel_number", "yr"])[["vpsf", "vq"]]
    idx = idx[~idx.index.duplicated()]
    out = []
    for y, ps in zip(years, parcels):
        if pd.isna(y) or not ps:
            out.append((np.nan, np.nan, False))
            continue
        yy = int(min(max(y, lo), hi))
        vals = [idx.loc[(p_, yy)] for p_ in ps if (p_, yy) in idx.index]
        if not vals:
            out.append((np.nan, np.nan, y < lo))
            continue
        v = pd.DataFrame(vals)
        out.append((float(v.vpsf.median()), float(v.vq.median()), y < lo))
    return pd.DataFrame(out, columns=["vpsf", "vq", "valued_later"], index=years.index)


def principal_permit(stems: set, p_idx: pd.DataFrame, earliest: bool) -> str:
    """The permit a case's clock runs through. On the DR track, the permit under review: the
    earliest filed of the linked permits (as the permits memo's DR clock). Otherwise the
    project's main permit: new construction first, then a site permit, then the largest
    estimated cost, among development-relevant linked permits; earliest filed breaks ties."""
    c = p_idx.loc[[s for s in stems if s in p_idx.index]]
    if not len(c):
        return ""
    if earliest:
        return c.filed_date.idxmin() if c.filed_date.notna().any() else c.index[0]
    c = c.assign(nc=c.permit_type.isin(["1", "2"]).astype(int),
                 site=c.site_permit.eq("Y").astype(int),
                 cost=c.estimated_cost.fillna(-1), f=c.filed_date.fillna(pd.Timestamp.max))
    return c.sort_values(["nc", "site", "cost", "f"], ascending=[False, False, False, True]).index[0]


def spell(start: pd.Series, end: pd.Series, censor: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Duration in days and event flag: the spell ends at `end` when there is one, and is
    censored at `censor` otherwise (an exit date, or the retrieval date)."""
    done = end.notna() & start.notna()
    t = (end - start).dt.days.where(done, (censor - start).dt.days)
    return t.where(start.notna()), done


def case_clocks(B: dict) -> pd.DataFrame:
    """One row per case on the three Commission tracks, and one per development-relevant
    permit on the ministerial track, with every clock's duration, event flag and cut."""
    import acquire_external_data as ax
    it, lk, p = B["it"], B["lk"], B["p"]
    retrieved = B["retrieved"]
    rec = ax.build_records()
    it = it[it.cn.ne("") & it.meeting_date.notna()].copy()
    rt = it.sort_values("meeting_date").groupby("cn").request_type.first()
    track = rt.map({r: k for k, v in TRACKS.items() for r in v})
    cases = track.dropna().index
    g = it[it.cn.isin(cases)].sort_values("meeting_date").groupby("cn")
    first = g.meeting_date.min()
    final = it[it.cn.isin(cases) & it.action.isin(FINAL_ACTIONS)].groupby("cn").meeting_date.min()
    parcels = g.parcels.agg(lambda x: sorted(set().union(*[set(v) for v in x])))
    stem = g.stem.first()
    # the application date: the earliest entitlement record, by case and else by stem
    r = rec.dropna(subset=["open_date"])
    ent = r[r.record_type.isin(ax.ENTITLEMENT_TYPES)]
    open_ent = pd.Series(cases, index=cases).map(ent.groupby("cn").open_date.min()) \
        .fillna(stem.map(ent[ent.stem.ne("")].groupby("stem").open_date.min()))
    link = lk[lk.tier.isin(["t1", "t2"])]
    stems_by = link.groupby("cn").stem.agg(set)
    p_idx = p[p.dev | p.stem.isin(set(link.stem))].set_index("stem")
    rows = []
    for cn in cases:
        tr = track[cn]
        ps = principal_permit(stems_by.get(cn, set()), p_idx, earliest=tr == "discretionary review")
        pr = p_idx.loc[ps] if ps else None
        rows.append({"unit": cn, "track": tr, "first_hearing": first[cn],
                     "final_action": final.get(cn, pd.NaT), "open_ent": open_ent.get(cn, pd.NaT),
                     "permit": ps,
                     **({k: pr[k] for k in ("filed_date", "issued_date", "completed_date",
                                            "status", "status_date", "proposed_units",
                                            "neighborhoods_analysis_boundaries", "permit_type")}
                        if pr is not None else {}),
                     "parcels": parcels.get(cn, [])})
    c = pd.DataFrame(rows)
    # the ministerial track: development-relevant permits no tier links to the Commission,
    # filed from the year the corpus starts. Earlier permits that never recorded a completion
    # would sit "at risk" for decades and hold every survival curve up; they are stale
    # records, not running spells. (The Commission tracks start there by construction.)
    m = p[p.dev & p.group.eq("not linked") & p.filed_date.ge(f"{CLOCK_FROM}-01-01")].copy()
    m = m.assign(unit=m.stem, track=MINISTERIAL, first_hearing=pd.NaT, final_action=pd.NaT,
                 open_ent=pd.NaT, permit=m.stem,
                 parcels=[[blk] if blk else [] for blk in
                          [__import__("normalize").blklot(b_, l_) for b_, l_ in zip(m.block, m.lot)]])
    cols = ["unit", "track", "first_hearing", "final_action", "open_ent", "permit", "filed_date",
            "issued_date", "completed_date", "status", "status_date", "proposed_units",
            "neighborhoods_analysis_boundaries", "permit_type", "parcels"]
    d = pd.concat([c[cols], m[cols]], ignore_index=True)
    d["exit_date"] = d.status_date.where(d.status.isin(EXITS))
    cens = d.exit_date.fillna(retrieved)
    # application to first hearing: the entitlement clock, except on the DR track, where the
    # developer's clock starts at the DBI filing of the permit under review (the data-
    # acquisition memo's note of 2026-09-11)
    app = d.open_ent.where(d.track.ne("discretionary review"), d.filed_date)
    d["app_date"] = app
    d["t_app_hearing"], d["e_app_hearing"] = spell(app, d.first_hearing, pd.Series(retrieved, index=d.index))
    d["t_hearing_final"], d["e_hearing_final"] = spell(d.first_hearing, d.final_action,
                                                       pd.Series(retrieved, index=d.index))
    d["t_filed_issued"], d["e_filed_issued"] = spell(d.filed_date, d.issued_date, cens)
    d["t_final_issued"], d["e_final_issued"] = spell(d.final_action, d.issued_date, cens)
    d["t_issued_done"], d["e_issued_done"] = spell(d.issued_date, d.completed_date, cens)
    d["first_filing"] = pd.concat([app, d.filed_date], axis=1).min(axis=1)
    d["t_total"], d["e_total"] = spell(d.first_filing, d.completed_date, cens)
    d.loc[d.track.eq(MINISTERIAL), ["t_app_hearing", "t_hearing_final", "t_final_issued"]] = np.nan
    # A case with no linked permit has no permit clock at all: missing, not censored at the
    # retrieval date (which would read as a very long wait).
    d.loc[d.permit.fillna("").eq(""), ["t_filed_issued", "t_final_issued", "t_issued_done",
                                      "t_total"]] = np.nan
    for ck, _ in CLOCKS:
        d[f"e_{ck}"] = d[f"e_{ck}"] & d[f"t_{ck}"].notna()
    d["fy"] = d.first_filing.dt.year
    d["period"] = (d.fy // 5 * 5).map(lambda y: f"{int(y)}--{int(y)+4}" if pd.notna(y) else "")
    d["ubin"] = d.proposed_units.map(unit_bin)
    v = assessed_value(d.fy, d.parcels)
    d = pd.concat([d, v], axis=1)
    return d




TRACK_COLOR = {"conditional use": "#a33", "discretionary review": "#5b7fa6",
               "large project authorisation": "#b07d2b", MINISTERIAL: "#2f6f4f"}
KM_HORIZON = 5500                 # days drawn on the survival curves (about fifteen years)
TRACK_SHORT = {"conditional use": "CU", "discretionary review": "DR",
               "large project authorisation": "LPA", MINISTERIAL: "Min."}


def headline_cells(d: pd.DataFrame, ck: str = "total") -> pd.DataFrame:
    """The developer's clock by track x parcel-value quintile: Kaplan--Meier quantiles on every
    spell, and completed-spell moments on the mature cohorts (first filing before
    MATURE_BEFORE), where most spells have had time to finish."""
    rows = []
    t, e = d[f"t_{ck}"], d[f"e_{ck}"]
    for tr in TRACK_ORDER:
        for q in list(range(1, VALUE_Q + 1)) + ["all"]:
            m = d.track.eq(tr) & t.notna() & ((d.vq == q) if q != "all" else d.vq.notna() | True)
            if m.sum() < MIN_CELL_N:
                rows.append({"track": tr, "vq": q, "spells": int(m.sum())})
                continue
            k = km(t[m], e[m])
            mat = m & e & d.fy.lt(MATURE_BEFORE)
            st = dist_stats(t[mat])
            k25, k50, k75, k90 = (km_quantile(k, x) for x in (.25, .5, .75, .9))
            s10 = k[k.t <= 3652].S.iloc[-1] if (k.t <= 3652).any() else 1.0
            rows.append({"track": tr, "vq": q, "spells": int(m.sum()), "completed": int((m & e).sum()),
                         "km_p25": k25, "km_p50": k50, "km_p75": k75, "km_p90": k90,
                         "s10": float(s10),
                         "km_iqr_med": (k75 - k25) / k50 if k50 and not np.isnan(k75) else np.nan,
                         "mat_n": st.get("n", 0), "mat_mean": st.get("mean", np.nan),
                         "mat_cv": st.get("cv", np.nan), "mat_sdlog": st.get("sd_log", np.nan),
                         "mat_p50": st.get("p50", np.nan)})
    return pd.DataFrame(rows)


def clocks_figures(ctx):
    d = ctx["d"]
    fig, axes = plt.subplots(2, 3, figsize=(7.4, 5.2), sharey=True)
    for ax, (ck, lab) in zip(axes.flat, CLOCKS):
        for tr in TRACK_ORDER:
            m = d.track.eq(tr) & d[f"t_{ck}"].notna()
            if m.sum() < MIN_CELL_N:
                continue
            k = km(d.loc[m, f"t_{ck}"], d.loc[m, f"e_{ck}"])
            k = k[k.t <= KM_HORIZON]
            ax.step(np.r_[0, k.t.values], np.r_[1, k.S.values], where="post",
                    color=TRACK_COLOR[tr], lw=1.4, label=f"{tr} ({int(m.sum()):,})")
        ax.set_title(lab, fontsize=7.5, loc="left")
        ax.set_xlim(0, KM_HORIZON)
        ax.set_xlabel("days", fontsize=7)
    for ax in axes[:, 0]:
        ax.set_ylabel("share of spells still running")
    handles, labels = [], []
    for ax in axes.flat:
        for h, l in zip(*ax.get_legend_handles_labels()):
            tr = l.split(" (")[0]
            if tr not in [x.split(" (")[0] for x in labels]:
                handles.append(h); labels.append(tr)
    fig.legend(handles, labels, loc="lower center", ncol=4, frameon=False, fontsize=7)
    fig.suptitle("Kaplan–Meier survival of each clock, by track (censored spells contribute "
                 "their exposure)", fontsize=8.5, x=0.02, ha="left")
    fig.tight_layout(rect=(0, 0.05, 1, 0.97))
    fig.savefig(CLK_FIG / "fig_km.pdf")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.0), sharey=True)
    for ax, ck in zip(axes, ("filed_issued", "total")):
        for tr in TRACK_ORDER:
            m = d.track.eq(tr) & d[f"t_{ck}"].notna()
            if m.sum() < MIN_CELL_N:
                continue
            h = km_hazard(d.loc[m, f"t_{ck}"], d.loc[m, f"e_{ck}"])
            ax.plot(h.lo / 365.25, h.h * 100, color=TRACK_COLOR[tr], alpha=0.3, lw=0.8)
            ax.plot(h.lo / 365.25, (h.h * 100).rolling(4, center=True, min_periods=2).mean(),
                    color=TRACK_COLOR[tr], lw=1.6, label=tr)
        ax.set_title(dict(CLOCKS)[ck], fontsize=8, loc="left")
        ax.set_xlabel("years since the clock started")
    axes[0].set_ylabel("% of running spells ending\nin the next 90 days")
    axes[1].legend(frameon=False, fontsize=6.5)
    fig.suptitle("Hazards: faint 90-day values, bold a one-year centred mean; intervals with "
                 f"fewer than {MIN_CELL_N} spells at risk not drawn", fontsize=8, x=0.02, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(CLK_FIG / "fig_hazard.pdf")
    plt.close(fig)

    hc = headline_cells(d)
    ctx["headline"] = hc
    ctx["headline_permit"] = headline_cells(d, "filed_issued")
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.1))
    for ax, h in zip(axes, (hc, ctx["headline_permit"])):
        for tr in TRACK_ORDER:
            g = h[h.track.eq(tr) & h.vq.ne("all")].copy()
            g["vq"] = g.vq.astype(int)
            ax.plot(g.vq, g.mat_cv, marker="o", color=TRACK_COLOR[tr], lw=1.4, label=tr)
        ax.set_xlabel("assessed value per sq. ft (quintile)")
        ax.set_xticks(range(1, VALUE_Q + 1))
    axes[0].set_title("first filing to completion", fontsize=8, loc="left")
    axes[1].set_title("permit filed to issued", fontsize=8, loc="left")
    axes[0].set_ylabel("coefficient of variation")
    h_, l_ = axes[0].get_legend_handles_labels()
    fig.legend(h_, l_, loc="lower center", ncol=4, frameon=False, fontsize=6.5)
    fig.suptitle(f"Dispersion against parcel value: CV of completed spells, cohorts first filed "
                 f"before {MATURE_BEFORE}; quintiles city-wide within the roll year; cells under "
                 f"{MIN_CELL_N} not drawn", fontsize=7.5, x=0.02, ha="left")
    fig.tight_layout(rect=(0, 0.08, 1, 0.92))
    fig.savefig(CLK_FIG / "fig_dispersion_value.pdf")
    plt.close(fig)


def _d(x):
    return "---" if x is None or pd.isna(x) else f"{x:,.0f}".replace(",", "{,}")


def _r(x, k=2):
    return "---" if x is None or pd.isna(x) else f"{x:.{k}f}"


def clocks_tables(ctx):
    d, mom, hc = ctx["d"], ctx["mom"], ctx["headline"]
    L = ["% GENERATED BY analyze_permit_content.py clocks --- do not edit by hand."]
    a = L.append
    # 1. what each track holds
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The units each track is measured on, and how many carry each clock. A spell "
      r"counts when its start date exists; `done' is the share that has ended by the retrieval "
      r"date; the rest are censored (still running, or the permit expired or was "
      r"withdrawn).}\label{tab:inventory}")
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{l" + "rr" * len(CLOCKS) + r"r}\toprule")
    a(r"Track & " + " & ".join(rf"\multicolumn{{2}}{{c}}{{{lab.split(' (')[0]}}}" for _, lab in CLOCKS)
      + r" & Units\\")
    a("".join(rf"\cmidrule(lr){{{2+2*i}-{3+2*i}}}" for i in range(len(CLOCKS))))
    a(r" & " + " & ".join(["spells & done"] * len(CLOCKS)) + r" & \\\midrule")
    for tr in TRACK_ORDER:
        g = d[d.track.eq(tr)]
        cells = []
        for ck, _ in CLOCKS:
            n = int(g[f"t_{ck}"].notna().sum())
            cells.append(f"{_d(n)} & {('---' if not n else f'{100*g[f'e_{ck}'].sum()/n:.0f}' + chr(92) + '%')}")
        a(rf"{tr} & " + " & ".join(cells) + rf" & {_d(len(g))}\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")
    # 2. every clock by track: the full distribution of completed spells, and KM quantiles
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Every clock, by track: the distribution of completed spells (days) --- "
      r"percentiles, mean, the standard deviation of log days and the coefficient of "
      r"variation --- beside the Kaplan--Meier median and 90th percentile, which count the "
      r"censored spells' exposure. `Neg.' is the number of negative intervals, kept in every "
      r"statistic except the log one. A dash: a cell under %d observations, or a Kaplan--Meier "
      r"quantile the curve never reaches.}\label{tab:moments}" % MIN_CELL_N)
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{llrrrrrrrrrrrr}\toprule")
    a(r"Clock & Track & $n$ & Neg. & p10 & p25 & p50 & p75 & p90 & p99 & Mean & "
      r"SD log & \textbf{CV} & KM p50 / p90\\\midrule")
    for ck, lab in CLOCKS:
        first = True
        for tr in TRACK_ORDER:
            r = mom[(mom.clock.eq(ck)) & mom.track.eq(tr) & mom.cut.eq("all")]
            if not len(r):
                continue
            r = r.iloc[0]
            a(rf"{lab.split(' (')[0] if first else ''} & {tr} & {_d(r.n)} & {_d(r.neg)} & "
              + " & ".join(_d(r.get(f"p{q}")) for q in (10, 25, 50, 75, 90, 99))
              + rf" & {_d(r.get('mean'))} & {_r(r.get('sd_log'))} & \textbf{{{_r(r.get('cv'))}}} & "
              rf"{_d(r.km_p50)} / {_d(r.km_p90)}\\")
            first = False
        a(r"\midrule")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")
    # 3. the headline
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{\textbf{The developer's clock --- first filing to completion --- by track and "
      r"parcel value.} Value is assessed value per square foot, city-wide quintile of the roll "
      r"year (1 lowest). Left: Kaplan--Meier quantiles over every spell (days) and the "
      r"interquartile range over the median; $S$(10 yr) is the share of spells still running ten "
      r"years after first filing --- no completion recorded. Right: completed spells of cohorts first filed "
      r"before %d, which have had a decade to finish --- mean, coefficient of variation and "
      r"standard deviation of log days. Descriptive: these are the moments, not "
      r"estimates of anything.}\label{tab:headline}" % MATURE_BEFORE)
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{llrrrrrrrrrrr}\toprule")
    a(r" & & & \multicolumn{6}{c}{Kaplan--Meier, all cohorts} & "
      rf"\multicolumn{{4}}{{c}}{{Completed, first filed before {MATURE_BEFORE}}}\\")
    a(r"\cmidrule(lr){4-9}\cmidrule(lr){10-13}")
    a(r"Track & Value & Spells & p25 & Median & p75 & p90 & IQR/med. & $S$(10 yr) & $n$ & Mean & "
      r"\textbf{CV} & SD log\\\midrule")
    for tr in TRACK_ORDER:
        first = True
        for _, r in hc[hc.track.eq(tr)].iterrows():
            lab = "all" if r.vq == "all" else str(int(r.vq))
            a(rf"{tr if first else ''} & {lab} & {_d(r.spells)} & {_d(r.get('km_p25'))} & "
              rf"{_d(r.get('km_p50'))} & {_d(r.get('km_p75'))} & {_d(r.get('km_p90'))} & "
              rf"{_r(r.get('km_iqr_med'))} & {_r(r.get('s10'))} & {_d(r.get('mat_n'))} & {_d(r.get('mat_mean'))} & "
              rf"\textbf{{{_r(r.get('mat_cv'))}}} & {_r(r.get('mat_sdlog'))}\\")
            first = False
        a(r"\midrule")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")
    # 4. the other cuts, on the developer's clock and the permit clock
    for (cut, col, lab, key), ck in [(c_, k_) for c_ in
                                     (("unit bin", "ubin", "proposed units", "byunits"),
                                      ("filing period", "period", "first filing", "byperiod"))
                                     for k_ in ("filed_issued", "total")]:
        a(r"\begin{table}[htbp]\centering")
        a(rf"\caption{{{dict(CLOCKS)[ck].split(' (')[0].capitalize()}, by track and {lab}: "
          rf"spells, Kaplan--Meier median and 90th percentile (days), and the median and CV of "
          rf"completed spells. Cells under {MIN_CELL_N} spells omitted.}}"
          rf"\label{{tab:{key}{'Permit' if ck == 'filed_issued' else 'Total'}}}")
        a(r"\resizebox{\textwidth}{!}{%")
        a(r"\begin{tabular}{llrrrrrr}\toprule")
        a(rf"Track & {lab.capitalize()} & Spells & KM p50 & KM p90 & Done & p50 done & "
          r"CV done\\\midrule")
        if True:
            for tr in TRACK_ORDER:
                g = mom[mom.clock.eq(ck) & mom.track.eq(tr) & mom.cut.eq(cut)]
                if col == "ubin":
                    order = [lab_ for *_, lab_ in UNIT_BINS] + ["unknown"]
                    g = g.assign(o=g.cell.map({k: i for i, k in enumerate(order)})).sort_values("o")
                first = True
                for _, r in g.iterrows():
                    a(rf"{tr if first else ''} & {r.cell} & {_d(r.spells)} & {_d(r.km_p50)} & "
                      rf"{_d(r.km_p90)} & {_d(r.completed)} & {_d(r.get('p50'))} & {_r(r.get('cv'))}\\")
                    first = False
                if len(g):
                    a(r"\midrule")
        a(r"\bottomrule\end{tabular}}\end{table}")
        a("")
    # 5. neighbourhoods, the developer's clock
    a(r"{\small\setlength{\tabcolsep}{3.5pt}")
    a(r"\begin{longtable}{@{}L{5.2cm}lrrrr@{}}")
    a(rf"\caption{{The developer's clock by analysis neighbourhood (of the project's permit) and "
      rf"track --- CU conditional use, DR discretionary review, LPA large project authorisation, "
      rf"Min.\ ministerial: spells, Kaplan--Meier median and 90th percentile (days), CV of "
      rf"completed spells. Cells under {MIN_CELL_N} spells omitted.}}\label{{tab:bynbhd}}\\\toprule")
    hdr = r"Neighbourhood & Track & Spells & KM p50 & KM p90 & CV done\\\midrule"
    a(hdr + r"\endfirsthead")
    a(r"\toprule " + hdr + r"\endhead")
    g = mom[mom.clock.eq("total") & mom.cut.eq("neighbourhood")].sort_values(["cell", "track"])
    last = None
    for _, r in g.iterrows():
        a(rf"{T(r.cell) if r.cell != last else ''} & {TRACK_SHORT[r.track]} & {_d(r.spells)} & {_d(r.km_p50)} & "
          rf"{_d(r.km_p90)} & {_r(r.get('cv'))}\\")
        last = r.cell
    a(r"\bottomrule\end{longtable}}")
    # one file per table, named by its label, so the memo places each where the text needs it;
    # the old ones go first, so a table that is no longer made cannot linger
    for f in CLK_TAB.glob("clk_*.tex"):
        f.unlink()
    chunks = [ch for ch in "\n".join(L[1:]).split("\n\n") if ch.strip()]
    for ch in chunks:
        lab = re.search(r"\\label\{tab:(\w+)\}", ch).group(1)
        (CLK_TAB / f"clk_{lab}.tex").write_text(L[0] + "\n" + ch.strip() + "\n")
    (CLK_TAB / "clocks_tables.tex").unlink(missing_ok=True)


def clocks_macros(ctx):
    d, hc, mom = ctx["d"], ctx["headline"], ctx["mom"]
    from scipy.stats import spearmanr
    m = {"ckRetrieved": str(ctx["retrieved"].date()), "ckMature": str(MATURE_BEFORE),
         "ckMinCell": str(MIN_CELL_N), "ckValueQ": str(VALUE_Q), "ckHorizon": _d(KM_HORIZON)}
    tag = {"conditional use": "Cu", "discretionary review": "Dr",
           "large project authorisation": "Lpa", MINISTERIAL: "Min"}
    for tr in TRACK_ORDER:
        g = d[d.track.eq(tr)]
        m[f"ckUnits{tag[tr]}"] = _d(len(g))
        h = hc[hc.track.eq(tr) & hc.vq.ne("all")].dropna(subset=["mat_cv"])
        h = h.assign(vq=h.vq.astype(int))
        m[f"ckCvLo{tag[tr]}"] = _r(h.mat_cv.iloc[0]) if len(h) else "---"
        m[f"ckCvHi{tag[tr]}"] = _r(h.mat_cv.iloc[-1]) if len(h) else "---"
        rho = spearmanr(h.vq, h.mat_cv).statistic if len(h) >= 3 else np.nan
        m[f"ckCvRho{tag[tr]}"] = _r(rho)
        hi = hc[hc.track.eq(tr) & hc.vq.ne("all")].dropna(subset=["km_iqr_med"])
        rho2 = spearmanr(hi.vq.astype(int), hi.km_iqr_med).statistic if len(hi) >= 3 else np.nan
        m[f"ckIqrRho{tag[tr]}"] = _r(rho2)
        hp = ctx["headline_permit"]
        hp = hp[hp.track.eq(tr) & hp.vq.ne("all")].dropna(subset=["mat_cv"])
        hp = hp.assign(vq=hp.vq.astype(int))
        m[f"ckPermitCvRho{tag[tr]}"] = _r(spearmanr(hp.vq, hp.mat_cv).statistic) if len(hp) >= 3 else "---"
        m[f"ckPermitCvLo{tag[tr]}"] = _r(hp.mat_cv.iloc[0]) if len(hp) else "---"
        m[f"ckPermitCvHi{tag[tr]}"] = _r(hp.mat_cv.iloc[-1]) if len(hp) else "---"
        a_ = hc[hc.track.eq(tr) & hc.vq.eq("all")]
        if len(a_):
            r = a_.iloc[0]
            m[f"ckTotMed{tag[tr]}"] = _d(r.get("km_p50"))
            m[f"ckTotPninety{tag[tr]}"] = _d(r.get("km_p90"))
            m[f"ckTotCv{tag[tr]}"] = _r(r.get("mat_cv"))
            m[f"ckTotIqr{tag[tr]}"] = _r(r.get("km_iqr_med"))
            m[f"ckTotStenPct{tag[tr]}"] = f"{100 * r.get('s10'):.0f}" if pd.notna(r.get("s10")) else "---"
            m[f"ckTotMatN{tag[tr]}"] = _d(r.get("mat_n"))
        for ck, _ in CLOCKS:
            r = mom[mom.clock.eq(ck) & mom.track.eq(tr) & mom.cut.eq("all")]
            if len(r):
                r = r.iloc[0]
                ckc = "".join(w.title() for w in ck.split("_"))
                m[f"ck{ckc}Med{tag[tr]}"] = _d(r.km_p50)
                m[f"ck{ckc}Cv{tag[tr]}"] = _r(r.get("cv"))
                m[f"ck{ckc}N{tag[tr]}"] = _d(r.spells)
                m[f"ck{ckc}Neg{tag[tr]}"] = _d(r.neg)
        g_ = d[d.track.eq(tr) & d.e_hearing_final]
        m[f"ckFirstFinalPct{tag[tr]}"] = f"{100 * g_.t_hearing_final.eq(0).mean():.0f}" if len(g_) else "---"
        g_ = d[d.track.eq(tr)]
        m[f"ckPermitPct{tag[tr]}"] = f"{100 * g_.permit.fillna('').ne('').mean():.0f}"
    m["ckValuedLater"] = _d(int(d.valued_later.sum()))
    m["ckClockFrom"] = str(CLOCK_FROM)
    m["ckValueCover"] = f"{100 * d.vq.notna().mean():.1f}"
    (CLK_TAB / "clocks_macros.tex").write_text(
        "% GENERATED BY analyze_permit_content.py clocks --- do not edit by hand.\n" +
        "\n".join(rf"\newcommand{{\{k}}}{{{v}}}" for k, v in sorted(m.items())) + "\n")


def moments_long(d: pd.DataFrame) -> pd.DataFrame:
    """The full set of cells: every clock x track x cut (all; unit bin; parcel-value
    quintile; filing period; neighbourhood), completed-spell statistics and Kaplan--Meier
    quantiles side by side. Written whole to $MFHR_DATA_ROOT/external/clocks/."""
    rows = []
    cuts = [("all", None), ("unit bin", "ubin"), ("value quintile", "vq"),
            ("filing period", "period"), ("neighbourhood", "neighborhoods_analysis_boundaries")]
    for ck, lab in CLOCKS:
        t, e = d[f"t_{ck}"], d[f"e_{ck}"]
        for tr in TRACK_ORDER:
            base = d.track.eq(tr) & t.notna()
            for cname, col in cuts:
                groups = [("all", base)] if col is None else \
                    [(str(k), base & d[col].astype(str).eq(str(k))) for k in
                     sorted(d.loc[base, col].dropna().astype(str).unique())]
                for gk, m in groups:
                    if m.sum() < MIN_CELL_N:
                        continue
                    st = dist_stats(t[m & e])
                    k = km(t[m], e[m])
                    rows.append({"clock": ck, "clock_label": lab, "track": tr, "cut": cname,
                                 "cell": gk, "spells": int(m.sum()), "completed": int((m & e).sum()),
                                 "censored_share": float(1 - (m & e).sum() / m.sum()), **st,
                                 **{f"km_p{int(100*q)}": km_quantile(k, q) for q in (.25, .5, .75, .9)}})
    return pd.DataFrame(rows)


def clocks_stage():
    """Build the clock frame, write the moments, and the clocks memo's tables, figures and
    macros."""
    CLK_FIG.mkdir(parents=True, exist_ok=True)
    CLK_TAB.mkdir(parents=True, exist_ok=True)
    CLK_OUT.mkdir(parents=True, exist_ok=True)
    B = load_permits(inventory=False)
    d = case_clocks(B)
    d.drop(columns=["parcels"]).to_parquet(CLK_OUT / "clocks.parquet", index=False)
    mom = moments_long(d)
    mom.to_csv(CLK_OUT / "clock_moments.csv", index=False)
    ctx = {"d": d, "mom": mom, "retrieved": B["retrieved"]}
    clocks_figures(ctx)
    clocks_tables(ctx)
    clocks_macros(ctx)
    print(f"{len(d):,} units; {len(mom):,} cells → {CLK_OUT}, {CLK_TAB}")


# ═══════════════════════════════════════════════════════════════════════════
# the outcome chain: entitlement → issuance → first construction → completion → units
# ═══════════════════════════════════════════════════════════════════════════
# Conditional on entitlement, what fraction of approved units are built, and how long does it
# take? The chain runs on the clocks frame's units (a Commission case, or a ministerial
# permit), restricted to housing projects: the principal permit adds at least one unit.
OC_MATURE = (2005, 2014)          # approvals old enough to have had a decade to finish; 2005 is
                                  # the first year Housing Production records completions
OC_YEARS = 10                     # the horizon at which "never delivered" is read
APPROVING = {"approved", "did_not_take_dr", "took_dr_and_approved", "took_dr"}
OC_MACROS = CLK_TAB / "outcomes_macros.tex"


def cif_curve(t: pd.Series, kind: pd.Series, of: str) -> pd.Series:
    """The whole cumulative-incidence step function of `of`, indexed by event time."""
    d = pd.DataFrame({"t": t.astype(float), "k": pd.Series(kind, index=t.index).fillna("")}).dropna(subset=["t"])
    d = d[d.t >= 0]
    g = d.groupby("t").agg(ev=("k", lambda k: (k != "").sum()), of=("k", lambda k: (k == of).sum()),
                           n=("k", "size")).sort_index()
    at_risk = g.n[::-1].cumsum()[::-1]
    S_prev = (1 - g.ev / at_risk).cumprod().shift(fill_value=1.0)
    return (S_prev * g["of"] / at_risk).cumsum()


def cif(t: pd.Series, kind: pd.Series, of: str, horizon: float) -> float:
    """Cumulative incidence of event `of` by `horizon` days with competing events (Aalen--
    Johansen): kind is the event that ended the spell ('' = censored). An exit is a competing
    event, not a censoring --- a withdrawn project does not go on to deliver at the others' rate."""
    d = pd.DataFrame({"t": t.astype(float), "k": kind.fillna("")}).dropna(subset=["t"])
    d = d[d.t >= 0].sort_values("t")
    if not len(d):
        return np.nan
    S, F, n = 1.0, 0.0, len(d)
    for tt, g in d.groupby("t"):
        if tt > horizon:
            break
        ev = (g.k != "").sum()
        F += S * (g.k == of).sum() / n
        S *= 1 - ev / n
        n -= len(g)
    return F


def _cif_at(g: pd.DataFrame, of: str, h: float) -> float:
    c = cif_curve(g.t_done, g.kind, of)
    c = c[c.index <= h]
    return float(c.iloc[-1]) if len(c) else (0.0 if len(g) else np.nan)


def outcome_frame(B: dict) -> pd.DataFrame:
    import acquire_external_data as ax
    d = pd.read_parquet(CLK_OUT / "clocks.parquet")
    p = B["p"].set_index("stem")
    it = B["it"]
    d = d[d.permit.fillna("").ne("")].copy()
    pr = p.reindex(d.permit)
    d["existing_units"] = pr.existing_units.values
    d["fcd"] = pr.first_construction_document_date.values
    new_bldg = d.permit_type.isin(["1", "2"])
    d["net_units"] = d.proposed_units - d.existing_units.where(~new_bldg, d.existing_units.fillna(0))
    d = d[d.net_units.ge(1)].copy()
    # entitlement: the Commission's first approving action; a ministerial permit starts at filing
    appr = it[it.action.isin(APPROVING) & it.cn.ne("")].groupby("cn").meeting_date.min()
    comm = d.track.ne(MINISTERIAL)
    d["approved"] = np.where(comm, d.unit.map(appr), d.filed_date)
    d["approved"] = pd.to_datetime(d.approved)
    d = d[d.approved.notna()].copy()
    # delivery: Housing Production's completions by permit, and the certificates of occupancy
    # in the unit-completion table; DBI's own completion date where neither records one
    R = DATA_ROOT / "external" / "pipeline"
    hp = pd.read_csv(R / "housing_production.csv.gz", dtype=str)
    hp["stem"] = hp.bpa.map(ax.digits)
    hp["units_done"] = pd.to_numeric(hp.net_units_completed, errors="coerce")
    hp["first_done"] = pd.to_datetime(hp.first_completion_date, errors="coerce")
    hp = hp[hp.stem.str.len().ge(8)].groupby("stem").agg(units_done=("units_done", "sum"),
                                                        first_done=("first_done", "min"))
    uc = pd.read_csv(R / "unit_completions.csv.gz", dtype=str)
    uc["stem"] = uc.building_permit_application.map(ax.digits)
    uc["date"] = pd.to_datetime(uc.date_issued, errors="coerce")
    ucf = uc.groupby("stem").date.min()
    d["hp_units"] = d.permit.map(hp.units_done)
    d["hp_first"] = d.permit.map(hp.first_done)
    d["uc_first"] = d.permit.map(ucf)
    d["done"] = d[["completed_date", "hp_first", "uc_first"]].min(axis=1)
    d["done_source"] = np.select([d.hp_first.notna(), d.uc_first.notna(), d.completed_date.notna()],
                                 ["Housing Production", "certificate of occupancy", "DBI completion"], "")
    d["exit"] = d.exit_date.where(d.done.isna())
    return d


def outcomes_stage():
    """The outcome chain for the clocks memo: tables, one figure, macros."""
    B = load_permits(inventory=False)
    retrieved = B["retrieved"]
    d = outcome_frame(B)
    d["ay"] = d.approved.dt.year
    mat = d[d.ay.between(*OC_MATURE)]
    t = (d.done.fillna(d.exit).fillna(retrieved) - d.approved).dt.days
    kind = np.where(d.done.notna(), "done", np.where(d.exit.notna(), "exit", ""))
    d["t_done"], d["kind"] = t, kind
    H = OC_YEARS * 365.25
    rows = []
    for tr in TRACK_ORDER:
        for vq in ["all", 1, 2, 3, 4, 5]:
            g = d[d.track.eq(tr) & (d.vq.eq(vq) if vq != "all" else True)]
            gm = g[g.ay.between(*OC_MATURE)]
            if len(gm) < MIN_CELL_N and vq != "all":
                continue
            done_t = (gm.done - gm.approved).dt.days[gm.done.notna()] / 365.25
            rows.append({"track": tr, "vq": vq, "n_all": len(g), "n": len(gm),
                         "issued": gm.issued_date.notna().mean(), "fcd": gm.fcd.notna().mean(),
                         "done": gm.done.notna().mean(), "exit": gm.exit.notna().mean(),
                         "open": (gm.done.isna() & gm.exit.isna()).mean(),
                         "units_appr": gm.net_units.sum(), "units_hp": gm.hp_units.fillna(0).sum(),
                         "med_years": done_t.median(), "p90_years": done_t.quantile(.9),
                         "cif10": _cif_at(g, "done", H), "cif10_exit": _cif_at(g, "exit", H)})
    oc = pd.DataFrame(rows)
    oc["units_share"] = oc.units_hp / oc.units_appr
    oc.to_csv(CLK_OUT / "outcomes.csv", index=False)
    d.drop(columns=["parcels"], errors="ignore").to_parquet(CLK_OUT / "outcomes_units.parquet", index=False)
    # ── tables ──
    lab = {"conditional use": "CU", "discretionary review": "DR", "large project authorisation": "LPA",
           MINISTERIAL: "Ministerial"}
    L = [r"\begin{table}[htbp]\centering\small",
         rf"\caption{{The outcome chain for housing projects (the principal permit adds at least one unit), "
         rf"entitled {OC_MATURE[0]}--{OC_MATURE[1]} (a ministerial permit: filed), by track and city-wide "
         rf"quintile of assessed value per square foot. Shares of projects reaching each stage by retrieval; "
         rf"units delivered are Housing Production's completed units on the principal permit; years are "
         rf"entitlement to the first recorded completion, among projects that completed. The last column is "
         rf"the cumulative incidence of completion within {OC_YEARS} years over every cohort, with exits as a "
         rf"competing event. Cells under {MIN_CELL_N} projects are omitted.}}\label{{tab:outcomes}}",
         r"\resizebox{\textwidth}{!}{\begin{tabular}{llrrrrrrrrrr}\toprule",
         r"Track & Value & $n$ & Issued & Completed & Exited & Open & Units approved & Units delivered & "
         r"\% units & Median years & Done by 10 y\\\midrule"]
    for r in oc.itertuples():
        L.append(rf"{lab[r.track] if r.vq == 'all' else ''} & {'all' if r.vq == 'all' else 'Q' + str(r.vq)} & "
                 rf"{_d(r.n)} & {P(r.issued, 0)} & {P(r.done, 0)} & {P(r.exit, 0)} & {P(r.open, 0)} & "
                 rf"{_d(r.units_appr)} & {_d(r.units_hp)} & {P(r.units_share, 0)} & {_r(r.med_years, 1)} & "
                 rf"{P(r.cif10, 0)}\\")
        if r.vq == 5 or (r.vq == "all" and not (oc.track.eq(r.track) & oc.vq.ne("all")).any()):
            L.append(r"\midrule")
    if L[-1] == r"\midrule":
        L.pop()
    L += [r"\bottomrule\end{tabular}}\end{table}"]
    (CLK_TAB / "clk_outcomes.tex").write_text("% GENERATED BY analyze_permit_content.py outcomes --- do not edit by hand.\n"
                                              + "\n".join(L) + "\n")
    # ── figure: cumulative incidence of completion and of exit, by track ──
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax_ = plt.subplots(1, 2, figsize=(11, 4))
    grid = np.arange(0, 15 * 365.25, 30)
    for tr in TRACK_ORDER:
        g = d[d.track.eq(tr)]
        for a, of in ((ax_[0], "done"), (ax_[1], "exit")):
            c = cif_curve(g.t_done, g.kind, of)
            y = c.reindex(c.index.union(grid)).ffill().fillna(0).reindex(grid)
            a.plot(grid / 365.25, y.values, color=TRACK_COLOR[tr], lw=2,
                   label=f"{TRACK_SHORT[tr]} ({_d(len(g))})".replace("{,}", ","))
    ax_[0].set_title("(a) Completed, cumulative incidence (exit competing)", fontsize=9)
    ax_[1].set_title("(b) Exited (withdrawn, expired, cancelled), cumulative incidence", fontsize=9)
    for a in ax_:
        a.set_xlabel("years from entitlement (ministerial: filing)")
        a.set_ylim(0, 1)
    ax_[0].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(CLK_FIG / "fig_outcomes.pdf")
    plt.close(fig)
    # ── macros ──
    tag = {"conditional use": "Cu", "discretionary review": "Dr", "large project authorisation": "Lpa",
           MINISTERIAL: "Min"}
    M = {"ocMatureFrom": str(OC_MATURE[0]), "ocMatureTo": str(OC_MATURE[1]), "ocYears": str(OC_YEARS),
         "ocUnits": _d(len(d)), "ocMature": _d(len(mat))}
    for tr in TRACK_ORDER:
        r = oc[oc.track.eq(tr) & oc.vq.eq("all")]
        if not len(r):
            continue
        r = r.iloc[0]
        M.update({f"ocN{tag[tr]}": _d(r.n), f"ocDone{tag[tr]}": P(r.done, 0), f"ocExit{tag[tr]}": P(r.exit, 0),
                  f"ocOpen{tag[tr]}": P(r.open, 0), f"ocIssued{tag[tr]}": P(r.issued, 0),
                  f"ocUnitsShare{tag[tr]}": P(r.units_share, 0), f"ocMedYears{tag[tr]}": _r(r.med_years, 1),
                  f"ocCif{tag[tr]}": P(r.cif10, 0), f"ocCifExit{tag[tr]}": P(r.cif10_exit, 0),
                  f"ocNever{tag[tr]}": P(1 - r.cif10, 0) if pd.notna(r.cif10) else "---",
                  f"ocUnitsAppr{tag[tr]}": _d(r.units_appr), f"ocUnitsHp{tag[tr]}": _d(r.units_hp)})
        q = oc[oc.track.eq(tr) & oc.vq.ne("all")].dropna(subset=["done"])
        if len(q) >= 2:
            M[f"ocDoneLo{tag[tr]}"] = P(q.done.iloc[0], 0)
            M[f"ocDoneHi{tag[tr]}"] = P(q.done.iloc[-1], 0)
    M["ocFcdCover"] = P(mat.fcd.notna().mean(), 0)
    M["ocDoneHp"] = P(mat.done_source.eq("Housing Production").mean(), 0)
    OC_MACROS.write_text("% GENERATED BY analyze_permit_content.py outcomes --- do not edit by hand.\n" +
                         "\n".join(rf"\newcommand{{\{k}}}{{{v}}}" for k, v in sorted(M.items())) + "\n")
    print(f"{len(d):,} housing units of observation; {len(mat):,} entitled {OC_MATURE[0]}--{OC_MATURE[1]} → {CLK_TAB}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--refresh", action="store_true")
    sub.add_parser("report")
    sub.add_parser("clocks")
    sub.add_parser("outcomes")
    a = ap.parse_args()
    if a.cmd == "fetch":
        fetch(a.refresh)
    elif a.cmd == "clocks":
        clocks_stage()
    elif a.cmd == "outcomes":
        outcomes_stage()
    else:
        report()


if __name__ == "__main__":
    main()
