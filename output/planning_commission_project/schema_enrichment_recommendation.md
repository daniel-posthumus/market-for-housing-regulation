# Schema Enrichment Recommendation — SF Planning Commission Minutes

*Decision memo answering `.claude/instructions/schema_enrichment_investigation.md`. Tests each of
the five candidate fields against the actual corpus before the ~9,000-item relabel, applies the
brief's 4-part decision rule, and runs the two cross-cutting checks. Schema edits that follow from
an ADOPT verdict have been applied to `extraction_common.py`; DEFER/REJECT candidates were left
untouched.*

---

## Method & corpus

- **HTML era (1998–2014):** measured on the parsed project blocks in
  `data/meeting_minutes/tagged/{year}/*.txt` (one `<<Project Start>>…<<Project End>>` block per
  agenda item). 9,001 blocks available; a random 800-block sample was scored.
- **Modern era (2015–present):** 2015 is pre-converted `.txt`; 2016–2025 are PDFs, extracted with
  `pdftotext -layout`. Meeting text was split into item blocks on the
  `^<item-no>. <case-no>` header. 601 blocks from 47 meeting files (a spread across
  2015/2018/2020/2022/2023/2024/2025); all scored.
- Presence was measured with regex detectors over raw block text (not the legacy
  `processed/structured_data.jsonl`, which is low quality). "Construction item" = block whose text
  contains construct/new-building/addition language (~48% of HTML items, ~54% of modern items).

The corpus under `data/meeting_minutes/` is **SF-only** — there are no other-jurisdiction sample
minutes to inspect, so check B is reasoned from process knowledge, not measured.

---

## Verdict table

| # | Candidate | HTML 1998–2014 | Modern 2015–present | Verdict |
|---|---|---|---|---|
| 1 | `stories_proposed` / `height_proposed_ft` | stories 63% of construction items (37% of all); height-in-ft 20% of all | stories 66% of construction items (44% of all); height-in-ft 21% of all | **DEFER** (derive-first from `project_descr`) |
| 2 | `discretion_trigger` | section-cite 38%; trigger-*reason* words 19% | section-cite 58%; trigger-*reason* words 29% | **DEFER** (post-hoc NLP + code-section map) |
| 3 | `units_affordable` / `inclusionary_pct` | mentioned 11%; grabbable count/pct **1%** | mentioned 11%; grabbable count/pct **1%** | **DEFER** (blocked on staff reports) |
| 4 | `staff_planner` | present **81%** (≈universal on real project items) | present **96%** | **ADOPT** ✅ |
| 5 | `appeal_status` / final-disposition | appeal word 8%; BoS/BoA refs 14% (boilerplate) | appeal word 5%; BoS/BoA refs 11% (boilerplate) | **REJECT** (wrong corpus) |

Net: **adopt 1, defer 3, reject 1.** One field added to the schema.

---

## Per-candidate findings

### 1. `stories_proposed` (and `height_proposed_ft`) — DEFER

- **Presence.** A story count appears in **63% (HTML) / 66% (modern)** of *construction* items but only
  37%/44% of all items (use/process items — CU for a use change, condo conversions, parking
  modifications, DR of a permit — frequently omit it). Height in **feet** is far rarer: ~20%/21% of
  all items; the proposed envelope is usually expressed in stories, not feet.
