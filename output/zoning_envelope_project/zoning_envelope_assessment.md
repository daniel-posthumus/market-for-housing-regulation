# By-Right Envelope Data: NZLUD, Current Codes, and Preemption Timeline — Assessment

**Prepared for:** Daniel Posthumus / market-for-housing-regulation
**Date:** 2026-06-08
**Scope:** Assessment-and-assembly only. This is *not* a commitment to the full envelope build, and it does *not* touch the minutes pipeline or the 36-field schema. The historical envelope is explicitly deferred (leads only — see §5).

**Jurisdiction set (fixed "priority submarket set", 14 cities):**
- *Peninsula (San Mateo Co.):* Daly City, South San Francisco, San Mateo, Redwood City, San Bruno, Burlingame
- *South Bay (Santa Clara Co.):* San Jose, Palo Alto, Mountain View, Sunnyvale
- *East Bay (Alameda/Contra Costa Co.):* Oakland, Berkeley, Fremont, Richmond

**Headline numbers.** NZLUD covers **4 of 14** jurisdictions (29%). All 14 current municipal codes were located and their host platforms identified; **2 of the 3 pilot cities were scraped successfully**, the third (Municode) was not. A defensible **current** HCD preemption-exposure panel was built for all 14 from the authoritative state dataset. **15 distinct `REVIEW:` items** are collected in §4. The by-right/conditional reads in the pilot were extracted conservatively, with every interpretive step flagged.

**Reproducible artifacts in this directory:**
- `nzlud_14city_subset.csv` — NZLUD rows for the 4 covered cities (key envelope fields)
- `nzlud_muni.csv` — full NZLUD municipal file (source, 2,639 munis)
- `hcd_preemption_exposure_panel.csv` — the Task-3 panel
- `hcd.csv` — full authoritative HCD Housing Element Compliance Report (source)
- `pilot_extract.py` — runnable Task-2 pilot (Fremont, San Mateo, San Jose)

---

## 1. NZLUD coverage assessment (Task 1)

**Source & vintage.** NZLUD (National Zoning and Land Use Database; Mleczko & Desmond 2023), public release at `github.com/mtmleczko/nzlud`, file `nzlud_muni.csv`. Per the repo README, **data were collected over 2019–2022**, replicating the sample frame of the **2006 Wharton (WRLURI) survey** plus 210 supplemental municipalities (~2,640 rows). Critically, the file carries a **per-municipality `timestamp`**, so the snapshot date differs city-by-city.

> **Snapshot-year caveat (load-bearing).** For all four covered cities the NZLUD snapshot **predates** the 6th-cycle Housing Element updates (adopted/certified 2023–2026; see §3) and predates or coincides with the SB-9 (eff. 2022-01-01) / SB-35 / SB-423 (eff. 2024-01-01) rezonings. NZLUD therefore reflects roughly the **pre-preemption** by-right envelope. Covered-city timestamps: **Fremont 2019-10-15, Redwood City 2019-10-24, Burlingame 2021-02-19, San Jose 2021-12-19.**

**The key field.** `mf_per` = "Proportion of residential districts (including mixed use) that permit multi-family housing **by right**" — exactly the by-right margin the model turns on. Other envelope fields: `restrict_mf_permit`, `min_lot_size` (binary indicator), `adu` (binary, allowed anywhere), `height_ft_median`/`height_ft_mode`, `height_st_median`, `parking_median`/`parking_mode`, `inclusionary`, `open_space`, density categories `maxden1…maxden5`, and the composite `zri`/`zri_up` indices.

### Coverage table

| Jurisdiction | County | In NZLUD? | NZLUD `place` (GEOID) | Snapshot | `mf_per` | `height_ft_median` | `parking_median` | `adu` | Confidence |
|---|---|---|---|---|---|---|---|---|---|
| Burlingame | San Mateo | **Yes** | Burlingame (0609066) | 2021-02-19 | 0.667 | 50 | 2 | 1 | med |
| Redwood City | San Mateo | **Yes** | "Redwood" (0660102) | 2019-10-24 | 0.600 | 50 | 1 | 1 | med (name trap) |
| Fremont | Alameda | **Yes** | Fremont (0626000) | 2019-10-15 | 0.467 | 30 | 1 | 1 | med (spot-checked) |
| San Jose | Santa Clara | **Yes** | SanJose (0668000) | 2021-12-19 | 0.389 | 45 | 1 | 1 | low (unverified) |
| Daly City | San Mateo | **No** | — | — | — | — | — | — | — |
| South San Francisco | San Mateo | **No** | — | — | — | — | — | — | — |
| San Mateo (city) | San Mateo | **No** | — | — | — | — | — | — | — |
| San Bruno | San Mateo | **No** | — | — | — | — | — | — | — |
| Palo Alto | Santa Clara | **No** | — | — | — | — | — | — | — |
| Mountain View | Santa Clara | **No** | — | — | — | — | — | — | — |
| Sunnyvale | Santa Clara | **No** | — | — | — | — | — | — | — |
| Oakland | Alameda | **No** | — | — | — | — | — | — | — |
| Berkeley | Alameda | **No** | — | — | — | — | — | — | — |
| Richmond | Contra Costa | **No** | — | — | — | — | — | — | — |

