# Progress Log

## 2026-06-18 — Finish the SF minutes pipeline: modern-era parsing, corpus repair, corpus-wide extraction, label-QA, and the SF coding manual

**Goal**: Push the (Guren-approved) SF Planning Commission minutes pipeline toward "A+": parse the unparsed modern era, get extraction running corpus-wide with quality checks, tee up the existing-label fixes, and write the SF-specific labeling rules.

**What was done**:
- **Modern-era parser** (`parse_modern_minutes.py`): pdfplumber for 2018+ PDFs, direct read for 2015–17 text → the same `<<Project>>` tagged blocks; date-led ISO filenames; hardened `ingest.py`'s filename date-parser for them. Parsed 2015–2026 → ~325 meetings.
- **Corpus repair**: found **44 corrupt modern PDFs** (not `%PDF`). Added `scrape_minutes.py --repair` + `%PDF` validation on every download, and broadened the archive harvester to key on anchor text "Minutes" across all three hosts (was seeing 129 of 295 minutes links). Repaired all 44, re-parsed + re-ingested. Corpus now complete **1998–2026 with raw↔tagged parity; labels.db = 16,100 items** (9,081 HTML + 7,019 modern).
- **Corpus-wide extraction** (`run_extraction.py`, supersedes the single-file/old-schema `inference.py`): pluggable engine (heuristic/hf/anthropic), schema-aligned via `extraction_common`, resumable, with **periodic QA checks** (coverage, distributions, accuracy-vs-gold). Ran the **heuristic v0 over all 16,100 blocks (~11s)** → `structured_data.jsonl` + `extracted_results.csv` + `extraction_qa_report.md`.
- **Learning-curve harness** (`learning_curve.py`) + refactored `train.py` into importable functions it reuses; smoke-tested with a real flan-t5-small fine-tune.
- **Label-QA gate** (`label_qa.py`): diffs each hand-label vs its source block; `--apply --backfill` ran on the real DB — **backfilled 405 labels, flagged 412 for confirmation, action='other' 351→76**, DB backed up.
- **Rare-class-first + year-balanced labeling queue** (`labeling_app/queue_order.py` + `app.py` + UI order selector); fixes the old chrono+LIMIT-5000 bug. Verified first 29 queued items span one-per-year 1998→2026.
- **SF coding manual** (`output/planning_commission_project/labeling_rules.md`): authoritative field-by-field SF rules (case-suffix→request_type map, the `action` disposition vocabulary incl. DR-specific, stance markers, recurring-case handling), cross-linked from the app README and review guide; **[QA]**-tagged rules map to `label_qa.py`.
- Installed `pdfplumber` + `flask`; pinned the Flask stack in `requirements.lock.txt` (flask already in `requirements.txt`).

