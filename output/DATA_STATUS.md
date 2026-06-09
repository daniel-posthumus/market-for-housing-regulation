# DATA_STATUS — Bay Area Minutes/Zoning Data Collection (authoritative)

**As of 2026-06-09.** Single source of truth for where data collection stands. Supersedes
`bay_area_census/bay_area_locality_census_report.md` and the individual probe reports (which remain as
detailed references — see §7). Every number below was re-derived from the current CSVs, not from prior
prose; where a prior report disagreed with its data file, the CSV wins.

Scope: the **nine-county ABAG Bay Area** land-use regulators — **109 localities** (85 cities, 16 towns,
8 county governments; San Francisco is a consolidated city-county). The project structures planning-body
**minutes** into an item-level dataset of discretionary land-use decisions and pairs them with the
**by-right zoning envelope**, to test two-margin substitution under state housing preemption.

---

## 1. Executive summary

**The data is feasible for the core result, on a panel starting ~2016 with good breadth.** Access is
largely solved (the Bay Area collapses into ~6 minutes platforms and ~6 zoning-code hosts; only 3 of 109
sites are bot-blocked). Of 109 localities, **68 have a verified minutes-start year and 36 reach back to
≤2016** (29 at high/med confidence) — and that 36 is a **floor**, because the 41 still-`unknown` are
mostly a method limit (JS-gated portals), not proven-shallow archives. Current zoning **maps** are an
automatable spatial join, not RA-hours (67 of 109 have a downloadable GIS layer, plus a statewide
backstop covering the rest). The **treatment variable — HCD builder's-remedy exposure — is clean and
fully sourced** (0–38 months of variation across the estimation sample). The single biggest open item is
**not a data question**: it is the modeling decision of *by-right vs. ministerial*, which defines what
the zoning extraction is even measuring. Recon is complete; the project can move to building.

## 2. What is settled (the firm picture)

### Access — solved; collapses into a few solution classes
The 109 localities' **minutes** sources collapse into a small set (from `bay_area_locality_census.csv`):

| Minutes solution class | # | Build effort |
|---|---|---|
| `granicus_family_clean` (Legistar/Granicus/IQM2) | **41** | one adapter |
| `civicplus_clean` | 26 | one AgendaCenter scraper |
| `civicclerk_modern_api` | 10 | one OData client (proven on Daly City) |
| `custom_cms` | 10 | per-site (irreducible tail) |
| `primegov_portal` 4 · `civicweb_portal` 3 · `escribe` 2 · `onbase` 2 · `municode_meetings` 2 · `novusagenda` 1 · `laserfiche` 1 | 15 | small per-platform clients |
| `civicplus_akamai` 2 · `granicus_family_akamai` 1 (Fremont, Portola Valley, San Jose) | 3 | access-blocked |
| `unknown` | 3 | to_verify |

**Access status:** `clean` **78** · `js_shell` (modern API) **16** · `unknown` **10** · `akamai_403`
**3** · `unsupported_platform` **2**. Overall tractability: `needs_adapter` 69, `clean` 35,
`access_blocked` 3, `unknown` 2.

**The Akamai scare was a myth, corrected by measurement:** a header-only sweep of all 28 CivicPlus sites
found **25 clean, only 2 blocked** (Fremont, Portola Valley) — Akamai is a per-city choice, not a
platform-wide wall. Total bot-blocked across all 109 = **3** (Fremont, Portola Valley, San Jose).

### Panel window & breadth — ~2016 is real; minutes ≠ agendas
From `archive_depth.csv` (post-merge): **68 of 109** have a verified earliest-**minutes** year; **41
remain `unknown`** (a method floor — JS/API portals not statically readable, not proven-absent).

| earliest minutes year | # |
|---|---|
| ≤2005 | 6 |
| 2006–2010 | 11 |
| 2011–2015 | 15 |
| 2016+ | 36 |
| unknown (method-limited) | 41 |

Localities continuous-capable from a common start: **≤2014 → 29** (22 high/med) · **≤2016 → 36** (29
high/med) · **≤2018 → 45** (37 high/med). **A ~2016 panel has enough co-submarket breadth to estimate a
strategic-interaction reaction function around the 2017–2023 preemption ramp**, and is a floor (resolving
the 41 unknowns at build time only adds localities).

