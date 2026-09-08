# Hand-Label Review Guide

*How to review every hand-labeled item with your own eyes before the existing
labels are treated as gold-standard / ground truth. Companion to
`data_infrastructure.md` (the pipeline + canonical schema), **`labeling_rules.md`
(the SF-specific coding manual — the decision rules you review against)**, and the
labeling app's `README.md` (developer-facing).*

---

## 1. What you are reviewing

The hand-labeling done so far is **319 records spanning 1998–2014**, stored as
`data/meeting_minutes/tagged/training/{year}_labeled.json` (one file per year). These
files — consolidated into `training.txt` by `training_sample_create.py` — are what
`train.py` / `llm_extract.py` treat as ground truth, so this review is the gate before
that happens.

| Year | Records | | Year | Records | | Year | Records |
|---|---|---|---|---|---|---|---|
| 1998 | 69 | | 2004 | 25 | | 2010 | 14 |
| 1999 | 11 | | 2005 | 13 | | 2011 | 12 |
| 2000 | 11 | | 2006 | 15 | | 2012 | 7 |
| 2001 | 13 | | 2007 | 19 | | 2013 | 16 |
| 2002 | 15 | | 2008 | 20 | | 2014 | 14 |
| 2003 | 23 | | 2009 | 22 | | **Total** | **319** |

**How these show up in the app.** `ingest.py` matches each hand-labeled record to the
raw project block(s) in the corpus *by case number* and seeds them with status
**`prelabeled`** — your human label, pre-filled, beside the raw text. (There are ~414
`prelabeled` rows because a handful of cases were heard at more than one meeting, so the
same label attaches to each occurrence — a free consistency check.) Reviewing every
`prelabeled` item is therefore equivalent to reviewing all the hand-labeling.

---

## 2. Launch the app

The data path was just re-pointed to Dropbox (the corpus moved off Google Drive on
2026-06-08), so the app finds the corpus with no extra setup. If you ever relocate the
data, override with `export MFHR_DATA_ROOT=/path/to/data`.

```bash
cd /Users/danpost/market-for-housing-regulation
source .venv/bin/activate              # or wherever your venv lives

cd code/commission_minutes_processing/labeling_app
python app.py                          # serves http://127.0.0.1:5005
```

Open **http://127.0.0.1:5005**. Nothing leaves your machine (the optional Anthropic
re-prefill is the only exception, and you have to ask for it).

`labels.db` already exists and holds the prelabels — you do **not** need to re-run
`ingest.py` to start reviewing.

---

## 3. The review workflow

The screen is two panes: **left = raw item text** (the source of truth you're checking
against), **right = the schema form**, pre-filled with the hand label.

1. **Filter the sidebar to `prelabeled`** (status dropdown). Optionally also pick a
   **year** and work through 1998 → 2014 in order. The counts above tell you how many to
   expect per year.
2. For each item, **read the raw block on the left and confirm every filled field on the
   right matches it.** Use the field dictionary in §4 to settle "what should go here."
3. Then one of:
   - **Correct → Save & next** (button or **⌘/Ctrl+Enter**) if a value is wrong. Status
     becomes `done`.
   - **Save & next** with no change if it's already right — this promotes it from
     `prelabeled` to `done`, which is your "I've eyeballed this" marker.
   - **Flag uncertain** (sets status `flagged`) + type a **note** if you want to revisit
     or flag an ambiguous source. Come back via the `flagged` filter.
4. Track progress with the **stats** view (or the status filter counts): you're done when
   no `prelabeled` items remain.

**Handy controls**
- **⌘/Ctrl+Enter** — Save & next
- **Alt+↑ / Alt+↓** — move between items
- **`p`** — Re-prefill the form from the raw text. Choose **`heuristic`** (offline regex;
  good for fixing migrated `action` values that show `other`) or **`anthropic`** (needs
  `pip install anthropic` + `ANTHROPIC_API_KEY`). ⚠️ Re-prefill **overwrites** the form,
  so only use it when you'd rather start from a fresh machine guess than the human label.
- Sidebar **search** filters by case number or text.

> The labels you're reviewing are the source of truth that fixes drift — when the raw
> block and the label disagree, the **raw block wins** (fix the label). The one exception:
> a recurring case prelabeled from a *different* meeting may carry that other meeting's
> disposition; judge each block on its own text.

---

## 4. Field dictionary — what goes in each field (36 fields)

Types: `scalar` (one short value) · `text` (free text) · `list` (comma-separated, stored
as a JSON array) · `int` (integer) · `enum` (must be one of the listed choices; anything
else coerces to `other` or empty). The form coerces on save, so commas in a list field
and digits in an int field are handled for you. **Leave a field empty if the source
doesn't state it** — do not guess.

