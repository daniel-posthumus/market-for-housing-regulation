# `demand_estimation/` — Layer I demand-side data collection

Automated acquisition, staging, and documentation of the **region-wide**
(nine-county ABAG Bay Area) demand-side data for the housing-demand layer of
`market-for-housing-regulation`. Spec: `.claude/instructions/demand_data_brief.md`.

The downloaded **data lives on Dropbox** under `data/demand/` (out of git),
resolved via `demand_paths.py` → `MFHR_DATA_ROOT`. Only the code lives here.

## Run it

```bash
# from the repo root
python -m demand_estimation.build              # collect every source + build artifacts
python -m demand_estimation.build --collect-only
python -m demand_estimation.build --build-only
```

Idempotent and resumable: an existing, non-empty file (with matching checksum
where the provider gives one) is never re-fetched. Every fetch/build is logged
to `data/demand/_manifest.csv` (`source,url,local_path,bytes,sha256,fetched_at,status`).
Each source and build step is failure-isolated — one flaky portal never blocks
the rest.

## Layout

```
demand_estimation/
├── demand_paths.py      # imports DATA_ROOT from the minutes pipeline; defines demand/ tree + FIPS
├── util.py              # polite HTTP (UA, rate-limit, retry/backoff), streaming download, sha256, key resolver
├── manifest.py          # _manifest.csv writer/checker (keyed by source+path)
├── arcgis.py            # generic ArcGIS Feature Service -> GeoJSON pager (bbox-clipped)
├── collectors/
│   ├── tiger.py         # §2.4 county/tract/BG/place/PUMA shapefiles
│   ├── lodes.py         # §2.3 LODES8 WAC/RAC/OD/xwalk (2022 + 2015)
│   ├── acs.py           # §2.1 PUMS (keyless FTP) + §2.2 tables (API, key-gated)
│   ├── ssurgo.py        # §2.5 Soil Data Access engineering properties (the instrument)
│   ├── hazard.py        # §2.6 CGS seismic hazard zones + fault traces (controls)
│   ├── zoning.py        # §2.7 Gov-OPR statewide zoning (Layer II backstop)
│   ├── amenities.py     # §2.8 CPAD open space, CDE schools, GTFS transit
│   └── migration_irs.py # §3.3 IRS county migration (free backup for Infutor/Verisk)
├── stubs.py             # §3 manual sources -> _stubs/<name>/README.md (manual_required)
├── build.py             # orchestrate collect -> build derived artifacts
└── report/
    └── demand_data_report.tex (+ .pdf)
```

## Automatable vs manual

| Source | Layer I role | Automated? |
|---|---|---|
| ACS PUMS | household micro / RC moments | ✅ keyless FTP bulk |
| ACS BG tables | shares, tenure, income | ✅ API (needs `CENSUS_API_KEY`) |
| LEHD LODES8 | job access (agglomeration) | ✅ direct |
| TIGER + crosswalk | geography spine, BG↔jurisdiction | ✅ direct |
| SSURGO soil | **instrument** (eng. properties) | ✅ Soil Data Access |
| CGS seismic | instrument **control** | ✅ ArcGIS |
| Gov-OPR zoning | envelope backstop (Layer II) | ✅ ArcGIS |
| CPAD / CDE / GTFS | amenities (+ controls) | ✅ best-effort |
| IRS migration | moving-cost backup | ✅ direct |
| **CoreLogic** | **price/transaction spine** | ❌ manual (BU/Questrom) |
| **RS Means** | soil→cost index schedule | ❌ manual (license) |
| Infutor / Verisk | moving costs (upgrade) | ❌ manual (optional) |

## The instrument, in one breath

SSURGO shrink-swell / plasticity / bearing properties → (× RS Means foundation
cost schedule, **manual**) → predicted construction-cost index → residualized
against job access, seismic, open space, transit, shore/slope → the cost-shifter
instrument. This run delivers the SSURGO engineering extract and the
residualization design matrix; the cost index is blocked on RS Means and is left
as a documented TODO (see `data/demand/soil/derived/TODO_*.md`).

## Two blocking manual hand-offs

**CoreLogic** (prices) and **RS Means** (cost schedule) cannot be automated
(licensed). See `data/demand/_stubs/{corelogic,rs_means}/README.md` for the exact
steps, drop paths, and expected schemas. Full provenance + hand-offs are in
`report/demand_data_report.pdf`.
