> ## ARCHIVED — 2026-08-30
>
> This is the read-only audit of `output/` as it stood **before** the 2026-08-30
> reorganization. Its `§4 Proposed reorganization` was executed (with two changes: the raw
> source CSVs moved to `output/bay_area_recon/_source_data/` rather than to Dropbox, and
> `.DS_Store` was left tracked), so **every path in this file below this banner is stale**.
> For the current layout see `output/README.md` and `output/bay_area_recon/README.md`; for
> what each directory contains see its own `memo.tex`. Kept because §2 and §5 record
> findings that live nowhere else — notably that the 14-row and 25-row HCD panels are *not*
> redundant, and that neither memo PDF has a recoverable `.tex` source.

# `output/` Inventory & Cleanup Proposal

> **Snapshot, 2026-08-29.** This inventory describes `output/` as it stood before the
> 2026-08-30 cleanup. Since then `DATA_STATUS.md`, `final_recon_bundle_report.md`, and
> `planning_commission_project/graphics/` have been deleted (recoverable from git history),
> and each subdirectory now carries its own `memo.tex` describing what it does and what it
> found. Those memos, not this file, are the current description of the directory.

*Read-only audit produced 2026-08-30. **Nothing was moved, renamed, deleted, or edited** —
this file is the only thing created. Every `git mv` below is a proposal awaiting approval.*

`output/` currently holds **12 top-level subfolders** and **4 loose files** (plus a tracked
`.DS_Store`), spanning **four research lines**. The good news first: **nothing here belongs to a
foreign project.** There is no data-center work, no course/thesis material, no stray client work.
Every file is about the market for housing regulation.

The mess is real but it is a *grouping* problem, not a *contamination* problem, and it has four
causes:

1. **Nine one-shot Bay Area recon probe folders sit as top-level siblings** of the two long-lived
   project folders, so the directory reads as 12 equals when it is really 2 projects + 1 archive +
   9 sub-artifacts of a single June 2026 reconnaissance sprint.
2. **Two orphan memo PDFs** (`demand_memo.pdf`, `operationalization_memo.pdf`) sit loose at the top
   with no folder, no `.tex` source, and no reference from any other document.
3. **Raw third-party source data lives in `output/`** (`nzlud_muni.csv`, 2,639×77, 827 KB;
   `hcd.csv`, 539 rows) — by the repo's own rule, `output/` is for reports, memos, figures, and
   small derived tables; bulk source data belongs at the Dropbox data root.
4. **Five probe reports are superseded but carry no redirect banner**, unlike the precedent set by
   `bay_area_locality_census_report.md` → `DATA_STATUS.md`.

**Note on dates.** Every file's mtime is `2026-06-28` (the corpus/repo was re-cloned onto the current
machine that day), so filesystem mtimes carry **no** information here. All "last updated" values
below come from `git log -1 -- <path>`. The single exception is
`planning_commission_project/labeling_rules.md` (mtime 2026-08-03, currently **modified in the
working tree**).

---

## 1. Summary table

