# Help-string audit — SCHEMA v1 → v2

**Required deliverable of `extraction_pipeline_v2_spec.md` §3.2.**
Date: 2026-09-07. Applied in `extraction_common.py`; this file records what changed and why.

`PROMPT_INSTRUCTION` is generated from `SCHEMA` help strings by `build_prompt()`, so **every
word of help text ships to the model on every request**. The help strings are the prompt.
They were written as notes to a human labeller and had never been read as model instructions.

Each field is graded against the four categories in §3.2:

| code | meaning |
|---|---|
| **(a)** | instructs the model to do something the labelling manual forbids |
| **(b)** | leaves a format ambiguous |
| **(c)** | fails to say where in the block the answer lives |
| **(d)** | fine as-is |

The prompt grew from 3,089 to 9,572 characters (≈772 → ≈2,393 tokens). All of it is in the
**cacheable prefix**, so the marginal cost across a corpus run is one cache write.

---

## Summary

| grade | fields | of 29 |
|---|---|---|
| (a) instructs against the manual | 1 | `request_type` |
| (b) format ambiguous | 6 | `case_number`, `lot_number`, `special_use_district`, `noes`, `absent`, `modifications` |
| (c) doesn't say where the answer lives | 17 | most of the schema |
| (d) fine as-is | 5 | the six fields rewritten under §2, minus one still graded (c) |

**(c) was near-universal and is the important finding.** The bakeoff's largest residual error
was locational — `type_district` over-extraction at 25–50% is a model that has been asked for
a zoning district and has not been told that zoning districts live on exactly one line. Seventeen
of 29 help strings named a concept without naming a location.

---

## The one (a): `request_type`

The known offender, and the only field where the help text instructed the model to break the
labelling rule.

**Before**
```
Type of request (often inferable from the case-number suffix)
```

**After**
```
The type of request. Determined by the "Request for..." sentence in the block. The
case-number suffix (C, V, DR, E, ET, ...) is a weak hint and is frequently wrong: an E case
may be an appeal of a preliminary negative declaration rather than generic CEQA review, and
an ET case is a Planning Code amendment, not a CEQA determination. Where the suffix and the
text disagree, follow the text.
```

The labelling manual's rule is that the suffix is a hint and the "Request for…" sentence
decides. The old text asserted the opposite. The bakeoff's suffix-following analysis is the
check: 100% of regex `request_type` misses were "followed the suffix" (it has no other
mechanism), while the models did so on only 0.9–1.3% **despite the prompt inviting it**.
Removing the invitation should be a free gain, and `bakeoff_report.py --request-type`
measures whether it was.

---

## Field-by-field

### Identity

| field | grade | change |
|---|---|---|
| `case_number` | (b)(c) | Now names the item/case line, and states that a space-broken suffix is still one case number (`"2005.0148DD V"`). Adds the negative: not the building-permit application number. |
| `request_type` | **(a)** | See above. |
| `action_instrument` | (d) | New in v2; written to spec. Names the MOTION:/RESOLUTION:/DRA line and says the instruments are not interchangeable. **A third value, `dra`, was added on 2026-09-07**: eleven gold items print a `DRA#: 0013` (Discretionary Review Action), and the two-value enum would have blanked a real value on all eleven. |
| `action_instrument_no` | (d) | New in v2; written to spec. |

### Location

| field | grade | change |
|---|---|---|
| `project_address` | (b) | Rewritten under §2.4: form `'<number> <Street Name>'`, keep ranges, stop before the locational gloss, no city/state/ZIP. **Demoted to validation-only on 2026-09-07** — the parcel join keys on (`assessor_block`, `lot_number`), the address checks that linkage rather than carrying it, and the geocoder is off the critical path. The help text is unchanged; what changed is that `format_ok` is a soft warning, not a gate, and the field is out of `ESTIMATION_CORE`. |
| `assessor_block` | (c) | Was `"Assessor's block number"`. Now names the `"in Assessor's Block NNNN"` phrase and adds the negative: *not the lot*. |
| `lot_number` | (b)(c) | Was `"Lot number(s)"` — silent on where, and on what to do with several. Now names the `"Lot NNN"` phrase and says to give all lots comma-separated. |

### Zoning & scale

This section carried the worst over-extraction and got the most attention.

