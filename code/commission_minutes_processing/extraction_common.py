#!/usr/bin/env python3
"""
extraction_common.py — the single source of truth for the minutes extraction
schema, prompt, and field-level scoring.

Imported by train.py, llm_extract.py, training_sample_create.py, the labeling app,
and the migration. Change a field here and it propagates everywhere: the prompt the
model sees, the keys the builder requires, the form the human labels, and the metric
all read from SCHEMA.

A field is one dict:
  name    canonical key
  type    "scalar" | "text" | "list" | "int" | "bool" | "enum"
  section UI grouping
  help    short human hint (also injected into the prompt)
  choices for "enum": allowed values ("" always allowed = unknown/NA)
"""
from __future__ import annotations
import json, re

# ───────────────────────────── enum vocabularies ─────────────────────────────
# `text_amendment` was merged into `planning_code_amendment` on 2026-09-05. They were never
# two things: SF suffixed Planning Code text amendments `T` until 2014 and `PCA` from 2015
# (909 `T` items 1998-2014, 403 `PCA` items 2015-2026, almost no overlap), and the labels
# split on exactly that date because labellers followed the suffix. Keeping both taught a
# model a calendar rule dressed up as a legal distinction, and broke any time series at 2015.
# Map/general-plan amendments keep their own values — those are genuinely different
# instruments, not different decades.
REQUEST_TYPES = [
    "conditional_use", "conditional_use_modification", "discretionary_review",
    "variance", "rezoning_map_amendment", "planning_code_amendment",
    "general_plan_amendment", "large_project_authorization",
    "downtown_project", "ceqa_environmental",
    "appeal_preliminary_negative_declaration", "historic", "coastal",
    "office_allocation", "appeal",
    # Added 2026-09-07. 490 corpus items with a case number are informational presentations:
    # the Commission is briefed and grants nothing. They were landing in `other`, where they
    # mixed with the genuinely unusual, and the ten already labelled had been given SEVEN
    # different request_types between them — a category with nowhere to go, not labeller
    # error. It matters for both measures: an informational hearing consumes agenda time
    # without producing an entitlement (intensity) and has no decision to be strict about
    # (stringency), so it has to be separable rather than pooled.
    "informational",
    "other",
]
# `action` is now the disposition FAMILY only. "Approved with conditions" / "approved as
# modified" are no longer distinct values — they are `approved` plus the orthogonal
# `conditions_imposed` / `project_modified` flags (which also apply to took_dr_and_approved
# etc.). `intent_to_*` capture the SF pattern of voting an intent, then adopting final
# language weeks later.
ACTIONS = [
    "approved", "disapproved", "continued", "continued_indefinitely", "withdrawn",
    "did_not_take_dr", "took_dr", "took_dr_and_approved",
    "intent_to_approve", "intent_to_disapprove", "filed", "no_action", "motion_failed",
    # Added 2026-09-07. PRELIM_REC_CATS carries `adopt`, `certify_eir` and `uphold_neg_dec`
    # but ACTIONS had no counterpart, so on those items "was the staff recommendation
    # followed?" could not be a direct enum comparison — the whole reason the two lists
    # mirror each other. The gap is not small: 457 blocks open their ACTION line with
    # "Adopted", 67 with "Certified", 40 with "Upheld".
    # ...and `initiated`: 61 blocks record "ACTION: Initiated", the Commission starting a
    # Planning Code or Map amendment and setting a hearing date. It is not an approval — the
    # substantive decision comes later — so folding it into `approved` would count one
    # amendment twice and date it wrongly.
    "adopted", "certified", "upheld", "initiated",
    "other",
]
# `motion_failed` is not `disapproved`. A failed motion means the Commission could not
# assemble a majority for ANY disposition — "Motion to Approve with Conditions FAILED,
# there was no alternate motion proposed" — so the request is neither granted nor denied
# and usually returns. 71 items in the corpus say it outright.
# Staff preliminary-recommendation categories — mirror the action disposition families so
# "was the staff rec followed?" is a direct enum comparison (no string wrangling), plus the
# staff/CEQA-specific recs (uphold neg-dec, certify EIR, pending). The verbatim wording is
# kept separately in `preliminary_recommendation`.
PRELIM_REC_CATS = [
    "approve", "adopt", "disapprove", "did_not_take_dr", "took_dr", "took_dr_and_approved",
    "uphold_neg_dec", "certify_eir", "pending", "no_action", "other",
]
# The pairs that make "did the Commission follow staff?" a direct comparison. Kept explicit
# rather than inferred from the names, because three of them do not match by string.
REC_TO_ACTION = {
    "approve": "approved", "adopt": "adopted", "disapprove": "disapproved",
    "did_not_take_dr": "did_not_take_dr", "took_dr": "took_dr",
    "took_dr_and_approved": "took_dr_and_approved",
    "uphold_neg_dec": "upheld", "certify_eir": "certified",
    "no_action": "no_action",
}
PRELIM_REC_CATS.append("initiate")
REC_TO_ACTION["initiate"] = "initiated"
# `adopt` covers the legislative/policy recommendations ("Adoption of Policy modifications",
# "Adopt CEQA findings") that `approve` does not fit: the Commission adopts its own
# resolutions and findings rather than granting somebody an entitlement.

# ───────────────────────────────── schema ─────────────────────────────────
# Bumped to 2 on 2026-09-07 (extraction_pipeline_v2_spec.md). Every extraction row and
# every gold record carries the version it was produced under, because v1 and v2 records
# are not comparable field-for-field: `speakers` changed shape, the speaker counts became
# derived rather than extracted, and `resolution_or_motion_no` split in two.
SCHEMA_VERSION = 2

# Record attribute, not an extracted field: which document format the item came from.
# Required for era-stratified splitting, era-matched few-shot retrieval, and the
# era-conditional prompt anatomy.
ERAS = ("html_1998_2014", "pdf_2015_2026")


def era_of(year: int) -> str:
    return "html_1998_2014" if int(year) <= 2014 else "pdf_2015_2026"