| Subfolder / file | Project line | What it is (one sentence) | Last updated (git) | Status |
|---|---|---|---|---|
| `planning_commission_project/` | SF minutes pipeline | The five living spec/reference docs for the SF Planning Commission minutes dataset — coding manual, schema, data-availability inventory, code review, schema-enrichment decision — plus `graphics/`. | 2026-07-31 (`629597e`) | **Current** (actively edited; `labeling_rules.md` has uncommitted changes) |
| `planning_commission_project/graphics/` | SF minutes pipeline | Three motivation screenshots (8.7 MB PNGs) used only by the research proposal that has since moved to `_archive/`. | 2026-06-08 (`7226ffb`) | **Orphaned** — consumer is in `_archive/` |
| `political_economic_housing_model/` | Theory / toy model | `toy_model.tex`+`.pdf` (v5, "Fragmented Housing Regulation as a Fiscal-Federalism Breakdown") and the 10-slide Beamer deck for the Guren meeting. | 2026-06-16 (`08b20b2`) | **Current** |
| `bay_area_census/` | Bay Area recon | The 109-locality census of every nine-county ABAG land-use regulator — minutes platform, zoning source, access barrier, solution class — plus the Akamai/CivicPlus finding that disproved the block premise. | 2026-06-09 (`14c2fce`) | CSV **current** (authoritative per `DATA_STATUS.md` §7); its report **superseded** (banner present) |
| `archive_depth_probe/` | Bay Area recon | Verified earliest-*minutes* year per locality (`archive_depth.csv`, 109 rows) plus the API depth-read that exposed the "agenda trap" (Oakland 2000→2014, Santa Rosa 1999→2016). | 2026-06-09 (`14c2fce`) | CSVs **current**; `archive_depth_report.md` **superseded in part** (banner present); `archive_depth_api_report.md` **superseded** (no banner) |
| `zoning_map_form_probe/` | Bay Area recon | Form of the *current* spatial zoning data for all 109 localities (67 downloadable GIS layers, 28 viewer-only, 13 PDF-only) + the CA Statewide Zoning layer backstop. | 2026-06-09 (`14c2fce`) | CSV **current**; report **superseded** (no banner) |
| `preperiod_envelope_probe/` | Bay Area recon | Wayback-CDX search for a datable ~2016 pre-period zoning envelope across the 25-locality estimation sample; 15/25 usable after false positives were killed. | 2026-06-09 (`14c2fce`) | CSV **current**; report **stale/superseded** — it still says 17/25, corrected to 15/25 in `final_recon_bundle_report.md` (no banner) |
| `hcd_preemption_panel/` | Bay Area recon | The **treatment variable**: HCD builder's-remedy exposure (0–38.4 months) for the 25 estimation-sample localities, built from the authoritative HCD compliance dataset. No report — narrative lives in `final_recon_bundle_report.md` §3. | 2026-06-09 (`14c2fce`) | **Current** (load-bearing) |
| `migration_cliffs_probe/` | Bay Area recon | Three-row CSV showing the ~2024 portal migrations were domain changes, not data loss (Marin Co. → 2005, Santa Clara Co. → 2008, San Jose → 2005). No report. | 2026-06-09 (`14c2fce`) | **Current** |
| `civicplus_depth_probe/` | Bay Area recon | CivicPlus AgendaCenter depth read: method proven (Campbell → 2006) but 12 of 14 are JS-gated behind CivicEngage. No report. | 2026-06-09 (`14c2fce`) | **Current** but incomplete (12 open) |
| `minutes_platform_pilot/` | Bay Area recon | The *earlier* 14-city priority-submarket pilot — platform classification + a 3-city scraper pilot (Daly City, San Jose, Fremont). Source of the load-bearing "SF `autoextract` does not transfer cross-jurisdiction" finding. | 2026-06-09 (`14c2fce`) | **Superseded in coverage** by the 109-locality census; **still the only source** for the extraction-transfer finding |
| `zoning_envelope_project/` | Bay Area recon | The 14-city by-right-envelope assessment (NZLUD coverage 4/14, code-host classification, a 14-city HCD exposure panel) plus two **raw upstream source files** (`nzlud_muni.csv`, `hcd.csv`). | 2026-06-09 (`14c2fce`) | Assessment **superseded in coverage**; raw CSVs **current inputs** (cited by `DATA_STATUS.md` §7); **misfiled** (bulk source data in `output/`) |
| `_archive/` | Mixed (2 lines) | Correctly-functioning archive: the two superseded research proposals (one per line) + the `minutes_data_sources.docx` that `minutes_data_availability.md` replaced. | 2026-06-08 (`7226ffb`) | **Current archive** — working as intended |
| `DATA_STATUS.md` | Bay Area recon | The authoritative consolidated status doc; folds in all six probes and re-derives every number from the CSVs. Explicitly supersedes the census report and the per-probe reports. | 2026-06-09 (`14c2fce`) | **Current — authoritative** |
| `final_recon_bundle_report.md` | Bay Area recon | Findings of the final four-task recon bundle (CivicPlus depth fill, migration cliffs, HCD firm-up, pre-period verification); the source of the 17→15 pre-period correction. | 2026-06-09 (`14c2fce`) | **Current** (companion detail to `DATA_STATUS.md`) |
| `demand_memo.pdf` | **Demand estimation** | "Estimating Housing Demand… Layer I: A Random-Coefficients Sorting Model with Tenure Choice" — the BLP/Bayer sorting spec, the soil cost-shifter instrument, and the judgment calls. | 2026-06-16 (`08b20b2`) | **Current but misfiled** — the only `output/` item from the demand line, and its sibling report already lives in `demand_estimation/report/` |
| `operationalization_memo.pdf` | Theory / toy model | "Operationalizing the Market for Housing Regulation" — the three-layer estimation blueprint (BLP demand / regulation game / spatial-equilibrium wrapper) that operationalizes toy model v5. | 2026-06-16 (`08b20b2`) | **Current but misplaced** — loose at top level; belongs beside `toy_model.tex` |
| `.DS_Store` | — | macOS Finder metadata, **tracked in git** despite the `.gitignore` rule (added before the rule existed); currently shows as modified. | — | **Should not be tracked** |