- **Extractability.** This is the problem. Story figures are word-form in prose ("a new **four-story**
  building"), and construction blocks routinely carry **multiple** story numbers — existing structure,
  proposed addition, and total — e.g. *"two-stories over a crawlspace … a 3-story rear horizontal
  addition."* A labeler must read to pick the *proposed* value, and a naive regex grabs the wrong one.
  So it is neither a clean seconds-long grab **nor** a clean script extraction.
- **Non-recoverability.** Every story/height figure already lives verbatim in `project_descr` (the
  full-text safety net). Rule 3 says do not open labeling surface for something a script can compute —
  but the ambiguity above means a *naive* script is unreliable.
- **Model relevance.** High — it is the position-above-envelope quantity the two-margin model wants.
- **Decision.** DEFER, don't hand-label yet. Run a **derived extraction from `project_descr`** (an
  "N-story" pass that resolves existing-vs-proposed-vs-addition) over the construction subset and
  measure its accuracy. **What flips this to ADOPT:** that derived pass scoring below an acceptable
  threshold on a hand-checked construction sample — at which point add `stories_proposed` (int) and
  `height_proposed_ft` (int) to the **Zoning & scale** section so a human resolves the ambiguity.
  Adding it now would pay correction cost on the ~50–65% of items where it is absent or unambiguous
  and a script would have nailed it.

### 2. `discretion_trigger` — DEFER

- **Presence.** A code-section *citation* is present in **38% (HTML) / 58% (modern)** of items
  ("pursuant to Planning Code Section 303 …"). But the human-readable *reason* (exceed height,
  rear-yard/setback variance, density bonus, FAR, parking) surfaces in plain words in only
  **19% / 29%**.
- **Extractability.** The reason is usually recoverable only by mapping the *section number* to a
  trigger category (303 = CU/exceedance, 317 = demolition/merger, 311/312 = neighborhood
  notification, 134 = rear yard, 209/243/249.x = SUD limits, …). Items routinely cite **several**
  sections at once, so a single enum cannot represent the trigger — it would need a **list** field —
  and the section→category map must be built and maintained against code that changes over time.
- **Non-recoverability.** The raw citations are inside `project_descr`, so the input is preserved; only
  the *categorization* is missing.
- **Model relevance.** High in principle — it is the bridge variable identifying *which* by-right
  margin binds.
- **Decision.** DEFER to a **post-hoc NLP / lookup pass over `project_descr`**, emitting a *list* of
  trigger categories, rather than a hand-labeled enum during the relabel. **What flips this to ADOPT:**
  a validated, maintainable section→trigger lookup table plus confirmation that a list field (not a
  scalar enum) is acceptable — then add `discretion_triggers` (list).

### 3. `units_affordable` / `inclusionary_pct` — DEFER

- **Presence.** Affordable / BMR / inclusionary language is mentioned in **11%** of items in both eras,
  but a **grabbable** affordable-unit count or inclusionary percentage appears in only **~1%**. Most
  mentions are *policy* items (Inclusionary Housing Program ordinance amendments) or generic public-
  comment phrases, not project-level deal terms. Density-bonus / HOME-SF references are ~0% (HTML) /
  2% (modern).
- **Extractability.** When the number exists it is in the **staff report**, which is not part of the
  minutes corpus. The minutes carry the disposition, not the BMR schedule.
- **Non-recoverability.** Not recoverable from the minutes at all for most items — it is a different
  source document.
- **Model relevance.** Real (the give-to-get exaction margin), but unfillable from this corpus.
- **Decision.** DEFER, concentrated in large-project authorizations / downtown projects. **What flips
  this to ADOPT:** ingesting the per-case staff reports as a second source; this is a corpus-expansion
  decision, not a schema decision.

### 4. `staff_planner` — ADOPT ✅

- **Presence.** The assigned planner is the **first parenthetical of the block header** —
  `(D. WINSLOW: (628) 652-7335)`. Detected in **81%** of HTML blocks and **96%** of modern blocks; the
  HTML misses are almost entirely non-project blocks (informational/continuance-calendar headers) and
  old-HTML parse artifacts, so on real project items it is effectively universal.
- **Extractability.** Maximal — it is already in front of the labeler, and the format is a stable
  `<initial>. <SURNAME>`. A regex pre-fill captures it cleanly (validated across 1998–2025 samples), so
  this is pure correction-not-typing.
