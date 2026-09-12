#!/usr/bin/env python3
"""
audit_state_laws.py
-------------------
Purpose : The regulatory timeline, and what of it this data can measure. One row per law or
          ordinance that plausibly bears on the project, each date and scope claim a verbatim
          quotation from a primary source that this script re-fetches and checks; the model
          object each law moves; a measurability entry per law; the raw first-stage series where
          the first stage is observable; and the diversion series (the Commission docket's
          composition by quarter, and its permit-side mirror). An audit, not a causal memo: it
          reports no effect. Spec: .claude/instructions/mfhr_next_phase_brief.md, Part 3.
Inputs  : leginfo.legislature.ca.gov (bill text, code sections, the Constitution);
          the Board of Supervisors' ordinance lists and enacted PDFs (via
          build_exaction_panel's index and cache; sf.gov for 2026 after the Board's site moved);
          the item table, planning records and DBI permits (acquire_external_data,
          analyze_permit_content)
Outputs : output/planning_commission_project/regulatory_timeline/law_inventory.csv
          output/planning_commission_project/regulatory_timeline/law_claims.csv
          $MFHR_DATA_ROOT/external/laws/  (claims checked, series)
          output/planning_commission_project/regulatory_timeline/{figures,tables}/
Author  : Dan Post
Created : 2026-09-11

Usage
-----
  python audit_state_laws.py fetch [--refresh]   # every cited source; the 2026 ordinance list
  python audit_state_laws.py claims              # law_claims.csv from LAWS
  python audit_state_laws.py probe               # verify every quotation
  python audit_state_laws.py build               # inventory, populations, series
  python audit_state_laws.py report              # memo tables, figures, macros

Notes
-----
The standard is the exaction panel's: a legal fact is a claim with a source URL and a quotation
taken from the source's cached text by a regular expression, and a claim whose quotation is not
in its source is carried as unverified. Dates are read out of the quotation (a named group), so
the value is the source's, not a typed one. A statute enacted at a regular session takes effect
on the January 1 after a 90-day period from enactment (Cal. Const. art. IV, sec. 8(c), itself a
claim); a bill that says it takes effect immediately takes effect when approved. A law's row is
`verified` = yes when every claim it rests on verifies, partial when some do, unverified when none.

The source cache (fetch, normalise, the archive-capture guard) is build_exaction_panel's, so
the two memos cite one cache.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from paths import DATA_ROOT                                          # noqa: E402
import build_exaction_panel as bx                                    # noqa: E402

LAWS_DIR = DATA_ROOT / "external" / "laws"
MEMO = HERE.parents[1] / "output" / "planning_commission_project" / "regulatory_timeline"
FIG, TAB = MEMO / "figures", MEMO / "tables"
CLAIMS = MEMO / "law_claims.csv"
INVENTORY = MEMO / "law_inventory.csv"
CHECK = LAWS_DIR / "law_claims_checked.csv"
SF2026 = "https://www.sf.gov/ordinances-2026"

LI = "https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id="
CODE = "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode={c}&sectionNum={s}"
CONS_IV8 = "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=CONS&sectionNum=SEC.%208.&article=IV"

CATEGORIES = {1: "arrival of discretion", 2: "delay given discretion", 3: "stringency in dollars",
              4: "option term and vesting", 5: "feasible envelope"}

# Generic patterns: the value is the named group.
CHAPTER = r"(?:Senate|Assembly) Bill No\. \d+ CHAPTER (?P<v>\d+)"
APPROVED = r"Approved by Governor (?P<v>[A-Z][a-z]+ \d{1,2}, \d{4})\."
IMMEDIATE = r"to take effect immediately"
SF_PASSED = r"(?:Date Passed: (?P<v>[A-Z][a-z]+ \d{1,2}, \d{4})|FINALLY PASSED on (?P<w>[\d/]+))"


def bill(bid: str) -> str:
    return LI + bid


def ord_pdf(o: str) -> str:
    idx = pd.read_csv(bx.ORD_INDEX, dtype=str).fillna("").set_index("ord")
    return idx.loc[o, "pdf"]


def ord_list(y: int) -> str:
    return bx.ORD_LIST.format(y=y)


# ── the laws ────────────────────────────────────────────────────────────────
# One dict per law. `claims` are (field, regex) pairs read against `src` unless a third element
# names another source. The analysis fields (`entry`) are this memo's reading of what the data
# can see; every legal fact inside them is also in a claim.
def L(key, short, juris, cite, src, cat, opt_in, target_rx, *, secondary="", sunset_rx=None,
      operative_rx=None, elig_rx=None, extra=(), effective_rule=None, applies="", entry=None):
    return dict(key=key, short=short, juris=juris, cite=cite, src=src, cat=cat, secondary=secondary,
                opt_in=opt_in, target_rx=target_rx, sunset_rx=sunset_rx, operative_rx=operative_rx,
                elig_rx=elig_rx, extra=list(extra), effective_rule=effective_rule, applies=applies,
                entry=entry or {})


def laws() -> list[dict]:
    X = []
    # ── California: the older frame ──
    X.append(L("psa", "Permit Streamlining Act", "CA", "Gov. Code 65920 et seq. (time limits: 65950)",
               CODE.format(c="GOV", s="65950"), 2, "no",
               r"shall approve or disapprove the project within whichever of the following periods is applicable",
               effective_rule="code", applies="every development project needing a permit",
               entry=dict(
                   treated="All development projects; the clock runs from the lead agency's CEQA "
                           "document or completeness determination, neither of which is in the data.",
                   first="Days from CEQA certification (or exemption) to approval, capped by the "
                         "statute; the start date is not recorded.",
                   second="---", unobserved="The CEQA completion and completeness dates.",
                   confounds="---", verdict="not measurable here")))
    X.append(L("haa", "Housing Accountability Act", "CA", "Gov. Code 65589.5",
               CODE.format(c="GOV", s="65589.5"), 2, "no",
               r"This section shall be known, and may be cited, as the Housing Accountability Act",
               secondary="4", effective_rule="code", applies="housing development projects",
               entry=dict(
                   treated="Housing projects consistent with objective standards; consistency is "
                           "not a field.",
                   first="Disapprovals or density reductions of compliant projects; the docket "
                         "records disapprovals but not compliance.",
                   second="Filing of projects that rely on it.", unobserved="Compliance with objective standards.",
                   confounds="Amended repeatedly (SB 167, AB 678, AB 1515, SB 330, AB 1633).",
                   verdict="not measurable here")))
    X.append(L("sb167", "SB 167 (HAA)", "CA", "SB 167 (Skinner), Stats. 2017", bill("201720180SB167"), 2, "no",
               r"SB 167, Skinner\. Housing Accountability Act\.", effective_rule="cons",
               elig_rx=r"This bill would require the findings of the local agency to instead be based on a preponderance of the evidence in the record",
               applies="as the HAA", entry=dict(verdict="not measurable here",
                                                 treated="As the HAA.", first="As the HAA.", second="---",
                                                 unobserved="As the HAA.", confounds="Enacted with AB 678 and AB 1515.")))
    X.append(L("ab678", "AB 678 (HAA)", "CA", "AB 678 (Bocanegra), Stats. 2017", bill("201720180AB678"), 2, "no",
               r"AB 678, Bocanegra\. Housing Accountability Act\.", effective_rule="cons", applies="as the HAA",
               entry=dict(verdict="not measurable here", treated="As the HAA.", first="As the HAA.",
                          second="---", unobserved="As the HAA.", confounds="Enacted with SB 167 and AB 1515.")))
    X.append(L("ab1515", "AB 1515 (HAA)", "CA", "AB 1515 (Daly), Stats. 2017", bill("201720180AB1515"), 2, "no",
               r"AB 1515, Daly\. Planning and zoning: housing\.", effective_rule="cons", applies="as the HAA",
               entry=dict(verdict="not measurable here", treated="As the HAA.", first="As the HAA.",
                          second="---", unobserved="As the HAA.", confounds="Enacted with SB 167 and AB 678.")))
    X.append(L("dbl", "Density Bonus Law", "CA", "Gov. Code 65915", CODE.format(c="GOV", s="65915"), 5, "yes",
               r"shall grant one density bonus, the amount of which shall be as specified in subdivision \(f\)",
               effective_rule="code", applies="projects that elect it",
               entry=dict(
                   treated="Projects that claim a bonus. The Planning records' density-bonus field is "
                           "empty; claims are found by text, and the pipeline dataset flags current projects.",
                   first="Bonus units granted; not recorded.", second="Project size and affordable share.",
                   unobserved="The bonus, the concessions and waivers granted.",
                   confounds="HOME-SF, the local alternative, and AB 2345.", verdict="partially usable")))
    X.append(L("ab1763", "AB 1763 (density bonus)", "CA", "AB 1763 (Chiu), Stats. 2019", bill("201920200AB1763"), 5, "yes",
               r"AB 1763, Chiu\. Planning and zoning: density bonuses: affordable housing\.", effective_rule="cons",
               applies="100% affordable projects that elect it",
               entry=dict(verdict="not measurable here", treated="100% affordable projects claiming a bonus; the "
                          "claim is not a field.", first="---", second="---", unobserved="The bonus.",
                          confounds="The brief lists it among the HAA amendments; its own title is the density bonus.")))
    X.append(L("ab2345", "AB 2345 (density bonus)", "CA", "AB 2345 (Gonzalez), Stats. 2020", bill("201920200AB2345"), 5, "yes",
               r"AB 2345, Gonzalez\. Planning and zoning: density bonuses: annual report: affordable housing\.",
               effective_rule="cons", applies="projects that elect the bonus",
               entry=dict(verdict="not measurable here", treated="As the Density Bonus Law.", first="---",
                          second="---", unobserved="The bonus.", confounds="As the Density Bonus Law.")))
    # ── streamlined and ministerial pathways ──
    X.append(L("sb35", "SB 35", "CA", "SB 35 (Wiener), Stats. 2017", bill("201720180SB35"), 1, "yes",
               r"SB 35, Wiener\. Planning and zoning: affordable housing: streamlined approval process\.",
               sunset_rx=r"remain in effect only until (?P<v>January 1, \d{4})",
               elig_rx=r"subject to a requirement mandating a minimum percentage of below market rate housing based on one of the following",
               effective_rule="cons",
               applies="where the locality has not met its RHNA progress, as HCD determines (the determination is not in a statute and was not read)",
               entry=dict(
                   treated="Projects that file under it: the Planning records' SB 35 flag, and the text. "
                           "Eligibility (affordability, labour standards, site exclusions) is not constructible.",
                   first="Applications filed under the pathway, by quarter; approval within the statutory "
                         "window without a hearing.",
                   second="Share of eligible-looking projects that choose it; time to permit.",
                   unobserved="Eligibility, and the labour and affordability commitments.",
                   confounds="An opt-in pathway bundled with labour and affordability conditions; SB 423 "
                             "changed it in 2024.", verdict="partially usable")))
    X.append(L("sb423", "SB 423", "CA", "SB 423 (Wiener), Stats. 2023", bill("202320240SB423"), 1, "yes",
               r"SB 423, Wiener\. Land use: streamlined housing approvals: multifamily housing developments\.",
               sunset_rx=r"remain in effect only until (?P<v>January 1, \d{4})", effective_rule="cons",
               applies="as SB 35",
               entry=dict(treated="As SB 35; the records have no SB 423 field, only the text.",
                          first="As SB 35.", second="As SB 35.", unobserved="As SB 35.",
                          confounds="Housing Element rezoning, the 2024 Housing Production ordinance.",
                          verdict="partially usable")))
    X.append(L("sb330", "SB 330 (Housing Crisis Act)", "CA", "SB 330 (Skinner), Stats. 2019", bill("201920200SB330"), 4, "yes",
               r"SB 330, Skinner\. Housing Crisis Act of 2019\.", secondary="2",
               sunset_rx=r"remain in effect only until (?P<v>January 1, \d{4})",
               elig_rx=r"more than five hearings pursuant to Section 65905",
               effective_rule="cons", applies="housing development projects; the freeze needs a preliminary application",
               entry=dict(
                   treated="Housing projects; preliminary applications are flagged on PRJ records (the SB 330 field).",
                   first="(i) Preliminary applications by quarter; (ii) for housing cases, the share heard "
                         "more than five times, before and after the operative date.",
                   second="Filing timing relative to local rule changes.",
                   unobserved="Which hearings SB 330 counts; completeness dates.",
                   confounds="The pandemic (2020), the Housing Element cycle.", verdict="usable")))
    X.append(L("sb8", "SB 8", "CA", "SB 8 (Skinner), Stats. 2021", bill("202120220SB8"), 4, "no",
               r"SB 8, Skinner\. Housing Crisis Act of 2019\.",
               sunset_rx=r"remain in effect only until (?P<v>January 1, 2030)", effective_rule="cons",
               applies="extends SB 330",
               entry=dict(verdict="not measurable here", treated="As SB 330.", first="As SB 330.",
                          second="---", unobserved="As SB 330.", confounds="An extension; no new variation.")))
    X.append(L("sb9", "SB 9", "CA", "SB 9 (Atkins), Stats. 2021", bill("202120220SB9"), 5, "yes",
               r"SB 9, Atkins\. Housing development: approvals\.", secondary="1",
               elig_rx=r"shall ministerially approve, as set forth in this section, a parcel map for an urban lot split",
               effective_rule="cons", applies="single-family zones",
               entry=dict(treated="Lot splits and two-unit projects in single-family zones; found only by text.",
                          first="Urban lot splits and SB 9 duplexes filed, by quarter.",
                          second="Units added in RH-1 districts.", unobserved="The election of the pathway, except by text.",
                          confounds="The Family Housing Opportunity SUD (2023).", verdict="partially usable")))
    X.append(L("sb10", "SB 10", "CA", "SB 10 (Wiener), Stats. 2021", bill("202120220SB10"), 5, "no",
               r"SB 10, Wiener\. Planning and zoning: housing development: density\.", effective_rule="cons",
               elig_rx=r"up to 10 units of residential density per parcel, at a height specified in the ordinance",
               applies="only where a city adopts an ordinance under it (none sought here)",
               entry=dict(verdict="not measurable here", treated="Parcels a local ordinance upzones under it; no "
                          "such San Francisco ordinance was looked for.", first="---", second="---",
                          unobserved="Adoption.", confounds="---")))
    X.append(L("ab2011", "AB 2011", "CA", "AB 2011 (Wicks), Stats. 2022", bill("202120220AB2011"), 1, "yes",
               r"AB 2011, Wicks\. Affordable Housing and High Road Jobs Act of 2022\.", secondary="5",
               operative_rx=r"(?P<v>July 1, 2023)\. Digest Key Vote",
               sunset_rx=r"(?P<v>January 1, 2033), and as of that date is repealed",
               elig_rx=r"use by right and subject to one of 2 streamlined, ministerial review processes",
               effective_rule="cons", applies="commercial-zone sites meeting its site, affordability and labour criteria",
               entry=dict(treated="Commercial-zone housing projects electing it; site, corridor and labour "
                                  "criteria are not constructible; found by text.",
                          first="Applications under it, by quarter.", second="Housing on commercial parcels.",
                          unobserved="Eligibility.", confounds="SB 6 (same year), AB 2243 (2024).",
                          verdict="partially usable")))
    X.append(L("sb6", "SB 6", "CA", "SB 6 (Caballero), Stats. 2022", bill("202120220SB6"), 5, "yes",
               r"SB 6, Caballero\. Local planning: housing: commercial zones\.",
               operative_rx=r"(?P<v>July 1, 2023), and would repeal the provisions on January 1, 2033",
               sunset_rx=r"July 1, 2023, and would repeal the provisions on (?P<v>January 1, 2033)",
               elig_rx=r"allowable use on a parcel that is within a zone where office, retail, or parking are a principally permitted use",
               effective_rule="cons", applies="commercial zones",
               entry=dict(verdict="not measurable here", treated="Housing on commercial parcels electing it; not a field.",
                          first="---", second="---", unobserved="Election.", confounds="AB 2011.")))
    X.append(L("ab2097", "AB 2097", "CA", "AB 2097 (Friedman), Stats. 2022", bill("202120220AB2097"), 5, "no",
               r"AB 2097, Friedman\. Residential, commercial, or other development types: parking requirements\.",
               elig_rx=r"shall not impose or enforce any minimum automobile parking requirement on a residential, commercial, or other development project if the project is located within one-half mile of public transit",
               effective_rule="cons", applies="within one-half mile of major transit",
               entry=dict(verdict="not measurable here", treated="Projects near transit; no transit layer is joined.",
                          first="Parking per unit (PRJ records from 2018).", second="---",
                          unobserved="Transit proximity; parking before 2018.",
                          confounds="San Francisco had already removed most minimums (not sourced here).")))
    X.append(L("ab1633", "AB 1633", "CA", "AB 1633 (Ting), Stats. 2023", bill("202320240AB1633"), 2, "no",
               r"AB 1633, Ting\. Housing Accountability Act: disapprovals: California Environmental Quality Act\.",
               elig_rx=r"fails to make a determination of whether the project is exempt from CEQA or commits an abuse of discretion",
               effective_rule="cons", applies="as the HAA",
               entry=dict(verdict="not measurable here", treated="As the HAA.", first="CEQA delay on compliant "
                          "projects; CEQA dates are not recorded.", second="---", unobserved="CEQA dates.",
                          confounds="---")))
    X.append(L("sb4", "SB 4", "CA", "SB 4 (Wiener), Stats. 2023", bill("202320240SB4"), 1, "yes",
               r"SB 4, Wiener\. Planning and zoning: housing development: higher education institutions and religious institutions\.",
               sunset_rx=r"(?P<v>January 1, 2036), and as of that date is repealed",
               elig_rx=r"use by right upon the request of an applicant who submits an application for streamlined approval, on any land owned by an independent institution of higher education or religious institution",
               effective_rule="cons", applies="land of religious and higher-education institutions",
               entry=dict(verdict="not measurable here", treated="Projects on such land; ownership is not in the data.",
                          first="---", second="---", unobserved="Ownership, election.", confounds="---")))
    X.append(L("ab2243", "AB 2243", "CA", "AB 2243 (Wicks), Stats. 2024", bill("202320240AB2243"), 1, "yes",
               r"AB 2243, Wicks\. Housing development projects: objective standards: affordability and site criteria\.",
               effective_rule="cons", applies="amends AB 2011 and SB 6",
               entry=dict(verdict="not measurable here", treated="As AB 2011.", first="As AB 2011.", second="---",
                          unobserved="As AB 2011.", confounds="An amendment; no separate variation.")))
    # ── accessory dwelling units ──
    for bid, short, auth, yr, title in (
            ("201920200AB68", "AB 68", "Ting", 2019, r"AB 68, Ting\. Land use: accessory dwelling units\."),
            ("201920200AB881", "AB 881", "Bloom", 2019, r"AB 881, Bloom\. Accessory dwelling units\."),
            ("201920200AB587", "AB 587", "Friedman", 2019, r"AB 587, Friedman\. Accessory dwelling units: sale or separate conveyance\."),
            ("202120220AB2221", "AB 2221", "Quirk-Silva", 2022, r"AB 2221, Quirk-Silva\. Accessory dwelling units\."),
            ("202120220SB897", "SB 897", "Wieckowski", 2022, r"SB 897, Wieckowski\. Accessory dwelling units: junior accessory dwelling units\.")):
        X.append(L(short.lower().replace(" ", ""), short, "CA", f"{short} ({auth}), Stats. {yr}", bill(bid), 5, "yes",
                   title, secondary="1", effective_rule="cons", applies="lots with a dwelling",
                   elig_rx=(r"ministerially approve or deny a permit application for the creation of an accessory dwelling unit or junior accessory dwelling unit within 60 days"
                            if short == "AB 68" else None),
                   entry=dict(treated="ADU permits: DBI's ADU flag and the text.",
                              first="ADU permits filed, by quarter (the 2020 state package; the 2023 local conformity).",
                              second="Units added in existing buildings.", unobserved="Which statute a permit relied on.",
                              confounds="Five statutes in four years, San Francisco's own ADU ordinances, the pandemic.",
                              verdict="partially usable" if short == "AB 68" else "not measurable here")))
    # ── 2025 ──
    X.append(L("ab130", "AB 130 (2025 budget trailer: housing)", "CA", "AB 130 (Committee on Budget), Stats. 2025",
               bill("202520260AB130"), 2, "no", r"AB 130, Committee on Budget\. Housing\.", secondary="1",
               elig_rx=r"this division does not apply to any aspect of a housing development project",
               effective_rule="immediate", applies="infill housing meeting Pub. Res. Code 21080.66",
               extra=[("elig_source", r"this division does not apply to any aspect of a housing development project", CODE.format(c="PRC", s="21080.66"))],
               entry=dict(verdict="not measurable here", treated="Infill projects exempt from CEQA under it; "
                          "the exemption is not a field.", first="CEQA document type on PRJ records.", second="---",
                          unobserved="Eligibility.", confounds="SB 131 (same day); too recent.")))
    X.append(L("sb131", "SB 131 (2025 budget trailer: CEQA)", "CA", "SB 131 (Committee on Budget and Fiscal Review), Stats. 2025",
               bill("202520260SB131"), 2, "no", r"SB 131, Committee on Budget and Fiscal Review\. Public Resources\.",
               elig_rx=r"This division does not apply to a rezoning that implements the schedule of actions contained in an approved housing element",
               effective_rule="immediate", applies="as the bill specifies",
               entry=dict(verdict="not measurable here", treated="As specified; not a field.", first="---",
                          second="---", unobserved="---", confounds="AB 130; too recent.")))
    X.append(L("sb79", "SB 79", "CA", "SB 79 (Wiener), Stats. 2025", bill("202520260SB79"), 5, "yes",
               r"SB 79, Wiener\. Housing development: transit-oriented development\.",
               operative_rx=r"(?P<v>July 1, 2026), except as specified, or within unincorporated areas",
               effective_rule="cons", applies="near transit-oriented development stops; San Francisco adopted an alternative plan (Ord. 82-26)",
               entry=dict(verdict="not measurable here", treated="Parcels near qualifying stops; too recent.",
                          first="---", second="---", unobserved="---", confounds="Ord. 82-26, the Family Zoning Plan.")))
    X.append(L("rhna", "Housing Element revision schedule", "CA", "Gov. Code 65588", CODE.format(c="GOV", s="65588"), 5, "no",
               r"Each local government shall review its housing element as frequently as appropriate",
               secondary="1", effective_rule="code",
               applies="every jurisdiction; the Bay Area's sixth-cycle due date is set by HCD's schedule (not read)",
               entry=dict(verdict="not measurable here", treated="The whole city.", first="---", second="---",
                          unobserved="The due dates.", confounds="---")))
    # ── San Francisco ──
    def SF(key, short, o, cat, target_rx, *, secondary="", opt_in="no", applies="", entry=None, year=None):
        f = pd.read_csv(bx.ORD_INDEX, dtype=str).fillna("").set_index("ord").loc[o]
        y = int(f.list_year) if year is None else year
        return L(key, short, "SF", f"Ord. {o.lstrip('0')} (File {f.file})", ord_pdf(o), cat, opt_in, target_rx,
                 secondary=secondary, applies=applies, effective_rule="sf",
                 extra=[("effective", rf"{f.file} {o} (?P<v>\d{{1,2}}/\d{{1,2}}/\d{{4}})", ord_list(y))],
                 entry=entry)
    X.append(SF("propc", "Proposition C and Ord. 76-16", "0076-16", 3,
                r"will become effective only on the effective date of the 16 Charter amendment revising Section 16\.110 at the June 7, 2016 election",
                applies="projects subject to the inclusionary program",
                entry=dict(verdict="usable", treated="Every project of ten or more units: the exaction panel.",
                           first="The vested inclusionary share (the exaction panel).",
                           second="Filing around the cutoffs; unit counts at the tiers.",
                           unobserved="Tenure for most projects.", confounds="158-17 fourteen months later.")))
    X.append(SF("ord158", "Ord. 158-17 (inclusionary)", "0158-17", 3,
                r"following voter approval of Proposition Cat the June 7, 2016 election",
                applies="projects with a complete EEA after 2016-01-12",
                entry=dict(verdict="usable", treated="As Prop C.", first="As Prop C.", second="As Prop C.",
                           unobserved="As Prop C.", confounds="The escalation keyed to the EEA date.")))
    X.append(SF("ord193", "Ord. 193-23 (fee vesting and waivers)", "0193-23", 4,
                r"provide that the type and rates of applicable development impact fees, with the exception of inclusionary housing fees, shall be determined at the time of project approval",
                secondary="3", applies="projects owing impact fees",
                entry=dict(verdict="partially usable", treated="Every project owing impact fees; the waiver "
                           "districts are in the zoning panel.", first="The vesting date of fees (the exaction panel).",
                           second="Timing of approvals and permits.", unobserved="Which projects took the waiver.",
                           confounds="201-23 a month later.")))
    X.append(SF("ord201", "Ord. 201-23 (fee reductions)", "0201-23", 3,
                r"reduce Article 4 development impact fees, including lnclusionary Affordable Housing fees",
                secondary="4", applies="pipeline and 2023-26 approvals",
                entry=dict(verdict="usable", treated="Approval dates and first construction documents: the exaction panel.",
                           first="The share of approved projects reaching a first construction document within 30 months.",
                           second="The same, against projects approved before the window.",
                           unobserved="Which pipeline projects requested the modification.",
                           confounds="Interest rates, 193-23.")))
    X.append(SF("homesf", "HOME-SF", "0116-17", 5,
                r"to add the Local Affordable Housing Bonus HOME-SF 5 Program",
                secondary="1", opt_in="yes", applies="projects that elect it",
                entry=dict(verdict="partially usable", treated="Projects electing it, by text; the 2019 ordinance "
                           "added a HOME-SF Project Authorization (not read).", first="HOME-SF applications by quarter.",
                           second="Project size.", unobserved="Election except by text.",
                           confounds="The state density bonus; 2023 amendments.")))
    X.append(SF("he2022", "Housing Element 2022 Update", "0010-23", 5,
                r"adopting the Housing Element 2022 Update as the Housing Element of the General Plan",
                secondary="1", applies="the whole city",
                entry=dict(verdict="not measurable here", treated="The whole city.", first="---", second="---",
                           unobserved="---", confounds="Every 2023--2025 ordinance implements it.")))
    X.append(SF("fhosud", "Family Housing Opportunity SUD", "0195-23", 1,
                r"create the Family Housing Opportunity Special Use District",
                secondary="5", applies="eligible projects in RH districts within the SUD",
                entry=dict(verdict="partially usable", treated="RH parcels in the SUD (a zoning-panel join); "
                           "eligibility conditions partly constructible.",
                           first="Discretionary reviews and CUs for RH projects in the SUD, by quarter.",
                           second="Multi-unit filings in RH districts.", unobserved="Eligibility details.",
                           confounds="The 2024 Housing Production ordinance.")))
    X.append(SF("hp2023", "Housing Production (Ord. 248-23)", "0248-23", 1,
                r"exempting, under certain conditions, specified housing projects from the notice and review procedures of Section 311 and the Conditional Use requirement of Section 317",
                applies="specified housing projects outside the Priority Equity Geographies",
                entry=dict(verdict="usable", treated="Residential items on the docket; the Priority Equity "
                           "Geographies SUD is a polygon not yet joined.",
                           first="Residential CU and DR items per quarter, before and after.",
                           second="Filings of the exempted project types.",
                           unobserved="Which projects the exemptions reached.",
                           confounds="Ord. 53-24 four months later; the Housing Element.")))
    X.append(SF("hp2024", "Housing Production (Ord. 53-24)", "0053-24", 1,
                r"exempting, under certain conditions, specified housing projects from the notice and review procedures of Section 311",
                applies="as 248-23",
                entry=dict(verdict="usable", treated="As 248-23.", first="As 248-23.", second="As 248-23.",
                           unobserved="As 248-23.", confounds="248-23.")))
    X.append(SF("fzp", "Family Zoning Plan", "0245-25", 5,
                r"create the Housing Choice-San Francisco Program to incent housing development through a local bonus program",
                secondary="1", applies="the rezoned areas",
                entry=dict(verdict="not measurable here", treated="Rezoned parcels; too recent.", first="---",
                           second="---", unobserved="---", confounds="SB 79 and Ord. 82-26.")))
    X.append(SF("incwaiver", "Inclusionary waiver for rent control (Ord. 260-25)", "0260-25", 3,
                r"allow the City to waive the lnclusionary Housing Fee and other requirements", opt_in="yes",
                applies="outside the Priority Equity Geographies SUD",
                entry=dict(verdict="not measurable here", treated="Too recent.", first="---", second="---",
                           unobserved="---", confounds="---")))
    X.append(SF("feedefer", "Impact-fee deferral (Ord. 196-25)", "0196-25", 3,
                r"postponing the collection of development impact fees for designated residential development projects",
                secondary="4", applies="designated residential projects",
                entry=dict(verdict="not measurable here", treated="Too recent.", first="---", second="---",
                           unobserved="---", confounds="---")))
    X.append(SF("sitepermit", "Site-permit streamlining (Ord. 154-23)", "0154-23", 2,
                r"define and limit the scope of Building Official review of site permits",
                applies="site permits",
                entry=dict(verdict="partially usable", treated="Site permits (DBI's site-permit flag).",
                           first="Days from filing to issuance of site permits, before and after.",
                           second="---", unobserved="---", confounds="DBI staffing, the permit backlog.")))
    X.append(SF("cuappeal", "CU appeals (Ord. 191-22)", "0191-22", 2,
                r"allow the signatures of Verified Tenants to count towards the threshold needed to permit an appeal of a Conditional Use authorization",
                applies="CU authorizations",
                entry=dict(verdict="not measurable here", treated="CU appeals; the Board's appeal docket is not in the data.",
                           first="---", second="---", unobserved="Appeals.", confounds="---")))
    X.append(SF("mindens", "Minimum densities (Ord. 292-24)", "0292-24", 1,
                r"require oonditional use authorization for residential housing developments that do not maximize minimum residential density",
                secondary="5", applies="RM, RC and RTO districts",
                entry=dict(verdict="partially usable", treated="Projects in RM/RC/RTO below minimum density "
                           "(zoning panel and unit counts).", first="CUs for under-density projects.",
                           second="Unit counts.", unobserved="The minimum density per parcel.",
                           confounds="The Family Zoning Plan.")))
    X.append(dict(key="july2026", short="The 25-unit ordinance of July 2026 (brief)", juris="SF",
                  cite="not found", src=SF2026, cat=3, secondary="", opt_in="", target_rx=None,
                  sunset_rx=None, operative_rx=None, elig_rx=None, extra=[], effective_rule=None,
                  applies="unknown", entry=dict(verdict="not measurable here", treated="Not identified.",
                                                first="---", second="---", unobserved="The law itself.",
                                                confounds="---")))
    return X


def _date(s: str):
    if not s:
        return pd.NaT
    s = s.replace(",", ", ").replace("  ", " ")
    return pd.to_datetime(s, errors="coerce")


CODE_NAMES = {"Government": "GOV", "Public Resources": "PRC", "Health and Safety": "HSC", "Civil": "CIV",
              "Revenue and Taxation": "RTC", "Education": "EDC", "Business and Professions": "BPC",
              "Labor": "LAB", "Welfare and Institutions": "WIC", "Code of Civil Procedure": "CCP"}


def touched_sections(t: str) -> list[tuple[str, str]]:
    """(lawCode, section) for every code section a bill's title says it adds or amends: the
    title reads 'An act to amend Sections 65589.5 and 65905.5 of, and to add Section 65941.1
    to, the Government Code, ... relating to housing'."""
    m = re.search(r"An act to (.*?)(?:, relating to|relating to)", t)
    if not m:
        return []
    out = []
    for seg in re.split(r"(?<=Code)[,;]?", m.group(1)):
        cm = re.search(r"the ([A-Z][A-Za-z ]+?) Code$", seg.strip())
        if not cm or cm.group(1) not in CODE_NAMES:
            continue
        for sec in re.findall(r"\b(\d{3,6}(?:\.\d+)*)\b", seg[:cm.start()]):
            out.append((CODE_NAMES[cm.group(1)], sec))
    return out


