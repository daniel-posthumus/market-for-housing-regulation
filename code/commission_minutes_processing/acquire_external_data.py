#!/usr/bin/env python3
"""
acquire_external_data.py
------------------------
Purpose : Find, verify and cache the five categories of external data the item panel needs
          but does not have --- a parcel risk set (the denominator the filing decision is
          modelled on), filing dates and the conditional-use-to-permit bridge, project
          scale, prices, and fee schedules --- and measure, for each, whether it actually
          joins to the item table. Spec: .claude/instructions/data_acquisition_brief.md.
Inputs  : $MFHR_DATA_ROOT/extraction/<RUN>/clean/*.jsonl              (the item table)
          $MFHR_DATA_ROOT/external/datasf/dbi_permits.csv.gz          (analyze_permits' cache)
          DataSF/Socrata (data.sf.gov), Zillow Research, sfplanning.org, the Wayback Machine
Outputs : $MFHR_DATA_ROOT/external/_acquisition/probe.json            (resolved identifiers)
          $MFHR_DATA_ROOT/external/{parcels,assessor,zoning,planning_records,pipeline,
                                    prices,fees}/…                    (the caches)
          $MFHR_DATA_ROOT/external/README.md                          (per-source provenance)
          output/planning_commission_project/data_acquisition/figures/*.pdf
          output/planning_commission_project/data_acquisition/tables/acquisition_tables.tex
          output/planning_commission_project/data_acquisition/tables/acquisition_macros.tex
Author  : Dan Post
Created : 2026-09-08

Usage
-----
  python acquire_external_data.py probe                  # resolve every identifier, live
  python acquire_external_data.py fetch  [--only NAME]   # download + cache (--refresh to redo)
  python acquire_external_data.py report                 # joins, figures, tables, README

Notes
-----
Three cached stages, like `analyze_conditions.py`, so no network stage repeats by accident.
`probe` is cheap and re-runnable; `fetch` skips anything already on disk unless `--refresh`;
`report` is offline apart from nothing at all.

Two identifier facts the brief could not know in advance, both found by `probe` and both
worth stating because they will bite anyone reusing the older scripts:

  * DataSF's Socrata domain is now **data.sf.gov**. `data.sfgov.org` still answers the
    resource endpoint (which is why `analyze_permits.py` keeps working), but the *catalogue*
    API only indexes the new domain, so a search against the old one returns zero hits and
    looks like "the dataset does not exist".
  * The assessor roll (`wv5m-vpq2`, the ID the brief proposed) is correct, but it starts at
    roll year 2007, not 1998. That is a cap on the risk set, not a scrape artefact.

Historic zoning is published two ways and only one of them is usable here: 1998--2008 are
*parcel-keyed* tables that join on `blklot` directly; 2006 and 2009--2015 are polygon layers
that need a spatial join. We fetch the first group and inventory the second, rather than
pretending a polygon layer is a panel. 1999 is not published at all.
"""
from __future__ import annotations

import argparse
import glob
import gzip
import io
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from paths import DATA_ROOT                                          # noqa: E402

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                      # noqa: E402

RUN = "corpus_v2_g3"
MEMO = HERE.parents[1] / "output" / "planning_commission_project" / "data_acquisition"
FIG, TAB = MEMO / "figures", MEMO / "tables"
EXT = DATA_ROOT / "external"
PROBE_DIR = EXT / "_acquisition"
PROBE_JSON = PROBE_DIR / "probe.json"
DBI_CACHE = EXT / "datasf" / "dbi_permits.csv.gz"

# The catalogue and the resource endpoint live on the same host; `data.sfgov.org` is a
# working alias for the second but not the first (see the module docstring).
SOCRATA_HOST = "data.sf.gov"
CATALOGUE = "https://api.us.socrata.com/api/catalog/v1"
UA = "market-for-housing-regulation/acquire_external_data (research)"
PAGE = 50_000
PAUSE = 0.6

plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                     "figure.dpi": 150, "savefig.bbox": "tight"})
SMOOTH_YEARS = 3
MIN_RATE_N = 20          # fewest items a year needs before a rate is drawn for it
MIN_CASES_FIG = 10       # …and the same, for a rate computed over cases rather than items
MIN_ITEMS_BRIDGE = 100   # fewest items a request type needs to get a row in the bridge table
SCALE_ERA = 2005         # the year every purpose-built scale dataset starts; the split for
                         # asking whether an *earlier* hearing gets a unit count at all
LAG_TRUNC_DAYS = 1500    # display truncation for the filing-lag histogram; stats are untruncated
# The oldest Wayback snapshot of the fee register's single URL. Recorded rather than
# recomputed: the CDX query that found it is a search, and the search is the result.
FEE_EARLIEST_SNAPSHOT = "2019-09-15"


# ═══════════════════════════════════════════════════════════════════════════
# the source registry
# ═══════════════════════════════════════════════════════════════════════════
# `select` None means "every column the view declares, minus geometry" (resolved at fetch
# time from the views API, so a column added upstream is picked up rather than dropped).
# `keep_rx` narrows that further by column name, which is how the historic zoning tables ---
# up to 128 columns of parcel attributes we do not want --- are kept to a sane size.
SOCRATA_SOURCES: dict[str, dict] = {
    # ── 2.1 the parcel risk set ──────────────────────────────────────────
    "parcels": dict(
        id="acdm-wktn", dir="parcels", file="parcels_active_retired.csv.gz",
        label="Parcels --- Active and Retired", category="Parcel risk set",
        select=("mapblklot,blklot,block_num,lot_num,active,in_asr_secured_roll,"
                "date_rec_add,date_rec_drop,date_map_add,date_map_drop,"
                "zoning_code,zoning_district,supervisor_district,analysis_neighborhood,"
                "planning_district,from_address_num,to_address_num,street_name,street_type,"
                "centroid_latitude,centroid_longitude"),
        key="blklot"),
    "assessor": dict(
        id="wv5m-vpq2", dir="assessor", file="assessor_secured_roll.csv.gz",
        label="Assessor Historical Secured Property Tax Rolls", category="Parcel risk set",
        select=("closed_roll_year,parcel_number,block,lot,use_code,use_definition,"
                "property_class_code,property_class_code_definition,year_property_built,"
                "number_of_units,number_of_stories,zoning_code,property_area,lot_area,"
                "assessed_land_value,assessed_improvement_value,assessed_fixtures_value,"
                "assessed_personal_property_value,supervisor_district,analysis_neighborhood"),
        key="parcel_number"),
    "land_use": dict(
        id="c5ge-t6pj", dir="parcels", file="land_use.csv.gz",
        label="San Francisco Land Use", category="Parcel risk set",
        select=("mapblklot,geography_type,resunits,cie,med,mips,retail,pdr,visitor,"
                "total_comm,parking_lo,garage,open_space,residentia,data_as_of"),
        key="mapblklot"),

    # ── 2.1 zoning, parcel-keyed years only ──────────────────────────────
    **{f"zoning_{y}": dict(
        id=i, dir="zoning", file=f"historic_zoning_{y}.csv.gz",
        label=f"Historic Zoning Districts --- {y}", category="Zoning (parcel-keyed)",
        select=None,
        keep_rx=(r"(?i)^(blklot|mapblklot|block_num|lot_num)$|zon|height|district|"
                 r"landuse|newuse|use_type|units|stories|far|bldg_sqft|sumdist"),
        key="blklot")
       for y, i in [(1998, "piqy-pimd"), (2000, "itx2-wzp5"), (2001, "8m6w-8yuk"),
                    (2002, "3yi7-eyfd"), (2003, "6ru7-zq4y"), (2004, "s3eh-jdwb"),
                    (2005, "b52k-gy2v"), (2007, "pe87-v8tx"), (2008, "tsdh-z53a")]},

    # ── 2.2 filing dates and the bridge ──────────────────────────────────
    "records_nonproject": dict(
        id="y673-d69b", dir="planning_records", file="nonprojects.csv.gz",
        label="Planning Department Records --- Non-Projects", category="Filing dates",
        select=("record_id,record_type,project_name,description,record_status,"
                "project_address,block,lot,parent_id,child_id,open_date,close_date,"
                "building_permits,applicant,applicant_org,assigned_to_planner"),
        key="record_id"),
    "records_project": dict(
        id="qvu5-m3a2", dir="planning_records", file="projects.csv.gz",
        label="Planning Department Records --- Projects", category="Filing dates",
        select=("record_id,record_type,project_name,description,record_status,"
                "project_address,block,lot,child_id,open_date,close_date,building_permits,"
                "applicant,applicant_org,assigned_to_planner,project_decision,"
                "project_decision_date,environmental_document_type,number_of_units_net,"
                "number_of_market_rate_units,number_of_affordable_units,"
                "number_of_units_exist,number_of_units_prop,change_of_use,additions,"
                "new_construction,demolition,inclusionary,adu,sb35,sb330,state_density_"
                "bonus_analyzed,residential_prop,residential_exist,parking_spaces_prop"),
        key="record_id"),

    # ── 2.3 project scale ────────────────────────────────────────────────
    "pipeline": dict(
        id="6jgi-cpb4", dir="pipeline", file="development_pipeline.csv.gz",
        label="San Francisco Development Pipeline", category="Project scale",
        select=None, key="case_no"),
    "housing_production": dict(
        id="xdht-4php", dir="pipeline", file="housing_production.csv.gz",
        label="Housing Production --- 2005--present", category="Project scale",
        select=None, key="blocklot"),
    "unit_completions": dict(
        id="j67f-aayr", dir="pipeline", file="unit_completions.csv.gz",
        label="Dwelling Unit Completion Counts by Building Permit", category="Project scale",
        select=None, key="building_permit_application"),
    "mohcd_pipeline": dict(
        id="aaxw-2cb8", dir="pipeline", file="mohcd_affordable_pipeline.csv.gz",
        label="MOHCD Affordable Housing Pipeline", category="Project scale",
        select=None, key="planning_case_number"),

    # ── 2.5 fees, the geographic side ────────────────────────────────────
    "impact_fee_areas": dict(
        id="ntc3-dd64", dir="fees", file="impact_fee_areas.csv.gz",
        label="Neighborhood-Specific Impact Fee Areas", category="Fees",
        select="objectid,fee,ordinance,url,area,tier", key="fee"),
}

# Found, inventoried, deliberately not fetched: polygon-only zoning layers, which need a
# spatial join this memo does not do. Recorded so the search is not repeated.
POLYGON_ZONING = {
    "2006": "afkh-hfhr", "2009": "jud5-ja46", "2010": "x4gj-zjx7", "2011": "rt4q-mf68",
    "2012": "vfz8-awmy", "2013": "6jb9-g73z", "2014": "hg44-eza7", "2015": "ada2-cu6t",
    "current (districts)": "xzez-p3nc", "current (height and bulk)": "usp2-teig",
    "current (special use)": "rxfc-8aap",
}

# ── 2.1 zoning as polygons: the layers the first pass inventoried and skipped ──
# Eleven of these were reported as "polygon; needs a spatial join" and left alone. The join
# is task 1 of the follow-up brief, and it is what turns "parcel-keyed zoning is 1998--2008"
# into a panel that spans the corpus.
#
# `usp2-teig` is the layer the brief names for current height and bulk. It is the *height
# district* layer (1,195 rows); `h9wh-cg3m` is Height AND Bulk (5,292 rows) and is the
# geometric continuation of the 2009--2014 historic series (~7,300 rows a year). Both are
# joined; the panel carries `h9wh-cg3m` as the height-and-bulk vintage and `usp2-teig`
# separately, so neither the brief's choice nor the series' own continuity is lost.
POLYGON_LAYERS: dict[str, dict] = {
    "zoning": {2006: "afkh-hfhr", 2009: "jud5-ja46", 2010: "x4gj-zjx7", 2011: "rt4q-mf68",
               2012: "vfz8-awmy", 2013: "6jb9-g73z", 2014: "hg44-eza7", 2015: "ada2-cu6t",
               0: "xzez-p3nc"},
    "height": {2009: "a4wu-zqx3", 2010: "xzb6-i4dc", 2011: "rwdp-2k4t", 2012: "nh4m-jbyj",
               2013: "9mmf-fv7s", 2014: "gu4h-44qp", 0: "h9wh-cg3m"},
    "heightdist": {0: "usp2-teig"},
    "sud": {0: "rxfc-8aap"},
    "feearea": {0: "ntc3-dd64"},
}
# Year 0 is the convention for "current, undated". The value carried out of each family:
# Families where a parcel has exactly one value, and families where it legitimately has
# zero or several. A special-use district is an overlay: 104{,}499 parcels sit in more than
# one, and a fee area covers only part of the city. Calling those "multi" and "unmatched"
# failures --- and sending 232{,}000 parcels to an area-overlap fallback --- was wrong; the
# right output there is a list and an honest zero.
SINGLE_VALUED = {"zoning", "height", "heightdist"}
MULTI_VALUED = {"sud", "feearea"}
POLY_VALUE = {"zoning": ["zoning_sim", "districtna", "districtname", "zoning"],
              "height": ["height", "gen_hght"],
              "heightdist": ["height", "gen_hght"],
              "sud": ["name"],
              "feearea": ["fee", "tier", "area", "ordinance"]}
POLY_DIR = EXT / "zoning" / "poly"
PANEL = EXT / "zoning" / "parcel_zoning_panel.parquet"
PANEL_COVERAGE = EXT / "zoning" / "spatial_coverage.csv"
# Parcel polygons, fetched only for the centroids that fail or multi-match, so the
# largest-overlap fallback costs a few thousand geometries rather than 236,556.
PARCEL_SHAPES = EXT / "parcels" / "parcel_shapes_fallback.geojson"
EQUAL_AREA = "EPSG:3310"          # California Albers; areas in square metres
# 2016 onward has no published vintage. The current layer is carried forward and flagged,
# never silently: see the panel's `is_snapshot` column and §2.3 of the memo.
PANEL_YEARS = range(1998, 2027)


# ── 2.4 prices ───────────────────────────────────────────────────────────
ZILLOW = {
    "zhvi_zip": ("https://files.zillowstatic.com/research/public_csvs/zhvi/"
                 "Zip_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv",
                 "ZHVI, all homes, smoothed and seasonally adjusted --- ZIP"),
    "zori_zip": ("https://files.zillowstatic.com/research/public_csvs/zori/"
                 "Zip_zori_uc_sfrcondomfr_sm_month.csv",
                 "ZORI, all homes plus multifamily, smoothed --- ZIP"),
    "zhvi_city": ("https://files.zillowstatic.com/research/public_csvs/zhvi/"
                  "City_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv",
                  "ZHVI, all homes, smoothed and seasonally adjusted --- city"),
    "zori_city": ("https://files.zillowstatic.com/research/public_csvs/zori/"
                  "City_zori_uc_sfrcondomfr_sm_month.csv",
                  "ZORI, all homes plus multifamily, smoothed --- city"),
}
# The Zillow files are national (123 MB for ZIP-level ZHVI). Only San Francisco County is
# kept; the national row count is recorded in the probe so the filter is auditable.
ZILLOW_COUNTY = "San Francisco County"
ZILLOW_CITY = "San Francisco"

# ── 2.5 the fee register ─────────────────────────────────────────────────
# The Citywide Development Impact Fee Register is republished each January at ONE URL, which
# means the site only ever holds the current year. The dated copies come from the Wayback
# Machine and from the two year-stamped URLs the department left behind. Effective dates are
# read out of the PDF itself, not inferred from the snapshot date.
FEE_REGISTERS = {
    2019: "https://web.archive.org/web/20190915185307if_/https://sfplanning.org/sites/default/files/forms/Impact_Fee_Schedule.pdf",
    2020: "https://web.archive.org/web/20200305123739if_/https://sfplanning.org/sites/default/files/forms/Impact_Fee_Schedule.pdf",
    2021: "https://web.archive.org/web/20210421052129if_/https://sfplanning.org/sites/default/files/forms/Impact_Fee_Schedule.pdf",
    2022: "https://sfplanning.org/sites/default/files/forms/Impact_Fee_Schedule-2022.pdf",
    2023: "https://sfplanning.org/sites/default/files/forms/Impact_Fee_Schedule-2023.pdf",
    2024: "https://sfplanning.org/sites/default/files/forms/Impact_Fee_Schedule-2024.pdf",
    2025: "https://sfplanning.org/sites/default/files/forms/Impact_Fee_Schedule-2025.pdf",
    2026: "https://sfplanning.org/sites/default/files/forms/Impact_Fee_Schedule.pdf",
}
FEES_DIR = EXT / "fees"

