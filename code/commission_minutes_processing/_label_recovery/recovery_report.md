# Hand-label recovery from labels.db

- distinct human records recovered : **199**
- placed with confidence           : 145
- ambiguous placement (review)     : 54
- case number had no block         : 0
- human date ≠ matched block date  : 37 (flagged, not changed)
- years written                    : 1998–2014 (199 records across 17 files)

Source: `labels.db.qa.bak` (clean, pre-QA-backfill). Field values are the human's, verbatim; only blank `meeting_date` was filled from the matched block. Ambiguous rows are placed best-effort — check `recovery_provenance.csv` (sort by confidence). Any labels the original ingest overwrote before this DB existed cannot be recovered from here.

Review DB rebuilt → `labeling_app/labels.db.recovered`: 16100 blocks, 198 human labels attached (145 prelabeled / 54 flagged), 1 placement collisions resolved.