def effective_from_notes(law: dict, chapter: str, year: str) -> tuple[str, str] | None:
    """A statute's effective date as leginfo's history note on a section it added or amended
    states it ('(Added by Stats. 2019, Ch. 654, Sec. 3. (SB 330) Effective January 1, 2020.)').
    A section amended since shows only its latest note, so every touched section is tried; the
    first that still carries this chapter's note answers."""
    t = bx.source_text(law["src"])
    short = law["short"].split(" (")[0]
    rx = (rf"Stats\. {year}, Ch\. {chapter}, Sec\. [\d.]+\. \({re.escape(short)}\) "
          rf"Effective (?P<v>[A-Z][a-z]+ \d{{1,2}}, \d{{4}})")
    for code, sec in touched_sections(t)[:12]:
        u = CODE.format(c=code, s=sec)
        try:
            if re.search(rx, bx.source_text(u)):
                return u, rx
        except Exception:
            continue
    return None


def curate() -> pd.DataFrame:
    """Every claim of every law, with its quotation, and the constitutional effective-date
    rule as a claim of its own."""
    rows = []

    def add(key, field, url, rx):
        try:
            t = bx.source_text(url)
            m = re.search(rx, t) if rx else None
        except Exception as e:
            print(f"  {key}/{field}: {e}")
            t, m = "", None
        val = ""
        if m:
            gd = m.groupdict()
            val = gd.get("v") or gd.get("w") or ""
        rows.append({"law": key, "field": field, "value": val, "source_url": url,
                     "quote": m.group(0) if m else "", "pattern": rx or ""})
        if rx and not m:
            print(f"  MISSING {key}/{field}")

    add("_constitution", "effective_rule", CONS_IV8,
        r"go into effect on January 1 next following a 90-day period from the date of enactment of the statute")
    for law in laws():
        k, u = law["key"], law["src"]
        if law["juris"] == "CA" and "billText" in u:
            add(k, "chapter", u, CHAPTER)
            add(k, "enacted", u, APPROVED)
            if law["effective_rule"] == "immediate":
                add(k, "immediate", u, IMMEDIATE)
            else:
                t = bx.source_text(u)
                ch = re.search(CHAPTER, t)
                yr = re.search(r"Stats\. (\d{4})", law["cite"])
                hit = effective_from_notes(law, ch.group("v"), yr.group(1)) if ch and yr else None
                if hit:
                    add(k, "effective", *hit)
                else:
                    print(f"  {k}: no history note carries its effective date")
        if law["juris"] == "SF" and law["target_rx"]:
            add(k, "enacted", u, SF_PASSED)
        if law["target_rx"]:
            add(k, "target", u, law["target_rx"])
        if law["elig_rx"]:
            add(k, "eligibility", u, law["elig_rx"])
        if law["sunset_rx"]:
            add(k, "sunset", u, law["sunset_rx"])
        if law["operative_rx"]:
            add(k, "operative", u, law["operative_rx"])
        for field, rx, *src in law["extra"]:
            add(k, field, src[0] if src else u, rx)
    c = pd.DataFrame(rows)
    MEMO.mkdir(parents=True, exist_ok=True)
    c.to_csv(CLAIMS, index=False)
    print(f"{len(c)} claims → {CLAIMS}")
    return c