SCHEMA = [
    # — identity & panel keys —
    # jurisdiction (always San Francisco), supervisorial_district (recoverable from the
    # address), and item (agenda number) were dropped 2026-08: not worth labeling — derive
    # jurisdiction/district post-hoc, and the agenda number carries no analytic signal.
    #
    # meeting_date was dropped 2026-08-30 for a different reason: it is not a property of
    # the item at all, it is a property of the MEETING the item was heard at. A block does
    # not state its own hearing date — the meeting header above it does — so asking a
    # labeler to type it is asking them to copy the section header, and getting it from the
    # item's own text is what produced the 1,890 wrong dates the date stage later repaired.
    # It is now assigned by `assign_meeting_dates.py` from the item's position in its source
    # document, and attached at export time from items.meeting_date. Do not re-add it here.
    {"name": "case_number", "type": "scalar", "section": "Identity",
     "help": "Planning case number, from the item/case line at the top of the block, e.g. "
             "98.226D or 2022-001764CUA. Include the whole suffix even when the archive "
             "breaks it with spaces (\"2005.0148DD V\" is one case number). Not the "
             "building-permit application number, which is a different and longer id"},
    {"name": "request_type", "type": "enum", "section": "Identity", "choices": REQUEST_TYPES,
     "help": "The type of request. Determined by the \"Request for...\" sentence in the "
             "block. The case-number suffix (C, V, DR, E, ET, ...) is a weak hint and is "
             "frequently wrong: an E case may be an appeal of a preliminary negative "
             "declaration rather than generic CEQA review, and an ET case is a Planning "
             "Code amendment, not a CEQA determination. Where the suffix and the text "
             "disagree, follow the text.\n"
             "  A STACKED suffix describes a GROUP, not this item. One case number can "
             "carry every action the project needs — 2013.1005EXVAR is E (environmental) "
             "plus X (Section 309 downtown authorization) plus VAR (variance), and the "
             "agenda splits it across 14a, 14b, 14c. Each sub-item is one request. Read "
             "THIS block's own \"Request for...\" sentence: 14b says \"Request for "
             "Variance from exposure (Planning Code Section 140) requirements\", so 14b is "
             "a variance, whatever else the shared case number mentions. The same applies "
             "to CUAVAR, DRPVAR, PCAMAP, DNXCUA and the older two-letter stacks (CV, DV, "
             "TZ, DD).\n"
             "  Use `informational` when the item is a BRIEFING rather than a request: the "
             "block says \"Informational Presentation\", \"Informational Item\" or "
             "\"Informational Hearing\", and its recommendation and action read \"None - "
             "Informational\". Nothing is granted or denied. Take this from the item's own "
             "text, not from the case-number suffix, which still names whatever entitlement "
             "the project will eventually need"},
    # Split from the single `resolution_or_motion_no` string on 2026-09-07. A motion and a
    # resolution are different instruments; keeping them in one string made the distinction
    # a comparison rule rather than a value, and the gold set was wrong about it on three
    # items (447/448/451) precisely because nothing in the schema forced the question.
    {"name": "action_instrument", "type": "enum", "section": "Identity",
     # "dra" was added 2026-09-07 from the gold set: 11 discretionary-review items record
     # a "DRA#: 0013" (Discretionary Review Action), which is neither a motion nor a
     # resolution. Forcing them into the two-value enum would have blanked a real value.
     "choices": ["motion", "resolution", "dra"],
     "help": "Which instrument recorded the Commission's action: a Motion, a Resolution, or "
             "on discretionary-review items a DRA (Discretionary Review Action). Read the "
             "word the block actually uses — these are different instruments and are not "
             "interchangeable. Found on the MOTION:/RESOLUTION:/DRA line in the closing "
             "block. \"\" if the block records no numbered instrument"},
    {"name": "action_instrument_no", "type": "int", "section": "Identity",
     "help": "The number of the Motion, Resolution or DRA, digits only, leading zeros "
             "dropped (\"DRA#: 0013\" gives 13). 0 if none. If the block lists several "
             "(e.g. for multiple related cases), give the one belonging to this item"},

    # — location (geocoding / merge keys) —
    # VALIDATION ONLY (decided 2026-09-07). The parcel join keys on (assessor_block,
    # lot_number) — which score 99-100% — and `link_permits.py` has always joined that way.
    # The address is kept to check the parcel linkage and to display, not as a merge key,
    # and the geocoder is off the critical path. `format_ok` is a soft warning, not a gate.
    {"name": "project_address", "type": "scalar", "section": "Location",
     "validation_only": True,
     "help": "The street address only, in the form '<number> <Street Name>', from the "
             "address line near the top of the block. Keep a range if the block gives one "
             "('1233-1237 Howard Street'). Do NOT include the cross-street or locational "
             "gloss that often follows it ('east side between 8th and 9th Streets') — stop "
             "at the street name. Do not include the city, state, or ZIP"},
    {"name": "assessor_block", "type": "scalar", "section": "Location",
     "help": "Assessor's block number, from the \"in Assessor's Block NNNN\" phrase on the "
             "address line. Digits, sometimes with a trailing letter (2888A). Not the lot"},
    # `type: "list"`, corrected 2026-09-07. It was declared scalar while `normalize.lot_list`
    # stored it as a list, so `coerce_record` stringified the list to "[8]" and the next
    # normalisation could not parse it back — the value was dropped. 122 of 232 gold records
    # lost their lot number this way, silently, on the save that touched some other field.
    {"name": "lot_number", "type": "list", "section": "Location",
     "help": "Lot number(s), from the \"Lot NNN\" phrase on the address line. If the block "
             "names several lots, give all of them separated by commas. Leading zeros as "
             "printed are fine. Not the block"},

    # — zoning & project scale (density) —
    # Extraction COPIES, it does not translate. A model that knows the North Beach NCD is
    # NC-3 will write "NC-3" where the block says "North Beach Neighborhood Commercial
    # District" — correct, and still wrong here, because it makes the field a mixture of what
    # the minutes said and what the model knew, which cannot be audited against the source.
    # Mapping district names to codes is a downstream step on text we already have.
    {"name": "type_district", "type": "scalar", "section": "Zoning & scale",
     "help": "The use/zoning district as the block writes it, from the zoning line "
             "(\"...is located in an NC-3 District and a 65-X Height and Bulk District\"). "
             "Copy it EXACTLY as printed: if the block gives a code, give the code (RH-2, "
             "NC-3, UMU, C-3-O); if it spells the district out (\"North Beach Neighborhood "
             "Commercial District\"), give those words. Never convert one into the other — "
             "do not replace a spelled-out district with the code you know it maps to, even "
             "when the mapping is correct. If the item spans more than one use district "
             "(a lot on a boundary, or a merged site), give them all, comma-separated in "
             "the order the block prints them — \"RH-2, P\". If the block has no zoning "
             "line, leave this blank — do not infer a district from the address, the "
             "neighbourhood, or the kind of project"},
    {"name": "type_district_descr", "type": "scalar", "section": "Zoning & scale",
     "help": "The plain-English district name the zoning line gives in parentheses after "
             "the code, e.g. \"Residential, House, Two-Family\" for RH-2. Copy it as "
             "printed. Where type_district lists several districts, list their names in the "
             "SAME ORDER separated by semicolons, so the two fields line up positionally "
             "(\"RH-2, P\" pairs with \"Residential, House, Two-Family; Public\") — the "
             "names themselves contain commas, which is why the separator differs. Blank if "
             "the zoning line gives no parenthetical, and blank whenever type_district is "
             "blank"},
    {"name": "height_and_bulk_district", "type": "scalar", "section": "Zoning & scale",
     "help": "Height and bulk district, e.g. 40-X, 50-N, 200-R2 — from the zoning line, "
             "where it follows the use district. Blank if the block does not name one"},
    {"name": "special_use_district", "type": "scalar", "section": "Zoning & scale",
     "help": "A named Special Use District from the zoning line, e.g. \"Van Ness and Market "
             "Residential Special Use District\". Most items are in none: blank is the "
             "normal answer. Do not put the use district or the height district here"},
    # Widened 2026-09-07 after the first labelling session. The target was "the Request-for
    # clause through the FIRST SENTENCE", and that sentence routinely names the request type
    # and the zoning district and then stops before the project itself — "Request for
    # temporary two year Conditional Use authorization for a public commercial surface
    # parking lot in a C-3-O(SD) District." drops "The proposal is to construct a temporary
    # expansion to an existing parking lot on the subject vacant site." The whole description
    # is now the target; structuring it (splitting off the district, the scale, the works)
    # happens downstream, on text we already have, rather than by a boundary rule guessing
    # in advance what will matter.
    {"name": "project_descr", "type": "text", "section": "Zoning & scale",
     "help": "The full description of what is being asked of the Commission, VERBATIM from "
             "the block. Start at the phrase that opens the request — \"Request for...\", "
             "\"Consideration of...\", \"Appeal of...\", \"Public hearing on...\" — and "
             "continue to the end of the descriptive text, which is where the closing block "
             "begins (SPEAKERS / ACTION / AYES / MOTION / Preliminary Recommendation / "
             "NOTE). Include every sentence of the description, not just the first: the "
             "sentence naming the request type is often followed by the sentences that say "
             "what the project actually is. Do not paraphrase, do not summarise, do not "
             "stop early. If the block has no such opening phrase, give the descriptive "
             "text as it stands"},

    # The "initial plus surname" instruction was a prompt-induced blank generator: the
    # 1998-2001 minutes print a BARE SURNAME — "(WASHINGTON)", "(LI)", "(MONTAÑA)" — and a
    # model told the answer is an initial plus a surname finds no initial and returns "".
    # 27 of 33 staff_planner disagreements in the first adjudication batch were exactly this,
    # on headers that name the planner perfectly clearly. Both forms are now spelled out.
    {"name": "staff_planner", "type": "scalar", "section": "Process",
     "help": "The assigned planner, from the parenthesis after the case number at the top "
             "of the block. Two formats appear and BOTH are valid answers: a bare surname, "
             "\"(WASHINGTON)\" gives 'Washington'; or an initial with a surname and a phone "
             "number, \"(D. WINSLOW: (415) 558-6335)\" gives 'D. Winslow'. Copy the name as "
             "printed and drop the phone number. A surname on its own is a complete answer "
             "— never leave this blank because there is no initial. Not a commissioner, "
             "not a speaker"},
    {"name": "preliminary_recommendation", "type": "scalar", "section": "Process",
     "help": "The staff recommendation VERBATIM, from the \"Preliminary Recommendation:\" "
             "line. Copy the wording as printed, e.g. 'Approve with Conditions', "
             "'Do not take Discretionary Review and approve'. This is what staff asked for, "
             "not what the Commission did — that is `action`"},
    {"name": "preliminary_recommendation_category", "type": "enum", "section": "Process",
     "choices": PRELIM_REC_CATS,
     "help": "The same staff recommendation bucketed into one disposition family, so it "
             "can be compared with `action` to measure how often the Commission followed "
             "staff. Derive it from the verbatim text above, not from the block at large"},
    {"name": "continued_to", "type": "scalar", "section": "Process",
     "help": "If action='continued': the target date, TAKEN FROM THE ACTION LINE. Copy it "
             "in whatever form it is printed (\"April 21, 2005\" or \"3/12/98\"); it is "
             "converted to ISO after extraction. A block often ALSO carries \"(Proposed for "
             "Continuance to ...)\" higher up — that is the agenda's proposal, not the "
             "Commission's decision, and the two differ on 15% of the blocks that print "
             "both. Where they disagree the ACTION line wins; ignore the parenthetical. "
             "Leave BLANK for action='continued_indefinitely' — there is no date, and "
             "`action` already says so; do not write 'indefinite' here"},
    {"name": "action", "type": "enum", "section": "Process", "choices": ACTIONS,
     "help": "The Commission's disposition, from the ACTION: line in the closing block. On "
             "a discretionary-review case \"Approved as proposed\" means the Commission "
             "declined to take DR (did_not_take_dr), not a plain approval. \"Continued to "
             "the call of the Chair\" is continued_indefinitely. \"None – Informational\" "
             "is no_action — a briefing decides nothing. A motion that failed with "
             "nothing carrying is motion_failed, not disapproved"},

    # — politics: mobilization & votes —
    # One structured list replaces a name list plus three separately-extracted counts. The
    # counts were the worst-scoring politics fields and were free to contradict `speakers`;
    # counting is deterministic and belongs in Python, not in a language model.
    # Stance is READ OFF A MARKER, never inferred from what the speaker said. The minutes
    # carry an explicit vocabulary and the corpus is large enough to trust it: "(+)"/"(-)"/
    # "(=)"/"(+/-)" in 1,444 items (2000-2009), and bare "+"/"-"/"=" line prefixes in 4,317
    # (2000-2026). Reading a speaker's remarks and deciding they sounded opposed is exactly
    # the kind of judgement that makes a label unauditable — and it is wrong often enough to
    # matter, since a supporter can spend their two minutes listing concerns.
    # Stance is anchored to THE REQUEST BEFORE THE COMMISSION, not to the Commission's
    # action. Anchoring to the action is circular: the same person saying the same words
    # would be `support` when the Commission takes DR and `oppose` when it does not, so the
    # label is determined by the outcome and can no longer be used to study outcomes. The
    # request is fixed before anyone votes, is readable off the item itself, and does not
    # invert when an item is continued.
    #
    # `stance_basis` separates a stance READ OFF A MARKER from one INFERRED from what the
    # speaker said. They are different kinds of evidence and pooling them means the reliable
    # half can never be isolated again; with the flag, "markers only" is a one-line filter.
    {"name": "speakers", "type": "list_of_objects", "section": "Politics",
     "item_schema": {"name": "str", "stance": "enum", "stance_basis": "enum"},
     "item_choices": {"stance": ["support", "oppose", "neutral"],
                      "stance_basis": ["marker", "inferred"]},
     "help": "Every member of the public recorded as speaking on this item, in the order "
             "the block lists them, from the SPEAKERS line of the closing block.\n"
             "  NAME: the person's name only. Drop what follows a dash or comma — "
             "\"Raymond Holland – Planning Association for the Richmond\" gives 'Raymond "
             "Holland' — and drop the topic of their remarks. KEEP a \"(M)\" or \"(F)\" "
             "prefix: the minutes use it for a speaker whose name was not caught, and it "
             "records their gender. \"(M) Speaker\" stays \"(M) Speaker\".\n"
             "  STANCE — LOOK FOR A MARKER FIRST. \"(+)\" or a leading \"+\" is support; "
             "\"(-)\" or a leading \"-\" is oppose; \"(=)\" or \"(+/-)\" or a leading "
             "\"=\" is neutral. A heading governs the list beneath it: under \"In "
             "support:\" every name is support until the next heading. Careful with "
             "\"-\": at the START of a speaker's line it means opposed, but the same "
             "character bullets the separate points one speaker made, and those are not "
             "speakers. When you take the stance from a marker or heading, set "
             "stance_basis='marker'.\n"
             "  ONLY IF THERE IS NO MARKER, infer the stance from what the block records the "
             "speaker as saying, and set stance_basis='inferred'. If the block gives you "
             "nothing to go on, leave BOTH stance and stance_basis blank — that is a real "
             "answer, not a failure. A heading that merges two positions (\"In opposition "
             "or neutral:\") settles neither, so those names get a blank stance.\n"
             "  WHAT SUPPORT AND OPPOSE ARE RELATIVE TO: the REQUEST this item puts before "
             "the Commission — never the Commission's eventual action, and never the "
             "building in the abstract. On a discretionary review the request is to TAKE "
             "discretionary review, so the DR requestor is `support` (even though they "
             "oppose the project) and the project sponsor is `oppose`. On a conditional use "
             "the request is to grant the authorization, so a neighbour objecting to the "
             "project is `oppose`. A speaker who addresses only the procedure — asking for "
             "a continuance, say — is `neutral` on the request.\n"
             "  If the block records only a count and no names (e.g. '3 speakers in "
             "support'), emit that many entries with an empty name, the stated stance and "
             "stance_basis='marker'. If it says the speakers were the same as another "
             "item's (\"Same as those listed for item 22\"), leave this EMPTY — that "
             "sentence is a cross-reference, not a person, and resolving it is a later step"},

    {"name": "support_count", "type": "int", "section": "Politics", "derived": True,
     "help": "# speakers in support (derived from `speakers`)"},
    {"name": "oppose_count", "type": "int", "section": "Politics", "derived": True,
     "help": "# speakers in opposition (derived from `speakers`)"},
    {"name": "neutral_count", "type": "int", "section": "Politics", "derived": True,
     "unmeasurable": True,
     "help": "# neutral speakers (derived from `speakers`)"},
    {"name": "ayes", "type": "list", "section": "Politics",
     "help": "Commissioners voting AYE, from the AYES line of the closing block. Surnames "
             "only. These are commissioners, never members of the public"},
    {"name": "noes", "type": "list", "section": "Politics",
     "help": "Commissioners voting NO, from the NOES (or NAYES) line. \"None\" means an "
             "empty list, not a commissioner called None"},
    {"name": "absent", "type": "list", "section": "Politics",
     "help": "Commissioners absent for the vote, from the ABSENT line. \"None\" means an "
             "empty list. Do not put excused or recused commissioners here"},
    # No gold values at all / exactly one: their scores are noise, so the report suppresses
    # them rather than printing a number that cannot mean anything.
    {"name": "recused", "type": "list", "section": "Politics", "unmeasurable": True,
     "help": "Commissioners recused (conflict of interest), from the RECUSED line"},
    {"name": "excused", "type": "list", "section": "Politics", "unmeasurable": True,
     "help": "Commissioners excused, from the EXCUSED line"},
    # `vote` is no longer a labeled field (dropped 2026-07): the tally is fully recoverable
    # from the ayes/noes lists, and hand-entering it only produced stale mismatches. Derive
    # it on demand with derive_vote() in the analysis layer.

    # — conditions —
    {"name": "conditions_imposed", "type": "enum", "section": "Conditions", "choices": ["yes", "no"],
     "help": "Did this action carry conditions of approval? 'yes' when the ACTION line "
             "says so (\"Approved with Conditions\") or when conditions are enumerated "
             "below it. Independent of `action`: a took_dr_and_approved can carry "
             "conditions too. Conditions described BEFORE the action belong to a prior "
             "entitlement and do not count"},
    {"name": "project_modified", "type": "enum", "section": "Conditions", "choices": ["yes", "no"],
     "help": "Were the project's plans themselves changed as part of this action — "
             "\"approved as modified\", \"with the modification that...\"? 'yes' only when "
             "the design or scope changed, not merely when conditions were attached"},
    {"name": "modifications", "type": "text", "section": "Conditions",
     "help": "The conditions or modifications the Commission imposed, copied from the text "
             "after the ACTION line. Copy the substance; bullet characters and numbering "
             "may be dropped. Blank when the action carried none"},
]

