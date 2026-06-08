# Minutes labeling app

A local web app for hand-labeling individual SF Planning Commission meeting items
against the full extraction schema. Machine pre-fills each item; you correct and
confirm. Nothing leaves your machine unless you opt into the Anthropic pre-fill.

## The schema is the single source of truth

All of it — the labeling form, the model prompt, the builder's required keys, and the
eval metric — is generated from `SCHEMA` in
[`../extraction_common.py`](../extraction_common.py) (35 fields, grouped: Identity,
Location, Zoning & scale, Process, Politics, Conditions). **Add/rename a field there
and it propagates everywhere** (re-run the app to pick it up). Field types:
`scalar | text | list | int | enum`.

## One-time setup

```bash
cd /Users/danpost/housing_project
source .venv/bin/activate

# 1. Migrate existing hand-labels into the new schema (backs up originals to
#    tagged/training/_backup_pre_schema/). Run once.
python code/commission_minutes_processing/migrate_labels.py

# 2. Build the labeling work-queue from the tagged corpus (1998–2014).
cd code/commission_minutes_processing/labeling_app
python ingest.py
```

`ingest.py` creates `labels.db` (SQLite) with one row per project block (~9k items).
Each gets a pre-filled label: your migrated record if the case number matches
(status `prelabeled`), else a heuristic guess (status `todo`). It is **idempotent** —
re-running adds only new items and never overwrites your edits (`--reset` to rebuild,
`--years 2010-2014` to scope).

## Labeling

```bash
python app.py          # serves http://127.0.0.1:5005
```

- **Left pane**: raw item text, case number highlighted. **Right pane**: the schema
  form, pre-filled.
- **Correct, then Save & next** (button or **⌘/Ctrl+Enter**). Status becomes `done`.
- **Re-prefill** (`p`) re-runs extraction on the raw text (overwrites the form) —
  choose `heuristic` (offline) or `anthropic` (needs `pip install anthropic` +
  `ANTHROPIC_API_KEY`).
- **Flag uncertain** to set status `flagged` and revisit later; add free-text `notes`.
- Filter the sidebar by status / year / search; **Alt+↑/↓** moves between items.
- Lists (ayes, speakers, …) are comma-separated; counts are integers; enums are
  dropdowns. The backend coerces everything to the schema on save.

> Worth knowing: migrated `prelabeled` items keep your *old* values, so some
> `action` fields show `other` (they were free-text continuance phrases). Hit
> **Re-prefill (heuristic)** on those — it maps the raw `ACTION:` line to the right
> enum (`continued`, `approved`, …) — then eyeball and Save.

## Export → train

```bash
# In the app: "Export done →"  (writes status=done labels to
# tagged/training/{year}_labeled.json, backing up to *.preexport.bak)

# Rebuild the consolidated training set and (re)train:
python ../training_sample_create.py
python ../train.py            # MINUTES_MODEL / MINUTES_USE_LORA / MINUTES_EPOCHS env vars
# or skip fine-tuning and few-shot extract:
python ../llm_extract.py --backend anthropic --shots 5
```

## Files
- `ingest.py` — corpus → `labels.db` work-queue (+ heuristic/prelabel seed).
- `app.py` — Flask server + JSON API (`/api/schema|items|item|prefill|stats|export`).
- `templates/index.html`, `static/app.js`, `static/style.css` — the UI.
- `labels.db` — your labeling store (SQLite; not the training output).

## Extending to other Bay Area jurisdictions
The schema already carries `jurisdiction` (defaults to "San Francisco") and
`supervisorial_district`. To add another city: scrape its minutes, parse into
`<<Project Start>>…<<Project End>>` blocks under `data/meeting_minutes/tagged/<city>/`,
adjust `ingest.py`'s source glob, and re-ingest. The form, model, and metric need no
change — that is the point of the shared schema, and what the toy model's
multi-jurisdiction (strategic-interaction) build requires.