def probe() -> pd.DataFrame:
    c = pd.read_csv(CLAIMS, dtype=str, keep_default_na=False)
    st = []
    for r in c.itertuples():
        try:
            t = bx.source_text(r.source_url)
            st.append("yes" if r.quote and bx.norm_text(r.quote) in t else "no")
        except Exception as e:
            st.append(f"unfetched ({type(e).__name__})")
    c["verified"] = st
    LAWS_DIR.mkdir(parents=True, exist_ok=True)
    c.to_csv(CHECK, index=False)
    print(c.verified.value_counts().to_string())
    return c


# ── the July 2026 check ──────────────────────────────────────────────────────
def sf_2026_titles() -> pd.DataFrame:
    """The 2026 ordinances as sf.gov lists them (the Board's own list, now on sf.gov; its
    archive copy stops at Ord. 52-26): number, file, effective date, title."""
    from bs4 import BeautifulSoup
    bx.source_text(SF2026)
    raw = (bx.LEGAL_DOCS / f"{bx.doc_id(SF2026)}.html").read_bytes()
    s = BeautifulSoup(raw, "html.parser")
    rows, seen = [], set()
    for a in s.select("a"):
        h = a.get("href") or ""
        if ".pdf" not in h or h in seen:
            continue
        seen.add(h)
        p = a.find_parent(["li", "p", "div", "tr"])
        t = re.sub(r"\s+", " ", p.get_text(" ", strip=True)) if p else ""
        m = re.search(r"File Number ?: ?(\d+) Enactment Number ?: ?o ?(\d{4}-\d{2}).*?Effective Date ?: ?([\d/]+) "
                      r"Title ?: ?(.*)$", t)
        if m and m.group(2) in h:
            rows.append({"file": m.group(1), "ord": m.group(2), "effective": m.group(3),
                         "title": m.group(4), "pdf": h})
    return pd.DataFrame(rows).drop_duplicates("ord")


