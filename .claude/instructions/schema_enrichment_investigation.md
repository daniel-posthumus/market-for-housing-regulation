# Schema Enrichment Feasibility Investigation

*A brief for Claude Code. The goal is an **investigation and recommendation**, not an
implementation. Do not add any field to the schema unless this investigation shows it has
real traction in the source data. A field that is rarely present, unreliable to extract, or
recoverable later from `project_descr` should be **rejected or deferred**, with the reasoning
recorded.*

---

## Context

We are about to pay a large fixed cost: a full human relabeling pass over the SF Planning
Commission minutes corpus (1998–present, ~9,000 items) under the canonical 35-field schema
defined in `extraction_common.py`. Adding a field *after* that pass means a second pass, so
we want to settle the schema *before* relabeling.

The data pipeline and schema are documented in `data_infrastructure.md`. The schema is
"data, not code": a typed `SCHEMA` list from which the labeling form, model prompt,
required-key list, and metric are all derived. Adding a field should be a one-list edit that
propagates — **confirm this is still true** before assuming any change is cheap.

Five candidate enrichments are proposed below. Each is a *hypothesis* that the information is
(a) present in the source minutes often enough to matter, (b) extractable reliably enough to
be worth a structured field, and (c) hard enough to reconstruct from `project_descr`
after-the-fact that it earns a place in the labeling workflow now. **Your job is to test each
hypothesis against the actual corpus and report back, not to assume any of them hold.**

---

## The decision rule (apply to every candidate)

A candidate field should be **adopted** only if all of the following hold; otherwise
**reject** (not worth it) or **defer** (worth it, but blocked on something — say what):

1. **Presence.** The information appears in a non-trivial share of relevant items. Measure
   it: sample across eras (HTML 1998–2014 and PDF 2015–present separately, since formats
   differ) and report the share of items where the value is actually stated in the source
   text. If it shows up in only a handful of items, reject or note it as a rare-item field.
2. **Extractability / markability.** A human reading the raw block can identify and record
   the value in seconds (this is a correction-not-typing workflow). If the value is
   ambiguous, requires external lookup, or is buried in a way that makes labeling slow and
   error-prone, that counts against it.
3. **Non-recoverability.** The value is *not* trivially derivable later from fields already
   captured (especially `project_descr`, which is full text and acts as the safety net). If
   it is cleanly recoverable post-hoc, prefer a derived/computed field over a human-labeled
   one — do **not** add labeling surface for something a script can compute.
4. **Model relevance.** It serves a specific use in the analysis (the two-margin
   by-right-vs-discretionary model, the strategic-interaction tests, or identification).
   Each candidate states its intended use below; verify the field would actually support it.

**Bias toward a lean schema.** Every human-fill field adds correction surface and a chance
for drift. When in doubt, reject, or capture as a *derived* field computed in
`coerce_record()` rather than labeled by hand. Prefer fewer, higher-traction fields.

---

## Candidates to investigate (in rough priority order)

### 1. `stories_proposed` (and possibly `height_proposed_ft`)
- **Why proposed:** The schema captures the *district's* height/bulk limit
  (`height_and_bulk_district`) but not the *project's* proposed height/stories. The
  two-margin model turns on how far a project reaches *above* its by-right envelope, and for
  the conditional-use / DR items that dominate the contested calendar, the fight is often
  about height and mass, not unit count (which `units_proposed` already covers and which is
  frequently zero or N/A for these items).
- **Intended use:** Measures the project's position relative to the envelope — the core
  quantity in §3 of the model.
- **Test:** In a cross-era sample, how often is a proposed story count or height stated in
  the request text (e.g. "four-story," "construct a new ... building")? Is it stated in a
  form a labeler can grab quickly? Distinguish residential new-construction items (likely
  present) from use/process items like medical-cannabis CU or condo conversions (likely
  absent). Report the presence rate **conditional on the item being a construction project**,
  not over all items.

### 2. `discretion_trigger`
- **Why proposed:** `request_type` says an item went to a hearing but not *why* — was it a
  use not principally permitted, a height/bulk exceedance, a density bonus, a setback/rear-yard
  variance, a special-district trigger, FAR, parking? This is the bridge variable between the
  two data margins (the map and the minutes): it identifies *which* envelope dimension is
  binding.
- **Intended use:** Lets the analysis measure which by-right margin discretion is biting on,
  not merely that some margin was exceeded.
- **Test:** The trigger is often stated as code-section citations in the request text (e.g.
  "pursuant to Section 303 ... to exceed the height limit"). Investigate: (a) how reliably
  the *reason* (not just the section number) is recoverable; (b) whether a clean enum exists
  or whether items routinely cite multiple triggers (in which case consider a list field);
  (c) whether mapping code sections → trigger categories is stable enough to encode. If the
  reason is usually only inferable through a code-section lookup table, assess whether that
  table is practical to build and maintain. If triggers are too heterogeneous to enum
  cleanly, recommend deferring to a post-hoc NLP pass over `project_descr` instead.

