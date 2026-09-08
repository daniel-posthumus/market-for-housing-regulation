# Minutes Data Infrastructure & Canonical Schema

*How the SF Planning Commission minutes become a structured, item-level dataset, and
the schema every record conforms to. Companion to `minutes_data_availability.md` (what
the raw files contain) and `processing_review.md` (the audit that motivated this build).*

---

## 1. The pipeline

The infrastructure turns a hearing PDF/HTML into one structured record per agenda item.
Every stage reads the **same schema** from `extraction_common.py`, so the labeling form,
the model prompt, the builder, and the eval metric can never drift apart.

```
                         data/meeting_minutes/
  scrape_minutes.py  ─►  raw/{year}/…               (HTML 1998–2014, PDF 2015–present)
  parse_sf_…py       ─►  tagged/{year}/*.txt         (project blocks: <<Project Start>>…<<End>>)
                              │
        ┌─────────────────── │ ───────────────────────────────────────────┐
        │   extraction_common.py  =  SCHEMA  (single source of truth)        │
        │   → FIELDS · PROMPT_INSTRUCTION · coerce_record() · score_examples │
        └─────────────────── │ ───────────────────────────────────────────┘
                              │
  autoextract.py  (regex best-guess, schema-complete)
                              │
  labeling_app/ ──  ingest.py ─► labels.db  (one row per block + machine pre-fill)
                    app.py    ─► two-pane web UI: raw text ↔ pre-filled form; human corrects
                    export    ─► tagged/training/{year}_labeled.json   (confirmed labels)
                              │
  migrate_labels.py  (one-time: old labels → schema, backed up)
                              │
  training_sample_create.py ─► tagged/training/training.txt   (prompt/completion JSONL)
                              │
  train.py (fine-tune)  /  llm_extract.py (few-shot)  ─► processed/structured_data.jsonl
                              │
  score_examples()  ─► field-level accuracy on a held-out split
```

