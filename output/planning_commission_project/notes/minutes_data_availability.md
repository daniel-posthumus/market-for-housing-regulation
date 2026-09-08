# SF Planning Commission Minutes — Raw Data Availability

*An exhaustive inventory of what the raw minutes files actually contain, by document
era. Read-only; produced 2026-06-05 from `data/meeting_minutes/raw/` (1,099 source
files, 1998–2025) via `/tmp/era_scan.py` plus manual reading. Supersedes the earlier
`minutes_data_sources.docx`.*

The SF Planning Commission has published a summary "Minutes and Calendar" for every
hearing (≈weekly, Thursdays) since 1998. Each document records, for every agenda item,
the project's identity, the request, staff's recommendation, who spoke, what the
Commission did, and how each commissioner voted. This is an unusually rich, ~27-year,
item-level record of discretionary land-use decisions for a single major city.

---

## 1. Eras, formats, and naming conventions

| Era | Years | Format | Filename pattern | Count |
|---|---|---|---|---|
| I | 1998–2000 | HTML | `min0198-documentid=4743.html` (one file ≈ one *month*, multiple meetings inside) | 35 |
| II | 2001–2014 | HTML | `index.aspx-page=1189.html` (one file ≈ one *meeting*) | ~640 |
| III | 2015–2016 | TXT | `april-16-2015.txt` (scraped page text) | 57 |
| IV | 2017–2025 | PDF (+ some TXT in 2017–18) | `YYYYMMDD_cal_min.pdf` (2017–23), `YYYYMMDD_cpc_min.pdf` (2024–25), `YYYYMMDD_closed_min.pdf` (closed sessions) | ~290 |

Notes:
- **Era I files are month-bundled**: a single HTML holds an index of that month's meeting
  dates followed by each meeting's full minutes — the parser must split on the in-page
  date anchors. Eras II–IV are one file per meeting.
- **Closed-session minutes** (`*_closed_min.pdf`, ~8 files) contain only the meeting
  header and a litigation/personnel agenda — **no project items**. Exclude them.
- Modern PDFs are **born-digital (text) PDFs, not scans** — `pdftotext`/`pdfplumber`
  extract cleanly; no OCR needed. The only artifact is occasional intra-word spacing
  ("Pla nnin g Director", "Win slow") from the PDF's text layout.

---

## 2. Field × era availability matrix

Share of sampled meetings (n=24/era) in which each field is detectable. The modern-PDF
column tops out near ~79% because the even sample includes closed/special sessions,
2024–25 `_cpc_min` layout variants, and items where a field is *legitimately absent*
(e.g. `NOES` is printed only on non-unanimous votes); read it as "present whenever
applicable", not "missing 21% of the time".

```
                              1998-2000   2001-2014   2015-2016   2017-2025
                                  HTML        HTML        TXT         PDF
meeting date                      100%        100%         96%         79%
Regular/Special/Joint type        100%         96%         92%         79%
location / room                   100%         96%         96%         67%
remote / teleconference             8%         33%          8%         38%
commissioners present             100%        100%         96%         79%
commissioners absent              100%         83%         92%         71%
staff in attendance                96%         88%         96%         79%
SPEAKER KEY (+ / - / =)             0%          0%         79%         79%
case # 98.226D  (2-digit yr)      100%         71%         88%         12%
case # 2004.1106D (4-digit yr)     46%         88%         88%         67%
case # 2022-001764CUA (dash)        0%          0%         79%         79%
assessor block / lot              100%         88%         88%         79%
zoning district                   100%         88%         88%         79%
height & bulk district            100%         88%         88%         79%
supervisorial district (Dist N)     0%          0%         21%         79%
request type (CU / DR / Var)      100%         83%         88%         79%
Preliminary Recommendation          0%         92%         92%         79%
SPEAKERS field                    100%        100%         96%         79%
ACTION field                      100%        100%         96%         79%
AYES field                        100%         96%         92%         79%
NOES / NAYES field                100%         67%         58%          4%
ABSENT (in vote) field            100%         83%         92%         71%
RECUSED field                       0%         12%         21%         17%
EXCUSED field                      29%         21%          0%          0%
motion / resolution number        100%         25%         38%         58%
verbatim speaker discussion         8%         88%         88%         62%
Board of Supervisors review       100%         83%         88%         79%
```