---

## 2. Per-subfolder description

### `planning_commission_project/` — SF minutes pipeline (CURRENT, the live project)
Five reference documents that the pipeline code cross-links to by path. `labeling_rules.md` is the
SF-specific coding manual (the spec hand-labels are graded against, with `[QA]`-tagged rules that
`label_qa.py` enforces); `data_infrastructure.md` documents the 36-field canonical schema and the
scrape→parse→label→train flow; `minutes_data_availability.md` is the era-by-era inventory of the
1,099 raw files; `processing_review.md` is the 2026-06-05 code review + hand-label audit (with a
2026-06-08 status header marking which findings were actioned); `schema_enrichment_recommendation.md`
is the ADOPT/DEFER/REJECT decision memo on five candidate fields. This folder is **referenced by five
code files** and by `README.md`, `STRUCTURE.md`, and `labeling_app/README.md` — it is the most
link-entangled folder in `output/`. **Do not rename it.**

### `planning_commission_project/graphics/` — ORPHANED (8.7 MB)
Three PNGs (`motivation_screenshot_1/2.png`, `prop_k_statement_1.png`). Their only consumer is
`_archive/planning_commission_project/proposal/research_proposal.tex`, whose `\graphicspath` points at
the hard-coded absolute path `/Users/danpost/housing_project/output/planning_commission_project/graphics/`
— an **old machine and an old repo name**, so that link is already broken regardless. No current
document references these images. They are 8.7 MB of the folder's 8.4 MB footprint.

### `political_economic_housing_model/` — Theory / toy model (CURRENT)
`toy_model.tex` (288 lines, v5) formalizes fragmented housing regulation as a fiscal-federalism
breakdown: the homevoter chooses on two margins (by-right envelope z̄ⱼ, discretionary tax τⱼ), the
fragmentation externality generates a reaction function, and state preemption leaks. Plus
`guren_meeting_slides.tex/.pdf` (10-slide Beamer deck for the June 2026 Adam Guren meeting). Two
Beamer build artifacts (`.nav`, `.snm`) are committed but are not in `.gitignore`.

### `bay_area_census/` — Bay Area recon, the frame (CSV CURRENT, report SUPERSEDED)
`bay_area_locality_census.csv` (109 rows × 20 cols) is the frame every other probe joins to on
`fips_geoid`, and remains authoritative per `DATA_STATUS.md` §7. Its report carries a correct
`⚠️ SUPERSEDED` banner redirecting to `DATA_STATUS.md` — **this is the precedent the other probe
reports should follow.** `akamai_civicplus_probe_finding.md` is the separate probe that disproved the
"CivicPlus is Akamai-walled" premise (25 of 28 clean; only Fremont + Portola Valley blocked). Also
holds `ca_place_geoid.json`, per-county `raw/*.json` provenance, and one sample Saratoga minutes PDF
(the retrieval proof-of-work).

### `archive_depth_probe/` — Bay Area recon, the time dimension (CSVs CURRENT)
Two generations of the same question. The shallow probe (`archive_depth_report.md`, banner present)
read year dropdowns; the API probe (`archive_depth_api_report.md`, no banner) queried Legistar
webapi / CivicClerk OData / PrimeGov / Granicus-RSS for actual Minutes-type files and **corrected the
"agenda trap"** — dropdown floors span agendas, not minutes. `archive_depth.csv` (109 rows) is the
post-merge authority: 68 verified, 36 reaching ≤2016, 41 `unknown` (a method limit, not proven
absence). Three `.py` scripts reproduce it; `consolidate.py` and `assemble_api.py` read the census CSV
via `HERE.parent / "bay_area_census"`.

