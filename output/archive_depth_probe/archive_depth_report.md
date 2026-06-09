# Archive-Depth Probe — Findings (minutes time dimension)

> **⚠️ SUPERSEDED IN PART (2026-06-08):** the API depth-read probe resolved 19 of the `unknown`/
> dropdown-floor rows to verified minutes years and corrected the agenda-trap (Oakland 2000→2014,
> Santa Rosa 1999→2016, Hayward 2013→2015). For the current depth numbers and panel-window read see
> **`archive_depth_api_report.md`**; `archive_depth.csv` has been updated with the verified years.
> The histogram below is the *original shallow-probe* snapshot, kept for provenance.


**Date:** 2026-06-08 · **Author:** Claude Code · **Brief:** `.claude/instructions/archive_depth_probe.md`
**Artifact:** `archive_depth.csv` (109 rows, join key `fips_geoid`). This is the panel-feasibility read.

> **One-line finding:** The panel's *time dimension is thinner and less certain than its access
> dimension.* Posted **minutes** (not agendas) are systematically shallow — verified minutes-start
> years cluster **2014–2023** — and depth is **genuinely unverifiable from a shallow probe for ~half
> the localities** (55/109) because their archives are JS-portal/API-only or access-blocked. A deep
> (pre-2010) panel is verifiably supported by only a handful of localities today.

## Method & honesty caveats (read first)

- **Shallow, listing-level, no retrieval.** Per the brief: read the archive's date range / oldest
  *minutes* entry as a legitimate browser; no downloads, no evasion, time-boxed, **unknown over guess.**
- **`unknown` (55 rows) is mostly a METHOD limit, not proven shallowness.** Most unknowns are CivicClerk/
  PrimeGov/IQM2/Municode-Meetings/OnBase/eScribe **JS-shell or API portals** whose archive depth isn't
  in static HTML, plus access-blocked sites (Akamai/403/ECONNREFUSED). Where an agent used the
  platform's **API** (Legistar `webapi`, CivicClerk OData, PrimeGov), it *did* read verified
  minutes-file years — so most "unknown" depths are **recoverable at build time**, just not by a
  static read. Treat `unknown` as "not measured shallowly," not "no archive."
- **Minutes ≠ agendas, and the gap is real.** Several API reads show agendas predating minutes by
  years (Milpitas agendas 2012 / minutes 2016; Gilroy agendas 2022 / minutes 2023; Vallejo agendas
  to 2006 / **no minutes at all** in the portal). Many year-dropdown "floors" are agenda ranges and
  are flagged `dropdown_range_minutes_not_confirmed` / `minutes_type_unverified` — **not** confirmed
  minutes depth.
- **Platform migrations truncate online minutes (~2024).** Marin County, Santa Clara County (BoS →
  "OneMeeting"), and San Jose show online minutes effectively starting ~2024 on the *current* system,
  with older records on a prior platform. A migration is a visible-archive cliff, not true depth.

## 1. Depth distribution (earliest **minutes** year)

**All rows (optimistic — includes unverified dropdown/agenda floors):**

| Bucket | # localities |
|---|---|
| ≤2005 | 6 |
| 2006–2010 | 7 |
| 2011–2015 | 13 |
| 2016+ | 28 |
| **unknown** | **55** |

**Verified subset (27/109 — high/med confidence, *actual minutes* not a dropdown/agenda range):**

| Bucket | # | Localities |
|---|---|---|
| ≤2005 | 2 | San Francisco (1998), Solano County (2005) |
| 2006–2010 | 2 | Piedmont (2007), Petaluma (2010) |
| 2011–2015 | 5 | San Ramon (2011), Tiburon (2012), Fairfax (2013), Mountain View (2014), Sunnyvale (2014) |
| 2016+ | 18 | Clayton/Milpitas/Palo Alto (2016); Lafayette/Larkspur/Morgan Hill (2017); Santa Clara (2018); Rio Vista (2019); Dublin/San Rafael (2020); Belvedere/Vacaville (2021); Los Altos (2022); Belmont/East Palo Alto/Gilroy/Pittsburg (2023); Marin County (2024) |

**Continuity:** continuous 41 · gaps 2 (Fairfax 2025 missing; Cloverdale 2012 missing) · unknown 66.

**The signal:** even reading optimistically, only **13 localities** reach ≤2010 and most of those are
unverified dropdown floors; in the **verified** set only **4** reach ≤2010 (SF, Solano County,
Piedmont, Petaluma). The modal verified minutes-start is **~2016**, i.e. the spread of modern civic
portals — exactly when CivicClerk/PrimeGov/CivicEngage adoption took off.