# ── 2.4 prices: the licensed extract that is already on disk ─────────────
# CoreLogic/Cotality is licensed and is never scraped. It does not have to be: the June 2026
# Redivis pull is on Dropbox under the data root, written by `demand_estimation/corelogic.py`.
#
# It went missing once, and the reason is worth recording. Dropbox's macOS file provider
# keeps files "online-only": the directory entry reports the full size while the file
# occupies zero blocks, and an un-materialised *directory* does not show up in `ls` at all.
# The first pass of this memo therefore reported the whole `demand/` subtree as absent. It
# was not; it was dehydrated. `sync` is the fix, and it is a stage of this script so the
# mistake is not repeatable.
CORELOGIC = DATA_ROOT / "demand" / "corelogic"
CORELOGIC_TX = CORELOGIC / "clean" / "corelogic_transactions_bg.parquet"
CORELOGIC_PROPERTY = CORELOGIC / "cotality_property_filtered.csv"
CL_CACHE = EXT / "prices" / "corelogic_sf_transactions.parquet"
CL_META = EXT / "prices" / "corelogic_sf_meta.json"
SF_FIPS = "06075"
# Cotality prints San Francisco's APN as "0836 003" --- block and lot in DataSF's own 4+3
# form with a separator. Strip the separator and it *is* `blklot`, which is what makes this a
# parcel-level price and not a block-group one.
APN_COL = "APN__PARCEL_NUMBER_UNFORMATTED_"
TRADE_WINDOW_DAYS = 1095        # +/- 3 years of the hearing: "did this parcel trade near the
                                # decision", the price a real-options threshold is measured at
DENSE_YEAR_SALES = 1000         # fewest sales a year needs before the series is called usable


# ═══════════════════════════════════════════════════════════════════════════
# small helpers
# ═══════════════════════════════════════════════════════════════════════════
def sess() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    tok = _app_token()
    if tok:
        s.headers["X-App-Token"] = tok
    return s


def _app_token() -> str | None:
    env = os.environ.get("SOCRATA_APP_TOKEN")
    if env and env.strip():
        return env.strip()
    p = HERE.parents[1] / "api_keys" / "socrata_app_token.txt"
    return p.read_text().strip() if p.exists() and p.read_text().strip() else None


def norm_case(s) -> str:
    """Case number / record_id → upper-case and spaceless, the same rule the extraction and
    `datasf_records.py` use, so nothing is compared across a formatting difference."""
    return re.sub(r"\s+", "", str(s or "")).upper()


CASE_STEM = re.compile(r"^(\d{4}[.\-]\d{3,6})")


def case_stem(s) -> str:
    """The case number without its letter suffix: 2014.0400CUA → 2014.0400,
    2022-001764CUA → 2022-001764. The Planning Department's umbrella PRJ record and the
    Housing Production table's `ppts_project_id` are both keyed on the stem, not the suffix,
    so the stem is what bridges a conditional use to a project."""
    m = CASE_STEM.match(norm_case(s))
    return m.group(1) if m else ""


def case_format(s) -> str:
    """Which of the three printed case-number formats this is. The Planning Department's
    records begin at the four-digit-year form, so the format is the whole explanation of the
    non-joining residual and the residual has to be reported by it."""
    c = norm_case(s)
    if re.match(r"^\d{4}-\d{6}", c):
        return "YYYY-NNNNNN"
    if re.match(r"^\d{4}\.\d{3,4}", c):
        return "YYYY.NNNN"
    if re.match(r"^\d{2}\.\d{2,4}", c):
        return "YY.NNN"
    return "other"


# ── the two-digit-year case number, and why it is not a fuzzy match ──────
# The first pass concluded that the Planning Department's records "simply do not go back
# that far under any key", on the evidence that 0 of 536 two-digit-format cases matched.
# That was wrong. The department stores `1998.505C` where the minutes print `98.505C`:
# the records are there and the *format* differs. Expanding the year is a deterministic
# normalisation, not a fuzzy match, and it recovers the era.
#
# The one judgement call is the century. It is resolved against the hearing year rather
# than a fixed pivot --- a case heard in 1998 cannot have been filed in 2084 --- and the
# rule is checked: `report` counts the cases for which BOTH centuries would match a real
# record, and that count is reported rather than assumed to be zero.
YY_CASE = re.compile(r"^(\d{2})\.(\d{2,4})(.*)$")


def expand_yy(cn: str, hearing_year) -> str:
    """`98.426D` heard in 1998 → `1998.426D`. Returns the input unchanged when it is not a
    two-digit-year case number, and when no century is consistent with the hearing."""
    m = YY_CASE.match(cn or "")
    if not m:
        return cn
    yy = int(m.group(1))
    ok = [y for y in (1900 + yy, 2000 + yy) if hearing_year and y <= hearing_year]
    return f"{max(ok)}.{m.group(2)}{m.group(3)}" if ok else cn


def digits(s) -> str:
    return re.sub(r"[^0-9]", "", str(s or ""))


_ALNUM = re.compile(r"^(\d*)([A-Za-z]*)$")


def _pad(tok: str, width: int) -> str:
    """Zero-pad the *digits* and keep the letter. DataSF writes lot 17A as `017A` and block
    452T as `0452T`: the padding goes on the numeric part, not the whole token.

    An earlier version of this function used `zfill` on the token as a whole, which is a
    no-op once a letter makes it long enough --- `'17A'.zfill(3) == '17A'` --- so every
    lettered parcel silently failed to join. 27{,}822 of San Francisco's parcels carry a
    lettered lot, and that one line was most of the memo's parcel-join shortfall."""
    m = _ALNUM.match(tok or "")
    if not m:
        return tok
    d, a = m.group(1), m.group(2)
    return (d.zfill(width) if d else "") + a


def blklot(block, lot) -> str:
    """DataSF's parcel key is block padded to 4 and lot padded to 3, concatenated
    (`3605052`, `4001017A`, `0452T044H`). The minutes print neither padded."""
    b, l = str(block or "").strip().upper(), str(lot or "").strip().upper()
    if not b or not l:
        return ""
    return _pad(b, 4) + _pad(l, 3)


# An item that spans several blocks has all of them in the single `assessor_block` field,
# comma- or semicolon-separated ("4624, 4720"), against a flat list of lots that carries no
# positional pairing back to them. Taking the field as one block produced the nonsense key
# `4624, 4720003`. The cross-product of the named blocks with the named lots, kept only where
# the resulting parcel actually exists, is the set of parcels the item plausibly touches ---
# it is a normalisation of what the minutes said, not a guess about what they meant.
BLOCK_LIST = re.compile(r"[,;/]|\band\b")


def _item_parcels(r) -> set[str]:
    lots = r.lot_number if isinstance(r.lot_number, list) else []
    blocks = [b.strip() for b in BLOCK_LIST.split(str(r.assessor_block or "")) if b.strip()]
    return {k for k in (blklot(b, l) for b in blocks for l in lots) if k}


def load_items() -> pd.DataFrame:
    rows = []
    for f in sorted(glob.glob(str(DATA_ROOT / "extraction" / RUN / "clean" / "*.jsonl"))):
        rows += [json.loads(l) for l in open(f)]
    df = pd.DataFrame(rows)
    df["meeting_date"] = pd.to_datetime(df.meeting_date, errors="coerce")
    df["year"] = df.meeting_date.dt.year.fillna(df.year).astype(int)
    df["cn_raw"] = df.case_number.map(norm_case)
    # The join key is the expanded form; `cn_raw` is kept so the memo can still report what
    # raw string equality buys, which is the point of Table 5's first row.
    df["cn"] = [expand_yy(c, y) for c, y in zip(df.cn_raw, df.year)]
    df["stem"] = df.cn.map(case_stem)
    df["parcels"] = [_item_parcels(r) for r in df.itertuples()]
    df["has_parcel"] = df.parcels.map(bool)
    return df


def path_of(src: dict) -> Path:
    return EXT / src["dir"] / src["file"]


def read_cached(name: str, **kw) -> pd.DataFrame:
    src = SOCRATA_SOURCES[name]
    p = path_of(src)
    if not p.exists():
        raise SystemExit(f"{p} missing — run `acquire_external_data.py fetch` first")
    return pd.read_csv(p, dtype=str, low_memory=False, **kw)


# ═══════════════════════════════════════════════════════════════════════════
# stage 1: probe — resolve every identifier against the live catalogue
# ═══════════════════════════════════════════════════════════════════════════
def view_meta(s: requests.Session, ds: str) -> dict:
    v = s.get(f"https://{SOCRATA_HOST}/api/views/{ds}.json", timeout=90).json()
    cols = [c["fieldName"] for c in v.get("columns", [])]
    return {"name": v.get("name"), "columns": cols,
            "updated_at": v.get("rowsUpdatedAt"), "view_type": v.get("displayType"),
            "attribution": v.get("attribution")}


def row_count(s: requests.Session, ds: str) -> int | None:
    try:
        r = s.get(f"https://{SOCRATA_HOST}/resource/{ds}.json",
                  params={"$select": "count(1)"}, timeout=180)
        r.raise_for_status()
        return int(r.json()[0]["count_1"])
    except Exception:
        return None


def catalogue_search(s: requests.Session, term: str, limit: int = 40) -> list[dict]:
    r = s.get(CATALOGUE, params={"search_context": SOCRATA_HOST, "domains": SOCRATA_HOST,
                                 "q": term, "limit": limit}, timeout=90)
    r.raise_for_status()
    j = r.json()
    return [{"id": d["resource"]["id"], "name": d["resource"]["name"],
             "type": d["resource"].get("type")} for d in j["results"]]


# The searches actually run, kept in the script so the memo's negatives are reproducible
# rather than remembered.
SEARCH_TERMS = ["parcel", "zoning", "assessor secured property", "land use",
                "housing pipeline", "housing inventory", "affordable housing pipeline",
                "impact fee", "inclusionary", "height bulk", "planning commission",
                "commission action", "entitlement", "rent", "home value", "sale price"]


def probe():
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    s = sess()
    out = {"host": SOCRATA_HOST, "retrieved": pd.Timestamp.today().date().isoformat(),
           "datasets": {}, "searches": {}, "polygon_zoning": {}, "http": {}}

    for name, src in SOCRATA_SOURCES.items():
        try:
            meta = view_meta(s, src["id"])
            n = row_count(s, src["id"])
            out["datasets"][name] = {"proposed_id": src["id"], "resolved_id": src["id"],
                                     "label": src["label"], "category": src["category"],
                                     "rows": n, **meta}
            print(f"  {name:22s} {src['id']}  {n if n is not None else '?':>10}  {meta['name']}")
        except Exception as e:
            out["datasets"][name] = {"proposed_id": src["id"], "resolved_id": None,
                                     "error": f"{type(e).__name__}: {e}"}
            print(f"  {name:22s} {src['id']}  FAILED {type(e).__name__}")
        time.sleep(PAUSE)

    for label, ds in POLYGON_ZONING.items():
        try:
            meta = view_meta(s, ds)
            out["polygon_zoning"][label] = {"id": ds, "rows": row_count(s, ds), **meta}
        except Exception as e:
            out["polygon_zoning"][label] = {"id": ds, "error": str(e)}
        time.sleep(PAUSE)

    for t in SEARCH_TERMS:
        try:
            out["searches"][t] = catalogue_search(s, t)
            print(f"  search {t!r}: {len(out['searches'][t])} hits")
        except Exception as e:
            out["searches"][t] = [{"error": str(e)}]
        time.sleep(PAUSE)

    # non-Socrata hosts: does the file exist, and how big is it?
    for k, (url, label) in ZILLOW.items():
        out["http"][f"zillow:{k}"] = _head(s, url, label)
    for y, url in FEE_REGISTERS.items():
        out["http"][f"fee_register:{y}"] = _head(s, url, f"Impact Fee Register {y}")

    PROBE_JSON.write_text(json.dumps(out, indent=2) + "\n")
    print(f"→ {PROBE_JSON}")