### `zoning_map_form_probe/` — Bay Area recon, spatial (CSV CURRENT, report SUPERSEDED)
Classified the *current* zoning-map data form for all 109: 67 downloadable GIS layers (55 native /
12 statewide-fallback), 28 viewer-only, 13 PDF-only, 1 none. Headline: the CA Statewide Zoning layer
(Gov-OPR) covers ~535/539 CA jurisdictions, so the address→district join is a script, not RA-hours.
Also caught a nice false positive in its own brief — "20 rows already mention GIS" was the substring
`gis` inside "Le**gis**tar".

### `preperiod_envelope_probe/` — Bay Area recon, the ~2016 envelope (CSV CURRENT, report STALE)
Wayback CDX search for a datable 2014–2018 capture of each estimation-sample locality's code page or
zoning-map PDF. **The report is the one genuinely stale narrative in `output/`:** it states 17/25
usable, but `final_recon_bundle_report.md` §4 later killed San Ramon (a rezoning *application* doc) and
Daly City (a General-Plan-Update draft), correcting the count to **15/25**. The CSV was corrected in
place; the report was not, and carries no banner.

### `hcd_preemption_panel/` — Bay Area recon, the TREATMENT VARIABLE (CURRENT, load-bearing)
25 rows, one per estimation-sample locality, with both date conventions retained (HCD-received-adopted
and HCD formal compliance finding) anchored to the ABAG 6th-cycle deadline 2023-01-31, yielding
builder's-remedy exposure of 0–38.4 months. This is the identifying variation for the whole
substitution test. `build_hcd_panel.py` reads `HERE.parent / "zoning_envelope_project" / "hcd.csv"` —
a cross-folder dependency that constrains how these two folders may be moved.

### `migration_cliffs_probe/` & `civicplus_depth_probe/` — Bay Area recon, two small follow-ups
`migration_cliffs.csv` (3 rows) establishes that the ~2024 portal migrations at Marin County, Santa
Clara County, and San Jose were domain/view changes with legacy archives still reachable — these three
big jurisdictions extend the panel backward. `civicplus_depth.csv` (14 rows) proved the AgendaCenter
Search-endpoint depth method but resolved only 2 of 14; the other 12 need a browser session to grab
each committee CID. Neither folder has its own report; both are narrated in
`final_recon_bundle_report.md`. `civicplus_depth_probe/probe.py` line 60 writes to a hard-coded
`/Users/danpost/...` path — **already broken** on this machine, independent of any move.

### `minutes_platform_pilot/` — Bay Area recon, the earliest pass (SUPERSEDED IN COVERAGE)
The original 14-city priority-submarket pilot: platform classification for 14 jurisdictions and an
actual scraper attempt on three (Daly City succeeded via CivicClerk OData; San Jose and Fremont were
Akamai-blocked). Superseded in *coverage* by the 109-locality census, but it remains the **only**
source for a finding `DATA_STATUS.md` §6 treats as gating: SF's `autoextract` heuristics do not
transfer cross-jurisdiction (verified on Daly City — confidently-wrong fields), so the extractor must
be re-validated with a human in the loop before any multi-jurisdiction scale-up. Keep this folder.
`jurisdiction_mappings.py` line 40 computes the repo root as `parent.parent.parent` — **depth-sensitive**.

### `zoning_envelope_project/` — Bay Area recon, by-right envelope (MIXED: assessment superseded, raw data misfiled)
The 14-city NZLUD/code/preemption assessment (29 KB, the longest document in `output/`). Findings:
NZLUD covers only 4 of 14 (a consequence of the WRLURI-2006 sample frame), all 14 code hosts located,
2 of 3 pilot scrapes succeeded. It also contains **two raw upstream source files that do not belong in
`output/`** — `nzlud_muni.csv` (2,639 rows × 77 cols, 827 KB, the full public NZLUD release) and
`hcd.csv` (539 rows, the full HCD compliance report). **Checked:** its `hcd_preemption_exposure_panel.csv`
(14 rows) is **NOT** subsumed by `hcd_preemption_panel/hcd_preemption_panel.csv` (25 rows) — 8
localities (Berkeley, Fremont, Redwood City, Richmond, San Bruno, San Jose, San Mateo, South San
Francisco) appear only in the 14-row file, and the two use different column schemas and different
exposure anchors (2023-02-01 vs 2023-01-31). **Do not delete either.**