# ═══════════════════════════════════════════════════════════════════════════
# build: inventory, populations, series
# ═══════════════════════════════════════════════════════════════════════════
RES_ITEM = re.compile(r"(?i)dwelling unit|residential|housing|\bunits?\b|\badu\b|apartment|condominium")
REQUEST_GROUP = {"conditional_use": "conditional use", "conditional_use_modification": "conditional use",
                 "discretionary_review": "discretionary review", "large_project_authorization": "large project authorisation",
                 "downtown_project": "large project authorisation", "variance": "variance",
                 "office_allocation": "office allocation"}
TEXT_PATTERNS = {"sb35": r"(?i)\bSB[- ]?35\b", "sb423": r"(?i)\bSB[- ]?423\b", "sb9": r"(?i)\bSB[- ]?9\b|urban lot split",
                 "ab2011": r"(?i)\bAB[- ]?2011\b", "homesf": r"(?i)home[- ]sf", "dbl": r"(?i)density bonus|65915",
                 "fhosud": r"(?i)family housing opportunity", "sb4": r"(?i)\bSB[- ]?4\b"}


def inventory(checked: pd.DataFrame) -> pd.DataFrame:
    const_ok = bool(((checked.law == "_constitution") & checked.verified.eq("yes")).any())
    # Does every history note found put the effective date on the January 1 after the year
    # of the Governor's approval? If so, that pattern stands in (labelled inferred) for the
    # statutes whose sections have all been amended since.
    ok = checked.verified.eq("yes")
    ca = {l["key"] for l in laws() if l["effective_rule"] == "cons"}
    notes = checked[ok & checked.field.eq("effective") & checked.law.isin(ca)][["law", "value"]].merge(
        checked[ok & checked.field.eq("enacted")][["law", "value"]], on="law", suffixes=("_eff", "_enacted"))
    pattern = bool(len(notes)) and all(
        _date(r.value_eff) == pd.Timestamp(f"{_date(r.value_enacted).year + 1}-01-01") for r in notes.itertuples())
    rows = []
    for law in laws():
        c = checked[checked.law.eq(law["key"])]
        val = lambda f: (c.loc[c.field.eq(f) & c.verified.eq("yes"), "value"].iloc[0]
                         if (c.field.eq(f) & c.verified.eq("yes")).any() else "")
        enacted = _date(val("enacted"))
        eff, basis = "", ""
        if law["effective_rule"] == "cons":
            # Only a date the source states. The Constitution's rule (the January 1 after a
            # 90-day period from enactment) is quoted but not computed: read with enactment as
            # the Governor's signature it would put an October bill's date a year later than
            # the history notes do.
            e = _date(val("effective"))
            eff, basis = (str(e.date()), "history note") if pd.notna(e) else ("", "")
            if not eff and pattern and pd.notna(enacted):
                eff, basis = f"{enacted.year + 1}-01-01", "inferred"
        elif law["effective_rule"] == "immediate" and pd.notna(enacted) and val("immediate") == "" and \
                (c.field.eq("immediate") & c.verified.eq("yes")).any():
            eff, basis = str(enacted.date()), "takes effect immediately"
        elif law["effective_rule"] == "sf":
            e = _date(val("effective"))
            eff, basis = (str(e.date()), "Board list") if pd.notna(e) else ("", "")
        elif law["effective_rule"] == "code":
            basis = "a code section in force; its enactment was not read"
        ok = c.verified.eq("yes")
        ver = "unverified" if not len(c) or not ok.any() else ("yes" if ok.all() else "partial")
        rows.append({"key": law["key"], "short_name": law["short"], "jurisdiction": law["juris"],
                     "full_cite": law["cite"] + (f", ch. {val('chapter')}" if val("chapter") else ""),
                     "enacted_date": str(enacted.date()) if pd.notna(enacted) else "",
                     "effective_date": eff, "effective_basis": basis,
                     "operative_date": str(_date(val("operative")).date()) if val("operative") else "",
                     "sunset_date": str(_date(val("sunset")).date()) if val("sunset") else "",
                     "applies_to_sf": law["applies"], "opt_in": law["opt_in"],
                     "eligibility_rule": c.loc[c.field.eq("eligibility") & ok, "quote"].str.slice(0, 240).iloc[0]
                     if (c.field.eq("eligibility") & ok).any() else "",
                     "target": c.loc[c.field.eq("target") & ok, "quote"].str.slice(0, 240).iloc[0]
                     if (c.field.eq("target") & ok).any() else "",
                     "category": law["cat"], "category_name": CATEGORIES[law["cat"]],
                     "secondary": law["secondary"], "source_url": law["src"], "verified": ver,
                     "verdict": law["entry"].get("verdict", "")})
    return pd.DataFrame(rows)