# ───────────────────── derived lookups (used everywhere) ─────────────────────
FIELDS       = [f["name"] for f in SCHEMA]
FIELD_BY_NAME = {f["name"]: f for f in SCHEMA}
SECTIONS     = list(dict.fromkeys(f["section"] for f in SCHEMA))
LIST_FIELDS  = {f["name"] for f in SCHEMA if f["type"] == "list"}
OBJLIST_FIELDS = {f["name"] for f in SCHEMA if f["type"] == "list_of_objects"}
INT_FIELDS   = {f["name"] for f in SCHEMA if f["type"] == "int"}
TEXT_FIELDS  = {f["name"] for f in SCHEMA if f["type"] == "text"}
ENUM_FIELDS  = {f["name"]: f["choices"] for f in SCHEMA if f["type"] == "enum"}
DEFAULTS     = {f["name"]: f.get("default", "") for f in SCHEMA}

# A derived field is computed in normalize.py from another extracted field. It is never
# asked of the model and never scored as an extraction field, but it stays a column of the
# output table. Fields marked unmeasurable have too few gold values for a score to mean
# anything; the report prints n and a dash instead of a number.
DERIVED_FIELDS      = {f["name"] for f in SCHEMA if f.get("derived")}
UNMEASURABLE_FIELDS = {f["name"] for f in SCHEMA if f.get("unmeasurable")}
EXTRACTED_FIELDS    = [f["name"] for f in SCHEMA if not f.get("derived")]

