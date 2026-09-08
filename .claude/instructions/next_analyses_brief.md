# Next analyses: permit linkage, conditions, and predicting delay

Written 2026-09-07 at the end of the session that produced the corpus extraction. This brief
is meant to be executed in a **fresh Claude Code session** with no memory of that work, so it
states the facts you would otherwise have to rediscover.

Read `CLAUDE.md` first — it has the repo's standing rules. This brief adds task-specific
detail and the findings that are expensive to re-derive.

---

## 0. Where things stand

The item-level dataset now exists. Every block the SF Planning Commission heard between 1998
and mid-2026 that carries a case number has been extracted into 29 structured fields:
**16,199 items across 8,987 distinct cases**.

**The data is on Dropbox, not in git**, at
`$MFHR_DATA_ROOT/extraction/corpus_v2_g3/` (resolve via
`code/commission_minutes_processing/paths.py`), in four forms kept deliberately separate:

| directory | what it is |
|---|---|
| `raw/chunk-NNN.jsonl` | the model's replies exactly as returned. **Never rewrite these.** |
| `interim/chunk-NNN.jsonl` | unwrapped from the evidence envelope, type-coerced |
| `clean/chunk-NNN.jsonl` | normalised — **this is your analysis input** |
| `evidence/chunk-NNN.jsonl` | the model's supporting spans for six fields |
| `manifest.json` | batch ids, run ids, token counts, cost, prompt SHA, schema/gold versions |
| `verification_failures.csv` | evidence spans that are not verbatim in the block |

Why three forms: every serious defect found while building the gold set was a *normalisation*
defect. One of them (`lot_number` declared `scalar` while the normaliser stored a list)
silently emptied the field on 122 of 232 gold records, and was recoverable only because the
untouched replies had been kept. **Re-deriving `clean` from `raw` is a local loop; re-querying
the corpus costs $69.** If you change `normalize.py`, re-derive rather than re-query.

Extraction quality, for context when you report anything: few-shot Haiku 4.5, **96.8%**
field-level accuracy on a frozen 139-item test half (97.4% HTML era, 94.0% PDF era). Two
memos already document this — read `output/planning_commission_project/` before writing
anything, so you do not contradict or duplicate them.

### Two coverage gaps that will bite you

- **1998–2000**: 12 meeting documents per year, against ~40 from 2001. The Commission met
  weekly; the archive does not. Rates are usable, **counts are not comparable**.
- **2025–2026**: 8 documents each. The scrape reached the archive before the archive reached
  the present. Any count for those years is a floor. Say so in every figure.

### Field fill rates worth knowing before you plan an analysis

```
case_number 99.1   request_type 98.4   action 98.1   project_descr 99.0   staff_planner 98.8
assessor_block 84.5   lot_number 84.1   project_address 84.6   ayes 84.4
preliminary_recommendation_category 78.7   type_district 78.2
speakers 43.4   action_instrument 40.5   action_instrument_no 39.5   continued_to 36.9
conditions_imposed 25.9   modifications 10.7   project_modified 8.9
```

---

## 1. Task zero: reorganise `output/planning_commission_project/`

Do this **first**, because the three new memos should land in the new structure rather than
being moved afterwards.

The directory is currently flat: five `.tex` memos, their PDFs, six loose `fig_*.pdf`, two
shared `*_tables.tex`, plus LaTeX build droppings and half a dozen `.md` notes. Give each memo
its own subfolder:

```
output/planning_commission_project/
├── README.md                        # NEW: one line per memo, what it answers
├── extraction_method_comparison/
│   ├── extraction_method_comparison.tex
│   ├── extraction_method_comparison.pdf
│   ├── figures/
│   └── tables/bakeoff_tables.tex
├── discretionary_review_patterns/
│   ├── discretionary_review_patterns.{tex,pdf}
│   ├── figures/fig_{composition,outcomes,delay,commissioners,geography,citations}.pdf
│   └── tables/corpus_tables.tex
├── meeting_level_info/
│   ├── meeting_level_info.{tex,pdf}
│   ├── figures/{extraction_accuracy,meeting_timeseries}.pdf
│   └── tables/
├── memo/                            # the standing pipeline-status memo
│   └── memo.{tex,pdf}
├── permit_linkage/                  # NEW, task 2
├── conditions_of_approval/          # NEW, task 3
├── predicting_delay/                # NEW, task 4
└── notes/                           # the loose .md files
    ├── data_infrastructure.md, hand_label_review_guide.md, help_string_audit.md,
    ├── labeling_rules.md, minutes_data_availability.md, processing_review.md,
    └── schema_enrichment_recommendation.md
```

