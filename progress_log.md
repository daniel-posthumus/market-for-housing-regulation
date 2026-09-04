# Progress Log

## 2026-09-04 — Meeting-level extraction validated over four rounds, run corpus-wide, and closed out

**Goal**: Finish the meeting-level layer: validate the extraction against hand labels until
the accuracy claim is honest, run it over the whole corpus, and document it. Then return to
item-level labeling.

**What was done**:
- Built the meeting-level gold set in **four rounds** (126 documents, 135 meetings), each
  scored with the extraction rules frozen and SHA-verified beforehand. Out-of-sample field
  agreement rose **92.1% → 95.3% → 96.0%** across rounds 2–4.
- Round 4 was the first with **machine-placed boundaries** (drawn weighted by measured
  anomaly rate, 20.9% vs 15.1% uniform) — the configuration that runs unattended.
- Found and fixed the **window-anchoring gap** that three rounds of hand-marking had hidden:
  the date stage anchors on the page title (correct for dates), but meeting *attributes* live
  in a body header ~230 lines below. `snap_to_header` cut unlabelled-meeting anomalies from
  84.5%→1.4% (time), 86.0%→4.5% (venue), 72.9%→2.0% (type).
- Fixed the early-era truncation the user spotted: `STAFF`/`IN ATTENDANCE:` and
  `THE`/`MEETING WAS CALLED TO ORDER` wrap mid-phrase, and the capture followed only four
  wrapped lines against staff rolls of twenty names.
- Ran `extract_all_meetings.py` over the corpus: **1,179 meetings from 1,086 documents,
  1998–2026**, written to `meetings_all.csv` + a `meetings_all` table.
- Added `plot_extraction_accuracy.py` (accuracy by round + error by era) and
  `plot_meeting_timeseries.py` (staff and absence over time, banded by inferred presidency,
  remote era shaded).
- Rewrote `meeting_level_info.tex` (15 pp) end-to-end; created `CLAUDE.md`.

**Key decisions / findings**:
- **Dates are done: 1,621/1,621 items correct across all four rounds**, never re-tuned.
- **No LLM anywhere** in the date or meeting pipeline — all deterministic, ~9s over 724 pages.
- Error is concentrated, not diffuse: **3.8% from 2002 onward, 11.1% for 1999–2001 (5.7%
  excluding `staff`)**. Every round's failures traced to a handful of named layout quirks.
- **Absence during remote hearings was 4.9% vs 9.1% in person** — the clearest pattern found.
- Presidency terms inferred from who called meetings to order: 19 terms, ~1 year each.
  Absence varies 2.0–15.4% by term but tracks the **era, not the chair** (Fong's two terms
  differ by more than many presidents differ from each other) — flagged as needing a
  specification that separates chair from period.
- Left the two Lees **visibly unresolved** on 192 meetings rather than guessing; an earlier
  tie-break had manufactured a 9%-vs-0.4% absence gap between them.
- **2018 is a corpus hole** (2 documents vs 27–44 for neighbours) — a scraping gap, drawn as
  a break rather than interpolated.

**Next steps**:
- **Return to item-level hand-labeling** — 80 `review` items, then the 145
  representativeness draws; the learning curve has never been run at full size.
- Fill the 2018 scrape gap before any year-on-year series crosses it.
- Optional: consistency pass on the gold labels' name spellings (~1.5 pts of measured
  accuracy, no code change); filter the 9 landing-page documents from denominators.

**Files touched**:
- `meeting_headers.py`, `assign_meeting_dates.py` — modified (snap_to_header, staff endpoint,
  stop markers, name reconciliation, joint-meeting presiding rule)
- `extract_all_meetings.py`, `draw_validation_sample.py`, `plot_extraction_accuracy.py`,
  `plot_meeting_timeseries.py` — created
- `date_boundary_app/{app.py,static/*,templates/*}` — modified (wrapped-date detection, range
  selection, meeting-level labelling view)