# The fields the regulation-game layer actually consumes. Grounded in the toy model's own
# enumeration of the discretionary tax: "process exposure (via request_type), mobilized
# opposition (via stance-marked speaker counts), conditioning (action = approved-with-
# conditions), delay (continuances, via a case-level join), and rare denial". `case_number`
# and `continued_to` are here because the delay component needs the case-level join and the
# target date; assessor_block/lot_number because the parcel join to construction keys on
# them. PROVISIONAL: no estimation code consumes these yet (see the note in the v2 spec
# report), so this list is derived from the theory memo rather than from working code.
VALIDATION_ONLY_FIELDS = {f["name"] for f in SCHEMA if f.get("validation_only")}

ESTIMATION_CORE = [
    "case_number", "request_type",
    "assessor_block", "lot_number",      # the parcel join; project_address is validation only
    "action", "continued_to",
    "conditions_imposed", "project_modified",
    "speakers",                       # and support/oppose/neutral_count derived from it
    "ayes", "noes", "absent",
    "preliminary_recommendation",
]


def derive_speaker_counts(speakers) -> dict:
    """The three stance counts, computed from `speakers`. Never taken from model output."""
    rows = speakers if isinstance(speakers, list) else []
    return {f"{stance}_count":
            sum(1 for sp in rows if isinstance(sp, dict) and sp.get("stance") == stance)
            for stance in ("support", "oppose", "neutral")}

