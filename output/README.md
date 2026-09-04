# `output/`

Reports, memos, figures, and small derived tables. Bulk data lives on Dropbox
(`MFHR_DATA_ROOT`), not here.

**Each project line carries a standing `memo.tex`** — an edit-in-place summary of what it
is, what is in it, how each artifact was produced, and what is still open. Those memos, not
this file, are the description of the work; this file only says where to look.
`planning_commission_project/` and `political_economic_housing_model/` each hold their own;
`bay_area_recon/` has **one memo at its root** (`bay_area_recon/memo.tex`) covering all nine
probes, not one per probe directory.

```
output/
├── planning_commission_project/     # the SF minutes pipeline (LIVE)
├── political_economic_housing_model/ # the theory (LIVE)
├── bay_area_recon/                  # June-2026 Bay Area feasibility sprint (closed)
└── _archive/                        # superseded documents, kept for the record
```

## Where to start

- **`planning_commission_project/`** — the live project line. The five spec/reference docs
  the pipeline code links to by path: `labeling_rules.md` (the coding manual hand labels are
  graded against), `data_infrastructure.md` (canonical schema + scrape→parse→label→train
  flow), `minutes_data_availability.md`, `processing_review.md`,
  `schema_enrichment_recommendation.md`, plus `hand_label_review_guide.md` and
  `meeting_level_info.tex`. **Do not rename this directory** — five code files,
  `README.md`, `STRUCTURE.md`, and `labeling_app/README.md` reference it by path.
- **`political_economic_housing_model/`** — `toy_model.tex` (+ `.pdf`, v5) formalizing
  fragmented housing regulation as a fiscal-federalism breakdown,
  `operationalization_memo.pdf` (the three-layer estimation blueprint that operationalizes
  it), and the Guren-meeting Beamer deck.
- **`bay_area_recon/`** — nine one-shot probes from a single reconnaissance sprint asking
  whether the SF pipeline can be extended to the ~109 ABAG land-use regulators. Closed work,
  but it holds live inputs (the 109-locality census frame and the HCD treatment panel). See
  `bay_area_recon/README.md`.
- **`_archive/`** — the two superseded research proposals, `minutes_data_sources.docx`, and
  `OUTPUT_INVENTORY.md` (the 2026-08-29 read-only audit of `output/` as it stood before the
  2026-08-30 reorganization; its "proposed reorganization" section is what was executed).

The demand-estimation line has no `output/` presence: its report and memos live in
`demand_estimation/report/`.
