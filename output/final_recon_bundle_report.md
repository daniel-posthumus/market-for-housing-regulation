# Final Recon Bundle — Findings (depth fill · migration cliffs · HCD firm-up · pre-period verification)

**Date:** 2026-06-09 · **Author:** Claude Code · **Brief:** `.claude/instructions/final_recon_bundle.md`
**Outputs (no new schemas; appended/updated existing CSVs):**
- `civicplus_depth_probe/civicplus_depth.csv` (Task 1) + merged gains into `archive_depth_probe/archive_depth.csv`
- `migration_cliffs_probe/migration_cliffs.csv` (Task 2)
- `hcd_preemption_panel/hcd_preemption_panel.csv` (Task 3)
- `preperiod_envelope_probe/preperiod_envelope.csv` (Task 4 — corrected in place)

---

## Task 1 — CivicPlus depth fill (14 → 2 resolved)

**Method proven, but mostly JS-gated.** The AgendaCenter **Search endpoint**
(`/AgendaCenter/Search/?CIDs={cid}&startDate=..&endDate=..`) returns `ViewFile/Minutes/_<date>` links
(definitively minutes-typed) per year — a clean, legitimate depth read. It resolved **Campbell → 2006**
(continuous, verified: minutes present 2006/08/10/12/14/16; 2004 empty) and **Los Altos Hills → 2022**
(static-HTML floor; deeper archive not resolvable). **The other 12 could not be resolved:** they run the
**newer CivicEngage** portal, which renders the committee list/archive via JS, so the committee `CID`
the Search endpoint needs isn't obtainable from a static read (and `PreviousVersions` 404s). Access is
clean (Akamai probe) — the depth just needs a **browser-rendered session to grab each CID**, after which
the proven Search method resolves it. So: **1 of 14 widened the ≤2016 panel (Campbell 2006).** Honest
`unknown` (flagged `civicengage_js_gated`) for the rest — not `minutes_absent`.

## Task 2 — Migration cliffs (3/3 gain deep history)

The ~2024 "cliffs" were **domain/view migrations, not data loss** — all three legacy archives retain
deep minutes:

| Jurisdiction | Prior platform | Reachable? | Earliest legacy minutes | Note |
|---|---|---|---|---|
| **Marin County** | Granicus (`marin.granicus.com` v33) + `marincounty.org` old archive | **yes** | **2005** | BoS agendas+minutes+video online since 2005; separate 1995–2005 archive (no video) |
| **Santa Clara County** | IQM2 (`sccgov.iqm2.com`) | **yes** | **~2008** | legacy IQM2 still up; BoS meetings w/ Minutes back to 2008 (minutes-type strongly indicated) |
| **San Jose** | Granicus (`sanjose.granicus.com` v51) | **listing only** | **2005** | minutes-RSS lists 80 items 2005–2018 (depth CONFIRMED); but MinutesViewer → `sanjoseca.gov` DocumentCenter is **Akamai-blocked** (docs gated, not defeated) |

**These three big jurisdictions extend the panel backward** — Marin & SCC fully, San Jose
listing-confirmed-but-access-gated. Merged into `archive_depth.csv`.

## Task 3 — HCD preemption panel (the treatment variable — clean)