---

## 3. Document structure (consistent across all eras)

Each meeting's minutes are organized into lettered sections; the exact set varies but
typically:

- **Header** — date, day, time, location (War Memorial Bldg → City Hall Room 400 →
  Remote Hearing), meeting type, **COMMISSIONERS PRESENT/ABSENT**, **STAFF IN ATTENDANCE**.
- **SPEAKER KEY** (Era III+ only): `+` support, `-` opposition, `=` neutral.
- **A. Items proposed for continuance** — items pushed to a later date (usually
  `SPEAKERS: None`, `ACTION: Continued to <date>`).
- **B. Commission Matters / C. Department Matters** — minutes adoption, Director's
  announcements, **review of past Board of Supervisors / Board of Appeals / HPC actions**.
- **Consent / Regular Calendar** — the substantive project items (the modeling payload).
- **Public Comment**.

Each **project item** is the unit of analysis and contains the fields below.

---

## 4. Item-level field inventory (with examples)

### 4.1 Identity
- **Item number** — agenda position (`1.`, `12a.`).
- **Case number** — the stable unique key. Format evolved:
  - Era I: `97.669C`, `98.226D` (2-digit year + `.` + serial + suffix letter)
  - Era II–III: `2004.1106D`, `2014.0956E` (4-digit year)
  - Era IV: `2022-001764CUA` (4-digit year + `-` + serial + multi-letter suffix)
  - **Suffix encodes the request type**: `C/CUA` conditional use, `D/DRP` discretionary
    review, `Z` zoning/map amendment, `E` environmental/CEQA, `V` variance,
    `PCA` planning-code amendment, `T` text amendment, `X` large-project authorization.
- **Staff planner** — name (+ phone in Era II+): `(C. FEENEY: (628) 652-7313)`.

### 4.2 Location / parcel / zoning
- **Project address**: `434 CORTLAND STREET – south side between Andover and Wool Streets`.
- **Assessor's block & lot**: `Lot 031 in Assessor's Block 5678`.
- **Supervisorial district** (Era IV): `(District 11)` — *new in the PDF era*, links each
  project to an elected supervisor.
- **Zoning use district**: `RH-2 (House, Two-Family)`, `NC-3`, `Cortland Avenue NCD`, `UMU`.
- **Height & bulk district**: `40-X`, `50-N`.

### 4.3 Request & recommendation
- **Project description / request** — free text, often citing Planning Code sections:
  *"Request for Conditional Use Authorization pursuant to Planning Code Sections 303, 317,
  and 738 to demolish a one-story, mixed-use building … and construct a three-story,
  6,245 square foot, four-unit residential building …"*
- **Continuance history**: `(Continued from Regular Meeting of December 2, 2004)`,
  `(Proposed for Continuance to January 12, 2023)`, `(Proposed for Indefinite Continuance)`.
- **Preliminary Recommendation** (Era II+): `Approve with Conditions`, `Do not take DR and
  approve as proposed`, `Pending`, `Disapprove`. This is *staff's* position — comparing it
  to the Commission's `ACTION` measures how often the Commission overrides staff.

### 4.4 Politics & disposition (the core modeling fields)
- **SPEAKERS** — list of public/representative speakers. **Era III+ tags each with a
  stance marker and a short topic**:
  `+ Andrea Bruss, Mayor Breed's office – Introduction of the ordinance`;
  `= Lisa Chen – Staff presentation`; `Georgia Schuttish – Illegal demolition on 21st St`.
  In Era I–II usually just names or `None`. → directly operationalizes **mobilization**
  (count of `+`/`-` speakers per item).