Two corrections this phase, both load-bearing:
- **The agenda-trap is real:** year-dropdown floors overstate minutes depth because dropdowns span
  *agendas*. API checks corrected Oakland 2000→**2014**, Santa Rosa 1999→**2016**, Hayward 2013→**2015**.
  All depth years here are minutes-type-verified where confidence is high/med.
- **Migration cliffs add deep history** (`migration_cliffs.csv`): the ~2024 portal migrations were
  domain/view changes, not data loss. **Marin County → 2005** (reachable), **Santa Clara County → 2008**
  (legacy IQM2 reachable), **San Jose → 2005** (minutes listing confirmed 2005–2018, but documents
  Akamai-gated). These big jurisdictions extend the panel backward.

### Spatial / zoning maps (current) — a script, not RA-hours
From `zoning_map_form.csv`: **67 of 109** have a downloadable GIS layer (`gis_layer`), 28 are
viewer-only (data likely behind the viewer), 13 are PDF-only, 1 none-found; `download_apparent=yes` for
71. Of the 67 GIS layers, **55 are native city/county/consortium** and 12 are covered via the statewide
fallback. **The high-ROI backstop:** the **California Statewide Zoning layer (Gov-OPR)** is one
downloadable layer covering ~all 109 — so an automated address→district join is feasible **everywhere**
(caveat: it is a 2022–23 aggregated snapshot; fidelity-check per city). County/regional consortia
(MarinMap, Solano ReGIS, Napa County GIS) fully cover their counties. Zoning **code** hosts likewise
collapse: Municode 44, CodePublishing 23, eCode360 14, public.law 8, American Legal 7 (403-walled), QCode 2.

### Treatment variable (HCD preemption) — clean and sourced
From `hcd_preemption_panel.csv` (authoritative HCD HE Review & Compliance dataset + Prohousing list):
**all 25 estimation-sample localities have sourced compliance dates.** Both date conventions are retained
(HCD-received-adopted ≈ self-adoption **and** HCD formal compliance finding), anchored to the ABAG
6th-cycle statutory deadline 2023-01-31. **Builder's-remedy exposure ranges 0.0–38.4 months** — strong
treatment variation (Alameda certified pre-deadline → 0; Oakland ~0.5mo; Daly City 22mo; Clayton 38mo).
**13 of 25 are prohousing-designated.**

## 3. In progress / bounded-but-not-done

- **CivicPlus depth — 12 of 14 localities** (`civicplus_depth.csv`: only Campbell 2006 and Los Altos
  Hills 2022 resolved). The newer **CivicEngage** sites render the committee list via JS, so the
  committee CID the (proven) AgendaCenter Search method needs isn't obtainable from a static read.
  **Remains:** a browser session to grab each CID, then the Search endpoint resolves depth. Not blocked,
  not absent — JS-gated. (Mostly small cities outside the high-value panel.)
- **Pre-period envelope — 15 of 25 covered** (`preperiod_envelope.csv`: 11 ordinance-text Wayback
  captures + 3 archived zoning-map PDFs + SF). **9 `none_found`** (Albany, Emeryville, Santa Rosa on
  eCode360; Palo Alto, Fairfax on American Legal *legacy*; Petaluma on public.law; Piedmont city-site;
  San Ramon and Daly City — killed as false/draft). These are recoverable from each code publisher's
  amendment/version history (a moderate dig). **Burlingame** has only an NZLUD 2019–21 proxy (post-SB-35
  vintage gap). Note: the usable count was corrected **17→15** after killing San Ramon (an application
  doc) and Daly City (a GPU draft) — earlier prose had overstated it.
- **HCD secondary items:** 8 prohousing localities are designated but their exact **date** is only in
  HCD's tracker XLS; and the compliance dataset is a current-status snapshot, so any
  decert→recert sequence isn't captured (all 25 currently "In"). Both are `to_verify`, secondary.
- **San Jose pre-migration minutes:** listing-confirmed 2005–2018 but documents Akamai-gated — retrieval
  needs a decision (browser automation / official request), not more probing.

## 4. Deferred (out of scope for the core project)

- **Deep (pre-2010) historical zoning maps** for the ratchet/long-run dynamics.
- **Physical-records workstream** — localities whose minutes appear offline-only (Vallejo, Sebastopol,
  Benicia, Dixon were flagged) — needs in-person/records-request work.
- **In-person archive visits** of any kind.

**The core substitution test does not depend on any of these** — they gate only the ambitious
long-history ratchet extension. The feasible ~2016 panel stands without them.

## 5. The two open DECISIONS (not probes — more data work will not resolve them)

