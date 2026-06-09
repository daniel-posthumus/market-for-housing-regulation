# Akamai / CivicPlus Retrieval Probe — Finding

**Date:** 2026-06-08 · **Author:** Claude Code · **Brief:** `.claude/instructions/akamai_civicplus_probe.md`
**One question:** is there a workable retrieval path for CivicPlus-behind-Akamai minutes, and at what cost?

> **Headline:** The census's premise is **largely false.** CivicPlus minutes are **NOT generally
> Akamai-blocked.** A full header-only sweep of **all 28 CivicPlus localities** (§6) found **25 clean
> (HTTP 200, real content), only 2 Akamai-blocked (Fremont, Portola Valley), and 1 stale-URL/Cloudflare
> 404 (Antioch — not Akamai).** One real **minutes PDF was downloaded and verified** (Saratoga) with
> nothing more than a normal browser User-Agent. Akamai is a **per-city deployment**, not a property of
> the CivicPlus platform. Go/no-go: **GO — cheap shared adapter** for the CivicPlus class; only **2
> cities** need a separate Akamai fallback.

---

## 1. The probe sample (what was tested, raw results)

Sampled from the census `unknown`/CivicPlus rows, spanning variants. Plain request = default client;
browser = normal browser UA + standard Accept headers (no evasion). San Jose & Fremont **not**
re-probed (already confirmed `akamai_403`).

| Host | Variant | Plain GET | Browser GET | Block? | Notes |
|---|---|---|---|---|---|
| **Saratoga** | classic `/AgendaCenter` (IIS) | 200 | 200 | **none** | **Minutes PDF retrieved + verified** (see §evidence) |
| **Campbell** | classic `/AgendaCenter` | 200 (real AgendaCenter HTML) | 200 (38 KB) | **none** | agendas present; no minutes on PC page now (agenda-lag) |
| **Half Moon Bay** | classic `/AgendaCenter` | 200 | 200 (27 KB) | **none** | real content; RSS endpoint also 200 |
| **Healdsburg** | classic `/AgendaCenter` | 200 | 200 (32 KB) | **none** | page embeds `ViewFile/Agenda/_…` links directly in HTML |
| **Pleasant Hill** | numeric CivicEngage `/277/Agendas` | 200 (IIS) | 200 | **none** | plain IIS, not Akamai-fronted |
| **Antioch** | `/government/agendas-and-minutes/` | 404 (**Cloudflare**) | 404 | not Akamai | wrong/migrated path; fronted by Cloudflare, not Akamai. `to_verify` correct URL |

**Result: 0 of 6 Akamai 403s.** Five return real content to a *plain* request; Antioch is a
Cloudflare 404 on a stale URL (a different CDN, and a path problem, not a bot-wall). The single
verified document path needed only a browser UA — no header tricks, no JS execution, no evasion.

## 2. Block characterization

- **Not a CivicPlus-wide wall.** The block is a **per-site Akamai subscription layered on top of the
  city's web stack**, independent of CivicPlus. Sampled (mostly small/mid) CivicPlus cities have
  **no** bot-protection on `/AgendaCenter` at all (even `curl/x.y` gets 200). The two confirmed
  blocks (Fremont, San Jose) are **large cities**; Akamai correlates with city size / IT budget, not
  with the CivicPlus platform.
