# market-for-housing-regulation

Structuring and analyzing **San Francisco Planning Commission meeting minutes**
(1998–present) into an item-level dataset of discretionary land-use decisions — the
empirical backbone for studying the *market for housing regulation*.

## What's here

```
code/commission_minutes_processing/   scrape → parse → label → train/extract pipeline
  paths.py                            where the data lives (DATA_ROOT, env-overridable)
  extraction_common.py                the 29-field SCHEMA (single source of truth)
  autoextract.py                      heuristic best-guess extraction
  assign_meeting_dates.py             which meeting each block belongs to (own stage)
  date_boundary_app/                  hand-mark meeting starts → gold standard for it
  scrape + parse + builder + train + llm_extract + inference
  labeling_app/                       local web app to hand-label items (Flask + SQLite)
  migrate_labels.py                   one-time: old labels → new schema
output/                               reports & memos (see output/README.md)
  planning_commission_project/        SF minutes pipeline: specs, coding manual, reviews
  political_economic_housing_model/   toy_model.tex (+ pdf) and the operationalization memo
  bay_area_recon/                     the June-2026 nine-probe Bay Area feasibility sprint
  _archive/                           superseded proposals + the pre-reorg output inventory
demand_estimation/                    Layer I demand-side collection (+ report/)
notes/                                commission_members.{xlsx,docx}
requirements.txt / requirements.lock.txt / environment-notes.md
STRUCTURE.md                          fuller map of the repo
```

## Data lives on Dropbox (not in git)

The ~18 GB corpus and all data are at
`…/Dropbox/market-for-housing-regulation/data/` and are **not** tracked in git. Code
finds them via `code/commission_minutes_processing/paths.py` (`DATA_ROOT`), overridable
with `export MFHR_DATA_ROOT=/path/to/data`.

The minutes corpus is organized **per locality** so it can scale to the whole Bay
Area — `meeting_minutes/<locality>/{raw,tagged,processed,meeting_level_data}`. Today the
only locality is `san_francisco`; to add another, create its subtree and run the pipeline
with `export MFHR_LOCALITY=oakland`. `paths.MEETING_MINUTES` always points at the active
locality, so the pipeline code stays locality-agnostic.

## Setup & workflow

```bash
python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
# label:
cd code/commission_minutes_processing/labeling_app && python ingest.py && python app.py
# dates are their own stage — run after any ingest/rebuild:
python ../assign_meeting_dates.py --apply --sync-labels
# build + train / extract:
python ../training_sample_create.py
python ../train.py          # or: python ../llm_extract.py --backend anthropic
```

See `code/commission_minutes_processing/labeling_app/README.md` for the labeling
workflow and `output/planning_commission_project/` for the data reports. `output/README.md`
maps the whole reports tree; each project line carries a standing `memo.tex` describing
itself, and the nine `bay_area_recon/` probes share one at `output/bay_area_recon/memo.tex`.
