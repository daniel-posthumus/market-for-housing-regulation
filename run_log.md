# Run log

Append-only. One entry per task: start, end, what was produced, what failed, what was skipped
and why. Brief: `.claude/instructions/mfhr_next_phase_brief.md`.

## 2026-09-11 — session start

- **Start:** 2026-09-11, evening.
- **Placement of new memos.** The brief names `memos/<name>.tex`. The repository has no
  `memos/` directory; every memo lives in its own folder under
  `output/planning_commission_project/<name>/` with `figures/` and `tables/` beside it
  (CLAUDE.md, `output/README.md`). New memos follow that convention:
  `output/planning_commission_project/{project_chronicle,exactions,clocks,regulatory_timeline}/`.
  Same content, same file names; only the parent directory differs.
- **State found.** `collect_conditions.py pull` was not running. The citypln host had stopped at
  13 of 21 in its last pass; 19 pull targets were outstanding (18 on citypln, 1 on
  default.sfplanning.org), plus 18 documents skipped for size (> 250 MB). The round-2
  out-of-sample hand check (30 rows) had been completed and scored (29/30) at 16:23, and the
  memo compiled at 16:33 already carries those figures; the blank §1.4 the brief describes is an
  earlier build.
- **Order within Part 0.** Item 2 (parser merges) requires a round 3, and a round's
  out-of-sample draw must come from documents pulled *after* its freeze
  (`sample_check`). So the order is: fix the boundary rule on documents already pulled, freeze
  round 3, then finish the pull (which supplies the round-3 out-of-sample pool), then parse,
  diff, sample, score, summary, report. The brief's `sample --round 2` is superseded by
  `sample --round 3`; the round-2 sample was already drawn and a round is sampled once.

## 2026-09-11 — background legal-sourcing agents (Parts 1, 3, 5): failed

- **Start:** ~17:45. Six research agents launched to collect primary-source citations (statute
  and ordinance dates, rates, vesting rules, envelope parameters), each claim with a verbatim
  quote to be re-fetched and checked mechanically by the pipelines.
- **End:** ~18:20. **Failed.** Every agent, and the sub-agents they spawned, stopped on a stream
  watchdog timeout ("no progress for 600s"). They left 4 statute records (AB 2011, SB 6, AB 2097,
  AB 1633) and 2 ordinance records (Ord. 37-02, 220-02) in the session scratchpad, plus cached
  pages. Nothing from them is used unverified.
- **Consequence:** the sourcing is redone inside the pipelines themselves (`audit_state_laws.py`,
  `build_exaction_panel.py` `fetch`/`probe` stages): fetch the primary page, extract the quoted
  passage, and store it with the claim, so a claim with no quote in its cached source is marked
  unverified by construction.

## 2026-09-11 — Part 0, items 2–5 (conditions parser round 3, permits memo, DR clock)

- **Start:** ~17:50. **End:** items 3–5 ~20:30; item 2 done except the round-3 out-of-sample
  hand check, which waits on the pull (item 1, still running).
- **Item 2, parser merges — round 3.** Diagnosed five causes with corpus-wide detectors (a body
  holding the next condition's number; a frequent heading after a sentence end; a letter's
  salutation): margin drift on shifted scans, numbers lost to OCR, Exhibit A running into
  correspondence, a numbered sub-list (the transformer-vault schedule) flipping the 2011–14
  bold-named template to "numbered" so whole sections collapsed into one row, and garbled
  footers read as section heads. The "1994" motion was a section-split failure, not a date
  read: a 1994 scan's running header made every page a title page, and the adjacent-title-page
  rule folded Motion 18915 (2013, 3141 Clement) into Motion 13693 (1994). Six rules added to
  `condition_parser.py`; round 2 archived with its whole parse (`archive_round` now keeps it);
  round 3 frozen 19:33. Gold documents were not opened during development.
  - In-sample (gold): precision 97.7 → 98.2, recall 98.0 → 98.5, headings 100%.
  - The 30 round-2 out-of-sample rows are all reproduced exactly by round 3 (29/30 correct).
  - Detectors over the whole parse: next-number merges 31 → 16; frequent-heading merges
    1,008 → 413 (includes real named sub-parts); correspondence 34 → 1; canonical rows
    84,684 → 86,442; earliest motion for a universe case 1994 → 1999.
  - Not fixed: 16 residual next-number merges (single pages laid out unlike their section);
    a few dozen headings split by a partial bold run ("Signage" / "Program."); CEQA forms bound
    after some motions still parse as rows. Reported, not chased.
