#!/usr/bin/env python3
"""
build_chronicle.py
------------------
Purpose : The generated half of the project chronicle (output/planning_commission_project/
          project_chronicle/project_chronicle.tex): its macros and its four tables --- the
          timeline of memos with what later work revised, the data inventory, the map from
          model primitive to identifying moment to dataset, and the open register. The
          chronicle summarises; every number in it is read from the memo or artefact that owns
          it, so it cannot drift from them. Spec: .claude/instructions/mfhr_next_phase_brief.md,
          Part A.
Inputs  : every memo's generated macros file (output/planning_commission_project/*/tables/
          *_macros.tex), which the chronicle \\inputs directly; the JSON artefacts the older
          memos are built from (meeting_field_score.json, bakeoff/g3_report.json,
          extraction/corpus_v2_g3/permit_summary.json); git history and run_log.md for dates;
          the compiled PDFs for page counts; the data panels for row counts
Outputs : output/planning_commission_project/project_chronicle/tables/chronicle_macros.tex
          output/planning_commission_project/project_chronicle/tables/chronicle_tables.tex
Author  : Dan Post
Created : 2026-09-11

Usage
-----
  python build_chronicle.py        # write the macros and tables, check macro-name clashes

Notes
-----
The older memos (meeting level, extraction, permit linkage) have no macros file; their
headline numbers are read from the JSON each is generated from. A memo's date is the date git
first recorded its .tex (following renames); a memo written in this batch and not yet
committed takes the date of its run-log entry.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from paths import DATA_ROOT                                          # noqa: E402

REPO = HERE.parents[1]
PCP = REPO / "output" / "planning_commission_project"
OUT = PCP / "project_chronicle"
TAB = OUT / "tables"
MEMOS = [  # folder, tex name, short name (the timeline's order is by date, then this order)
    ("meeting_level_info", "meeting_level_info", "Meeting-level information"),
    ("extraction_method_comparison", "extraction_method_comparison", "Item-level extraction"),
    ("discretionary_review_patterns", "discretionary_review_patterns", "The corpus memo"),
    ("permit_linkage", "permit_linkage", "Permit linkage"),
    ("conditions_of_approval", "conditions_of_approval", "Conditions of approval"),
    ("predicting_delay", "predicting_delay", "Predicting delay"),
    ("data_acquisition", "data_acquisition_memo", "Data acquisition"),
    ("datasf_planning_catalogue", "datasf_planning_catalogue", "DataSF catalogue"),
    ("conditions_content", "conditions_content", "Conditions census"),
    ("permits_content", "permits_content", "Permit content"),
    ("clocks", "clocks", "Clocks"),
    ("exactions", "exactions", "Exactions"),
    ("regulatory_timeline", "regulatory_timeline", "Regulatory timeline"),
]
RUNLOG_KEYS = {"clocks": "Part 2 (clocks)", "exactions": "Part 1 (exactions)",
               "regulatory_timeline": "Part 3 (regulatory timeline)"}


def git_first_date(path: Path) -> str:
    r = subprocess.run(["git", "log", "--follow", "--diff-filter=A", "--format=%ad", "--date=short",
                        "--", str(path)], cwd=REPO, capture_output=True, text=True)
    ds = r.stdout.split()
    return ds[-1] if ds else ""


def runlog_date(key: str) -> str:
    t = (REPO / "run_log.md").read_text()
    m = re.search(rf"^## (\d{{4}}-\d{{2}}-\d{{2}}) — {re.escape(key)}", t, re.M)
    return m.group(1) if m else ""


def pages(pdf: Path) -> int:
    import pymupdf
    return len(pymupdf.open(pdf)) if pdf.exists() else 0


def n(x) -> str:
    return f"{x:,.0f}".replace(",", "{,}")


def macro_files() -> list[Path]:
    return sorted(p for p in PCP.glob("*/tables/*macros*.tex") if "project_chronicle" not in str(p))


def check_clashes(extra: dict) -> list[str]:
    seen, clash = {}, []
    for f in macro_files():
        for name in re.findall(r"\\newcommand\{\\(\w+)\}", f.read_text()):
            if name in seen:
                clash.append(f"{name}: {seen[name]} and {f.name}")
            seen[name] = f.name
    for name in extra:
        if name in seen:
            clash.append(f"{name}: {seen[name]} and chronicle_macros")
    return clash


def build():
    TAB.mkdir(parents=True, exist_ok=True)
    M = {}
    # ── dates and lengths ──
    rows = []
    for folder, tex, short in MEMOS:
        p = PCP / folder / f"{tex}.tex"
        d = git_first_date(p) or runlog_date(RUNLOG_KEYS.get(folder, "~none~"))
        rows.append({"folder": folder, "short": short, "date": d, "pages": pages(p.with_suffix(".pdf"))})
    dates = pd.DataFrame(rows)
    tag = lambda f: "".join(w.title() for w in f.split("_"))
    for r in dates.itertuples():
        M[f"chDate{tag(r.folder)}"] = r.date or "---"
        M[f"chPages{tag(r.folder)}"] = str(r.pages)
    M["chMemos"] = str(len(dates))
    M["chPagesTotal"] = n(dates.pages.sum())
    # ── the older memos' headline numbers, from their own artefacts ──
    mf = json.loads((HERE / "meeting_field_score.json").read_text())
    M.update(chMeetScored=str(mf["meetings_scored"]), chMeetValues=n(mf["values"]),
             chMeetAcc=f"{mf['accuracy_pct']:.1f}", chMeetEarly=f"{mf['by_era']['1998-2001']['pct']:.1f}",
             chMeetLate=f"{mf['by_era']['2015+']['pct']:.1f}")
    mt = pd.read_csv(HERE / "meetings_all.csv", usecols=["meeting_date"])
    my = pd.to_datetime(mt.meeting_date, errors="coerce").dt.year
    M.update(chMeetings=n(len(mt)), chMeetFirst=str(int(my.min())), chMeetLast=str(int(my.max())))
    g3 = json.loads((HERE / "bakeoff" / "g3_report.json").read_text())
    M.update(chGoldItems=str(g3["n_gold"]), chRegexTest=f"{g3['scores']['regex']['test']['accuracy']:.1f}",
             chHaikuTest=f"{g3['scores']['haiku-4.5-g3']['test']['accuracy']:.1f}")
    ps = json.loads((DATA_ROOT / "extraction" / "corpus_v2_g3" / "permit_summary.json").read_text())
    M.update(chItems=n(ps["items"]), chPermitsDistinct=n(ps["distinct_permits"]),
             chPermitMatch=f"{ps['match_rate_pct']:.1f}",
             chParcelPrecision=f"{ps['parcel_rule']['precision']:.2f}",
             chParcelRecall=f"{ps['parcel_rule']['recall']:.2f}",
             chDbiCondTalk=n(ps["dbi_conditions"]["condition_talk"]))
    # ── the inventory's row counts, read from the objects ──
    import pyarrow.parquet as pq
    E = DATA_ROOT / "external"
    count = lambda p: pq.ParquetFile(p).metadata.num_rows if p.exists() else 0
    M.update(chRowsExactions=n(count(E / "exactions" / "exaction_projects.parquet")),
             chRowsEnvelope=n(count(E / "zoning" / "parcel_envelope.parquet")),
             chRowsClocks=n(count(E / "clocks" / "clocks.parquet")),
             chRowsOutcomes=n(count(E / "clocks" / "outcomes_units.parquet")),
             chRowsNumeric=n(count(E / "cpc_packets" / "conditions_numeric.parquet")),
             chRowsConditions=n(count(E / "cpc_packets" / "conditions_long.parquet")))
    inv = pd.read_csv(PCP / "regulatory_timeline" / "law_inventory.csv")
    M["chLaws"] = str(len(inv))
    ey = pd.to_datetime(inv.enacted_date, errors="coerce").dt.year
    M.update(chLawFirst=str(int(ey.min())), chLawLast=str(int(ey.max())))
    # year spans, read from the objects
    import acquire_external_data as ax
    it = ax.load_items()
    M.update(chItemFirst=str(int(it.year.min())), chItemLast=str(int(it.year.max())))
    rec = ax.build_records()
    ry = rec.open_date.dt.year
    M.update(chRecFirst=str(int(ry.min())), chRecLast=str(int(ry.max())))
    pp = pd.read_parquet(E / "prices" / "parcel_year_price.parquet", columns=["year"])
    M.update(chPriceFirst=str(int(pp.year.min())), chPriceLast=str(int(pp.year.max())))
    cl = pd.read_parquet(E / "cpc_packets" / "conditions_long.parquet", columns=["hearing_date", "adoption_date"])
    cy = pd.to_numeric(cl.hearing_date.str[:4].where(cl.hearing_date.fillna("").ne(""), cl.adoption_date.str[:4]),
                       errors="coerce")
    M.update(chCondFirst=str(int(cy.min())), chCondLast=str(int(cy.max())))
    ck = pd.read_parquet(E / "clocks" / "clocks.parquet", columns=["fy"])
    M.update(chClockFirst=str(int(ck.fy.min())), chClockLast=str(int(ck.fy.max())))
    ex = pd.read_parquet(E / "exactions" / "exaction_projects.parquet", columns=["filed_date"])
    M.update(chExFirst=str(int(ex.filed_date.dt.year.min())), chExLast=str(int(ex.filed_date.dt.year.max())))
    rr_ = pd.read_csv(PCP / "records_request" / "pre2010_with_motion_number.csv", usecols=["hearing_date"])
    M.update(chRrFirst=str(rr_.hearing_date.min())[:4], chRrLast=str(rr_.hearing_date.max())[:4])
    rr = PCP / "records_request"
    M["chRrWith"] = n(len(pd.read_csv(rr / "pre2010_with_motion_number.csv")))
    M["chRrWithout"] = n(len(pd.read_csv(rr / "pre2010_without_motion_number.csv")))
    clash = check_clashes(M)
    if clash:
        raise SystemExit("macro clashes:\n" + "\n".join(clash))
    (TAB / "chronicle_macros.tex").write_text(
        "% GENERATED BY build_chronicle.py --- do not edit by hand.\n" +
        "\n".join(rf"\newcommand{{\{k}}}{{{v}}}" for k, v in sorted(M.items())) + "\n")
    write_tables(dates)
    print(f"{len(M)} macros; {len(macro_files())} memo macro files checked for clashes → {TAB}")


# ═══════════════════════════════════════════════════════════════════════════
# the tables: the content is the chronicle's reading of the memos; numbers are macros
# ═══════════════════════════════════════════════════════════════════════════
TIMELINE = {
    "meeting_level_info": (
        "Can the hearing date and the sitting's attributes be derived from the minutes?",
        r"Dates are right for every gold item; the attribute fields score in the mid-90s out of sample, "
        r"worst in 1998--2001.",
        r"2026-09-04: the 2018 scraping gap refilled. 2026-09-10: the round-4 figure (held-out meetings) and "
        r"the all-meetings figure (\chMeetAcc\%, unadjudicated) are different measurements; a name-reducer "
        r"defect had split six commissioners into two each."),
    "extraction_method_comparison": (
        "Rules or a language model for the item fields?",
        r"Haiku with retrieved examples, \chHaikuTest\% against the regex's \chRegexTest\% on the frozen test half.",
        r"2026-09-10: the regex baseline had been understated by its own scoring; few-shot retrieval had "
        r"let an item answer itself, which touched every ``all items'' figure but not the test half."),
    "discretionary_review_patterns": (
        "What does the item-level dataset contain?",
        r"Denial has nearly vanished while conditioning tripled; delay's upper tail lengthened; DR's share "
        r"of the docket collapsed.",
        r"2026-09-10: the conditioning rate was recomputed after a defect fix. 2026-09-11: the case "
        r"universe fixed at \corpusUniverse; its distinct-parcel count is superseded by the acquisition "
        r"memo's join."),
    "permit_linkage": (
        "How far can a decision be followed into DBI's permits?",
        r"The printed permit number matches on \chPermitMatch\% of \chPermitsDistinct\ numbers; the parcel "
        r"fallback runs at \chParcelPrecision\ precision; the Commission's conditions are not in DBI.",
        r"2026-09-11: the fallback's precision was measured on discretionary reviews only, so the permit "
        r"content memo keeps that tier (T3) apart; the Planning records' permit field is a second route."),
    "conditions_of_approval": (
        "Where do the conditions of approval live, and how much is obtainable?",
        r"In the Commission packet's draft motion, addressable by case number from 2011; templated, and a "
        r"thirteen-category scheme spans them.",
        r"2026-09-08 and 2026-09-11: ``the two routes never cover the same item'' withdrawn (the records' "
        r"permit field reaches \condBridgeCUPct\% of conditional uses); ``no amount of work recovers "
        r"condition content before 2010'' withdrawn --- it was a claim about two URL patterns, and the "
        r"census found text for \condReachEarlyHtml\% and \condReachLateHtml\% of the pre-2010 eras: little, not none."),
    "predicting_delay": (
        "How much of the hearing-to-hearing time is forecastable?",
        r"The level somewhat, the tail barely; the residual dispersion is the first proxy for the cost of uncertainty.",
        r"2026-09-11: the case universe note. Its clock starts at the first hearing; the acquisition memo's "
        r"filing clocks and the clocks memo's full distributions supersede it as the duration measure."),
    "data_acquisition": (
        "Where are the denominator, the clock, the scale, the price and the fees?",
        r"A defined parcel-year risk set, a filing clock, a price on every parcel-year, and fees that vary "
        r"across sections rather than years.",
        r"2026-09-08: two of its own conclusions reversed (the lettered-lot key; the two-digit year). "
        r"2026-09-11: on the DR track the developer's clock starts at the DBI filing, not the Planning "
        r"record (from the permit content memo). 2026-09-11: the feasible envelope added."),
    "datasf_planning_catalogue": (
        "What does DataSF hold under ``planning'' that the pipeline does not use?",
        r"No dataset of Commission actions exists; review-time metrics and DBI's station routing are the "
        r"unused delay data.",
        r"None yet."),
    "conditions_content": (
        "What do the conditions say, across every case and motion?",
        r"Condition text for \ccCondWithTextPct\% of conditioned items; a template with a thin "
        r"project-specific layer; the entitlement's term is a \ccValidityModal-month template.",
        r"2026-09-11 (this brief): the parser's third round; the document once read as the earliest motion in the "
        r"universe was two motions bound together, and every span resting on it moved; the numbers inside the conditions extracted."),
    "permits_content": (
        "Are the projects that reach the Commission different from similar ones?",
        r"Bigger and slower, but most of the time difference is immortal time; the lower abandonment survives.",
        r"2026-09-11 (this brief): the landmark comparison quantified how little of the time gap survives; "
        r"cost placeholders dropped; the first-construction-document date dropped as an outcome."),
    "clocks": (
        "What are the full distributions of every duration, by track and parcel value?",
        r"Long and dispersed on every track; about a third never record completion; dispersion does not "
        r"fall consistently with value.",
        r"Extended the same day with the outcome chain."),
    "exactions": (
        "What inclusionary requirement and impact fees vested for each project?",
        r"A sourced version chain and vesting rules; a median \exPuMed\ per unit for new buildings of ten or more units.",
        r"None yet; its open questions are stated in it."),
    "regulatory_timeline": (
        "Which state laws and ordinances generate variation this data can measure?",
        r"\rtUsable\ of \rtLaws; the opt-in pathways are elections, not assignments.",
        r"None yet."),
}

INVENTORY = [
    # object, unit, coverage, known defects, memo
    ("Minutes corpus and item table", r"item (\chItems)", r"\chItemFirst--\chItemLast",
     "fields extracted by model at a measured error; modern era thin in the gold set", "corpus; extraction"),
    ("Meeting table", r"meeting (\chMeetings)", r"\chMeetFirst--\chMeetLast",
     r"attributes weakest in 1998--2001 (\chMeetEarly\% against \chMeetLate\% from 2015)", "meeting level"),
    ("Case universe", r"case (\corpusUniverse)", r"\chItemFirst--\chItemLast", "case numbers as printed, two-digit years expanded", "corpus; acquisition"),
    ("Permit matches", r"printed permit number (\chPermitsDistinct)", r"\chItemFirst--\chItemLast",
     "the printed route reaches DR, almost never CU", "permit linkage"),
    ("DBI permits, every column", r"permit (\pcPermits)", r"\pcFiledFirst--\pcFiledLast",
     "cost placeholders; first-construction date sparse", "permit content"),
    ("Linkage tiers T1/T2/T3", "item--permit link", r"\chItemFirst--\chItemLast", r"T3 precision measured on DR only", "permit content"),
    ("Planning records", "record", r"\chRecFirst--\chRecLast\ (sparse early)", "record open date is not acceptance", "acquisition"),
    ("Parcel-year zoning panel", r"parcel-year (\acqPanelRows)", r"\acqPanelFirst--\acqPanelLast",
     r"zoning after \acqSnapshotFirst\ carried forward", "acquisition"),
    ("Risk set", r"parcel-year (\acqRiskParcelYears)", r"\acqPanelFirst--\acqPanelLast", r"condominium rule drops \acqRiskCondoLost\ hearings", "acquisition"),
    ("Price panel", "parcel-year", r"\chPriceFirst--\chPriceLast", "imputed where no sale, flagged", "acquisition"),
    ("Fee registers", "rate by year", r"\exRegFirst--\exRegLast\ (no 2017)", "pre-2011 missing", "acquisition; exactions"),
    ("Conditions census and parse", r"condition row (\chRowsConditions)", r"\chCondFirst--\chCondLast\ (thin early)",
     r"pre-2010 documents largely not online", "conditions census"),
    ("Conditions numbers", r"condition (\chRowsNumeric)", "as the census", "numeric fields only where stated", "conditions census"),
    ("Clocks panel", r"case or permit (\chRowsClocks)", r"\chClockFirst--\chClockLast", "right-censoring; non-completion truncation", "clocks"),
    ("Outcome chain", r"housing project (\chRowsOutcomes)", r"\chClockFirst--\chClockLast", "units delivered are a lower bound", "clocks"),
    ("Exaction panel", r"new-construction project (\chRowsExactions)", r"\chExFirst--\chExLast",
     r"priced from \exRegFirst; floor area mostly assumed; tenure mostly unknown", "exactions"),
    ("Law inventory and series", r"law (\chLaws)", r"enacted \chLawFirst--\chLawLast", r"\rtEffInferred\ effective dates inferred", "regulatory timeline"),
    ("Feasible envelope", r"parcel-year (\chRowsEnvelope)", r"\acqPanelFirst--\acqPanelLast",
     "rules held at one reading; some districts unread", "acquisition"),
    ("Records request package", r"item (\chRrWith\ + \chRrWithout)", r"\chRrFirst--\chRrLast", "draft; not sent", "records request"),
]

IDENT = [
    # primitive, moment, dataset, exists?
    (r"$\tau$: fees and inclusionary obligation", "dollar obligation per project, by vesting date",
     "exaction panel", "yes (priced from 2011)"),
    (r"$\tau$: the template conditions", "count and kind of conditions per motion", "conditions census", "yes (thin before 2010)"),
    (r"$\sigma$: delay dispersion", "CV and Kaplan--Meier quantiles of each clock, by track and value",
     "clocks panel", "yes (cross-sectional, not ex ante)"),
    (r"$\sigma$: outcome risk", "probability of denial, conditioning, abandonment", "item table; outcome chain", "yes"),
    ("Arrival of discretion", "share of projects that face a hearing, by parcel", "risk set + linkage tiers", "partly (T3 unvalidated outside DR)"),
    ("Filing threshold", "filing hazard by parcel value", "risk set, price panel, planning records", "yes"),
    ("Parcel value", "price per parcel-year", "price panel", "yes (imputed where no sale)"),
    ("Capacity (the risk set's ``could have filed'')", "maximum units per parcel-year", "feasible envelope", "yes (assumptions stated)"),
    ("Option term", "validity period and extensions", "conditions numbers", "yes"),
    (r"Separating $\tau$ from $\sigma$", r"variation in $\tau$ with $\sigma$ fixed, and the reverse",
     "inclusionary changes; SB 330; Housing Production ordinances", "described, not estimated"),
    ("Deterred projects", "non-filing on the risk set", "risk set", "defined; selection not solved"),
]

REGISTER = [
    # item, owner-type, blocks a first draft?
    ("Deterred projects: the risk set models non-filing, but who would have filed is unobserved", "modelling decision", "yes"),
    ("The unconstrained-envelope counterfactual: an envelope exists; its relaxation is unspecified", "modelling decision", "no"),
    (r"Identification of $\tau$ separately from $\sigma$: the candidate variation is described, not estimated", "modelling decision", "yes"),
    (r"$\sigma$ is a parcel-level primitive estimated from a hazard --- an assumption, not a finding", "modelling decision", "yes"),
    ("The pre-2010 motion residual", "records request", "no"),
    ("Recorded Notices of Special Restrictions: document type, bulk access, pre-1990 terms", "records request", "no"),
    ("Whether T3 linkage can be validated outside discretionary review", "data", "no"),
    ("Area-specific inclusionary rates, TSF grandfathering, existing-use credits, the 2017 register", "data", "no"),
    ("How the 2016 grandfathering deadline was applied (conditions contradict the codified text)", "data", "no"),
    (r"Effective dates of \rtEffInferred\ statutes inferred, not read", "literature check", "no"),
    ("The Priority Equity Geographies polygon, for the Housing Production ordinances' treated set", "data", "no"),
    ("The meeting-level gold set is unadjudicated for its all-meetings figure", "data", "no"),
    ("The 25-unit ordinance of July 2026 named in the brief", "literature check", "no"),
    ("Review-time metrics and DBI station routing (DataSF), unused", "data", "no"),
]


# Every supersession found, with the old and new figures read out of the logs and the brief
# that record them (a regex over the file, so the figure is the record's, not retyped).
def _src(name: str) -> str:
    return {"progress": (REPO / "progress_log.md").read_text(), "claude": (REPO / "CLAUDE.md").read_text(),
            "brief": (REPO / ".claude" / "instructions" / "mfhr_next_phase_brief.md").read_text(),
            "runlog": (REPO / "run_log.md").read_text()}[name]


def _v(src: str, rx: str) -> tuple:
    m = re.search(rx, re.sub(r"\s+", " ", _src(src)), re.S)
    if not m:
        raise SystemExit(f"supersession source moved: {src}: {rx}")
    return tuple(g.replace(",", "{,}") for g in m.groups())


SUPERSESSIONS = [
    # (date, memo, what was claimed or measured, what replaced it, source, regex); {0},{1}.. fill from the regex
    ("2026-09-04", "Meeting level", "2018 was a corpus hole of {0} documents; series crossed it",
     "refilled from the S3 packet prefix to {1} documents", "claude", r"\((\d+) documents to (\d+)\)"),
    ("2026-09-07", "Extraction", "accuracy against the hand labels as the measure",
     "{1}\\% of {0} judged disagreements were label errors; unadjudicated accuracy is a lower bound", "progress",
     r"of (\d+) judged disagreements, \*\*(\d+)% were label errors"),
    ("2026-09-07", "Extraction", "gold records complete",
     "a scalar/list mismatch had silently dropped lot numbers on {0} of {1} gold records; repaired", "progress",
     r"\*\*(\d+) of (\d+) gold records\*\*"),
    ("2026-09-08", "Permit linkage", "the permit route collapsed after 2019",
     "a change in how the minutes print the number; reading it recovers {0} items and {1} permits", "progress",
     r"recovers \*\*(\d+) items and (\d+) permits\*\*"),
    ("2026-09-08", "Conditions of approval", "discretionary review carries no conditions section",
     "{0} of {1} discretionary reviews do", "progress", r"not 0-for-all: it is \*\*(\d+) of (\d+)\*\*"),
    ("2026-09-08", "Conditions of approval", "the two linkage routes never cover the same item",
     r"the Planning records' permit field reaches \condBridgeCUPct\% of conditional uses", "progress", r"()"),
    ("2026-09-08", "Data acquisition", "parcel join below the acceptance criterion, at {0}\\%",
     "{1}\\% once lettered lots and multi-block items are keyed correctly", "progress",
     r"parcel join from ([\d.]+)% to \*\*([\d.]+)%\*\*"),
    ("2026-09-08", "Data acquisition", "pre-2002 records do not go back that far under any key",
     "expanding the two-digit year recovers {0} of {1} pre-2002 cases", "progress",
     r"recovers \*\*(\d+) of (\d+)\*\* pre-2002 cases"),
    ("2026-09-08", "Data acquisition", "a first price surface",
     "it carried a +{0} log bias, caught by held-out scoring and rebuilt", "progress", r"\*\*\+([\d.]+) log bias\*\*"),
    ("2026-09-10", "Extraction", "the regex baseline at {1}\\%",
     "{0}\\% once scored through the same storage layer as the model", "progress",
     r"\*\*([\d.]+)%\*\* on the test half, not ([\d.]+)%"),
    ("2026-09-10", "Meeting level", "{0} distinct roll-call names",
     "{1} once a mojibake token stopped splitting commissioners in two", "progress",
     r"(\d+) distinct roll-call names → (\d+)"),
    ("2026-09-10", "Permit linkage and the analyses", "the parcel-key fix applied",
     "the lettered-lot bug was still live in four scripts; the fix grows the fallback's validation set from {0} to {1}",
     "progress", r"validation set from ([\d,]+) to ([\d,]+)"),
    ("2026-09-11", "Conditions of approval", "no condition content before 2010",
     r"\condReachEarlyHtml\% and \condReachLateHtml\% of the pre-2010 eras' conditioned items have text", "progress", r"()"),
    ("2026-09-11", "The corpus memo", "{0} distinct parcels", "{1} from the acquisition memo's join (not yet regenerated)",
     "progress", r"The corpus memo's ([\d,]+) distinct parcels is superseded by ([\d,]+)"),
    ("2026-09-11", "Conditions census", r"the earliest motion read for a case in the universe was heard in \ccRprevEarliestInUniverse",
     r"two motions bound together; the earliest such item now dates from \ccRcurEarliestInUniverse", "runlog", r"()"),
    ("2026-09-11", "Permit content", "the within-cell time gap between linked and unlinked permits, read at face value",
     r"most is immortal time: measured from the hearing, about \pcLmSurviveDays\% survives in means and \pcLmMedSurvive\% in medians", "runlog", r"()"),
    ("2026-09-11", "Data acquisition", "the DR clock starts at the Planning record",
     r"at the DBI filing, a median \pcDrDiffMed\ days earlier", "runlog", r"()"),
    ("2026-09-11", "Exactions (in its own build)", "the 2019 register as cited",
     "the archive served the 2020 file for the 2019 capture; the fetcher now refuses another capture", "runlog", r"()"),
    ("2026-09-11", "The brief", "about {0} pre-2010 items with a motion number and {1} without; {2} addresses",
     r"\chRrWith\ and \chRrWithout; \ccProbed\ addresses", "brief",
     r"approximately ([\d,]+) by the census.*?roughly (\d+)\s+with no motion number.*?(\d[\d,]+) addresses answered"),
    ("2026-09-11", "The brief", "AB 1763 among the Housing Accountability Act amendments",
     "its own title is a density-bonus bill", "runlog", r"()"),
    ("2026-09-11", "The brief", "a 25-unit ordinance of July 2026",
     r"none among the Board's 2026 ordinances through Ord.~\rtSfLastOrd", "runlog", r"()"),
]


def write_tables(dates: pd.DataFrame):
    T = ["% GENERATED BY build_chronicle.py --- do not edit by hand."]

    def block(label, lines):
        T.extend([rf"\ifnum\pdfstrcmp{{\chtab}}{{{label}}}=0", *lines, r"\fi"])

    L = [r"{\scriptsize\begin{longtable}{L{1.55cm}L{1.85cm}L{2.8cm}L{3.7cm}L{4.2cm}}",
         r"\caption{Every memo, in order of completion: the question it was asked, its answer in a sentence, and "
         r"what later work revised or overturned in it.}\label{tab:chtimeline}\\\toprule",
         r"Date & Memo & Question & Answer & Revised or overturned by\\\midrule\endfirsthead",
         r"\toprule Date & Memo & Question & Answer & Revised or overturned by\\\midrule\endhead"]
    d = dates.assign(o=range(len(dates))).sort_values(["date", "o"])
    for r in d.itertuples():
        q, a, rev = TIMELINE[r.folder]
        tag = "".join(w.title() for w in r.folder.split("_"))
        L.append(rf"\chDate{tag} & {r.short} (\S\ref{{sec:ch{r.folder.replace('_', '')}}}) & {q} & {a} & {rev}\\")
    L.append(r"\bottomrule\end{longtable}}")
    block("timeline", L)

    L = [r"{\scriptsize\begin{longtable}{L{2.9cm}L{3.0cm}L{2.0cm}L{4.4cm}L{2.0cm}}",
         r"\caption{The data inventory: every table and panel, its unit and size, its years, its known defects, and "
         r"the memo that documents it.}\label{tab:chinventory}\\\toprule",
         r"Object & Unit (rows) & Years & Known defects & Memo\\\midrule\endfirsthead",
         r"\toprule Object & Unit (rows) & Years & Known defects & Memo\\\midrule\endhead"]
    for o, u, c, k, m in INVENTORY:
        L.append(rf"{o} & {u} & {c} & {k} & {m}\\")
    L.append(r"\bottomrule\end{longtable}}")
    block("inventory", L)

    L = [r"\begin{table}[htbp]\centering\scriptsize",
         r"\caption{Which data object identifies which model object.}\label{tab:chident}",
         r"\begin{tabular}{L{3.5cm}L{4.2cm}L{3.6cm}L{2.9cm}}\toprule",
         r"Model primitive & Identifying moment & Dataset & Exists?\\\midrule"]
    for a_, b_, c_, e_ in IDENT:
        L.append(rf"{a_} & {b_} & {c_} & {e_}\\")
    L.append(r"\bottomrule\end{tabular}\end{table}")
    block("ident", L)

    L = [r"\begin{table}[htbp]\centering\scriptsize",
         r"\caption{The open register: what is unresolved, who or what resolves it, and whether it blocks a "
         r"first presentable draft.}\label{tab:chregister}",
         r"\begin{tabular}{L{9.8cm}L{2.7cm}l}\toprule Open item & Needs & Blocks draft\\\midrule"]
    for a_, b_, c_ in REGISTER:
        L.append(rf"{a_} & {b_} & {c_}\\")
    L.append(r"\bottomrule\end{tabular}\end{table}")
    block("register", L)
    L = [r"{\scriptsize\begin{longtable}{L{1.55cm}L{2.2cm}L{4.8cm}L{5.9cm}}",
         r"\caption{Every supersession found: what a memo claimed or measured, and what replaced it. Old and new "
         r"figures are read from the logs and the brief that record them.}\label{tab:chsupersessions}\\\toprule",
         r"Date & Memo & Was & Now\\\midrule\endfirsthead\toprule Date & Memo & Was & Now\\\midrule\endhead"]
    for d, memo, was, now, src, rx in SUPERSESSIONS:
        g = _v(src, rx) if rx != r"()" else ()
        L.append(rf"{d} & {memo} & {was.format(*g)} & {now.format(*g)}\\")
    L.append(r"\bottomrule\end{longtable}}")
    block("supersessions", L)
    (TAB / "chronicle_tables.tex").write_text("\n".join(T) + "\n")


if __name__ == "__main__":
    build()