**10 of 14 jurisdictions are absent from NZLUD** — including the three largest non-San-Jose cities in the set (Oakland, Berkeley, Sunnyvale, Palo Alto, Mountain View, plus most of the Peninsula). This is a direct consequence of the WRLURI-2006 sample frame: cities that didn't respond to the 2006 Wharton survey (and weren't among the 210 supplements) are simply not in NZLUD. Each missing city is a `REVIEW:` gap (§4).

### Spot-check against the live code (overlaps Task 2)

**Fremont (covered).** Fremont's current Title 18 (Chapter 18.90) has four base residential districts — R-1, R-2, R-3, R-G — with multifamily ("Multiple dwellings … apartment …") coded **`P` (permitted by right) in R-3 and R-G, `--` (prohibited) in R-1 and R-2**. A naive base-residential by-right share is **2/4 = 0.50**, vs **NZLUD `mf_per` = 0.467**. These are *close but not identical*, and the denominators differ: NZLUD's denominator is "residential districts **including mixed use**," which pulls in Fremont's commercial/mixed-use, City Center, Downtown, and Warm-Springs districts. I **cannot adjudicate** the exact figure without re-running NZLUD's district enumeration → `REVIEW:`. Height: NZLUD `height_ft_median = 30` matches the 30-ft base-district standard in Fremont's height table, but the R-3/R-G multifamily standards are higher; "median across residential districts" is plausible but not verified cell-by-cell.

**San Jose (covered).** Could **not** be verified against the code directly: San Jose's code is on Municode, which serves only a JavaScript shell to a plain fetch (see §2). San Jose Title 20 uses an **`S` = Special Use Permit** symbol in its use table — exactly the kind of symbol that is easy to conflate with by-right. NZLUD reports `mf_per = 0.389`, **unverified** → `REVIEW:`.

### WRLURI benchmark (REVIEW #11 — now resolved)

The Dropbox WRLURI Stata files were git-LFS pointer stubs when this report was first written; they
are now materialized, so the benchmark was run (`wrluri_crosscheck.py` → `wrluri_crosscheck.csv`).
WRLURI is a **survey-based** restrictiveness index (higher = more restrictive); NZLUD `zri` is a
**code-derived** restrictiveness index; NZLUD `mf_per` is multifamily-by-right share (inverse of
restrictiveness). Joined on Census place FIPS (WRLURI 2006 `ufips`, WRLURI 2018 `GEOID`; NZLUD
GEOID = 600000 + FIPS).

**Coverage of the 14-city set:** WRLURI 2006 = **4/14** (the *exact same four* as NZLUD — Burlingame,
Redwood City, San Jose, Fremont — confirming NZLUD inherited the WRLURI-2006 frame). WRLURI 2018 =
**1/14 usable** (Sunnyvale = 0.63; Palo Alto is in the 2018 frame but its index is missing/NaN; the
four NZLUD cities are absent from 2018). **All three sources overlap on 0/14.** So the only
benchmarkable overlap is NZLUD ∩ WRLURI-2006 = **4 cities**.

| City | WRLURI 2006 (survey) | NZLUD `zri` (code) | NZLUD `mf_per` |
|---|---|---|---|
| Redwood City | 0.552 | 2.533 | 0.600 |
| Fremont | 0.499 | 2.537 | 0.467 |
| Burlingame | 0.443 | 2.925 | 0.667 |
| San Jose | **0.162** (least restrictive) | **3.318** (most restrictive) | 0.389 |

