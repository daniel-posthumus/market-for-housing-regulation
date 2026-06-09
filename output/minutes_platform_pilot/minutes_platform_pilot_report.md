# Multi-Jurisdiction Minutes — Platform Classification & Scraper Pilot

**Date:** 2026-06-08 · **Author:** Claude Code · **Brief:** `.claude/instructions/minutes_platform_pilot.md`
**Scope (fixed by Daniel):** the **priority submarket set** — 14 SF co-submarket cities, not all nine counties.
**Pilot cities (chosen by Daniel):** Daly City (CivicClerk), San Jose (Legistar), Fremont (CivicPlus).

> **How to read this.** This is a *pilot* — its value is in surfacing what transfers and what
> breaks, honestly, before the multi-jurisdiction fixed cost is paid. Every uncertain item is
> tagged `REVIEW:` and collected in **§4**. The schema, SF pipeline, and labeling app were **not
> modified**; the only new code is an *additive, not-yet-wired* mapping layer (§3). I verified
> rather than assumed wherever I could; where I could not verify, I say so.

---

## §1 — Task 1: Platform classification (14 jurisdictions)

Platform identified from portal URL patterns and HTML vendor signatures (`*.legistar.com`,
`*.granicus.com`, `*.civicclerk.com`, `/AgendaCenter` = CivicPlus, `*.primegov.com`,
OnBase `AgendaOnline`, eScribe `escribemeetings.com`). **Confidence = my confidence in the
platform ID**, which is separate from whether minutes are *retrievable* (see §2). "Earliest
online" is approximate and is itself a `REVIEW:` item wherever marked.

| # | Jurisdiction | Body | Minutes archive entry point | Platform | Earliest online (approx) | Conf. | Notes |
|---|---|---|---|---|---|---|---|
| 1 | **San Jose** | Planning Commission | `sanjose.legistar.com` · `sanjose.granicus.com` (MinutesViewer) | **Legistar/Granicus** | Granicus minutes ~2008; agendas ~2006 | High (platform) | Minutes **Akamai-blocked** at the doc layer (§2). Legistar minutes status = "Draft", no file. |
| 2 | **Daly City** | Planning Commission | `dalycityca.portal.civicclerk.com` · API `dalycityca.api.civicclerk.com` | **CivicClerk** (modern) + legacy CivicPlus | API events seen 2023→; "Historical Agendas" page older | High (platform) | **Only pilot city where minutes were retrieved.** But minutes sparsely published (§2). |
| 3 | **Fremont** | Planning Commission | `fremont.gov/government/agenda-center` | **CivicPlus** (CivicEngage) | by-year archive (yr unverified) | Med | **Akamai-blocked**; civic-scraper returned 0 assets (verified). `REVIEW:` earliest year. |
| 4 | **Oakland** | Planning Commission | `oakland.legistar.com` · `oakland.granicus.com` | **Legistar/Granicus** | Granicus video ~2016; older unclear | Med | `REVIEW:` minutes archive depth / earliest year unclear. |
| 5 | **Berkeley** | Planning Commission | `berkeleyca.gov` (PDFs) · "Records Online" · `berkeley.granicus.com` (council video) | **Custom CMS + Laserfiche** (Granicus video only) | unclear | **Low** | `REVIEW:` PC minutes are city-hosted PDFs + a Laserfiche "Records Online" system; no standard vendor for PC minutes. |
| 6 | **Sunnyvale** | Planning Commission | `sunnyvaleca.legistar.com` | **Legistar/Granicus** | Legistar post-2013; "Pre-2014" tab to ~2008 | High | Clean Legistar instance. |
| 7 | **Mountain View** | **Environmental Planning Commission (EPC)** | `mountainview.legistar.com` · `mountainview.granicus.com` | **Legistar/Granicus** | video ~2008; "City Records" (Laserfiche) thru 2019 | Med | Body is **EPC**, not "Planning Commission" — schema-transfer/name-matching note. |
| 8 | **Palo Alto** | **Planning & Transportation Commission (PTC)** | `cityofpaloalto.primegov.com` · `paloalto.gov` (PDFs) | **PrimeGov** | PDF archive ~2007 | Med-High | Body is **PTC** — name-matching note. PrimeGov is civic-scraper-supported. |
| 9 | **Redwood City** | Planning Commission | `meetings.redwoodcity.org/AgendaOnline` | **Hyland OnBase** | 2002 (2002–2008 archive + later) | Med | **OnBase NOT supported by civic-scraper** — custom integration needed. |
| 10 | **Burlingame** | Planning Commission | `burlingameca.legistar.com` · `burlingameca.granicus.com` (MinutesViewer) | **Legistar/Granicus** | unclear | Med-High | Clean Granicus `MinutesViewer.php` present — promising. `REVIEW:` earliest year. |
| 11 | **South San Francisco** | Planning Commission | `bm-public-ssf.escribemeetings.com` (eScribe) · `ci-ssf-ca.legistar.com` | **eScribe** (minutes) + Legistar/Granicus | unclear | Med | **eScribe NOT supported by civic-scraper.** Minutes/packets in eScribe "Board Manager". `REVIEW:` earliest year. |
| 12 | **City of San Mateo** | Planning Commission | `cityofsanmateo.org/3971` "Public Meeting Portal" | **CivicPlus site; portal vendor ambiguous** (PrimeGov signatures present) | unclear | **Low** | `REVIEW:` meeting-portal vendor not confidently identified. Distinct from **San Mateo County** PC (archive to 2013). |
| 13 | **San Bruno** | Planning Commission | `sanbruno.ca.gov/AgendaCenter/Planning-Commission-8` | **CivicPlus** (CivicEngage) | unclear | Med | `REVIEW:` earliest year. |
| 14 | **Richmond** | Planning Commission | `ci.richmond.ca.us/Archive.aspx?AMID=32` · `richmond.granicus.com` | **CivicPlus** (Archive Center) + Sire/Granicus | PC agendas to 2008 | Med | Mixed: CivicPlus Archive Center + a "Sire AgendaPLUS" reference. `REVIEW:` which system holds minutes. |