- **Resolution to a stable id.** The name normalizes to `Initial. Surname` (title-cased). Only the
  **phone number** drifts (415→628 area code, 558→575→652 prefixes) — the *name* is stable, so
  `initial + surname` is a clean planner key with no entity-resolution project required (minor
  casing like `A. STARR`/`A. Starr` is handled by `.title()`).
- **Non-recoverability.** The planner name is **not** in `project_descr` or any existing field — it is
  in the header only. autoextract did not previously capture it. So it must be captured to exist.
- **Model relevance.** Direct — planner fixed effects as an identification lever / control in the
  disposition analysis.
- **Decision.** ADOPT. Lean, high-traction, near-universal, instantly markable, not recoverable from
  any existing field. **(Applied — see "SCHEMA edits" below.)**

### 5. `appeal_status` / final-disposition linkage — REJECT

- **Presence.** "Appeal" appears in **8% (HTML) / 5% (modern)** of blocks and Board-of-Supervisors /
  Board-of-Appeals references in 14% / 11%, but inspection shows these are **boilerplate** — the
  Director's-report line "no report from the Board of Appeals," CEQA-appeal agenda items, or the
  standard "this action may be appealed" notice. The *appeal status of a given project's commission
  disposition* (whether it was later appealed to and overturned by the Board of Supervisors) is **not
  in the planning-commission minutes**; it lives in a separate Board of Supervisors / Board of Appeals
  record series.
- **Decision.** REJECT for a minutes-only build — adding it would create an unfillable field. **What
  flips this to ADOPT:** acquiring and key-joining the BoS/BoA record series, after which final
  disposition becomes a *cross-corpus linkage*, not a labeled field.

---

## Cross-cutting check A — derive-don't-label audit

| Quantity | Recommendation | Where | Status |
|---|---|---|---|
| `vote` from `len(ayes) − len(noes)` | **Derive, not hand-label** | `coerce_record()` (coercion-time) | **Implemented** |
| staff-override indicator (`preliminary_recommendation` vs `action`) | Feasible; defer implementation | post-processing | Not implemented (recommended) |
| continuance count / first-to-final span | Needs a case-level table the item schema can't express | post-processing join on `case_number` | Not implemented (defer) |

- **`vote` — implemented.** `vote` is fully recoverable from the roll call and should not consume
  labeling surface. Added a coercion-time derivation that fills `vote` as `"{len(ayes)}-{len(noes)}"`
  **only when `vote` is empty and `ayes` is present** — it never overwrites a stated tally, so a later
  QA pass can still flag ayes/noes-vs-tally mismatches. Non-destructive and applies uniformly to human
  labels, model output, and migrated records. (The mismatch *flag* itself belongs in a QA/validation
  pass, not in `coerce_record`, which by design returns only the record.)
- **staff-override — recommended, deferred.** `staff_override = normalize(preliminary_recommendation)
  != action` is feasible by reusing autoextract's `_action_enum()` to map the free-text staff
  recommendation onto the `ACTIONS` enum. Held back from implementation because it introduces a new
  derived output field plus a maintained normalization map (drift risk), and the brief frames it as
  "assess feasibility." Implement as a post-processing step once the relabel settles
  `action`/`preliminary_recommendation` normalization.
- **continuance count / disposition span — defer.** Requires joining records on `case_number` across
  meetings, i.e. a **case-level table** the current item-level schema does not express. Recommend a
  downstream case-level rollup table built from the item records, **not** a new hand-labeled field and
  **not** a schema change now.

**None of the three should become hand-labeled fields in the relabel.**

## Cross-cutting check B — multi-jurisdiction forward-compatibility

No other-jurisdiction minutes exist in the corpus to inspect, so this is reasoned from process
knowledge. **No new fields are needed now**, for these reasons:

- The schema is already **jurisdiction-tagged** (`jurisdiction`, defaulting to "San Francisco"), so a
  second locality's records coexist without a structural change.
