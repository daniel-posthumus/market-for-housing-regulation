# Minutes labeling app

A local web app for hand-labeling individual SF Planning Commission meeting items
against the full extraction schema. Machine pre-fills each item; you correct and
confirm. Nothing leaves your machine unless you opt into the Anthropic pre-fill.

## The schema is the single source of truth

All of it — the labeling form, the model prompt, the builder's required keys, and the
eval metric — is generated from `SCHEMA` in
[`../extraction_common.py`](../extraction_common.py) (29 fields, grouped: Identity,
Location, Zoning & scale, Process, Politics, Conditions). **Add/rename a field there
and it propagates everywhere** (re-run the app to pick it up). Field types:
`scalar | text | list | int | enum`.

## One-time setup

```bash
cd ~/market-for-housing-regulation
source .venv/bin/activate

# 1. Migrate existing hand-labels into the new schema (backs up originals to
#    tagged/training/_backup_pre_schema/). Run once.
python code/commission_minutes_processing/migrate_labels.py

# 2. Parse the modern era (2015–present) into tagged blocks (HTML era is already
#    tagged by parse_sf_meeting_minutes.py).
python code/commission_minutes_processing/parse_modern_minutes.py

# 3. Build the labeling work-queue from the tagged corpus (1998–present).
cd code/commission_minutes_processing/labeling_app
python ingest.py
```

`ingest.py` creates `labels.db` (SQLite) with one row per project block.
Each gets a pre-filled label: your migrated record if the case number matches
(status `prelabeled`), else a heuristic guess (status `todo`). It is **idempotent** —
re-running adds only new items and never overwrites your edits (`--reset` to rebuild,
`--years 2010-2014` to scope).

### Dates are a separate stage