**Platform distribution (best estimate):** Legistar/Granicus ×6 (San Jose, Sunnyvale, Mtn View, Oakland, Burlingame, + SSF partial), CivicPlus ×4 (Fremont, San Bruno, Richmond, San Mateo-site), CivicClerk ×1 (Daly City), PrimeGov ×1 (Palo Alto), Hyland OnBase ×1 (Redwood City), eScribe ×1 (SSF), Custom/Laserfiche ×1 (Berkeley).

**Headline:** Granicus-family (Legistar/Granicus/Sire/eScribe — all now Granicus-owned) touches the
**majority** of the set, but **four distinct non-Granicus stacks** (CivicPlus, CivicClerk, PrimeGov,
Hyland OnBase) each appear, and **two cities (Berkeley, City of San Mateo) could not be cleanly
classified**. So a single-vendor scraper will **not** cover this set.

---

## §2 — Task 2: Scraper pilot (per city)

Tooling tested: the `civic-scraper` library (v1.1.0; supports civic_clerk, civic_plus, granicus,
legistar, primegov) plus, where it failed, the platforms' own public endpoints. **All retrieval was
verified, not assumed.** Reproducible code: `pilot_scrape.py` (Daly City, the one that worked).

### civic-scraper, as-shipped — limitations found (apply across cities)
1. **CLI only auto-routes CivicPlus/DigitalTowPath.** Legistar, CivicClerk, Granicus, PrimeGov must
   be driven via the Python API (`from civic_scraper.platforms import LegistarSite`, etc.). The CLI
   `--url` will `ScraperError` on a Legistar/CivicClerk URL.
2. **Legistar timezone bug.** `LegistarSite(url)` crashes in `pytz.timezone(None)` unless you pass
   `timezone="US/Pacific"` explicitly (the kwarg defaults to `None`).
3. **Legistar fetches current-year only.** `events(since=2024)` returned **180 events, all dated
   2026** — it does not backfill historical years despite the `since` argument. Historical backfill
   needs the **Legistar Web API** (`webapi.legistar.com/v1/<client>/events`) with `$filter` dates.
4. **Version drift.** civic-scraper's CivicClerk scraper targets the **legacy ASPX** portal; Daly
   City runs the **modern `*.api.civicclerk.com` React backend**, which civic-scraper does not read.
   Its CivicPlus scraper expects the classic `/AgendaCenter`; newer CivicEngage sites differ.

