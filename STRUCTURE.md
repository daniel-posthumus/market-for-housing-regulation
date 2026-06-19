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
├── output/                      # reports, toy model, proposals
├── notes/                       # commission_members.{xlsx,docx}
└── (data/ lives on Dropbox — not in repo)
```

## `code/commission_minutes_processing/`

| File | Role |
|---|---|
| `paths.py` | **Where the data lives** — `DATA_ROOT`/`MEETING_MINUTES`, env-overridable via `MFHR_DATA_ROOT`. `MEETING_MINUTES` resolves to the *active locality* (`MFHR_LOCALITY`, default `san_francisco`) so the pipeline scales across the Bay Area. Everything imports paths from here. |
| `extraction_common.py` | **The 35-field `SCHEMA`** (single source of truth) → `FIELDS`, `PROMPT_INSTRUCTION`, `coerce_record()`, `score_examples()`. |
| `autoextract.py` | Regex/heuristic best-guess extraction from a raw block (form pre-fill + builder derivations). |
| `minutes_scraping/scrape_minutes.py` | Consolidated, idempotent scraper (S3 HTML 1998–2014; live archive PDFs 2015–present). Legacy scrapers deprecated alongside. |
| `parse_sf_meeting_minutes.py` | Scrape/parse archived **HTML (1998–2014)** → `tagged/{year}/*.txt` blocks + meeting metadata. |
| `parse_modern_minutes.py` | Parse the **modern era (2015–present)** — text (2015–17) and PDF (2018+, via pdfplumber) → the same `<<Project>>`-tagged blocks, date-led filenames, + `processed/modern_meetings_metadata.csv`. Handles the dash case format and the spaced/space-stripped item headers the HTML parser can't. |
| `training_sample_create.py` | Pair labels ↔ blocks → consolidated `tagged/training/training.txt` (JSONL). |
| `migrate_labels.py` | One-time: migrate old `*_labeled.json` into the schema (backs up originals). |
| `train.py` | Fine-tune T5 (`MINUTES_MODEL`/`MINUTES_USE_LORA`/`MINUTES_EPOCHS`); held-out test report. Factored into importable functions reused by `learning_curve.py`. |
| `learning_curve.py` | Fine-tune at increasing label counts vs. a fixed held-out test set → field-accuracy curve (`learning_curve.png`/`curve.csv`/`per_field.csv`); answers "how many labels do I need?". |
| `label_qa.py` | Audit existing labels against their source blocks (continuance mis-coding, dropped `vote`/`noes`/`absent`, 2014 districts, `action='other'`); `--apply --backfill` safely fills recoverable fields and flags items for confirmation. |
| `llm_extract.py` | Few-shot, schema-constrained extraction (HF or Anthropic backend) on the same split. |
| `run_extraction.py` | **Corpus-wide** structured extraction with periodic QA. Pluggable engine (`heuristic`/`hf`/`anthropic`), schema-aligned via `extraction_common`, resumable → `processed/structured_data.jsonl` + `extracted_results.csv` + `extraction_qa_report.md` (coverage, distributions, accuracy-vs-gold). Supersedes `inference.py`. |
| `inference.py` | Legacy single-file/old-schema demo (kept for reference; use `run_extraction.py`). |
| `data_collect.py` | JSONL → `processed/extracted_results.csv`. |
| `labeling_app/` | Local web app to hand-label items: `ingest.py` (corpus → `labels.db`), `app.py` (Flask UI), `queue_order.py` (rare-class-first + year-balanced queue), `templates/` + `static/`, `README.md`. |
| `scratch_code/` | Prototypes (pdfplumber, LoRA variants). |

Run order: `scrape_minutes → parse_sf_meeting_minutes (1998–2014) + parse_modern_minutes
(2015–present) → (labeling_app: ingest → app → export) → training_sample_create →
train.py | llm_extract.py → inference → data_collect`. `learning_curve.py` (how many
labels?) and `label_qa.py` (audit/back-fill existing labels) support the labeling loop.

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
| `report/` | `demand_data_report.tex` (+ `.pdf`) — provenance + manual hand-offs. |

## `output/`

```
output/
├── planning_commission_project/
│   ├── minutes_data_availability.md   # what the raw files contain, by era
│   ├── processing_review.md           # code review + hand-label audit
│   ├── data_infrastructure.md         # schema + worked examples
│   ├── labeling_rules.md              # SF-specific coding manual (label/review spec)
│   ├── hand_label_review_guide.md     # app workflow for reviewing labels
│   ├── proposal/                      # research_proposal.tex
│   └── graphics/
└── political_economic_housing_model/
    ├── toy_model.tex (+ .pdf)         # formal toy model + minutes mapping
    └── proposal/
```

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
