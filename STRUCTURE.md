# Repository Structure

`market-for-housing-regulation` structures **SF Planning Commission meeting minutes**
(1998–present) into an item-level dataset of discretionary land-use decisions. The
pipeline: **scrape → parse/tag → label → build → train/extract**, all reading the same
schema so nothing drifts.

Reproducibility: `requirements.txt` / `requirements.lock.txt` / `environment-notes.md`.
The data corpus lives on **Dropbox, out of git** (see "Data" below).

---

## Top level

```
market-for-housing-regulation/
├── README.md
├── STRUCTURE.md                 # this file
├── requirements.txt / .lock     # Python deps (py3.12)
├── environment-notes.md         # interpreter, system deps, flagged issues
├── code/commission_minutes_processing/   # the minutes pipeline (below)
├── demand_estimation/           # Layer I demand-side data collection (below)
├── output/                      # reports & memos: pipeline specs, toy model, Bay Area recon (below)
├── notes/                       # commission_members.{xlsx,docx}
└── (data/ lives on Dropbox — not in repo)
```

## `code/commission_minutes_processing/`

| File | Role |
|---|---|
| `paths.py` | **Where the data lives** — `DATA_ROOT`/`MEETING_MINUTES`, env-overridable via `MFHR_DATA_ROOT`. `MEETING_MINUTES` resolves to the *active locality* (`MFHR_LOCALITY`, default `san_francisco`) so the pipeline scales across the Bay Area. Everything imports paths from here. |
| `extraction_common.py` | **The 29-field `SCHEMA` v2** (single source of truth) → `FIELDS`, `build_prompt()`/`item_suffix()`/`prompt_sha()`, `coerce_record()`, `compare_field()`, `verify_evidence()`. The help strings ARE the prompt: `build_prompt()` generates it from them (audit: `output/planning_commission_project/notes/help_string_audit.md`). |
| `normalize.py` | **The storage layer** — one rule per field, applied to hand labels and model output alike, so gold and prediction are never compared across a formatting difference neither side chose. `iso_date`, `lot_list`, `block_key` (unpadded — `pad_key`/`blklot`/`parcel_keys` pad at query time, digits not token), `clean_name`, `address_core`, `request_for_clause`/`descr_proposal`, `normalize_record`. Self-testing: `python normalize.py`. |
| `provenance.py` | `extraction_runs` / `predictions` / `verification_failures` in `labels.db`: which run, model, prompt SHA, schema version and gold version produced any value. Append, never overwrite. Also the `gold_version` registry (`bakeoff/gold_versions.json`). |
| `review_queue.py` | ONE queue for every kind of re-review — `field_redefined`, `migration_ambiguous`, `adjudication`, `new_item` — field-level and sorted so the same field is worked consecutively. Served by the labelling app. |
| `assign_meeting_dates.py` | **The date stage.** Re-locates every parsed block inside its source page BY CONTENT (fold → sequential search → longest increasing chain) and reads off the meeting header it falls under → `items.meeting_date` + `items.meeting_ordinal`. Never re-derives block boundaries, so a parser change cannot silently misalign dates. Validates every page mechanically (header year, Thursday check, unplaced-block ratio) → `date_assignment_audit.csv`. |
| `meeting_headers.py` | The **meeting-level schema** (10 fields) and its heuristic pre-fill: cuts a ±15-non-blank-line window around a boundary, reads type / time / venue / roll call / staff, and reconciles names corpus-wide (`name_reducer`, which resolves a shared surname only where a real given name evidences the ambiguity). Also `meeting_lookup()`, the item→meeting resolver. |
| `extract_all_meetings.py` | Runs the meeting-level extraction over **every** document → `meetings_all.csv` + the `meetings_all` table in `date_gold.db`. `--score` scores the current rules against the hand-confirmed meetings → `meeting_field_score.json`, which is what `plot_extraction_accuracy.py` reads (no hand-typed accuracy figures). |
| `date_boundary_app/` | Hand-mark where each meeting starts, to build the date gold standard: `app.py` (Flask UI, port 5006, plus `--score` for boundary/positional/block-date agreement — HAND marks only), the meeting-level confirm view, `date_gold.db`, `FROZEN_ROUND*.json`. |
| `draw_validation_sample.py` | Draws a validation round weighted by the measured per-year anomaly rate and pre-marks its boundaries by machine (`source='machine'`, so the scorer excludes them from gold). |
| `migrate_gold_v1_v2.py` | Carries the hand-labelled gold from schema v1 to v2. **Proposes, does not decide**: AUTO where a value derives unambiguously, FLAG into the review queue otherwise. Freezes `bakeoff/gold/gold_v1_snapshot.json` first and reads from it thereafter. |
| `gold_split.py` | The frozen train/test split, stratified by (era, year), with a content SHA. `--verify` reports drift and the `gold_version`; `--refreeze` re-hashes when labels changed but membership did not; `--rebuild` only when the item set changed. |
| `bakeoff_extract.py` / `bakeoff_report.py` / `bakeoff_memo.py` | Method × field comparison (regex vs Claude) over the Batch API, split-aware scoring with accuracy and over-extraction reported separately, and the LaTeX tables for `extraction_method_comparison.tex`. |
| `extract_corpus.py` | **The production corpus pass** — what produced the 16,199-record `corpus_v2_g3` table every memo reads. Batch API in resumable 1,500-item chunks, structured outputs with evidence spans, few-shot from the train half only, → `$MFHR_DATA_ROOT/extraction/<run>/{raw,interim,clean,evidence}/` + `manifest.json`. The three forms are kept SEPARATELY so a normaliser bug is a local re-derive rather than a re-query. |
| `analyze_corpus.py` | The corpus memo, generated: composition, dispositions, delay chains (`chains()`, reused downstream), commissioners, geography, Planning Code citations → `discretionary_review_patterns/{figures,tables}`. |
| `analyze_permits.py` | The permit-linkage memo: parses the four printed permit-number forms out of the request text, matches them against a local cache of DataSF's Building Permits (`i98e-djp9`, 1.3M rows), scores a block+lot fallback against the permit-number subset, and writes `permit_summary.json` next to the extraction so the corpus memo's linkage table cannot drift. |
| `analyze_conditions.py` | The conditions memo: `probe` (is a Commission packet published for this case?), `fetch` (pull it, keep only the motion's Exhibit A), `report` (coverage, a block-text sweep, and a twelve-category taxonomy). Both network stages cache to `$MFHR_DATA_ROOT/external/cpc_packets/`. |
| `analyze_delay.py` | The delay memo: builds the case panel from `chains()`, joins DBI for the supervisor district, and runs OLS / LPM / quantile regressions plus a temporal out-of-sample test. |
| `acquire_external_data.py` | The data-acquisition memo, in five cached stages: `sync` (materialise Dropbox online-only files — an un-materialised directory is invisible to `ls`), `probe` (resolve every DataSF identifier against the live catalogue), `fetch` (cache the parcel layer, assessor roll, historic zoning, planning records, pipeline, Zillow, the Cotality SF slice and the impact-fee registers under `$MFHR_DATA_ROOT/external/`), `spatial` (point-in-polygon → the 1998–2026 parcel-year zoning panel), `report` (join rates, the risk-set funnel, the price surface, figures, tables, and the `external/README.md` provenance table). Establishes the filing-to-hearing clock and the `building_permits` bridge that overturns the conditions memo's non-overlap claim. |
| `build_adjudication.py` | Queues every gold-vs-model disagreement for a three-way verdict (gold right / model right / both wrong) and mirrors it into `review_queue`. Some measured "model error" is gold error. |
| `autoextract.py` | Regex/heuristic best-guess extraction from a raw block (form pre-fill + builder derivations). |
| `minutes_scraping/scrape_minutes.py` | Consolidated, idempotent scraper (S3 HTML 1998–2014; live archive PDFs 2015–present), content-hash manifest so a re-run skips what it has. Deprecates `minutes_scrape_1998_2014.py`, `minutes_scrape_2018_2025.py` and `minutes_scrape_c.py`, which sit beside it unused. |
| `parse_sf_meeting_minutes.py` | Scrape/parse archived **HTML (1998–2014)** → `tagged/{year}/*.txt` blocks + meeting metadata. |
| `parse_modern_minutes.py` | Parse the **modern era (2015–present)** — text (2015–17) and PDF (2018+, via pdfplumber) → the same `<<Project>>`-tagged blocks, date-led filenames, + `processed/modern_meetings_metadata.csv`. Handles the dash case format and the spaced/space-stripped item headers the HTML parser can't. |
| `resplit_blocks.py` / `merge_split_tails.py` | Apply corrected parser boundaries to `labels.db` in place without moving an `item_id`: `resplit` cuts a stored block that holds two agenda items; `merge_split_tails` puts back a tail the splitter cut off at a numbered line that was a date or a list of conditions. |
| `rebuild_review_db.py` | One-time recovery: re-parse the HTML era in memory and re-attach human labels to the clean blocks by content match. Writes `labels.db.rebuilt` and prints what it does NOT carry across. |
| `recover_labels_from_db.py` | Recover hand labels from an older DB by content-matching them onto current blocks. |
| `resolve_speaker_refs.py` | Post-processing: resolve "SPEAKERS: Same as those listed for item 22" to the referenced item's speakers (1,402 items). A cross-reference is not a person, so extraction leaves it empty and this fills it afterwards. |
| `flag_representative_sample.py` | Flag a fixed, seeded per-year sample of unlabelled case-bearing items into the app's queue, so the gold set stops being temporally concentrated. |
| `link_permits.py` | Pilot item↔permit/planning-record linker on the hand-labelled subset. Superseded for corpus scale by `analyze_permits.py`; kept for its Planning-Code section index and the `y673-d69b` join. |
| `datasf_records.py` | DataSF Socrata prototype — the planning-records join source, complementary to the minutes corpus. |
| `plot_meeting_timeseries.py` / `plot_extraction_accuracy.py` | The two meeting-level figures: staffing and commissioner absence over the corpus (banded by inferred presidency), and extraction accuracy across the four validation rounds beside the current computed score. |
| `training_sample_create.py` | Pair labels ↔ blocks → consolidated `tagged/training/training.txt` (JSONL). |
| `migrate_labels.py` | One-time: migrate old `*_labeled.json` into the schema (backs up originals). |
| `train.py` | Fine-tune T5 (`MINUTES_MODEL`/`MINUTES_USE_LORA`/`MINUTES_EPOCHS`); held-out test report. Factored into importable functions reused by `learning_curve.py`. |
| `learning_curve.py` | Fine-tune at increasing label counts vs. a fixed held-out test set → field-accuracy curve (`learning_curve.png`/`curve.csv`/`per_field.csv`); answers "how many labels do I need?". |
| `label_qa.py` | Audit existing labels against their source blocks (continuance mis-coding, dropped `vote`/`noes`/`absent`, 2014 districts, `action='other'`); `--apply --backfill` safely fills recoverable fields and flags items for confirmation. |
| `llm_extract.py` | Few-shot, schema-constrained extraction (HF or Anthropic backend) on the same split. |
| `run_extraction.py` | Local-engine extraction with periodic QA (superseded for the production pass by `extract_corpus.py`). Pluggable engine (`heuristic`/`hf`/`anthropic`), schema-aligned via `extraction_common`, resumable → `processed/structured_data.jsonl` + `extracted_results.csv` + `extraction_qa_report.md` (coverage, distributions, accuracy-vs-gold). Supersedes `inference.py`. |
| `inference.py` | Legacy single-file/old-schema demo (kept for reference; use `run_extraction.py`). |
| `data_collect.py` | Any run's JSONL → a flat CSV (`--jsonl`/`--csv`); renders v2 `speakers` objects and derives the vote tally. |
| `labeling_app/` | Local web app to hand-label items: `ingest.py` (corpus → `labels.db`), `app.py` (Flask UI, port 5005), `queue_order.py` (rare-class-first + year-balanced queue), `templates/` + `static/`, `README.md`. Carries the unified review queue, the speaker row editor with read-only derived counts, era display/filter, and a structured-outputs LLM pre-fill (no model pin). |
| `scratch_code/` | Prototypes (pdfplumber, LoRA variants). |

Run order, **item level**: `scrape_minutes → parse_sf_meeting_minutes (1998–2014) +
parse_modern_minutes (2015–present) → labeling_app/ingest → assign_meeting_dates --apply
→ (labeling_app: app → export) → gold_split → extract_corpus (--submit/--collect) →
analyze_corpus | analyze_permits | analyze_delay | analyze_conditions`.

Run order, **meeting level** (joins to the item level on document + `meeting_ordinal`):
`date_boundary_app (mark boundaries) → meeting_headers → extract_all_meetings --score →
plot_meeting_timeseries | plot_extraction_accuracy`.

The local-model line — `training_sample_create → train.py | llm_extract.py →
run_extraction → data_collect` — is the pre-API path and is kept for the accuracy floor.
`learning_curve.py` (how many labels?) and `label_qa.py` (audit/back-fill existing labels)
support the labeling loop.

## `demand_estimation/`

Layer I demand-side data collection — **region-wide** (nine-county ABAG Bay
Area), not per-locality, so its data sits at the **data root** under
`data/demand/` (like `crosswalks/`, `shapefiles/`, `clean/`). Spec:
`.claude/instructions/demand_data_brief.md`.

| File | Role |
|---|---|
| `demand_paths.py` | Imports `DATA_ROOT` from the minutes `paths.py`; defines the `demand/` tree + the 9-county FIPS set. |
| `util.py` / `manifest.py` / `arcgis.py` | Polite streaming HTTP + checksums + Census-key resolver; `_manifest.csv` writer; ArcGIS Feature Service → GeoJSON pager. |
| `collectors/` | One module per source: `tiger`, `lodes`, `acs` (PUMS+tables), `ssurgo` (the instrument), `hazard`, `zoning`, `amenities`, `migration_irs`. |
| `stubs.py` | Manual licensed sources (CoreLogic, RS Means, Infutor/Verisk) → `_stubs/<name>/README.md`. |
| `build.py` | `python -m demand_estimation.build` — collect → build (BG↔jurisdiction crosswalk, job access, soil extract, controls, PUMS, design matrix). Idempotent, failure-isolated. |
| `report/` | `demand_data_report.tex` (+ `.pdf`) — provenance + manual hand-offs — and `demand_memo.pdf`, the Layer-I sorting-model spec (BLP/Bayer, soil cost-shifter instrument). |

## `output/`

Reports, memos, figures, small derived tables — never bulk data. **Each project line carries a
standing `memo.tex`**: the edit-in-place summary of what it holds and what is still open. The
nine `bay_area_recon/` probes share a single memo at the sprint root rather than one apiece.
`output/README.md` is the entry point; it says where to look, the memos say what is there.

```
output/
├── README.md                          # start here — layout + where to look first
├── planning_commission_project/       # the SF minutes pipeline (LIVE; code links here by path)
│   ├── README.md                      # one line per memo — start here
│   ├── memo/                          # the standing pipeline-status memo
│   ├── notes/                         # the spec/reference .md docs the code links to
│   │   ├── minutes_data_availability.md   # what the raw files contain, by era
│   │   ├── processing_review.md           # code review + hand-label audit
│   │   ├── data_infrastructure.md         # schema + worked examples
│   │   ├── labeling_rules.md              # SF coding manual (label/review spec)
│   │   ├── hand_label_review_guide.md     # app workflow for reviewing labels
│   │   ├── help_string_audit.md           # the prompt-generating help strings
│   │   └── schema_enrichment_recommendation.md  # ADOPT/DEFER/REJECT on fields
│   └── <one folder per memo>/         # each: <name>.{tex,pdf} + figures/ + tables/
│       meeting_level_info, extraction_method_comparison,
│       discretionary_review_patterns, permit_linkage,
│       conditions_of_approval, predicting_delay, data_acquisition
├── political_economic_housing_model/  # the theory (LIVE)
│   ├── toy_model.tex (+ .pdf, .bib)   # formal toy model + minutes mapping
│   ├── operationalization_memo.pdf    # three-layer estimation blueprint
│   ├── guren_meeting_slides.tex (+ .pdf)
│   └── memo.tex                       # what the model says
├── bay_area_recon/                    # June-2026 Bay Area feasibility sprint (closed)
│   ├── memo.tex                       # ONE memo for all nine probes (findings + contradictions)
│   ├── README.md                      # what each probe asked; the sideways path constants
│   ├── bay_area_census/               # the 109-locality frame (everything joins to it)
│   ├── archive_depth_probe/           # earliest posted MINUTES year per locality
│   ├── zoning_map_form_probe/         # how each locality publishes its zoning map
│   ├── preperiod_envelope_probe/      # datable ~2016 pre-period envelope (15/25)
│   ├── hcd_preemption_panel/          # the TREATMENT variable (25 localities)
│   ├── migration_cliffs_probe/        # 2024 portal migrations kept their history
│   ├── civicplus_depth_probe/         # CivicPlus depth (2 of 14 resolved)
│   ├── minutes_platform_pilot/        # earlier 14-city pilot; autoextract does NOT transfer
│   ├── zoning_envelope_project/       # 14-city by-right envelope assessment
│   └── _source_data/                  # raw third-party releases: nzlud_muni.csv, hcd.csv
└── _archive/                          # superseded: 2 research proposals, minutes_data_sources.docx,
                                       #   OUTPUT_INVENTORY.md (pre-reorg audit)
```

The demand-estimation line keeps its documents in `demand_estimation/report/`
(`demand_data_report.tex/.pdf`, `demand_memo.pdf`), not in `output/`.

## Data (on Dropbox, not in git)

Path: `…/Dropbox/market-for-housing-regulation/data/`. Code resolves it via `paths.py`
(`MFHR_DATA_ROOT` to override). The minutes corpus is organized **per locality** so it
scales to the whole Bay Area:

```
data/meeting_minutes/
└── <locality>/                  # e.g. san_francisco (the only one so far)
    ├── raw/{year}/              # frozen HTML/PDF originals
    ├── tagged/{year}/           # text with <<Project>> markers
    │   └── training/            # {year}_labeled.json + samples + training.txt
    ├── processed/               # structured_data.jsonl, *.csv, minutes_extractor/
    └── meeting_level_data/
```

`paths.MEETING_MINUTES` points at the active locality (set `MFHR_LOCALITY=oakland` to
switch; default `san_francisco`), so all pipeline code is locality-agnostic. To onboard a
new Bay Area locality: create `meeting_minutes/<locality>/` and run scrape → parse → label
→ build → train/extract with `MFHR_LOCALITY` set. Region-wide data (`crosswalks/`,
`shapefiles/`, `clean/`, national `raw/`, and the **`demand/`** subtree written by
`demand_estimation/` — ACS, LODES, TIGER, SSURGO, hazard, zoning, amenities, migration)
is **not** per-locality and stays at the data root. The labeling DB (`labeling_app/labels.db`) is a regenerable local cache (gitignored);
durable labels are exported to `<locality>/tagged/training/{year}_labeled.json`.