### Daly City — CivicClerk — **PARTIAL SUCCESS (only city with retrieved minutes)**
- **Retrieval:** civic-scraper's `CivicClerkSite` does **not** work (legacy portal). The **modern
  public OData API does**: `GET dalycityca.api.civicclerk.com/v1/Events?$filter=categoryName eq
  'Planning Commission' and eventDate ...`. Documents live in each event's `publishedFiles[]`
  (typed `Agenda`/`Minutes`/`Agenda Packet`), downloadable via
  `/v1/Meetings/GetMeetingFileStream(fileId=<id>,plainText=false)`. I downloaded a real 2-page
  minutes PDF (Dec 5 2023 PC) and extracted clean text (`sample_dalycity_pc_2023-12-05_minutes.txt`).
- **Completeness check (CRITICAL):** of **12** PC meetings in 2024, published-file types were
  **Agenda ×11, Agenda Packet ×5, Minutes ×1**. **Only one Minutes document is exposed via the API
  for the entire year.** Either Daly City rarely posts minutes to the portal, posts them elsewhere,
  or posts them with long lag. This is a **silent-gap** risk: an automated run would "succeed" and
  quietly return almost no minutes. `REVIEW:` (§4).
- **Subtlety:** minutes are attached to the **following** meeting's packet (the Feb 6 2024 packet
  carried the *Dec 5 2023* minutes), because minutes are approved one meeting later. Any backfill
  must key minutes to meeting N−1, not the meeting they appear under.
- **Field transfer (autoextract on the real doc):** ran the existing `autoextract.extract()` on the
  retrieved minutes. Results (this is the load-bearing finding):

  | Field | autoextract output | Truth | Verdict |
  |---|---|---|---|
  | `ceqa_determination` | `eir` | FEIR present | ✅ transfers |
  | `item` | `1` | item 1 | ✅ transfers |
  | `meeting_date` / `jurisdiction` | (passed in) | — | n/a |
  | `case_number` | `21-14998` | `GPA-04-21-14998` | ❌ **mangled** (SF regex grabbed a tail) |
  | `request_type` | `""` | GPA + Planned Dev + Subdivision + Design Review | ❌ **missed** (SF uses case-suffix codes; Daly City uses prefixes) |
  | `action` | `""` | "voted 4-0 … recommend approval" | ❌ **missed** (no SF-style `ACTION:` label) |
  | `project_address` | `333 90Th Street` | (City Hall boilerplate) | ❌ **false positive** (grabbed footer) |
  | `units_proposed` | `235` | `1,235` | ❌ **wrong** (thousands comma split the match) |
  | `parking_spaces` | `5` | `34.5` | ❌ **wrong** (decimal split the match) |
  | `assessor_block` | `""` | `APN 091-211-230` | ❌ **missed** (Daly City uses APN, not "Assessor's Block") |
  | vote / speakers / stance | empty / 0 | 4-0 vote; no +/−/= markers | ❌ missed (SF 2015+ stance markers absent) |

  **Conclusion:** of ~35 fields, **2 transfer** cleanly; the rest are empty **or confidently wrong**.
  The false positives (address, units, parking) are the dangerous part — they look populated. SF's
  `autoextract` heuristics are SF-specific and must **not** be run on other jurisdictions without a
  per-jurisdiction extractor. This is expected for a pilot and is the core finding.

### San Jose — Legistar — **FAILED to retrieve minutes (access-blocked)**
- **Event metadata: available & complete.** The Legistar Web API returned **14** PC meetings for
  H1-2024 (matches the 2nd/4th-Wednesday schedule), so completeness is *checkable* here.
- **But every PC meeting has `EventMinutesFile = False`, status "Draft"** — San Jose does **not**
  attach minutes files in Legistar.
- **Granicus route also dead-ends:** `sanjose.granicus.com` exposes a minutes RSS (PC items ~2008–2017),
  but each `MinutesViewer.php` **302-redirects to `www.sanjoseca.gov/DocumentCenter/View/…`, which
  returns HTTP 403 (Akamai bot-protection).** The city site itself 403s curl and WebFetch.
- **Net:** San Jose minutes are reachable by a human browser but **blocked from automated retrieval
  on every route** (Legistar=no file, Granicus→Akamai DocumentCenter, city site=403). `REVIEW:` (§4).
  No minutes obtained → no field-transfer run for San Jose.

### Fremont — CivicPlus — **FAILED to retrieve (access-blocked)**
- `civic-scraper` `CivicPlusSite("https://www.fremont.gov/AgendaCenter").scrape(...)` returned **0
  assets** (verified). The classic `/AgendaCenter`, the new `/government/agenda-center` path, and the
  CivicPlus RSS feed all return **HTTP 403 "Access Denied" (Akamai, `errors.edgesuite.net`)**.