- **Item 2, other defects.** Table 8's two columns were near-identical by construction (a
  located Exhibit A always yields a row): dropped the second, caption says why and gives the
  exceptions (6). §6 additions split into template text (opening 200 characters, numbers masked,
  recurring in ≥10 other cases) and other text; the quoted draw is from the latter; the
  `modifications` claim softened. "The probe still running." fixed at the macro. The contents
  now sit on their own page (the split contents put the page folio beside "5").
- **Item 3, immortal time.** Landmark-anchored comparison added (`landmark` in
  `analyze_permit_content.py`): linked permits alive at their first linked hearing on or after
  filing; unlinked permits alive at filing + a lag drawn from linked lags in the same cell
  (5 seeds). Days to issuance: standardised difference 0.71 raw, 0.43 within cells, 0.04
  (0.03–0.06) from the hearing — 9% survives; in weighted medians 197 → 55 days (28%).
  Abandonment does not shrink (−0.16 → −0.24).
- **Item 4, hygiene.** Cost ≤ $1 rows (10.2% of filled rows) were *not* dropped before; now
  dropped from every cost comparison (log-cost std. diff. 0.43 → 0.54 raw, 0.19 → 0.23 cells).
  `first_construction_document_date` dropped as an outcome (clock table and comparison).
  **Checked the brief's premise:** the "modal value 2019-12-31" was an artefact of the field
  notes counting timestamps as strings (23 rows at that midnight); by calendar day it is not
  modal. The field is still dropped, for coverage (1.2% of permits, near-empty before the
  1990s); the table now counts dates by day. DR negative differences (201 cases, 8.9%): 76 take
  the Planning date from another record (record-type issue), 15 are T2 sibling/parent-record
  links, 110 are the DR's own permit filed after the request.
- **Item 5.** Dated note added to the data-acquisition memo (§4 clocks) with one clock per
  track; its figures are read from the permits memo's macros.
- **Commands:** `collect_conditions.py archive --round 2`, `freeze --round 3`, `parse`,
  `score --round 3`, `report`; `analyze_permit_content.py report`; three memos compiled
  (0 undefined, 0 overfull) — conditions memo to be recompiled after the pull.

## 2026-09-11 — Part 0, item 1 (conditions pull and re-run): done

- **End:** ~21:30.
- **Pull finished.** The last 36 documents (the 18 over 250 MB and the 18 failed retries on the
  citypln host; one on default.sfplanning.org) were pulled with `--max-mb 2000` after the round-3
  freeze; all succeeded except vault links that are not public (unchanged). Documents parsed:
  8,062 → 8,088; canonical condition rows 86,779.
- **Re-run:** `parse`, `diff` (1,308 draft–adopted pairs), `sample --round 3` (30 rows from the
  9 documents pulled after the freeze that carry canonical rows), blind hand check by two agents
  under `HANDCHECK.md` (29/30 rows start and end correctly — one body keeps a page number; 30/30
  headings), `score --round 3`, `summary`, `numbers` (Part 6), `report`.
- **Round 3 figures:** in-sample 98.2 / 98.5 (precision / recall), headings 100%; out of sample
  96.7% (start 100%, end 96.7%, heading 100%) on rows heard 2016–2025 only; round 2's 30 rows
  reproduced exactly. The round-effect table compares rounds on the 8,062 documents both parsed.
- **Conditions memo** regenerated (66 pp, 0 undefined, 0 overfull): §1.4 rewritten for three
  rounds; dated correction for the 1994 motion (facts generated as macros); Table 8 one column;
  §6 template split; new §9 "The numbers inside the conditions" (Part 6).
- **Skipped:** re-reading the one motion (19041, 2013.0663C) whose pages beyond the round-2 cache
  were never kept; `reextract --network` would recover it and was not run.

## 2026-09-11 — order of work after Part 0

- Parts 2 and 6 were finished before Part 1, not in the brief's order. Part 6 (`numbers`) is a
  stage of the conditions pipeline and went into the conditions memo regenerated for Part 0
  item 1; Part 2 extends `analyze_permit_content.py`, which Part 0 item 3 had just loaded and
  re-run. Part 1 was in progress throughout (its legal sourcing had to be redone in-pipeline
  after the research agents failed).

## 2026-09-11 — Part 2 (clocks): done

- **Produced:** `analyze_permit_content.py clocks`; memo
  `output/planning_commission_project/clocks/clocks.tex` (16 pp, 0 undefined, 0 overfull);
  `external/clocks/{clocks.parquet,clock_moments.csv}`. A new memo rather than a permits-memo
  section, because the moments are the model's inputs and the permits memo is about the data.
- **Choices recorded in the memo:** Kaplan–Meier for every clock by track; the ministerial track
  starts at 1998 (earlier permits that never record completion are stale records, not running
  spells); a case with no linked permit has no permit clock (missing, not censored).