**Result — the two indices DISAGREE on ordering.** Spearman rank correlation (n=4): WRLURI 2006 vs
NZLUD `zri` = **−1.00** (perfectly inverted; the expected sign is positive), and WRLURI 2006 vs
`mf_per` = **+0.40** (expected negative). San Jose is the clearest case: *least* restrictive of the
four by the 2006 survey, *most* restrictive by NZLUD's current code index.

**Honest reading.** This is **not a validation and not a refutation** — n=4 makes a ±1.00 corr
near-meaningless, the two snapshots are **13–15 years apart** (WRLURI 2006 vs NZLUD 2019–21, spanning
California's entire pro-housing preemption shift), and the constructs differ by design (WRLURI captures
*process/political* friction — delay, council pressure — while NZLUD reads the *code text*). The
takeaway is a **caution**: NZLUD `zri` and WRLURI are **not interchangeable** for this set and may be
inversely related here, so neither can stand in for the other without adjudication. This remains a
`REVIEW:` item (§4 #11). Per-cell envelope measures (`mf_per`, height) — not the composite index —
are the more defensible thing to carry forward, and even those need the code spot-check above.

---

## 2. Municipal-code location table + extraction pilot (Task 2)

### 2a. Code-location table (all 14)

| Jurisdiction | Host platform | Zoning code locus | URL | Scrapable by plain GET? |
|---|---|---|---|---|
| Daly City | Municode | Code of Ordinances (zoning title) | https://library.municode.com/ca/daly_city | No (JS shell) |
| South San Francisco | QCode / eCode360 | Title 20 Zoning | https://qcode.us/codes/southsanfrancisco/ | Redirects; not confirmed |
| San Mateo (city) | public.law law-library | **Title 27 Zoning** | https://law.cityofsanmateo.org/us/ca/cities/san-mateo/code/27 | **Yes (200)** |
| Redwood City | Municode / eLaws | Zoning Code (Articles) | https://library.municode.com/ca/redwood_city/codes/zoning_code | No (JS shell) |
| San Bruno | eCode360 | Title 12 Land Use, Art. III Zoning | https://ecode360.com/SA5001 | No (403) |
| Burlingame | QCode | Title 25 Zoning | https://qcode.us/codes/burlingame/ | Redirects; not confirmed |
| San Jose | Municode | **Title 20 Zoning** (ch. 20.30 residential) | https://library.municode.com/ca/san_jose/codes/code_of_ordinances?nodeId=TIT20ZO | No (JS shell); static PDF fallback ✔ |
| Palo Alto | American Legal | **Title 18 Zoning** | https://codelibrary.amlegal.com/codes/paloalto/latest/paloalto_ca/0-0-0-76269 | No (403) |
| Mountain View | Municode | **Chapter 36 Zoning** | https://library.municode.com/ca/mountain_view/codes/code_of_ordinances?nodeId=PTIITHCO_CH36ZO | No (JS shell) |
| Sunnyvale | eCode360 | **Title 19**, Table 19.18.030 (residential uses) | https://ecode360.com/42729899 | No (403) |
| Oakland | Municode | **Planning Code Title 17** | https://library.municode.com/ca/oakland/codes/planning_code | No (JS shell) |
| Berkeley | public.law (Open Law) | **Title 23 Zoning** | https://berkeley.municipal.codes/BMC/23 | No (403 to bot) |
| Fremont | CodePublishing | **Title 18 Planning & Zoning** (ch. 18.90 residential) | https://www.codepublishing.com/CA/Fremont/html/Fremont18/Fremont18.html | **Yes (200)** |
| Richmond | Municode | **Art. XV, ch. 15.04 Zoning** | https://library.municode.com/ca/richmond/codes/code_of_ordinances?nodeId=ARTXVZOSU | No (JS shell) |

Platform mix across the 14: **Municode ×6, eCode360 ×3, public.law ×2, QCode ×2, American Legal ×1, CodePublishing ×1.** Of these, only **CodePublishing and public.law** returned real code text to a plain authenticated GET; Municode returns a JS shell (content behind `api.municode.com`); American Legal, eCode360, and Berkeley's host returned **403** to a bot. This matches the NZLUD authors' own note that they "were unable to utilize web scraping" and downloaded codes manually. **`REVIEW:` (scaling).**

### 2b. Pilot extraction — 3 cities (`pilot_extract.py`)

> **`REVIEW:` (coordination).** The brief says to pilot the **same 2–3 cities as the minutes platform pilot**, but that pilot's city selection is **not yet finalized**. I piloted the three strong candidates below (chosen to span code-platform types, code structures, and NZLUD-covered vs not). **Final pilot-city selection must be reconciled with the minutes pilot before either side is treated as canonical.** All three are in the 14-city set.

| Pilot city | Why chosen | Result |
|---|---|---|
| **Fremont** | CodePublishing (scrapable), consolidated use-table structure, **in NZLUD** (enables spot-check) | Extracted cleanly |
| **San Mateo (city)** | public.law (scrapable), **per-district-chapter** structure (contrast), **not in NZLUD** (gap-fill demo), the §3 preemption standout | Extracted; one structural trap found |
| **San Jose** | Municode, **in NZLUD**, largest city | **Could not scrape** (JS shell) — documents the access barrier |

**Field-level honesty assessment.**

- **By-right/conditional use (the hinge).**
  - *Fremont R-3/R-G:* multifamily = **`P` (by right)**, clean read from Table 18.90.080 where the code itself defines `P`=permitted, `C`=conditional-use-permit, `Z`=zoning-administrator-permit, `--`=prohibited. **Parsed cleanly.**
  - *San Mateo R3:* §27.22.010 PERMITTED USES lists "Multiple family dwellings" as **by right**; §27.22.020 SPECIAL USES (special-use-permit) is the conditional bucket. **Parsed cleanly** — but the code splits permitted vs. special into *separate sections per district*, not a single matrix, so a generic table-parser would miss it.
  - *San Jose:* **unresolved** — host blocked; the `S`=Special-Use-Permit symbol must not be read as by-right. `REVIEW:`.
- **`REVIEW:` — "by right" ≠ "ministerial" in California.** Even where the *use* is `P`, Fremont (and most of these cities) still applies **design/site review**, which can be discretionary. The model's "ministerial, no hearing" envelope is **narrower** than the code's "permitted use" column. Deciding what counts as by-right for the model is a **policy call for Daniel**, not something to infer from the use table alone.
- **Height — `REVIEW:` (a real trap).** San Mateo §27.22.055: "Building height shall not exceed the standards set forth on the **Building Height Plan of the General Plan**." The height limit is **not in the code text at all** — it lives in a General-Plan map. Height for such cities **cannot be text-scraped** and NZLUD's height fields (where present) are correspondingly unreliable. Fremont, by contrast, tabulates height numerically in the code.
- **Parking / FAR / density:** numeric and tabulated in Fremont; in San Mateo these are per-district sections (27.22.030 parking, 27.22.050 FAR) and parsed in the pilot's section-by-section mode. Workable but bespoke per city.
- **Overlays:** both cities carry overlay districts (Fremont: TOD, Hillside, Housing-Element-Sites-Inventory overlays; San Mateo: Two-Unit, mixed-use residential overlays) that **modify base zoning** and were **not** resolved in this pilot. `REVIEW:` for any production build.

**What would need to scale:** (1) a Municode-API client + American-Legal/eCode360 access path (or licensed/bulk downloads) for ~9 of 14 cities; (2) per-city parsers for *both* the consolidated-matrix and per-district-section structures; (3) a separate ingestion of General-Plan height/density maps where the code defers to them; (4) overlay resolution; (5) a human-in-the-loop pass on every by-right/conditional and every "design-review-attaches" call.

---

## 3. HCD/RHNA preemption-exposure panel (Task 3)

**Authoritative source.** California Open Data **"Housing Element Compliance Report"** (HCD), `housing_element.csv`, last updated **2026-06-05** (`hcd.csv` here). Columns: Jurisdiction, County, Planning Period, Record Type, Date Received, Review Status, **Reviewed Date**, **Compliance Status**, COG, Cycle.

**What the dataset is — and isn't.** It is a **current snapshot**: for each jurisdiction it carries the **latest adopted** 6th-cycle (planning period `6S` = ABAG region) element and HCD's review of it. **All 14 are currently "In" compliance.** The `Reviewed Date` is the date HCD found the adopted element **in substantial compliance** — i.e., the date builder's-remedy exposure **ended**. It does **not** record the intervening draft-review rounds, conditional certifications, or any decertify/re-certify history. **`REVIEW:` — this is a snapshot, not a full longitudinal review log.**

**Dating exposure.** The ABAG 6th-cycle Housing Element deadline was **2023-01-31**, with **no grace period** during HCD review. Only 4 of 109 Bay Area jurisdictions met it (Alameda, Emeryville, San Francisco, San Leandro — **none of the 14**). So **all 14 cities were exposed to the builder's remedy beginning ~2023-02-01**, ending on each city's HCD-found-compliant date. Window below (`br_exposure_months ≈` months from 2023-02-01 to certification; full file `hcd_preemption_exposure_panel.csv`):

| Jurisdiction | County | Element received | **HCD found compliant** | ≈ Builder's-remedy exposure |
|---|---|---|---|---|
| Oakland | Alameda | 2023-02-13 | 2023-02-17 | ~0.5 mo |
| Berkeley | Alameda | 2023-01-24 | 2023-02-28 | ~0.9 mo |
| Fremont | Alameda | 2023-01-23 | 2023-03-22 | ~1.6 mo |
| Redwood City | San Mateo | 2023-02-22 | 2023-03-27 | ~1.8 mo |
| Mountain View | Santa Clara | 2023-04-26 | 2023-05-26 | ~3.8 mo |
| Richmond | Contra Costa | 2023-09-26 | 2023-10-02 | ~8.0 mo |
| South San Francisco | San Mateo | 2023-10-27 | 2023-11-20 | ~9.6 mo |
| San Jose | Santa Clara | 2023-11-30 | 2024-01-29 | ~11.9 mo |
| Sunnyvale | Santa Clara | 2024-02-29 | 2024-03-06 | ~13.1 mo |
| Burlingame | San Mateo | 2024-03-15 | 2024-03-20 | ~13.6 mo |
| Palo Alto | Santa Clara | 2024-07-26 | 2024-08-20 | ~18.6 mo |
| San Bruno | San Mateo | 2024-09-25 | 2024-10-21 | ~20.7 mo |
| Daly City | San Mateo | 2024-11-13 | 2024-12-03 | ~22.1 mo |
| **San Mateo (city)** | San Mateo | 2026-04-07 | **2026-06-01** | **~40 mo (standout)** |

**San Mateo** is the clear outlier — exposed to the builder's remedy for ~3.3 years. (HCD issued a "Failure to Adopt a Compliant 6th-Cycle Housing Element" Letter of Inquiry to *San Mateo County* in 2024; the *city* certified only in 2026.) This single-cell history makes San Mateo an attractive treatment case — but its `Reviewed Date` is the **end** of exposure; the back-and-forth in between is not in this dataset (`REVIEW:`).

**State-law dating (sourced, not from memory).**
- **SB-9** (lot splits / up-to-4-units ministerial): eff. **2022-01-01**.
- **SB-35** (streamlined ministerial approval for cities short of RHNA progress, ≥10% affordable): signed **2017**, in effect since 2018.
- **SB-423** (extends/expands SB-35 to 2036, adds coastal): approved **2023-10-11**, eff. **2024-01-01**.
- **Builder's remedy** (Housing Accountability Act): attaches whenever a jurisdiction lacks a substantially compliant Housing Element — here, from the 2023-01-31 deadline until each city's certification date above.

**`REVIEW:` — a second preemption variable not yet assembled.** SB-35/SB-423 streamlining eligibility is set by HCD's **Statutory Determinations** (RHNA-progress-based: "below-moderate" vs "above-moderate" vs "fully streamlined"), published as periodic HCD PDFs, **separate** from Housing-Element compliance. A jurisdiction-by-time SB-35/423 exposure panel would need those determination tables; it is **not** built here.

---

## 4. Human review needed (consolidated `REVIEW:` flags)

The single most important section. Each item: what's uncertain · what I found · the question for Daniel.

1. **NZLUD missing 10 of 14 jurisdictions.** *Found:* only Burlingame, Redwood City, Fremont, San Jose are in NZLUD; Daly City, South SF, San Mateo, San Bruno, Palo Alto, Mountain View, Sunnyvale, Oakland, Berkeley, Richmond are absent (WRLURI-2006 sample frame). *Question:* Accept that ~71% of the envelope must come from current-code reading, or re-run NZLUD's `parse_zoning_txt.py` on the 10 missing cities to extend it consistently?

2. **NZLUD snapshot is pre-preemption.** *Found:* covered-city snapshots are 2019–2021, before the 2023–2026 Housing-Element updates and SB-9/35/423 rezonings. *Question:* Treat NZLUD as the *pre-period* envelope and re-scrape current codes for the *post-period*? (This is the natural two-period design — confirm.)

3. **Fremont `mf_per` discrepancy (NZLUD vs code).** *Found:* NZLUD 0.467 vs hand-count 0.50 (base-residential); denominators differ (NZLUD includes mixed-use). *Question:* Whose definition of the by-right MF share does the model want — base-residential districts only, or "residential incl. mixed-use" (NZLUD)?

4. **"By right" (use) ≠ "ministerial" (no hearing).** *Found:* even `P` uses in Fremont/San Mateo can trigger discretionary **design/site review**. *Question:* For the model's envelope, does "by right" mean "principally permitted use" or "no discretionary review of any kind"? This decision changes the values for most cities.

5. **Height not in code text (San Mateo).** *Found:* §27.22.055 defers height to the **General Plan Building Height Plan map**. *Question:* Accept that height for such cities needs a separate GP-map ingestion, and that NZLUD height fields are unreliable where the code defers to maps?

6. **Anti-bot blocking on 5–6 of 14 hosts.** *Found:* Municode = JS shell (API only); American Legal, eCode360, Berkeley host = 403. Only CodePublishing + public.law scrape cleanly. *Question:* Budget for a Municode-API client + licensed/bulk downloads, or do manual download for the blocked cities (as NZLUD's authors did)?

7. **San Jose by-right read unverified.** *Found:* Municode shell blocked direct read; Title 20 uses `S`=Special-Use-Permit; NZLUD `mf_per=0.389` not checked against code. *Question:* OK to verify San Jose from its static Title-20 PDF before trusting NZLUD's value?

8. **HCD panel is a snapshot, not a full history.** *Found:* the dataset carries only the latest adopted element + its certification date; no decertify/re-certify/conditional-cert history. *Question:* Is "exposure start = 2023-01-31 deadline, exposure end = HCD `Reviewed Date`" an acceptable approximation, or do you need the per-city review-letter timeline (HCD APR / letters)?

9. **"Substantial compliance" date is legally contestable.** *Found:* builder's-remedy applicability turns on *substantial compliance*, which courts have treated as possibly the **adoption** date (sometimes earlier than HCD's formal finding). *Question:* Use HCD's `Reviewed Date`, the city's `Date Received`/adoption date, or flag both?

10. **SB-35/SB-423 streamlining not assembled.** *Found:* a separate RHNA-progress preemption variable lives in HCD Statutory Determination PDFs. *Question:* Do you want this as a second exposure series, and over which years?

11. **WRLURI cross-check — RUN (2026-06-08); discordance flagged.** The LFS stubs were materialized and the benchmark was run (see §1 "WRLURI benchmark"; code `wrluri_crosscheck.py`). *Found:* NZLUD and WRLURI-2006 cover the same 4/14 cities; WRLURI-2018 covers only Sunnyvale; over the 4-city overlap NZLUD `zri` is **rank-inverted** vs WRLURI 2006 (Spearman −1.00), driven by San Jose (least restrictive by 2006 survey, most by NZLUD code index). *Caveats:* n=4, 13–15-yr vintage gap, different constructs — not a validation. *Question for Daniel:* given the disagreement, do we (a) treat WRLURI as a non-comparable legacy benchmark and rely on per-cell NZLUD/code measures, or (b) want the discordance investigated city-by-city before using either index?

12. **Name-join trap (Redwood City).** *Found:* NZLUD stores Redwood City as `place = "Redwood"` (GEOID 0660102) — matched by FIPS, not name. *Question:* Confirm we join NZLUD↔minutes on **FIPS GEOID**, not place-name strings (several cities will mis-join on name).

13. **Pilot-city coordination.** *Found:* I piloted Fremont, San Mateo, San Jose; the minutes platform pilot's 2–3 cities are not finalized. *Question:* Which 2–3 cities are the minutes pilot's, so the envelope pilot can be re-pointed to match?

14. **Overlay districts unresolved.** *Found:* both pilot cities carry overlays (TOD, Hillside, Housing-Element-Sites, two-unit, mixed-use) that modify base zoning; not handled. *Question:* Are overlays in-scope for the envelope, or is base zoning sufficient for the first pass?

15. **South SF / Burlingame (QCode) host not confirmed.** *Found:* `qcode.us` URLs redirect (301); I did not verify the live zoning-title path or its scrapability. *Question:* Lower priority — confirm acceptable to resolve these two during the build rather than now.

---

## 5. Leads for historical reconstruction (post-decision) — noticed, NOT acted on

- **NZLUD pipeline is re-runnable.** `parse_zoning_txt.py` + the repo's `municipal_codes/` input corpus is an open, re-runnable NLP pipeline. Re-running it on **archived** code snapshots (e.g., Wayback Machine captures of Municode/CodePublishing) could yield panel zoning data — this is the cheapest path to a historical envelope and is exactly what the authors designed for.
- **public.law / OpenLaw full-text, bulk-downloadable codes** (San Mateo, Berkeley) are public-domain and offer HTML/XML bulk download — but **current only**. Their inline ordinance citations (e.g., "Ord. No. 1991-18") are threads to pull the underlying dated ordinances.
- **Municode / American Legal "Ordinance List and Disposition Tables"** give chronological ordinance histories per city — a map of *when* each zoning change happened, to date envelope changes.
- **CodePublishing** retains prior codified versions for some cities (archive links).
- **City legislative archives** (Granicus / Legistar) hold adopted-ordinance PDFs with dates — e.g., `sanjose.granicus.com`, `mountainview.legistar.com`, `sunnyvaleca.legistar.com` — useful to date specific rezonings.
- **WRLURI 2006 + 2018 waves** (now materialized in Dropbox; benchmark run — see §1 and §4 #11). Use as a coarse legacy benchmark only: it overlaps NZLUD on just 4 cities and disagrees on ordering.
- **Wayback Machine** snapshots of each city's zoning code page — the single most general source for "what did the code say in year X."
- **HCD historical compliance data** — older versions of the California Open Data Housing-Element dataset (and APR data) for prior cycles.

---

## 6. Feasibility assessment (one paragraph)

**The *current* by-right envelope is feasible but not free, and the *historical* envelope remains the expensive, deferred piece.** On the current side, NZLUD gives a clean, machine-readable starting point for only **4 of the 14** cities and is a **pre-preemption (2019–2021) snapshot**, so even those four must be re-read against today's code; the other 10 must be built from current municipal codes. Current-code reading is tractable — all 14 codes were located and two of three pilot cities scraped cleanly in minutes — but it is **not** a one-pass scrape: roughly half the hosts (Municode, American Legal, eCode360) block bots and need an API client or licensed/manual download, the codes come in **at least two structural shapes** (consolidated use-matrix vs. per-district sections), and two genuine traps recur — **height that lives in a General-Plan map rather than the code text**, and a **"permitted use" that still triggers discretionary design review**, which means the model's true ministerial envelope is narrower than the code's permitted-use column and *cannot be read off the table mechanically*. The realistic cost of the current envelope is therefore **a few engineer-weeks plus a mandatory human-in-the-loop pass on every by-right/conditional and design-review call** — cheap relative to the minutes, and well within reach for 14 cities. The **preemption-exposure variable is the cheapest and most solid deliverable**: the authoritative HCD dataset already pins a defensible builder's-remedy exposure window for all 14 (with San Mateo a ~3-year standout), needing only a human decision on the "substantial compliance date" convention and an optional second SB-35/423 series. The **historical** envelope — reconstructing the *past* by-right rules year by year — is where cost explodes (archived codes, ordinance-disposition tables, GP-map history) and should stay deferred; the good news is that NZLUD's open, re-runnable pipeline plus Wayback/ordinance-history sources (§5) make it *possible* later without bespoke per-city archival research from scratch. **Net: the current-envelope side is feasible at the scale the model needs; confidence is high for the HCD panel, medium for current-code extraction (contingent on the by-right/ministerial definition and host-access budget), and the historical side should remain a separate, explicitly-budgeted decision.**

---

*Sources: NZLUD — github.com/mtmleczko/nzlud (README, `nzlud_muni.csv`, data dictionary) and Mleczko & Desmond (2023), Urban Studies, doi 10.1177/00420980231156352. HCD — California Open Data "Housing Element Compliance Report" (`housing_element.csv`, updated 2026-06-05) and hcd.ca.gov housing-element / statutory-determinations pages. ABAG 6th-cycle deadline & builder's remedy — abag.ca.gov "The Builder's Remedy and Housing Elements"; SV@Home; Holland & Knight (2022). State-law dates — leginfo (SB-423), CA YIMBY, Holland & Knight, Buchalter legislative updates. Municipal codes — the host URLs in §2a (CodePublishing/Fremont and law.cityofsanmateo.org fetched directly; others identified, not all scrapable).*
