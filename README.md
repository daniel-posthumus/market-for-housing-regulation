# market-for-housing-regulation

Structuring and analyzing **San Francisco Planning Commission meeting minutes**
(1998–present) into an item-level dataset of discretionary land-use decisions — the
empirical backbone for studying the *market for housing regulation*.

## What's here

```
code/commission_minutes_processing/   scrape → parse → label → train/extract pipeline
  paths.py                            where the data lives (DATA_ROOT, env-overridable)
  extraction_common.py                the 35-field SCHEMA (single source of truth)
  autoextract.py                      heuristic best-guess extraction
  scrape + parse + builder + train + llm_extract + inference
  labeling_app/                       local web app to hand-label items (Flask + SQLite)
  migrate_labels.py                   one-time: old labels → new schema
output/
  planning_commission_project/        data-availability, processing review, infra report
  political_economic_housing_model/    toy_model.tex (+ pdf) and proposal
notes/                                commission_members.{xlsx,docx}
requirements.txt / requirements.lock.txt / environment-notes.md
STRUCTURE.md                          fuller map of the repo
```

## Data lives on Google Drive (not in git)

The ~18 GB corpus and all data are at
`…/My Drive/market-for-housing-regulation/data/` and are **not** tracked in git. Code
finds them via `code/commission_minutes_processing/paths.py` (`DATA_ROOT`), overridable
with `export MFHR_DATA_ROOT=/path/to/data`. Archived non-minutes material (the old
tabular pipeline, prospectus, resources, memos) is in the Drive `_archive/` folder.

## Setup & workflow

```bash
python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
# label:
cd code/commission_minutes_processing/labeling_app && python ingest.py && python app.py
# build + train / extract:
python ../training_sample_create.py
python ../train.py          # or: python ../llm_extract.py --backend anthropic
```

See `code/commission_minutes_processing/labeling_app/README.md` for the labeling
workflow and `output/planning_commission_project/` for the data reports.