- The enums that are **SF-specific** are `request_type` and `action` — values like
  `discretionary_review`, `took_dr`, `did_not_take_dr`, `large_project_authorization`,
  `office_allocation` encode mechanisms many Bay Area suburbs **do not have**. The fact that a
  jurisdiction has *no* discretionary-review path is itself an informative datum about its
  by-right/discretionary split, so it should be representable, not erased.
- Both enums already include `"other"`, and `coerce_record()` snaps unknown values to `"other"`,
  so foreign vocabularies degrade gracefully rather than crashing.

**Recommendation:** do **not** union-in hypothetical fields now. When a second jurisdiction is added,
introduce a **per-jurisdiction synonym/mapping layer** that maps that locality's hearing types and
dispositions onto the canonical enums (and extends `REQUEST_TYPES`/`ACTIONS` only for genuinely new,
recurring categories). Build the mapping layer when the second corpus arrives — not speculatively.

---

## SCHEMA edits applied to `extraction_common.py`

Only the ADOPT verdict (#4) and check-A item 1 (`vote`) were applied. DEFER/REJECT candidates were
left untouched.

**1. New field `staff_planner` (Process section):**

```python
# — process & friction —
{"name": "staff_planner", "type": "scalar", "section": "Process",
 "help": "Assigned planner from the block header, e.g. 'D. Winslow' (initial + surname; drop phone)"},
{"name": "preliminary_recommendation", "type": "scalar", "section": "Process",
 "help": "STAFF recommendation, e.g. 'Approve with Conditions', 'Disapprove'"},
```

- name: `staff_planner` · type: `scalar` · section: `Process` (no enum). This one-list edit propagates
  automatically to `FIELDS`, the labeling form (served by `labeling_app/app.py` from `SCHEMA`/
  `SECTIONS`), `PROMPT_INSTRUCTION`, the required-key list (`training_sample_create.REQUIRED = FIELDS`),
  and `score_examples()`. Schema size is now **36 fields**.

**2. Coercion-time `vote` derivation (check A), end of `coerce_record()`:**

```python
        else:
            out[n] = str(v).strip()
    # Derived (check A): vote is recoverable from the roll call, so it is computed
    # rather than hand-labeled. Fill only when empty — never overwrite a stated
    # tally — so a QA pass can still flag ayes/noes-vs-tally mismatches.
    if not out["vote"] and out["ayes"]:
        out["vote"] = f"{len(out['ayes'])}-{len(out['noes'])}"
    return out
```

**3. Supporting pre-fill in `autoextract.py`** (so the new field is machine-filled for
correction-not-typing; not a schema change):

```python
    pl = re.search(r"\(\s*([A-Z]\.?\s*[A-Za-z][A-Za-z'\-]+(?:-[A-Za-z'\-]+)?)\s*"
                   r":?\s*\(?\d{3}\)?[\s\-]?\d{3}", block)
    if pl:
        rec["staff_planner"] = re.sub(r"\s+", " ", pl.group(1).strip()).title()
```

### Verification

- `ast.parse` succeeds on both edited files; `import extraction_common` succeeds.
- `len(FIELDS) == 36`; `staff_planner` present; `SECTIONS` unchanged (it lands in the existing
  `Process` section); `PROMPT_INSTRUCTION` contains `- staff_planner:`.
- `coerce_record` derives `vote="6-0"` from 6 ayes / 0 noes, **preserves** a stated `"5-2"`, and
  leaves `vote` empty when there are no ayes.
- `score_examples()` still builds; `empty_record()` is schema-complete (`staff_planner=""`).
- `autoextract.extract()` fills `staff_planner="J. Purvis"` and `vote="3-0"` on the doc's worked
  example.

> Note: `code/.../paths.py` still defaults `DATA_ROOT` to the old Google Drive path. The corpus now
> lives only in Dropbox, so scripts must be run with `MFHR_DATA_ROOT` pointed at
> `…/Dropbox/market-for-housing-regulation/data` (or `paths.py` updated). Out of scope for this memo,
> but flagged so the relabel pipeline can find the data.
