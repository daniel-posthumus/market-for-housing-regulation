# SF Planning Commission Minutes — Labeling Rules (Coding Manual)

*The authoritative, San-Francisco-specific decision rules for turning one agenda item
into one structured record. This is the spec your hand-labels are graded against — when
a label and this manual disagree, fix the label. Companion to `data_infrastructure.md`
(the 36-field schema + worked examples) and `hand_label_review_guide.md` (the app
workflow). Several rules here are already enforced automatically by
`code/commission_minutes_processing/label_qa.py`; those are tagged **[QA]**.*

Schema source of truth: `code/commission_minutes_processing/extraction_common.py`
(`SCHEMA`). Enum values quoted below are copied verbatim from it — only those values are
valid (anything else coerces to `other` or empty).

---

## 0. Golden rules (apply everywhere)

1. **The raw block wins.** Code what *this* block's text says, not what you remember or
   what a prior label said.
2. **Empty, never guess.** If the block doesn't state a field, leave it empty (`""`, `[]`,
   or `0` for counts). A wrong guess is worse than a blank. **[QA]** flags fields the
   source clearly states but the label left empty.
3. **One record = one agenda item at one meeting** (one `<<Project Start>>…End>>` block).
4. **Code the *disposition*, not the proposal.** The single most common past error was
   recording a *proposed* continuance ("Proposed for Continuance to Aug 6") as the
   `action`. The `action` is whatever the **`ACTION:`** line says the Commission did. **[QA]**
5. **Surnames only, lower-friction.** Commissioner/speaker lists are surnames; the form
   coerces comma-separated text to a JSON list on save.

---

## 1. Unit of analysis & recurring cases

- **Multi-part items** (`3a.`, `3b.`) are **separate records** — each has its own case
  number and often its own action.
- **A case heard across several meetings** (continued, then heard later) yields **one
  record per meeting**. Judge each block on its own text: the continuance-calendar
  appearance is `action = continued`; the later substantive hearing carries the real
  disposition. Do **not** copy the final disposition back onto the continuance block.
- **Non-land-use agenda items** — "Land Acknowledgement", "Commission
  Comments/Questions", "Director's Announcements", "Consideration of Adoption [of
  draft minutes]", "Election of Officers", "Review of Past Events at the Board of
  Supervisors". These are agenda items but **not discretionary land-use decisions**:
  leave `case_number`, `request_type`, parcel, and zoning fields **empty**; record
  `action` only if the block states one (e.g. "Adopted" → `other`). They are not part of
  the analysis sample; don't invent fields for them.
- **Closed sessions** (`*_closed_min`) are excluded from the corpus entirely — you won't
  see them.

---

## 2. Era cheat-sheet

| Feature | HTML era 1998–2014 | Modern era 2015–present |
|---|---|---|
| Case-number format | dot: `98.226D`, `2006.0893C` | dash: `2021-002057DRP` |
| `supervisorial_district` | **not stated** → leave empty | `(District 2)` in header → fill |
| Speaker stance `+ / − / =` | usually **absent** → counts stay `0` | usually present → fill counts. Rule is marker-based, not era-based: count only explicit markers, never infer stance |
| Zoning wording | "… RM-1 (…) **Use District**" | "… RH-2 (…) **Zoning District**" |
| Source | scraped HTML | PDF (pdfplumber) / text |

---

## 3. Identity fields

### `meeting_date` — ISO `YYYY-MM-DD`
Comes from the meeting, not the item; the app pre-fills it from the file. Verify it
matches the hearing date in the header.

### `case_number`
- Copy the item's **own** planning case number; **drop spaces** (`2022-001764CUA`).
- Formats: dot (`98.226D`, four-digit year `2006.0893C`) and dash (`2021-002057DRP`).
- A case can carry **multiple request suffixes** (`2006.0074CDV` = CU + DR + Variance);
  keep the full string.
- **Do not** record a *cited prior* case or a Building Permit number as the case number.
- **[QA]** flags blocks that print a case number but whose label left it empty.

### `request_type` (enum) — infer from the case suffix, confirm with the "Request for…" text
Valid values: `conditional_use, conditional_use_modification, discretionary_review,
variance, rezoning_map_amendment, planning_code_amendment, general_plan_amendment,
text_amendment, large_project_authorization, downtown_project, ceqa_environmental,
historic, coastal, office_allocation, other`.

**SF suffix → request_type** (the authoritative map, mirrors `autoextract.derive_request_type`):