**Key design choice.** The schema is data, not code scattered across files. `SCHEMA` is a
list of typed field specs; `FIELDS`, the prompt text, the form, the required-key list, and
the metric are all *derived* from it. To add a field (e.g. another jurisdiction's quirk),
you edit one list and re-run — the form, model, and metric update automatically.

**Type system & coercion.** Each field has a type — `scalar`, `text`, `list`, `int`,
`enum` — and `coerce_record()` guarantees every stored record is schema-complete and
correctly typed: list fields become real JSON arrays (split on commas), `int` fields parse
to integers, `enum` fields snap to an allowed value (or `other`/`""`), unknown keys are
dropped. Whatever the human types in the form or the model emits as JSON passes through the
same coercion.

---

## 2. The canonical schema (36 fields)

Grouped by the sections shown in the labeling form.

### Identity
| field | type | choices / notes |
|---|---|---|
| `meeting_date` | scalar | ISO date of the hearing, YYYY-MM-DD |
| `jurisdiction` | scalar | Regulating body's city/county (defaults to "San Francisco") |
| `supervisorial_district` | scalar | Supervisorial district number, e.g. '11' (modern minutes only) |
| `item` | scalar | Agenda item number, e.g. '1', '12a' |
| `case_number` | scalar | Planning case number, e.g. 98.226D, 2022-001764CUA |
| `request_type` | enum | `conditional_use`, `conditional_use_modification`, `discretionary_review`, `variance`, `rezoning_map_amendment`, `planning_code_amendment`, `general_plan_amendment`, `text_amendment`, `large_project_authorization`, `downtown_project`, `ceqa_environmental`, `historic`, `coastal`, `office_allocation`, `other` (often inferable from the case-number suffix) |
| `resolution_or_motion_no` | scalar | Permanent action id, e.g. 'Motion No. 14638' |

### Location
| field | type | choices / notes |
|---|---|---|
| `project_address` | scalar | Street address of the project |
| `assessor_block` | scalar | Assessor's block number |
| `lot_number` | scalar | Lot number(s) |

### Zoning & scale
| field | type | choices / notes |
|---|---|---|
| `type_district` | scalar | Use/zoning district code, e.g. RH-2, NC-3, UMU |
| `type_district_descr` | scalar | Plain-English district name |
| `height_and_bulk_district` | scalar | Height & bulk district, e.g. 40-X, 50-N |
| `special_use_district` | scalar | Special use district, if any |
| `units_proposed` | int | Net new dwelling units proposed |
| `units_demolished` | int | Dwelling units demolished/removed |
| `parking_spaces` | scalar | Parking spaces, e.g. '4' or 'from 6 to 4' |
| `demolition` | enum | `yes`, `no` |
| `project_descr` | text | Full request/description text |

### Process
| field | type | choices / notes |
|---|---|---|
| `staff_planner` | scalar | Assigned planner from the block header, e.g. 'D. Winslow' (initial + surname; drop phone) — machine pre-filled by `autoextract.py` |
| `preliminary_recommendation` | scalar | STAFF recommendation, e.g. 'Approve with Conditions', 'Disapprove' |
| `ceqa_determination` | enum | `exempt`, `categorical_exemption`, `negative_declaration`, `mitigated_negative_declaration`, `eir`, `addendum`, `none`, `other` |
| `continued_to` | scalar | If continued: target date (YYYY-MM-DD) or 'indefinite' |
| `action` | enum | `approved`, `approved_with_conditions`, `approved_as_modified`, `disapproved`, `continued`, `continued_indefinitely`, `withdrawn`, `did_not_take_dr`, `took_dr`, `took_dr_and_approved`, `filed`, `no_action`, `other` |

### Politics
| field | type | choices / notes |
|---|---|---|
| `speakers` | list | Public speaker names |
| `support_count` | int | # speakers in SUPPORT (the `+` marker, modern minutes) |
| `oppose_count` | int | # speakers in OPPOSITION (the `-` marker) |
| `neutral_count` | int | # neutral speakers (the `=` marker) |
| `speaker_statements` | text | Notes / verbatim of what speakers or commissioners argued (optional) |
| `ayes` | list | Commissioners voting AYE |
| `noes` | list | Commissioners voting NO |
| `absent` | list | Commissioners absent for the vote |
| `recused` | list | Commissioners recused (conflict of interest) |
| `excused` | list | Commissioners excused |
| `vote` | scalar | Vote tally, e.g. '7-0', '5-2'. Derived in `coerce_record()` from `len(ayes)-len(noes)` when left empty; a stated tally is preserved |

### Conditions
| field | type | choices / notes |
|---|---|---|
| `modifications` | text | Conditions / modifications imposed by the Commission |

**Why these fields** (mapping to the toy model, see `toy_model.tex` §"Mapping"):
`action`→approval rate $a_j$; `continued_to`/continuance count→delay margin $\rho_j$;
`support/oppose_count`→homevoter mobilization; `ayes/noes/recused`→homevoter weight
$\phi_j\lambda_j$ and member-level voting; `preliminary_recommendation` vs `action`→the
staff-override wedge $a_j^{\text{pl}}-a_j^\ast$; `units_*`/`height_and_bulk_district`→
density/scale $g(D_j)$; `meeting_date`+`jurisdiction`+`supervisorial_district`+address→the
panel and spatial keys for the fragmentation/strategic-interaction tests; `staff_planner`→
planner fixed effects in the disposition analysis.

---

## 3. Worked example A — a 2006 conditional use (HTML era)

**Raw block** (`tagged/training/2006_sample.txt`, item 6):

```
6.  2006.0893C                               (J. Purvis: (415) 558-6354)
1099 Sunnydale Avenue - south side between Schwerin Street and Garrison Avenue;
Lot 001 in Assessor's Block 6363 (also known as 222 Schwerin Street and Heritage
Homes) - Request for Conditional Use Authorization under Planning Code Section
303(e) to modify Conditions of Approval under Motion No. 14737 … requiring 18
off-street parking spaces … The current proposal would reduce the garage capacity
to six spaces to accommodate an additional 3,600 sq. ft. of community space. …
within the RM-1 (Residential, Mixed-Use, Low-density) Use District and a 40-X
Height and Bulk District.
 Preliminary Recommendation: Approval with conditions
SPEAKERS:  None
ACTION:    Approved
AYES:      Alexander, Olague, Antonini, Lee, Moore, and Sugaya
MOTION:    17322
```

**Structured under the schema** (non-empty fields; ✎ = field the human corrected after
the machine pre-fill):

```json
{
  "meeting_date": "2006-…",                 ← from the meeting file
  "jurisdiction": "San Francisco",
  "item": "6",
  "case_number": "2006.0893C",
  "request_type": "conditional_use",        ← derived from the "C" suffix
  "resolution_or_motion_no": "Motion No. 17322",   ✎ heuristic grabbed the *cited prior*
                                                      Motion 14737; human picks the action's
                                                      motion (17322) from the MOTION: line
  "project_address": "1099 Sunnydale Avenue",
  "assessor_block": "6363",
  "lot_number": "001",
  "type_district": "RM-1",                  ✎ heuristic missed (non-standard "( … ) Use District")
  "type_district_descr": "residential, mixed-use, low-density",   ✎
  "height_and_bulk_district": "40-X",
  "units_proposed": 0,                      ✎ a parking/community-space modification — no new units
  "parking_spaces": "from 18 to 6",         ✎ heuristic grabbed "18" (the existing requirement)
  "staff_planner": "J. Purvis",             ← machine pre-filled from the block header
  "preliminary_recommendation": "Approval with conditions",
  "action": "approved",
  "speakers": [],
  "ayes": ["Alexander","Olague","Antonini","Lee","Moore","Sugaya"],
  "vote": "6-0"
}
```

This is the everyday case: the machine pre-fill nails identity, parcel, recommendation,
action, and the roll-call, and the human fixes three things the regex can't reliably get
from prose — the *action's* motion number vs. a cited one, the zoning district in a
non-standard parenthetical, and "18 → 6" parking (the heuristic sees the first number).
That is exactly the correction-not-typing workflow the app is built for.

---

## 4. Worked example B — a 2023 discretionary review (modern PDF era)

This one shows the fields that only exist in the modern era — **supervisorial district**,
**speaker stance markers**, **CEQA determination** — and a genuinely contested item.

**Raw block** (`raw/2023/20230105_cal_min.pdf`, item 13):

```
13.  2021-002057DRP                              (D. WINSLOW: (628) 652-7335)
2011 FILBERT STREET – south side between Buchanan and Webster Streets; Lot 003A in
Assessor's Block 0532 (District 2) – Request for Discretionary Review of Building
Permit No. 2021.0120.2957 for the demolition of a one-story garage and a one-story
shed and construction of a new four-story, two-unit residential building with a one
car parking garage within a RH-2 (Residential-House, Two Family-) Zoning District and
40-X Height and Bulk District. The Planning Department found that the project is exempt
from the California Environmental Quality Act (CEQA). …
Preliminary Recommendation: Do Not Take Discretionary Review and Approve
SPEAKERS:  = David Winslow – Staff report
           - Devon Johnson – DR presentation
           + Tara Sullivan – Project sponsor presentation
           - Ms. Albericci – Sunlight will be blocked in her yard
           - Rhonda Miller – Project will disrupt their backyard and living conditions
ACTION:    No DR
AYES:      Braun, Ruiz, Koppel, Moore, Tanner
```

**Structured under the schema**:

```json
{
  "meeting_date": "2023-01-05",
  "jurisdiction": "San Francisco",
  "supervisorial_district": "2",            ← from "(District 2)" (modern only)
  "item": "13",
  "case_number": "2021-002057DRP",
  "request_type": "discretionary_review",   ← derived from the "DRP" suffix
  "project_address": "2011 Filbert Street",
  "assessor_block": "0532",
  "lot_number": "003A",
  "type_district": "RH-2",                   ✎ (heuristic missed the modern "… Zoning District" form)
  "height_and_bulk_district": "40-X",
  "units_proposed": 2,                       ✎ "two-unit" is a word, not a digit — human enters 2
  "units_demolished": 0,                     ✎ garage + shed, not dwelling units
  "demolition": "yes",
  "ceqa_determination": "exempt",
  "staff_planner": "D. Winslow",             ← machine pre-filled from the block header
  "preliminary_recommendation": "Do Not Take Discretionary Review and Approve",
  "action": "did_not_take_dr",               ← "No DR" → enum (mapping added to autoextract)
  "speakers": ["David Winslow","Devon Johnson","Tara Sullivan","Ms. Albericci","Rhonda Miller"],
  "support_count": 1,                        ← the single "+" (project sponsor)
  "oppose_count": 3,                         ← three "-" (staff-noted DR requester + 2 neighbors)
  "neutral_count": 1,                        ← the "=" (staff)
  "ayes": ["Braun","Ruiz","Koppel","Moore","Tanner"],
  "absent": ["Diamond","Imperial"],
  "vote": "5-0"
}
```

Read the politics straight off the record: a neighbor-initiated discretionary review with
**3 opposed vs. 1 in support**, where the commission sided with the project sponsor (took
*no* DR, 5-0) and *against* the mobilized opposition — and staff had recommended exactly
that. `support_count`/`oppose_count` are the mobilization variable; `preliminary_
recommendation` = `action` here means **no staff override**. Multiply this across ~9,000
items and you have the panel the toy model's reaction-function and override tests need.

---

## 5. What the human still does

The pre-fill is correction-bait, not ground truth. Across both examples the recurring
human fixes are: word-number unit counts ("two-unit" → 2), zoning codes in non-standard
parentheticals, "from X to Y" parking, distinguishing the *action's* motion number from
cited prior ones, and `units_demolished` (non-dwelling demolition → 0). The app surfaces
each item's raw text beside the form precisely so these take seconds. Confirmed records
export to `{year}_labeled.json`, which `training_sample_create.py` consolidates into
`training.txt` for `train.py` / `llm_extract.py`, scored by `score_examples()` on the
held-out split.