EOJ_TOKEN = "<extra_id_0>"

# ───────────────────────────────── the prompt ─────────────────────────────────
# Generated from SCHEMA so it can never drift from FIELDS. Assembled in two parts:
#
#   build_prompt()      the CACHEABLE PREFIX — role, blank policy, block anatomy, field
#                       list, and (optionally) a fixed example set. Byte-identical across
#                       every request in a stratum, which is what makes prompt caching work.
#   item_suffix(block)  the PER-ITEM part — the block itself and a short closing reminder.
#
# The prefix has to come first for caching, which puts the instructions far from the block.
# `item_suffix` counters that with a recency reminder restating only the three rules the
# error analysis says are actually being broken. It is not a second copy of the
# instructions.
BLANK_POLICY = """RULES ON BLANK FIELDS — read these before extracting:

1. A blank is a real answer, not a failure. It means "the block does not say". Leave the
   field as "" (or [] for lists, 0 for counts) whenever the text is silent.
2. Extract only what the block states. Do NOT infer a value from the case number, do NOT
   fill a field from general knowledge of San Francisco planning practice, and do NOT carry
   a value over from a similar item you have seen.
3. Before writing any non-empty value, check that you could point to the words in this
   block that support it. If you cannot, the field is blank.
4. Leaving five fields blank is better than guessing one. A blank is a question a human can
   answer later; a confident wrong value is a silent error nobody will catch.
"""

# The single highest-value prompt addition. Most residual error is LOCATIONAL — a model
# asked for a zoning district that does not know districts live on one line will supply one
# from the shape of the text. Naming the anatomy tells it where to look and, just as
# importantly, when to stop looking.
BLOCK_ANATOMY = """The text is one item from the calendar of a San Francisco Planning
Commission hearing. A typical item runs in this order:

  1. Item / case line — the case number (e.g. 2004.0392C), sometimes with
     a continuation note.
  2. Address line — street address, often followed by a locational gloss
     ("east side between 8th and 9th Streets"), then Assessor's Block and
     Lot.
  3. Zoning line — e.g. "...is located in an NC-3 District and a 65-X
     Height and Bulk District." Zoning districts appear ONLY on this line.
     If the item has no such line, type_district and
     type_district_descr are blank.
  4. "Request for..." sentence — this, and not the case number, determines
     request_type.
  5. Preliminary Recommendation: <verb>.
  6. Staff planner, often on its own line.
  7. A closing block: SPEAKERS, then ACTION, then AYES / NOES / ABSENT.

Not every item has every part. A missing part means a blank field, not a
field to be inferred from a neighbouring part.
"""