## 2026-09-11 — Part 1 (exactions): done, with open questions stated in the memo

- **Produced:** `build_exaction_panel.py` (`fetch`, `claims`, `probe`, `build`, `report`); memo
  `output/planning_commission_project/exactions/exactions.tex` (19 pp, 0 undefined, 0 overfull);
  `exaction_sources.csv` (394 claims, all verified: 112 legal, 282 register rates);
  `external/exactions/{exaction_projects.parquet, rate_table.csv, parcel_fee_areas.parquet,
  claims_checked.csv, crosscheck_conditions.csv, bunching_weeks.csv, notch_counts.csv}`.
- **Sources added:** fee registers for 2011–2016 and 2018 recovered from the Wayback Machine
  (DBI and Planning URLs found through CDX); no 2017 register exists under any pattern tried
  (a gap, interpolated for pricing and flagged, drawn as a gap). Ordinances 158-17, 193-23 and
  201-23 read (page images where the text layer garbles amended figures); Prop. C (June 2016)
  sourced from Ord. 158-17's findings.
- **Failure found and fixed:** the archive served the 2020 register for the 2019 register's
  capture URL. `source_text` now refuses a capture other than the one cited; 2019–2026 are read
  from the copies `acquire_external_data.py` downloaded on 2026-09-08.
- **Corrections made while building:** alterations adding units were first included and priced
  a fire-radio permit in a 62-unit building at $2.7m (the permit repeats the building's unit
  count); the panel now prices new construction only and sets 4,039 alterations aside. A PRJ
  record's floor area is used only when plausible for the permit's own unit count. 100%
  affordable projects are excluded from the distributions. The 2006 percentages are keyed to
  the first application (62-13's Table 415.3) after the conditions cross-check showed
  pre-July-2006 filings approved years later at the 2002 rate.
- **Not done (in the memo's "What remains"):** area-specific (UMU/SUD) requirements; TSF
  grandfathering; existing-use credits; the 2017 and pre-2011 registers; how the 2016
  grandfathering deadline was applied (conditions contradict the codified text for several
  cases); MOHCD's own published schedules were not fetched.

## 2026-09-11 — Part 3 (regulatory timeline): done

- **Produced:** `audit_state_laws.py` (`fetch`, `claims`, `probe`, `build`, `report`); memo
  `output/planning_commission_project/regulatory_timeline/regulatory_timeline.tex` (19 pp,
  0 undefined, 0 overfull); `law_inventory.csv` (45 laws: 29 California statutes and code
  sections, 16 SF ordinances) and `law_claims.csv` (156 claims, all verified) beside it;
  `external/laws/` (checked claims; quarterly docket composition, hearings per residential case,
  permit-side linked share, record and permit first-stage series).
- **Effective dates:** the Constitution's rule is quoted, not computed (reading "date of
  enactment" as the Governor's signature would put October bills a year late, which the
  history notes contradict). 5 statutes' dates come from leginfo history notes; 18 are
  inferred from the notes' uniform pattern (January 1 after the approval year) and marked.
- **Sources found:** the Board's ordinance lists moved to sf.gov in 2026 (the archive copy stops
  at Ord. 52-26); Legistar's web API for SF carries nothing after 2020. The 2026 list on sf.gov
  runs to Ord. 133-26 (effective 2026-08-23).
- **The July 2026 25-unit ordinance:** not found. No 2026 ordinance title mentions the
  inclusionary program or 25 units; the row stays in the inventory, unverified.