Rules for the move:

- **Use `git mv`** so history follows the files.
- **Update every path.** `\includegraphics{fig_x.pdf}` becomes `\includegraphics{figures/fig_x.pdf}`;
  `\input{corpus_tables}` becomes `\input{tables/corpus_tables}`. There are 7 `\includegraphics`
  and 2 `\input` across the existing memos — grep, do not guess.
- **Update the generating scripts too**, or the next run scatters files back into the flat
  layout: `analyze_corpus.py` (`OUT`), `bakeoff_memo.py` (`TEX`), and whatever writes
  `meeting_timeseries.pdf` / `extraction_accuracy.pdf` (check `plot_meeting_timeseries.py`).
- **Recompile every memo and check the log**, per CLAUDE.md: `grep -c Undefined *.log` must be
  0 and `grep -c 'Overfull .hbox.*in alignment' *.log` must be 0.
- **Do not commit LaTeX droppings.** `.aux/.log/.out/.toc` are already git-ignored; verify
  with `git status` after the move.
- `fig_citations.pdf` is generated by `analyze_corpus.py` but **not referenced by any memo** —
  the citations analysis is presented as a table instead. Either include it in
  `discretionary_review_patterns.tex` or stop generating it; do not leave it orphaned.
- Also update `output/README.md`, which maps the reports.

---

## 2. Memo: permit linkage

**Folder**: `output/planning_commission_project/permit_linkage/`
**Question**: how far can the entitlement record be followed to an actual building permit, and
what does that buy?

### What is already established (do not re-derive, but do verify)

- **3,932 items** mention a building or demolition permit in `project_descr`.
- **2,778 items** yield a parsable permit number — 1,752 distinct — in three printed forms:
  - modern dotted: `2013.10.21.9832` → regex `\b(20\d\d|19\d\d)[.\-/](\d{2})[.\-/](\d{2})[.\-/](\d{3,5})\b`
  - packed: `201310219832` → `\b((?:19|20)\d\d)(\d{2})(\d{2})(\d{4})\b`
  - 1990s eight-digit: `No. 9725973` → `\bNos?\.?\s*(\d{7,9})\b`
  - Normalise by stripping non-digits before matching against DataSF.
- **On a random sample of 120 parsed numbers, 117 matched (97.5%)** against DataSF's Building
  Permits dataset. That was a sample; **run it over all 1,752 and report the true rate.**
- The **parcel route** (block + lot, available on 84.1% of items): on a random 150 parcels,
  **68% had at least one DBI permit**, but the **median matched parcel carries 15 permits**,
  most of them plumbing/electrical/small alterations irrelevant to the entitlement.

### DataSF specifics

```
Building Permits (DBI)   i98e-djp9   1,295,048 rows
Dwelling Unit Completion acdm-wktn     236,556 rows
```
Endpoint: `https://data.sfgov.org/resource/<id>.json?$where=...&$limit=...&$select=...`

**Gotcha**: plain `urllib` fails with `CERTIFICATE_VERIFY_FAILED` on this machine. Use
`ssl.create_default_context(cafile=certifi.where())`. There is an existing prototype at
`code/commission_minutes_processing/datasf_records.py` with an app-token loader — use a token
if one is available (`api_keys/socrata_app_token.txt`), and be polite about rate limits.

Useful DBI fields: `permit_number`, `filed_date`, `issued_date`, `approved_date`,
`first_construction_document_date`, `status`, `estimated_cost`, `revised_cost`,
`proposed_units`, `existing_units`, `block`, `lot`, `permit_type_definition`, `description`.

### What the memo should answer

1. **Match rate over all 1,752 permits**, not a sample. Break it down **by decade** — the
   1990s eight-digit form is the least certain and DBI's coverage of that era should be tested
   rather than assumed.
2. **Which items carry a parsable permit number?** Cross-tabulate against `request_type`. The
   strong prior is that discretionary review does (it *is* a review of a permit) and
   conditional use does not, but check — and report the rate for every family, because that
   tells you which parts of the docket the permit route can and cannot reach.
