# API Depth-Read Probe — Findings (panel window, locked)

**Date:** 2026-06-08 · **Author:** Claude Code · **Brief:** `.claude/instructions/api_depth_read_probe.md`
**Artifacts:** `archive_depth_api.csv` (55 probed rows); merged into `archive_depth.csv` (the 19 verified
years replace prior `unknown`/agenda-floor values). Join key `fips_geoid`.

> **One-line finding:** A **~2016-start panel is feasible at usable breadth** — **32 localities** have
> minutes from ≤2016 (25 at high/med confidence; 11 freshly API-verified to the document level) — and
> that is a **floor**, since 14 CivicPlus + others are *unmeasured-not-absent*. But the probe also
> **confirmed the agenda-trap is real and material** (Oakland's apparent "2000" minutes are actually
> **2014**; Santa Rosa "1999"→**2016**; Hayward "2013"→**2015**), so the **deep (≤2010) panel is
> genuinely scarce online** — the ratchet/long-history extension stays breadth-constrained.

## What this probe did

Queried each portal's **API** (metadata only — file type + date; no downloads, no build) to convert
shallow-probe `unknown` depths into **verified earliest-MINUTES-type years**. A year counts only if the
API returned an actual Minutes-type file/item — agendas/packets/video do not. Methods used, behaving as
a legitimate client: **CivicClerk OData** (paginated `publishedFiles type=Minutes`), **Legistar webapi**
(`EventMinutesFile`), **PrimeGov** `ListArchivedMeetings`, **Granicus** minutes-RSS.

## Results: 19 localities newly verified to the document level

| Verified earliest minutes (API) | Localities |
|---|---|
| 2005 | Alameda (Legistar) |
| 2008 | El Cerrito (CivicClerk), San Carlos (PrimeGov) |
| 2009 | Albany (PrimeGov), Daly City (CivicClerk) |
| 2014 | **Oakland** (Legistar — *was 2000 by dropdown*), Burlingame (Legistar) |
| 2015 | **Hayward** (Legistar — *was 2013*), Emeryville (Legistar), Sonoma County (Granicus RSS) |
| 2016 | **Santa Rosa** (Legistar — *was 1999*) |
| 2017 | Hercules, Napa, San Mateo County (Legistar) |
| 2019 | Pinole (CivicClerk), San Mateo (PrimeGov) |
| 2021 | Napa County (Legistar) |
| 2023 | Contra Costa County (Legistar), Pleasanton (CivicClerk — portal itself starts 2023) |

**Plus:** **Cupertino** → `minutes_absent_in_api` (Legistar shows events status-Final back to 2005 but
**no minutes files** — minutes posted elsewhere). **Sonoma (city)** → minutes confirmed to **pre-date
2017** (CivicWeb "prior to 2017" bucket) but exact year not exposed. **34** targets → `no_api_depth_readable`.

## Updated depth distribution (109 localities)

| Bucket | shallow probe | **after API probe** |
|---|---|---|
| ≤2005 | 6 | **4** |
| 2006–2010 | 7 | **9** |
| 2011–2015 | 13 | **15** |
| 2016+ | 28 | **36** |
| **unknown** | **55** | **45** |

`unknown` fell 55→45 (10 net resolved; +9 more years corrected/confirmed). **Known minutes-start now
64/109** (49 high/med confidence; 19 API-verified to the document level this probe).

## The agenda-trap, confirmed and material

The shallow probe's year-dropdown floors **systematically overstate minutes depth**, because dropdowns
span *agendas/events*, which predate posted minutes. Hard API checks:
- **Oakland**: dropdown 2000 → **minutes 2014** (14-year overstatement).
- **Santa Rosa**: dropdown 1999 → **minutes 2016**. **Hayward**: 2013 → **2015**.
- (Alameda 2005 and Emeryville 2015 were confirmed accurate.)