### `_archive/` — working as intended
Created 2026-06-08 (`7226ffb`) by moving both `proposal/` directories and `minutes_data_sources.docx`
out of the live folders. The two proposals are genuinely **different documents**, not duplicates:
`planning_commission_project/proposal` is "The Market for Housing Regulation — Application to San
Francisco" (8.2 KB tex, 9.5 MB pdf); `political_economic_housing_model/proposal` is "Building a
Political Economic Model of Housing" (4.0 KB tex, 94 KB pdf). This is the archive precedent the rest of
the cleanup should imitate.

---

## 3. Misfiled / doesn't belong here

**No foreign project was found.** Grepping all of `output/` for data-centre, thesis, coursework,
problem-set, and course-code patterns returned zero hits; every document is about housing regulation.
If the user remembers a distinct project in here, it is either already gone or was never committed.

What *is* misfiled, in descending order of confidence:

1. **`demand_memo.pdf` — wrong line.** It is the only `output/` artifact from the **demand-estimation**
   line, whose home is `demand_estimation/` and whose sibling document (`demand_data_report.pdf`)
   already lives in `demand_estimation/report/`. Nothing in the repo references it.
2. **`operationalization_memo.pdf` — right line, wrong place.** It operationalizes `toy_model.tex`
   (it opens "This note operationalizes the toy model (toy model v5)") and belongs beside it, not
   loose at the top of `output/`. Nothing in the repo references it.
3. **`zoning_envelope_project/nzlud_muni.csv` and `hcd.csv` — raw source data in a reports folder.**
   827 KB + 73 KB of unmodified third-party releases. Per the repo's own convention (data lives on
   Dropbox, `output/` holds reports/figures/small derived tables), these belong under the Dropbox
   data root's `raw/`. The 14-city *subsets* derived from them are legitimately `output/` material.
4. **`planning_commission_project/graphics/` — orphaned by its consumer.** 8.7 MB serving only an
   archived proposal with an already-broken absolute `\graphicspath`.
5. **The nine recon folders as top-level siblings.** Not misfiled by *line*, but flattened: they are
   sub-artifacts of one June 2026 sprint, presented as peers of the two multi-year project folders.
6. **`output/.DS_Store` tracked in git** despite `.gitignore` line 1 (it predates the rule).

**Neither memo has a `.tex` source anywhere in the repo or on disk** — they were committed as PDFs
only in `08b20b2`, and the 2026-06-16 progress-log entry does not list them under "Files touched".
Whatever produced them is outside version control. Worth recovering before either memo needs an edit.

---

## 4. Proposed reorganization

Target shape:

```
output/
├── DATA_STATUS.md                  ← stays at top (authoritative status doc)
├── OUTPUT_INVENTORY.md             ← this file
├── planning_commission_project/    ← unchanged name (5 code files link to it)
├── political_economic_housing_model/
│   └── operationalization_memo.pdf ← moved in
├── bay_area_recon/                 ← NEW: the 9 probe folders + final_recon_bundle_report.md
└── _archive/
    └── planning_commission_project/
        └── graphics/               ← moved in
```

### Group A — obviously safe (no reference breaks, or a single trivial edit)

```bash
# A1. Reunite the demand memo with its own research line. Zero references repo-wide.
git mv output/demand_memo.pdf demand_estimation/report/demand_memo.pdf

# A2. Put the operationalization memo beside the toy model it operationalizes. Zero references.
git mv output/operationalization_memo.pdf \
       output/political_economic_housing_model/operationalization_memo.pdf

# A3. Follow the graphics to the only document that uses them.
git mv output/planning_commission_project/graphics \
       output/_archive/planning_commission_project/graphics

# A4. Untrack the Finder metadata (leaves the files on disk; .gitignore already covers them).
git rm --cached output/.DS_Store code/.DS_Store
```

