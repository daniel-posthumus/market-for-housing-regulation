# Zoning-Data-Form Probe — Findings (the maps, not the code)

**Date:** 2026-06-08 · **Author:** Claude Code · **Brief:** `.claude/instructions/zoning_map_form_probe.md`
**Artifact:** `zoning_map_form.csv` (109 rows, join key `fips_geoid`).

> **One-line finding:** The spatial side is the **good outcome.** An **automatable spatial join is
> feasible for essentially every locality** — there is a single downloadable **California Statewide
> Zoning layer (Gov-OPR)** covering ~535/539 CA jurisdictions, *plus* native/consortium GIS layers for
> the large majority. The envelope-matching step is a **script, not an army of RA-hours.**

## Method note

Shallow form/inventory probe (locate + classify the spatial-data form; no downloads, no joins, no
build), per the brief. **Correction to the brief's premise:** the stated "20 rows already mention GIS"
is a **false positive** — the substring "gis" inside "Le**gis**tar"; the census has **0 genuine GIS
mentions**, so spatial form was probed fresh for all 109.

## 1. Form summary

**best_form (as reported):**

| best_form | # | meaning |
|---|---|---|
| `gis_layer` | **67** | downloadable shapefile/GeoJSON/feature service (scriptable spatial join) |
| `gis_viewer_only` | 28 | interactive ArcGIS viewer, no obvious download (data likely behind it — build-time check) |
| `pdf_map` | 13 | static zoning-map PDF (manual visual lookup) |
| `none_found` | 1 | Pacifica (no city layer/viewer/PDF located shallowly) |

**Honest coverage split** (because 12 Alameda cities were classified only via the statewide layer
without a native check):

| coverage class | # |
|---|---|
| `gis_layer` — **native city or county/consortium** (confirmed) | **55** |
| `gis_layer` — **CA Statewide fallback only** (Alameda cities; native unchecked) | 12 |
| `gis_viewer_only` | 28 |
| `pdf_map` | 13 |
| `none_found` | 1 |

`download_apparent`: **yes 71 · no 37 · unknown 1.**

**The universal backstop (highest-ROI find):** the **CA Statewide Zoning layer (Gov-OPR)** on
`gis.data.ca.gov` is a single downloadable layer (shapefile/GeoJSON/REST) covering ~all 109 localities.
So even the 28 viewer-only + 13 PDF + 1 none localities have an **automatable** path via this one source
— subject to the caveat below.

## 2. County/regional layers that unlock many at once (the build-plan wins)

The brief's highest-value target — one source covering many localities — was found **repeatedly**:

| Source | Coverage | Form |
|---|---|---|
| **CA Statewide Zoning (Gov-OPR)**, `gis.data.ca.gov` | **~all 109** (535/539 CA jurisdictions) | downloadable layer + REST; **2022-23 snapshot, aggregated fidelity** |
| **MarinMap consortium** (`gis.marinpublic.com/.../MarinMap2/Open_Data_Download`) | **all 12 Marin** (per-town zoning layers 114–125) + open-data hub | downloadable |
| **Solano Regional GIS Consortium** (`services2.arcgis.com/SCn6czzcqKAFwdGU`) | **all 8 Solano** (per-city zoning FeatureServers) + shapefile downloads | downloadable |
| **Napa County GIS** (`gis.napacounty.gov/.../hosted/<City>_Zoning`) | **all 6 Napa** (per-city hosted services) + open-data portal | downloadable |
| County GIS (Contra Costa, San Mateo, Santa Clara, Sonoma) | **unincorporated areas only** (cities self-publish) | downloadable |

Santa Clara, San Mateo, Contra Costa, and Sonoma have **no single all-cities county layer**, but most
of their cities **self-publish** native ArcGIS open-data zoning layers (San Jose, Palo Alto, Mountain
View, Cupertino, Campbell, Santa Clara, Sunnyvale, Milpitas; Belmont, Menlo Park, Redwood City, South
SF; Concord, Pittsburg, Pleasant Hill, San Ramon, Moraga; Petaluma, Rohnert Park, Santa Rosa).

