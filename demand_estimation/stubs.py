#!/usr/bin/env python3
"""
stubs.py — manual / non-automatable sources (brief §3).

These are licensed or auth-walled products. We do NOT scrape, mirror, or
circumvent them. For each we write ``demand/_stubs/<name>/README.md`` (what it
is, why it can't be automated, the exact steps Daniel takes, the expected drop
path, and the schema the pipeline expects) and register a ``manual_required``
row in the manifest. The free IRS migration backup is collected automatically
(see collectors/migration_irs.py); only the licensed upgrade is stubbed here.
"""
from __future__ import annotations

from . import demand_paths as dp

STUBS: dict[str, str] = {}

STUBS["corelogic"] = r"""# CoreLogic / Cotality — property transactions, deeds, tax, characteristics

**Status:** `landed` (manual pull complete) — see `corelogic/clean/`.
**Layer I role:** the **price / transaction spine** of demand — the dependent
variable side. The owner-side user-cost series (demand memo §3) is built FROM
these prices + tax + an interest-rate series.

## Why it could not be automated
Cotality (formerly CoreLogic) is a licensed product behind institutional access:
no public API or bulk URL; not scraped or mirrored.

## How it was obtained
Pulled manually from **Stanford Libraries' Redivis "Data Farm"** — the Cotality
Smart Data Platform datasets the campus licenses:
  * **Owner Transfer and Mortgage** (deeds / sales history) — the price spine;
  * **Property** (tax-assessor + characteristics) — note this single dataset is
    the "tax" *and* "property" file (there is no separate Tax dataset);
  * (optional) **Historical Property** for an assessment/tax time panel.
Extracts were filtered server-side to the nine Bay Area county FIPS, with PII
columns (owner / buyer / seller names, mailing addresses) excluded at query time
per the Stanford EULA and IRB rules. The query templates and column choices are
in this folder's data dictionaries; `demand_estimation/corelogic.py` merges and
geocodes them. LLMA (loan performance) is walled off by EULA and not linked.

## Where it lives
    demand/corelogic/
    ├── cotality_owner_transfer_filtered.csv   # deeds (transaction grain)
    ├── cotality_property_filtered.csv         # tax-assessor + characteristics (parcel grain)
    └── clean/
        ├── corelogic_transactions_bg.parquet  # one row per sale + chars + tax + GEOID
        └── corelogic_parcels_bg.parquet       # one row per parcel

## Remaining (minor)
The owner-side **user-cost** series still needs an interest-rate series (e.g.
FRED 30-yr mortgage) combined with these prices and the assessed-tax field — a
small automatable follow-up, not a blocker.
"""

STUBS["rs_means"] = r"""# RS Means — residential construction-cost schedule

**Status:** `manual_required` (paid subscription product).
**Layer I role:** converts the SSURGO engineering-property extract (§2.5) into
the **predicted foundation-cost index** — the actual instrument. Without it the
cost index cannot be computed.

## Why this cannot be automated
RS Means is a paid subscription. Do not scrape.

## What Daniel must do
Pull residential **foundation-type cost differentials** (slab-on-grade vs
pier/pile vs mat foundation) from RS Means (BU library may have access; else
Questrom).

## Where to drop it
    demand/soil/derived/foundation_cost_schedule.csv

## Schema the pipeline expects
| column | meaning |
|---|---|
| `soil_or_foundation_class` | shrink-swell / bearing class (joins to SSURGO) |
| `usd_per_unit_premium` | $ premium vs the slab-on-grade baseline |

The mukey-level `mapunit_engineering_properties.parquet` (already built) joins
to this schedule on the shrink-swell / bearing class to produce
`soil/derived/bg_predicted_construction_cost.parquet`.

## Public proxy (first-pass option)
Published geotechnical cost rules-of-thumb (slab vs pier/pile premia) can stand
in for a first pass; flag the source. Default to RS Means for the real index.
"""

STUBS["migration_infutor_verisk"] = r"""# Migration flows — Infutor / Verisk

**Status:** `manual_required`, **OPTIONAL for v1**.
**Layer I role:** moving-cost estimation à la Coven (only needed if moving costs
are structurally estimated).

## Why this cannot be automated
Infutor / Verisk are licensed commercial address-history products. Do not
scrape.

## Free automatable backup (already pulled)
IRS SOI county-to-county migration flows are collected automatically into
`demand/migration/irs/` (countyinflow / countyoutflow). Use these as the
first-pass moving-cost input; treat Infutor/Verisk as the upgrade.

## What Daniel must do (only if upgrading)
License Infutor or Verisk address-history panels via BU/Questrom.

## Where to drop it
    demand/migration/infutor/      # or demand/migration/verisk/

## Schema the pipeline expects
| field | meaning |
|---|---|
| `person_or_hh_id` | anonymized mover id |
| `from_geo`,`to_geo` | origin / destination (block group or tract) |
| `move_date` | timing |
"""


def write_all(manifest) -> dict:
    dp.STUBS.mkdir(parents=True, exist_ok=True)
    written = []
    for name, body in STUBS.items():
        d = dp.STUBS / name
        d.mkdir(parents=True, exist_ok=True)
        readme = d / "README.md"
        readme.write_text(body)
        manifest.record(f"manual_{name}", url="", local_path=readme,
                        bytes=readme.stat().st_size, status="manual_required")
        written.append(name)
    return {"status": "ok", "stubs": written}
