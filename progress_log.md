# Progress Log

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
