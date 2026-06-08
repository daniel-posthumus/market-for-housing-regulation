# Minutes Processing Pipeline — Code Review & Hand-Label Audit

*Review only. Nothing in the pipeline or data was modified. All figures below come
from read-only analysis of the repo on 2026-06-05 (`/tmp/audit_labels.py`).*

> **Status update (2026-06-08).** Several findings have since been actioned:
> - **#1 train path** — fixed: `train.py` now reads `tagged/training/training.txt` (with a clear error if absent).
> - **#2 coverage** — the builder now consolidates **all years 1998–2014** (269 paired examples, up from 159; per-year pairing in `tagged/training/logs/`).
> - **Artifact** — canonical consolidated file is `tagged/training/training.txt`; the stale 1998-only `tagged/training.jsonl` is orphaned (safe to delete).
> - **B2 typos** — 26 obvious key typos fixed across `*_labeled.json`; the builder also self-heals via a `KEY_ALIASES` map.
> - **#6 metric** — replaced `valid_json_ratio` with `field_accuracy` (+ `exact_record_ratio`, `parseable_ratio`), now shared via `extraction_common.py`.
> - **#7 capacity** — actioned: found+fixed a real bug (86% of completions and 21% of inputs were being truncated by the old 256/512 caps → raised to 1024); default base model now `flan-t5-base` with an optional LoRA path (`MINUTES_USE_LORA`); proper 80/10/10 train/val/**held-out test** split with a final test report; new `llm_extract.py` for few-shot, schema-constrained extraction (HF or Anthropic backend) evaluated on the same held-out split.
> - **#8** — `labels_stringified.py` retired; stale `tagged/training.jsonl` deleted.
> - **#4** (hardcoded inference file) left as-is per author (testing on one date), though its generation caps were raised to match.

## TL;DR

Your hunch is **half right, and the better half is worse than you think.** The
*reading* in the hand-labels is mostly fine — addresses, case numbers, descriptions,
and aye-lists are usually correct. What is actually broken is **schema discipline and
pipeline plumbing**:

1. **The model trains on ~half your labels, and only through 2004.** The consolidated
   training file covers **1997–2004 (159 records)**; the 2005–2014 labels you hand-made
   last summer (~150 records) were never consolidated.
2. **`train.py` points at a file that doesn't exist.** It reads
   `tagged/training.txt`; the builder writes `tagged/training/training.txt`.
3. **There is no fixed schema.** Across 319 labelled records there are **~90 distinct
   field names**, including **34 typo'd / non-canonical variants** (`aciton`,
   `caes_number`, `spekaers`, `tyope_district`, …). 2.8% of records have no
   `case_number` key at all and are silently dropped by the matcher.
4. **Key fields were abandoned mid-project.** `vote` appears in only **19%** of records
   (essentially 1998 only); `noes` in 38%; `type_district` is **absent for all of 2014**.

So: you do **not** need to re-read every document by hand. You need a **fixed schema, a
key-normalization + re-consolidation pass, and a few path fixes**, then a targeted
spot-check. Details and a prioritized fix list below.

---

## Part A — Code review

Files: `parse_sf_meeting_minutes.py`, `training_sample_create.py`, `labels_stringified.py`,
`train.py`, `inference.py`, `data_collect.py`, and the scrapers (scrapers covered
separately in the scraping work).

### A1. Pipeline plumbing bugs (verified)

