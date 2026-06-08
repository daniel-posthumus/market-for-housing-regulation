# Repository Structure

`market-for-housing-regulation` structures **SF Planning Commission meeting minutes**
(1998–present) into an item-level dataset of discretionary land-use decisions. The
pipeline: **scrape → parse/tag → label → build → train/extract**, all reading the same
schema so nothing drifts.

Reproducibility: `requirements.txt` / `requirements.lock.txt` / `environment-notes.md`.
The data corpus lives on **Google Drive, out of git** (see "Data" below).

---

## Top level

```
market-for-housing-regulation/
├── README.md
├── STRUCTURE.md                 # this file
├── requirements.txt / .lock     # Python deps (py3.12)
├── environment-notes.md         # interpreter, system deps, flagged issues
├── code/commission_minutes_processing/   # the whole pipeline (below)
├── output/                      # reports, toy model, proposals
├── notes/                       # commission_members.{xlsx,docx}
└── (data/ lives on Google Drive — not in repo)
```

## `code/commission_minutes_processing/`

| File | Role |
|---|---|
| `paths.py` | **Where the data lives** — `DATA_ROOT`/`MEETING_MINUTES`, env-overridable via `MFHR_DATA_ROOT`. Everything imports paths from here. |
| `extraction_common.py` | **The 35-field `SCHEMA`** (single source of truth) → `FIELDS`, `PROMPT_INSTRUCTION`, `coerce_record()`, `score_examples()`. |
| `autoextract.py` | Regex/heuristic best-guess extraction from a raw block (form pre-fill + builder derivations). |
| `minutes_scraping/scrape_minutes.py` | Consolidated, idempotent scraper (S3 HTML 1998–2014; live archive PDFs 2015–present). Legacy scrapers deprecated alongside. |
| `parse_sf_meeting_minutes.py` | Scrape/parse archived HTML → `tagged/{year}/*.txt` blocks + meeting metadata. |
| `training_sample_create.py` | Pair labels ↔ blocks → consolidated `tagged/training/training.txt` (JSONL). |
| `migrate_labels.py` | One-time: migrate old `*_labeled.json` into the schema (backs up originals). |
| `train.py` | Fine-tune T5 (`MINUTES_MODEL`/`MINUTES_USE_LORA`/`MINUTES_EPOCHS`); held-out test report. |
| `llm_extract.py` | Few-shot, schema-constrained extraction (HF or Anthropic backend) on the same split. |
| `inference.py` | Run a trained model on a tagged file → `processed/structured_data.jsonl`. |
| `data_collect.py` | JSONL → `processed/extracted_results.csv`. |
| `labeling_app/` | Local web app to hand-label items: `ingest.py` (corpus → `labels.db`), `app.py` (Flask UI), `templates/` + `static/`, `README.md`. |
| `scratch_code/` | Prototypes (pdfplumber, LoRA variants). |

Run order: `scrape_minutes → parse_sf_meeting_minutes → (labeling_app: ingest → app →
export) → training_sample_create → train.py | llm_extract.py → inference → data_collect`.

## `output/`

```
output/
├── planning_commission_project/
│   ├── minutes_data_availability.md   # what the raw files contain, by era
│   ├── processing_review.md           # code review + hand-label audit
│   ├── data_infrastructure.md         # schema + worked examples
│   ├── proposal/                      # research_proposal.tex
│   └── graphics/
└── political_economic_housing_model/
    ├── toy_model.tex (+ .pdf)         # formal toy model + minutes mapping
    └── proposal/
```

## Data (on Google Drive, not in git)

Path: `…/My Drive/market-for-housing-regulation/data/` with the familiar layout
(`meeting_minutes/{raw,tagged,processed}`, etc.). Code resolves it via `paths.py`
(`MFHR_DATA_ROOT` to override). The Google Drive `_archive/` folder holds retired
material: the old tabular pipeline (`code/cleaning_code`), `resources/`, the old
`prospectus`, `scratch/`, and most research memos. The labeling DB
(`labeling_app/labels.db`) is a regenerable local cache (gitignored); durable labels are
exported to `tagged/training/{year}_labeled.json`.