3. **Coverage over time.** Note before you start: the parsed-permit counts thin out sharply
   after 2019 (2020: 8, 2021: 1, 2022: 3). Establish whether that is the modern minutes
   dropping permit numbers from the text, the DR collapse removing the items that carry them,
   or a parsing failure on a newer printed format. **This matters** — it determines whether
   the permit route is usable for recent years at all.
4. **What the match buys.** For matched items, report how often you can observe: permit
   issued (and lag from hearing), construction started (`first_construction_document_date`),
   withdrawn/expired, units proposed vs `proposed_units` in DBI, and cost.
5. **The parcel route as a fallback.** Can items *without* a parsable permit number be matched
   by block+lot plus a date window plus a permit-type filter? Design the filter, then
   **validate it against the permit-number subset** — you have ground truth there, which is
   exactly what that subset is for. Report precision and recall, not just coverage. If it does
   not work, say so; a negative result here is worth writing down.
6. **Are entitlement conditions visible in DBI?** Expect no — DBI records the permit's own
   status and scope, not the Commission's conditions of approval — but confirm it, because
   task 3 depends on the answer.

---

## 3. Memo: conditions of approval — the binding constraint

**Folder**: `output/planning_commission_project/conditions_of_approval/`
**Question**: can we recover *what* conditions were imposed, not merely *that* they were?

### Why this matters more than anything else on the list

The pattern finding of the corpus memo is that the Commission substituted from **denial**
(1.4% of items over 28 years, ~0% since 2020) to **conditioning** (13.8% → 45.7% of items).
The burden moved into the content of the approval. But:

**`conditions_imposed` is populated on 25.9% of items and its median length is three
characters.** It is a yes/no flag. The minutes routinely say conditions were imposed without
enumerating them, and the extraction faithfully records that. `modifications` does carry text
(median 108 characters) but is populated on only 10.7% of items.

So the dataset answers *whether* and almost never *how*. Closing that gap is the highest-value
thing on this list, because the paper's measure of stringency depends on it.

### The lead to chase

The Commission's conditions live in the **numbered motion or resolution**, not in the minutes.
Schema v2 extracts that document number:

- `action_instrument` ∈ {`motion`, `resolution`, `dra`} — populated on **40.5%** of items
- `action_instrument_no` — the number, digits only — **39.5%**

`dra` is a Discretionary Review Action, added because 11 gold items printed `DRA#: 0013`.

### What the memo should establish

1. **Coverage.** How many items have an instrument number, and how does that vary by
   `request_type`, by era, and by whether `conditions_imposed` is set? An item with conditions
   but no instrument number is unreachable by this route — quantify that group.
2. **Are the documents obtainable?** This is the open empirical question and the memo's real
   contribution. Investigate, in order:
   - The Planning Department publishes motions and resolutions; find whether there is a
     systematic URL pattern keyed on the motion number, or a bulk source.
   - Check DataSF for a planning-records or commission-actions dataset that carries condition
     text. (`p4e4-a5a7` turned out to be another permits table, not planning records — do not
     waste time there.)
   - Check whether the minutes themselves carry condition text anywhere the current schema is
     not looking. The blocks are in `labels.db` (`items.block_text`) and are the same text the
     extraction saw; a targeted regex sweep for enumerated conditions costs nothing.
   **Report what you find, including "not available", with evidence.** Do not speculate about
   what might exist.
3. **The template question the corpus memo raised.** Conditions of approval are substantially
   standardised in SF practice — affordability, transportation demand management, open space,
   hours of operation, design review, monitoring and reporting. If you obtain any condition
   text at all, even for a few hundred items, **test whether a small taxonomy covers most of
   it**. A handful of indicators derived from templated text would convert an unstructured
   field into a usable measure of burden. If you cannot obtain text, say what a pilot would
   need.
4. **The cost-relevant distinction.** Not every condition is costly in the same way. Separate
   conditions that change the *project* (units, height, envelope — `project_modified` already
   flags this in principle) from those that change its *obligations* (fees, monitoring,
   reporting). Propose a coding scheme even if you cannot yet populate it.

**Be honest about the ceiling.** If the motions are not obtainable at scale, that is the
finding, and the memo should say what it would take rather than dressing up a partial result.

---

## 4. Memo: what predicts delay

**Folder**: `output/planning_commission_project/predicting_delay/`
**Question**: is delay predictable from what a developer could observe at first hearing —
especially in the right tail?

### The construction, already validated