| # | Issue | Evidence | Severity |
|---|---|---|---|
| 1 | **Train input path mismatch.** `train.py:59` loads `raw / "training.txt"` where `raw = data/"tagged"`, i.e. `tagged/training.txt`. But `training_sample_create.py:34` writes `train_dir/"training.txt"` = `tagged/training/training.txt`. `tagged/training.txt` does **not exist** (only a stale `tagged/training.jsonl`, 69 rows). | `ls tagged/` → no `training.txt`; `train.py` would `FileNotFoundError`. | **High** |
| 2 | **Only 1997–2004 consolidated.** `tagged/training/training.txt` = 159 records, case-year distribution 1997:14, 1998:64, 1999:4, 2000:16, 2001:20, 2002:16, 2003:23, 2004:1. Diagnostics in `tagged/training/logs/` stop at `diagnostics_2004.json`. The 2005–2014 `*_labeled.json` (≈150 records) are never built in. | `wc -l training/training.txt`; `ls logs/`. | **High** |
| 3 | **Two competing consolidated artifacts.** `tagged/training.jsonl` (69 rows, 1998-only) and `tagged/training/training.txt` (159 rows). Unclear which is canonical; `train.py` references neither correctly. | file listing. | Med |
| 4 | **`inference.py` runs on one hardcoded file.** `inference.py:97` `target = tag_dir/"2004"/"October_14_2004.txt"  # adjust as needed`, and appends to `structured_data.jsonl`. There is no loop over the 633 tagged files, so `structured_data.jsonl` holds ~21 rows. To process the corpus you must hand-edit the source per file. | `inference.py:95-97`. | **High** (blocks scaling) |
| 5 | **Strict key matching drops typo'd records.** `training_sample_create.py` builds each example from a fixed required-field list and `r["case_number"]`-style access. The 9 records (2.8%) whose key is `caes_number` / `case_+number` / `case number` get an empty case number → fail to pair with a source block. | key-frequency scan. | Med |
| 6 | **Evaluation metric is syntactic only.** `train.py:101` `metric_for_best_model="valid_json_ratio"` — it measures whether output *parses as JSON*, not whether the *fields are correct*. A model that returns well-formed but wrong JSON scores 100%. | `train.py:110-124`. | Med |
| 7 | **Model/Data capacity mismatch.** `google/flan-t5-small` (80M params), 512-token input cap, asked to emit a 15-key JSON object, trained on 159 examples (≈143 after the 90/10 split). Long agenda items exceed 512 tokens and are truncated before the ACTION/AYES lines. | `train.py` config. | Med |
| 8 | **`labels_stringified.py` is a redundant 70-row inline copy** of 1998 labels with its own inconsistencies (`aciton`, `Speakers`). It duplicates `1998_labeled.json` and should be retired. | file contents. | Low |

### A2. Parsing & robustness (`parse_sf_meeting_minutes.py`)

- **Block-tagging is decent but silent on failure.** The `<<Project Start>>…End>>`
  tagging produced **1 fully-empty tagged file** (`tagged/1998/02-12-1998.txt`) out of 633
  — i.e. the parser emitted markers with no content and didn't warn. (Note: an earlier
  quick `grep` suggested ~15 empties; that was a shell artifact from **filenames
  containing spaces** — see next point. The true count is 1.)
- **Odd output filenames.** Several tagged files have spaces / stray underscores in their
  names, e.g. `tagged/2000/August_3 _____2000.txt`, from date-slug fallback when the
  header date didn't parse. Harmless but messy and breaks naive shell globbing.
- **Hardcoded S3 archive page-IDs** for 1998–2014 and **dependence on `div#ctl00_content_Screen`**
  and the `\d_\d{1,2}_\d{2}` anchor pattern make it brittle to site changes (addressed in
  the new scraper).
- The case-code regex `\b(?:\d{2}|\d{4})\.\d{3,}(?:[A-Z0-9/]+)?\b` is reasonable for the
  `98.226D` / `2004.1106D` eras but does **not** match the modern `2022-001764CUA`
  (dash) format — fine for 1998–2014 HTML, but won't carry into the PDF era.

### A3. Pre-existing issues outside the minutes pipeline (flagged, not fixed)
- `cleaning_code/08_acs_pull.py` hard-codes a **Census API key** — rotate + move to `.env`.
- `cleaning_code/07_llm_regulations.py` reads from a separate `~/SIEPR-HOUSING-POLICY`
  project rather than this repo's `data/llm_regulatory_measurement/`.