This validates why an API/minutes-type read was necessary, and means **any remaining non-API dropdown
floor in the 64 "known" should be treated as an upper bound on depth** until API-confirmed (most are
Legistar — trivial to confirm at build time).

## Panel-window read (the decisive paragraph)

**Widest common start with usable breadth ≈ 2016.** Counting localities with minutes continuous-capable
from each bar: **≤2014 → 25** (18 high/med, 7 hard-verified); **≤2016 → 32** (25 high/med, 11 hard);
**≤2018 → 41** (33 high/med). **A ~2016 panel therefore has enough breadth to estimate a
strategic-interaction reaction function around the 2017–2023 preemption ramp** — 32 co-submarket
localities at a common ≤2016 start is workable, and it is a **floor, not a ceiling**: the 45 still-`unknown`
are overwhelmingly a **method limit** (CivicPlus AgendaCenter ×14 and IQM2/Municode/custom CMS expose no
clean document-type/date API), **not** proven-shallow archives — most will resolve to 2010s-era minutes
once read at build time (the CivicPlus access path is already proven clean; it just needs AgendaCenter
scraping rather than an API). The **deep (≤2010) panel is the opposite story**: only **13** localities
reach that far, and the probe *removed* inflated deep claims (Oakland 2000→2014), so deep history is
genuinely sparse online — the **ratchet/long-run extension is breadth-constrained and needs the deferred
deep-archive + physical-records workstream**, not the live portals. Net: the core feasible result (the
preemption-substitution test on a ~2016+ panel) is **solidly feasible at breadth**; the long-history
ratchet result is **not** broadly supported by online minutes and should be scoped separately.

## Consolidated `REVIEW:` / `to_verify`

1. **`no_api_depth_readable` (34) — resolvable at build time, not by API:**
   - **CivicPlus AgendaCenter (14)** — Antioch, Atherton, Campbell, Cotati, Half Moon Bay, Los Altos
     Hills, Millbrae, Monte Sereno, Oakley, Pleasant Hill, San Anselmo, San Pablo, Windsor, Yountville.
     No document-type/date API; depth needs AgendaCenter scraping (access is clean — see Akamai probe).
   - **IQM2 (2)** Pacifica, Santa Clara County; **Municode Meetings (2)** Brisbane, Los Gatos;
     **custom CMS (4)** Alameda County, Colma, Novato, Ross; **eScribe** Brentwood; **NovusAgenda**
     Union City; **OnBase** Concord; **CivicWeb** American Canyon, Calistoga; **Newark**; **Foster City**
     (legacy CivicClerk, no modern API).
2. **`minutes_absent_in_api`:** **Cupertino** — Legistar has events but no minutes files; locate the
   minutes source (city site?) at build time.
3. **Migration cliffs — pre-migration minutes elsewhere:** **Marin County** (~2024), **Santa Clara
   County** (BoS → OneMeeting, 2024), **San Jose** (Akamai). Current portals start ~2024; find the
   prior system for older minutes.
4. **`to_verify`:** **San Leandro** (Legistar slug unresolved — retry); **Sonoma** city (exact pre-2017
   year via CivicWeb filetree); remaining non-API dropdown floors in the 64 "known" (Legistar-confirm).
5. **Already correctly parked (not re-probed):** access-blocked (Fremont, Portola Valley, San Jose,
   Redwood City, South SF, Fairfield, Suisun) and minutes-may-not-exist-online (Vallejo, Sebastopol,
   Benicia, Dixon) — physical-records workstream candidates.

---

### Artifacts
- `archive_depth_api.csv` — 55 probed rows (`locality, fips_geoid, platform, api_endpoint_used, earliest_minutes_year, minutes_type_confirmed, continuity, gap_notes, confidence, method, flags`).
- `archive_depth.csv` — updated (19 verified years merged; method tagged `[API depth-read]`).
- `archive_depth_api_report.md` — this report.
- `api_probe.py`, `assemble_api.py` — reproducible (API queries + merge + histogram).
