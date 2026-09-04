> **⚠️ SUPERSEDED (2026-06-09):** This census report is no longer the current status document for
> this directory. See **`../memo.tex`** (§2) for the maintained, up-to-date summary; the
> authoritative data itself is `bay_area_locality_census.csv`, from which every number in the memo was
> re-derived. This file is retained for provenance only, and several of its counts predate the
> 2026-06-08 CivicPlus/Akamai sweep — where it disagrees with the CSV, the CSV wins (see `../memo.tex` §7).

# Bay Area Locality Source Census

**Date:** 2026-06-08 · **Author:** Claude Code · **Brief:** `.claude/instructions/bay_area_locality_census.md`
**Primary artifact:** `bay_area_locality_census.csv` (109 rows, sortable). This report is the roll-up.

> **What this is.** A **shallow, complete** map of every Bay Area land-use regulator — its minutes
> source, zoning source, access barriers, and the **solution class** each falls into — so the build
> can be scoped and sequenced. It is a census, **not** a build: no documents were retrieved, no
> scrapers written, no archive depth verified. Per the brief, two fields were deliberately **not**
> filled (earliest-online year; whether a discretionary-review mechanism exists) — both default to
> `to_verify`/unknown and are resolved during the build.

## Scope & completeness

Full nine-county ABAG frame, **complete**: **101 incorporated cities/towns + 8 county governments + San Francisco** (reference row; SF city = SF county, consolidated) = **109 rows**. The city frame was supplied authoritatively (not agent-invented); each locality was then shallow-surveyed.