1. **By-right vs. ministerial definition** — the model's hinge. Whether a use is *principally permitted*
   (built with certainty, no hearing) vs. *conditionally permitted* / subject to design review defines
   what the zoning-envelope extraction even measures, and the substitution test turns on it. A **modeling
   decision (Daniel + advisor)**, not a measurement.
2. **Deep-history / ratchet investment** — whether to fund the pre-2010 reconstruction + San-Jose-Akamai
   access + physical-records work, or scope the project to the feasible ~2016 panel. A **strategic/budget
   decision.**

## 6. Directly-actionable next steps (ordered by dependency)

**A. Build steps now unblocked (access + form are settled):**
1. Build the **Granicus/Legistar minutes adapter** — unlocks the **41** `granicus_family_clean`
   localities; access clean. *(Claude Code / RA-eng, ~days.)*
2. Build the **CivicClerk OData reader** (proven on Daly City) — **10** localities. *(small.)*
3. Build the **CivicPlus AgendaCenter scraper** — **26** clean localities. *(small–moderate.)*
4. Build the **Municode zoning-code client** (44 codes) + **CodePublishing** (23) + **eCode360** (14).
5. **Extraction-transfer re-test (do this early, with a human in the loop):** SF's `autoextract`
   heuristics do **not** transfer cross-jurisdiction (verified on Daly City — confidently-wrong fields).
   Validate an extractor on real Granicus/CivicClerk minutes **with human review** — **not** an
   unattended run — before scaling. This is the gating quality step for the whole minutes build.

**B. Cheap finish-the-recon items (parallel, low effort):**
6. Browser session to grab the **12 CivicPlus CivicEngage committee CIDs**, then the proven Search method
   resolves their depth.
7. Publisher version-history dig for the **9 pre-period `none_found`** (eCode360 / American-Legal-legacy /
   public.law) + a true ≤2018 source for **Burlingame**.
8. Pull the **8 prohousing dates** from HCD's tracker XLS.

**C. Decision-gated (do NOT start until the by-right/ministerial call is made):**
9. The **zoning-envelope extraction itself** (current and ~2016 pre-period) — its target is undefined
   until Decision #1 is made. Likewise any pre-2010 work waits on Decision #2.

## 7. Artifact index (source of truth per question)

| File | Authoritative for | Rows |
|---|---|---|
| `bay_area_census/bay_area_locality_census.csv` | platforms, access, solution classes, GEOIDs | 109 |
| `archive_depth_probe/archive_depth.csv` | verified minutes-start year per locality (post all merges) | 109 |
| `archive_depth_probe/archive_depth_api.csv` | API depth-read detail | 55 |
| `civicplus_depth_probe/civicplus_depth.csv` | CivicPlus depth (Campbell 2006, Los Altos Hills 2022; 12 JS-gated) | 14 |
| `migration_cliffs_probe/migration_cliffs.csv` | Marin/SCC/San Jose pre-migration archives | 3 |
| `zoning_map_form_probe/zoning_map_form.csv` | current spatial zoning-data form | 109 |
| `preperiod_envelope_probe/preperiod_envelope.csv` | ~2016 pre-period envelope coverage (corrected) | 25 |
| `hcd_preemption_panel/hcd_preemption_panel.csv` | HCD builder's-remedy treatment variable | 25 |
| `zoning_envelope_project/hcd.csv`, `nzlud_*` | raw HCD dataset; NZLUD coverage | — |
| **Superseded:** `bay_area_census/bay_area_locality_census_report.md` and the per-probe `*_report.md` | detailed narratives; **this file is current** | — |

---

### Self-verification (required)
Every count above was re-derived from the named CSV on 2026-06-09 and cross-checked: census 109 (clean
78 / js_shell 16 / unknown 10 / akamai 3 / unsupported 2; CivicPlus 28 = 25 clean+2 akamai+1 unknown;
3 total akamai_403 = Fremont/Portola Valley/San Jose); archive_depth known 68/109, buckets
6/11/15/36/41, ≤2016=36 (high/med 29), ≤2018=45; zoning best_form gis_layer 67 / viewer 28 / pdf 13 /
none 1, native-gis 55; pre-period usable 15/25 (11 text + 3 pdf + 1 SF), 9 none_found, 1 nzlud; HCD 25
rows, exposure 0.0–38.4mo, 13 prohousing; civicplus 14 (2 resolved); migration 3. **The document and the
artifacts agree.**