- `output/planning_commission_project/meeting_level_info.tex` (+ .pdf), `extraction_accuracy.*`,
  `meeting_timeseries.*` — created/rewritten
- `CLAUDE.md` — created; `.gitignore` — modified (LaTeX rules, generated CSVs)

## 2026-08-30 (later) — Meeting-level labelling pass and an `output/` that makes sense

**Goal**: Stand up a meeting-level pass alongside the date-boundary work, and reorganize
`output/` so nine one-shot probes stop sitting as siblings of the two real project lines.

**What was done**:
- Wrote `meeting_headers.py`: cuts the ±15 **non-blank** lines around each marked boundary and
  pre-fills 12 meeting-level fields (type, scheduled and gavel times, presiding, room, roll
  call, staff, joint body, previous meeting's adjournment).
- Added a `/meetings` view to the boundary app — window left, form right, ⌘/Ctrl+Enter to
  save and advance — plus CSV export. Pilot is **81 meetings**: the 80 hand-marked boundaries
  plus the joint session the date stage found, carried with `origin='detected'`.
- Pre-fill coverage on 81: meeting_type 81, scheduled_time 81, location 81, present 78, staff
  76, called_to_order 74, presiding 74, absent 36, joint_body 4, adjournment 3.
- Fixed three pre-fill bugs found by inspection: roll calls bleeding past their label (regex
  lookahead → line-based capture with stop markers), **cross-meeting bleed** (the window
  straddles two meetings, so `ABSENT: Martin` from the previous item's vote was read as this
  meeting's absences — now everything but adjournment is read from the date line down), and a
  wrapped stop marker (`THE MEETING WAS / CALLED TO ORDER`).
- Sub-agent reorganized `output/` into three research lines + archive: `bay_area_recon/`
  (the nine probes, moved together so sibling path constants survive) with `_source_data/`
  for raw third-party CSVs; `demand_memo.pdf` rejoined `demand_estimation/report/`;
  `operationalization_memo.pdf` joined the theory line; `OUTPUT_INVENTORY.md` filed to
  `_archive/`. New `README.md` at `output/` and in `bay_area_recon/`.

**Key decisions / findings**:
- A meeting is not an item: time, type, room, roll call and staff are properties of the
  hearing shared by every item heard at it, so they are recorded once and joined on the date.
- The window's **leading** lines are the previous meeting's tail — useful (adjournment) and
  dangerous (its roll call), which is why the date line splits the two halves.
- Four joint sittings in the pilot: with the Redevelopment Agency Commission (1998),
  Building Inspection (2007, 2018) and Historic Preservation (2023). The 2018 one is headed
  "Special Meeting" and is only detectable as joint from "PLANNING COMMISSION / AND / BUILDING
  INSPECTION COMMISSION".
- Reorg verification: all 14 touched scripts byte-compile, 12 module-level path constants
  resolve, 0 surviving pre-move paths, 0 new dangling references.

**Next steps**:
- Work the 81-meeting pilot in `/meetings`; then decide whether to extend meeting-level
  labelling to all 818 detected meetings.
- Still open from earlier: 21 unmarked gold documents, the 42 date disagreements.

**Files touched**:
- `meeting_headers.py` — created; `date_boundary_app/{app.py,templates/meetings.html,static/meetings.js,static/style.css,templates/index.html,README.md}` — modified/created
- `output/**` — reorganized into `bay_area_recon/` + two live lines + `_archive/`; two new READMEs
- `jurisdiction_mappings.py`, `build_hcd_panel.py`, `civicplus_depth_probe/probe.py`, 5 `memo.tex`, `STRUCTURE.md`, `README.md` — path/reference updates
- `.gitignore` — modified (`.DS_Store`, `meetings_pilot.csv`)

## 2026-08-30 — Gold-validated date inference, schema drops meeting_date, output/ reorganized into per-directory memos

**Goal**: Review the hand-marked meeting-boundary gold set, harden the date-inference stage
against it, document the method, and clean up `output/`.

**What was done**:
- Reviewed all **76 hand-marked documents** against independent structural signals (roll call,
  adjournment, "Back to Top" anchors, room location). Only 3 flags, and 2 were the machine's:
  the hand labels were right where they disagreed with the detector.
- Added the corroboration gate the review implied — a header needs a roll call within 800
  chars after **or** a room/location within 300 chars before. Dropped 21 of 824 candidates and
  moved **47 items** onto correct dates (killed the "Rules Committee meeting this coming
  Monday, February 10, 2003 at 2:30 p.m." false positive and its 2003-01-23 twin).
- Replaced same-date dedup with a distance rule (3,000 chars), so a genuine second meeting on
  one day survives — the 1998-01-15 joint Planning/Redevelopment Agency session in Room 404.
- Added **positional** precision/recall to the scorer; set-level agreement cannot see a missed
  same-day boundary. Final gold scores: boundary P/R **1.000**, positional P/R **0.988/1.000**,
  item date accuracy **1.000** on 988 items.
- Verified all **280 modern PDFs**: filename date == in-document header, 280/280, zero
  exceptions. PDF filenames adopted as authoritative.
- Fixed `raw/2000/20000203-documentid=32.pdf.html` — a PDF saved under an `.html` name, parsed
  as HTML into one 23 KB block of binary. Readers now route on magic bytes; that meeting's
  **15 items were restored**. Only such file in the corpus.
- Dropped `meeting_date` from `SCHEMA` (28 fields now): it is a property of the meeting, not
  the item. Stripped from 23,043 stored labels, preserved 42 disagreeing hand-typed dates to
  `date_field_disagreements.csv`, and attached at export time from `items.meeting_date`.
- Wrote `output/planning_commission_project/date_boundary_inference.tex` (4 pp: method,
  validation, error rates) and a `memo.tex` in **all 11** `output/` subdirectories (3 via
  sub-agents), plus `OUTPUT_INVENTORY.md` from a fourth.
- Deleted `output/DATA_STATUS.md`, `output/final_recon_bundle_report.md`, and
  `planning_commission_project/graphics/`; folded their content into the memos that replace them.
- **Reorganized `output/`** from 11 flat siblings into 3 lines + archive. The nine June-2026
  Bay Area recon probes moved together into `output/bay_area_recon/` (moving them as a group
  preserves every `HERE.parent / "<sibling>"` constant); the two raw third-party releases
  (`nzlud_muni.csv`, `hcd.csv`) moved out of the reports folder into
  `bay_area_recon/_source_data/`; `demand_memo.pdf` rejoined `demand_estimation/report/`;
  `operationalization_memo.pdf` moved beside the toy model it operationalizes;
  `OUTPUT_INVENTORY.md` was filed into `output/_archive/` as the audit record it is.
  Added `output/README.md` and `output/bay_area_recon/README.md`.

**Key decisions / findings**:
- **No LLM is used for date inference.** All 724 pages pass deterministically and gold
  agreement is exact — paying for inference would buy nothing and make the stage
  non-reproducible. Revisit only for a new jurisdiction, piloted on its own gold sample first.
- `raw/2007/index.aspx-page=1340.html` is short because it is a real closed-session special
  meeting (Planning Director search), not a scrape failure.
- Sub-agents surfaced stale numbers across the recon probes: `preperiod` 17/25 → **15/25**,
  `archive_depth` reports understate their own CSV (64/32 vs 68/36), and `consolidate.py`'s
  "verified" filter now wrongly rejects API-verified rows.

**Next steps**:
- Mark the remaining 21 gold documents (both previously-broken ones are now readable).
- Consider adding the missed 1998-01-15 joint-session boundary to gold.
- Work the 42 date disagreements — several look like labels attached to the wrong block.
- No LaTeX toolchain on this machine: the 12 `.tex` files pass a structural check but are
  uncompiled.

**Files touched**:
- `assign_meeting_dates.py` — modified (corroboration gate, same-day meetings, PDF sniffing)
- `parse_sf_meeting_minutes.py`, `rebuild_review_db.py` — modified (`read_page_text` routes on magic bytes)
- `extraction_common.py`, `autoextract.py`, `labeling_app/app.py` — modified (meeting_date dropped; export joins it)
- `date_boundary_app/{app.py,README.md}` — modified (positional scoring, PDF sniffing)
- `output/*/memo.tex` — created (11 files); `output/planning_commission_project/date_boundary_inference.tex` — created
- `output/DATA_STATUS.md`, `output/final_recon_bundle_report.md`, `planning_commission_project/graphics/` — deleted
- `output/planning_commission_project/labeling_rules.md`, `STRUCTURE.md`, `README.md` — modified
- `output/` reorganized (see above): 9 probe dirs → `output/bay_area_recon/`; path constants fixed in
  `jurisdiction_mappings.py` (+1 `.parent`), `build_hcd_panel.py` (`_source_data/hcd.csv`),
  `civicplus_depth_probe/probe.py` (hard-coded `/Users/danpost/…` → `Path(__file__).parent`);
  `output/README.md`, `output/bay_area_recon/README.md` — created
- `date_field_disagreements.csv` — created; `labels.db` — 47 dates + 23,043 label records updated

## 2026-08-29 — Meeting dates become their own stage: corpus-wide date repair + boundary gold-standard app

**Goal**: Get back to hand-labeling; instead found that ~29% of HTML-era items carried the
wrong `meeting_date`. Fix it automatically, move date assignment out of the parser into its
own stage, and build a way to validate that stage against hand-marked truth.

**What was done**:
- Set up the labeling app (`labels.db` already on the 29-field schema; 117 done / 82 review /
  179 flagged / 22,651 todo) and repointed `paths.py` at this machine's Dropbox root.
- Diagnosed the date bug: the HTML parser split pages into meetings via `<a name="6_4_98">`
  anchors, present on only **3 of 724** archive pages, so every other page stamped its FIRST
  date on all blocks. 1999 and 2000 had **12 distinct dates each** (one per monthly file)
  against ~40–48 real meetings.
- Wrote `assign_meeting_dates.py` — a stage that never re-derives block boundaries. It folds
  page and block text, locates each block by content via an order-preserving
  longest-increasing-subsequence alignment (multi-pass, since anchored pages emit sections out
  of document order), detects meeting headers, and reads off the date each block falls under.
- Applied it: **1,890 item dates corrected**, 1,873 label records synced. 1998 17→45 distinct
  dates, 1999 12→38, 2000 12→35; 2001+ essentially unchanged. 99.1% of items now land on a
  Thursday, the remainder on archive-declared special meetings.
- Built `date_boundary_app/` (Flask, :5006) — click the line where a meeting starts, pick its
  date; `--score` compares gold marks to the pipeline at both boundary and block level.
  Queue covers 1,086 documents (HTML + PDF), sample = one typical month per year = 97 docs.
- Deleted `fix_meeting_dates.py`: its block-offset logic mirrored an outdated boundary rule
  and would have written *wrong* dates (it put 97.499Q on 06-25; truth is 06-18).

**Key decisions / findings**:
- Date assignment is now **decoupled from parsing by construction** — the only coupling is
  "here is the text of a block", so changing boundary rules can't silently misalign dates.
- Two signals carry the whole corpus deterministically: a header date must be weekday-adjacent
  *and* followed by a gavel time (kills prose like "Saturday, June 16, 2001, at 9:00"), plus
  the archive's own title date on per-meeting pages. **All 724 pages validate — no LLM tagging
  was needed**, so none is wired up.
- Non-Thursday dates are only flagged when the archive doesn't corroborate them; the 139
  Mon/Tue/Fri items are real special meetings.
- 42 existing hand-labels carry a date disagreeing with their page (one is a typo,
  `20001-09-07`); these were **left alone**, not overwritten, and are listed for review.

**Next steps**:
- Mark the 97-document sample in the boundary app, then `python app.py --score --csv`.
- Resume labeling: 82 `review` items first, then the 145 `[sample: representativeness]` draws.
- Decide what to do with the 42 label/page date disagreements — several look like labels
  attached to the wrong block by the content-matching recovery.

**Files touched**:
- `code/commission_minutes_processing/assign_meeting_dates.py` — created (the date stage)
- `code/commission_minutes_processing/date_boundary_app/{app.py,templates,static,README.md}` — created
- `code/commission_minutes_processing/fix_meeting_dates.py` — deleted (superseded; wrote wrong dates)
- `code/commission_minutes_processing/paths.py` — modified (data root → this machine's Dropbox)
- `README.md`, `labeling_app/README.md` — modified (date stage documented; stale 35-field/path fixes)
- `.gitignore` — modified (date_gold.db, audit CSVs)
- `labels.db` — 1,890 item dates + 1,873 label records corrected (backup: `labels.db.predateassign.bak`)

## 2026-06-18 — Finish the SF minutes pipeline: modern-era parsing, corpus repair, corpus-wide extraction, label-QA, and the SF coding manual

**Goal**: Push the (Guren-approved) SF Planning Commission minutes pipeline toward "A+": parse the unparsed modern era, get extraction running corpus-wide with quality checks, tee up the existing-label fixes, and write the SF-specific labeling rules.

**What was done**:
- **Modern-era parser** (`parse_modern_minutes.py`): pdfplumber for 2018+ PDFs, direct read for 2015–17 text → the same `<<Project>>` tagged blocks; date-led ISO filenames; hardened `ingest.py`'s filename date-parser for them. Parsed 2015–2026 → ~325 meetings.
- **Corpus repair**: found **44 corrupt modern PDFs** (not `%PDF`). Added `scrape_minutes.py --repair` + `%PDF` validation on every download, and broadened the archive harvester to key on anchor text "Minutes" across all three hosts (was seeing 129 of 295 minutes links). Repaired all 44, re-parsed + re-ingested. Corpus now complete **1998–2026 with raw↔tagged parity; labels.db = 16,100 items** (9,081 HTML + 7,019 modern).
- **Corpus-wide extraction** (`run_extraction.py`, supersedes the single-file/old-schema `inference.py`): pluggable engine (heuristic/hf/anthropic), schema-aligned via `extraction_common`, resumable, with **periodic QA checks** (coverage, distributions, accuracy-vs-gold). Ran the **heuristic v0 over all 16,100 blocks (~11s)** → `structured_data.jsonl` + `extracted_results.csv` + `extraction_qa_report.md`.
- **Learning-curve harness** (`learning_curve.py`) + refactored `train.py` into importable functions it reuses; smoke-tested with a real flan-t5-small fine-tune.
- **Label-QA gate** (`label_qa.py`): diffs each hand-label vs its source block; `--apply --backfill` ran on the real DB — **backfilled 405 labels, flagged 412 for confirmation, action='other' 351→76**, DB backed up.
- **Rare-class-first + year-balanced labeling queue** (`labeling_app/queue_order.py` + `app.py` + UI order selector); fixes the old chrono+LIMIT-5000 bug. Verified first 29 queued items span one-per-year 1998→2026.
- **SF coding manual** (`output/planning_commission_project/labeling_rules.md`): authoritative field-by-field SF rules (case-suffix→request_type map, the `action` disposition vocabulary incl. DR-specific, stance markers, recurring-case handling), cross-linked from the app README and review guide; **[QA]**-tagged rules map to `label_qa.py`.
- Installed `pdfplumber` + `flask`; pinned the Flask stack in `requirements.lock.txt` (flask already in `requirements.txt`).

**Key decisions / findings**:
- Model-based extraction is a *downstream* step (needs a trained model + clean labels); ran the free, schema-correct **heuristic as v0** now, with the same runner ready to swap to T5/Anthropic via one flag.
- Heuristic floor vs gold: copy fields strong (case#/request_type 100%, assessor_block 95%, height 88%, address 70%); judgment/roll-call/free-text weak — but `action`/`noes`/`vote` numbers are depressed by *dirty gold* (pre-backfill) and exact-match scoring, not just the engine.
- Root cause of the 44 corrupt files: the harvester's host-specific regex missed older multi-host minutes links; fixed by anchor-text matching → self-healing scraper.
- Label target guidance unchanged: ~600 well-balanced labels (oversample rare classes, label the modern era); use the learning curve to find the plateau.

**Next steps**:
- Human-confirm the 412 flagged labels in the app (label to `labeling_rules.md`); copy fields are auto-trustable, focus eyes on action/votes + the 76 residual `other`.
- Re-run `run_extraction.py` after confirmation → trustworthy accuracy baseline.
- Then: export → `learning_curve.py` → train T5 → `run_extraction.py --engine hf` and compare to the heuristic floor. (Optional: patch heuristic modern `action` parsing; set up Anthropic for an LLM pass.)
- Reconcile minor PDF-stack lock drift (pdfplumber 0.11.9→0.11.10, pypdfium2, pdfminer) from this session's install if a full re-lock is wanted.

**Files touched**:
- `code/commission_minutes_processing/parse_modern_minutes.py` — created (2015+ parser)
- `code/commission_minutes_processing/run_extraction.py` — created (corpus-wide extraction + QA)
- `code/commission_minutes_processing/learning_curve.py` — created (how-many-labels curve)
- `code/commission_minutes_processing/label_qa.py` — created (label linter + backfill)
- `code/commission_minutes_processing/labeling_app/queue_order.py` — created (priority queue)
- `code/commission_minutes_processing/train.py` — modified (refactored into importable functions)
- `code/commission_minutes_processing/minutes_scraping/scrape_minutes.py` — modified (`--repair`, `%PDF` validation, anchor-text harvester)
- `code/commission_minutes_processing/labeling_app/{ingest.py,app.py,templates/index.html,static/app.js,static/style.css}` — modified (ISO dates; priority order + rare badge)
- `output/planning_commission_project/labeling_rules.md` — created (SF coding manual)
- `output/planning_commission_project/hand_label_review_guide.md`, `STRUCTURE.md`, `code/commission_minutes_processing/{minutes_scraping/README.md,labeling_app/README.md}` — modified (docs/cross-links)
- `requirements.lock.txt` — modified (Flask stack pins)
- Dropbox `…/san_francisco/{tagged/2015..2026, processed/structured_data.jsonl, extracted_results.csv, extraction_qa_report.md}` — created/updated (out of git); `labeling_app/labels.db` — backfilled (+ `.qa.bak` backup)
- memory `sf-minutes-pipeline-to-a-plus.md` — created/updated

## 2026-06-16 — Layer I demand-data pipeline (build + CoreLogic + crime), report, and Guren slides

**Goal**: Execute the demand-side data-collection brief — build, run, and document the full region-wide (9-county Bay Area) Layer I data pipeline — then a chain of follow-ups (gSSURGO spatial, 511 transit, CoreLogic price spine, crime, PUMS weights), plus a 10-slide deck for the Adam Guren meeting and a demand-estimation roadmap. (Session spanned 06-12→06-16.)

**What was done**:
- Built `demand_estimation/` from scratch: collectors (TIGER, LODES, ACS PUMS+tables, SSURGO tabular+spatial, CGS hazard, Gov-OPR zoning, CPAD/CDE/GTFS amenities, IRS migration, crime), `build.py` orchestration (BG↔jurisdiction crosswalk, job access, soil extract + BG areal aggregation, controls, PUMS, design matrix), manifest/paths/ArcGIS-pager/manual-stubs. ~1.5 GB pulled to Dropbox `data/demand/`, idempotent + sha256-checksummed.
- Follow-ups: gSSURGO spatial polygons via SDA WKT → `bg_soil_engineering.parquet` (5,184 BGs); 511 regional GTFS all-operator transit (4,590 BGs; fixed dead Caltrain feed → Trillium mirror + zip validation).
- CoreLogic/Cotality: wrote the Redivis SQL filter queries; merged the user's extracts (5.3M deeds + 2.28M parcels) → geocoded to BG (100% parcel match, 99.9% BG) via `corelogic.py`.
- Crime: SF + Oakland incident-level (Oakland geocoded via Census batch geocoder) + FBI CDE county-level for all 9 counties (api.data.gov key) → `bg_crime.parquet`, merged into controls.
- PUMS survey weights (WGTP/PWGTP) added → population-correct moments; fixed corelogic stub README provenance (BU → Stanford Redivis).
- `demand_data_report.tex` (18 pp, compiles clean): inventory, granularity table, per-source detail w/ integrated diagnostics + spatial choropleths, CoreLogic section, instrument-pipeline status, **new §8 demand-estimation roadmap**.
- `output/political_economic_housing_model/guren_meeting_slides.tex` — 10-slide Beamer deck (fiscal-federalism framing, two margins, reaction function, identification routed to Wollmann).

**Key decisions / findings**:
- Demand data is region-wide (data root), reusing the minutes pipeline's `paths.py` `DATA_ROOT`; not per-locality.
- Soil instrument has spatial variation independent of the price/income gradient (good for identification); SSURGO survey areas discovered at runtime (not all FIPS-aligned).
- CoreLogic "Property" = tax-assessor + characteristics (there is no separate Tax dataset); LLMA walled off by EULA. Census batch geocoder is flaky → hardened with small batches + retries.
- Data validated sane: CoreLogic median-price-by-year reproduces the Bay Area cycle; FBI crime rankings as expected; IRS shows post-COVID out-migration.
- **Demand is NOT estimated** — only the data/design-matrix is assembled (stated explicitly in report §8); user will estimate by hand.

**Next steps**:
- Estimate demand by hand per report §8: define product/market/shares → hedonic price/user-cost index → conditional logit → BLP, instrument price (soil + slope + Gandhi–Houde).
- RS Means cost schedule (the one manual blocker) → soil instrument's $ form; FRED 30-yr rate for owner user cost.
- Build the multi-jurisdiction τ_j (discretionary-review) panel + z̄_j integration for the Layer III regulation game (only SF minutes processed so far).

**Files touched**:
- `demand_estimation/` — created (collectors/, `build.py`, `corelogic.py`, `crime.py`, `manifest.py`, `demand_paths.py`, `util.py`, `arcgis.py`, `stubs.py`, `report/`)
- `demand_estimation/report/demand_data_report.tex` (+ stats/inventory/fill `.py` helpers, `figures/`, `.tex` fragments) — created (18-pp report)
- `output/political_economic_housing_model/guren_meeting_slides.tex` — created (10-slide deck)
- `STRUCTURE.md`, `requirements.txt`, `requirements.lock.txt`, `environment-notes.md`, `.gitignore` — modified (deps/docs/ignore for the demand pipeline)
- Dropbox `data/demand/` — created (~1.5 GB; out of git)

## 2026-06-09 — Multi-jurisdiction reconnaissance: census, 6 probes, and consolidated DATA_STATUS

**Goal**: Scope scaling the SF minutes/zoning pipeline to the whole Bay Area — map every locality's data sources and answer, end-to-end, whether the multi-jurisdiction panel is feasible (access, depth, spatial form, pre-period envelope, HCD treatment) — then consolidate into one authoritative status doc.

**What was done**:
- Restructured the data corpus to be per-locality (`meeting_minutes/<locality>/...`); `paths.py` gained `MFHR_LOCALITY` (default `san_francisco`); updated README/STRUCTURE/docstrings.
- Ran the **minutes platform pilot** (14 priority cities): platform classification, scraper pilot (civic-scraper limits found), and an additive `jurisdiction_mappings.py` synonym layer.
- Deployed a sub-agent for the **zoning-envelope assessment** (NZLUD coverage, code-host table, HCD panel); materialized **77 git-LFS pointer stubs** (~2.9 GB) in Dropbox from the local LFS cache (sha256-verified); ran the **WRLURI cross-check** (NZLUD `zri` rank-inverted vs WRLURI-2006, n=4).
- Built the **Bay Area locality census** (109 localities, 8 parallel per-county agents, GEOIDs from Census Gazetteer).
- Ran six probes: **Akamai/CivicPlus**, **archive-depth** (shallow), **API depth-read**, **zoning-data-form**, **pre-period envelope**, and the **final recon bundle** (CivicPlus depth, migration cliffs, HCD firm-up, pre-period verification).
- Wrote **`output/DATA_STATUS.md`** — the single authoritative status doc; superseded the census report with a redirect. Self-verified all 33 numbers against the CSVs.

**Key decisions / findings**:
- **Akamai "wall" is a myth** — only 3 of 109 sites bot-blocked (Fremont, Portola Valley, San Jose); 25 of 28 CivicPlus clean.
- **Agenda-trap is real and large** — year-dropdowns overstate minutes depth (Oakland 2000→**2014**, Santa Rosa 1999→**2016**); only minutes-type-verified years trusted.
- **~2016 panel is feasible**: 68/109 verified minutes-start, **36 reach ≤2016** (a floor; 41 `unknown` are JS-gated, not absent). Migration cliffs add deep history (Marin 2005, SCC 2008, San Jose 2005).
- Current zoning maps are scriptable (67/109 downloadable GIS + statewide Gov-OPR backstop). HCD treatment variable is clean/sourced (0–38mo exposure variation).
- Pre-period envelope cheap for **15/25** (corrected down from 17 after killing San Ramon/Daly City false positives). Recon is **complete**; the two remaining blockers are decisions, not data.

**Next steps**:
- Build the unblocked adapters: Granicus/Legistar (41 localities), CivicClerk OData (10), CivicPlus AgendaCenter (26), Municode (44)/CodePublishing (23)/eCode360 (14).
- **Gating quality step**: re-test extraction transfer (SF `autoextract` does NOT transfer) on real docs **with human review**, not unattended.
- Cheap recon finishers: 12 CivicPlus CIDs (browser session), 9 pre-period `none_found` (publisher version history), 8 prohousing dates (HCD tracker XLS).
- **Two open DECISIONS gate the rest**: (1) by-right vs. ministerial definition (the model's hinge → zoning extraction); (2) deep-history/ratchet investment (pre-2010 + San Jose Akamai + physical records).

**Files touched**:
- `code/commission_minutes_processing/paths.py`, `README.md`, `STRUCTURE.md`, `parse_sf_meeting_minutes.py`, `labeling_app/ingest.py` — modified (per-locality restructure)
- `output/DATA_STATUS.md` — created (authoritative consolidated status; supersedes census report)
- `output/minutes_platform_pilot/`, `zoning_envelope_project/`, `bay_area_census/`, `archive_depth_probe/`, `zoning_map_form_probe/`, `preperiod_envelope_probe/`, `hcd_preemption_panel/`, `migration_cliffs_probe/`, `civicplus_depth_probe/` — created (probe CSVs, reports, reproducible scripts, raw per-county provenance). *All nine now live under `output/bay_area_recon/` (moved 2026-08-30).*
- Dropbox `data/raw,clean,crosswalks,llm_regulatory_measurement/*` — 77 LFS stubs materialized to real data (not in git)
- memory `corpus-moved-to-dropbox.md` — updated (per-locality layout note)

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
