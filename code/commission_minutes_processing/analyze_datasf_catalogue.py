#!/usr/bin/env python3
"""
analyze_datasf_catalogue.py
---------------------------
Purpose : Everything DataSF's catalogue returns for "planning": every asset, what it is,
          who publishes it, how big and how fresh it is, and whether it carries a join key
          the pipeline uses. Exploratory; the relevance flags are judgement, kept in a
          hand-written CSV beside the memo and marked as judgement there.
          Spec: .claude/instructions/claude_code_brief_conditions_permits_catalogue.md, Task 4.
Inputs  : Socrata Discovery API (api.us.socrata.com/api/catalog/v1), the views and resource
          endpoints on data.sf.gov
          output/planning_commission_project/datasf_planning_catalogue/relevance.csv (judgement)
Outputs : $MFHR_DATA_ROOT/external/datasf_catalogue/catalogue_planning_<date>.json (raw)
          $MFHR_DATA_ROOT/external/datasf_catalogue/enrich/<id>.json           (per asset)
          $MFHR_DATA_ROOT/external/datasf_catalogue/catalogue_planning.csv     (one row per asset)
          output/planning_commission_project/datasf_planning_catalogue/tables/
              datasf_catalogue_{macros,tables}.tex
Author  : Dan Post
Created : 2026-09-11

Usage
-----
  python analyze_datasf_catalogue.py fetch [--refresh]   # the catalogue + one enrichment per asset
  python analyze_datasf_catalogue.py report              # CSV, tables, macros

Notes
-----
The data-acquisition memo recorded that the catalogue indexes `data.sf.gov` and that a
search against `data.sfgov.org` returns nothing. `fetch` re-runs that test with each
parameter separately, because the answer turns out to depend on which parameter names the
host: `search_context` resolves the old alias to the new domain and `domains` alone
returns almost nothing for either. The raw answers are saved so the claim can be checked.

Row counts are `SELECT count(*)` against the resource endpoint for tabular assets --- a
number from the rows, never from the documentation --- and are left blank for charts,
stories and links, which have no rows of their own.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from paths import DATA_ROOT                                          # noqa: E402

MEMO = HERE.parents[1] / "output" / "planning_commission_project" / "datasf_planning_catalogue"
TAB = MEMO / "tables"
RELEVANCE = MEMO / "relevance.csv"
STORE = DATA_ROOT / "external" / "datasf_catalogue"
ENRICH = STORE / "enrich"
CATALOGUE = "https://api.us.socrata.com/api/catalog/v1"
HOST, OLD_HOST = "data.sf.gov", "data.sfgov.org"
QUERY = "planning"
UA = "market-for-housing-regulation/analyze_datasf_catalogue (research; contact danpost@bu.edu)"
PAUSE = 0.6             # under two requests a second on either host
STALE_YEARS = 2
INVENTORY_ID = "y8fp-fbf5"    # DataSF's dataset inventory; Planning's rows are CPC-*


def sess() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    return s


def get(s, url, params=None, tries=6):
    for k in range(tries):
        try:
            r = s.get(url, params=params, timeout=120)
        except requests.RequestException:
            time.sleep(min(120, 2 ** (k + 1)))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(min(120, 2 ** (k + 2)))
            continue
        time.sleep(PAUSE)
        return r
    raise RuntimeError(f"gave up: {url}")


def catalogue_pages(s, params: dict) -> tuple[list, int]:
    """Page to exhaustion in a stable order. The default order is relevance, and paging a
    relevance-ranked list is not stable: on 2026-09-11 two runs returned 273 rows holding
    263 and then 261 distinct assets --- some repeated, some never seen, and not the same
    ones each time. Ordered by id it returns all 273. `fetch` re-measures it."""
    out, off = [], 0
    while True:
        r = get(s, CATALOGUE, {**params, "limit": 100, "offset": off, "order": "dataset_id"})
        j = r.json()
        res = j.get("results", [])
        out += res
        total = j.get("resultSetSize", 0)
        off += len(res)
        if not res or off >= total:
            return out, total


def fetch(refresh: bool, refresh_enrich: bool = False):
    STORE.mkdir(parents=True, exist_ok=True)
    ENRICH.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    raw_f = STORE / f"catalogue_planning_{today}.json"
    s = sess()
    if raw_f.exists() and not refresh:
        raw = json.loads(raw_f.read_text())
    else:
        base = {"search_context": HOST, "domains": HOST, "q": QUERY}
        results, total = catalogue_pages(s, base)
        # The host test, one parameter at a time, keeping each query's id set so `report`
        # can say whether two queries with the same count return the same assets.
        host_test = []
        for lab, p in [("domains + search_context = data.sf.gov", base),
                       ("domains + search_context = data.sfgov.org",
                        {"search_context": OLD_HOST, "domains": OLD_HOST, "q": QUERY}),
                       ("search_context = data.sf.gov only", {"search_context": HOST, "q": QUERY}),
                       ("search_context = data.sfgov.org only",
                        {"search_context": OLD_HOST, "q": QUERY}),
                       ("domains = data.sf.gov only", {"domains": HOST, "q": QUERY}),
                       ("domains = data.sfgov.org only", {"domains": OLD_HOST, "q": QUERY})]:
            res, n = catalogue_pages(s, p)
            host_test.append({"query": lab, "params": p, "resultSetSize": n,
                              "ids": sorted({r["resource"]["id"] for r in res})})
        # Per asset type, as the API counts them, to reconcile with the web UI's 155.
        by_type = {}
        for t in ["datasets", "charts", "maps", "datalenses", "stories", "files", "hrefs",
                  "filters", "calendars", "forms", "measures", "visualizations",
                  "federated_hrefs", "links", "apis"]:
            r = get(s, CATALOGUE, {**base, "only": t, "limit": 1})
            j = r.json()
            by_type[t] = j.get("resultSetSize") if "resultSetSize" in j else \
                f"error: {j.get('error', r.status_code)}"
        raw = {"retrieved": dt.datetime.now().isoformat(timespec="seconds"), "query": base,
               "resultSetSize": total, "results": results, "host_test": host_test,
               "by_type_only": by_type}
        raw_f.write_text(json.dumps(raw, indent=1) + "\n")
        print(f"catalogue: {total} results for q={QUERY!r} → {raw_f.name}")
    # The datasets facet's own member list, saved so `report` can check that the facet
    # is exactly the results typed "dataset" rather than assume it. (Paged in relevance
    # order the two looked different; paged by id they are the same set.)
    if "datasets_facet_ids" not in raw:
        res, n = catalogue_pages(s, {**raw["query"], "only": "datasets"})
        raw["datasets_facet_ids"] = sorted({r["resource"]["id"] for r in res})
        raw_f.write_text(json.dumps(raw, indent=1) + "\n")
    # The paging hazard, measured rather than remembered: the same query paged in the
    # default (relevance) order, and how many distinct assets that actually returns.
    if "relevance_paging" not in raw:
        out, off = [], 0
        while True:
            r = get(s, CATALOGUE, {**raw["query"], "limit": 100, "offset": off})
            res = r.json().get("results", [])
            out += [x["resource"]["id"] for x in res]
            off += len(res)
            if not res or off >= r.json().get("resultSetSize", 0):
                break
        raw["relevance_paging"] = {"rows": len(out), "distinct": len(set(out)),
                                   "measured": dt.datetime.now().isoformat(timespec="seconds")}
        raw_f.write_text(json.dumps(raw, indent=1) + "\n")
    # The city's own dataset inventory lists what departments hold, published or not. It is
    # where a Commission-actions table would appear if one existed anywhere, so Planning's
    # rows are kept whole.
    inv_f = STORE / "inventory_planning.json"
    if not inv_f.exists() or refresh:
        r = get(s, f"https://{HOST}/resource/{INVENTORY_ID}.json", {"$limit": 50000})
        rows = [x for x in r.json() if str(x.get("inventory_id", "")).startswith("CPC-")]
        inv_f.write_text(json.dumps({"retrieved": dt.datetime.now().isoformat(timespec="seconds"),
                                     "id": INVENTORY_ID, "rows": rows}, indent=1) + "\n")
    # one enrichment per asset: the view's own metadata, and a row count where it has rows
    for k, r in enumerate(raw["results"], 1):
        rid = r["resource"]["id"]
        f = ENRICH / f"{rid}.json"
        if f.exists() and not refresh_enrich:
            continue
        e = {"id": rid}
        try:
            v = get(s, f"https://{HOST}/api/views/{rid}.json")
            vj = v.json() if v.status_code == 200 else {}
            e["view_status"] = v.status_code
            e["view"] = {k2: vj.get(k2) for k2 in ("viewType", "displayType", "assetType",
                                                   "rowsUpdatedAt", "createdAt",
                                                   "publicationDate", "modifyingViewUid",
                                                   "attribution", "category", "provenance")}
            e["columns"] = [{"name": c.get("fieldName"), "type": c.get("dataTypeName")}
                            for c in vj.get("columns", []) or []]
        except Exception as ex:
            e["view_error"] = f"{type(ex).__name__}: {ex}"
        if r["resource"].get("type") in ("dataset", "filter", "map", "datalens") and \
                r["resource"].get("lens_view_type", "tabular") == "tabular":
            try:
                c = get(s, f"https://{HOST}/resource/{rid}.json", {"$select": "count(*)"})
                e["count_status"] = c.status_code
                if c.status_code == 200:
                    cj = c.json()
                    e["rows"] = int(list(cj[0].values())[0]) if cj else 0
                else:
                    e["count_error"] = c.text[:200]
            except Exception as ex:
                e["count_error"] = f"{type(ex).__name__}: {ex}"
        f.write_text(json.dumps(e, indent=1) + "\n")
        if k % 25 == 0:
            print(f"  enriched {k}/{len(raw['results'])}", flush=True)


# ═══════════════════════════════════════════════════════════════════════════
# report
# ═══════════════════════════════════════════════════════════════════════════
TYPE_LABEL = {"dataset": "dataset", "map": "map", "filter": "filtered view",
              "href": "external link", "story": "story", "file": "file", "chart": "chart",
              "datalens": "data lens", "calendar": "calendar", "form": "form"}
GEOM = {"point", "multipoint", "line", "multiline", "linestring", "multilinestring",
        "polygon", "multipolygon", "location"}
# The pipeline's join keys, recognised by column name. A name is a claim about the rows,
# not proof, and the table says "by name".
KEY_RX = {
    "blklot": re.compile(r"(?i)^(blklot|block_lot|blocklot|mapblklot|parcel_number|apn|"
                         r"parcel|parcel_id|block_and_lot)$"),
    "block+lot": None,
    # `b1_alt_id` is Accela's name for the record number (2019-013953CUA); PermitSF calls it
    # `recordno`. Both were missed by the first version of this pattern, which also matched
    # `project_id_add` on the parcel layer --- the recorder's mapping project, not a case.
    "case/record": re.compile(r"(?i)(case_?no|case_?number|record_?id|record_?number|recordno|"
                              r"b1_alt_id|alt_id|planning_case|ppts|prj_?id)"),
    "permit": re.compile(r"(?i)(permit_?(no|num|number)|building_permit|bpa|"
                         r"application_?number|permit_application)"),
    "date": None,
}


def _dept(r) -> str:
    for m in r["classification"].get("domain_metadata", []):
        if m["key"] == "Department-Metrics_Publishing-Department" and m["value"].strip():
            return m["value"].strip()
    return (r["resource"].get("attribution") or "").strip() or "(not stated)"


def _meta(r, key) -> str:
    for m in r["classification"].get("domain_metadata", []):
        if m["key"] == key:
            return (m["value"] or "").strip()
    return ""


def assets() -> tuple[pd.DataFrame, dict]:
    raw_f = sorted(STORE.glob("catalogue_planning_*.json"))[-1]
    raw = json.loads(raw_f.read_text())
    cached = cached_ids()
    rows = []
    for r in raw["results"]:
        res = r["resource"]
        rid = res["id"]
        e = json.loads((ENRICH / f"{rid}.json").read_text()) if (ENRICH / f"{rid}.json").exists() \
            else {}
        cols = [c["name"] for c in e.get("columns", []) if c.get("name")] or \
            list(res.get("columns_field_name") or [])
        types = [str(c.get("type") or "").lower() for c in e.get("columns", [])] or \
            [str(t).lower() for t in res.get("columns_datatype") or []]
        lc = {c.lower() for c in cols}
        keys = []
        matched = [c for c in cols if KEY_RX["blklot"].match(c) or
                   KEY_RX["case/record"].search(c) or KEY_RX["permit"].search(c)]
        if any(KEY_RX["blklot"].match(c) for c in cols):
            keys.append("blklot")
        if ({"block", "lot"} <= lc or {"block_num", "lot_num"} <= lc or
                {"blk", "lot"} <= lc):
            keys.append("block+lot")
        if any(KEY_RX["case/record"].search(c) for c in cols):
            keys.append("case/record")
        if any(KEY_RX["permit"].search(c) for c in cols):
            keys.append("permit")
        if any(t in ("calendar_date", "date", "floating_timestamp", "fixed_timestamp")
               for t in types) or any(re.search(r"(?i)date", c) for c in cols):
            keys.append("date")
        geom = sorted({t for t in types if t in GEOM})
        updated = res.get("data_updated_at") or res.get("updatedAt") or ""
        rows.append({
            "id": rid, "name": res.get("name", ""), "type": TYPE_LABEL.get(res.get("type"),
                                                                             res.get("type")),
            "parent": ";".join(res.get("parent_fxf") or []),
            "department": _dept(r), "attribution": res.get("attribution") or "",
            "category": r["classification"].get("domain_category") or "",
            "tags": ";".join(r["classification"].get("domain_tags") or []),
            "created": (res.get("createdAt") or "")[:10], "updated": updated[:10],
            "frequency": _meta(r, "Publishing-Details_Publishing-frequency") or
                         _meta(r, "Publishing-Details_Data-change-frequency"),
            "rows": e.get("rows"), "n_columns": len(cols), "columns": ";".join(cols),
            "geometry": ";".join(geom),
            "description": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ",
                                                      res.get("description") or "")).strip()[:300],
            "join_keys": ";".join(keys), "key_columns": ";".join(matched),
            "cached": rid in cached,
            "permalink": r.get("permalink", "")})
    df = pd.DataFrame(rows)
    df["rows"] = pd.to_numeric(df.rows, errors="coerce")
    return df, raw


def cached_ids() -> set[str]:
    """What the pipeline already caches: every Socrata source `acquire_external_data.py`
    fetches (its Table 1 and its polygon layers) plus DBI, which `analyze_permits.py` does."""
    import acquire_external_data as ax
    ids = {s["id"] for s in ax.SOCRATA_SOURCES.values()}
    ids |= {i for fam in ax.POLYGON_LAYERS.values() for i in fam.values()}
    ids |= {"i98e-djp9"}
    return ids


def T(s) -> str:
    return (str(s).replace("\\", r"\textbackslash{}").replace("&", r"\&").replace("%", r"\%")
            .replace("$", r"\$").replace("#", r"\#").replace("_", r"\_").replace("{", r"\{")
            .replace("}", r"\}").replace("~", r"\textasciitilde{}").replace("^", r"\^{}")
            # A cell that opens with "[ARCHIVED]" follows a row break, and LaTeX reads
            # `\\[ARCHIVED]` as a break with an optional length: brackets go in braces.
            .replace("[", "{[}").replace("]", "{]}"))


def N(x) -> str:
    return f"{int(x):,}".replace(",", "{,}")


def geom_label(g: str) -> str:
    """Socrata's `location` type is a point with an address; multi-parts are still the shape."""
    kinds = {x.replace("multi", "").replace("location", "point").replace("linestring", "line")
             for x in str(g).split(";") if x}
    return ";".join(sorted(kinds))


