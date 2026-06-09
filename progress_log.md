# Progress Log

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
