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
        top = s.astype(str).value_counts().head(3)
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
    return pd.DataFrame(rows), {"n_rows": len(db), "n_cols": db.shape[1],
                                "n_system": int(sum(c.startswith(":") for c in db.columns))}


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
    p["t_start"] = (p.first_construction_document_date - p.issued_date).dt.days
    p["t_done"] = (p.completed_date - p.issued_date).dt.days
    p["t_total"] = (p.completed_date - p.filed_date).dt.days
    p["issued"] = p.issued_date.notna()
    p["started"] = p.first_construction_document_date.notna()
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
            ts = g.loc[g.t_start >= 0, "t_start"]
            td = g.loc[g.t_done >= 0, "t_done"]
            out.append({"type": lab, "decade": dl, "n": len(g), "issued": g.issued.mean(),
                        "issue_med": ti.median(), "issue_p90": ti.quantile(.9),
                        "started": g.started.mean(), "start_med": ts.median(),
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


# ── the report ───────────────────────────────────────────────────────────────
def describe(s: pd.Series) -> dict:
    s = s.dropna()
    return {"n": len(s), "p25": s.quantile(.25), "med": s.median(), "p75": s.quantile(.75),
            "p90": s.quantile(.9), "neg": (s < 0).mean() if len(s) else np.nan}


def report():
    FIG.mkdir(parents=True, exist_ok=True)
    TAB.mkdir(parents=True, exist_ok=True)
    meta = json.loads(FULL.with_name("dbi_permits_full_meta.json").read_text())
    retrieved = pd.Timestamp(meta["retrieved"][:10])
    raw = pd.read_csv(FULL, dtype=str, low_memory=False)
    inv, inv_meta = field_inventory(raw, retrieved)
    d = typed(raw)
    del raw
    p, col = collapse(d)
    p = clocks(p)
    ct = clock_table(p)
    rules = universe(p)
    p["dev"] = rules.any(axis=1) & p.permit_type.ne("8")
    sens = {}
    for q in COST_Q_ALT:
        r_ = universe(p, q=q)
        sens[f"top {100*(1-q):.0f}\\%"] = r_.any(axis=1) & p.permit_type.ne("8")
    r_ = universe(p, nominal=COST_NOMINAL)
    sens[f"nominal \\${COST_NOMINAL/1e6:.0f}M"] = r_.any(axis=1) & p.permit_type.ne("8")

    cache = FULL.with_name("_permit_content_linkage.pkl")
    if cache.exists() and cache.stat().st_mtime > FULL.stat().st_mtime:
        L = pd.read_pickle(cache)
    else:                           # the tiers take minutes; cache them beside the DBI file
        L = linkage(d, p)
        pd.to_pickle(L, cache)
    it, lk = L["items"], L["links"]
    p = tier_flags(p, lk)
    p["net_units"] = p.proposed_units - p.existing_units
    p["ubin"] = p.proposed_units.map(unit_bin)
    p["log_cost"] = np.log1p(p.estimated_cost.clip(lower=0))
    p["log_rcost"] = np.log1p(p.revised_cost.clip(lower=0))

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
    for c in ("issued", "completed", "exited", "started"):
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
            ("started", "outcome: construction started"),
            ("completed", "outcome: completed"), ("exited", "outcome: expired or withdrawn")]
    cmp, sup1, sup2 = comparison(comp_base, VARS)
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

    ctx = dict(inv=inv, inv_meta=inv_meta, col=col, ct=ct, rules=rules, p=p, dev=dev,
               sens=sens, lk=lk, it=it, cmp=cmp, sup1=sup1, sup2=sup2, by_units=by_units,
               drt=drt, drc=drc, cut=cut, cuc=cuc, mn=mn, mnc=mnc, tsf=tsf, dclk=dclk,
               comp_base=comp_base, meta=meta)
    figures(ctx)
    write_tables(ctx)
    write_macros(ctx)
    print(f"permits {len(p):,}; dev universe {int(p.dev.sum()):,}; linked "
          f"{int(dev.linked.sum()):,}; T3 only {int(dev.t3_only.sum()):,} → {TAB}")


def comparison_small(g: pd.DataFrame, cell: list[str] | None = None) -> dict:
    """Outcomes and scale for a two-group comparison, raw and within the cells given
    (default type x year bin)."""
    g = g.copy()
    g["log_cost"] = np.log1p(g.estimated_cost.clip(lower=0))
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
    g["log_cost"] = np.log1p(g.estimated_cost.clip(lower=0))
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
    a(r"\begin{tabular}{llrrrrrrrrr}\toprule")
    a(r"Type & Filed & Permits & Issued & \multicolumn{2}{c}{Filed $\to$ issued} & Started & "
      r"Issued $\to$ start & Completed & Issued $\to$ done & Exited\\")
    a(r"\cmidrule(lr){5-6} & & & & median & p90 & & median & & median & \\\midrule")
    last = None
    for r in ctx["ct"]:
        lab = "" if r["type"] == last else T(r["type"])
        last = r["type"]
        a(rf"{lab} & {r['decade']} & {f0(r['n'])} & {P(r['issued'])}\% & {f0(r['issue_med'])} & "
          rf"{f0(r['issue_p90'])} & {P(r['started'])}\% & {f0(r['start_med'])} & "
          rf"{P(r['completed'])}\% & {f0(r['done_med'])} & {P(r['exited'])}\%\\")
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
    }
    (TAB / "permits_content_macros.tex").write_text(
        "% GENERATED BY analyze_permit_content.py --- do not edit by hand.\n" +
        "\n".join(rf"\newcommand{{\{k}}}{{{v}}}" for k, v in sorted(m.items())) + "\n")
    print("→", TAB / "permits_content_macros.tex")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--refresh", action="store_true")
    sub.add_parser("report")
    a = ap.parse_args()
    if a.cmd == "fetch":
        fetch(a.refresh)
    else:
        report()


if __name__ == "__main__":
    main()