def name_label(n: str) -> str:
    """An asset name with break points where it has no spaces to break at: camel case
    ("NeighborhoodQuadrants"), underscores and dots ("MTA.equitystrategy_nhoods_data"), and a
    run of digits after letters ("HistoricZoningDistricts2008")."""
    t = T(n)
    t = re.sub(r"(?<=[a-z])(?=[A-Z])", r"\\allowbreak{}", t)
    t = re.sub(r"(?<=[A-Za-z])(?=\d{4})", r"\\allowbreak{}", t)
    return t.replace(r"\_", r"\_\allowbreak{}").replace(".", r".\allowbreak{}")


def keys_label(k: str) -> str:
    return T(str(k).replace(";", ", ")).replace("/", r"/\allowbreak ")


def report():
    TAB.mkdir(parents=True, exist_ok=True)
    df, raw = assets()
    rel = pd.read_csv(RELEVANCE, dtype=str).fillna("") if RELEVANCE.exists() else \
        pd.DataFrame(columns=["id", "flag", "reason"])
    df = df.merge(rel[["id", "flag", "reason"]], on="id", how="left").fillna(
        {"flag": "unassessed", "reason": ""})
    df.to_csv(STORE / "catalogue_planning.csv", index=False)
    retrieved = raw["retrieved"][:10]
    cutoff = (pd.Timestamp(retrieved) - pd.DateOffset(years=STALE_YEARS)).date().isoformat()
    df["stale"] = df.updated.ne("") & (df.updated < cutoff)
    ht = {h["query"]: h for h in raw["host_test"]}
    same = set(ht["domains + search_context = data.sf.gov"]["ids"]) == \
        set(ht["domains + search_context = data.sfgov.org"]["ids"])
    bt = raw["by_type_only"]

    m = {"catRetrieved": retrieved, "catTotal": N(len(df)),
         "catApiTotal": N(raw["resultSetSize"]),
         "catDatasets": N(bt["datasets"]), "catMaps": N(bt["maps"]),
         "catFilters": N(bt["filters"]), "catLinks": N(bt["hrefs"]),
         "catStories": N(bt["stories"]), "catFiles": N(bt["files"]),
         "catCharts": N(bt["charts"]),
         "catNonDatasets": N(len(df) - bt["datasets"]),
         "catOldHostCount": N(ht["domains + search_context = data.sfgov.org"]["resultSetSize"]),
         "catOldHostSameIds": "the same" if same else "a different set of",
         "catDomainsOnly": N(ht["domains = data.sf.gov only"]["resultSetSize"]),
         "catDomainsOnlyOld": N(ht["domains = data.sfgov.org only"]["resultSetSize"]),
         "catDepartments": N(df.department.nunique()),
         "catPlanningDept": N(int(df.department.eq("Planning").sum())),
         "catCached": N(int(df.cached.sum())),
         "catStale": N(int(df.stale.sum())), "catStaleCutoff": cutoff,
         "catStaleYears": str(STALE_YEARS),
         "catWithRows": N(int(df.rows.notna().sum())),
         "catRowsTotal": N(int(df.rows.fillna(0).sum())),
         "catCore": N(int(df.flag.eq("core").sum())),
         "catPossible": N(int(df.flag.eq("possibly useful").sum())),
         "catNotRelevant": N(int(df.flag.eq("not relevant").sum())),
         "catUnassessed": N(int(df.flag.eq("unassessed").sum())),
         "catPossibleNotCached": N(int((df.flag.isin(["core", "possibly useful"])
                                        & ~df.cached).sum())),
         "catKeyBlklot": N(int(df.join_keys.str.contains("blklot").sum())),
         "catKeyBlockLot": N(int(df.join_keys.str.contains(r"block\+lot").sum())),
         "catKeyCase": N(int(df.join_keys.str.contains("case/record").sum())),
         "catKeyPermit": N(int(df.join_keys.str.contains("permit").sum())),
         "catKeyAny": N(int(df.join_keys.str.contains("blklot|block|case|permit").sum())),
         "catGeom": N(int(df.geometry.ne("").sum()))}
    (TAB / "datasf_catalogue_macros.tex").write_text(
        "% GENERATED BY analyze_datasf_catalogue.py --- do not edit by hand.\n" +
        "\n".join(rf"\newcommand{{\{k}}}{{{v}}}" for k, v in sorted(m.items())) + "\n")
    inv = json.loads((STORE / "inventory_planning.json").read_text())["rows"]
    iv = pd.DataFrame(inv)
    st = iv.publishing_status.fillna("(blank)").value_counts()
    coa = iv[(iv.dataset_name.fillna("") + " " + iv.dataset_description.fillna("")).str.contains(
        r"(?i)planning commission|commission action|motion|conditions? of approval", regex=True)]
    rp = raw.get("relevance_paging", {})
    pipe_int = iv[iv.dataset_name.fillna("").str.contains("Development Pipeline") &
                  iv.publishing_status.ne("Published")]
    m2 = {"catRelPagingRows": N(rp.get("rows", 0)), "catRelPagingDistinct": N(rp.get("distinct", 0)),
          "catInvPipelineInternal": N(len(pipe_int)),
          "catInvPlanning": N(len(iv)), "catInvPublished": N(int(st.get("Published", 0))),
          "catInvUnpublished": N(len(iv) - int(st.get("Published", 0))),
          "catInvCommission": N(len(coa))}
    with (TAB / "datasf_catalogue_macros.tex").open("a") as fh:
        fh.write("\n".join(rf"\newcommand{{\{k}}}{{{v}}}" for k, v in sorted(m2.items())) + "\n")
    write_tables(df, raw, ht, iv)
    print(f"{len(df)} assets; {int(df.cached.sum())} cached; flags "
          f"{df.flag.value_counts().to_dict()} → {TAB}")