| Suffix | request_type |
|---|---|
| `C`, `CUA` | `conditional_use` |
| `D`, `DR`, `DRP`, `DRM`, `DD` | `discretionary_review` |
| `V` | `variance` |
| `Z`, `MAP` | `rezoning_map_amendment` |
| `PCA` | `planning_code_amendment` |
| `GPA` | `general_plan_amendment` |
| `T` | `text_amendment` |
| `E`, `ENV`, `ENX` | `ceqa_environmental` |
| `X`, `LPA` | `large_project_authorization` |
| `OFA` | `office_allocation` |
| `H`, `COA` | `historic` |
| `R` | `downtown_project` (Planning Code §309) |

Rules:
- **Multiple suffixes →** use the type of the request the Commission is actually *acting
  on* (usually the lead entitlement; a CU + Variance heard together → `conditional_use`
  if the CU is the substantive ask). If genuinely co-equal, pick the first/most
  consequential and note it.
- **`conditional_use_modification`** when the request modifies *conditions of a prior CU*
  (Planning Code §303(e), "modify Conditions of Approval under Motion No. …").
- **Suffix not in the table** (e.g. `U`, `CRV`, `SHD`): classify from the "Request for …"
  sentence; if it doesn't map cleanly to a value, use `other` — don't force it.