**Doc edits required by Group A:**
- `STRUCTURE.md` — the `output/` tree block lists `graphics/` under `planning_commission_project/`
  (one line). While there, note that the same block still lists `proposal/` under **both**
  `planning_commission_project/` and `political_economic_housing_model/` — **those two lines have been
  broken since 2026-06-08**, when `7226ffb` moved both `proposal/` dirs into `_archive/`. Fix all
  three in one pass.
- `README.md` — its `output/` tree lists only `planning_commission_project/` and
  `political_economic_housing_model/`; it omits `DATA_STATUS.md`, both memos, and all nine recon
  folders. Not *broken* by A1–A4, but it is the most out-of-date map in the repo. Worth refreshing
  in the same commit.
- `.gitignore` — add `*.nav` and `*.snm` to the LaTeX-artifact block (currently both are committed
  for `guren_meeting_slides`). Optional; does not untrack them by itself.

### Group B — needs the user's judgment

**B1. Group the nine recon folders under `output/bay_area_recon/`.**
This is the change that actually fixes the "12 equals" problem. Moving all nine **together** preserves
every `HERE.parent / "<sibling>"` constant inside the probe scripts, so the Python keeps working:

```bash
mkdir -p output/bay_area_recon
git mv output/bay_area_census          output/bay_area_recon/bay_area_census
git mv output/archive_depth_probe      output/bay_area_recon/archive_depth_probe
git mv output/civicplus_depth_probe    output/bay_area_recon/civicplus_depth_probe
git mv output/migration_cliffs_probe   output/bay_area_recon/migration_cliffs_probe
git mv output/zoning_map_form_probe    output/bay_area_recon/zoning_map_form_probe
git mv output/preperiod_envelope_probe output/bay_area_recon/preperiod_envelope_probe
git mv output/hcd_preemption_panel     output/bay_area_recon/hcd_preemption_panel
git mv output/zoning_envelope_project  output/bay_area_recon/zoning_envelope_project
git mv output/minutes_platform_pilot   output/bay_area_recon/minutes_platform_pilot
git mv output/final_recon_bundle_report.md output/bay_area_recon/final_recon_bundle_report.md
```

Edits this **requires** (a move without them silently breaks documented paths):

| File | What breaks | Fix |
|---|---|---|
| `output/DATA_STATUS.md` | §7 "Artifact index" table — **10 relative paths** (`bay_area_census/…`, `archive_depth_probe/…` ×2, `civicplus_depth_probe/…`, `migration_cliffs_probe/…`, `zoning_map_form_probe/…`, `preperiod_envelope_probe/…`, `hcd_preemption_panel/…`, `zoning_envelope_project/…`) plus the superseded-row reference | prefix each with `bay_area_recon/` |
| `output/bay_area_census/bay_area_locality_census_report.md` | its banner points up to `output/DATA_STATUS.md` (repo-relative, not sibling-relative) | **no change needed** — verify only |
| `output/minutes_platform_pilot/jurisdiction_mappings.py:40` | `Path(__file__).resolve().parent.parent.parent / "code" / …` resolves to the repo root today; one level deeper it resolves to `output/` | add one `.parent` |
| `progress_log.md` | the 2026-06-09 entry lists all nine folders by path in one line | update that line (historical log — the user may prefer to leave it as a record of where things were) |
| `README.md`, `STRUCTURE.md` | neither currently maps the recon folders; if refreshed per Group A, write the new paths | write once |

Scripts confirmed **safe** under B1 because their cross-folder constants move with them:
`archive_depth_probe/consolidate.py:18`, `archive_depth_probe/assemble_api.py:13`,
`zoning_map_form_probe/consolidate.py:18` (all `HERE.parent / "bay_area_census" / …`), and
`hcd_preemption_panel/build_hcd_panel.py:14` (`HERE.parent / "zoning_envelope_project" / "hcd.csv"`).

**B2. Add supersession banners to the five reports that lack them** (edits, not moves — follows the
`bay_area_locality_census_report.md` precedent exactly):