**GEOID join key: 109/109 attached, 0 fabricated.** Place GEOIDs come from the Census 2023 Gazetteer (e.g. Redwood City `0660102`, Daly City `0617918`); county GEOIDs from standard FIPS (e.g. Sonoma County `06097`). Everything keys on GEOID, not name (the pilot's "Redwood"≠"Redwood City" trap).

## Method & honesty caveats (read before trusting a cell)

- **`minutes_access` — CivicPlus/Akamai now RESOLVED (sweep 2026-06-08).** The concern that ~27 CivicPlus rows might be Akamai-gated was **measured and largely disproven**: a header-only sweep of all 28 CivicPlus localities found **25 clean, only 2 `akamai_403` (Fremont, Portola Valley), 1 stale-URL (Antioch)**. Those measured values are now in the CSV; the CivicPlus class is **not** an access risk. See `akamai_civicplus_probe_finding.md`. Remaining `minutes_access=unknown` (**10 rows**) are non-CivicPlus custom-CMS / unidentified sites, not yet probed. Other confirmed block: **San Jose** (`granicus_family_akamai`). **Total confirmed `akamai_403`: 3.**
- **`js_shell` (16 rows)** = content behind a modern API (CivicClerk, PrimeGov, IQM2, OnBase, Municode Meetings). Not blocked, but needs an API adapter, not a page scrape.
- **Mostly-unknown depth fields:** `zoning_structure`, `height_in_code`, `in_nzlud` are `unknown` for nearly all rows (shallow rule). `in_nzlud` is only filled for the 8 pilot cities (4 yes / 4 no).
- **New solution classes** were added where the pilot's list didn't fit a real pattern: `primegov_portal`, `civicweb_portal`, `municode_meetings_portal`, `novusagenda_portal`, `laserfiche_portal`, `encodeplus`. Kept minimal.

---

## §A — Solution-class roll-up (the itemized work plan)

**This is the payoff: ~109 localities collapse into a handful of engineering efforts.**

### Minutes (one adapter per class unlocks all its localities)

| # | minutes_solution_class | What the single effort is | Localities |
|---|---|---|---|
| 41 | `granicus_family_clean` | One Legistar/Granicus adapter (ViewPublisher/MinutesViewer + Legistar Web API). Includes IQM2 (Granicus-owned). | **41** |
| 27 | `civicplus_clean` | One CivicPlus AgendaCenter/CivicEngage scraper — **gated on resolving Akamai (see caveat)** | **27** |
| 10 | `civicclerk_modern_api` | One CivicClerk OData client (the Daly City method) | **10** |
| 10 | `custom_cms` | Per-site PDF scrapers (no shared vendor) — the irreducible long tail | **10** |
| 4 | `primegov_portal` | One PrimeGov portal-API client | 4 |
| 3 | `civicweb_portal` | One Granicus CivicWeb/iCompass client | 3 |
| 2 | `escribe_portal` | One eScribe client (unsupported by civic-scraper) | 2 |
| 2 | `onbase_portal` | One Hyland OnBase AgendaOnline client | 2 |
| 2 | `municode_meetings_portal` | One Municode Meetings (adaHtmlDocument) client | 2 |
| 1 ea | `civicplus_akamai`, `granicus_family_akamai`, `novusagenda_portal`, `laserfiche_portal` | one-offs / Akamai-blocked | 4 |
| 3 | `unknown` | platform not identified — `to_verify` | 3 |
| 1 | `reference_done` | SF (built) | 1 |

**Four classes cover 88/109 minutes sources** (granicus 41 + civicplus 27 + civicclerk 10 + custom 10). The Granicus family alone is the single highest-ROI adapter.

### Zoning (codes cluster onto ~6 commercial hosts)

| # | zoning_solution_class | Single effort | Localities |
|---|---|---|---|
| 44 | `municode_api` | One Municode library client | **44** |
| 23 | `codepublishing_clean` | One CodePublishing scraper | **23** |
| 14 | `ecode360_clean` | One eCode360 (General Code) scraper | **14** |
| 8 | `publiclaw_clean` | One public.law / municipal.codes scraper | 8 |
| 7 | `amlegal_or_ecode_403` | American Legal — **403/bot-wall, licensed or manual path** | 7 |
| 3 | `city_site` | Per-site (self-hosted code pages) | 3 |
| 2 | `qcode_clean` | One QCode scraper | 2 |
| 1 ea | `encodeplus`, `reference_done` | one-offs | 2 |
| 6 | `unknown` | host not identified — `to_verify` | 6 |

**Three hosts cover 81/109 zoning codes** (Municode 44 + CodePublishing 23 + eCode360 14). Only American Legal (7) presents an access wall.

---

## §B — Tractability tier summary

**Overall tier** (per locality): `clean` **35** · `needs_adapter` **70** · `access_blocked` **2** · `unknown` **2**.
("needs_adapter" dominates because most modern portals need an API client, not because they're blocked — most are retrievable.)

**Minutes access:** `clean` 66 · `unknown` 23 *(mostly CivicPlus — Akamai unverified)* · `js_shell` 16 · `akamai_403` 2 · `unsupported_platform` 2.
**Zoning access:** `clean` 94 · `unknown` 14 · `akamai_403` 1. **Zoning is overwhelmingly tractable**; minutes is where the access risk concentrates.

**`physical_only`: 0 confirmed.** No locality was confirmed paper-only — but a few tiny jurisdictions (Colma, Monte Sereno, Newark) are `minutes_platform=unknown`/low-confidence and could turn out sparse or physical. If an in-person check is wanted **before you leave the Bay Area this summer**, those three (plus Fairfax, San Rafael — platform unconfirmed) are the candidates to eyeball, not a digitization project.

---

## §C — Consolidated REVIEW / to_verify (76 flagged rows)

Every flag lives inline in the CSV `notes` column. Grouped by theme:

1. **CivicPlus Akamai status (≈20 rows)** — the dominant `to_verify`. Every CivicPlus `/AgendaCenter` row marked `minutes_access=unknown` (Antioch, Moraga, Oakley, Orinda, Pleasant Hill, San Pablo, Corte Madera, Larkspur, San Anselmo, Half Moon Bay, Hillsborough, Millbrae, Woodside, Campbell, Saratoga, Cloverdale, Cotati, Healdsburg, Windsor, …). **One probe per CivicPlus host resolves the whole class.**
2. **Platform not identified (`unknown`, low confidence):** Newark (AgendaQuick/Destiny?), Fairfax (WordPress?), San Rafael, Belvedere (ProudCity, migrating), Colma, Monte Sereno (Site & Architectural Commission — body itself uncertain).
3. **Dual/migrating systems — which is authoritative:** Foster City (CivicClerk vs PrimeGov), San Pablo (CivicPlus vs Legistar), Menlo Park (new custom CMS vs Granicus archive), El Cerrito & Pinole & Union City (migrated to CivicClerk/other; legacy archive elsewhere), Brentwood (eScribe vs legacy IQM2), Santa Clara County (portal migrating).
4. **Zoning host ambiguity:** San Ramon (EncodePlus vs Municode), Walnut Creek & Sausalito & Calistoga & Cotati (CodePublishing vs eCode360), Cloverdale & San Bruno & Burlingame & Daly City & Redwood City & South SF (zoning host not surveyed — pilot cities, minutes-only).
5. **Regulating body ≠ "Planning Commission" (record/verify):** Alameda (**Planning Board**), Albany (Planning & Zoning Commission), Mountain View (**EPC**), Palo Alto & San Carlos (**Planning & Transportation Commission**), San Leandro (PC + Board of Zoning Adjustments), Monte Sereno (Site & Architectural Commission — `to_verify`), several counties (Board of Supervisors / Permit Sonoma).
6. **American Legal 403 (zoning):** Antioch, Danville, Fairfax, Cupertino, Palo Alto, Pinole, + American Canyon — `zoning_access` to_verify.
7. **Pilot-confirmed access blocks:** San Jose & Fremont (`akamai_403`); Redwood City (OnBase) & South SF (eScribe) unsupported by civic-scraper.
8. **Not collected by design:** earliest-online year (all rows) and discretionary-review existence (all rows) — `to_verify`, resolved during build per the brief.

---

## §D — One-paragraph read

**Yes — the Bay Area collapses into a small, fundable set of solution classes.** On the minutes side, four adapters cover 88 of 109 localities (Granicus-family 41, CivicPlus 27, CivicClerk-API 10, plus 10 irreducible custom-CMS one-offs), and on the zoning side three commercial code hosts cover 81 of 109 (Municode 44, CodePublishing 23, eCode360 14). The recommended **sequence** follows ROI: build the **Granicus/Legistar minutes adapter** and the **Municode zoning client** first (each unlocks the plurality and both are `clean`), then the **CivicClerk OData client** (10 localities, the proven Daly City method), then the **CivicPlus class** — whose Akamai exposure is now **resolved** (a full sweep found 25/28 clean and only 2 blocked: Fremont, Portola Valley), so it is a clean shared-adapter target, not an access risk. The long tail (eScribe, OnBase, PrimeGov, CivicWeb, NovusAgenda, Laserfiche, Municode-Meetings — 1–4 localities each) is real but small and can be deferred. Zoning is overwhelmingly tractable (94/109 `clean`); the only wall is American Legal (7 codes) behind a 403. **No locality is confirmed paper-only**, so there is no urgent in-person digitization trip — at most, eyeball the five platform-`unknown` small jurisdictions (Colma, Monte Sereno, Newark, Fairfax, San Rafael) before leaving. Net: the fixed cost is roughly **~4 minutes adapters + ~4 zoning clients + an Akamai-access decision**, which is estimable and bounded — the multi-jurisdiction build is fundable, and the right first dollar goes to the Granicus+Municode pair.

---

### Artifacts
- `bay_area_locality_census.csv` — the 109-row census (sort by `tractability_tier`, `minutes_solution_class`, or `county`).
- `bay_area_locality_census_report.md` — this roll-up.
- `consolidate.py` — reproducible: reads `raw/*.json` + Gazetteer GEOIDs → CSV + roll-ups.
- `raw/*.json` — the 8 per-county survey outputs (provenance).
- `ca_place_geoid.json` — Census Gazetteer CA place→GEOID lookup (join source).