| field | grade | change |
|---|---|---|
| `type_district` | (c) | Was `"Use/zoning district code, e.g. RH-2, NC-3, UMU"`. Now: *"It appears only on the zoning line… If the block has no such line, leave this blank — do not infer a district from the address, the neighbourhood, or the kind of project."* This is the field the anatomy section (§3.1) was written for. |
| `type_district_descr` | (c) | Now tied to the parenthetical on the zoning line, with an explicit dependency: **blank whenever `type_district` is blank**. |
| `height_and_bulk_district` | (c) | §2.6 says leave alone; §3.2 requires the location. Meaning unchanged, location added. |
| `special_use_district` | (b)(c) | `"Special use district, if any"` gave no location and made blank feel like a failure. Now: *"Most items are in none: blank is the normal answer."* Adds the negative: not the use or height district. |
| `project_descr` | (d) | Rewritten under §2.3 to a pinned, mechanical target. |

### Process

| field | grade | change |
|---|---|---|
| `staff_planner` | (c) | Now names the parenthesis after the case number and shows the transformation (`"(D. WINSLOW: (415) 558-6335)"` → `D. Winslow`). Negatives added: not a commissioner, not a speaker. |
| `preliminary_recommendation` | (c) | Names the `"Preliminary Recommendation:"` line, and distinguishes it from `action`: *"what staff asked for, not what the Commission did."* |
| `preliminary_recommendation_category` | (c) | Now says to derive it from the verbatim recommendation above rather than from the block at large. |
| `continued_to` | (b) | Was correct but asked for ISO. Under §1.2 the model should return what the text says and the normaliser converts — so it now says *copy the date as printed*. This is the field that scored 0% in the first bakeoff on entirely correct values. |
| `action` | (c) | Was `"Commission's disposition of the item"`. Now names the ACTION: line and carries the three conventions that actually get this wrong: `"Approved as proposed"` on a DR case means `did_not_take_dr`; `"call of the Chair"` means `continued_indefinitely`; a failed motion with nothing carrying is `motion_failed`. |

### Politics

| field | grade | change |
|---|---|---|
| `speakers` | (d) | Restructured under §2.1, including the anonymous-count rule. |
| `support_count` / `oppose_count` / `neutral_count` | — | Now `derived: True`. **Skipped by `build_prompt()`** — never asked of the model. |
| `ayes` | (c) | Names the AYES line; adds *"these are commissioners, never members of the public"*. |
| `noes` | (b)(c) | Names the NOES/NAYES line and states that `"None"` means an empty list — a real failure mode, since the audit found `'none'` being parsed as a commissioner. |
| `absent` | (b)(c) | Same, plus the negative: excused and recused commissioners do not belong here. |
| `recused` / `excused` | (c) | Location added; both marked `unmeasurable: True` (0 and 1 gold values). |

### Conditions

| field | grade | change |
|---|---|---|
| `conditions_imposed` | (c) | Was `"(orthogonal to `action`)"` — meta-commentary, not a location. Now says where to look and carries the rule the adjudication surfaced: **conditions described *before* the action belong to a prior entitlement and do not count** (the Café Flore case, where an item recites the conditions of a prior CU). |
| `project_modified` | (b) | Now draws the line explicitly: yes only when the design or scope changed, not merely when conditions were attached. These two fields were being conflated. |
| `modifications` | (b)(c) | Says where to copy from, and that bullets and numbering may be dropped — which is what the free-text comparison already tolerates, so the instruction and the metric now agree. |

---

## What was deliberately not changed

- **No help string tells the model a storage format.** Dates, lot lists and address truncation
  are `normalize.py`'s job (§1.2, §5). The one help string that violated this (`continued_to`
  asking for ISO) was fixed.
- **No help string was loosened to make a metric pass.** `project_descr` and `project_address`
  were pinned *harder*, per §12: a comparator that forgives an error still admits the error.
- **`case_number` keeps its shape.** It is the one field where the regex beats every model
  (99.1%), and nothing about it needed changing except naming the line and adding the
  permit-number negative.

## Open item

`build_prompt(era=...)` accepts an era argument but currently returns the same anatomy for
both. §3.4 requires inspecting whether the 2015–2026 PDF minutes follow a different item
layout before writing a second anatomy section, and the modern gold set does not exist yet.
The parameter is in place so that becomes a one-line change rather than a refactor.