Built deterministically from the **authoritative HCD HE Review & Compliance dataset**
([data.ca.gov/dataset/housing-element-compliance-report](https://data.ca.gov/dataset/housing-element-compliance-report),
already pulled to `zoning_envelope_project/hcd.csv`) + the **HCD Prohousing Designated Jurisdictions**
list. **All 25 estimation-sample localities have authoritative, sourced compliance dates.** Per the
brief, I recorded **both** date conventions and did not collapse them: `he_adoption_date` (HCD-received
adopted element ≈ self-adoption) **and** `hcd_certification_date` (HCD formal compliance finding), with
the ABAG 6th-cycle statutory deadline (2023-01-31) as the exposure-window anchor. **Builder's-remedy
exposure spans 0 → 38.4 months** — strong treatment variation (Alameda certified pre-deadline → 0;
Oakland ~0.5mo; … Daly City 22mo; Santa Rosa 31mo; Clayton 38mo). **13 of 25 are prohousing-designated**
(5 with HCD-sourced dates: Mountain View/Petaluma Jan-2024, Walnut Creek Aug-2024, Santa Rosa/Sonoma
County 2025; 8 designated with exact date `to_verify` in the HCD tracker XLS). **Caveat:** the HCD
dataset is a **current-status snapshot** — pre-certification decert→recert history is not in it (all 25
are currently "In"); flagged in `status_sequence`. The treatment variable is **clean and analytically
usable**; only the prohousing *dates* (secondary) and any contested decert history are `to_verify`.

## Task 4 — Pre-period verification (corrected in the file)

Re-adjudicated the four dubious finds **and verified the file matches**:

| Locality | Original | Verdict | Corrected to |
|---|---|---|---|
| Solano County | Oro-Valley-AZ PDF (shared host) | **already corrected** (it was the CodePublishing code page, 2015) | `ordinance_text` 2015 ✓ |
| Walnut Creek | Oro-Valley-AZ PDF | **already corrected** | `ordinance_text` 2014 ✓ |
| Palo Alto | `Symposium.pdf` | **already corrected** (not a zoning map) | `none_found` ✓ |
| **San Ramon** | `rezoning.pdf` | **FALSE POSITIVE** — it's a 7-page "Rezoning to P-1 Submittal Requirements" application doc | **→ `none_found`** |
| **Daly City** | "Zoning Ordinance.pdf" under `/gpu/` | **DOWNGRADED** — under a General-Plan-Update path, 404'd/unconfirmable as adopted | **→ `none_found`** |

**Corrected usable pre-period count: 17 → 15/25** (3 spatial zoning-map PDFs: San Leandro 2016,
Sunnyvale 2014, Tiburon 2015[2006-effective]; 11 ordinance-text code captures; + SF reference). 1
NZLUD-proxy (Burlingame, post-SB-35 caveat); **9 `none_found`**. **The file `preperiod_envelope.csv` now
contains zero live false-positive source URLs and matches this narrative** (verified below).

---

## Consolidated `REVIEW:` / `to_verify` (all four tasks)

1. **CivicPlus depth (12 localities)** — Antioch, Atherton, Cotati, Half Moon Bay, Millbrae, Monte
   Sereno, Oakley, Pleasant Hill, San Anselmo, San Pablo, Windsor, Yountville: depth needs a
   browser-rendered session to obtain the AgendaCenter committee CID, then the proven Search method
   resolves it. (Not access-blocked; CivicEngage JS-gated.)
2. **San Jose pre-migration minutes** — confirmed to exist 2005–2018 (Granicus RSS) but docs are
   Akamai-blocked; a decision item (browser-automation/official request) if San Jose's deep history is
   wanted. Marin County & Santa Clara County legacy archives are reachable — no decision needed.
3. **HCD prohousing dates (8 localities)** — designated but exact date only in HCD's tracker XLS
   (SF, Alameda, El Cerrito, San Leandro, Oakland, Sunnyvale, Emeryville, Hayward). Secondary variable.
4. **HCD decert/recert history** — the compliance dataset is a current-status snapshot; if any sample
   locality's compliance was contested/decertified-then-recertified, that sequence isn't captured
   (all 25 currently "In") — `to_verify` only for contested cases.
5. **Pre-period `none_found` (9)** — Albany, Emeryville, Santa Rosa (eCode360); Palo Alto, Fairfax
   (American Legal **legacy** platform, not Wayback-queried); Petaluma (public.law); Piedmont
   (city-site); **San Ramon, Daly City** (killed in Task 4). Pre-period likely recoverable from each
   code publisher's amendment/version history — a moderate build task, not historical reconstruction.
6. **Burlingame** — only NZLUD 2019–21 (post-SB-35); needs a true ≤2018 source.

## Closing read

With these four tied off, the feasibility picture is firm. **Confirmed ≤2016 panel breadth rose from 32
to 36 localities** (Campbell 2006 + the three migration-cliff jurisdictions: Marin County 2005, Santa
Clara County 2008, San Jose 2005), and **all three migration cliffs add real deep history** — the
biggest jurisdictions in the set extend the panel backward, two fully reachable and San Jose
listing-confirmed-but-access-gated. The **HCD treatment variable is clean**: every estimation-sample
locality has authoritative, sourced compliance dates with 0–38-month builder's-remedy exposure variation
— the load-bearing input to the substitution test is solid, with only secondary prohousing dates and
contested-decert history left as `to_verify`. The **pre-period envelope is cheap for ~60% (15/25)** after
honestly killing two false/draft sources, with the remainder a bounded publisher-version-history dig. The
one soft spot is **CivicPlus depth** (12 newer-CivicEngage localities need a browser session for the
CID), but that does not gate the core result — those are mostly small cities outside the high-value
panel. **Recon is complete: the remaining open questions are DECISIONS, not probes** — the conceptual
by-right-vs-ministerial definition (the model's hinge) and the strategic deep-history/ratchet investment
(San Jose Akamai access, pre-2010 reconstruction). Nothing further is measurable; the project can move
from probing to building and deciding.

## Self-verification (required)

Confirmed that every correction in this report is reflected in the output files:
- `preperiod_envelope.csv`: San Ramon & Daly City = `none_found`; **0 live false-positive source URLs**
  (`orovalley`/`symposium`/`rezoning.pdf` appear only in correction *notes*); usable = **15/25**.
- `archive_depth.csv`: Campbell 2006, Los Altos Hills 2022, Marin County 2005, Santa Clara County 2008,
  San Jose 2005 merged; ≤2016 known = **36**.
- `hcd_preemption_panel.csv`: 25 rows, all with sourced HCD dates; 13 prohousing.
- `migration_cliffs.csv`: 3 rows. `civicplus_depth.csv`: 14 rows (Campbell 2006, Los Altos Hills 2022).
**The files and the narrative agree.**