### 3. `units_affordable` (or `inclusionary_pct`)
- **Why proposed:** Inclusionary / below-market-rate units and density-bonus deals are the
  "give-to-get" exaction margin — a distinct policy object from total units, currently buried
  in free-text `modifications` if captured at all.
- **Intended use:** The concession/exaction component of the discretionary tax.
- **Test:** How often do items state an affordable-unit count or inclusionary percentage in a
  structured, grabbable way? Is it in the request text, the conditions, or only in an
  attached staff report not present in the minutes? If the latter, this likely **defers** —
  note the dependency on staff reports. Also check whether it is concentrated in specific
  item types (large-project authorizations, downtown projects) rather than spread across the
  corpus.

### 4. `staff_planner`
- **Why proposed:** The assigned planner's name appears in the raw block (e.g.
  "(J. Purvis: (415) 558-6354)"). Planner fixed effects could be an identification lever.
- **Intended use:** Controls / fixed effects in the disposition analysis.
- **Test:** This one is likely cheap (the name is already in front of the labeler), so the
  real questions are: (a) is it present consistently across eras? (b) is the same planner
  named consistently enough (spelling, initials vs. full name) to resolve to a stable
  identifier, or will it need entity-resolution? If consistent and present, this is a strong
  adopt; if names are too noisy to resolve, it may not be worth the labeling surface.

### 5. `appeal_status` / final-disposition linkage
- **Why proposed:** SF process is layered — a commission disposition can be appealed to the
  Board of Supervisors and is not always final. If the analysis cares about *final*
  regulatory outcomes, the commission vote alone may not be it.
- **Intended use:** Distinguishing commission outcomes from final outcomes.
- **Test:** Determine whether appeal status is even *present* in the planning-commission
  minutes, or whether it lives only in a separate record series (Board of Supervisors
  minutes). If it requires a different corpus, this is almost certainly **out of scope for a
  minutes-only build** — recommend deferring and document why, rather than adding an
  unfillable field.

---

## Two cross-cutting checks (do these regardless of the candidates)

### A. Derive-don't-label audit
Before adding anything, confirm which *already-discussed* derived quantities should be
**computed** rather than labeled, so they are not mistakenly added as human-fill fields
during the relabel:
- `vote` derivable from `len(ayes) - len(noes)` (flag mismatch with any stated tally);
- staff-override indicator from `preliminary_recommendation` vs. `action` (requires
  normalizing both onto a common enum — assess feasibility of that normalization);
- continuance count / first-to-final-disposition span from joining records on `case_number`
  across meetings (requires a case-level table the item-level schema does not currently
  express — assess whether to add one).

Recommend which of these to implement as coercion-time or post-processing derivations. The
relabel should **not** create hand-labeled fields for anything in this list.

### B. Multi-jurisdiction forward-compatibility
The relabel optimizes the schema for SF. The expensive mistake would be optimizing for SF
*only* and then discovering, while labeling a second Bay Area locality, that it records
something SF does not (or names the same thing differently). Before finalizing:
- Inspect one or two other Bay Area jurisdictions' minute formats if any samples are
  available, or reason from what is knowable about their processes.
- Identify any field whose **enum vocabulary** (`request_type`, `action`) will need a
  per-jurisdiction synonym/mapping layer — e.g. a locality that has no discretionary-review
  mechanism at all, which is itself an informative datum about its by-right/discretionary
  split.
- Recommend whether the schema should adopt the *union* of fields across jurisdictions now,
  versus a per-jurisdiction extension layer later. Do not over-build for hypothetical
  localities, but flag any near-certain additions.

---

## Deliverable

Produce a short written recommendation (a markdown report, not code changes) containing:

1. For each of the five candidates: **ADOPT / DEFER / REJECT**, with the measured presence
   rate (by era), an extractability assessment, and the reasoning. Defer/reject must say what
   would change the decision.
2. For cross-cutting check A: a list of fields to implement as **derived** (with where —
   coercion vs. post-processing) and confirmation that none should be hand-labeled.
3. For cross-cutting check B: any schema changes needed now for multi-jurisdiction
   forward-compatibility, or an explicit statement that none are needed yet and why.
4. If — and only if — a candidate is ADOPT, the exact one-list edit to `SCHEMA` it implies
   (field name, type, enum choices if any, form section), so the change can be reviewed
   before the relabel begins.

**Only recommend adopting fields that the investigation shows will have traction.** A
disciplined report that adopts one field and rejects four, with evidence, is a better outcome
than one that adds all five. Err toward a lean schema; the cost of a field is paid on every
one of ~9,000 items, in both labeling time and drift risk.
