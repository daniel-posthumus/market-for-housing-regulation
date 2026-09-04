# `bay_area_recon/`

One research sprint, June 2026, asking a single feasibility question: **can the San Francisco
minutes pipeline be extended to the ~109 land-use regulators of the nine-county ABAG Bay
Area?** Nine probes, each in its own directory, each with the script that produced its CSV
sitting beside it. They were top-level siblings of the two long-lived project lines until
2026-08-30; they are sub-artifacts of one sprint, so they now live together.

**Read [`memo.tex`](memo.tex) in this directory** — one consolidated memo covering all nine
probes: the question and the answer, the 109-locality frame, minutes depth and access, the
zoning envelope, the HCD treatment variable, a full data inventory, and the list of known
contradictions between the CSVs and their narrative `.md` reports. It replaced the nine
per-probe memos on 2026-08-31. The two documents these probes originally reported into
(`DATA_STATUS.md`, `final_recon_bundle_report.md`) were deleted on 2026-08-30 and their
content folded in as well. Each probe directory still holds its own CSVs, scripts and `.md`
reports; the memo's data-inventory section says which file came from where.

## The directories

| Directory | The question it answers | Status |
|---|---|---|
| `bay_area_census/` | **The frame.** All 109 localities: minutes platform, zoning source, access barrier. Every other probe joins to it on `fips_geoid`. | CSV live |
| `archive_depth_probe/` | **The time dimension.** Earliest year of posted *minutes* per locality — and the "agenda trap" that inflates naive dropdown reads by a decade. | CSV live |
| `zoning_map_form_probe/` | **Spatial form.** How each locality's current zoning map is published (67 GIS / 28 viewer-only / 13 PDF), plus the CA Statewide Zoning backstop. | CSV live |
| `preperiod_envelope_probe/` | **The ~2016 pre-period.** Wayback-CDX search for a datable pre-period zoning envelope across the 25-locality estimation sample; 15/25 usable. | CSV live |
| `hcd_preemption_panel/` | **The treatment variable.** HCD builder's-remedy exposure (0–38.4 months) for the 25 estimation-sample localities. | **Load-bearing** |
| `migration_cliffs_probe/` | Whether the ~2024 portal migrations at Marin Co., Santa Clara Co., and San Jose destroyed history (they did not). | CSV live |
| `civicplus_depth_probe/` | CivicPlus AgendaCenter depth. Method proven; 12 of 14 still need a browser session for the committee CID. | Incomplete |
| `minutes_platform_pilot/` | The earlier 14-city pilot. Superseded in *coverage* by the census, but the **only** source for the finding that SF's `autoextract` heuristics do not transfer cross-jurisdiction. | Keep |
| `zoning_envelope_project/` | The 14-city by-right-envelope assessment: NZLUD coverage (4/14), code hosts, extraction pilot. | Superseded in coverage |
| `_source_data/` | Unmodified third-party releases the probes consume: `nzlud_muni.csv` (full NZLUD, 2,639 munis) and `hcd.csv` (full HCD compliance report, 539 rows). Not project output — kept in-repo so the derived panels stay reproducible. | Inputs |

## If you move these directories

Several scripts reach sideways with `HERE.parent / "<sibling>"`
(`archive_depth_probe/consolidate.py`, `archive_depth_probe/assemble_api.py`,
`zoning_map_form_probe/consolidate.py` → `bay_area_census/`;
`hcd_preemption_panel/build_hcd_panel.py` → `_source_data/`). Moving the group as a unit is
safe; splitting it up is not. `minutes_platform_pilot/jurisdiction_mappings.py` additionally
counts `.parent` up to the repo root and must be re-counted on any change of depth.