- **Net:** Fremont minutes not retrievable via civic-scraper or direct fetch. No field-transfer run.

> **Akamai is a recurring wall.** San Jose and Fremont both sit behind Akamai bot-protection. The
> parallel **zoning-envelope pilot independently hit the same wall** on San Jose's *code* host and
> several others. This is a real, repeated cost driver for the whole project (see §4, §6).

---

## §3 — Task 3: Per-jurisdiction synonym / mapping layer

Implemented as a **separate, additive** module: `jurisdiction_mappings.py` (in this folder). It does
**not** modify `extraction_common.py`'s `SCHEMA`, enums, or `coerce_record()`. It maps a locality's
local hearing-type/disposition vocabulary onto the canonical `request_type` / `action` enums, and
tags every judgment call `REVIEW`. `python jurisdiction_mappings.py` validates that all targets are
real enum values (passes). If promoted, it would move to `code/commission_minutes_processing/`.

Mappings are drawn **only from the one real Daly City document** (San Jose/Fremont vocab is unmapped
because their minutes could not be retrieved). Human-readable view:

**Daly City → canonical `request_type`**

| Local term (case prefix) | Canonical | Clean? | Note |
|---|---|---|---|
| General Plan Amendment (GPA) | `general_plan_amendment` | ✅ | obvious synonym |
| rezoning | `rezoning_map_amendment` | ✅ | |
| Conditional Use | `conditional_use` | ✅ | |
| Variance | `variance` | ✅ | |
| **Design Review (DR)** | `other` | ⚠️ `REVIEW` | **TRAP:** Daly City "DR" = *Design Review*, **not** SF "Discretionary Review". Mapping by the letters would be wrong. |
| Planned Development (PD) | `other` | ⚠️ `REVIEW` | no canonical PD enum — `other` loses info |
| Major Subdivision (SUB) | `other` | ⚠️ `REVIEW` | schema has no subdivision type |
| Precise Plan | `other` | ⚠️ `REVIEW` | no canonical enum |
| Development Agreement | `other` | ⚠️ `REVIEW` | no canonical enum |

**Daly City → canonical `action`**

| Local phrasing | Canonical | Clean? | Note |
|---|---|---|---|
| "approved" / "approved with conditions" | `approved` / `approved_with_conditions` | ✅ | |
| "denied" | `disapproved` | ✅ | |
| "continued" / "withdrawn" | `continued` / `withdrawn` | ✅ | |
| **"recommend approval"** | `approved` | ⚠️ `REVIEW` | PC only **recommends** to Council on legislative items; not a final SF-style action |
| "Motion carried 4-0" | `approved` | ⚠️ `REVIEW` | generic; real disposition must be read from the motion text |

**Discretionary-review absence (substantive, not my call):** Daly City's one document shows *Design
Review*, not SF-style discretionary review of by-right projects. Per the brief I do **not** record
"no DR mechanism" on my own authority — it's a `REVIEW` item (§4): one document can't distinguish
"absent" from "unsampled".

---

## §4 — Human review needed (consolidated)

Each item: what's uncertain · what I found · the question for Daniel.

**Platform classification**
1. `REVIEW:` **Berkeley** PC-minutes platform — *Found:* city-hosted PDFs + a Laserfiche "Records
   Online"; Granicus is council-video only. *Q:* where do Berkeley PC **minutes** actually live, and
   how far back?
2. `REVIEW:` **City of San Mateo** meeting-portal vendor — *Found:* CivicPlus site, but the "Public
   Meeting Portal" showed mixed PrimeGov/Granicus signatures. *Q:* which vendor hosts the minutes?
3. `REVIEW:` **Earliest-online year** for Oakland, Burlingame, SSF, San Bruno, Fremont, City of San
   Mateo, Richmond — I report approximate/unknown rather than guess. *Q:* do you need verified
   archive-start years now, or only for cities that survive the access check?
4. `REVIEW:` **Richmond** — agendas in CivicPlus "Archive Center" but a "Sire AgendaPLUS" reference
   too. *Q:* which system holds the *minutes*?

**Scraper pilot / access**
5. `REVIEW:` **San Jose minutes are Akamai-blocked on every automated route** (Legistar = no file;
   Granicus → Akamai DocumentCenter 403; city site 403). *Q:* is an Akamai-bypass budget (browser
   automation / official data request / API key) acceptable, or should San Jose use a different
   source? This affects every Akamai city.