def _head(s: requests.Session, url: str, label: str) -> dict:
    try:
        r = s.head(url, timeout=90, allow_redirects=True)
        if r.status_code >= 400:                       # some hosts refuse HEAD; try a range
            r = s.get(url, timeout=90, headers={"Range": "bytes=0-1024"})
        return {"label": label, "url": url, "status": r.status_code,
                "bytes": r.headers.get("content-length")}
    except Exception as e:
        return {"label": label, "url": url, "status": None, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# stage 2: fetch — cache each source once
# ═══════════════════════════════════════════════════════════════════════════
GEOM_COLS = re.compile(r"(?i)^(the_geom|geometry|multigeom|shape|centroid|location|"
                       r"latlong)$|^:@computed")


def select_for(s: requests.Session, src: dict) -> str:
    if src.get("select"):
        return src["select"]
    cols = [c for c in view_meta(s, src["id"])["columns"] if not GEOM_COLS.match(c)]
    if src.get("keep_rx"):
        cols = [c for c in cols if re.search(src["keep_rx"], c)]
    return ",".join(cols)


def fetch_socrata(name: str, refresh: bool) -> Path:
    src = SOCRATA_SOURCES[name]
    p = path_of(src)
    if p.exists() and not refresh:
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    s = sess()
    sel = select_for(s, src)
    off, n = 0, 0
    tmp = p.with_suffix(".part")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        while True:
            for attempt in range(4):
                try:
                    r = s.get(f"https://{SOCRATA_HOST}/resource/{src['id']}.csv",
                              params={"$select": sel, "$order": ":id",
                                      "$limit": PAGE, "$offset": off}, timeout=600)
                    r.raise_for_status()
                    break
                except requests.RequestException:
                    if attempt == 3:
                        raise
                    time.sleep(5 * (attempt + 1))
            lines = r.text.splitlines(True)
            if off:
                lines = lines[1:]                      # header once
            if not lines:
                break
            fh.writelines(lines)
            n += len(lines)
            if len(r.text.splitlines()) - 1 < PAGE:
                break
            off += PAGE
            print(f"    {name}: {n:,} rows", flush=True)
            time.sleep(PAUSE)
    _promote(tmp, p)
    print(f"  cached {n:,} lines → {p}")               # lines, not rows: a description field
    return p                                           # may carry embedded newlines


def _promote(tmp: Path, p: Path):
    """Rename the part file into place, then check that it took. Dropbox's macOS file
    provider occasionally reports a rename as done and leaves the source in place --- three
    of the nine zoning files landed that way on the first run --- so verify rather than
    trust, and say so if it still has not happened."""
    for attempt in range(3):
        try:
            tmp.replace(p)
        except OSError:
            pass
        time.sleep(0.5 * (attempt + 1))
        if p.exists() and not tmp.exists():
            return
    if tmp.exists() and not p.exists():
        raise SystemExit(f"could not rename {tmp} → {p}; the data is complete in the "
                         f".part file, rename it by hand")


def fetch_zillow(refresh: bool):
    d = EXT / "prices"
    d.mkdir(parents=True, exist_ok=True)
    s = sess()
    meta = {}
    for k, (url, label) in ZILLOW.items():
        out = d / f"{k}_sf.csv.gz"
        if out.exists() and not refresh:
            continue
        r = s.get(url, timeout=900)
        r.raise_for_status()
        df = pd.read_csv(io.BytesIO(r.content), low_memory=False)
        col = "CountyName" if "CountyName" in df.columns else "RegionName"
        sub = (df[df.CountyName.eq(ZILLOW_COUNTY)] if col == "CountyName"
               else df[df.RegionName.eq(ZILLOW_CITY) & df.StateName.eq("CA")])
        if col == "CountyName" and "City" in sub.columns:
            pass                                       # ZIP file: county filter is enough
        sub.to_csv(out, index=False, compression="gzip")
        meta[k] = {"national_rows": int(len(df)), "sf_rows": int(len(sub))}
        print(f"  {k}: {len(df):,} national rows → {len(sub):,} San Francisco rows → {out}")
        time.sleep(PAUSE)
    if meta:
        (d / "_filter.json").write_text(json.dumps(meta, indent=2) + "\n")


def fetch_fees(refresh: bool):
    FEES_DIR.mkdir(parents=True, exist_ok=True)
    s = sess()
    for y, url in FEE_REGISTERS.items():
        out = FEES_DIR / f"impact_fee_register_{y}.pdf"
        if out.exists() and not refresh:
            continue
        try:
            r = s.get(url, timeout=300)
            r.raise_for_status()
            out.write_bytes(r.content)
            print(f"  fee register {y}: {len(r.content):,} bytes → {out}")
        except requests.RequestException as e:
            print(f"  fee register {y}: {type(e).__name__} — not cached")
        time.sleep(1.0)


def fetch(only: str | None, refresh: bool):
    names = [only] if only else list(SOCRATA_SOURCES)
    for name in names:
        if name in SOCRATA_SOURCES:
            fetch_socrata(name, refresh)
    if not only or only == "zillow":
        fetch_zillow(refresh)
    if not only or only == "fees":
        fetch_fees(refresh)
    if not only or only == "corelogic":
        fetch_corelogic(refresh)
    if not only or only == "polygons":
        fetch_polygons(refresh)


# ── polygons and the spatial join ────────────────────────────────────────
def poly_path(fam: str, year: int) -> Path:
    return POLY_DIR / f"{fam}_{'current' if year == 0 else year}.geojson"


def fetch_polygons(refresh: bool):
    """Pull each polygon layer once as GeoJSON. These are small (a few thousand features)
    and the geometry is the whole point, so unlike the tabular sources they are not
    column-filtered."""
    POLY_DIR.mkdir(parents=True, exist_ok=True)
    s = sess()
    for fam, years in POLYGON_LAYERS.items():
        for year, ds in years.items():
            out = poly_path(fam, year)
            if out.exists() and not refresh:
                continue
            r = s.get(f"https://{SOCRATA_HOST}/resource/{ds}.geojson",
                      params={"$limit": 500_000}, timeout=600)
            r.raise_for_status()
            out.write_bytes(r.content)
            n = r.text.count('"Feature"')
            print(f"  {fam} {year or 'current'} ({ds}): {n:,} features → {out.name}")
            time.sleep(PAUSE)


def _parcel_points():
    """Parcel centroids as a GeoDataFrame, with the dates that say when each parcel existed.
    `date_map_add`/`date_map_drop` are the map's own record of when the lot came into and
    went out of force, which is what makes a 2003 hearing joinable to 2003 geometry."""
    import geopandas as gpd
    par = read_cached("parcels", usecols=["blklot", "mapblklot", "active",
                                          "date_map_add", "date_map_drop",
                                          "centroid_latitude", "centroid_longitude"])
    par = par[par.centroid_latitude.notna() & par.centroid_longitude.notna()].copy()
    for c in ("date_map_add", "date_map_drop"):
        par[c] = pd.to_datetime(par[c], errors="coerce")
    g = gpd.GeoDataFrame(
        par, geometry=gpd.points_from_xy(pd.to_numeric(par.centroid_longitude),
                                         pd.to_numeric(par.centroid_latitude)),
        crs="EPSG:4326").to_crs(EQUAL_AREA)
    return g


def _fetch_parcel_shapes(blklots: set[str], refresh: bool):
    """Polygons for just the parcels the centroid rule could not place. Socrata's `in()`
    has a practical length limit, so the ids go in batches."""
    import geopandas as gpd
    if PARCEL_SHAPES.exists() and not refresh:
        have = gpd.read_file(PARCEL_SHAPES)
        if set(have.blklot) >= blklots:
            return have
    s = sess()
    ids = sorted(blklots)
    feats = []
    for i in range(0, len(ids), 50):                # 200 per query returned a server 500
        chunk = ids[i:i + 50]
        where = "blklot in (" + ",".join(f"'{b}'" for b in chunk) + ")"
        for attempt in range(4):
            try:
                r = s.get(f"https://{SOCRATA_HOST}/resource/acdm-wktn.geojson",
                          params={"$select": "blklot,shape", "$where": where,
                                  "$limit": 5000}, timeout=300)
                r.raise_for_status()
                break
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(3 * (attempt + 1))
        feats += r.json().get("features", [])
        if (i // 50) % 20 == 0:
            print(f"    shapes {i:,}/{len(ids):,}", flush=True)
        time.sleep(PAUSE)
    if not feats:
        return None
    gj = {"type": "FeatureCollection", "features": feats}
    PARCEL_SHAPES.parent.mkdir(parents=True, exist_ok=True)
    PARCEL_SHAPES.write_text(json.dumps(gj))
    print(f"  fetched {len(feats):,} parcel polygons for the overlap fallback")
    return gpd.read_file(PARCEL_SHAPES)


def _value_of(gdf, fam: str) -> pd.Series:
    """The first attribute a family actually publishes, per layer. The historic zoning
    layers call the district `zoning_sim`; the current one publishes `zoning_sim`,
    `zoning` and `districtname`. Take the first present rather than assuming."""
    for c in POLY_VALUE[fam]:
        if c in gdf.columns and gdf[c].notna().any():
            return gdf[c].astype(str)
    return pd.Series([""] * len(gdf), index=gdf.index)


def spatial(refresh: bool = False):
    """Point-in-polygon every parcel against every polygon layer, fall back to
    largest-area overlap where the centroid rule fails or is ambiguous, and write a
    parcel-year panel keyed on `blklot`."""
    import geopandas as gpd
    if PANEL.exists() and not refresh:
        print(f"{PANEL} exists; --refresh to rebuild")
        return
    pts = _parcel_points()
    print(f"{len(pts):,} parcel centroids")
    layers, cover = {}, []
    need_fallback: set[str] = set()
    for fam, years in POLYGON_LAYERS.items():
        for year in years:
            f = poly_path(fam, year)
            if not f.exists():
                continue
            poly = gpd.read_file(f).to_crs(EQUAL_AREA)
            poly = poly[poly.geometry.notna() & poly.geometry.is_valid |
                        poly.geometry.notna()]
            poly = poly.assign(_val=_value_of(poly, fam))
            j = gpd.sjoin(pts[["blklot", "geometry"]], poly[["_val", "geometry"]],
                          predicate="within", how="left")
            n_match = j.groupby("blklot")["_val"].apply(lambda v: v.notna().sum())
            matched = int((n_match == 1).sum())
            multi = int((n_match > 1).sum())
            unmatched = int((n_match == 0).sum())
            if fam in SINGLE_VALUED:                  # only here is multi/none a failure
                need_fallback |= set(n_match.index[n_match != 1])
            layers[(fam, year)] = (poly, j)
            cover.append({"family": fam, "year": year, "parcels": len(pts),
                          "single_valued": fam in SINGLE_VALUED,
                          "centroid_matched": matched, "centroid_multi": multi,
                          "centroid_unmatched": unmatched})
            print(f"  {fam:11s} {year or 'current':>7}: {matched:,} matched, "
                  f"{multi:,} multi, {unmatched:,} unmatched", flush=True)

    print(f"{len(need_fallback):,} parcels need the largest-overlap fallback in at least "
          f"one layer", flush=True)
    shapes = _fetch_parcel_shapes(need_fallback, refresh) if need_fallback else None
    if shapes is not None and len(shapes):
        shapes = shapes.to_crs(EQUAL_AREA)
        shapes = shapes[shapes.geometry.notna()]

    rows = []
    for (fam, year), (poly, j) in layers.items():
        print(f"  resolving {fam} {year or 'current'} …", flush=True)
        if fam in MULTI_VALUED:
            # an overlay: keep every district the parcel falls in, as a sorted list
            g = (j.dropna(subset=["_val"]).groupby("blklot")["_val"]
                 .apply(lambda v: "; ".join(sorted(set(v)))))
            rows.append(pd.DataFrame({"blklot": g.index, "family": fam, "year": year,
                                      "value": g.values, "method": "centroid"}))
            continue
        best = (j.dropna(subset=["_val"]).drop_duplicates("blklot")
                .set_index("blklot")["_val"])
        method = pd.Series("centroid", index=best.index)
        if shapes is not None and len(shapes) and need_fallback:
            sub = shapes[shapes.blklot.isin(need_fallback)]
            if len(sub):
                # `gpd.overlay` computes a full intersection layer and was taking minutes per
                # layer. The candidate pairs from an `intersects` sjoin, intersected
                # row-wise, give the same largest-overlap winner far more cheaply.
                cand = gpd.sjoin(sub[["blklot", "geometry"]],
                                 poly[["_val", "geometry"]].reset_index(drop=True),
                                 predicate="intersects", how="inner")
                if len(cand):
                    pg = poly.geometry.reset_index(drop=True)
                    inter = cand.geometry.intersection(
                        gpd.GeoSeries(pg.loc[cand.index_right].values,
                                      index=cand.index, crs=poly.crs))
                    cand = cand.assign(_a=inter.area)
                    win = (cand.sort_values("_a", ascending=False)
                           .drop_duplicates("blklot").set_index("blklot")["_val"])
                    best = win.combine_first(best)
                    method = pd.Series("overlap", index=win.index).combine_first(method)
        rows.append(pd.DataFrame({"blklot": best.index, "family": fam, "year": year,
                                  "value": best.values,
                                  "method": method.reindex(best.index).values}))
    long = pd.concat(rows, ignore_index=True)

    # ── the parcel-year panel ────────────────────────────────────────────
    par = read_cached("parcels", usecols=["blklot", "date_map_add", "date_map_drop"])
    for c in ("date_map_add", "date_map_drop"):
        par[c] = pd.to_datetime(par[c], errors="coerce")
    par["y0"] = par.date_map_add.dt.year.fillna(min(PANEL_YEARS)).astype(int)
    par["y1"] = par.date_map_drop.dt.year.fillna(max(PANEL_YEARS)).astype(int)
    parcel_zoning = _parcel_keyed_zoning()           # the 1998--2008 tabular vintages
    poly_zoning = {y: g.set_index("blklot").value
                   for y, g in long[long.family.eq("zoning")].groupby("year")}
    poly_method = {y: g.set_index("blklot").method
                   for y, g in long[long.family.eq("zoning")].groupby("year")}

    panel = []
    for y in PANEL_YEARS:
        live = par[(par.y0 <= y) & (par.y1 >= y)]
        if y in parcel_zoning:
            v = parcel_zoning[y].reindex(live.blklot)
            src, snap, meth = f"parcel-keyed {y}", False, "table"
        elif y in poly_zoning:
            v = poly_zoning[y].reindex(live.blklot)
            src, snap, meth = f"polygon {y}", False, "spatial"
        elif 0 in poly_zoning and y > max([k for k in poly_zoning if k], default=0):
            v = poly_zoning[0].reindex(live.blklot)
            src, snap, meth = "current layer, carried forward", True, "spatial"
        else:
            continue                                  # 1999: nothing published
        panel.append(pd.DataFrame({"blklot": live.blklot.values, "year": y,
                                   "zoning": v.values, "vintage": src,
                                   "is_snapshot": snap, "method": meth}))
    panel = pd.concat(panel, ignore_index=True)
    for fam in ("height", "sud", "feearea"):
        cur = long[long.family.eq(fam)]
        if len(cur):
            byyr = {y: g.set_index("blklot").value for y, g in cur.groupby("year")}
            def pick(r, byyr=byyr):
                return byyr.get(r, byyr.get(0))
            col = []
            for y, g in panel.groupby("year"):
                srcs = pick(y)
                col.append(pd.Series(srcs.reindex(g.blklot).values, index=g.index))
            panel[fam] = pd.concat(col).sort_index()
    PANEL.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(PANEL, index=False)
    pd.DataFrame(cover).to_csv(PANEL_COVERAGE, index=False)
    print(f"  parcel-year panel: {len(panel):,} rows, "
          f"{panel.year.min()}--{panel.year.max()} → {PANEL}")
    print(f"  coverage by layer → {PANEL_COVERAGE}")


def _parcel_keyed_zoning() -> dict[int, pd.Series]:
    """The 1998--2008 tabular vintages, as one Series of zoning per year keyed on blklot."""
    out = {}
    for name, src in SOCRATA_SOURCES.items():
        if not name.startswith("zoning_"):
            continue
        p = path_of(src)
        if not p.exists():
            continue
        d = pd.read_csv(p, dtype=str, low_memory=False)
        kc = "blklot" if "blklot" in d.columns else "mapblklot"
        vc = next((c for c in ("zoning", "zonsimpl", "newzon", "sumdist")
                   if c in d.columns and d[c].notna().any()), None)
        if vc is None:
            continue
        d = d[[kc, vc]].dropna().drop_duplicates(kc)
        d[kc] = d[kc].str.upper()
        out[int(name.split("_")[1])] = d.set_index(kc)[vc]
    return out


# ── the local licensed extract ───────────────────────────────────────────
def dehydrated(root: Path) -> list[Path]:
    """Files Dropbox is holding online-only: the size is in the directory entry but no
    blocks are allocated. Reading one materialises it; there is no supported API."""
    out = []
    for p in root.rglob("*"):
        if p.is_file():
            st = p.stat()
            if st.st_size and st.st_blocks * 512 < st.st_size * 0.5:
                out.append(p)
    return out


def sync(root: Path | None = None):
    """Materialise every online-only file under the demand subtree. Cheap when there is
    nothing to do, and the only way to tell a missing directory from a dehydrated one."""
    root = root or (DATA_ROOT / "demand")
    if not root.exists():
        sys.exit(f"{root} does not exist. If Dropbox has it, it has not been created "
                 f"locally at all --- this is not a hydration problem.")
    todo = dehydrated(root)
    total = sum(p.stat().st_size for p in todo)
    print(f"{root}: {len(todo)} file(s) online-only, {total/1e9:.2f} GB")
    for i, p in enumerate(todo, 1):
        t = time.time()
        try:
            with p.open("rb") as fh:
                while fh.read(1 << 22):
                    pass
        except OSError as e:
            print(f"  [{i}/{len(todo)}] {p.name}: {type(e).__name__}: {e}")
            continue
        print(f"  [{i}/{len(todo)}] {p.relative_to(root)} in {time.time()-t:.0f}s")
    left = dehydrated(root)
    nom = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    act = sum(p.stat().st_blocks * 512 for p in root.rglob("*") if p.is_file())
    print(f"{nom/1e9:.2f} GB nominal, {act/1e9:.2f} GB on disk, {len(left)} still online-only")


def fetch_corelogic(refresh: bool):
    """Slice the Bay-Area-wide Cotality extract down to San Francisco and attach the parcel
    key, once, so `report` does not re-read two gigabytes on every run. Nothing is downloaded
    here --- the extract is licensed and already on disk."""
    if CL_CACHE.exists() and not refresh:
        return
    if not CORELOGIC_TX.exists():
        print(f"CoreLogic extract absent at {CORELOGIC_TX}; run `sync` first, and if it is "
              f"still absent it is genuinely not there")
        return
    for p in (CORELOGIC_TX, CORELOGIC_PROPERTY):
        if p.exists() and p.stat().st_blocks * 512 < p.stat().st_size * 0.5:
            print(f"  materialising {p.name} …")
            with p.open("rb") as fh:
                while fh.read(1 << 22):
                    pass
    print("  reading the Cotality property extract for San Francisco APNs …", flush=True)
    keep = []
    for ch in pd.read_csv(CORELOGIC_PROPERTY, usecols=["CLIP", "FIPS_CODE", APN_COL],
                          dtype=str, chunksize=400_000, low_memory=False):
        keep.append(ch[ch.FIPS_CODE.fillna("").str.zfill(5).eq(SF_FIPS)])
    apn = pd.concat(keep, ignore_index=True)
    apn["blklot"] = (apn[APN_COL].fillna("").str.replace(r"[^0-9A-Za-z]", "", regex=True)
                     .str.upper())
    apn = apn[apn.blklot.str.len().between(7, 9)]
    print(f"  {len(apn):,} San Francisco parcels with a usable parcel key", flush=True)

    tx = pd.read_parquet(CORELOGIC_TX, columns=[
        "clip", "fips", "sale_date", "sale_amount", "price_per_sqft", "arms_length",
        "is_residential", "living_sqft", "units", "year_built", "GEOID"])
    sf = tx[tx["fips"].fillna("").str.zfill(5).eq(SF_FIPS)].copy()
    sf["blklot"] = sf["clip"].astype(str).map(dict(zip(apn.CLIP.astype(str), apn.blklot)))
    sf["sale_date"] = pd.to_datetime(sf.sale_date, errors="coerce")
    CL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    sf.to_parquet(CL_CACHE, index=False)
    CL_META.write_text(json.dumps({
        "source": str(CORELOGIC_TX), "bay_area_rows": int(len(tx)),
        "sf_rows": int(len(sf)), "sf_parcels_in_property_file": int(len(apn)),
        "built": pd.Timestamp.today().date().isoformat()}, indent=2) + "\n")
    print(f"  cached {len(sf):,} San Francisco transactions → {CL_CACHE}")


# ═══════════════════════════════════════════════════════════════════════════
# stage 3: report
# ═══════════════════════════════════════════════════════════════════════════
def corelogic_status() -> dict:
    """The brief asks whether the CoreLogic pull actually happened before Stanford access
    lapsed. It did, and the extract is on disk: `demand_estimation/stubs.py` records the
    status as `landed` and `demand/corelogic/` holds both the raw Cotality files and the
    cleaned parquet the demand pipeline built from them."""
    rep = HERE.parents[1] / "demand_estimation"
    stub = (rep / "stubs.py").read_text() if (rep / "stubs.py").exists() else ""
    m = re.search(r'STUBS\["corelogic"\][\s\S]{0,400}?\*\*Status:\*\*\s*`([a-z_]+)`', stub)
    root = DATA_ROOT / "demand"
    files = [p for p in root.rglob("*") if p.is_file()] if root.exists() else []
    return {"documented_status": m.group(1) if m else "unknown",
            "path": str(CORELOGIC),
            "present": CORELOGIC_TX.exists(),
            "demand_root_present": root.exists(),
            "demand_files": len(files),
            "demand_bytes": sum(p.stat().st_size for p in files),
            "demand_online_only": len(dehydrated(root)) if root.exists() else None}


def corelogic_sf(it: pd.DataFrame) -> dict:
    """What the licensed price data actually buys, measured against the item table rather
    than asserted. Three questions: does it join on the parcel key, on how much of the risk
    set, and --- the one that matters for a filing threshold --- did the parcel trade near
    the hearing, so there is a price contemporaneous with the decision."""
    if not CL_CACHE.exists():
        return {}
    sf = pd.read_parquet(CL_CACHE)
    sf["sale_date"] = pd.to_datetime(sf.sale_date, errors="coerce")
    al = sf[sf.arms_length.astype("boolean").fillna(False)
            & sf.sale_amount.gt(1000) & sf.sale_date.notna()]
    res = al[al.is_residential.astype("boolean").fillna(False)]
    have = set(al.blklot.dropna())
    keys = {p for ps in it.parcels for p in ps}
    wp = it[it.has_parcel]
    ever = wp.parcels.map(lambda ps: bool(ps & have))
    by = {k: g.sale_date.values for k, g in al.groupby("blklot")}
    w = np.timedelta64(TRADE_WINDOW_DAYS, "D")

    def near(r):
        for p in r.parcels:
            d = by.get(p)
            if d is not None and np.any(np.abs(d - np.datetime64(r.meeting_date)) <= w):
                return True
        return False

    n3 = [near(r) for r in wp.itertuples()]
    yr = al.sale_date.dt.year
    n_by_year = al.groupby(yr).size()
    # A handful of deeds carry a placeholder date back to 1900. The year the series actually
    # becomes usable is the first with a real volume of sales, and that is what is reported;
    # the raw minimum is kept beside it so the artefact is visible rather than hidden.
    dense = n_by_year[n_by_year >= DENSE_YEAR_SALES]
    return {"rows": int(len(sf)), "priced": int(len(al)), "residential": int(len(res)),
            "dense_first": int(dense.index.min()) if len(dense) else int(yr.min()),
            "last_full": int(dense.index.max()) if len(dense) else int(yr.max()),
            "max_date": str(al.sale_date.max().date()),
            "parcels": int(al.blklot.nunique()),
            "first": int(yr.min()), "last": int(yr.max()),
            "keyed": float(sf.blklot.notna().mean()),
            "item_keys": len(keys), "item_keys_traded": len(keys & have),
            "items_with_key": int(len(wp)), "items_ever_traded": int(sum(ever)),
            "items_traded_near": int(sum(n3)),
            "median_recent": float(al.loc[yr.eq(yr.max()), "sale_amount"].median()),
            "series": al.groupby(yr).sale_amount.median(),
            "series_n": al.groupby(yr).size(),
            "ppsf": al.loc[al.price_per_sqft.between(1, 5000)].groupby(yr).price_per_sqft
                      .median()}


FEE_RATE = re.compile(r"\$([\d,]+(?:\.\d+)?)\s*(?:per|/|X)?\s*"
                      r"(gross square (?:foot|feet)|gross sq\.? ?ft\.?|gsf|"
                      r"net square (?:foot|feet)|nsf|square (?:foot|feet)|sq\.? ?ft\.?|"
                      r"s\.f\.|unit|required tree|gross floor area)?", re.I)
# `\b` on both sides matters: the school fee cites "Section 17620" of the State Education
# Code, and without the trailing boundary that was silently keyed as Planning Code §176.
FEE_SECTION = re.compile(r"(?i)\bSection\s+(\d{2,3}[A-Za-z]?(?:\.\d+)?)\b(?!\d)")
FEE_ED_CODE = re.compile(r"(?i)State Ed\.? Code")
FEE_EFFECTIVE = re.compile(r"(?i)Rates effective as of\s+([A-Z][a-z]+ \d{1,2},\s*\d{4})")

# The record boundary. Every row of the register names the ordinance that creates the fee,
# in column C, and that column's first line always carries the name of a *code* --- the
# section number wraps onto the next line. Row numbers looked like a cleaner boundary but
# the 2019 and 2020 registers do not print them, which silently merged every row on a page
# into one and attributed the whole page's rates to the first section on it.
CODE_MARK = re.compile(r"(?i)\b(Planning Code|State Ed\.? Code|Public Works Code|"
                       r"Administrative Code|Subdivision Code|Building Code|Health Code|"
                       r"Police Code|SFPUC Resolution|Municipal Code)\b")

# The register is a wide landscape spreadsheet; no text extractor turns it back into
# columns. What *is* stable is the Planning Code section, printed once per row, so the
# section is the key a rate is attributed to. This maps the sections the conditions memo's
# recurring headings correspond to; anything else is kept with an empty label.
FEE_SECTIONS = {
    "411A": "Transportation Sustainability Fee",
    "411": "Transit Impact Development Fee (superseded by the TSF)",
    "412": "Downtown Park Fee",
    "413": "Jobs-Housing Linkage Program Fee",
    "414": "Child Care Fee --- residential",
    "414A": "Child Care Fee --- commercial",
    "415": "Inclusionary Affordable Housing Program Fee",
    "417": "Eastern Neighborhoods Alternative Inclusionary Fee",
    "418": "Rincon Hill Community Infrastructure Fee",
    "420": "Visitacion Valley Community Facilities Fee",
    "423": "Eastern Neighborhoods Infrastructure Impact Fee",
    "424": "Van Ness \\& Market Affordable Housing Fee",
    "426": "Open Space --- non-residential alternative",
    "427": "Open Space --- variance or exception",
    "435": "Union Square Park, Recreation and Open Space Fee",
}


def parse_fee_registers() -> pd.DataFrame:
    """One row per dollar rate the register prints, keyed to the register year, the Planning
    Code section of the row it sits in, and the label that precedes it on its own line
    (`>99 Units`, `800-99,999 gsf`, `Hospitals`) --- which is the land-use dimension.

    The register's rows are separated by a bare row number in column A; that is the only
    reliable record boundary in the extracted text, and it is what is used here."""
    cols = ["year", "effective", "section", "fee", "label", "rate", "unit"]
    if not FEES_DIR.exists():
        return pd.DataFrame(columns=cols)
    try:
        import pdfplumber
    except ImportError:
        print("pdfplumber not available; skipping the fee register parse")
        return pd.DataFrame(columns=cols)
    rows = []
    for f in sorted(FEES_DIR.glob("impact_fee_register_*.pdf")):
        year = int(re.search(r"(\d{4})", f.name).group(1))
        try:
            with pdfplumber.open(f) as pdf:
                pages = [p.extract_text() or "" for p in pdf.pages]
        except Exception as e:
            print(f"  {f.name}: {type(e).__name__} — unreadable")
            continue
        m = FEE_EFFECTIVE.search(pages[0] if pages else "")
        eff = m.group(1) if m else ""
        for txt in pages:
            for block in _fee_blocks(txt.splitlines()):
                body = "\n".join(block)
                ms = FEE_SECTION.search(body)
                sec = ("EdCode" if FEE_ED_CODE.search(body)
                       else ms.group(1) if ms else "")
                for line in block:
                    for r in FEE_RATE.finditer(line):
                        rows.append({
                            "year": year, "effective": eff, "section": sec,
                            "fee": FEE_SECTIONS.get(sec, "School Impact Fee"
                                                    if sec == "EdCode" else ""),
                            "label": _rate_label(line, r.start()),
                            "rate": float(r.group(1).replace(",", "")),
                            "unit": (r.group(2) or "").lower()})
    return pd.DataFrame(rows, columns=cols)


def _fee_blocks(lines: list[str]) -> list[list[str]]:
    """Split a page into the register's rows: a new row begins at each line that names a
    code in the ordinance-reference column, and runs to the line before the next one.
    Everything before the first such line is the page header and is dropped."""
    starts = [i for i, l in enumerate(lines) if CODE_MARK.search(l)]
    if not starts:
        return []
    bounds = starts + [len(lines)]
    return [lines[bounds[k]:bounds[k + 1]] for k in range(len(starts))]


LABEL_STOP = re.compile(r"(?i)(residential|non-?residential|hospitals?|"
                        r"medical services|pdr|office|retail|hotel|entertainment|"
                        r"institutional|industrial|senior housing|"
                        r"research (?:&|and) development|[<>]?[\d,]+[\-–][\d,]+ ?(?:units?|gsf)|"
                        r"[<>][\d,]+ ?(?:units?|gsf))\s*:?\s*$")


def _rate_label(line: str, at: int) -> str:
    """The land-use or size band a rate belongs to is printed immediately before it on the
    same line (`>99 Units: $13.13 per gross sq. ft.`). Take that, when it is one of the
    register's own categories, and nothing otherwise --- a guessed label is worse than an
    empty one."""
    lead = line[:at].strip().rstrip(":").strip()
    lead = lead.split("PLUS")[-1].strip()
    m = LABEL_STOP.search(lead)
    return m.group(1).strip() if m else ""


def build_records() -> pd.DataFrame:
    """Both planning-records tables, stacked, with the join keys normalised once."""
    parts = []
    for name, kind in (("records_nonproject", "non-project"), ("records_project", "project")):
        d = read_cached(name)
        d["table"] = kind
        parts.append(d)
    rec = pd.concat(parts, ignore_index=True)
    rec["cn"] = rec.record_id.map(norm_case)
    rec["stem"] = rec.record_id.map(case_stem)
    for c in ("open_date", "close_date"):
        rec[c] = pd.to_datetime(rec[c], errors="coerce")
    return rec


def dbi_stems() -> set[str]:
    """The permit numbers DBI actually holds, normalised the way `analyze_permits.py`
    normalises them (digits only, no zero-padding), so a bridge measured here and a match
    measured there mean the same thing."""
    if not DBI_CACHE.exists():
        print("DBI cache absent; the permit bridge cannot be scored")
        return set()
    out: set[str] = set()
    for chunk in pd.read_csv(DBI_CACHE, dtype=str, usecols=["permit_number"],
                             chunksize=200_000, low_memory=False):
        out |= set(chunk.permit_number.fillna("").map(digits))
    out.discard("")
    return out


PERMIT_SPLIT = re.compile(r"[^0-9A-Za-z]+")


def permits_from(field) -> list[str]:
    """`building_permits` is a comma- or semicolon-separated list of permit numbers, and
    occasionally a single one. Normalise each to digits, drop anything too short to be a
    permit number (a stray '0' or a year)."""
    if not isinstance(field, str) or not field.strip():
        return []
    out = []
    for tok in PERMIT_SPLIT.split(field):
        d = digits(tok)
        if len(d) >= 8:
            out.append(d)
    return out


# ── the joins ────────────────────────────────────────────────────────────
def join_parcels(it: pd.DataFrame, par: pd.DataFrame) -> dict:
    keys = set(par.blklot.dropna())
    map_keys = set(par.mapblklot.dropna())
    active = set(par.loc[par.active.astype(str).str.lower().eq("true"), "blklot"].dropna())
    blocks = set(par.block_num.dropna())
    hit, hit_map, any_active, blockonly = [], [], [], []
    for ps in it.parcels:
        hit.append(bool(ps & keys))
        hit_map.append(bool(ps & map_keys))
        any_active.append(bool(ps & active))
        blockonly.append(bool(ps) and not (ps & keys) and
                         bool({p[:4] for p in ps} & blocks))
    it = it.assign(par_hit=hit, par_hit_map=hit_map, par_active=any_active,
                   par_blockonly=blockonly)
    wp = it[it.has_parcel]
    n_par = len({p for ps in it.parcels for p in ps})
    return {"items": len(it), "with_key": int(it.has_parcel.sum()),
            "distinct_keys": n_par,
            "joined": int(wp.par_hit.sum()),
            "joined_map": int(wp.par_hit_map.sum()),
            "joined_active": int(wp.par_active.sum()),
            "retired_only": int((wp.par_hit & ~wp.par_active).sum()),
            "block_exists_lot_not": int(wp.par_blockonly.sum()),
            "no_block_at_all": int((~wp.par_hit & ~wp.par_blockonly).sum()),
            "frame": it}


def join_assessor(it: pd.DataFrame) -> dict:
    """Streamed: the roll is four million rows and only two columns of it are needed to
    answer the coverage question."""
    want = {p for ps in it.parcels for p in ps}
    seen: dict[str, set[int]] = {}
    years: Counter = Counter()
    n_rows, n_parcels = 0, set()
    for chunk in pd.read_csv(path_of(SOCRATA_SOURCES["assessor"]), dtype=str,
                             usecols=["closed_roll_year", "parcel_number",
                                      "assessed_land_value", "assessed_improvement_value"],
                             chunksize=400_000, low_memory=False):
        n_rows += len(chunk)
        n_parcels |= set(chunk.parcel_number.dropna())
        years.update(chunk.closed_roll_year.dropna().tolist())
        sub = chunk[chunk.parcel_number.isin(want)]
        for pn, yr in zip(sub.parcel_number, sub.closed_roll_year):
            seen.setdefault(pn, set()).add(int(yr))
    joined = [bool(ps & seen.keys()) for ps in it.parcels]
    it = it.assign(asr_hit=joined)
    wp = it[it.has_parcel]
    yr_int = {int(y) for y in years}
    n_years = len(yr_int)
    full = sum(1 for v in seen.values() if len(v) == n_years)
    return {"rows": n_rows, "distinct_parcels": len(n_parcels),
            "years": sorted(yr_int), "year_counts": {int(k): v for k, v in years.items()},
            "item_keys_wanted": len(want), "item_keys_found": len(seen),
            "items_joined": int(wp.asr_hit.sum()), "items_with_key": int(len(wp)),
            "parcels_all_years": full, "frame": it,
            "median_years_per_parcel": float(np.median([len(v) for v in seen.values()])
                                             if seen else np.nan)}


def join_records(it: pd.DataFrame, rec: pd.DataFrame) -> dict:
    """Three tests, reported separately, because 'it joins' is three different claims:
    raw string equality, the normalised case number, and the suffix-stripped stem."""
    cases = it.drop_duplicates("cn")[["cn", "cn_raw", "stem", "case_number", "year",
                                      "meeting_date"]]
    cases = cases[cases.cn.ne("")]
    raw = set(rec.record_id.dropna())
    norm = set(rec.cn)
    stems = set(rec.stem) - {""}
    cases = cases.assign(
        j_raw=cases.case_number.isin(raw),
        j_norm=cases.cn_raw.isin(norm),
        j_stem=cases.cn_raw.map(case_stem).isin(stems) & cases.cn_raw.map(case_stem).ne(""),
        j_exp=cases.cn.isin(norm) | (cases.stem.isin(stems) & cases.stem.ne("")))
    cases["j_any"] = cases.j_norm | cases.j_stem | cases.j_exp
    cases["fmt"] = cases.cn_raw.map(case_format)
    # How often would the other century also have hit a real record? If this is not zero the
    # expansion is a guess and has to be labelled one.
    amb = 0
    for c, hy in zip(cases.cn_raw, cases.year):
        m = YY_CASE.match(c)
        if not m:
            continue
        hits = 0
        for y in (1900 + int(m.group(1)), 2000 + int(m.group(1))):
            cand = f"{y}.{m.group(2)}{m.group(3)}"
            st = case_stem(cand)
            hits += int(cand in norm or (st and st in stems))
        amb += int(hits > 1)
    return {"cases": cases, "n": len(cases),
            "raw": int(cases.j_raw.sum()), "norm": int(cases.j_norm.sum()),
            "stem": int(cases.j_stem.sum()), "exp": int(cases.j_exp.sum()),
            "any": int(cases.j_any.sum()), "century_ambiguous": amb}


def filing_lag(it: pd.DataFrame, rec: pd.DataFrame) -> pd.DataFrame:
    """Filing → first hearing, in days. `open_date` is the date the Planning Department
    opened the record; the first hearing is the earliest meeting the item table records for
    that case. Both sides are collapsed to one row per case first, so a case heard four
    times contributes one observation, not four."""
    first = (it[it.cn.ne("")].groupby("cn", as_index=False)
             .agg(first_hearing=("meeting_date", "min"), year=("year", "min"),
                  request_type=("request_type", "first"), stem=("stem", "first")))
    by_cn = (rec.dropna(subset=["open_date"]).groupby("cn", as_index=False)
             .agg(open_date=("open_date", "min")))
    by_stem = (rec[rec.stem.ne("")].dropna(subset=["open_date"])
               .groupby("stem", as_index=False).agg(open_stem=("open_date", "min")))
    d = first.merge(by_cn, on="cn", how="left").merge(by_stem, on="stem", how="left")
    d["open"] = d.open_date.fillna(d.open_stem)
    d["lag"] = (d.first_hearing - d["open"]).dt.days
    return d


def permit_bridge(it: pd.DataFrame, rec: pd.DataFrame, stems: set[str]) -> dict:
    """§2.2 question 3, the highest-value one. A conditional-use item is joined to its
    planning record on the case number; from there `building_permits` is read out directly
    and, where the record names a parent PRJ, from the parent as well. Every number is
    normalised the way `analyze_permits.py` normalises a printed one, then matched against
    DBI's cached permit table."""
    rec = rec.copy()
    rec["nums"] = rec.building_permits.map(permits_from)
    direct = {c: set(n) for c, n in zip(rec.cn, rec.nums) if n}
    by_stem: dict[str, set[str]] = {}
    for st, n in zip(rec.stem, rec.nums):
        if st and n:
            by_stem.setdefault(st, set()).update(n)
    # parent_id points from a suffixed record (…CUA) at the umbrella project (…PRJ)
    parent = {c: norm_case(p) for c, p in zip(rec.cn, rec.get("parent_id", pd.Series(dtype=str)))
              if isinstance(p, str) and p.strip()}

    out = []
    for r in it.itertuples():
        nums: set[str] = set()
        nums |= direct.get(r.cn, set())
        p = parent.get(r.cn)
        if p:
            nums |= direct.get(p, set())
        nums |= by_stem.get(r.stem, set())
        out.append(nums)
    it = it.assign(bridge=out)
    it["n_bridge"] = it.bridge.map(len)
    it["bridge_in_dbi"] = [bool(n & stems) for n in it.bridge]
    return {"frame": it}


def units_from_records(it: pd.DataFrame) -> pd.DataFrame:
    """Unit counts reached from the Projects table, by case number and by stem."""
    pr = read_cached("records_project")
    pr["cn"] = pr.record_id.map(norm_case)
    pr["stem"] = pr.record_id.map(case_stem)
    for c in ("number_of_units_prop", "number_of_units_net", "number_of_units_exist",
              "number_of_affordable_units", "number_of_market_rate_units"):
        pr[c] = pd.to_numeric(pr.get(c), errors="coerce")
    by_cn = pr.set_index("cn")
    by_stem = (pr[pr.stem.ne("")].sort_values("record_id")
               .drop_duplicates("stem").set_index("stem"))
    cols = ["number_of_units_prop", "number_of_units_net", "number_of_affordable_units"]
    a = it.cn.map(lambda c: by_cn.number_of_units_prop.get(c, np.nan)
                  if c in by_cn.index else np.nan)
    b = it.stem.map(lambda s: by_stem.number_of_units_prop.get(s, np.nan) if s else np.nan)
    it = it.assign(units_prj=pd.to_numeric(a, errors="coerce").fillna(
        pd.to_numeric(b, errors="coerce")))
    it["units_net_prj"] = it.stem.map(
        lambda s: by_stem.number_of_units_net.get(s, np.nan) if s else np.nan)
    return it


# ── figures ──────────────────────────────────────────────────────────────
def fig_riskset(it: pd.DataFrame, asr: dict):
    fig, axes = plt.subplots(2, 1, figsize=(7.2, 5.4))
    ax = axes[0]
    g = it[it.has_parcel].groupby("year")
    n = g.size()
    for col, lab, c in (("par_hit", "block+lot found in the parcel layer", "#2f6f4f"),
                        ("par_active", "… and the parcel is still active", "#5b7fa6"),
                        ("asr_hit", "block+lot found in the assessor roll", "#b07d2b")):
        s = g[col].mean() * 100
        s = s.mask(n < MIN_RATE_N)
        ax.plot(s.index, s.values, color=c, alpha=0.25, lw=0.9)
        ax.plot(s.index, s.rolling(SMOOTH_YEARS, center=True, min_periods=1).mean(),
                color=c, lw=2.0, label=lab)
    ax.set_ylabel("% of items carrying a parcel key")
    ax.set_ylim(0, 105)
    ax.set_xlabel("hearing year")
    ax.legend(frameon=False, fontsize=7.5, loc="lower left")
    ax.set_title("The parcel key joins; the assessor roll does not reach back\n"
                 f"({SMOOTH_YEARS}-year centred mean over the faint annual series)",
                 loc="left")
    ax = axes[1]
    yc = pd.Series(asr["year_counts"]).sort_index()
    ax.bar(yc.index, yc.values / 1000, color="#b07d2b", width=0.8)
    ax.set_ylabel("parcels on the roll\n(thousands)")
    ax.set_xlabel("closed roll year")
    ax.set_xticks(list(yc.index)[::2])                 # roll years are integers, not 2007.5
    ax.set_title("Assessor secured roll, parcels per roll year — the panel is "
                 f"{min(asr['years'])}–{max(asr['years'])}, not 1998–2026", loc="left")
    fig.tight_layout()
    fig.savefig(FIG / "fig_parcel_riskset.pdf")
    plt.close(fig)


def fig_case_join(cases: pd.DataFrame):
    g = cases.groupby("year")
    n = g.size()
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    for col, lab, c in (("j_any", "case number or its stem found in a planning record",
                         "#2f6f4f"),
                        ("j_norm", "case number found (normalised)", "#5b7fa6"),
                        ("j_raw", "case number found (raw string)", "#a33")):
        s = (g[col].mean() * 100).mask(n < MIN_CASES_FIG)
        ax.plot(s.index, s.values, color=c, alpha=0.25, lw=0.9)
        ax.plot(s.index, s.rolling(SMOOTH_YEARS, center=True, min_periods=1).mean(),
                color=c, lw=2.0, label=lab)
    ax.set_ylabel("% of cases heard that year")
    ax.set_xlabel("hearing year")
    ax.set_ylim(0, 105)
    ax.legend(frameon=False, fontsize=7.5, loc="lower right")
    ax.set_title("Planning Department records reach back further than the modern case "
                 "format does\n(years with fewer than 10 cases are left blank)", loc="left")
    fig.tight_layout()
    fig.savefig(FIG / "fig_case_join.pdf")
    plt.close(fig)


def fig_corelogic(cl: dict, it: pd.DataFrame):
    """The price side of the risk set: what a parcel sold for, and how often the Commission
    is looking at a parcel with a price near the hearing."""
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.4))
    ax = axes[0]
    s, n = cl["series"], cl["series_n"]
    keep = (s.index >= 1990) & (n >= DENSE_YEAR_SALES)
    vol = ax.twinx()
    vol.bar(n.index[keep], n[keep] / 1000, color="#ccc", width=0.8, zorder=0)
    vol.set_ylabel("sales (thousands)", color="#999")
    vol.set_ylim(0, n[keep].max() / 1000 * 3.2)        # keep the bars low, behind the line
    vol.spines["top"].set_visible(False)
    ax.set_zorder(vol.get_zorder() + 1)
    ax.patch.set_visible(False)
    ax.plot(s.index[keep], s[keep] / 1e6, color="#5b7fa6", alpha=0.3, lw=0.9)
    ax.plot(s.index[keep], (s[keep] / 1e6).rolling(SMOOTH_YEARS, center=True,
                                                   min_periods=1).mean(),
            color="#5b7fa6", lw=2.2)
    ax.set_ylabel("median sale price (\\$m)", color="#5b7fa6")
    ax.set_xlabel("sale year")
    ax.set_title(f"San Francisco arms-length sales, Cotality\n"
                 f"(bars: annual volume; {SMOOTH_YEARS}-yr centred mean)",
                 loc="left", fontsize=8.5)
    ax = axes[1]
    wp = it[it.has_parcel]
    g = wp.groupby("year")
    n_i = g.size()
    # A hearing in year Y needs sales through Y + the window before the "traded near the
    # hearing" rate is even defined. The extract stops in cl["max_date"], so the last few
    # hearing years are censored, not falling --- draw the gap rather than the artefact.
    cutoff = int(cl["max_date"][:4]) - TRADE_WINDOW_DAYS // 365
    for col, lab, c, mask_late in (
            ("cl_ever", "parcel ever traded", "#5b7fa6", False),
            ("cl_near", f"parcel traded within {TRADE_WINDOW_DAYS // 365} years of the "
                        f"hearing", "#2f6f4f", True)):
        ser = (g[col].mean() * 100).mask(n_i < MIN_RATE_N)
        if mask_late:
            ser = ser.mask(ser.index > cutoff)
        ax.plot(ser.index, ser.values, color=c, alpha=0.25, lw=0.9)
        ax.plot(ser.index, ser.rolling(SMOOTH_YEARS, center=True, min_periods=1).mean(),
                color=c, lw=2.0, label=lab)
    ax.axvspan(cutoff, wp.year.max(), color="#f2f2f2", zorder=0)
    ax.text(cutoff + 0.3, 4, f"window runs past\nthe extract\n({cl['max_date']})",
            fontsize=6.5, color="#888", va="bottom")
    ax.set_ylim(0, 100)
    ax.set_ylabel("% of items with a parcel key")
    ax.set_xlabel("hearing year")
    ax.legend(frameon=False, fontsize=7.5, loc="upper left")
    ax.set_title("A price contemporaneous with\nthe decision", loc="left", fontsize=8.5)
    fig.tight_layout()
    fig.savefig(FIG / "fig_corelogic.pdf")
    plt.close(fig)