### Identity
| field | type | what goes in |
|---|---|---|
| `meeting_date` | scalar | ISO date of the hearing, `YYYY-MM-DD` (comes from the meeting file). |
| `jurisdiction` | scalar | Regulating body's city/county. Defaults to `San Francisco`. |
| `supervisorial_district` | scalar | District number, e.g. `11`. Modern minutes only — empty for the HTML era. |
| `item` | scalar | Agenda item number, e.g. `1`, `12a`. |
| `case_number` | scalar | Planning case number, e.g. `98.226D`, `2022-001764CUA`. |
| `request_type` | enum | `conditional_use`, `conditional_use_modification`, `discretionary_review`, `variance`, `rezoning_map_amendment`, `planning_code_amendment`, `general_plan_amendment`, `text_amendment`, `large_project_authorization`, `downtown_project`, `ceqa_environmental`, `historic`, `coastal`, `office_allocation`, `other`. Often inferable from the case-number suffix (e.g. `…C` → conditional_use, `…DR`/`DRP` → discretionary_review). |
| `resolution_or_motion_no` | scalar | Permanent action id, e.g. `Motion No. 14638`. Use the *action's* motion, not a cited prior one. |

### Location
| field | type | what goes in |
|---|---|---|
| `project_address` | scalar | Street address of the project. |
| `assessor_block` | scalar | Assessor's block number. |
| `lot_number` | scalar | Lot number(s). |

### Zoning & scale
| field | type | what goes in |
|---|---|---|
| `type_district` | scalar | Use/zoning district **code**, e.g. `RH-2`, `NC-3`, `UMU`. |
| `type_district_descr` | scalar | Plain-English district name, e.g. `residential, mixed-use, low-density`. |
| `height_and_bulk_district` | scalar | Height & bulk district, e.g. `40-X`, `50-N`. |
| `special_use_district` | scalar | Special use district, if any. |
| `units_proposed` | int | **Net new** dwelling units proposed. Watch word-numbers ("two-unit" → `2`); `0` for parking/use items with no new units. |
| `units_demolished` | int | Dwelling units demolished/removed. Non-dwelling demolition (garage, shed) → `0`. |
| `parking_spaces` | scalar | Parking spaces, e.g. `4` or `from 6 to 4` (capture the change, not just the first number). |
| `demolition` | enum | `yes` / `no` — does the project involve demolition? |
| `project_descr` | text | Full request/description text. The safety-net free-text field — keep it complete. |

### Process
| field | type | what goes in |
|---|---|---|
| `staff_planner` | scalar | Assigned planner from the block header, e.g. `D. Winslow` (initial + surname; **drop the phone number**). Machine pre-filled. |
| `preliminary_recommendation` | scalar | **Staff** recommendation, e.g. `Approve with Conditions`, `Disapprove`, `Do Not Take DR and Approve`. |
| `ceqa_determination` | enum | `exempt`, `categorical_exemption`, `negative_declaration`, `mitigated_negative_declaration`, `eir`, `addendum`, `none`, `other`. |
| `continued_to` | scalar | If continued: target date `YYYY-MM-DD`, or `indefinite`. |
| `action` | enum | `approved`, `approved_with_conditions`, `approved_as_modified`, `disapproved`, `continued`, `continued_indefinitely`, `withdrawn`, `did_not_take_dr`, `took_dr`, `took_dr_and_approved`, `filed`, `no_action`, `other`. This is the Commission's disposition — map the `ACTION:` line to the enum (re-prefill `heuristic` does this for migrated `other`s). |

### Politics
| field | type | what goes in |
|---|---|---|
| `speakers` | list | Public speaker names, comma-separated. |
| `support_count` | int | # speakers in **support** (the `+` marker; modern minutes). |
| `oppose_count` | int | # speakers in **opposition** (the `-` marker). |
| `neutral_count` | int | # **neutral** speakers (the `=` marker). |
| `speaker_statements` | text | Notes/verbatim of what speakers or commissioners argued. Optional. |
| `ayes` | list | Commissioners voting **aye**, comma-separated surnames. |
| `noes` | list | Commissioners voting **no**. |
| `absent` | list | Commissioners absent for the vote. |
| `recused` | list | Commissioners recused (conflict of interest). |
| `excused` | list | Commissioners excused. |
| `vote` | scalar | Tally, e.g. `7-0`, `5-2`. **Derived** in `coerce_record()` from `len(ayes)-len(noes)` when left blank; a stated tally is kept. So you can usually leave it empty and let it compute — but if the minutes print a tally that disagrees with the roll call, type the stated one and double-check the `ayes`/`noes`. |