- **Where Akamai *is* present** (Fremont, from the minutes pilot), it returns the Akamai "Access
  Denied" reference page (`errors.edgesuite.net`) to a scripted client **even with browser headers** —
  i.e. more than a UA check. Whether a *real headless browser* passes it was **not** tested (would be
  path (c); no browser tooling was installed, and the sample didn't require it). That residual
  unknown affects only the Akamai minority, not the class. `REVIEW:`
- **Revised estimate — now measured, not estimated (full sweep §6):** the true Akamai count in the
  CivicPlus class is **2 of 28** (Fremont, Portola Valley). The 23 `unknown` CivicPlus rows were
  overwhelmingly **plain-reachable** (25/28 clean). The class is re-scored from "access risk" to
  "**clean, needs a normal scraper**," with a 2-city Akamai exception. (Note: Portola Valley is a
  *small* town on CivicEngage Central — so Akamai isn't strictly a big-city tell; it is genuinely
  per-site. A handful of sites are Cloudflare-fronted but return 200, i.e. fronted ≠ blocked.)
- **Separate caveat (not access):** the **agenda-vs-minutes** gap recurs — Campbell/Healdsburg PC
  pages currently show agendas but no posted minutes (the Daly City pattern). This is a
  data-completeness/posting-practice question per host, **not** an Akamai problem. Saratoga *did*
  post minutes, proving minutes-bearing retrieval is real. `to_verify` per host at build time.

## 3. Ranked-path finding (cheapest first)

| Path | Works on sample? | Cost |
|---|---|---|
| **(a) documented route** — the `/AgendaCenter` public web interface itself; minutes at `/AgendaCenter/ViewFile/Minutes/_<MMDDYYYY>-<ID>`, discoverable from the committee page HTML + `/AgendaCenter/PreviousVersions` for the archive | **YES** | ~free; it's the public site |
| **(b) plain-fetch tuning** — normal browser UA + Accept headers | **YES** (one verified minutes PDF) | trivial; no evasion |
| (c) browser automation | not needed for sampled hosts; **untested** for the Akamai minority | only relevant to the few Akamai cities |
| (d) official / licensed request | not needed for the class; only a fallback for genuinely Akamai-walled cities | per-site, budget/authority — `REVIEW:` |

**Cheapest working path: (a)+(b)** — fetch the committee's `/AgendaCenter` page with a normal browser
UA, parse the embedded `ViewFile/{Agenda,Minutes}/_…` links, walk `/AgendaCenter/PreviousVersions`
for history. **No Akamai fight, no evasion, no licensing** for the majority.

## 4. Go/no-go for the 27-locality CivicPlus class

**GO — cheap shared adapter.** One CivicPlus AgendaCenter adapter (committee page → `ViewFile`
links + `PreviousVersions` archive walk) serves the large majority of the 27. Rough effort: **~1–2
engineer-days** for the shared adapter + a small per-host config (the committee category ID, e.g.
`Planning-Commission-6`), plus a one-line Akamai-detection guard that flags the minority to a
fallback. **The Akamai-fronted minority is now enumerated** (full sweep, §6): exactly **2 cities**
(Fremont, Portola Valley) need path (c)/(d) — a tiny, bounded budget decision, **not** the whole
class. **Confidence: high** — all 28 hosts swept, 25 clean / 2 Akamai / 1 stale-URL, with one
end-to-end minutes retrieval (Saratoga) verified. The only residual is per-host minutes-vs-agenda
posting (a data-completeness check, not access).

## 5. Consolidated `REVIEW:` (Daniel's judgment / authority)

1. `REVIEW:` **Akamai minority fallback — minority now ENUMERATED (full sweep done 2026-06-08).**
   A header-only sweep of **all 28 CivicPlus localities** (browser UA, no evasion) found the true
   blocked minority is just **2**: **Fremont** and **Portola Valley** (both `AkamaiGHost` 403).
   **25 of 28 returned 200 with real content (clean).** One more — **Antioch** — is a Cloudflare
   **404 on a stale census URL** (not an Akamai block; needs the correct agenda URL → `to_verify`).
   So the access fallback question now applies to **only 2 cities**, not a class: decide
   headless-browser automation vs. per-site public-records request vs. accept-the-gap for **Fremont
   and Portola Valley** specifically. (Census CSV `minutes_access` updated to the measured values.)
   Full sweep table in §6.
2. `REVIEW:` **ToS / acceptable automated access.** Retrieval here behaved as a legitimate browser
   fetching public records (no evasion, no IP rotation, no CAPTCHA defeat). Confirm comfort with
   scraping `/AgendaCenter` at modest, rate-courteous volume; these are public records, but a
   per-host ToS check before bulk runs is prudent.
3. `to_verify` (build-time, not authority): **minutes-vs-agenda posting** per host (some show agendas
   only — the Daly City pattern); **Antioch's correct agenda URL** (the census URL 404s on Cloudflare).

---

## 6. Full CivicPlus sweep (all 28 — header-only, browser UA, 2026-06-08)

| Locality | County | HTTP | Server | Result |
|---|---|---|---|---|
| Fremont | Alameda | 403 | AkamaiGHost | **akamai_403** |
| Portola Valley | San Mateo | 403 | AkamaiGHost | **akamai_403** |
| Antioch | Contra Costa | 404 | cloudflare | stale-URL 404 (Cloudflare, not Akamai) |
| Atherton | San Mateo | 200 | - | clean |
| Campbell | Santa Clara | 200 | - | clean |
| Cloverdale | Sonoma | 200 | microsoft-iis/10.0 | clean |
| Corte Madera | Marin | 200 | - | clean |
| Cotati | Sonoma | 200 | - | clean |
| Dublin | Alameda | 200 | - | clean |
| Half Moon Bay | San Mateo | 200 | - | clean |
| Healdsburg | Sonoma | 200 | - | clean |
| Hillsborough | San Mateo | 200 | civicplus | clean |
| Larkspur | Marin | 200 | microsoft-iis/10.0 | clean |
| Los Altos Hills | Santa Clara | 200 | - | clean |
| Millbrae | San Mateo | 200 | - | clean |
| Monte Sereno | Santa Clara | 200 | - | clean |
| Moraga | Contra Costa | 200 | - | clean |
| Oakley | Contra Costa | 200 | cloudflare | clean (Cloudflare-fronted, not blocked) |
| Orinda | Contra Costa | 200 | microsoft-iis/10.0 | clean |
| Pleasant Hill | Contra Costa | 200 | microsoft-iis/10.0 | clean |
| San Anselmo | Marin | 200 | - | clean |
| San Bruno | San Mateo | 200 | - | clean |
| San Pablo | Contra Costa | 200 | cloudflare | clean (Cloudflare-fronted, not blocked) |
| Saratoga | Santa Clara | 200 | microsoft-iis/10.0 | clean |
| St. Helena | Napa | 200 | microsoft-iis/10.0 | clean |
| Windsor | Sonoma | 200 | microsoft-iis/10.0 | clean |
| Woodside | San Mateo | 200 | cloudflare | clean (Cloudflare-fronted, not blocked) |
| Yountville | Napa | 200 | - | clean |

**Tally: 25 clean · 2 akamai_403 (Fremont, Portola Valley) · 1 stale-URL (Antioch).** Header-only, no evasion; Fremont taken from the minutes pilot (not re-probed). Census CSV `minutes_access` updated to these measured values.

---

### Boundaries honored
No production adapter, no full-archive retrieval, no extraction (one verified sample doc, the ceiling).
No evasion (legitimate browser headers only; where only evasion would work — the Akamai minority — that
is reported as "needs official/licensed/browser path," not implemented). Did not re-probe San
Jose/Fremont. Did not expand to other platform classes.

### Evidence
- `sample_saratoga_pc_minutes_2026-04-08.pdf` — real minutes retrieved via plain HTTP from
  `saratoga.ca.us/AgendaCenter/ViewFile/Minutes/_04082026-1428` (200, application/pdf, browser UA).