def fig_filing_lag(d: pd.DataFrame):
    ok = d[d.lag.notna()]
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.4))
    x = ok.loc[ok.lag.between(0, LAG_TRUNC_DAYS), "lag"]
    axes[0].hist(x, bins=50, color="#5b7fa6")
    med, p90 = ok.lag.median(), ok.lag.quantile(0.9)
    axes[0].axvline(med, color="#a33", lw=1.5)
    axes[0].annotate(f"median {med:.0f} d", xy=(med, axes[0].get_ylim()[1]),
                     xytext=(6, -10), textcoords="offset points", color="#a33", fontsize=8)
    axes[0].set_xlabel("days, filing → first hearing")
    axes[0].set_ylabel("cases")
    axes[0].set_title("The wait the delay memo could not see", loc="left", fontsize=9)
    g = ok.groupby("year").lag
    n, m = g.size(), g.median()
    q1, q3 = g.quantile(0.25), g.quantile(0.75)
    keep = n >= MIN_CASES_FIG
    axes[1].fill_between(m.index[keep], q1[keep], q3[keep], color="#5b7fa6", alpha=0.2,
                         label="interquartile range")
    axes[1].plot(m.index[keep], m[keep], color="#5b7fa6", alpha=0.3, lw=0.9)
    axes[1].plot(m.index[keep],
                 m[keep].rolling(SMOOTH_YEARS, center=True, min_periods=1).mean(),
                 color="#2f6f4f", lw=2.0, label="median (3-yr centred mean)")
    axes[1].set_xlabel("hearing year")
    axes[1].set_ylabel("days, filing → first hearing")
    axes[1].legend(frameon=False, fontsize=7.5)
    axes[1].set_title("and how it moved", loc="left", fontsize=9)
    fig.tight_layout()
    fig.savefig(FIG / "fig_filing_lag.pdf")
    plt.close(fig)