def build():
    import acquire_external_data as ax
    import analyze_permit_content as apc
    checked = pd.read_csv(CHECK, dtype=str, keep_default_na=False)
    inv = inventory(checked)
    # the July 2026 check, against the Board's 2026 list on sf.gov
    t26 = sf_2026_titles()
    hits = t26[t26.title.str.contains(r"(?i)inclusionary|affordable|dwelling|units|housing|residential|density|zoning", regex=True)]
    july = t26[pd.to_datetime(t26.effective, errors="coerce").dt.month.isin([7, 8])]
    j = {"n_2026": len(t26), "last_ord": t26.ord.max() if len(t26) else "",
         "last_effective": str(pd.to_datetime(t26.effective, errors="coerce").max().date()) if len(t26) else "",
         "housing_titles": hits[["ord", "effective", "title"]].to_dict("records"),
         "july_aug_titles": len(july),
         "inclusionary_or_units_2026": int(t26.title.str.contains(r"(?i)inclusionary|25 units|twenty-five").sum())}
    MEMO.mkdir(parents=True, exist_ok=True)
    inv.to_csv(INVENTORY, index=False)
    # ── the docket's composition, by quarter ──
    it = ax.load_items()
    it = it[it.meeting_date.notna()]
    it["q"] = it.meeting_date.dt.to_period("Q")
    it["group"] = it.request_type.map(REQUEST_GROUP).fillna("other")
    it["residential"] = it.project_descr.fillna("").str.contains(RES_ITEM)
    comp = it.groupby(["q", "group"]).size().unstack(fill_value=0)
    comp["residential"] = it.groupby("q").residential.sum()
    comp["residential_cu"] = it[it.group.eq("conditional use")].groupby("q").residential.sum()
    comp["residential_dr"] = it[it.group.eq("discretionary review")].groupby("q").residential.sum()
    comp["items"] = it.groupby("q").size()
    comp["meetings"] = it.groupby("q").meeting_date.nunique()
    comp = comp.fillna(0)
    # hearings per residential case, by year of first hearing (SB 330's five-hearing cap)
    cases = it[it.cn.ne("")].groupby("cn").agg(first=("meeting_date", "min"), n=("meeting_date", "nunique"),
                                              res=("residential", "max"), grp=("group", "first"))
    cases["fy"] = cases["first"].dt.year
    hear = cases[cases.res].groupby("fy").agg(cases=("n", "size"), over5=("n", lambda s: int((s > 5).sum())),
                                               mean=("n", "mean"), p90=("n", lambda s: s.quantile(.9)))
    # ── the permit side: the share of development-relevant permits that are Commission-linked ──
    B = apc.load_permits(inventory=False)
    p = B["p"]
    dev = p[p.dev & p.filed_date.notna()].copy()
    dev["q"] = dev.filed_date.dt.to_period("Q")
    ps = dev.groupby("q").agg(dev=("stem", "size"), linked=("linked", "sum"), t3=("t3_only", "sum"))
    ps["share"] = ps.linked / ps.dev
    adu = p[p.adu.eq("Y") & p.filed_date.notna()].groupby(p.filed_date.dt.to_period("Q")).size()
    site = p[p.site_permit.eq("Y") & p.permit_type.isin(["1", "2"]) & p.issued_date.notna()].copy()
    site["t"] = (site.issued_date - site.filed_date).dt.days
    site_t = site.groupby(site.filed_date.dt.to_period("Q")).t.median()
    # ── records: flags and text mentions, by quarter ──
    rec = ax.build_records()
    rec = rec[rec.open_date.notna()]
    rec["q"] = rec.open_date.dt.to_period("Q")
    d = rec.description.fillna("")
    recq = pd.DataFrame({"sb35_flag": rec[rec.sb35.eq("CHECKED")].groupby("q").size(),
                         "sb330_flag": rec[rec.sb330.eq("CHECKED")].groupby("q").size(),
                         **{f"txt_{k}": rec[d.str.contains(rx)].groupby("q").size() for k, rx in TEXT_PATTERNS.items()}}).fillna(0)
    LAWS_DIR.mkdir(parents=True, exist_ok=True)
    comp.to_csv(LAWS_DIR / "docket_composition_quarterly.csv")
    hear.to_csv(LAWS_DIR / "hearings_per_residential_case.csv")
    ps.to_csv(LAWS_DIR / "permit_linked_share_quarterly.csv")
    recq.to_csv(LAWS_DIR / "records_first_stage_quarterly.csv")
    pd.DataFrame({"adu_permits": adu, "site_permit_median_days": site_t}).to_csv(LAWS_DIR / "permit_first_stage_quarterly.csv")
    (LAWS_DIR / "build_meta.json").write_text(json.dumps({**j, "retrieved": str(B["retrieved"].date()),
                                                           "items_last": str(it.meeting_date.max().date())},
                                                          indent=1, default=str))
    print(f"{len(inv)} laws ({inv.verified.value_counts().to_dict()}); July 2026 check: "
          f"{j['inclusionary_or_units_2026']} matching titles of {j['n_2026']} → {LAWS_DIR}")


