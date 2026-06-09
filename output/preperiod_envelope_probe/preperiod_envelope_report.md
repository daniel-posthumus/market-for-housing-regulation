# Pre-Period Zoning Envelope Probe — Findings (the map, circa 2016)

**Date:** 2026-06-09 · **Author:** Claude Code · **Brief:** `.claude/instructions/preperiod_envelope_probe.md`
**Artifact:** `preperiod_envelope.csv` (25 estimation-sample localities). Join key `fips_geoid`.

> **One-line finding:** The core substitution test's **pre-period (~2016) envelope is cheaply available
> for ~2/3 of the estimation sample** — **17 of 25** localities have a datable in-window (2014–2018)
> source via the Wayback Machine (12 archived code-page use-tables + 5 archived zoning-map PDFs + SF in
> hand). The remaining **8/25** (7 `none_found` + 1 NZLUD-only) are a **real but bounded** cost item —
> a moderate dig into code-publisher version history, **not** full historical reconstruction.

## Scope & method

**Estimation sample = the 25 localities with verified minutes ≤2016 (high/med confidence)** from
`archive_depth.csv` — only panel localities need a pre-period envelope. For each, I queried the
**Wayback Machine CDX API** (capture timestamp = verifiable vintage) for in-window (2014–2018) captures
of (a) the municipal **code** page (a 2016 use-table = text-form envelope) and (b) a city-site
**zoning-map PDF** (spatial). Locate-and-date only — no downloads, no reconstruction. Every recorded
source has an observed capture year; matches were **basename-verified** to reject false positives
(rejected: an Oro-Valley-AZ PDF on the shared `codepublishing.com` host that polluted Solano County &
Walnut Creek; Palo Alto's "Symposium.pdf"; an SF SoMa area-guide).

## Coverage summary

| preperiod_source_found | # | form |
|---|---|---|
| `ordinance_text` (Wayback code-page use-table) | 11 | text |
| `zoning_pdf` (archived zoning-map PDF) | 5 | spatial |
| `gis_layer` (SF reference — DataSF historical + project) | 1 | spatial |
| `nzlud_proxy` (2019–21 only) | 1 | text |
| `none_found` | 7 | — |

**Usable in-window pre-period source: 17/25 (68%)** — 6 spatial (SF, San Leandro, San Ramon, Sunnyvale,
Tiburon + …) / 12 text. By vintage: the **zoning-map PDFs are cleanest** (2014–2016: Sunnyvale 2014,
San Ramon 2015, Tiburon 2015[2006-effective], San Leandro 2016); the **code-page captures cluster
2014–2018** (Walnut Creek/San Carlos/Daly City 2014, Solano County 2015, Alameda/Oakland/Mountain
View/Sonoma County 2017, El Cerrito/Hayward/Clayton/Milpitas 2018).

## Critical-path read (decisive)

**The core test's pre-period is cheap for the majority and only moderately expensive for a minority —
it is not a showstopper.** Two-thirds of the estimation sample (17/25) have a Wayback-datable ~2016
envelope, and the **cheapest path is the code-page capture**: Municode and CodePublishing retain
crawlable 2014–2018 snapshots of the use-table, so one method (Wayback CDX on the code library) unlocks
the dozen Municode/CodePublishing cities at once, supplemented by the five archived zoning-map PDFs for a
spatial pre-period where present. The **8/25 gap (7 `none_found` + Burlingame NZLUD-only)** is real but
**bounded and patterned**: it concentrates in **eCode360** (Albany, Emeryville, Santa Rosa),
**American-Legal-legacy** (Palo Alto, Fairfax), **public.law** (Petaluma), and one city-site case
(Piedmont) — platforms whose code pages either weren't crawled at the URLs I queried or sit on a legacy
host. For these the 2016 use-table **almost certainly still exists** in the publisher's own
amendment/version history (the brief's source #2), so recovering it is a **moderate per-locality task,
not the expensive historical-reconstruction workstream**. **Two honest caveats:** (1) the code-page
captures cluster **2017–2018**, which is pre-SB-9/SB-423/builder's-remedy (2021–23) but *post*-SB-35
(2017) — clean for the main preemption ramp, marginal for an SB-35-specific margin (the 2014–2016
zoning-map PDFs are the cleaner pre-period anchors); (2) **Burlingame's only source is NZLUD 2019–21**,
which is *after* SB-35 — not a valid pre-period and flagged. Net: budget a **moderate pre-period
recovery pass** (Wayback code pages + a publisher-version-history dig for ~8 cities); the substitution
test's pre-period is feasible at the breadth the panel needs. Deep (pre-2010) ratchet maps remain
separately deferred (out of scope here; none chased).

## Consolidated `REVIEW:` / `to_verify`

1. **`none_found` (7) — pre-period likely recoverable from the code publisher's version history, not
   Wayback:** **eCode360** (Albany, Emeryville, Santa Rosa), **American Legal legacy** (Palo Alto,
   Fairfax — 2016 code on `library.amlegal.com`, not the queried `codelibrary.amlegal.com`),
   **public.law** (Petaluma), **city-site PDF** (Piedmont). `to_verify` via each publisher's
   amendment/"superseded version" feature (a moderate build-time task).
2. **`REVIEW:` Burlingame** — only NZLUD (2019–21, post-SB-35) found; needs a true ≤2018 source for a
   valid pre-period (it is the one `in_nzlud=yes` city in the sample).
3. **`to_verify` San Ramon** — the 2015 capture is `rezoning.pdf`, possibly a single rezoning exhibit
   rather than the citywide zoning map; confirm it is the full envelope.
4. **`to_verify` Daly City** — the 2014 "Zoning Ordinance.pdf" sits under a General-Plan-Update path;
   confirm it is the adopted ordinance, not a GPU draft.
5. **Highest-ROI shared source:** **Wayback code-page captures** cover the Municode/CodePublishing
   cohort with one method — the cheapest path to the text-form pre-period at scale.
6. **Vintage tension (cross-cutting):** code-page captures cluster 2017–2018 (post-SB-35); prefer the
   2014–2016 zoning-map PDFs as anchors where both exist, and treat 2017–2018 use-tables as "early-ramp"
   rather than strictly pre-SB-35.
7. **Deep-history leads:** none chased (pre-2010 ratchet maps remain the deferred workstream).

---

### Artifacts
- `preperiod_envelope.csv` — 25 rows (`locality, fips_geoid, minutes_start_year, preperiod_source_found, source_url, observed_vintage, form, confidence, notes`).
- `preperiod_envelope_report.md` — this report.
- `wayback_probe.py`, `finalize.py` — reproducible (Wayback CDX queries + verified corrections).
- `_wayback_results.json` — raw probe output (provenance).