- `analysis_code/01_election_land_use_scatters.py:15` builds a malformed path
  (`f'{clean_data}master_county_level'` — missing `/` and `.csv`).

---

## Part B — Hand-label audit (319 records, 1998–2014)

### B1. Per-year counts and schema drift

`vote` and `noes` columns show when fields were quietly dropped. Numbers are
*records containing that key*:

```
year  n   case_n proj_addr lot  block descr type_d ty_descr speak action ayes noes vote
1998  69    69     62      62    60    68    57     51      60    67    62   61   61
1999  11    11     11      11    11    11     9     10      10    11    10   10    0
2000  11    11      8       8     7    11     6      4      10    11    10   10    0
2001  13    12     12      12    12    13    11     10      11    13    10   10    0
2002  15    15     11      15    15    15    13     12      15    15    13    2    0
2003  23    22     20      21    21    23    16     15      22    23    18    3    0
2004  25    23     21      22    22    24    20     12      24    24    17    1    0
2005  13    12     13      12    12    13    12     13      13    13    12    0    0
2006  15    14     12      11    11    15     9      9      14    15    13    1    0
2007  19    19     18      18    18    19    11     14      18    19    15    5    0
2008  20    20     18      18    18    20    15     18      20    20    19    3    0
2009  22    22     17      18    18    22    10     11      20    21    14    2    0
2010  14    14      9       8     9    13     8      9      14    14     9    3    0
2011  12    10     10      11    11    10     9     11      11    11     7    3    0
2012   7     7      7       7     7     7     7      7       0     3     0    0    0
2013  16    15     15      15    15    16     4      7      15    14    14    6    0
2014  14    14     10       4     7    14     0      0      13    14    13    0    0
```

Read this table top to bottom and the project's drift is obvious:
- **`vote` was recorded in 1998 (61 rows) and then abandoned** — 0 rows in every later
  year. Net: only **19%** of all records have a vote tally, even though the raw minutes
  always print one. This is the single most damaging gap for any voting analysis.
- **`noes` coverage collapses** to 0–6 per year after 1998 (38% overall).
- **2012** has no `speakers`/`ayes` and almost no `action`.
- **2014** dropped `type_district` and `type_district_descr` entirely, and `lot`/`block`
  coverage fell to 4/14 and 7/14.
- **1998 is 22% of all labels** and **40% of the 159-row consolidated set** — the model
  sees one 7-member commission far more than any other.

### B2. Field-name chaos (no fixed schema)

Across the corpus there are ~90 distinct keys. Genuine typos / non-canonical variants
(34 of them), any of which silently breaks a `record[key]` lookup:

```
aciton, caes_number, case_+number, case number, spekaers, speaker_statemetns,
tyope_district, tpye_district, type_disrict, type_distrct, type_district_, type_district,
prjoect_address, porject_descr, project descr, project-descr, asessor_block,
assessor_blocks, heigh_and_bulk_district, district_type, district_type_descr,
preliminary_recmomendation, preliminary_recommendaiton, preliminar_recommendation,
prleiminary_recommendation, special_ues_district, zoninig_district, aayes, nayes,
Action, Speakers, first_action_ayes, first_action_noes, first_action_absent
```