A case heard more than once is one project the Commission did not finish with. Chain items by
`case_number` (upper-cased, whitespace stripped), order by `meeting_date`, and take:

- `hearings` — count of appearances
- `days` — first hearing to last
- `final_action` — the action on the last appearance

This gives **8,987 cases, of which 37.9% were heard more than once**. See `chains()` in
`code/commission_minutes_processing/analyze_corpus.py` — reuse it rather than rewriting.

### The stylised facts to explain

- **The median is stable at ~35 days** across every cohort from 1995 to 2020 — roughly the
  Commission's own meeting cycle. A routine continuance moves an item two or three meetings.
- **The tail is what moved.** The 90th percentile went from **147 days** (cases first heard
  1995–99) to **360 days** (2015–19).
- Delay sorts on outcome: approved 28 days median, continued-indefinitely 84, no-action 116.
- **45% of all 16,199 items are a repeat appearance** of a case already heard.

The interesting quantity is therefore **variance, not the mean**. For a developer with
financing costs, the width of the distribution is what matters, and it widened while the centre
did not.

### What to run

Simple regressions are fine — this is a first pass, not the paper's identification strategy.

1. **OLS of `log(days + 1)` on what was observable at first hearing**: `request_type`,
   `type_district`, `assessor_block` (or a coarser geography — 2,557 blocks is too many for
   fixed effects; consider the top ~50 plus an "other", or `supervisor_district` if you can
   get it from the parcel join), year of first hearing, `preliminary_recommendation_category`,
   speaker counts, and whether the first hearing was itself a continuance.
2. **The tail is the point.** Also run a **quantile regression at the 90th percentile**, or a
   linear probability model for `days > 180` / `days > 365`. If the covariates predict the
   median but not the tail, that is the headline finding and it is a substantive one: the
   unpredictable part of delay is exactly what a developer cannot plan around.
3. **Report the residual variance explicitly.** The fitted value is expected delay conditional
   on observables; the residual is the part that cannot be forecast. That residual is the first
   available proxy for the *cost of uncertainty*, which is what the paper is ultimately about.
4. **`preliminary_recommendation_category` deserves its own cut.** It is issued *before* the
   hearing and is populated on 78.7% of items — a public, dated signal of the expected outcome.
   Does a case where staff recommended approval move faster? Does a departure from staff
   predict a long tail?

### Cautions to state in the memo

- **Right-censoring.** A case first heard in 2024 cannot show a three-year tail. Either drop
  recent cohorts or handle it explicitly; do not report the apparent recent decline as real.
- **This measures hearing-to-hearing time, not the entitlement clock.** It starts when an item
  first reaches an agenda, already well downstream of filing. The pre-hearing wait is invisible
  here.
- **Case-number chains are an imperfect project identifier.** A project can change case number
  on re-filing, and one case number can cover several agenda sub-items heard the same day
  (`1a`, `1b`). The chains are a **lower bound** on how often projects return.
- **No causal claims.** These are descriptive predictive regressions. Say so.

---

## 5. Conventions — read `CLAUDE.md`, and note these in particular

- **Never hand-place a number a script could compute.** Every figure and table in the existing
  memos is generated. Follow that: write the analysis script, have it emit
  `tables/<name>_tables.tex`, and `\input` it.
- **LaTeX**: TeX is installed but not on the PATH in non-interactive shells —
  `export PATH="/Library/TeX/texbin:$PATH"`. Run `pdflatex` twice. Then check the log for
  `Overfull \hbox ... in alignment`, which means a table is past the margin; fix with
  `\resizebox{\textwidth}{!}{...}`. Compile before claiming a memo is done.
- **Figures**: faint raw series behind a bold smoothed line for time series; state the
  smoothing window; prefer a calendar-time window. **Draw gaps rather than interpolating** —
  see the coverage gaps in §0.
- **Never commit data files.** `labels.db`, `bakeoff/` and `api_keys/` are git-ignored; the
  Dropbox extraction output is outside the repo.
- **Report in-sample and out-of-sample separately** where it applies, and say which is which.
- If a number surprises you, **check the source block before assuming the extraction is
  wrong.** During the gold work, 52% of apparent model errors turned out to be label errors.

## 6. When you are done

Update `progress_log.md` at the repo root (newest entry first, after the `# Progress Log`
header) using the format the existing entries use, and update `output/README.md` so the three
new memos are findable. Then commit — the repo's convention is direct commits to `main`.