**Key decisions / findings**:
- Model-based extraction is a *downstream* step (needs a trained model + clean labels); ran the free, schema-correct **heuristic as v0** now, with the same runner ready to swap to T5/Anthropic via one flag.
- Heuristic floor vs gold: copy fields strong (case#/request_type 100%, assessor_block 95%, height 88%, address 70%); judgment/roll-call/free-text weak — but `action`/`noes`/`vote` numbers are depressed by *dirty gold* (pre-backfill) and exact-match scoring, not just the engine.
- Root cause of the 44 corrupt files: the harvester's host-specific regex missed older multi-host minutes links; fixed by anchor-text matching → self-healing scraper.
- Label target guidance unchanged: ~600 well-balanced labels (oversample rare classes, label the modern era); use the learning curve to find the plateau.

**Next steps**:
- Human-confirm the 412 flagged labels in the app (label to `labeling_rules.md`); copy fields are auto-trustable, focus eyes on action/votes + the 76 residual `other`.
- Re-run `run_extraction.py` after confirmation → trustworthy accuracy baseline.
- Then: export → `learning_curve.py` → train T5 → `run_extraction.py --engine hf` and compare to the heuristic floor. (Optional: patch heuristic modern `action` parsing; set up Anthropic for an LLM pass.)
- Reconcile minor PDF-stack lock drift (pdfplumber 0.11.9→0.11.10, pypdfium2, pdfminer) from this session's install if a full re-lock is wanted.

**Files touched**:
- `code/commission_minutes_processing/parse_modern_minutes.py` — created (2015+ parser)
- `code/commission_minutes_processing/run_extraction.py` — created (corpus-wide extraction + QA)
- `code/commission_minutes_processing/learning_curve.py` — created (how-many-labels curve)
- `code/commission_minutes_processing/label_qa.py` — created (label linter + backfill)
- `code/commission_minutes_processing/labeling_app/queue_order.py` — created (priority queue)
- `code/commission_minutes_processing/train.py` — modified (refactored into importable functions)
- `code/commission_minutes_processing/minutes_scraping/scrape_minutes.py` — modified (`--repair`, `%PDF` validation, anchor-text harvester)
- `code/commission_minutes_processing/labeling_app/{ingest.py,app.py,templates/index.html,static/app.js,static/style.css}` — modified (ISO dates; priority order + rare badge)
- `output/planning_commission_project/labeling_rules.md` — created (SF coding manual)
- `output/planning_commission_project/hand_label_review_guide.md`, `STRUCTURE.md`, `code/commission_minutes_processing/{minutes_scraping/README.md,labeling_app/README.md}` — modified (docs/cross-links)
- `requirements.lock.txt` — modified (Flask stack pins)
- Dropbox `…/san_francisco/{tagged/2015..2026, processed/structured_data.jsonl, extracted_results.csv, extraction_qa_report.md}` — created/updated (out of git); `labeling_app/labels.db` — backfilled (+ `.qa.bak` backup)
- memory `sf-minutes-pipeline-to-a-plus.md` — created/updated

## 2026-06-16 — Layer I demand-data pipeline (build + CoreLogic + crime), report, and Guren slides

**Goal**: Execute the demand-side data-collection brief — build, run, and document the full region-wide (9-county Bay Area) Layer I data pipeline — then a chain of follow-ups (gSSURGO spatial, 511 transit, CoreLogic price spine, crime, PUMS weights), plus a 10-slide deck for the Adam Guren meeting and a demand-estimation roadmap. (Session spanned 06-12→06-16.)

**What was done**:
- Built `demand_estimation/` from scratch: collectors (TIGER, LODES, ACS PUMS+tables, SSURGO tabular+spatial, CGS hazard, Gov-OPR zoning, CPAD/CDE/GTFS amenities, IRS migration, crime), `build.py` orchestration (BG↔jurisdiction crosswalk, job access, soil extract + BG areal aggregation, controls, PUMS, design matrix), manifest/paths/ArcGIS-pager/manual-stubs. ~1.5 GB pulled to Dropbox `data/demand/`, idempotent + sha256-checksummed.
- Follow-ups: gSSURGO spatial polygons via SDA WKT → `bg_soil_engineering.parquet` (5,184 BGs); 511 regional GTFS all-operator transit (4,590 BGs; fixed dead Caltrain feed → Trillium mirror + zip validation).
- CoreLogic/Cotality: wrote the Redivis SQL filter queries; merged the user's extracts (5.3M deeds + 2.28M parcels) → geocoded to BG (100% parcel match, 99.9% BG) via `corelogic.py`.
- Crime: SF + Oakland incident-level (Oakland geocoded via Census batch geocoder) + FBI CDE county-level for all 9 counties (api.data.gov key) → `bg_crime.parquet`, merged into controls.
- PUMS survey weights (WGTP/PWGTP) added → population-correct moments; fixed corelogic stub README provenance (BU → Stanford Redivis).
- `demand_data_report.tex` (18 pp, compiles clean): inventory, granularity table, per-source detail w/ integrated diagnostics + spatial choropleths, CoreLogic section, instrument-pipeline status, **new §8 demand-estimation roadmap**.
- `output/political_economic_housing_model/guren_meeting_slides.tex` — 10-slide Beamer deck (fiscal-federalism framing, two margins, reaction function, identification routed to Wollmann).

**Key decisions / findings**:
- Demand data is region-wide (data root), reusing the minutes pipeline's `paths.py` `DATA_ROOT`; not per-locality.
- Soil instrument has spatial variation independent of the price/income gradient (good for identification); SSURGO survey areas discovered at runtime (not all FIPS-aligned).
- CoreLogic "Property" = tax-assessor + characteristics (there is no separate Tax dataset); LLMA walled off by EULA. Census batch geocoder is flaky → hardened with small batches + retries.
- Data validated sane: CoreLogic median-price-by-year reproduces the Bay Area cycle; FBI crime rankings as expected; IRS shows post-COVID out-migration.
- **Demand is NOT estimated** — only the data/design-matrix is assembled (stated explicitly in report §8); user will estimate by hand.

**Next steps**:
- Estimate demand by hand per report §8: define product/market/shares → hedonic price/user-cost index → conditional logit → BLP, instrument price (soil + slope + Gandhi–Houde).
- RS Means cost schedule (the one manual blocker) → soil instrument's $ form; FRED 30-yr rate for owner user cost.
- Build the multi-jurisdiction τ_j (discretionary-review) panel + z̄_j integration for the Layer III regulation game (only SF minutes processed so far).

**Files touched**:
- `demand_estimation/` — created (collectors/, `build.py`, `corelogic.py`, `crime.py`, `manifest.py`, `demand_paths.py`, `util.py`, `arcgis.py`, `stubs.py`, `report/`)
- `demand_estimation/report/demand_data_report.tex` (+ stats/inventory/fill `.py` helpers, `figures/`, `.tex` fragments) — created (18-pp report)
- `output/political_economic_housing_model/guren_meeting_slides.tex` — created (10-slide deck)
- `STRUCTURE.md`, `requirements.txt`, `requirements.lock.txt`, `environment-notes.md`, `.gitignore` — modified (deps/docs/ignore for the demand pipeline)
- Dropbox `data/demand/` — created (~1.5 GB; out of git)

## 2026-06-09 — Multi-jurisdiction reconnaissance: census, 6 probes, and consolidated DATA_STATUS

**Goal**: Scope scaling the SF minutes/zoning pipeline to the whole Bay Area — map every locality's data sources and answer, end-to-end, whether the multi-jurisdiction panel is feasible (access, depth, spatial form, pre-period envelope, HCD treatment) — then consolidate into one authoritative status doc.

**What was done**:
- Restructured the data corpus to be per-locality (`meeting_minutes/<locality>/...`); `paths.py` gained `MFHR_LOCALITY` (default `san_francisco`); updated README/STRUCTURE/docstrings.
- Ran the **minutes platform pilot** (14 priority cities): platform classification, scraper pilot (civic-scraper limits found), and an additive `jurisdiction_mappings.py` synonym layer.
- Deployed a sub-agent for the **zoning-envelope assessment** (NZLUD coverage, code-host table, HCD panel); materialized **77 git-LFS pointer stubs** (~2.9 GB) in Dropbox from the local LFS cache (sha256-verified); ran the **WRLURI cross-check** (NZLUD `zri` rank-inverted vs WRLURI-2006, n=4).
- Built the **Bay Area locality census** (109 localities, 8 parallel per-county agents, GEOIDs from Census Gazetteer).
- Ran six probes: **Akamai/CivicPlus**, **archive-depth** (shallow), **API depth-read**, **zoning-data-form**, **pre-period envelope**, and the **final recon bundle** (CivicPlus depth, migration cliffs, HCD firm-up, pre-period verification).
- Wrote **`output/DATA_STATUS.md`** — the single authoritative status doc; superseded the census report with a redirect. Self-verified all 33 numbers against the CSVs.

**Key decisions / findings**:
- **Akamai "wall" is a myth** — only 3 of 109 sites bot-blocked (Fremont, Portola Valley, San Jose); 25 of 28 CivicPlus clean.
- **Agenda-trap is real and large** — year-dropdowns overstate minutes depth (Oakland 2000→**2014**, Santa Rosa 1999→**2016**); only minutes-type-verified years trusted.
- **~2016 panel is feasible**: 68/109 verified minutes-start, **36 reach ≤2016** (a floor; 41 `unknown` are JS-gated, not absent). Migration cliffs add deep history (Marin 2005, SCC 2008, San Jose 2005).
- Current zoning maps are scriptable (67/109 downloadable GIS + statewide Gov-OPR backstop). HCD treatment variable is clean/sourced (0–38mo exposure variation).
- Pre-period envelope cheap for **15/25** (corrected down from 17 after killing San Ramon/Daly City false positives). Recon is **complete**; the two remaining blockers are decisions, not data.

**Next steps**:
- Build the unblocked adapters: Granicus/Legistar (41 localities), CivicClerk OData (10), CivicPlus AgendaCenter (26), Municode (44)/CodePublishing (23)/eCode360 (14).
- **Gating quality step**: re-test extraction transfer (SF `autoextract` does NOT transfer) on real docs **with human review**, not unattended.
- Cheap recon finishers: 12 CivicPlus CIDs (browser session), 9 pre-period `none_found` (publisher version history), 8 prohousing dates (HCD tracker XLS).
- **Two open DECISIONS gate the rest**: (1) by-right vs. ministerial definition (the model's hinge → zoning extraction); (2) deep-history/ratchet investment (pre-2010 + San Jose Akamai + physical records).

**Files touched**:
- `code/commission_minutes_processing/paths.py`, `README.md`, `STRUCTURE.md`, `parse_sf_meeting_minutes.py`, `labeling_app/ingest.py` — modified (per-locality restructure)
- `output/DATA_STATUS.md` — created (authoritative consolidated status; supersedes census report)
- `output/minutes_platform_pilot/`, `zoning_envelope_project/`, `bay_area_census/`, `archive_depth_probe/`, `zoning_map_form_probe/`, `preperiod_envelope_probe/`, `hcd_preemption_panel/`, `migration_cliffs_probe/`, `civicplus_depth_probe/` — created (probe CSVs, reports, reproducible scripts, raw per-county provenance)
- Dropbox `data/raw,clean,crosswalks,llm_regulatory_measurement/*` — 77 LFS stubs materialized to real data (not in git)
- memory `corpus-moved-to-dropbox.md` — updated (per-locality layout note)

## 2026-06-08 — Data migration to Dropbox, schema enrichment, hand-label review setup

**Goal**: Move the data corpus off the over-quota Google Drive to Dropbox without losing files; settle the extraction schema before the full relabel; and tee up a review of all hand-labeling so far.

**What was done**:
- Migrated the entire `market-for-housing-regulation` corpus (~18 GB, 2219 data files + `_archive/`) from Google Drive → Dropbox via rsync; verified byte-identical (matching file counts, byte totals, and empty `rsync -anc` checksum diff), then deleted the Google Drive source.
- Ran the schema-enrichment investigation (`.claude/instructions/schema_enrichment_investigation.md`) via a sub-agent against the real corpus: adopted 1 field, deferred 3, rejected 1.
- Applied the adopted changes to `extraction_common.py` and synced `data_infrastructure.md` (now 36 fields).
- Repointed `paths.py` from the dead Google Drive path to Dropbox; verified it resolves.
- Wrote `hand_label_review_guide.md` (app usage + 36-field dictionary + 2 worked examples) for reviewing the 319 hand-labels before they become ground truth.
- Created `output/_archive/`, moved both `proposal/` folders, `minutes_data_sources.docx`, and loose LaTeX build artifacts into it; added LaTeX `.gitignore` rules.

**Key decisions / findings**:
- Copy→verify→delete sequence used deliberately (no in-place `mv` across cloud mounts) so nothing could be lost mid-transfer.
- Schema verdicts: `staff_planner` **ADOPT** (present 81% HTML / 96% modern); `stories/height`, `discretion_trigger`, `units_affordable` **DEFER**; `appeal_status` **REJECT** (lives in the Board of Supervisors corpus, not minutes).
- Added a derived `vote` (computed in `coerce_record()` from ayes−noes when blank) rather than a hand-labeled field.
- Hand-labeling reality: 319 records (1998–2014) live in `tagged/training/{year}_labeled.json`; they appear in the labeling app under the `prelabeled` status filter (~414 rows, since some cases recur across meetings).
- Schema is still "one-list edit propagates" — confirmed form/prompt/required-keys/metric all derive from `SCHEMA`.

**Next steps**:
- Empty Google Drive **web Trash** to actually reclaim the 15 GB quota (local deletion done; Trash still counts until purged).
- Review all `prelabeled` items in the labeling app (per `hand_label_review_guide.md`), mark `done`, then export → rebuild `training.txt`.
- Optional: `git rm --cached` any previously-tracked build artifacts now ignored; commit the migration/schema/tidy changes.

**Files touched**:
- `code/commission_minutes_processing/extraction_common.py` — modified (added `staff_planner` to SCHEMA; derived `vote` in `coerce_record()`)
- `code/commission_minutes_processing/paths.py` — modified (default data root → Dropbox)
- `output/planning_commission_project/data_infrastructure.md` — modified (36 fields; `staff_planner`, `vote` note, examples)
- `output/planning_commission_project/hand_label_review_guide.md` — created
- `output/planning_commission_project/schema_enrichment_recommendation.md` — created (by sub-agent)
- `output/_archive/**` — created (moved: both `proposal/` dirs, `minutes_data_sources.docx`, loose `toy_model.*` build artifacts)
- `.gitignore` — modified (LaTeX build-artifact rules; Dropbox comment)
- `progress_log.md` — created (this log)