- **Article 11 Permit to Alter** (a "Major Alteration" to a Category I–IV downtown/C-3
  building) → `historic` — it's the C-3 analog of a Certificate of Appropriateness; do
  **not** use `downtown_project` (that's §309), even though the parcel is in a C-3 district.
- `coastal` only for actual coastal-zone permits.
- **[QA]** flags empty `request_type` when the suffix clearly implies one.

### `resolution_or_motion_no`
- The **action's own** identifier from the `MOTION:` / `RESOLUTION:` line, e.g.
  `Motion No. 17322`. CUs/most actions get a **Motion No.**; some get a **Resolution No.**
- **Not** a prior motion cited in the description (e.g. "…under Motion No. 14737" is the
  *cited* one — skip it). This cited-vs-action confusion is a known trap. **[QA]**

### `item`
Agenda item number as printed (`1`, `12a`, `3b`).

---

## 4. Location fields (geocoding keys — keep clean)

| field | rule |
|---|---|
| `project_address` | Street address only (`2011 Filbert Street`). Drop "south side between … and …" cross-street prose. |
| `assessor_block` | Block number only (`0532`, `6363`). Keep leading zeros as printed. **[QA]** back-fills when empty but stated. |
| `lot_number` | Lot(s) as printed (`003A`, `001`, `12 and 13`). |

---

## 5. Zoning & scale

| field | rule |
|---|---|
| `type_district` | The use/zoning **code**: `RH-2`, `RM-1`, `NC-3`, `UMU`, `RTO`, `C-3-O`, etc. From "within the **RH-2** (…) Zoning/Use District". **[QA]** back-fills when empty but stated (the 2014 gap). |
| `type_district_descr` | The plain-English name in the parenthetical, e.g. `residential, house, two-family` — lower-case it. |
| `height_and_bulk_district` | The height/bulk code: `40-X`, `50-N`, `105-F`, `240-S`. From "… and a **40-X** Height and Bulk District". |
| `special_use_district` | Named SUD if any (e.g. `Van Ness SUD`); usually empty. |
| `project_descr` | The full "Request for …" sentence(s). The safety-net field — keep it complete; this is where reviewers verify everything else. |

---

## 6. Process & friction

### `staff_planner`
From the block header `(D. WINSLOW: (628) 652-7335)` → `D. Winslow` (leading initial +
surname, Title-Cased, phone dropped). **[QA]** back-fills from the header.

### `preliminary_recommendation`
The **staff** recommendation, roughly verbatim: `Approval with Conditions`, `Disapprove`,
`Do Not Take DR and Approve`, `Uphold Preliminary Mitigated Negative Declaration`. Keep it
distinct from `action` — the gap between them is the staff-override signal the project
studies, so precision here matters.

### `continued_to`
Only when continued: the **target date** as ISO `YYYY-MM-DD`, or `indefinite` for
"continued indefinitely / off calendar / to the call of the chair".

### `action` (enum) — the disposition; **most error-prone field, read carefully**
Valid: `approved, approved_with_conditions, approved_as_modified, disapproved, continued,
continued_indefinitely, withdrawn, did_not_take_dr, took_dr, took_dr_and_approved, filed,
no_action, other`.

Read the **`ACTION:`** line and map:

| `ACTION:` text | value |
|---|---|
| Approved | `approved` |
| Approved with Conditions | `approved_with_conditions` |
| Approved as Modified / as Amended | `approved_as_modified` |
| Disapproved / Denied | `disapproved` |
| Continued [to date] / Continued as Proposed | `continued` (+ set `continued_to`) |
| Continued Indefinitely / off calendar | `continued_indefinitely` |
| Withdrawn | `withdrawn` |
| Filed | `filed` |

**Discretionary Review (`…D/DR/DRP`) items use the DR-specific values** — *not* approved/disapproved:

| `ACTION:` text | value |
|---|---|
| Did Not Take DR / No DR | `did_not_take_dr` |
| Took DR (and modified) | `took_dr` |
| Took DR and Approved | `took_dr_and_approved` |

Hard rules:
- **Disposition only.** "Proposed for Continuance to X" / "(Continued from … of …)" is
  context, **not** the action. Code what the `ACTION:` line states. **[QA]** flags
  `action='other'` and cross-family mismatches vs the source `ACTION:` line.
- For a DR item that the Commission simply didn't take, it's `did_not_take_dr`, **not**
  `approved` (the permit stands, but the Commission's action was on the DR).
- `no_action` for items pulled with no vote; `other` only when nothing above fits.

---

## 7. Politics — speakers & votes

### Speaker stance — only from explicit `+ / − / =` markers
Some `SPEAKERS:` lists mark each speaker's position:
- `+` = **support** → `support_count`
- `−` = **oppose** → `oppose_count`
- `=` = **neutral** (incl. staff "– Staff report") → `neutral_count`

Rules:
- `support_count`/`oppose_count`/`neutral_count` = the **number of marked speakers** of
  each sign. **Count only explicit markers.** If the block has no `+/−/=` markers, leave
  all three at `0` — **do NOT infer** support/opposition from what a speaker said. (Markers
  are mostly a modern feature but show up in some older minutes; go by the markers present,
  not the year.)
- `speakers` = the **names** (drop the "– topic" after the dash). If nobody spoke
  (`SPEAKERS: None`), leave `speakers` **empty — never write "None" as a name.**

### Roll-call lists — `ayes`, `noes`, `absent`, `recused`, `excused`
- Surnames only, as a list. The SF Commission seats **7**, so no roll-call list — and no
  side of a vote tally — can exceed 7.
- Disambiguate same-surname members as printed (`W. Lee`, `S. Lee`).
- **If a category is empty, leave the list empty — never write "None".** A literal "None"
  entry adds a phantom member and corrupts the tally (a phantom noe turns a real `7-0` into
  an impossible `7-1`). Coercion now strips "none"/"n/a" defensively, but don't enter it.
  **[QA]** back-fills `ayes`/`noes`/`absent` when the block has the roll-call line but the
  label is empty.
- **`recused`** = stepped aside for a conflict of interest (different from `absent` =
  not present, and `excused` = formally excused). Code each from its own labeled line.

### `vote`
Tally string `7-0`, `5-2`. If the block prints a tally, use it; otherwise the app derives
it as `len(ayes)-len(noes)`. **Sanity: ayes + noes ≤ 7** (the Commission's size) — a tally
like `7-1` is impossible and almost always a "None" wrongly entered in `noes`. **[QA]**
flags a stated tally that disagrees with the aye/noe counts.

---

## 8. Conditions

### `modifications`
Conditions or modifications the Commission imposed (free text). Empty if none.

---

## 9. Quick worked decisions (the recurring judgment calls)

| Situation | Do this |
|---|---|
| DR item, "ACTION: No DR" | `action = did_not_take_dr` (not `approved`) |
| "ACTION: Continued as proposed" | `action = continued`, `continued_to = <date>` |
| Description cites "Motion No. 14737"; MOTION line says 17322 | `resolution_or_motion_no = Motion No. 17322` |
| Item `3a` and `3b` under one heading | two records, one per sub-item |
| Same case on the continuance calendar *and* heard later | two records; continuance block = `continued` |
| Land Acknowledgement / Commission Comments | minimal record: `action` if stated, everything land-use empty |

---

## 10. How these rules are checked

`label_qa.py` operationalizes the **[QA]**-tagged rules: it diffs each hand-label against
its source block and flags likely violations (continuance mis-coding, `action='other'`,
dropped roll-call/`vote`/districts, missing case number, cited-vs-action motion). Run:

```bash
python code/commission_minutes_processing/label_qa.py            # report only
python code/commission_minutes_processing/label_qa.py --apply --backfill   # fix + flag for review
```

So the workflow is: **label to this manual → `label_qa.py` catches deviations → you
confirm in the app.** As new SF edge cases surface during review, add the rule here first
(this file is the spec), then, if it's mechanically checkable, extend `label_qa.py` to
enforce it.