| File | Banner should redirect to | Why |
|---|---|---|
| `preperiod_envelope_probe/preperiod_envelope_report.md` | `final_recon_bundle_report.md` §4 | **highest priority** — its 17/25 headline is factually wrong; the corrected figure is 15/25 |
| `archive_depth_probe/archive_depth_api_report.md` | `DATA_STATUS.md` | its 32-locality ≤2016 count was later raised to 36 |
| `zoning_map_form_probe/zoning_map_form_report.md` | `DATA_STATUS.md` | numbers re-derived in `DATA_STATUS.md` §2 |
| `bay_area_census/akamai_civicplus_probe_finding.md` | `DATA_STATUS.md` | folded into `DATA_STATUS.md` §2 |
| `minutes_platform_pilot/minutes_platform_pilot_report.md` | `DATA_STATUS.md`, **noting the extraction-transfer finding is still live** | superseded in coverage only |

**B3. Move raw upstream source data out of `output/` to the Dropbox data root.**
`nzlud_muni.csv` (827 KB) and `hcd.csv` are unmodified third-party releases, not project output.
Moving them **breaks two scripts and one doc**, so this is a real decision, not a tidy-up:
`hcd_preemption_panel/build_hcd_panel.py:14`, `zoning_envelope_project/wrluri_crosscheck.py`, and the
`DATA_STATUS.md` §7 row that names `zoning_envelope_project/hcd.csv` as authoritative. *My
recommendation: skip this one.* The reproducibility benefit of keeping the exact input beside the
script that consumed it outweighs 900 KB of tidiness, and `wrluri_crosscheck.py:24` already hard-codes
a broken `/Users/danpost/...` Dropbox path — pointing more scripts at the data root makes that class of
breakage more likely, not less.

**B4. Renaming `planning_commission_project/` → e.g. `sf_minutes/`.**
*Recommendation: do not do this.* The folder is referenced by `extraction_common.py`, `label_qa.py`,
`llm_extract.py`, `queue_order.py`, `labeling_app/README.md`, `environment-notes.md`, `README.md`,
`STRUCTURE.md`, and ~8 lines of `progress_log.md`. The clarity gain is cosmetic; the breakage surface
is the largest in the repo. Same reasoning applies to `political_economic_housing_model/`.

### Explicitly NOT proposed
- **No deletions of any kind.** In particular `zoning_envelope_project/hcd_preemption_exposure_panel.csv`
  looks redundant with `hcd_preemption_panel/hcd_preemption_panel.csv` and **is not** (see §2).
- **Not moving the probe `.py` scripts to `code/`.** All 14 resolve their inputs relative to
  `Path(__file__).parent`; separating them from their CSVs would break that and lose the
  script-beside-its-output reproducibility pairing.
- **Not touching `_archive/`** beyond receiving `graphics/`. It is already correct.

---

## 5. Could not determine

- **The original scope of each probe.** Every probe report cites a brief at
  `.claude/instructions/<probe>.md`, but `.claude/` is gitignored and only
  `schema_enrichment_investigation.md` survives on disk. The eight briefs
  (`bay_area_locality_census`, `archive_depth_probe`, `api_depth_read_probe`,
  `zoning_map_form_probe`, `preperiod_envelope_probe`, `minutes_platform_pilot`,
  `akamai_civicplus_probe`, `final_recon_bundle`) are unrecoverable, so I judged each probe's
  completeness from its own report and CSV rather than against its commissioned scope.
- **Whether the 12 unresolved CivicPlus localities and 41 `unknown` archive depths have been chased
  since 2026-06-09.** No `output/` file has been touched on the recon line since; the work may have
  happened elsewhere or not at all.
- **Where the two memo PDFs were authored.** No `.tex`, no `.md`, no build script in the repo or on
  disk, and the progress log does not mention them.
- **Real per-file modification history.** All mtimes are `2026-06-28` (re-clone), so "last updated"
  is git-commit granularity only — for the nine recon folders that is a single commit (`14c2fce`),
  which cannot distinguish which probe was written first within that sprint.
- **Whether `output/.DS_Store` contains meaningful Finder state.** Not inspected; recommended for
  `git rm --cached` regardless.