### Conditions
| field | type | what goes in |
|---|---|---|
| `modifications` | text | Conditions / modifications imposed by the Commission. |

---

## 5. Two worked examples

These are the canonical examples from `data_infrastructure.md` — one from each era — to
calibrate what a correct record looks like (`✎` = a field the human fixed after the
machine pre-fill).

### Example A — 2006 conditional use (HTML era)

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

**Correct record** (non-empty fields):

```json
{
  "meeting_date": "2006-…",                 // from the meeting file
  "jurisdiction": "San Francisco",
  "item": "6",
  "case_number": "2006.0893C",
  "request_type": "conditional_use",        // derived from the "C" suffix
  "resolution_or_motion_no": "Motion No. 17322",   // ✎ the *action's* motion (17322),
                                                    //   not the cited prior Motion 14737
  "project_address": "1099 Sunnydale Avenue",
  "assessor_block": "6363",
  "lot_number": "001",
  "type_district": "RM-1",                  // ✎ heuristic missed the parenthetical form
  "type_district_descr": "residential, mixed-use, low-density",   // ✎
  "height_and_bulk_district": "40-X",
  "units_proposed": 0,                      // ✎ parking/community-space mod — no new units
  "parking_spaces": "from 18 to 6",         // ✎ heuristic grabbed "18" (existing requirement)
  "staff_planner": "J. Purvis",             // machine pre-filled from the header
  "preliminary_recommendation": "Approval with conditions",
  "action": "approved",
  "speakers": [],
  "ayes": ["Alexander","Olague","Antonini","Lee","Moore","Sugaya"],
  "vote": "6-0"                             // derived from 6 ayes, 0 noes
}
```

The everyday case: pre-fill nails identity, parcel, recommendation, action, and roll-call;
the human fixes the *action's* motion vs. a cited one, the zoning district in a
non-standard parenthetical, and "18 → 6" parking.

### Example B — 2023 discretionary review (modern PDF era)

Shows the modern-only fields — **supervisorial district**, **speaker stance markers**
(`+`/`-`/`=`), **CEQA determination** — on a contested item.

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

**Correct record**:

```json
{
  "meeting_date": "2023-01-05",
  "jurisdiction": "San Francisco",
  "supervisorial_district": "2",            // from "(District 2)" (modern only)
  "item": "13",
  "case_number": "2021-002057DRP",
  "request_type": "discretionary_review",   // derived from the "DRP" suffix
  "project_address": "2011 Filbert Street",
  "assessor_block": "0532",
  "lot_number": "003A",
  "type_district": "RH-2",                   // ✎ modern "… Zoning District" form
  "height_and_bulk_district": "40-X",
  "units_proposed": 2,                       // ✎ "two-unit" is a word, not a digit
  "units_demolished": 0,                     // ✎ garage + shed, not dwelling units
  "demolition": "yes",
  "ceqa_determination": "exempt",
  "staff_planner": "D. Winslow",             // machine pre-filled from the header
  "preliminary_recommendation": "Do Not Take Discretionary Review and Approve",
  "action": "did_not_take_dr",               // "No DR" → enum
  "speakers": ["David Winslow","Devon Johnson","Tara Sullivan","Ms. Albericci","Rhonda Miller"],
  "support_count": 1,                        // the single "+"
  "oppose_count": 3,                         // three "-"
  "neutral_count": 1,                        // the "="
  "ayes": ["Braun","Ruiz","Koppel","Moore","Tanner"],
  "absent": ["Diamond","Imperial"],
  "vote": "5-0"                              // derived from 5 ayes, 0 noes
}
```

Read the politics straight off the record: a neighbor-initiated DR with **3 opposed vs. 1
in support**, where the commission sided with the project sponsor (no DR, 5-0) and staff
had recommended exactly that (`preliminary_recommendation` = `action` ⇒ no staff override).

---

## 6. When you finish

Once no `prelabeled` items remain (all reviewed → `done`):

```bash
# In the app: "Export done →"  writes status=done labels to
#   tagged/training/{year}_labeled.json   (backs up the old file to *.preexport.bak)

python ../training_sample_create.py        # rebuild the consolidated training.txt
python ../train.py                         # fine-tune, or:
python ../llm_extract.py --backend anthropic --shots 5
```

Export only writes items you've marked `done`, and it backs up the prior JSON first — so
your review *is* what becomes the gold standard.
```