- **Correction to the brief:** AB 1763 is a density-bonus bill (its title), not an HAA amendment.
- **Not done:** the Priority Equity Geographies polygon (needed for the 2024 Housing Production
  ordinances' treated set) is not joined; RHNA due dates (HCD schedule) not read.

## 2026-09-11 — Part 4 (outcome chain): done

- **Produced:** `analyze_permit_content.py outcomes` (reads `clocks.parquet`; writes
  `external/clocks/{outcomes.csv,outcomes_units.parquet}`, `clocks/tables/clk_outcomes.tex`,
  `outcomes_macros.tex`, `figures/fig_outcomes.pdf`); new section "The outcome chain" in the
  clocks memo (now 18 pp, 0 undefined, 0 overfull) rather than a new memo, since its cuts are
  the clocks memo's (track × parcel-value quintile).
- **Definitions:** housing projects = principal permit adds ≥ 1 unit; entitlement = the
  Commission's first approving action (ministerial: filing); completion = Housing Production's
  first completion on the principal permit, else the unit-completion table's first certificate,
  else DBI's completion date; exits (withdrawn/expired/cancelled) are a competing event
  (Aalen–Johansen cumulative incidence, implemented in the script), not a censoring.
- **Caveats stated in the memo:** unit shares are lower bounds (completions recorded under
  other permit numbers are missed); DBI's first construction document covers under half the
  mature projects, so that link is shown only as a share. The landmark comparison the brief
  allowed here is already in the permits memo (Part 0, item 3) and was not repeated.

## 2026-09-11 — Part 5 (parcel-year envelope): done

- **Produced:** `acquire_external_data.py envelope` (claims, panel, sensitivity, report);
  `external/zoning/parcel_envelope.parquet` (5,824,609 parcel-years: height, alternative height,
  bulk, map-lot lot area, density rule, units by density / by envelope / maximum, binding
  constraint, state density-bonus uplift) and `envelope_sensitivity.csv`;
  `data_acquisition/envelope_sources.csv` (25 claims, all verified); a new section "The
  feasible envelope" in the data-acquisition memo (36 pp, 0 undefined, 0 overfull).
- **Rules:** zoning control tables 209.1–209.4, 210.1–210.3, 710–713, 750, 840–843, 827, 829
  as captured 2021; the Government Code's 50% density-bonus ceiling and five-unit threshold. A
  district's rule is held at that reading for every year; named NCDs, redevelopment areas, P,
  M and pre-2008 SoMa districts are unread and left blank (7% of map lots in 2026).
- **Correction during the build:** condominium unit parcels carry no lot area and were first
  counted once per unit; lot area is now read at the map lot and totals count map lots once.
- **Sensitivity (2026 total):** 687,630–937,959 units across floor height, unit size, rear-yard
  depth and setback-loss runs (central 795,840).

## 2026-09-11 — Part 7 (records request): done — nothing sent

- **Produced:** `collect_conditions.py records`;
  `output/planning_commission_project/records_request/records_request.md` (a letter to the
  Planning Department's custodian of records, the search statement, and a separate set of
  questions for the Assessor-Recorder on NSRs) with `pre2010_with_motion_number.csv` (1,032
  items) and `pre2010_without_motion_number.csv` (554). Placeholders are left for addresses,
  contact details and a cost ceiling.
- **Counts differ from the brief's estimates** (≈1,040 and ≈590; 50,366 addresses): the completed
  census gives 1,032 and 554, and the probe tried 127,920 addresses (10,165 documents). The
  letter uses the census figures and a note to Dan says so.
- **Citations:** Gov. Code § 7920.000 (the Act's name) and § 7922.535 (the 10-day determination)
  quoted from leginfo and checked.

## 2026-09-11 — Part A (project chronicle): done

- **Produced:** `build_chronicle.py`; memo
  `output/planning_commission_project/project_chronicle/project_chronicle.tex` (15 pp, 0
  undefined, 0 overfull): the question; a timeline of 13 memos with what later work revised in
  each; a table of 21 supersessions whose old and new figures are read by regex from
  `progress_log.md`, `CLAUDE.md`, the brief and this log; a walk through every memo and this
  batch's other products; what the data say so far; the data inventory (row counts and year
  spans computed from the objects); the model as specified; the primitive → moment → dataset
  map; the open register; next steps.
- **Every number** comes from a memo's own macros file (input directly; the script refuses to
  run on a macro-name clash) or is computed by the script. Written last, as the brief asks, so
  it reads the other memos' final macros.
- **Page budget:** the brief asks for two to four pages per memo and 15–25 pages in all; with
  13 memos plus this batch's products the walk runs to roughly a page each, with detail pushed
  back to the memos, which is what the total allows.
- **Not summarised:** the `dr_supply_model` blueprint the brief names is not in the repository.

## 2026-09-11 — housekeeping at the end of the batch

- `conditions_of_approval` correction macros refreshed from the completed census (they still
  carried pre-round-3 figures: 7.5 → 7.6%, 1,601 → 1,600, 1,587 → 1,586); memo recompiled.
- `STRUCTURE.md`, `output/planning_commission_project/README.md`, the standing `memo/memo.tex`
  and `external/README.md` (and its generator in `acquire_external_data.py`) list the new
  scripts, memos and data. Project memory updated (parser now round 3; primary-source traps).
- **Deliverables checklist:** conditions memo regenerated with Part 0 fixes and out-of-sample
  figures — done; permits memo landmark comparison and cost-placeholder fix — done;
  data-acquisition DR clock note — done; chronicle — done; exactions memo, pipeline and panel
  — done (open questions stated); clocks memo — done; regulatory timeline, pipeline and law
  inventory — done; outcome chain — done (clocks memo section); parcel-year envelope — done
  (data-acquisition memo section); `conditions_numeric.parquet` and its pages — done;
  records request and CSVs — done, not sent; this log — kept throughout.
- **Nothing committed.** All changes are in the working tree for review.