# ── tables ───────────────────────────────────────────────────────────────
def T(s):
    """Escape a *plain* string for LaTeX. Never pass it something that already carries
    markup --- an earlier draft did, and silently ate the backslash off every
    \\texttt{}."""
    return (str(s).replace("\\", r"\textbackslash{}").replace("_", r"\_")
            .replace("&", r"\&").replace("%", r"\%").replace("#", r"\#")
            .replace("$", r"\$"))


def BRK(s: str) -> str:
    """Escaped text with a break opportunity after every underscore and slash. Field names
    like \\texttt{number\\_of\\_units\\_certified} do not fit a narrow \\texttt{p\\{\\}}
    column and \\texttt{} suppresses hyphenation, so the break has to be put there."""
    return T(s).replace(r"\_", r"\_\allowbreak ").replace("/", r"/\allowbreak ")


def TT(s: str) -> str:
    return rf"\texttt{{{BRK(s)}}}"


def N(x) -> str:
    """A LaTeX-safe thousands separator: 16{,}199, which does not pick up maths spacing."""
    return f"{int(x):,}".replace(",", "{,}")


def macros(d: dict) -> str:
    L = ["% GENERATED BY acquire_external_data.py — do not edit by hand.",
         "% Every number that appears in the memo's prose is defined here."]
    for k, v in sorted(d.items()):
        L.append(rf"\newcommand{{\{k}}}{{{v}}}")
    return "\n".join(L) + "\n"