6. `REVIEW:` **Fremont (CivicPlus) Akamai-blocked**; civic-scraper returns 0. Same question as #5.
7. `REVIEW:` **Daly City minutes coverage** — only **1 Minutes doc across 12 PC meetings (2024)** in
   the API. *Q:* does Daly City post minutes elsewhere/with lag, or genuinely seldom? A backfill
   would otherwise silently return almost nothing.
8. `REVIEW:` **eScribe (South SF) and Hyland OnBase (Redwood City) are unsupported by civic-scraper.**
   *Q:* worth a custom integration, or drop these cities from the near-term set?

**Mapping (judgment calls — every ⚠️ in §3)**
9. `REVIEW:` **Design Review vs Discretionary Review** name-collision (Daly City "DR"). Must not be
   auto-mapped to `discretionary_review`. *Q:* confirm `other`, or add a canonical enum?
10. `REVIEW:` **No canonical enum** for Planned Development, Major Subdivision, Precise Plan,
    Development Agreement (all Daly City). *Q:* these recur across CA suburbs — add enum value(s)
    (a **proposal**, not a unilateral edit), or keep collapsing to `other`?
11. `REVIEW:` **"Recommend approval" ≠ final approval.** Daly City PC is advisory on legislative
    items; SF `action` enums assume a final commission disposition. *Q:* add a "recommended_*"
    distinction, or accept the conflation?
12. `REVIEW:` **Discretionary-review absence (substantive).** *Q:* does Daly City lack an SF-style
    discretionary-review path (an informative by-right datum), or is it just unsampled? Needs your
    domain judgment — I will not assert it.

**Cross-pilot coordination**
13. `REVIEW:` **Pilot-city mismatch with the zoning-envelope pilot.** This minutes pilot uses **Daly
    City + San Jose + Fremont**; the zoning pilot (run in parallel, before this choice was final)
    used **Fremont + San Mateo + San Jose**. They overlap on San Jose + Fremont but diverge on the
    third city. *Q:* to make the two data sides join cleanly, should both standardize on the same
    trio (e.g. add Daly City to the zoning pilot, or San Mateo to this one)?

---

## §5 — Honest feasibility assessment

The schema's *identity* fields (date, jurisdiction, item, CEQA, case number) are conceptually portable,
but the pilot shows that **(a) retrieval, not parsing, is the binding constraint, and (b) SF's
`autoextract` does not transfer** — on the one real non-SF document it produced 2 correct fields and
several confidently-wrong ones, so every jurisdiction needs its own extractor (or an LLM extractor)
keyed through the additive mapping layer, never the SF heuristics. On retrieval, the 14 cities span
**six** platform stacks; civic-scraper as-shipped is partial at best (CLI mis-routing, a Legistar
timezone crash, current-year-only Legistar, and legacy-vs-modern version drift that broke it on Daly
City's CivicClerk and Fremont's CivicPlus), and **two of the three pilot cities were blocked outright
by Akamai bot-protection** — a wall the parallel zoning pilot hit independently. The one success (Daly
City) came from the platform's *own* modern API, not civic-scraper, and even there minutes were almost
absent from the feed. **Net:** the multi-jurisdiction build is *feasible but materially more expensive
than "point civic-scraper at a list"* — realistically a **per-platform adapter set (≈4–6 distinct
integrations), a per-jurisdiction extractor+mapping layer with a mandatory human-in-the-loop on
request_type/action, and an explicit Akamai-access budget.** Confidence: **high** on the platform
taxonomy and the civic-scraper limitations (directly verified); **medium** on field-level transfer
(one document, one city); **low/unmeasured** on San Jose and Fremont extraction (never retrieved).
My recommendation: before scaling, resolve §4 #5–#8 (access) and #9–#11 (enum policy), and pick the
**Granicus-family-first** cities with clean `MinutesViewer` (e.g. Burlingame, Sunnyvale) as the next,
more tractable pilot wave — they are likelier to yield minutes without an Akamai fight.

---

### Artifacts in this folder
- `minutes_platform_pilot_report.md` — this report.
- `jurisdiction_mappings.py` — additive mapping layer (Task 3 code); `python jurisdiction_mappings.py` validates targets.
- `pilot_scrape.py` — reproducible Daly City CivicClerk retrieval (the method that worked).
- `sample_dalycity_pc_2023-12-05_minutes.txt` — the real minutes text used for the §2 transfer table.