CLOSING_REMINDER = """Reminder: extract only what this block states. Zoning districts come from
the zoning line only. request_type comes from the "Request for..."
sentence, not the case number. A blank is a valid answer."""

ROLE_LINE = ("You are a strict JSON extractor. Given one item from a San Francisco Planning "
             "Commission hearing calendar, extract and return exactly one valid "
             "double-quoted JSON object with the keys below (any order). Only output the "
             "JSON—no commentary.")


def _field_line(f: dict) -> str:
    hint = f["help"]
    t = f["type"]
    if t == "enum":
        hint += " — one of: " + ", ".join(f["choices"]) + ', or ""'
    elif t == "list":
        hint += " — list of strings"
    elif t == "list_of_objects":
        stances = ", ".join((f.get("item_choices") or {}).get("stance", []))
        hint += (' — list of objects, each {"name": string, "stance": string}; '
                 f'stance is one of: {stances}, or ""')
    elif t == "int":
        hint += " — integer"
    return f"- {f['name']}: {hint}"


def build_prompt(era: str | None = None, examples: str = "") -> str:
    """The cacheable prefix. `era` reserved for the era-conditional anatomy (§3.4): the
    PDF-era layout has not been inspected yet, so both eras currently get the same text and
    the parameter exists so switching later is a one-line change rather than a refactor."""
    parts = [ROLE_LINE, "", BLANK_POLICY, "", BLOCK_ANATOMY, "", "Required keys:"]
    parts += [_field_line(f) for f in SCHEMA if not f.get("derived")]
    if examples:
        parts += ["", examples.rstrip()]
    return "\n".join(parts) + "\n"


def item_suffix(block: str) -> str:
    """The per-item part: the block, then the recency reminder."""
    return "\nRaw block:\n\n" + block.rstrip() + "\n\n" + CLOSING_REMINDER + "\n"


def prompt_sha(prefix: str) -> str:
    """SHA-256 of the assembled cacheable prefix, stored on every extracted row so prompt
    drift between runs is detectable rather than inferred."""
    import hashlib
    return hashlib.sha256(prefix.encode("utf-8")).hexdigest()


# Back-compatible name. Callers that just want "the instruction text" still work; the
# bakeoff harness composes build_prompt() + item_suffix() explicitly.
PROMPT_INSTRUCTION = build_prompt()


# ────────────────────── evidence spans and verification ──────────────────────
# Structured outputs already guarantee a parseable, complete record. What they cannot tell
# you is whether a value was READ or INVENTED. On the six fields where over-extraction was
# measured highest, the model returns {"value": ..., "evidence": ...} and the evidence must
# be a verbatim substring of the block.
#
# This is the change that converts over-extraction from a rate measured once on 114 gold
# items into an error detectable on all 16,191. Failures are FLAGGED, never auto-nulled:
# the point is a triage signal, and silently blanking a value the model got right from an
# oddly-worded block would be a worse error than the one being caught.
EVIDENCE_FIELDS = ("type_district", "type_district_descr", "special_use_district",
                   "conditions_imposed", "project_modified", "modifications")


def _norm_ws(t: str) -> str:
    """Collapse runs of whitespace. PDF-era text has ragged line breaks, so a containment
    check on raw text fails on correct evidence. Case and punctuation are deliberately NOT
    normalised — doing so would weaken the check to the point of uselessness."""
    return re.sub(r"[\s\xa0]+", " ", str(t or "")).strip()


def verify_evidence(field: str, payload: dict, block: str) -> str | None:
    """Return None if the evidence checks out, else a failure reason."""
    value, evidence = payload.get("value", ""), payload.get("evidence", "")
    if not value:
        return "evidence_present_on_blank" if evidence else None
    if not evidence:
        return "missing_evidence"
    if _norm_ws(evidence) not in _norm_ws(block):
        return "evidence_not_in_block"
    return None


def unwrap_evidence(rec: dict, block: str = "") -> tuple:
    """Split a raw model record into (flat record, {field: evidence}, [failures]).

    A record whose evidence fields are already flat (the regex extractor, or a v1 record)
    passes through unchanged with no evidence and no failures, so the same code path scores
    both.
    """
    flat, evidence, failures = dict(rec), {}, []
    for f in EVIDENCE_FIELDS:
        v = rec.get(f)
        if not isinstance(v, dict):
            continue
        flat[f] = v.get("value", "")
        evidence[f] = v.get("evidence", "")
        reason = verify_evidence(f, v, block) if block else None
        if reason:
            failures.append({"field": f, "reason": reason,
                             "value": v.get("value", ""), "evidence": v.get("evidence", "")})
    return flat, evidence, failures


# ───────────────────────────── record helpers ─────────────────────────────
# `demolition` is no longer an extracted/labeled field (dropped 2026-07-03): it added
# labeling burden for a signal the description already states. Derive it on demand from
# project_descr instead — e.g. in data_collect when building the analysis table.
_DEMO_RE = re.compile(r"\bdemoli(?:sh|shed|tion)\b", re.I)


def demolition_from_descr(project_descr: str) -> str:
    """'yes' if the project description mentions demolition, else 'no'."""
    return "yes" if project_descr and _DEMO_RE.search(project_descr) else "no"


def derive_vote(rec: dict) -> str:
    """Vote tally 'ayes-noes' derived from the roll-call lists (replaces the old hand-
    labeled `vote` field). Empty when there is no aye roll-call. Use in the analysis
    layer (data_collect) rather than storing a redundant, error-prone tally."""
    ayes = _to_list(rec.get("ayes"))
    noes = _to_list(rec.get("noes"))
    return f"{len(ayes)}-{len(noes)}" if ayes else ""


def empty_record() -> dict:
    """A schema-complete record with type-appropriate empties."""
    rec = {}
    for f in SCHEMA:
        if f["type"] in ("list", "list_of_objects"):
            rec[f["name"]] = []
        elif f["type"] == "int":
            rec[f["name"]] = "" if f.get("blank_ok") else 0
        else:
            rec[f["name"]] = f.get("default", "")
    return rec