def write_tables(ctx: dict):
    L = ["% GENERATED BY acquire_external_data.py — do not edit by hand."]
    a = L.append
    P, asr, cj, lag, br, fees = (ctx["parcels"], ctx["assessor"], ctx["cases"],
                                 ctx["lag"], ctx["bridge"], ctx["fees"])
    pr = ctx["probe"]

    # ── T1 the inventory ────────────────────────────────────────────────
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Every identifier resolved against the live catalogue on %s. `Key unique' "
      r"is tested on the cached rows, not assumed: DBI's \texttt{permit\_number} was not "
      r"unique and 145{,}795 rows repeated one. A dash means the column is not a key we "
      r"join on.}\label{tab:inventory}" % T(pr["retrieved"]))
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{llrrll}\toprule")
    a(r"Dataset & Identifier & Rows & Distinct key & Key unique & Date range\\\midrule")
    last = None
    for name, row in ctx["inventory"]:
        if row["category"] != last:
            a(rf"\multicolumn{{6}}{{l}}{{\emph{{{T(row['category'])}}}}}\\")
            last = row["category"]
        a(rf"\quad {T(row['label'])} & \texttt{{{T(row['id'])}}} & {N(row['rows'])} & "
          rf"{N(row['distinct'])} & {row['unique']} & {row['range']}\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")

    # ── T2 the parcel join ──────────────────────────────────────────────
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The parcel join, and what the residual is. The item table's key is "
      r"\texttt{assessor\_block} plus each entry of \texttt{lot\_number}, zero-padded to "
      r"DataSF's 4+3 \texttt{blklot} form; an item joins if any of its lots does. "
      r"Percentages are of the %s items that carry a parcel key at "
      r"all.}\label{tab:parceljoin}" % N(P["with_key"]))
    a(r"\begin{tabular}{lrr}\toprule")
    a(r"Items & Count & Share\\\midrule")
    w = P["with_key"]
    for lab, k, base in (
            ("All items in the corpus", P["items"], P["items"]),
            (r"\quad carrying a block and at least one lot", w, P["items"]),
            (r"\quad\quad joined to \texttt{blklot} in the parcel layer", P["joined"], w),
            (r"\quad\quad\quad \dots\ to a parcel still active", P["joined_active"], w),
            (r"\quad\quad\quad \dots\ to a retired parcel only", P["retired_only"], w),
            (r"\quad\quad joined to \texttt{mapblklot} (the surviving map parcel)",
             P["joined_map"], w),
            (r"\quad\quad not joined: block exists, that lot does not",
             P["block_exists_lot_not"], w),
            (r"\quad\quad not joined: no such block", P["no_block_at_all"], w)):
        a(rf"{lab} & {N(k)} & {100*k/base:.1f}\%\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    # ── T3 the assessor panel ───────────────────────────────────────────
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The assessor roll as a parcel-year panel, and how far it reaches. The "
      r"panel is unbalanced by construction --- a parcel enters when it is created and "
      r"leaves when it is merged away --- and it starts at roll year %d, which is the "
      r"binding constraint on the risk set.}\label{tab:assessor}" % min(asr["years"]))
    a(r"\begin{tabular}{lr}\toprule")
    a(r"Quantity & Value\\\midrule")
    for lab, v in (("Rows (parcel-years)", N(asr["rows"])),
                   ("Distinct parcels", N(asr["distinct_parcels"])),
                   ("Roll years", rf"{min(asr['years'])}--{max(asr['years'])} "
                                  rf"({len(asr['years'])} years)"),
                   ("Distinct item parcel keys sought", N(asr["item_keys_wanted"])),
                   ("\\quad found on the roll", N(asr["item_keys_found"])),
                   ("\\quad\\quad median roll years observed for one of them",
                    f"{asr['median_years_per_parcel']:.0f} of {len(asr['years'])}"),
                   ("\\quad\\quad present in every roll year",
                    N(asr["parcels_all_years"])),
                   ("Items with a parcel key joined to the roll",
                    rf"{N(asr['items_joined'])} "
                    rf"({100*asr['items_joined']/asr['items_with_key']:.1f}\%)")):
        a(rf"{lab} & {v}\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    # ── T4 zoning ───────────────────────────────────────────────────────
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Zoning, by how it is published. The parcel-keyed years join to the item "
      r"table directly; the polygon years need a spatial join and are inventoried, not "
      r"cached. 1999 is not published in either form.}\label{tab:zoning}")
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{llrrl}\toprule")
    a(r"Year & Identifier & Rows & Distinct \texttt{blklot} & Form\\\midrule")
    for r in ctx["zoning_rows"]:
        ident = "---" if r["id"] == "---" else rf"\texttt{{{T(r['id'])}}}"
        a(rf"{T(r['year'])} & {ident} & {N(r['rows'])} & "
          rf"{r['distinct'] if isinstance(r['distinct'], str) else N(r['distinct'])} & "
          rf"{r['form']}\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")

    # ── T5 the case-number join ─────────────────────────────────────────
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Does a planning record's identifier join to the item table's case number? "
      r"Tested four ways over all %s distinct cases. `Stem' strips the letter suffix "
      r"(\texttt{2014.0400CUA}$\to$\texttt{2014.0400}), which is how a suffixed case reaches "
      r"its umbrella project record; `expanded' rewrites the two-digit year the minutes "
      r"printed before 2002 (\texttt{98.426D}$\to$\texttt{1998.426D}) against the hearing "
      r"year. The century is never guessed: %d cases would have matched a real record under "
      r"\emph{both} centuries.}\label{tab:casejoin}"
      % (N(cj["n"]), cj["century_ambiguous"]))
    a(r"\begin{tabular}{lrr}\toprule")
    a(r"Join & Cases matched & Rate\\\midrule")
    for lab, k in (("Raw string equality", cj["raw"]),
                   ("Normalised (upper-case, whitespace stripped)", cj["norm"]),
                   ("Suffix-stripped stem", cj["stem"]),
                   ("Two-digit year expanded against the hearing year", cj["exp"]),
                   (r"\textbf{Any of them}", cj["any"])):
        bold = lab.startswith(r"\textbf")
        v = rf"\textbf{{{100*k/cj['n']:.1f}\%}}" if bold else rf"{100*k/cj['n']:.1f}\%"
        a(rf"{lab} & {N(k)} & {v}\\")
    a(r"\midrule")
    for lo, hi in ((1998, 2004), (2005, 2009), (2010, 2014), (2015, 2019), (2020, 2026)):
        g = cj["frame"][(cj["frame"].year >= lo) & (cj["frame"].year <= hi)]
        if not len(g):
            continue
        a(rf"\quad heard {lo}--{hi} & {N(int(g.j_any.sum()))}\,/\,{N(len(g))} & "
          rf"{100*g.j_any.mean():.1f}\%\\")
    a(r"\midrule")
    a(r"\multicolumn{3}{l}{\emph{The residual, by the format the minutes print the case "
      r"number in}}\\")
    for lab, n, k in ctx["case_formats"]:
        a(rf"\quad \texttt{{{T(lab)}}} & {N(n-k)} unmatched of {N(n)} & "
          rf"{100*k/n:.1f}\% matched\\")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    # ── T6 filing → hearing ─────────────────────────────────────────────
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Filing to first hearing, in days, for cases that join a planning record "
      r"with an \texttt{open\_date}. One observation per case. The negative tail is real "
      r"and is reported rather than trimmed: a record opened after the hearing is a record "
      r"created for a follow-on action, and it is a reason to use the "
      r"\emph{earliest} open date of the case, which is what is done "
      r"here.}\label{tab:filinglag}")
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{lrrrrrr}\toprule")
    a(r"Cases & $N$ & Coverage & Median & 90th pct & 99th pct & Negative\\\midrule")
    for lab, g in ctx["lag_groups"]:
        ok = g[g.lag.notna()]
        if len(ok) < MIN_CASES_FIG:
            continue
        a(rf"{T(lab)} & {N(len(g))} & {100*len(ok)/len(g):.1f}\% & {ok.lag.median():.0f} & "
          rf"{ok.lag.quantile(.9):.0f} & {ok.lag.quantile(.99):.0f} & "
          rf"{100*(ok.lag < 0).mean():.1f}\%\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")

    # ── T7 the permit bridge ────────────────────────────────────────────
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{The \texttt{building\_permits} bridge, by request type. A permit is reached "
      r"if the case's own planning record, its parent project record, or any record sharing "
      r"its stem names one; `in DBI' means the number, normalised to digits, is present in "
      r"the cached Building Permits table. The permit-number column is the route "
      r"\texttt{analyze\_permits.py} already had, for comparison, and the last column is "
      r"the union of the two --- the share of the docket that now reaches a real building "
      r"permit by some route.}\label{tab:bridge}")
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{lrrrrr}\toprule")
    a(r"Request type & Items & Reaches a permit & \dots\ present in DBI & "
      r"Printed permit number & Either route\\\midrule")
    for rt, n, reach, indbi, printed, either in ctx["bridge_rows"]:
        a(rf"\texttt{{{T(rt)}}} & {N(n)} & {100*reach/n:.1f}\% & {100*indbi/n:.1f}\% & "
          rf"{100*printed/n:.1f}\% & {100*either/n:.1f}\%\\")
    a(r"\midrule")
    n, reach, indbi, printed, either = ctx["bridge_all"]
    a(rf"All items & {N(n)} & \textbf{{{100*reach/n:.1f}\%}} & "
      rf"\textbf{{{100*indbi/n:.1f}\%}} & {100*printed/n:.1f}\% & "
      rf"\textbf{{{100*either/n:.1f}\%}}\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")

    # ── T8 what else the records carry ──────────────────────────────────
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{What the two planning-records tables carry that the item table does not, "
      r"with the share of rows on which the field is actually populated. Documentation is "
      r"not evidence: these are the rows.}\label{tab:recordfields}")
    a(r"\resizebox{\textwidth}{!}{%")
    a(r"\begin{tabular}{lllrr}\toprule")
    a(r"Field & Table & What it is & Populated & Rows\\\midrule")
    for fld, tbl, what, pct, rows in ctx["record_fields"]:
        a(rf"\texttt{{{T(fld)}}} & {T(tbl)} & {T(what)} & {pct:.1f}\% & {N(rows)}\\")
    a(r"\bottomrule\end{tabular}}\end{table}")
    a("")

    # ── T9 project scale ────────────────────────────────────────────────
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Project scale: what each source offers, and what share of the %s "
      r"conditional-use items in the item table it actually reaches. `Join key available' "
      r"is a property of the file; the last column is the "
      r"measurement.}\label{tab:scale}" % N(ctx["n_cu"]))
    a(r"\begin{tabular}{@{}p{2.8cm}p{3.4cm}p{3.0cm}p{1.8cm}p{3.0cm}@{}}\toprule")
    a(r"Source & Unit field & Join key & Years & CU items reached\\\midrule")
    for r in ctx["scale_rows"]:
        a(rf"{r['src']} & {TT(r['unit'])} & {r['key']} & {r['years']} & "
          rf"{r['reach']}\\[2pt]")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    # ── T10 prices ──────────────────────────────────────────────────────
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Prices and rents. The first row is the one that matters: a licensed "
      r"transaction file, already on disk, that joins to the item table on the same parcel "
      r"key as everything else in \S2. The rows below it are the fallbacks, in descending "
      r"order of quality, and are what an analysis would be left with if the licence "
      r"lapsed.}\label{tab:prices}")
    a(r"\begin{tabular}{@{}p{2.5cm}p{2.9cm}p{2.9cm}p{5.9cm}@{}}\toprule")
    a(r"Source & Status & Grain & What it gives\\\midrule")
    for r in ctx["price_rows"]:
        a(rf"{r['src']} & {r['status']} & {r['grain']} & {r['what']}\\[2pt]")
    a(r"\bottomrule\end{tabular}\end{table}")
    a("")

    # ── T11 fees ────────────────────────────────────────────────────────
    if len(fees):
        a(r"\begin{table}[htbp]\centering")
        a(r"\caption{The Citywide Development Impact Fee Register, one per year. `Rate "
          r"observations' counts every dollar figure the register prints; `sections' is the "
          r"number of distinct Planning Code (or other code) sections those rates are keyed "
          r"to. The effective date is read out of the register's own cover line, not "
          r"inferred from when the file was retrieved.}\label{tab:fees}")
        a(r"\begin{tabular}{lrrrl}\toprule")
        a(r"Register & Pages & Rate observations & Code sections & Effective\\\midrule")
        for r in ctx["fee_rows"]:
            a(rf"{r['year']} & {r['pages']} & {N(r['rates'])} & {N(r['fees'])} & "
              rf"{T(r['effective'])}\\")
        a(r"\bottomrule\end{tabular}\end{table}")
        a("")

        a(r"\begin{table}[htbp]\centering")
        a(r"\caption{Rates behind the largest obligation-imposing condition headings in the "
          r"conditions memo, as each register prints them, keyed by Planning Code section "
          r"and by the register's own land-use band. Dollars per gross square foot except "
          r"\S415, which the register states per square foot of residential gross floor "
          r"area. A dash is a band the register of that year does not print. These are the "
          r"rates in force in January of the register year; the Article~4 reduction adopted "
          r"in August 2026 post-dates the last register cached "
          r"here.}\label{tab:feerates}")
        a(r"\resizebox{\textwidth}{!}{%")
        cols = ctx["fee_years"]
        a(r"\begin{tabular}{l" + "r" * len(cols) + r"}\toprule")
        a(r"Fee & " + " & ".join(str(c) for c in cols) + r"\\\midrule")
        for nm, vals in ctx["fee_matrix"]:
            a(nm + " & " + " & ".join(vals) + r"\\")
        a(r"\bottomrule\end{tabular}}\end{table}")
        a("")

    # ── T12 negatives ───────────────────────────────────────────────────
    a(r"\begin{table}[htbp]\centering")
    a(r"\caption{Negative results. A row here means the search was run and is not to be "
      r"run again. The first two are carried over from the conditions memo and were not "
      r"re-run.}\label{tab:negatives}")
    a(r"\begin{tabular}{@{}p{4.0cm}p{4.4cm}p{5.8cm}@{}}\toprule")
    a(r"What was sought & Where it was looked for & Result\\\midrule")
    for w, wh, res in ctx["negatives"]:
        a(rf"{w} & {wh} & {res}\\[2pt]")
    a(r"\bottomrule\end{tabular}\end{table}")

    TAB.mkdir(parents=True, exist_ok=True)
    (TAB / "acquisition_tables.tex").write_text("\n".join(L) + "\n")
    print("→", TAB / "acquisition_tables.tex")


# ── the external-directory README ────────────────────────────────────────
def _md(s: str) -> str:
    """Source labels are written for LaTeX; the README is Markdown, where `---` and `--`
    are three and two hyphens rather than dashes."""
    return str(s).replace("---", "—").replace("--", "–")



def write_readme(ctx: dict):
    L = ["# `external/` — cached third-party data",
         "",
         "Written by `code/commission_minutes_processing/acquire_external_data.py`.",
         "One directory per source. Nothing here is in git; re-fetch with",
         "`python acquire_external_data.py fetch --refresh`.",
         "",
         f"Last acquisition run: **{pd.Timestamp.today().date().isoformat()}**",
         "",
         "| Source | Resolved identifier | Host | Rows cached | Retrieved | Path |",
         "|---|---|---|---|---|---|"]
    for r in ctx["readme_rows"]:
        L.append(f"| {_md(r['label'])} | `{r['id']}` | {r['host']} | "
                 f"{r['rows']} | {r['retrieved']} | `{r['path']}` |")
    for k, (url, label) in ZILLOW.items():                # not Socrata: no identifier
        p = EXT / "prices" / f"{k}_sf.csv.gz"
        if p.exists():
            n = len(pd.read_csv(p, low_memory=False))
            L.append(f"| Zillow {_md(label)} | — | files.zillowstatic.com | {n:,} "
                     f"(San Francisco County only) | {ctx['probe'].get('retrieved','')} | "
                     f"`prices/{k}_sf.csv.gz` |")
    if CL_CACHE.exists():
        meta = json.loads(CL_META.read_text()) if CL_META.exists() else {}
        L.append(f"| CoreLogic/Cotality — San Francisco slice | — | local, licensed | "
                 f"{meta.get('sf_rows', 0):,} | {meta.get('built','')} | "
                 f"`{CL_CACHE.relative_to(EXT)}` |")
    for y in sorted(FEE_REGISTERS):
        p = FEES_DIR / f"impact_fee_register_{y}.pdf"
        if p.exists():
            L.append(f"| Citywide Development Impact Fee Register {y} | — | "
                     f"sfplanning.org / web.archive.org | {p.stat().st_size:,} bytes | "
                     f"{ctx['probe'].get('retrieved','')} | "
                     f"`fees/impact_fee_register_{y}.pdf` |")
    L += ["",
          "Two directories here predate this script and are written by others:",
          "`cpc_packets/` (`analyze_conditions.py`) and `datasf/dbi_permits.csv.gz`",
          "(`analyze_permits.py`). They are not re-fetched by `acquire_external_data.py`.",
          "",
          "## Staleness",
          "",
          "Compare the row count above against a fresh `count(1)` on the resource endpoint;",
          "`probe` does exactly that and rewrites `_acquisition/probe.json`. The Planning",
          "Department records and the parcel layer grow daily; the assessor roll grows once",
          "a year; the fee registers are republished each January at one URL, so the copy",
          "here is the only dated copy that will exist.",
          "",
          "## Not cached here, on purpose",
          "",
          "- Polygon-only zoning layers (2006, 2009–2015, current) — they need a spatial",
          "  join, and the parcel-keyed years 1998–2008 plus `acdm-wktn`'s own",
          "  `zoning_code` cover what this memo measures.",
          "- The raw CoreLogic/Cotality extracts — they are licensed and already live under",
          "  `$MFHR_DATA_ROOT/demand/corelogic/`, written by `demand_estimation/`. Only the",
          "  San Francisco slice with the parcel key attached is derived into `prices/`, by",
          "  `fetch --only corelogic`. Nothing is downloaded for it.",
          "",
          "## Dropbox online-only files",
          "",
          "The macOS file provider keeps files dehydrated: the directory entry reports the",
          "full size while no blocks are allocated, and an un-materialised directory does not",
          "appear in `ls` or `find` at all. `acquire_external_data.py sync` reports nominal",
          "bytes against bytes on disk and materialises what is missing. Run it before",
          "concluding that anything under `demand/` is gone.",
          ""]
    (EXT / "README.md").write_text("\n".join(L) + "\n")
    print("→", EXT / "README.md")