## 2. Panel-window read

The realistic **balanced-panel start is ~2016**, not earlier. From ~2016 a usable number of localities
have continuous posted minutes (the 18 verified 2016+ cases, plus most of the 55 `unknown` modern-portal
cities whose APIs will likely confirm a similar 2014–2018 floor at build time). A **~2014 window** is
plausible for the Legistar cohort (Mountain View, Sunnyvale, and others whose Legistar archives start
2013–2014). A **deep (≤2010) panel is not broadly supported online**: only a handful of localities
verifiably reach that far (SF 1998, Solano County 2005, Petaluma 2010, plus dropdown-floor candidates
Oakland ~2000, Walnut Creek 2006, San Mateo County 2009 that need API confirmation). **This is the
core tension to flag:** the model's ratchet and deep pre-preemption dynamics want long history, but the
online minutes record mostly begins in the 2014–2017 portal era. The good news for the *preemption*
test specifically: a 2016-start panel still straddles the key state-preemption ramp (SB-35 2017 →
builder's-remedy 2023), so the pre/post-preemption substitution test is feasible on the achievable
window even though the long-run ratchet results would need the deferred deep-history (API-deep-dive for
2005–2015 where it exists, then physical/records-request for the pre-online era).

**Cheapest path to firming this up:** the shallow probe under-measured depth by ~half. A **build-time
API depth-read** (Legistar `webapi` `EventMinutesFile`, CivicClerk OData `publishedFiles type=Minutes`,
PrimeGov `ListArchivedMeetings`) would convert most of the 55 `unknown` rows into verified years cheaply
— that, not more web-scraping, is the next step to lock the panel window.

## 3. Consolidated `REVIEW:` / `to_verify`

**A. Depth unverifiable shallowly — resolve via platform API at build time (the big bucket, ~48 rows):**
CivicClerk portals (Daly City, Foster City, El Cerrito, Pinole, Pleasanton, Vallejo*), PrimeGov (Albany,
San Carlos, San Mateo), IQM2 (Pacifica, Santa Clara County), Municode-Meetings (Brisbane, Los Gatos),
OnBase (Concord, Redwood City), eScribe (Brentwood, South SF), CivicWeb (American Canyon, Sonoma),
NovusAgenda (Union City), plus CivicPlus JS-accordion archives (Campbell, Saratoga, Millbrae, Oakley,
Pleasant Hill, Los Altos Hills, Monte Sereno, Cotati, Windsor, Yountville, Atherton, Half Moon Bay),
and Legistar floors needing minutes-file confirmation (Cupertino, Hercules, Contra Costa County, Napa,
Napa County, Burlingame, San Mateo County, Santa Rosa, Rohnert Park).

**B. `REVIEW:` minutes may not exist online at all (candidate for deferred physical-records workstream):**
- **Vallejo** — CivicClerk portal has agendas+video to 2006 but **no minutes-type files anywhere**.
- **Sebastopol** — custom CMS shows **video only**, no written-minutes archive; site directs to records request.
- **Benicia** — Granicus minutes publisher lists commissions only; **City Council minutes not located online**.
- **Dixon** — online Granicus recent only; Council DVDs 1998–2009 are **offline at City Hall**.

**C. `REVIEW:` access-blocked (depth unverifiable, from prior probes):** Fremont, Portola Valley, San
Jose (Akamai); Redwood City (OnBase), South SF (eScribe) unsupported; Fairfield/Suisun (403);
American Canyon/Calistoga (ECONNREFUSED — may be transient, retry at build time).

**D. `REVIEW:` migration cliffs (older minutes on a prior system):** Marin County (~2024), Santa Clara
County (OneMeeting Jan–May 2024), San Jose — confirm where pre-migration minutes live.

**E. `to_verify` gaps:** Fairfax (2025 missing from list), Cloverdale (2012 missing).

---

### Artifacts
- `archive_depth.csv` — 109 rows (`locality, fips_geoid, minutes_url, earliest_minutes_year, continuity, gap_notes, type_basis, confidence, method, flags`).
- `archive_depth_report.md` — this report.
- `consolidate.py` — reproducible (reads `raw/*.json` + census GEOIDs → CSV + histograms).
- `raw/*.json` — the 8 per-county probe outputs (provenance).