# "none"/"n/a" are not names — leave roll-call & speaker lists empty when nobody is in
# that category (writing "None" as a list entry silently inflated vote tallies, e.g. a
# phantom noe turning a 7-0 into an impossible 7-1). Stripped here so it can't recur.
_NOT_A_NAME = re.compile(r"^\s*(none|n/?a)\s*$", re.I)


def _to_list(v):
    if v is None:
        return []
    items = v if isinstance(v, list) else re.split(r"[;,]", str(v))
    return [s for x in items if (s := str(x).strip()) and not _NOT_A_NAME.match(s)]


def _to_int(v, blank_ok: bool = False):
    if isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"-?\d+", str(v or ""))
    if m:
        return int(m.group(0))
    return "" if blank_ok else 0


def _to_objlist(v, fld) -> list:
    """Coerce a list-of-objects field to its declared item shape.

    Built from `item_schema` rather than hard-coded keys, so adding a key to the shape —
    `stance_basis`, say — does not silently get dropped here. Accepts the v1 shape (a list
    of bare names or a comma string) so a v1 record can still be loaded; the extra keys come
    back blank, which is honest: the v1 label did not record them.
    """
    if isinstance(v, str):
        v = [x.strip() for x in re.split(r"[;,]", v) if x.strip()]
    if not isinstance(v, list):
        return []
    keys = list(fld.get("item_schema") or {"name": "str"})
    choices = fld.get("item_choices") or {}
    def one(item):
        if not isinstance(item, dict):
            item = {"name": str(item)}
        out = {}
        for k in keys:
            val = str(item.get(k) or "").strip()
            if k in choices:
                val = val.lower()
                val = val if val in choices[k] else ""
            out[k] = val
        return out
    return [o for o in (one(x) for x in v) if any(o.values())]


def coerce_record(rec: dict) -> dict:
    """Return a schema-complete record: every field present, correctly typed,
    unknown keys dropped (after the caller has normalised typos)."""
    out = empty_record()
    for f in SCHEMA:
        n, t = f["name"], f["type"]
        if n not in rec or rec[n] is None:
            continue
        v = rec[n]
        if t == "list_of_objects":
            out[n] = _to_objlist(v, f)
        elif t == "list":
            out[n] = _to_list(v)
        elif t == "int":
            out[n] = _to_int(v, blank_ok=f.get("blank_ok", False))
        elif t == "enum":
            sv = str(v).strip().lower().replace(" ", "_")
            if sv in f["choices"]:
                out[n] = sv
            elif "other" in f["choices"] and str(v).strip():
                # enums that offer "other" accept a typed-in value; keep it verbatim
                # (the UI surfaces a free-text box whenever "other" is available)
                out[n] = str(v).strip()
            else:
                out[n] = ""
        else:
            out[n] = str(v).strip()
    return out


# ───────────────────────────── parsing / scoring ─────────────────────────────
def is_valid_json(txt: str) -> bool:
    try:
        json.loads(txt)
        return True
    except Exception:
        return False


def parse_obj(txt: str):
    if txt is None:
        return None
    txt = txt.split(EOJ_TOKEN)[0].strip()
    try:
        return json.loads(txt)
    except Exception:
        m = re.search(r"\{.*\}", txt, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
        return None


def norm_scalar(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        v = ", ".join(str(x) for x in v)
    return re.sub(r"\s+", " ", str(v).strip().lower())


def as_set(v) -> set:
    if v is None:
        return set()
    items = v if isinstance(v, list) else re.split(r"[;,]", str(v))
    return {re.sub(r"\s+", " ", str(x).strip().lower()) for x in items if str(x).strip()}


def is_empty(v) -> bool:
    return v in (None, "", [], {}, 0, "0") or (isinstance(v, str) and not v.strip())


def field_match(pred: dict, ref: dict, key: str) -> bool:
    if key in LIST_FIELDS:
        return as_set(pred.get(key)) == as_set(ref.get(key))
    return norm_scalar(pred.get(key)) == norm_scalar(ref.get(key))


# ── field-aware comparison ───────────────────────────────────────────────────
# `field_match` is exact string equality after case/space folding. It is kept, frozen,
# because earlier results were scored with it — but it is the WRONG question for most of
# this schema, and the accuracy numbers it produced were largely measuring transcription
# style. Measured on the gold set, exact match charged Haiku 4.5 six points for things like
# a trailing full stop.
#
# `compare_field` asks, per field, whether the extracted FACT is the same. The rule is
# chosen by what the field is, not by one global tolerance:
#
#   enum          exact on the vocabulary value. `approved` and `continued` are different
#                 answers and nothing should fold them together.
#   int           exact. A count is a count.
#   date          parsed and compared as a date, so "April 21, 2005" == "2005-04-21".
#   identifier    punctuation and case discarded, digits preserved. "Motion No.: 14644" ==
#                 "Motion No. 14644"; lot "009, 010" == "9,10". For a permanent action id
#                 the INSTRUMENT is kept — motion and resolution are different things.
#   names         a set of surnames. Ordering and honorifics carry no information.
#   address       the street address proper, ignoring the locational gloss the minutes
#                 append ("1233 Howard Street" == "1233 Howard Street, east side between
#                 8th and 9th"). NOTE: this measures whether the right address was found,
#                 not whether it was returned in the clean form the geocoder wants — that
#                 is a prompt/post-processing concern, tracked separately by `format_ok`.
#   free text     containment or high token overlap. Labellers abbreviate and stop early,
#                 so requiring the extractor to reproduce an abbreviation measures nothing.
FREETEXT_FIELDS = {"project_descr", "modifications", "preliminary_recommendation"}
# `speakers` left OUT of NAME_FIELDS in v2: it is no longer a name list but a list of
# {name, stance}, and comparing only the names would silently ignore the stance — the very
# thing the restructure was for.
NAME_FIELDS = {"ayes", "noes", "absent", "excused", "recused"}
IDENT_FIELDS = {"case_number", "assessor_block", "lot_number", "type_district",
                "type_district_descr", "height_and_bulk_district", "special_use_district"}
DATE_FIELDS = {"continued_to"}
OVERLAP_MIN = 0.85

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}


def _alnum(v) -> str:
    return re.sub(r"[^a-z0-9]", "", str(v or "").lower())


def _digits_set(v) -> set:
    """{'009, 010'} and {'9,10'} are the same lots."""
    return {str(int(x)) for x in re.findall(r"\d+", str(v or ""))}


def _as_date(v):
    s = str(v or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})$", s)
    if m and m.group(1).lower() in _MONTHS:
        try:
            import datetime
            return datetime.date(int(m.group(3)), _MONTHS[m.group(1).lower()],
                                 int(m.group(2))).isoformat()
        except ValueError:
            return None
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})$", s)
    if m:
        mo, day, yr = (int(x) for x in m.groups())
        yr += 1900 if 90 <= yr <= 99 else (2000 if yr < 90 else 0)
        try:
            import datetime
            return datetime.date(yr, mo, day).isoformat()
        except ValueError:
            return None
    return None