- **10.0%** of records carry ≥1 non-canonical key.
- **2.8%** have no `case_number` key (typo'd) → unmatchable by the current builder.
- Competing schemas coexist: `type_district` vs `zoning_district` vs `district_type`;
  multi-action items use both `_2/_3` suffixes **and** a parallel `first_action_*` scheme.

### B3. A real coding-rule inconsistency: `action` on continuances

For continuance items the labeller recorded the **proposed** continuance instead of the
**disposition**. Verified examples (label `action` → source `ACTION:` line):

| case | labelled `action` | source `ACTION:` |
|---|---|---|
| 98.226D | `proposed for continuance to june 25, 1998` | `Continued as proposed` |
| 98.251C | `proposed for continuance to june 11, 1998` | `Continued as proposed` |
| 97.686C | `proposed for continuance to september 3, 1998` | `Continued as proposed` |

≥23 records (7.2%) show this pattern. It's defensible to keep the target date, but then
it should live in a separate `continued_to` field — folding it into `action` means the
`action` column mixes *dispositions* ("Approved", "Continued as proposed", "Withdrawn")
with *proposals*, which will confuse any downstream classifier.

### B4. What I could NOT cleanly measure (honesty note)

I tried to score aye-lists against the source blocks automatically. The naive number
(~24%) is **not trustworthy**: the same case number often appears twice in a meeting
(once on the continuance calendar, once when heard), and the raw AYES line frequently
runs straight into `…MillsRESOLUTION NO.: 14633` with no delimiter. Both confound an
automatic set-comparison. Spot-checking the residual, **most apparent "mismatches" are
my parser's fault, not the label's** — the labeller generally separated names correctly.
A proper aye-accuracy number needs the block-deduplication fix (B/A5) first.

### B5. Verdict

- **Reading quality: fine.** Addresses, descriptions, case numbers, district codes, and
  aye-lists are mostly accurate where present. You did not "read the documents badly."
- **Schema & pipeline quality: poor, and that's what's actually hurting you.** No fixed
  schema → typos and dropped fields; `vote`/`noes` abandoned; half the labels never
  consolidated; the trainer can't even find its input file; inference doesn't scale past
  one file.
- **Recommendation: do NOT re-hand-label from scratch.** ~85% of the *content* is
  salvageable. Re-do the **schema and consolidation**, coerce the existing labels into it,
  back-fill `vote`/`noes` (and 2014's districts) which are trivially present in the raw
  text, and spot-check 10% against source.

---

## Part C — Prioritized fix list (recommendations only)

**P0 — make the pipeline run end-to-end again**
1. Fix the train input path: point `train.py` at `tagged/training/training.txt` (or have
   the builder write to the path `train.py` reads). Delete/retire the stale
   `tagged/training.jsonl` and `labels_stringified.py`.
2. Make `inference.py` take a path/glob argument and loop over all tagged files, writing
   one row per project block; truncate-or-chunk inputs >512 tokens instead of silently
   dropping the tail.

**P1 — define and enforce one schema**
3. Write a single JSON schema (or a `pydantic`/`dataclass`) for a project record with an
   explicit field list and types: `case_number, project_address, lot_number,
   assessor_block, height_and_bulk_district, type_district, type_district_descr,
   project_descr, request_type, preliminary_recommendation, speakers[],
   speaker_stances[], action (disposition enum), continued_to, ayes[], noes[], absent[],
   recused[], excused[], vote, resolution_or_motion_no, meeting_date, source_file`.
4. Add a **key-normalization map** (`aciton→action`, `caes_number→case_number`, …) and a
   validator that *rejects* unknown keys, so typos surface instead of being dropped.

**P2 — re-consolidate and back-fill**
5. Re-run `training_sample_create.py` over **all** years 1998–2014 after coercing each
   `*_labeled.json` through the schema; assert the diagnostics cover every year.
6. Back-fill `vote`, `noes`, `absent`, and 2014's district fields from the raw text —
   they are present in every era (see `minutes_data_availability.md`); this is a parse,
   not a re-read.
7. De-duplicate same-case appearances (continuance vs. heard) by keeping the block that
   contains a real disposition + vote, not the continuance stub.

**P3 — quality + scale**
8. Replace `valid_json_ratio` with a **field-level accuracy** metric (exact-match per key
   on a held-out, source-verified set).
9. Reconsider the extractor: at 159–300 examples, a larger base (`flan-t5-base`, the LoRA
   path in `scratch_code/`) or a few-shot LLM-extraction prompt with the schema enforced
   will likely beat `flan-t5-small`; treat the hand-labels as an eval set, not just train.
10. Drop the empty `tagged/1998/02-12-1998.txt`, regularize tagged filenames, and add a
    parser warning when a tagged file has zero project blocks.