# ═══════════════════════════════════════════════════════════════════════════
# report
# ═══════════════════════════════════════════════════════════════════════════
def _n(x) -> str:
    return "---" if x is None or pd.isna(x) else f"{x:,.0f}".replace(",", "{,}")


def _t(s) -> str:
    return (str(s).replace("\\", r"\textbackslash{}").replace("&", r"\&").replace("%", r"\%")
            .replace("$", r"\$").replace("#", r"\#").replace("_", r"\_"))


def report():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    FIG.mkdir(parents=True, exist_ok=True)
    TAB.mkdir(parents=True, exist_ok=True)
    inv = pd.read_csv(INVENTORY, dtype=str, keep_default_na=False)
    checked = pd.read_csv(CHECK, dtype=str, keep_default_na=False)
    meta = json.loads((LAWS_DIR / "build_meta.json").read_text())
    comp = pd.read_csv(LAWS_DIR / "docket_composition_quarterly.csv", index_col=0)
    comp.index = pd.PeriodIndex(comp.index, freq="Q")
    hear = pd.read_csv(LAWS_DIR / "hearings_per_residential_case.csv", index_col=0)
    ps = pd.read_csv(LAWS_DIR / "permit_linked_share_quarterly.csv", index_col=0)
    ps.index = pd.PeriodIndex(ps.index, freq="Q")
    recq = pd.read_csv(LAWS_DIR / "records_first_stage_quarterly.csv", index_col=0)
    recq.index = pd.PeriodIndex(recq.index, freq="Q")
    pq = pd.read_csv(LAWS_DIR / "permit_first_stage_quarterly.csv", index_col=0)
    pq.index = pd.PeriodIndex(pq.index, freq="Q")
    L = {l["key"]: l for l in laws()}
    M: dict[str, str] = {}
    T = ["% GENERATED BY audit_state_laws.py report --- do not edit by hand."]

    def block(label, lines):
        T.extend([rf"\ifnum\pdfstrcmp{{\rttab}}{{{label}}}=0", *lines, r"\fi"])

    # ── inventory table ──
    lines = [r"{\scriptsize\begin{longtable}{L{2.6cm}L{2.8cm}L{1.45cm}L{1.45cm}L{1.45cm}L{1.45cm}cL{1.3cm}}",
             r"\caption{The law inventory. Dates are read from the quotations (Section~\ref{sec:rtsources}); a "
             r"California statute's effective date is the January~1 after a 90-day period from enactment "
             r"unless the bill takes effect immediately; San Francisco's are the Board's list. Cat.: the model "
             r"object (Section~\ref{sec:rtcats}). The full rows, with eligibility and target quotations, are "
             r"\texttt{law\_inventory.csv}.}\label{tab:rtinventory}\\\toprule",
             r"Law & Citation & Enacted & Effective & Operative & Sunset & Cat. & Verified\\\midrule\endfirsthead",
             r"\toprule Law & Citation & Enacted & Effective & Operative & Sunset & Cat. & Verified\\\midrule\endhead"]
    for r in inv.itertuples():
        lines.append(rf"{_t(r.short_name)} & {_t(r.full_cite)} & {r.enacted_date or '---'} & {(r.effective_date + ('$^i$' if r.effective_basis == 'inferred' else '')) if r.effective_date else '---'} & "
                     rf"{r.operative_date or '---'} & {r.sunset_date or '---'} & {r.category}{('/' + r.secondary) if r.secondary else ''} & {r.verified}\\")
    lines.append(r"\bottomrule\end{longtable}}")
    block("rtinventory", lines)

    # ── populations, per law, from the data ──
    yrs = lambda s, y0, y1: s[(s.index.year >= y0) & (s.index.year <= y1)].sum() / max(1, (y1 - y0 + 1))
    pop = {
        "sb35": ("SB 35 flag + text", (recq.sb35_flag + recq.txt_sb35).groupby(recq.index.year).sum()),
        "sb423": ("SB 423 in text", recq.txt_sb423.groupby(recq.index.year).sum()),
        "sb330": ("SB 330 flag (PRJ)", recq.sb330_flag.groupby(recq.index.year).sum()),
        "sb9": ("SB 9 or lot split in text", recq.txt_sb9.groupby(recq.index.year).sum()),
        "ab2011": ("AB 2011 in text", recq.txt_ab2011.groupby(recq.index.year).sum()),
        "dbl": ("density bonus in text", recq.txt_dbl.groupby(recq.index.year).sum()),
        "homesf": ("HOME-SF in text", recq.txt_homesf.groupby(recq.index.year).sum()),
        "fhosud": ("FHO SUD in text", recq.txt_fhosud.groupby(recq.index.year).sum()),
        "ab68": ("DBI ADU permits", pq.adu_permits.groupby(pq.index.year).sum()),
        "hp2023": ("residential CU items", comp.residential_cu.groupby(comp.index.year).sum()),
        "hp2024": ("residential CU items", comp.residential_cu.groupby(comp.index.year).sum()),
        "sb4": ("SB 4 in text", recq.txt_sb4.groupby(recq.index.year).sum()),
    }
    popline = {}
    for k, (lab, s) in pop.items():
        s = s[s.index <= 2025]
        recent = s[s.index >= 2021]
        popline[k] = f"{lab}: {_n(recent.mean())} a year, 2021--2025" if len(recent) else lab
    # ── summary table ──
    lines = [r"{\small\begin{longtable}{L{4.2cm}cL{4.6cm}L{2.2cm}}",
             r"\caption{Summary: one row per law. Treated set: whether the data can say which projects the law "
             r"reached. Population: projects or records a year where the data counts them (records and permits "
             r"through 2025).}\label{tab:rtsummary}\\\toprule",
             r"Law & Cat. & Treated set / population & Verdict\\\midrule\endfirsthead",
             r"\toprule Law & Cat. & Treated set / population & Verdict\\\midrule\endhead"]
    order = {"usable": 0, "partially usable": 1, "not measurable here": 2}
    inv2 = inv.assign(o=inv.verdict.map(order)).sort_values(["o", "category"])
    for r in inv2.itertuples():
        e = L[r.key]["entry"]
        tr = e.get("treated", "---")
        if r.key in popline:
            tr += " " + popline[r.key] + "."
        lines.append(rf"{_t(r.short_name)} & {r.category} & \scriptsize {_t(tr)} & {_t(r.verdict)}\\")
    lines.append(r"\bottomrule\end{longtable}}")
    block("rtsummary", lines)

    # ── measurability entries ──
    lines = []
    for cat in sorted(CATEGORIES):
        sub = inv[inv.category.astype(int).eq(cat)]
        if not len(sub):
            continue
        lines.append(rf"\subsection{{Category {cat}: {CATEGORIES[cat]}}}")
        for r in sub.itertuples():
            e = L[r.key]["entry"]
            c = checked[checked.law.eq(r.key) & checked.verified.eq("yes")]
            tq = c.loc[c.field.eq("target"), "quote"]
            eq = c.loc[c.field.eq("eligibility"), "quote"]
            lines.append(rf"\paragraph{{{_t(r.short_name)}}} \textit{{{_t(r.full_cite)}; "
                         rf"effective {r.effective_date or 'not established'}{' (inferred)' if r.effective_basis == 'inferred' else ''}; opt-in: {r.opt_in or '---'}; "
                         rf"verified: {r.verified}.}}")
            if len(tq):
                lines.append(r"\emph{What it changed.} In its own words: ``" + _t(tq.iloc[0][:300]) + "''" +
                             (" --- ``" + _t(eq.iloc[0][:260]) + "''" if len(eq) else "") + ".")
            else:
                lines.append(r"\emph{What it changed.} Not established: no quotation verified.")
            lines.append(r"\emph{Treated set.} " + _t(e.get("treated", "---")) +
                         (" " + _t(popline[r.key]) + "." if r.key in popline else ""))
            lines.append(r"\emph{Mechanical first stage.} " + _t(e.get("first", "---")) +
                         r" \emph{Behavioural second stage.} " + _t(e.get("second", "---")) +
                         r" \emph{Unobserved.} " + _t(e.get("unobserved", "---")) +
                         r" \emph{Confounds.} " + _t(e.get("confounds", "---")) +
                         r" \emph{Verdict:} \textbf{" + _t(e.get("verdict", "")) + "}.")
    block("rtentries", lines)

    # ── figures ──
    def marks(ax_, keys, y=None):
        for k in keys:
            r = inv[inv.key.eq(k)]
            if not len(r):
                continue
            d = r.operative_date.iloc[0] or r.effective_date.iloc[0]
            if not d:
                continue
            x = pd.Period(pd.Timestamp(d), freq="Q").to_timestamp()
            ax_.axvline(x, color="0.6", lw=0.8, ls=":")
            ax_.text(x, ax_.get_ylim()[1] * 0.97, r.short_name.iloc[0].split(" (")[0], rotation=90, fontsize=6,
                     va="top", ha="right", color="0.35")
    cat1 = inv[inv.category.eq("1")].key.tolist()
    groups = ["conditional use", "discretionary review", "large project authorisation", "variance",
              "office allocation", "other"]
    x = comp.index.to_timestamp()
    fig, axs = plt.subplots(2, 1, figsize=(11, 7.5), sharex=True)
    base = np.zeros(len(comp))
    cols = ["#a33", "#5b7fa6", "#b07d2b", "#2f6f4f", "#8a6fb0", "0.7"]
    for g, c in zip(groups, cols):
        v = comp[g].values if g in comp else np.zeros(len(comp))
        axs[0].fill_between(x, base, base + v, step="post", color=c, alpha=0.8, label=g)
        base = base + v
    axs[0].plot(x, comp.residential, color="k", lw=1.2, drawstyle="steps-post", label="residential items (text)")
    axs[0].set_ylabel("items per quarter")
    axs[0].legend(fontsize=7, ncol=4, loc="upper left")
    axs[0].set_title("(a) The Commission's docket by request type, per quarter; dotted lines: category-1 laws "
                     f"(minutes through {meta['items_last']})", fontsize=9)
    marks(axs[0], cat1)
    sh = comp[groups].div(comp[groups].sum(axis=1), axis=0)
    for g, c in zip(groups[:3], cols[:3]):
        axs[1].plot(x, sh[g], drawstyle="steps-post", color=c, alpha=0.35, lw=0.8)
        axs[1].plot(x, sh[g].rolling(4, min_periods=2).mean(), color=c, lw=2, label=f"{g} (4-quarter mean)")
    ax2 = axs[1].twinx()
    xs = ps.index.to_timestamp()
    ax2.plot(xs[xs >= x.min()], ps.share[xs >= x.min()].rolling(4, min_periods=2).mean(), color="k", lw=1.5,
             ls="--", label="Commission-linked share of development permits (right)")
    axs[1].set_ylabel("share of the docket")
    ax2.set_ylabel("share of permits")
    axs[1].legend(fontsize=7, loc="upper left")
    ax2.legend(fontsize=7, loc="upper right")
    axs[1].set_title("(b) Shares; faint: quarterly, bold: four-quarter mean", fontsize=9)
    marks(axs[1], cat1)
    fig.tight_layout()
    fig.savefig(FIG / "fig_diversion.pdf")
    plt.close(fig)

    fig, axs = plt.subplots(3, 2, figsize=(11, 9))
    panels = [
        ("SB 35 / SB 423 records per quarter", [(recq.sb35_flag + recq.txt_sb35, "SB 35 (flag + text)"),
                                                (recq.txt_sb423, "SB 423 (text)")], ["sb35", "sb423"]),
        ("SB 330 preliminary applications (PRJ flag)", [(recq.sb330_flag, "flagged")], ["sb330", "sb8"]),
        ("ADU permits filed (DBI flag)", [(pq.adu_permits, "ADU permits")], ["ab68", "ab2221"]),
        ("Density bonus and HOME-SF in record text", [(recq.txt_dbl, "density bonus"), (recq.txt_homesf, "HOME-SF")],
         ["homesf", "ab2345"]),
        ("Residential CU and DR items per quarter", [(comp.residential_cu, "residential CU"),
                                                      (comp.residential_dr, "residential DR")], ["hp2023", "hp2024", "fhosud"]),
    ]
    last_full = pd.Period(pd.Timestamp(meta["retrieved"]), freq="Q") - 1
    for a_, (title, ser, ks) in zip(axs.flat, panels):
        for (s, lab), c in zip(ser, ("#a33", "#5b7fa6")):
            s = s[(s.index.year >= 2012) & (s.index <= last_full)]
            xx = s.index.to_timestamp()
            a_.plot(xx, s.values, drawstyle="steps-post", lw=0.8, alpha=0.35, color=c)
            a_.plot(xx, s.rolling(4, min_periods=2).mean().values, lw=2, color=c, label=lab + " (4-quarter mean)")
        a_.set_title(title, fontsize=9)
        a_.legend(fontsize=7, loc="upper left")
        marks(a_, ks)
    a_ = axs.flat[5]
    h = hear[(hear.index >= 2005) & (hear.index <= 2024)]
    a_.bar(h.index, h.over5 / h.cases * 100, color="#5b7fa6")
    a_.set_title("Residential cases heard on more than five dates (% of cases, by first-hearing year)", fontsize=8)
    r = inv[inv.key.eq("sb330")]
    if len(r) and r.effective_date.iloc[0]:
        a_.axvline(pd.Timestamp(r.effective_date.iloc[0]).year - 0.5, color="0.4", ls=":")
    fig.tight_layout()
    fig.savefig(FIG / "fig_first_stages.pdf")
    plt.close(fig)

    # ── macros ──
    vc = inv.verified.value_counts()
    vd = inv.verdict.value_counts()
    M.update(rtEffNotes=_n(inv.effective_basis.eq("history note").sum()),
             rtEffInferred=_n(inv.effective_basis.eq("inferred").sum()))
    M.update(rtLaws=_n(len(inv)), rtCa=_n(inv.jurisdiction.eq("CA").sum()), rtSf=_n(inv.jurisdiction.eq("SF").sum()),
             rtVerified=_n(vc.get("yes", 0)), rtPartial=_n(vc.get("partial", 0)), rtUnverified=_n(vc.get("unverified", 0)),
             rtClaims=_n(len(checked)), rtClaimsOk=_n(checked.verified.eq("yes").sum()),
             rtUsable=_n(vd.get("usable", 0)), rtPartUsable=_n(vd.get("partially usable", 0)),
             rtNotMeasurable=_n(vd.get("not measurable here", 0)),
             rtCatOne=_n(inv.category.eq("1").sum()), rtCatTwo=_n(inv.category.eq("2").sum()),
             rtCatThree=_n(inv.category.eq("3").sum()), rtCatFour=_n(inv.category.eq("4").sum()),
             rtCatFive=_n(inv.category.eq("5").sum()), rtOptIn=_n(inv.opt_in.eq("yes").sum()),
             rtSfTwentySix=_n(meta["n_2026"]), rtSfLastOrd=meta["last_ord"], rtSfLastEff=meta["last_effective"],
             rtSfMatch=_n(meta["inclusionary_or_units_2026"]), rtItemsLast=meta["items_last"])
    q0 = comp.index.min()
    M.update(rtFirstQ=str(q0), rtLastQ=str(comp.index.max()))
    pre = comp[(comp.index.year >= 2021) & (comp.index.year <= 2022)]
    post = comp[(comp.index.year >= 2024) & (comp.index <= comp.index.max())]
    M.update(rtResCuPre=f"{pre.residential_cu.mean():.1f}", rtResCuPost=f"{post.residential_cu.mean():.1f}",
             rtResDrPre=f"{pre.residential_dr.mean():.1f}", rtResDrPost=f"{post.residential_dr.mean():.1f}",
             rtItemsPre=f"{pre['items'].mean():.0f}", rtItemsPost=f"{post['items'].mean():.0f}")
    h1 = hear[(hear.index >= 2015) & (hear.index <= 2019)]
    h2 = hear[(hear.index >= 2020) & (hear.index <= 2025)]
    M.update(rtOverFivePre=f"{100 * h1.over5.sum() / h1.cases.sum():.1f}",
             rtOverFivePost=f"{100 * h2.over5.sum() / h2.cases.sum():.1f}",
             rtOverFiveNPre=_n(h1.over5.sum()), rtOverFiveNPost=_n(h2.over5.sum()),
             rtCasesPre=_n(h1.cases.sum()), rtCasesPost=_n(h2.cases.sum()))
    sh_ = ps[(ps.index.year >= 2005)]
    M.update(rtLinkedEarly=f"{100 * sh_[sh_.index.year <= 2009].linked.sum() / sh_[sh_.index.year <= 2009].dev.sum():.1f}",
             rtLinkedLate=f"{100 * sh_[(sh_.index.year >= 2020) & (sh_.index.year <= 2025)].linked.sum() / sh_[(sh_.index.year >= 2020) & (sh_.index.year <= 2025)].dev.sum():.1f}")
    for k, name in (("sb35", "SbThirtyFive"), ("sb423", "SbFourTwoThree"), ("sb330", "SbThreeThirty"),
                    ("ab68", "Adu"), ("dbl", "Dbl"), ("homesf", "Homesf"), ("sb9", "SbNine"),
                    ("ab2011", "AbTwentyEleven")):
        M[f"rtPop{name}"] = _t(popline.get(k, "---"))
    words = "Zero One Two Three Four Five Six Seven Eight Nine".split()
    for r in inv.itertuples():
        name = "".join(w.title() for w in re.findall(r"[a-z]+", r.key)) + "".join(words[int(d)] for d in re.findall(r"\d", r.key))
        M[f"rtEff{name}"] = r.effective_date or "---"
        M[f"rtEnacted{name}"] = r.enacted_date or "---"
    M.update(rtWinPre="2021--2022", rtWinPost=f"2024--{comp.index.max().year}", rtHearWinPre="2015--2019",
             rtHearWinPost="2020--2025", rtLinkWinEarly="2005--2009", rtLinkWinLate="2020--2025")
    shortlist = inv[inv.verdict.eq("usable")].short_name.tolist()
    M["rtShortlist"] = _t("; ".join(shortlist))
    (TAB / "regulatory_timeline_tables.tex").write_text("\n".join(T) + "\n")
    (TAB / "regulatory_timeline_macros.tex").write_text(
        "% GENERATED BY audit_state_laws.py report --- do not edit by hand.\n" +
        "\n".join(rf"\newcommand{{\{k}}}{{{v}}}" for k, v in sorted(M.items())) + "\n")
    print(f"{len(M)} macros → {TAB}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--refresh", action="store_true")
    for c in ("claims", "probe", "build", "report"):
        sub.add_parser(c)
    a = ap.parse_args()
    if a.cmd == "fetch":
        urls = {CONS_IV8, SF2026} | {l["src"] for l in laws()} | {e[2] for l in laws() for e in l["extra"] if len(e) > 2}
        for u in sorted(urls):
            try:
                bx.source_text(u, a.refresh)
            except Exception as e:
                print(f"  {u}: {e}")
    elif a.cmd == "claims":
        curate()
    elif a.cmd == "probe":
        probe()
    elif a.cmd == "build":
        build()
    elif a.cmd == "report":
        report()


if __name__ == "__main__":
    main()