- **ACTION** — the disposition: `Approved`, `Approved with Conditions [as modified]`,
  `Disapproved`, `Continued to <date>`, `Continued indefinitely`, `Withdrawn`,
  `Did not take DR`, `Took DR and approved`.
- **Vote roll-call**: `AYES:`, `NOES:`/`NAYES:`, `ABSENT:`, `RECUSED:`, `EXCUSED:` —
  each followed by commissioner surnames. Example:
  `AYES: Braun, Ruiz, Koppel, Moore, Tanner / ABSENT: Diamond, Imperial`.
  **`NOES` is printed only when the vote is non-unanimous** — so absence of a NOES line
  ⇒ unanimous, not missing data.
- **Numeric vote tally** (`7-0`, `5-2`) — printed in Era I; later eras require *deriving*
  it from the roll-call (count of AYES vs NOES).
- **Resolution / Motion number**: `Motion No. 14638`, `Resolution No. 14633` — the
  permanent identifier of the Commission's formal action.

### 4.5 Verbatim discussion (Era II–IV)
From ~2001 on, the minutes include **substantial verbatim transcription** of commissioner
and director comments — not just dispositions. E.g. the 2015-04-16 minutes record
Commissioner Antonini reading from Moretti's *New Geography of Jobs* and *The Economist*
on the productivity cost of low density, and the Director outlining five process changes.
This is a **rich, under-exploited text source** for measuring stated preferences,
ideology, and the *rhetoric* of regulation — directly relevant to the political-economy
model (it literally contains commissioners theorizing about density and rents).

### 4.6 Cross-body context
Every meeting's "Review of Past Events" recaps recent **Board of Supervisors / Land Use
Committee / Board of Appeals / Historic Preservation Commission** actions on planning
matters — letting you link Commission decisions to the broader legislative pipeline.

---

## 5. Coverage, gaps, and data-quality notes

- **Temporal coverage is near-complete** 1998–2025 at roughly weekly cadence (~40–50
  meetings/yr; Era I months bundle ~3 meetings each).
- **Vote rolls are present in every era** — even where the *hand-labels* dropped them
  (see `processing_review.md`); back-filling `vote`/`noes` is a parse, not a re-read.
- **`NOES`/`RECUSED`/`EXCUSED` are conditional** (printed only when they occur). Treat a
  missing line as a structural zero, not missingness.
- **Stance markers (`+/-/=`) exist only from 2015** — pre-2015 mobilization must be
  inferred from speaker names/affiliations or left coarser.
- **Supervisorial district appears only from ~2015** — for earlier years, geocode the
  address to a district.
- **Closed-session PDFs** carry no items; **2017–2018 mix** TXT and PDF for the same
  period (de-duplicate by date).
- **Extraction**: HTML (Era I–II) needs tag-stripping + the `div#ctl00_content_Screen`
  container; born-digital PDFs (Era IV) extract cleanly with `pdftotext -layout` /
  `pdfplumber`, modulo intra-word spaces. No OCR required anywhere.

---

## 6. What is extractable for modeling (summary)

Per **project item** (the panel observation), every era yields at minimum:
`meeting_date, case_number, request_type, address, block/lot, zoning_district,
height_bulk, project_description, action/disposition, ayes[], noes[], absent[]`.
From 2001: `preliminary_recommendation` (→ staff-override measure) and verbatim
discussion. From 2015: `speaker_stances (+/-/=)` (→ mobilization/contestation) and
`supervisorial_district`. Resolution/motion numbers give a permanent action key
throughout.

Derived variables of direct interest to the political-economy model
(`toy_model.tex`): **approval probability**, **continuance count / delay** (a proxy for
discretionary friction), **staff-override rate**, **contestation** (count of `-` vs `+`
speakers), **commissioner-level voting records**, and (via address geocoding) tract- and
district-level regulatory intensity linkable to prices, demographics, and CRA eligibility.