`ingest.py` and `rebuild_review_db.py` stamp each block with a provisional date (the
source file's first one). **That is not the answer** — a single archive page can bundle
four meetings. Run the date stage after any ingest or rebuild:

```bash
python ../assign_meeting_dates.py                    # dry run + date_assignment_audit.csv
python ../assign_meeting_dates.py --apply --sync-labels
```

It locates each block back inside its source page by content and reads off the meeting
header it falls under, so it stays correct no matter how the parser's block boundaries
change. `../date_boundary_app/` is where you hand-mark meeting starts to check it.

It writes two columns: `items.meeting_date` and `items.meeting_ordinal` — the index of
the meeting header the block sits under, within its source document. The ordinal exists
because a date is not a key: seven archive-era documents hold a joint session and the
regular meeting on the same day, and the item level has to tell them apart.

### The meeting bar

The strip above the item form is the meeting the item was heard at — date and weekday,
meeting type (**joint** / special / closed session are badged; regular is the default),
the other body when it is a joint hearing, gavel time, room, and present/absent counts
(hover for the names). **None of it is labeled.** It is read once per meeting by the
meeting-level pipeline and joined in, which is why `meeting_date` is not a form field:
it is a property of the hearing, not of the item.

```bash
python ../extract_all_meetings.py      # rebuild the corpus-wide meeting table
```

That writes `meetings_all` into `../date_boundary_app/date_gold.db`, which the app reads
at startup (restart it to pick up a rebuild). The bar turns amber and reads *no meeting
record* where the raw document is not in the corpus — 2018, whose PDFs were lost and
which the re-scrape recovered only two of. The date is still right on those items; only
the hearing-level attributes are missing.

## Audit existing labels first (the QA gate)

Before adding new labels, fix the ones you have. `label_qa.py` diffs every existing
label against its source block and surfaces the likely-wrong ones (the migration
left **351 of 415** with `action='other'`, plus dropped `vote`/`noes`/`absent` and
2014's districts — see `../../output/planning_commission_project/processing_review.md`).

```bash
cd code/commission_minutes_processing
python label_qa.py                       # REPORT only → label_audit.csv + summary
python label_qa.py --apply --backfill    # back up labels.db, additively fill
                                         # recoverable fields from source, and flag
                                         # every touched item for confirmation
```

`--backfill` never overwrites a real human value — it only fills empties / `'other'`
enums from what the raw block plainly states (action, roll-call, districts, planner,
…). Each changed item becomes `flagged` with a `[QA-backfilled: …]` note. Then in the
app, filter to **flagged** and confirm top-down — the corrected values are already
pre-filled, so it's a glance-and-save, not a re-type.

## Labeling

```bash
python app.py          # serves http://127.0.0.1:5005
```

**Label to the rules.** The SF-specific coding manual —
[`../../../output/planning_commission_project/labeling_rules.md`](../../../output/planning_commission_project/labeling_rules.md)
— is the authoritative spec for every field (case-suffix → request_type, the `action`
disposition vocabulary, stance markers, recurring-case handling, etc.). Keep it open
while labeling; `label_qa.py` enforces the mechanical parts of it.

**Queue order.** The sidebar's **order** dropdown defaults to **rare-class first**:
items are scored by how under-represented their `request_type`/`action` is in the
labels you already have, then interleaved round-robin by year — so labeling top-down
fills scarce classes *evenly across 1998–2014* instead of grinding through 1998 first
(which is already 40% of the labels). A `rare N` badge shows the scarcity score. Switch
to **chronological** to label a year in order. (This also fixes the old bug where the
list capped at 5,000 and later years were unreachable.)

- **Left pane**: raw item text, case number highlighted. **Right pane**: the schema
  form, pre-filled.
- **compact / verbatim** (`t`) switches how the left pane is rendered. The 1998–2014
  archive breaks a line at every HTML tag boundary and pads with blanks — 54% of all
  stored block text is whitespace, and the worst single item is 2,051 lines — so
  **compact** reflows each paragraph onto one line, keeping a break before every
  labelled field (`ACTION`, `AYES`, `Preliminary Recommendation`, …) and rejoining a
  label to its value. Corpus-wide that is 1.29M rendered lines down to 308k, and it
  makes the HTML and PDF eras read the same shape. **This is display only** —
  `items.block_text` is never modified, because that exact string is what the trainer
  sees and what `assign_meeting_dates.py` matches back into its source page. Hit `t`
  whenever you need to check the stored text character-for-character; the choice
  sticks across items and sessions.
- **Correct, then Save & next** (button or **⌘/Ctrl+Enter**). Status becomes `done`.
- **Re-prefill** (`p`) re-runs extraction on the raw text (overwrites the form) —
  choose `heuristic` (offline) or `anthropic` (needs `pip install anthropic` +
  `ANTHROPIC_API_KEY`).
- **Flag uncertain** to set status `flagged` and revisit later; add free-text `notes`.
- Filter the sidebar by status / year / search; **Alt+↑/↓** moves between items.
- Lists (ayes, speakers, …) are comma-separated; counts are integers; enums are
  dropdowns. The backend coerces everything to the schema on save.

> Worth knowing: `label_qa.py --apply --backfill` (above) now does the old
> per-item "Re-prefill the `action='other'` items" chore in bulk. You can still hit
> **Re-prefill (heuristic / anthropic)** (`p`) on any single item to re-pull it from
> the raw text.

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

### How many labels? — the learning curve

`../learning_curve.py` fine-tunes at increasing label counts against one fixed
held-out test set and plots field-accuracy vs. #labels, so you stop labeling at the
empirical plateau instead of guessing. It reuses `train.py`'s code path.

```bash
python ../learning_curve.py --smoke                 # fast plumbing check (flan-t5-small)
python ../learning_curve.py --sizes 80,140,200,269  # the real curve (flan-t5-base)
```

Outputs `learning_curve.png`, `curve.csv`, and `per_field.csv` (which fields still
starve for labels) under `processed/minutes_extractor/learning_curve/`. It's
compute-heavy (a full fine-tune per point) and resumable (skips done points).

## Files
- `ingest.py` — corpus → `labels.db` work-queue (+ heuristic/prelabel seed).
- `app.py` — Flask server + JSON API (`/api/schema|items|item|prefill|stats|export`).
- `queue_order.py` — pure rare-class-first + year-balanced queue ordering (used by
  `/api/items`; `python queue_order.py` runs its self-test).
- `templates/index.html`, `static/app.js`, `static/style.css` — the UI.
- `labels.db` — your labeling store (SQLite; not the training output).
- `../label_qa.py` — audit/back-fill existing labels (the QA gate above).
- `../assign_meeting_dates.py` — the date stage (which meeting each block belongs to).
- `../extract_all_meetings.py` — the corpus-wide meeting-level table the meeting bar reads.
- `../meeting_headers.py` — meeting-level schema, pre-fill, and the item → meeting join.
- `../date_boundary_app/` — hand-mark meeting starts; scores the date stage against them;
  `/meetings` is where meeting-level records are confirmed by hand.
- `../learning_curve.py` — how-many-labels learning curve.

## Extending to other Bay Area jurisdictions
`jurisdiction` and `supervisorial_district` are no longer labeled fields (dropped
2026-08 — derived post-hoc), so a second city is a corpus question, not a schema one.
To add one: scrape its minutes, parse into
`<<Project Start>>…<<Project End>>` blocks under `data/meeting_minutes/tagged/<city>/`,
adjust `ingest.py`'s source glob, and re-ingest. The form, model, and metric need no
change — that is the point of the shared schema, and what the toy model's
multi-jurisdiction (strategic-interaction) build requires.