def write_tables(df: pd.DataFrame, raw: dict, ht: dict, iv: pd.DataFrame):
    L = ["% GENERATED BY analyze_datasf_catalogue.py --- do not edit by hand."]
    a = L.append
    bt = raw["by_type_only"]

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The catalogue's answer to \texttt{q=planning}, by asset type. `Facet' is "
      r"the API's own count with \texttt{only=} set to that type; `results' counts the "
      r"assets returned when the full result set is paged in a stable order. The two agree "
      r"type by type.}\label{tab:types}")
    a(r"\begin{tabular}{lrr}\toprule")
    a(r"Asset type & Facet count & Results\\\midrule")
    tc = df.type.value_counts()
    for key, lab in (("datasets", "dataset"), ("maps", "map"), ("filters", "filtered view"),
                     ("hrefs", "external link"), ("stories", "story"), ("files", "file"),
                     ("charts", "chart")):
        a(rf"{lab} & {N(bt[key])} & {N(tc.get(lab, 0))}\\")
    a(rf"\midrule All & & {N(len(df))}\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Which parameter names the host decides what the catalogue returns. Each row "
      r"is the same query, \texttt{q=planning}, with the host passed differently.}"
      r"\label{tab:hosttest}")
    a(r"\begin{tabular}{lr}\toprule")
    a(r"Query & Results\\\midrule")
    for h in raw["host_test"]:
        a(rf"\texttt{{{T(h['query'])}}} & {N(h['resultSetSize'])}\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{By publishing department and by type. `Stale' is not updated in the "
      r"%d years before retrieval; `rows' sums \texttt{count(*)} over the tabular assets. "
      r"Departments with fewer than three assets are pooled.}\label{tab:depts}" % STALE_YEARS)
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{lrrrrrr}\toprule")
    a(r"Department & Assets & Datasets & Maps & Other & Stale & Rows\\\midrule")
    d = df.copy()
    small = d.department.map(d.department.value_counts()) < 3
    d.loc[small, "department"] = "(departments with fewer than three)"
    order = d.department.value_counts().index
    for dep in order:
        g = d[d.department == dep]
        a(rf"{T(dep)} & {len(g)} & {int(g.type.eq('dataset').sum())} & "
          rf"{int(g.type.eq('map').sum())} & "
          rf"{int((~g.type.isin(['dataset', 'map'])).sum())} & {int(g.stale.sum())} & "
          rf"{N(g.rows.fillna(0).sum())}\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")

    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{By the catalogue's own category.}\label{tab:cats}")
    a(r"\begin{tabular}{lrr}\toprule")
    a(r"Category & Assets & of which datasets\\\midrule")
    for cat, g in df.assign(category=df.category.replace("", "(none)")).groupby("category"):
        a(rf"{T(cat)} & {len(g)} & {int(g.type.eq('dataset').sum())}\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    pos = df[df.flag.isin(["core", "possibly useful"]) & ~df.cached].sort_values(
        ["flag", "department", "name"])
    a(r"\begin{longtable}{@{}L{3.2cm}L{1.95cm}R{1.6cm}L{1.8cm}L{5.2cm}@{}}")
    a(r"\caption{Flagged \emph{core} or \emph{possibly useful} and not already cached. The "
      r"flag and the reason are judgement.}\label{tab:possible}\\\toprule")
    a(r"Asset & Identifier & Rows & Keys (by name) & Why\\\midrule\endfirsthead")
    a(r"\toprule Asset & Identifier & Rows & Keys (by name) & Why\\\midrule\endhead")
    for r in pos.itertuples():
        a(rf"{name_label(r.name)} \emph{{({T(r.flag)})}} & \texttt{{{T(r.id)}}} & "
          rf"{N(r.rows) if pd.notna(r.rows) else '---'} & {keys_label(r.join_keys)} & "
          rf"{T(r.reason)}\\")
    a(r"\bottomrule\end{longtable}")
    a("")

    st = df[df.stale].sort_values("updated")
    a(r"\begin{longtable}{@{}L{5.3cm}L{1.95cm}L{2.0cm}L{1.8cm}L{2.6cm}@{}}")
    a(r"\caption{Stale: not updated in the %d years before retrieval, oldest "
      r"first.}\label{tab:stale}\\\toprule" % STALE_YEARS)
    a(r"Asset & Identifier & Type & Last update & Department\\\midrule\endfirsthead")
    a(r"\toprule Asset & Identifier & Type & Last update & Department\\\midrule\endhead")
    for r in st.itertuples():
        a(rf"{name_label(r.name)} & \texttt{{{T(r.id)}}} & {T(r.type)} & {r.updated} & "
          rf"{T(r.department)}\\")
    a(r"\bottomrule\end{longtable}")
    a("")

    # Nine columns: 4pt separators and a small face, or the table runs past the margin.
    a(r"{\small\setlength{\tabcolsep}{4pt}")
    a(r"\begin{longtable}{@{}L{3.1cm}L{1.75cm}L{1.3cm}R{1.45cm}R{0.7cm}L{1.15cm}L{1.25cm}"
      r"L{1.75cm}L{0.55cm}@{}}")
    a(r"\caption{The full catalogue, grouped by department. `Upd.' is the last data update; "
      r"`Cols' the column count; `Keys' the pipeline join keys a column name suggests; "
      r"`Flag' is judgement (c core, p possibly useful, n not relevant); a dagger marks a "
      r"source the pipeline already caches.}\label{tab:full}\\\toprule")
    hdr = (r"Asset & Identifier & Type & Rows & Col. & Upd. & Geom. & Keys & Fl.\\"
           r"\midrule")
    a(hdr + r"\endfirsthead")
    a(r"\toprule " + hdr + r"\endhead")
    short = {"core": "c", "possibly useful": "p", "not relevant": "n", "unassessed": "?"}
    for dep in df.department.value_counts().index:
        g = df[df.department == dep].sort_values(["type", "name"])
        a(rf"\multicolumn{{9}}{{@{{}}l}}{{\textbf{{{T(dep)}}} ({len(g)})}}\\")
        for r in g.itertuples():
            a(rf"{name_label(r.name)}{'$^{\\dagger}$' if r.cached else ''} & \texttt{{{T(r.id)}}} & "
              rf"{T(r.type)} & {N(r.rows) if pd.notna(r.rows) else '---'} & {r.n_columns} & "
              rf"{r.updated[:7]} & {T(geom_label(r.geometry))} & "
              rf"{keys_label(r.join_keys)} & {short.get(r.flag, '?')}\\")
    a(r"\bottomrule\end{longtable}}")
    a("")
    rows_of = dict(zip(df.id, df.rows))
    rp = raw.get("relevance_paging", {})
    coa = iv[(iv.dataset_name.fillna("") + " " + iv.dataset_description.fillna("")).str.contains(
        r"(?i)planning commission|commission action|motion|conditions? of approval", regex=True)]
    pipe_int = iv[iv.dataset_name.fillna("").str.contains("Development Pipeline") &
                  iv.publishing_status.ne("Published")]
    neg = [
        ("A dataset of Commission actions, motions or conditions",
         rf"all {N(len(df))} catalogue results; Planning's {N(len(iv))} entries in the "
         rf"dataset inventory, published or not",
         "none" + ("" if not len(coa) else
                   rf"; the inventory entr{'y' if len(coa) == 1 else 'ies'} that use the words: "
                   + "; ".join(T(x) for x in coa.dataset_name))),
        ("The catalogue by the old host name",
         r"\texttt{search\_context} or \texttt{domains} = \texttt{data.sfgov.org}",
         rf"{N(ht['domains + search_context = data.sfgov.org']['resultSetSize'])} with "
         rf"\texttt{{search\_context}}, the same assets as the new host; "
         rf"{N(ht['domains = data.sfgov.org only']['resultSetSize'])} with \texttt{{domains}} "
         r"alone, as for the new host --- not zero"),
        ("Every result, by paging the default order", "the Discovery API, relevance order",
         rf"{N(rp.get('rows', 0))} rows holding {N(rp.get('distinct', 0))} distinct assets; "
         r"page in \texttt{order=dataset\_id}"),
        ("Planning permitting data", r"\texttt{kncr-c6jw} [Deprecated]",
         rf"{N(rows_of.get('kncr-c6jw', 0) or 0)} rows; its description names the two records "
         r"tables, both cached, as its replacement"),
        ("Historic resources", r"\texttt{njrt-gtwr} [Deprecated]",
         rf"{N(rows_of.get('njrt-gtwr', 0) or 0)} rows; its description names "
         rf"\texttt{{3tsw-4idn}} as its replacement"),
        ("Extra zoning vintages", r"\texttt{5uzm-v8n8} (March 2005), \texttt{cfhj-8c6b} (April 2000)",
         rf"{N(rows_of.get('5uzm-v8n8') or 0)} and {N(rows_of.get('cfhj-8c6b') or 0)} rows, "
         rf"the row counts of the cached 2005 and 2000 vintages "
         rf"({N(rows_of.get('b52k-gy2v') or 0)}, {N(rows_of.get('itx2-wzp5') or 0)}); "
         r"probably duplicates; compared on row count only"),
        ("A published history of the Development Pipeline", r"the dataset inventory",
         rf"{N(len(pipe_int))} quarterly snapshots are listed as held and not published"),
    ]
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Negative results. A row here is a search that was run and is not to be run "
      r"again.}\label{tab:negatives}")
    a(r"\begin{tabular}{@{}L{3.6cm}L{4.6cm}L{6.0cm}@{}}\toprule")
    a(r"Sought & Where & Result\\\midrule")
    for w_, wh, res in neg:
        a(rf"{w_} & {wh} & {res}\\[2pt]")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")
    un = iv[iv.publishing_status.ne("Published")].sort_values(["publishing_status", "inventory_id"])
    a(r"\begin{longtable}{@{}L{1.6cm}L{1.9cm}L{4.2cm}L{6.6cm}@{}}")
    a(r"\caption{What the Planning Department lists in the city's dataset inventory "
      r"(\texttt{%s}) and has not published. Descriptions are the inventory's own, cut to "
      r"their first sentence or so.}\label{tab:inventory}\\\toprule" % INVENTORY_ID)
    a(r"Inventory & Status & Dataset & What the inventory says\\\midrule\endfirsthead")
    a(r"\toprule Inventory & Status & Dataset & What the inventory says\\\midrule\endhead")
    for r in un.itertuples():
        desc = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(r.dataset_description or "")))
        desc = re.sub(r"^\s*A\. SUMMARY\s*", "", desc).strip()
        a(rf"\texttt{{{T(r.inventory_id)}}} & {T(r.publishing_status)} & {T(r.dataset_name)} & "
          rf"{T(desc[:170]) + ('\\dots' if len(desc) > 170 else '')}\\")
    a(r"\bottomrule\end{longtable}")
    (TAB / "datasf_catalogue_tables.tex").write_text("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--refresh", action="store_true", help="re-query the catalogue")
    f.add_argument("--refresh-enrich", action="store_true", help="re-query every asset too")
    sub.add_parser("report")
    a = ap.parse_args()
    if a.cmd == "fetch":
        fetch(a.refresh, a.refresh_enrich)
    else:
        report()


if __name__ == "__main__":
    main()