def _instrument(v) -> tuple:
    t = str(v or "").lower()
    kind = ("resolution" if "resolution" in t else "dra" if re.search(r"\bdra\b", t)
            else "motion" if "motion" in t else "")
    num = re.findall(r"\d{3,}", t)
    return (kind, num[0] if num else "")


def _surname(n) -> str:
    n = re.sub(r"\(.*?\)", "", str(n)).strip().strip(".,")
    n = re.sub(r"^(commissioner|president|vice[- ]president|mr\.?|ms\.?|dr\.?)\s+", "", n, flags=re.I)
    parts = [p for p in re.split(r"\s+", n) if p]
    return _alnum(parts[-1]) if parts else ""


def _names(v) -> set:
    items = v if isinstance(v, list) else re.split(r"[;,]", str(v or ""))
    out = {_surname(x) for x in items if str(x).strip()}
    return {x for x in out if x and x != "none"}


def _content_words(v) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", str(v or "").lower()) if len(w) > 2}


def _overlap(a, b) -> float:
    A, B = _content_words(a), _content_words(b)
    return len(A & B) / len(A | B) if A | B else 0.0


ADDR_GLOSS = re.compile(
    r",\s*(?:east|west|north|south|between|on the|at the|corner|near|adjacent)|"
    r"\s*\((?:a\.?k\.?a|aka)\b", re.I)


def _address_core(v) -> str:
    s = ADDR_GLOSS.split(str(v or "").strip())[0]
    return _alnum(re.sub(r"\b(street|avenue|boulevard|road|drive|place|way|terrace|lane)\b",
                         lambda m: m.group(0)[:2], s, flags=re.I))


def _speaker_set(v) -> set:
    """{(surname, stance)}. Anonymous entries (the count-only case) keep an empty name, so
    "3 speakers in support" compares equal to three unnamed support entries."""
    out = set()
    for i, sp in enumerate(v if isinstance(v, list) else []):
        if isinstance(sp, dict):
            nm, st = _surname(sp.get("name")), str(sp.get("stance") or "").lower()
        else:
            nm, st = _surname(sp), ""
        # unnamed speakers are distinguished by position so N of them compare as N
        out.add((nm or f"__anon{i}", st))
    return out


def compare_field(pred: dict, ref: dict, key: str) -> bool:
    """Is the extracted fact the same? See the table above for the per-field rule."""
    p, r = pred.get(key), ref.get(key)
    if key in OBJLIST_FIELDS:
        return _speaker_set(p) == _speaker_set(r)
    if key in NAME_FIELDS:
        return _names(p) == _names(r)
    if key in DATE_FIELDS:
        dp, dr = _as_date(p), _as_date(r)
        return (dp == dr) if (dp and dr) else (_alnum(p) == _alnum(r))
    if key == "resolution_or_motion_no":
        ip, ir = _instrument(p), _instrument(r)
        return bool(ir[1]) and ip == ir
    if key == "project_address":
        return bool(_address_core(r)) and _address_core(p) == _address_core(r)
    if key in ("lot_number", "assessor_block"):
        return bool(_digits_set(r)) and _digits_set(p) == _digits_set(r)
    if key in IDENT_FIELDS:
        return bool(_alnum(r)) and _alnum(p) == _alnum(r)
    if key in FREETEXT_FIELDS:
        if is_empty(r) and is_empty(p):
            return True
        ap, ar = _alnum(p), _alnum(r)
        if ar and ap and (ap.startswith(ar) or ar.startswith(ap) or ar in ap):
            return True          # the labeller abbreviated; the extractor did not
        return _overlap(p, r) >= OVERLAP_MIN
    return field_match(pred, ref, key)      # enums, ints, anything else: exact


def format_ok(pred: dict, key: str) -> bool:
    """Separate from correctness: is the value in the shape downstream code expects?
    Only `project_address` currently has a shape requirement (the geocoder wants the street
    address, not the minutes' locational gloss)."""
    if key == "project_address":
        return not ADDR_GLOSS.search(str(pred.get(key) or ""))
    return True


def score_examples(pred_texts: list[str], ref_texts: list[str]) -> dict:
    """Field-level accuracy over decoded prediction/reference strings. Only fields
    non-empty in the reference are scored. See processing_review.md for rationale."""
    n = len(pred_texts)
    valid = parseable = exact = 0
    field_hits = field_tot = 0
    for p, r in zip(pred_texts, ref_texts):
        if is_valid_json(p.split(EOJ_TOKEN)[0].strip()):
            valid += 1
        pp, rr = parse_obj(p), parse_obj(r)
        if not isinstance(pp, dict) or not isinstance(rr, dict):
            continue
        parseable += 1
        all_match = True
        for k in FIELDS:
            if is_empty(rr.get(k)):
                continue
            field_tot += 1
            if field_match(pp, rr, k):
                field_hits += 1
            else:
                all_match = False
        if all_match:
            exact += 1
    return {
        "valid_json_ratio":   valid / n if n else 0.0,
        "parseable_ratio":    parseable / n if n else 0.0,
        "field_accuracy":     field_hits / field_tot if field_tot else 0.0,
        "exact_record_ratio": exact / parseable if parseable else 0.0,
    }