## 3. Coverage read (feasibility)

**The project can rely on automated spatial joins for a usable majority — in fact, for essentially
all localities.** Two tiers: (1) **native/consortium layers** give authoritative, current,
downloadable zoning for **55+ localities** (and Marin/Solano/Napa are *fully* covered by their
county/regional consortia — 26 localities from three sources); (2) the **CA Statewide Zoning layer is a
universal fallback** that covers the rest, including the 28 viewer-only and 13 PDF localities, so no
locality is a dead end for automation. The cheapest path to current-envelope spatial matching is
therefore: **use native city/county/consortium layers where they exist (authoritative + current), and
the Gov-OPR statewide layer as the gap-filler** — one regional source already closes most of the gap.
**The tension to flag:** the statewide layer is a **2022-23 aggregated snapshot** whose per-city
fidelity and currency vary, so for the ~42 localities that natively offer only a viewer/PDF, the
statewide layer is automatable but should be **fidelity-checked against the city's own viewer/PDF**
before trusting it for the by-right envelope (the load-bearing variable). Net: spatial matching is a
**scriptable problem, not an RA-hours problem** — a decisively better outcome than the minutes side.
Only **1 locality (Pacifica)** had no spatial form located at all, and even it is covered by the
statewide layer.

## 4. Consolidated `REVIEW:` / `to_verify`

**A. `gis_viewer_only` — data very likely exists behind the viewer; confirm a download/feature-service
endpoint at build time (28 localities):** Antioch, Brentwood, Brisbane, Burlingame, Cloverdale, Cotati,
Danville, East Palo Alto, El Cerrito, Foster City, Half Moon Bay, Hillsborough, Lafayette, Los Altos,
Los Altos Hills, Los Gatos, Martinez, Morgan Hill, Oakley, Orinda, Richmond, San Bruno, San Carlos,
San Mateo, Sebastopol, Sonoma, Walnut Creek, Windsor. (Several are explicitly backed by a hosted
ArcGIS layer — e.g. Walnut Creek, Campbell-style — so the true `gis_layer` count is likely **>67**.)
Some use non-ArcGIS viewers (Digital Map Products "CommunityView": El Cerrito, Lafayette, Martinez) for
which a download may need a city request.

**B. `none_found` — `to_verify` directly:** **Pacifica** (only Municode/third-party ZoningPoint found;
city points to County parcel maps). Covered by the statewide layer regardless.

**C. `to_verify` low-confidence IDs:** **Hillsborough** (an ArcGIS "Zoning Map" web app exists but
ownership unverified amid Hillsborough-FL false positives); **Foster City** (hub may expose a download,
unconfirmed); **Brisbane** (hosted layer suggested by AGOL items, download unverified).

**D. `REVIEW:` statewide-fallback fidelity:** the Gov-OPR layer underpins automation for the 28
viewer + 13 PDF + 1 none localities and the 12 Alameda cities (native unchecked) — its **2022-23
vintage and aggregated accuracy** should be spot-checked per city before relying on it for the by-right
envelope. Decision for Daniel: accept the statewide layer as the universal first cut, or budget native
extraction for the ~42 non-native-layer localities.

**E. Deferred historical-map lead (noted, not investigated):** **Stanford EarthWorks** holds a
**historical 2014 version of the San Mateo County planning-zones layer** — a pointer for the deferred
historical-envelope workstream (the substitution test's long-run spatial dimension). Do not act now.

---

### Artifacts
- `zoning_map_form.csv` — 109 rows (`locality, fips_geoid, best_form, spatial_url, source_type, download_apparent, confidence, notes`).
- `zoning_map_form_report.md` — this report.
- `consolidate.py` — reproducible (reads `raw/*.json` + census GEOIDs → CSV + form distribution).
- `raw/*.json` — the 8 per-county probe outputs (provenance).