# ── the report itself ────────────────────────────────────────────────────
def report():
    FIG.mkdir(parents=True, exist_ok=True)
    TAB.mkdir(parents=True, exist_ok=True)
    pr = json.loads(PROBE_JSON.read_text()) if PROBE_JSON.exists() else {"retrieved": "",
                                                                         "datasets": {},
                                                                         "polygon_zoning": {}}
    it = load_items()
    print(f"{len(it):,} items, {it.cn.ne('').sum():,} with a case number, "
          f"{it[it.cn.ne('')].cn.nunique():,} distinct cases")

    par = read_cached("parcels", usecols=["mapblklot", "blklot", "block_num", "lot_num",
                                          "active", "date_rec_add", "date_rec_drop"])
    P = join_parcels(it, par)
    it = P.pop("frame")
    asr = join_assessor(it)
    it = asr.pop("frame")
    rec = build_records()
    cj = join_records(it, rec)
    cj["frame"] = cj.pop("cases")
    lag = filing_lag(it, rec)
    stems = dbi_stems()
    br = permit_bridge(it, rec, stems)
    it = br["frame"]
    it = units_from_records(it)
    cl_sf = corelogic_sf(it)
    if cl_sf:
        _have = set(pd.read_parquet(CL_CACHE, columns=["blklot"]).blklot.dropna())
        it["cl_ever"] = it.parcels.map(lambda ps: bool(ps & _have))
        _al = pd.read_parquet(CL_CACHE, columns=["blklot", "sale_date", "sale_amount",
                                                 "arms_length"])
        _al["sale_date"] = pd.to_datetime(_al.sale_date, errors="coerce")
        _al = _al[_al.arms_length.astype("boolean").fillna(False)
                  & _al.sale_amount.gt(1000) & _al.sale_date.notna()]
        _by = {k: g.sale_date.values for k, g in _al.groupby("blklot")}
        _w = np.timedelta64(TRADE_WINDOW_DAYS, "D")

        def _near(r):
            for q in r.parcels:
                d = _by.get(q)
                if d is not None and np.any(np.abs(d - np.datetime64(r.meeting_date)) <= _w):
                    return True
            return False
        it["cl_near"] = [_near(r) for r in it.itertuples()]
    else:
        it["cl_ever"] = it["cl_near"] = False
    fees = parse_fee_registers()

    # ── inventory rows ──────────────────────────────────────────────────
    inventory, readme_rows = [], []
    for name, src in SOCRATA_SOURCES.items():
        p = path_of(src)
        if not p.exists():
            continue
        key = _resolve_key(p, src["key"])
        try:
            col = pd.read_csv(p, dtype=str, usecols=[key], low_memory=False)[key]
            nrows, ndist = len(col), col.nunique(dropna=True)
        except Exception:
            nrows = sum(1 for _ in gzip.open(p, "rt")) - 1
            ndist = 0
        rng = _date_range(p, name)
        meta = pr["datasets"].get(name, {})
        inventory.append((name, {
            "label": src["label"], "id": src["id"], "category": src["category"],
            "rows": nrows, "distinct": ndist,
            "unique": "yes" if ndist == nrows else f"no ({N(nrows-ndist)} repeats)",
            "range": rng}))
        readme_rows.append({"label": src["label"], "id": src["id"], "host": SOCRATA_HOST,
                            "rows": f"{nrows:,}", "retrieved": pr.get("retrieved", ""),
                            "path": str(p.relative_to(EXT))})
    inventory.sort(key=lambda kv: (kv[1]["category"], kv[1]["label"]))

    # ── zoning table ────────────────────────────────────────────────────
    zrows = []
    for name, src in SOCRATA_SOURCES.items():
        if not name.startswith("zoning_"):
            continue
        p = path_of(src)
        if not p.exists():
            continue
        d = pd.read_csv(p, dtype=str, low_memory=False)
        kc = "blklot" if "blklot" in d.columns else "mapblklot"
        zrows.append({"year": name.split("_")[1], "id": src["id"], "rows": len(d),
                      "distinct": d[kc].nunique(dropna=True),
                      "form": rf"parcel-keyed (\texttt{{{kc}}})"})
    zrows.append({"year": "1999", "id": "---", "rows": 0, "distinct": "---",
                  "form": "not published"})
    for lab, m in pr.get("polygon_zoning", {}).items():
        zrows.append({"year": lab, "id": m.get("id", ""), "rows": m.get("rows") or 0,
                      "distinct": "---", "form": "polygon; needs a spatial join"})
    zrows.sort(key=lambda r: (r["year"][:4], r["year"]))

    # ── the bridge table ────────────────────────────────────────────────
    it["printed_permit"] = _printed_permits(it)
    it["either_route"] = it.bridge_in_dbi | it.printed_permit
    brows = []
    for rt, g in it[it.request_type.ne("")].groupby("request_type"):
        if len(g) < MIN_ITEMS_BRIDGE:
            continue
        brows.append((rt, len(g), int((g.n_bridge > 0).sum()), int(g.bridge_in_dbi.sum()),
                      int(g.printed_permit.sum()), int(g.either_route.sum())))
    brows.sort(key=lambda r: -r[3] / r[1])
    ball = (len(it), int((it.n_bridge > 0).sum()), int(it.bridge_in_dbi.sum()),
            int(it.printed_permit.sum()), int(it.either_route.sum()))

    # ── record-field coverage ───────────────────────────────────────────
    npj = read_cached("records_nonproject")
    prj = read_cached("records_project")
    rf = []
    for fld, what in (("open_date", "date the record was opened"),
                      ("close_date", "date it was closed"),
                      ("record_status", "application status"),
                      ("applicant", "applicant name"),
                      ("applicant_org", "applicant organisation"),
                      ("assigned_to_planner", "case planner"),
                      ("building_permits", "associated permit numbers"),
                      ("parent_id", "the umbrella project record")):
        if fld in npj.columns:
            rf.append((fld, "non-project", what, 100 * npj[fld].notna().mean(), len(npj)))
    for fld, what in (("number_of_units_prop", "proposed dwelling units"),
                      ("number_of_units_net", "net new dwelling units"),
                      ("number_of_affordable_units", "affordable units"),
                      ("project_decision", "the department's decision"),
                      ("project_decision_date", "decision date"),
                      ("environmental_document_type", "CEQA document type"),
                      ("building_permits", "associated permit numbers"),
                      ("inclusionary", "inclusionary flag")):
        if fld in prj.columns:
            rf.append((fld, "project", what, 100 * prj[fld].notna().mean(), len(prj)))

    # ── project scale ───────────────────────────────────────────────────
    cu = it[it.request_type.eq("conditional_use")]
    n_cu = len(cu)
    pipe = read_cached("pipeline")
    pipe["cn"] = pipe.case_no.map(norm_case)
    pipe["stem"] = pipe.case_no.map(case_stem)
    hp = read_cached("housing_production")
    hp["stem"] = hp.get("ppts_project_id", pd.Series(dtype=str)).map(case_stem)
    mo = read_cached("mohcd_pipeline")
    mo["stem"] = mo.planning_case_number.map(case_stem)
    uc = read_cached("unit_completions")

    pipe_stems = set(pipe.stem) - {""}
    hp_stems = set(hp.stem) - {""}
    mo_stems = set(mo.stem) - {""}
    hp_parcels = set(hp.get("blocklot", pd.Series(dtype=str)).dropna())
    cu_reach = {
        "pipeline": cu.stem.isin(pipe_stems).mean(),
        "hp_case": cu.stem.isin(hp_stems).mean(),
        "hp_parcel": np.mean([bool(ps & hp_parcels) for ps in cu.parcels]),
        "mohcd": cu.stem.isin(mo_stems).mean(),
        # `number_of_units_prop` is never null and is zero on most rows --- a project that
        # proposes no dwelling units. "Populated" would be 100% and would mean nothing, so
        # the non-zero share is reported next to it.
        "prj_units": cu.units_prj.notna().mean(),
        "prj_units_nz": (cu.units_prj.fillna(0) > 0).mean(),
        # "The file reaches back to 2005" is a property of the file. What matters is whether
        # a *hearing* from before then gets a unit count, so measure that instead.
        "units_nz_early": (cu.loc[cu.year < SCALE_ERA, "units_prj"].fillna(0) > 0).mean(),
        "units_nz_late": (cu.loc[cu.year >= SCALE_ERA, "units_prj"].fillna(0) > 0).mean(),
    }
    scale_rows = [
        dict(src="Planning Records --- Projects", unit="number_of_units_prop",
             key="case-number stem", years=_yr_range(prj, "open_date"),
             reach=rf"{100*cu_reach['prj_units']:.1f}\% populated, "
                   rf"{100*cu_reach['prj_units_nz']:.1f}\% non-zero"),
        dict(src="SF Development Pipeline", unit="net_pipeline_units",
             key=BRK("case_no") + r" (PRJ) $+$ blklot",
             years=_yr_range(pipe, "current_status_date"),
             reach=rf"{100*cu_reach['pipeline']:.1f}\%"),
        dict(src="Housing Production 2005--", unit="net_units",
             key=BRK("ppts_project_id") + r" $+$ blocklot",
             years=_yr_range(hp, "first_completion_date"),
             reach=rf"{100*cu_reach['hp_case']:.1f}\% by case, "
                   rf"{100*cu_reach['hp_parcel']:.1f}\% by parcel"),
        dict(src="MOHCD Affordable Pipeline", unit="total_project_units",
             key=BRK("planning_case_number"), years=_yr_range(mo, "entitlement_approval"),
             reach=rf"{100*cu_reach['mohcd']:.1f}\%"),
        dict(src="Unit Completion Counts", unit="number_of_units_certified",
             key="building permit number", years=_yr_range(uc, "date_issued"),
             reach="via the permit bridge only"),
    ]

    # ── prices ──────────────────────────────────────────────────────────
    cl = corelogic_status()
    zmeta = _zillow_meta()
    price_rows = [
        dict(src="CoreLogic / Cotality",
             status=("cached, licensed" if cl["present"] else "absent"),
             grain=r"transaction $\times$ parcel",
             what=(rf"{N(cl_sf['priced'])} arms-length priced San Francisco sales "
                   rf"{cl_sf['dense_first']}--{cl_sf['last']} over "
                   rf"{N(cl_sf['parcels'])} parcels, "
                   rf"joined on \texttt{{blklot}}"
                   if cl_sf else "sale prices and characteristics")),
        dict(src="CoStar", status="institutional access question",
             grain="property and lease", what="commercial rents; not attempted, not scraped"),
        dict(src="Zillow ZHVI", status="cached", grain=zmeta["zhvi_grain"],
             what=zmeta["zhvi_what"]),
        dict(src="Zillow ZORI", status="cached", grain=zmeta["zori_grain"],
             what=zmeta["zori_what"]),
        dict(src="Assessor assessed values", status="cached",
             grain="parcel-year",
             what=f"land and improvement value, {min(asr['years'])}--{max(asr['years'])}; "
                  f"Proposition 13 makes it a function of tenure"),
        dict(src="ACS rents and values", status="handled by " + TT("demand_estimation"),
             grain="tract", what="median rent and value; the demand pipeline collects it"),
    ]

    # ── fees ────────────────────────────────────────────────────────────
    fee_rows, fee_years, fee_matrix = _fee_tables(fees)

    negatives = [
        ("Condition-of-approval text", r"DBI Building Permits (\texttt{i98e-djp9})",
         r"87 of 1{,}295{,}048 rows mention one --- conditions memo \S4, not re-run"),
        ("A commission-actions dataset", "DataSF catalogue",
         r"does not exist --- conditions memo \S4, not re-run"),
        ("Assessor roll before 2007", r"\texttt{wv5m-vpq2}",
         f"the roll starts at {min(asr['years'])}; no earlier vintage is published"),
        ("Parcel-keyed zoning for 1999", "DataSF historic zoning series",
         "1998 and 2000 exist; 1999 does not"),
        ("Parcel-keyed zoning after 2008", "DataSF historic zoning series",
         "2009--2015 are polygon layers only"),
        ("A time-varying height and bulk layer keyed on parcel",
         "DataSF historic height and bulk series",
         "2009--2014 published as polygons; no parcel-keyed vintage"),
        (f"A market price after {cl_sf['max_date']}" if cl_sf else "CoreLogic extracts",
         TT("$MFHR_DATA_ROOT/demand/corelogic"),
         (f"the Cotality pull ends there, two years short of the corpus; Zillow covers the "
          f"tail at ZIP level only" if cl_sf else "not present under the data root")),
        ("Impact fee registers before 2019",
         "sfplanning.org and the Wayback Machine",
         "the register is republished at one URL; no snapshot before 2019-09-15"),
        ("A unit count for the early docket", "every scale source in Table~\\ref{tab:scale}",
         f"a non-zero count reaches {100*cu_reach['units_nz_early']:.1f}\\% of "
         f"conditional uses heard before {SCALE_ERA} against "
         f"{100*cu_reach['units_nz_late']:.1f}\\% after"),
    ]

    # ── the prose numbers that are not already in a table cell ──────────
    slug = {"open_date": "OpenDate", "close_date": "CloseDate",
            "record_status": "RecordStatus", "applicant": "Applicant",
            "applicant_org": "ApplicantOrg", "assigned_to_planner": "Planner",
            "building_permits": "BuildingPermits", "parent_id": "ParentId",
            "number_of_units_prop": "UnitsProp", "number_of_units_net": "UnitsNet",
            "number_of_affordable_units": "UnitsAff", "project_decision": "Decision",
            "project_decision_date": "DecisionDate",
            "environmental_document_type": "EnvDoc", "inclusionary": "InclFlag"}
    field_macros = {}
    for fld, tbl, _what, pct, _rows in rf:
        key = slug.get(fld)
        if key:
            field_macros[f"acq{'Np' if tbl == 'non-project' else 'Pr'}{key}"] = f"{pct:.1f}"
    bridge_macros = {}
    for rt, key in (("large_project_authorization", "Lpa"), ("variance", "Var"),
                    ("downtown_project", "Dnt"), ("planning_code_amendment", "Pca")):
        g = it[it.request_type.eq(rt)]
        bridge_macros[f"acqBridge{key}"] = f"{100*g.bridge_in_dbi.mean():.1f}" if len(g) \
            else "---"
    # Source quirks the limitations section names. A date centuries out is a typo; a pipeline
    # date a few years out is an estimate. Both are reported, and neither is typed by hand.
    def _mx(d, c, how="max"):
        v = pd.to_datetime(d[c], errors="coerce").dropna()
        return str((v.max() if how == "max" else v.min()).year) if len(v) else "---"
    quirk_macros = {"acqUcMaxYear": _mx(uc, "date_issued"),
                    "acqMohcdMaxYear": _mx(mo, "entitlement_approval"),
                    "acqNpMinYear": _mx(npj, "open_date", "min")}
    # The year from which the case-number join stops being a format problem, and the worst
    # annual rate from that year on --- so the prose does not have to eyeball the figure.
    cf = cj["frame"]
    ann = cf.groupby("year").j_any.agg(["mean", "size"])
    ann = ann[ann["size"] >= MIN_CASES_FIG]
    good = ann[ann["mean"] >= 0.95]
    brk = int(good.index.min()) if len(good) else 0
    casejoin_macros = {
        "acqCaseJoinBreakYear": str(brk) if brk else "---",
        "acqCaseJoinLateMin": (f"{100*ann.loc[ann.index >= brk, 'mean'].min():.0f}"
                               if brk else "---")}
    row_macros = {"acqNonProjRows": N(len(npj)), "acqProjRows": N(len(prj)),
                  "acqPipelineRows": N(len(pipe)), "acqMohcdRows": N(len(mo)),
                  "acqFeeAreaRows": N(len(read_cached("impact_fee_areas")))}
    lu = read_cached("land_use", usecols=["geography_type"])
    land_use_multi = N(int(lu.geography_type.eq("multiple_parcels").sum()))
    scale_earliest = str(SCALE_ERA)
    # The permit memo's hearing-to-issuance median, recomputed from the artefact that memo
    # wrote, so this memo cannot quote a stale number at it.
    pmatch = DATA_ROOT / "extraction" / RUN / "permit_matches.csv"
    issue_lag_median = issue_lag_p90 = "---"
    if pmatch.exists():
        pm = pd.read_csv(pmatch, usecols=["meeting_date", "issued_date"], low_memory=False)
        d = (pd.to_datetime(pm.issued_date, errors="coerce")
             - pd.to_datetime(pm.meeting_date, errors="coerce")).dt.days
        d = d[d > 0]
        issue_lag_median = f"{d.median():.0f}" if len(d) else "---"
        issue_lag_p90 = f"{d.quantile(0.9):.0f}" if len(d) else "---"

    ctx = dict(probe=pr, parcels=P, assessor=asr, cases=cj, lag=lag, bridge=br, fees=fees,
               field_macros=field_macros, bridge_macros=bridge_macros,
               row_macros=row_macros, land_use_multi=land_use_multi,
               quirk_macros=quirk_macros, casejoin_macros=casejoin_macros,
               cl_sf=cl_sf,
               scale_earliest=scale_earliest, issue_lag_median=issue_lag_median,
               issue_lag_p90=issue_lag_p90,
               inventory=inventory, zoning_rows=zrows, bridge_rows=brows, bridge_all=ball,
               record_fields=rf, n_cu=n_cu, scale_rows=scale_rows, price_rows=price_rows,
               fee_rows=fee_rows, fee_years=fee_years, fee_matrix=fee_matrix,
               negatives=negatives, readme_rows=readme_rows,
               lag_groups=_lag_groups(lag),
               case_formats=[(f, len(g), int(g.j_any.sum()))
                             for f, g in cj["frame"].groupby("fmt")])

    fig_riskset(it, asr)
    if cl_sf:
        fig_corelogic(cl_sf, it)
    fig_case_join(cj["frame"])
    fig_filing_lag(lag)
    write_tables(ctx)
    write_readme(ctx)
    _write_macros(ctx, it, cu, cl, zmeta, cu_reach)

    ok = lag[lag.lag.notna()]
    print(f"parcel join: {P['joined']:,}/{P['with_key']:,} "
          f"({100*P['joined']/P['with_key']:.1f}%) of items with a key")
    print(f"assessor: {asr['rows']:,} parcel-years, {min(asr['years'])}-{max(asr['years'])}, "
          f"{100*asr['items_joined']/asr['items_with_key']:.1f}% of keyed items")
    print(f"case join: {cj['any']:,}/{cj['n']:,} ({100*cj['any']/cj['n']:.1f}%)")
    print(f"filing lag: n={len(ok):,}, median {ok.lag.median():.0f} d, "
          f"p90 {ok.lag.quantile(.9):.0f} d, p99 {ok.lag.quantile(.99):.0f} d")
    print(f"bridge: {ball[1]:,} items reach a permit, {ball[2]:,} present in DBI; "
          f"CU {100*cu.bridge_in_dbi.mean():.1f}%")
    print(f"fees: {len(fees):,} rate observations across {fees.year.nunique() if len(fees) else 0} registers")
    print("figures written to", FIG)


def _printed_permits(it: pd.DataFrame) -> pd.Series:
    """Reuse `analyze_permits.py`'s parser rather than re-implementing it, so the two memos
    cannot disagree about which items print a permit number."""
    try:
        import analyze_permits as ap
    except Exception:
        return pd.Series(False, index=it.index)
    d = it.project_descr.fillna("").astype(str)
    return d.map(lambda t: bool(ap.parse_permits(t)))


def _resolve_key(p: Path, want: str) -> str:
    """The declared key column, or the nearest thing the file actually has. The 2001 zoning
    vintage carries `mapblklot` and no `blklot`; keying it on a column that is not there
    reported it as 153{,}669 duplicate rows, which is the opposite of the truth."""
    head = pd.read_csv(p, nrows=0)
    if want in head.columns:
        return want
    for alt in ("blklot", "mapblklot", "record_id", "parcel_number"):
        if alt in head.columns:
            return alt
    return head.columns[0]


def _date_range(p: Path, name: str) -> str:
    """The date range of whichever column is the natural clock for the source."""
    cols = {"parcels": "date_map_add", "assessor": "closed_roll_year",
            "records_nonproject": "open_date", "records_project": "open_date",
            "pipeline": "current_status_date", "housing_production": "first_completion_date",
            "unit_completions": "date_issued", "mohcd_pipeline": "entitlement_approval",
            "land_use": "data_as_of"}
    c = cols.get(name)
    if not c:
        return "---"
    try:
        s = pd.read_csv(p, dtype=str, usecols=[c], low_memory=False)[c].dropna()
    except Exception:
        return "---"
    if not len(s):
        return "---"
    if c == "closed_roll_year":
        v = pd.to_numeric(s, errors="coerce").dropna()
        return f"{int(v.min())}--{int(v.max())}"
    v = pd.to_datetime(s, errors="coerce").dropna()
    if not len(v):
        return "---"
    return f"{v.min().date()}--{v.max().date()}{_sic(v.max().year)}"


# A pipeline date in 2030 is an estimate; a completion date in 2205 is a typo. Mark the
# second rather than quietly reporting it as the end of the file's coverage.
_THIS_YEAR = pd.Timestamp.today().year


def _sic(year: int) -> str:
    return r"\,\emph{(sic)}" if year > _THIS_YEAR + 25 else ""


def _yr_range(d: pd.DataFrame, col: str) -> str:
    if col not in d.columns:
        return "---"
    v = pd.to_datetime(d[col], errors="coerce").dropna()
    return f"{v.min().year}--{v.max().year}{_sic(v.max().year)}" if len(v) else "---"


def _lag_groups(lag: pd.DataFrame):
    g = [("All cases", lag)]
    for rt in ("conditional_use", "discretionary_review", "variance",
               "planning_code_amendment"):
        g.append((rt.replace("_", " "), lag[lag.request_type.eq(rt)]))
    for lo, hi in ((1998, 2009), (2010, 2017), (2018, 2026)):
        g.append((f"heard {lo}--{hi}", lag[(lag.year >= lo) & (lag.year <= hi)]))
    return g


def _zillow_meta() -> dict:
    d = EXT / "prices"
    out = {"zhvi_grain": "---", "zhvi_what": "not cached",
           "zori_grain": "---", "zori_what": "not cached"}
    for k, tag in (("zhvi_zip", "zhvi"), ("zori_zip", "zori")):
        p = d / f"{k}_sf.csv.gz"
        if not p.exists():
            continue
        df = pd.read_csv(p, low_memory=False)
        months = [c for c in df.columns if re.match(r"^\d{4}-\d{2}-\d{2}$", c)]
        out[f"{tag}_grain"] = f"{len(df)} San Francisco ZIPs, monthly"
        out[f"{tag}_what"] = (f"{months[0][:7]} to {months[-1][:7]}, "
                              f"{len(months)} months" if months else "no month columns")
        out[f"{tag}_n"] = len(df)
        out[f"{tag}_months"] = len(months)
        out[f"{tag}_first"] = months[0][:7] if months else ""
        out[f"{tag}_last"] = months[-1][:7] if months else ""
    return out


# The fees behind the largest recurring obligation-imposing condition headings in the
# conditions memo, each as (Planning Code section, land-use label the register prints).
# A label of None means "the largest rate the section prints that year", which is the right
# summary for a fee whose bands the register does not label on the same line.
FEE_OF_INTEREST = [
    ("Transportation Sustainability Fee, residential 21--99 units", "411A", "21-99 Units"),
    ("Transportation Sustainability Fee, residential $>$99 units", "411A", ">99 Units"),
    ("Transportation Sustainability Fee, non-residential 800--99,999\\,gsf", "411A",
     "800-99,999 gsf"),
    ("Inclusionary Affordable Housing, \\S415 (per sq ft of residential)", "415", None),
    ("Jobs-Housing Linkage Program, \\S413", "413", None),
    ("Child Care, residential \\S414", "414", None),
    ("Child Care, commercial \\S414A", "414A", None),
    ("Eastern Neighborhoods Infrastructure, \\S423", "423", None),
    ("School Impact Fee, residential (State Ed.\\ Code)", "EdCode", None),
]


def _fee_tables(fees: pd.DataFrame):
    if not len(fees):
        return [], [], []
    rows = []
    for y, g in fees.groupby("year"):
        pdfp = FEES_DIR / f"impact_fee_register_{y}.pdf"
        pages = 0
        try:
            import pdfplumber
            with pdfplumber.open(pdfp) as pdf:
                pages = len(pdf.pages)
        except Exception:
            pass
        eff = g.effective.replace("", np.nan).dropna()
        rows.append({"year": int(y), "pages": pages, "rates": len(g),
                     "fees": int(g.loc[g.section.ne(""), "section"].nunique()),
                     "effective": eff.iloc[0] if len(eff) else "---"})
    rows.sort(key=lambda r: r["year"])
    years = [r["year"] for r in rows]
    matrix = []
    for nm, sec, label in FEE_OF_INTEREST:
        hit = fees[fees.section.eq(sec)]
        if label is not None:
            hit = hit[hit.label.eq(label)]
        vals = []
        for y in years:
            v = hit.loc[hit.year == y, "rate"]
            vals.append(f"\\${v.max():,.2f}" if len(v) else "---")
        if any(v != "---" for v in vals):
            matrix.append((nm, vals))
    return rows, years, matrix


def _write_macros(ctx, it, cu, cl, zmeta, cu_reach):
    P, asr, cj, lag = ctx["parcels"], ctx["assessor"], ctx["cases"], ctx["lag"]
    ok = lag[lag.lag.notna()]
    br_all = ctx["bridge_all"]
    fees = ctx["fees"]
    dr = it[it.request_type.eq("discretionary_review")]
    fmt = dict((f, (n, k)) for f, n, k in ctx["case_formats"])
    zpar = [r for r in ctx["zoning_rows"] if r["form"].startswith("parcel")]
    zpol = [r for r in ctx["zoning_rows"] if r["form"].startswith("polygon")]

    def rate(sec, label, year):
        h = fees[fees.section.eq(sec)]
        if label:
            h = h[h.label.eq(label)]
        v = h.loc[h.year == year, "rate"]
        return f"{v.max():,.2f}" if len(v) else "---"

    def lagmed(rt):
        g = ok[ok.request_type.eq(rt)]
        return f"{g.lag.median():.0f}" if len(g) else "---"

    m = {
        "acqEitherRoute": f"{100*br_all[4]/br_all[0]:.1f}",
        "acqEitherDR": f"{100*dr.either_route.mean():.1f}",
        "acqEitherCU": f"{100*cu.either_route.mean():.1f}",
        "acqCuUnitsNZ": f"{100*cu_reach['prj_units_nz']:.1f}",
        "acqOldFormatCases": N(fmt.get("YY.NNN", (0, 0))[0]),
        "acqOldFormatMatched": N(fmt.get("YY.NNN", (0, 0))[1]),
        "acqModernUnmatched": N(fmt.get("YYYY.NNNN", (0, 0))[0]
                                - fmt.get("YYYY.NNNN", (0, 0))[1]),
        "acqZoningParcelYears": str(len(zpar)),
        "acqZoningPolygonYears": str(len(zpol)),
        "acqAsrSought": N(asr["item_keys_wanted"]),
        "acqAsrFound": N(asr["item_keys_found"]),
        "acqLagCU": lagmed("conditional_use"),
        "acqLagDR": lagmed("discretionary_review"),
        "acqLagVar": lagmed("variance"),
        "acqTsfNinetyNineFirst": rate("411A", ">99 Units", int(fees.year.min())),
        "acqTsfNinetyNineLast": rate("411A", ">99 Units", int(fees.year.max())),
        "acqInclusionaryFirst": rate("415", None, int(fees.year.min())),
        "acqInclusionaryLast": rate("415", None, int(fees.year.max())),
        "acqZoningParcelSpan": (f"{zpar[0]['year']}--{zpar[-1]['year']}" if zpar else "---"),
        "acqCorpusSpan": f"{int(it.year.min())}--{int(it.year.max())}",
        **ctx["quirk_macros"],
        **ctx["casejoin_macros"],
        "acqAsrGapYears": str(min(asr["years"]) - int(it.year.min())),
        "acqAsrParcelsPerYear": N(int(np.median(list(asr["year_counts"].values())))),
        "acqLagTrunc": N(LAG_TRUNC_DAYS),
        "acqMinCasesFig": str(MIN_CASES_FIG),
        "acqMinItemsBridge": str(MIN_ITEMS_BRIDGE),
        "acqFeeEarliestSnapshot": FEE_EARLIEST_SNAPSHOT,
        "acqScaleEarliest": ctx["scale_earliest"],
        "acqDelayIssueMedian": ctx["issue_lag_median"],
        "acqDelayIssuePninety": ctx["issue_lag_p90"],
        **ctx["field_macros"],
        **ctx["bridge_macros"],
        **ctx["row_macros"],
        "acqZhviMonths": str(zmeta.get("zhvi_months", 0)),
        "acqZoriMonths": str(zmeta.get("zori_months", 0)),
        "acqLandUseMulti": ctx["land_use_multi"],
        **({} if not ctx["cl_sf"] else {
            "acqClRows": N(ctx["cl_sf"]["rows"]),
            "acqClPriced": N(ctx["cl_sf"]["priced"]),
            "acqClResidential": N(ctx["cl_sf"]["residential"]),
            "acqClParcels": N(ctx["cl_sf"]["parcels"]),
            "acqClFirst": str(ctx["cl_sf"]["first"]),
            "acqClDenseFirst": str(ctx["cl_sf"]["dense_first"]),
            "acqClMaxDate": ctx["cl_sf"]["max_date"],
            "acqClLast": str(ctx["cl_sf"]["last"]),
            "acqClKeyed": f"{100*ctx['cl_sf']['keyed']:.1f}",
            "acqClItemKeys": N(ctx["cl_sf"]["item_keys"]),
            "acqClItemKeysTraded": N(ctx["cl_sf"]["item_keys_traded"]),
            "acqClItemKeysTradedPct":
                f"{100*ctx['cl_sf']['item_keys_traded']/ctx['cl_sf']['item_keys']:.1f}",
            "acqClItemsEver": N(ctx["cl_sf"]["items_ever_traded"]),
            "acqClItemsEverPct":
                f"{100*ctx['cl_sf']['items_ever_traded']/ctx['cl_sf']['items_with_key']:.1f}",
            "acqClItemsNear": N(ctx["cl_sf"]["items_traded_near"]),
            "acqClItemsNearPct":
                f"{100*ctx['cl_sf']['items_traded_near']/ctx['cl_sf']['items_with_key']:.1f}",
            "acqClWindowYears": str(TRADE_WINDOW_DAYS // 365),
            "acqClMedianRecent": f"{ctx['cl_sf']['median_recent']/1e6:.2f}",
            "acqDemandFiles": N(cl["demand_files"]),
            "acqDemandGB": f"{cl['demand_bytes']/1e9:.2f}",
        }),
        "acqCuUnitsNZearly": f"{100*cu_reach['units_nz_early']:.1f}",
        "acqCuUnitsNZlate": f"{100*cu_reach['units_nz_late']:.1f}",
        "acqItems": N(len(it)),
        "acqCases": N(cj["n"]),
        "acqWithParcel": N(P["with_key"]),
        "acqParcelShare": f"{100*P['with_key']/P['items']:.1f}",
        "acqParcelRows": N(ctx["inventory"] and
                           dict(ctx["inventory"])["parcels"]["rows"]),
        "acqParcelJoin": f"{100*P['joined']/P['with_key']:.1f}",
        "acqParcelJoinN": N(P["joined"]),
        "acqParcelActive": f"{100*P['joined_active']/P['with_key']:.1f}",
        "acqParcelRetired": N(P["retired_only"]),
        "acqParcelNoLot": N(P["block_exists_lot_not"]),
        "acqParcelNoBlock": N(P["no_block_at_all"]),
        "acqDistinctParcelKeys": N(P["distinct_keys"]),
        "acqAsrRows": N(asr["rows"]),
        "acqAsrParcels": N(asr["distinct_parcels"]),
        "acqAsrFirst": str(min(asr["years"])),
        "acqAsrLast": str(max(asr["years"])),
        "acqAsrNYears": str(len(asr["years"])),
        "acqAsrJoin": f"{100*asr['items_joined']/asr['items_with_key']:.1f}",
        "acqAsrBalanced": N(asr["parcels_all_years"]),
        "acqCaseRaw": f"{100*cj['raw']/cj['n']:.1f}",
        "acqCaseNorm": f"{100*cj['norm']/cj['n']:.1f}",
        "acqCaseStem": f"{100*cj['stem']/cj['n']:.1f}",
        "acqCaseAny": f"{100*cj['any']/cj['n']:.1f}",
        "acqCaseAnyN": N(cj["any"]),
        "acqLagN": N(len(ok)),
        "acqLagCoverage": f"{100*len(ok)/len(lag):.1f}",
        "acqLagMedian": f"{ok.lag.median():.0f}",
        "acqLagPninety": f"{ok.lag.quantile(.9):.0f}",
        "acqLagPninetynine": f"{ok.lag.quantile(.99):.0f}",
        "acqLagNegative": f"{100*(ok.lag < 0).mean():.1f}",
        "acqBridgeItems": N(br_all[1]),
        "acqBridgeShare": f"{100*br_all[1]/br_all[0]:.1f}",
        "acqBridgeDbi": N(br_all[2]),
        "acqBridgeDbiShare": f"{100*br_all[2]/br_all[0]:.1f}",
        "acqBridgeCU": f"{100*cu.bridge_in_dbi.mean():.1f}",
        "acqBridgeCUN": N(int(cu.bridge_in_dbi.sum())),
        "acqBridgeCUTotal": N(len(cu)),
        "acqBridgeDR": f"{100*dr.bridge_in_dbi.mean():.1f}",
        "acqPrintedCU": f"{100*cu.printed_permit.mean():.1f}",
        "acqPrintedAll": f"{100*br_all[3]/br_all[0]:.1f}",
        "acqNonUniqueSources": str(sum(1 for _, r in ctx["inventory"]
                                       if r["unique"] != "yes")),
        "acqSourcesCached": str(len(ctx["inventory"])),
        "acqPrintedDR": f"{100*dr.printed_permit.mean():.1f}",
        "acqCuUnits": f"{100*cu_reach['prj_units']:.1f}",
        "acqCuPipeline": f"{100*cu_reach['pipeline']:.1f}",
        "acqCuHousingProd": f"{100*cu_reach['hp_parcel']:.1f}",
        "acqCuMohcd": f"{100*cu_reach['mohcd']:.1f}",
        "acqZhviZips": str(zmeta.get("zhvi_n", 0)),
        "acqZhviFirst": zmeta.get("zhvi_first", ""),
        "acqZhviLast": zmeta.get("zhvi_last", ""),
        "acqZoriZips": str(zmeta.get("zori_n", 0)),
        "acqZoriFirst": zmeta.get("zori_first", ""),
        "acqZoriLast": zmeta.get("zori_last", ""),
        "acqCoreLogicSales": N(ctx["cl_sf"]["priced"]) if ctx["cl_sf"] else "---",
        "acqFeeRegisters": str(fees.year.nunique() if len(fees) else 0),
        "acqFeeRates": N(len(fees)),
        "acqFeeFirst": str(int(fees.year.min())) if len(fees) else "---",
        "acqFeeLast": str(int(fees.year.max())) if len(fees) else "---",
        "acqRetrieved": ctx["probe"].get("retrieved", ""),
    }
    (TAB / "acquisition_macros.tex").write_text(macros(m))
    print("→", TAB / "acquisition_macros.tex")


# ═══════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe", help="resolve every identifier against the live catalogue")
    f = sub.add_parser("fetch", help="download and cache each source")
    f.add_argument("--only",
                   help="a source name from the registry, or "
                        "zillow / fees / corelogic / polygons")
    f.add_argument("--refresh", action="store_true", help="re-download even if cached")
    sp = sub.add_parser("spatial", help="point-in-polygon → the parcel-year zoning panel")
    sp.add_argument("--refresh", action="store_true", help="rebuild the panel")
    y = sub.add_parser("sync", help="materialise Dropbox online-only files under demand/")
    y.add_argument("--root", help="subtree to materialise (default $MFHR_DATA_ROOT/demand)")
    sub.add_parser("report", help="joins, coverage, figures, tables, README")
    a = ap.parse_args()
    if a.cmd == "probe":
        probe()
    elif a.cmd == "fetch":
        fetch(a.only, a.refresh)
    elif a.cmd == "spatial":
        spatial(a.refresh)
    elif a.cmd == "sync":
        sync(Path(a.root).expanduser() if a.root else None)
    else:
        report()


if __name__ == "__main__":
    main()
