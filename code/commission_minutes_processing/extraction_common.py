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
REQUEST_TYPES = [
    "conditional_use", "conditional_use_modification", "discretionary_review",
    "variance", "rezoning_map_amendment", "planning_code_amendment",
    "general_plan_amendment", "text_amendment", "large_project_authorization",
    "downtown_project", "ceqa_environmental",
    "appeal_preliminary_negative_declaration", "historic", "coastal",
    "office_allocation", "appeal", "other",
]
# `action` is now the disposition FAMILY only. "Approved with conditions" / "approved as
# modified" are no longer distinct values — they are `approved` plus the orthogonal
# `conditions_imposed` / `project_modified` flags (which also apply to took_dr_and_approved
# etc.). `intent_to_*` capture the SF pattern of voting an intent, then adopting final
# language weeks later.
ACTIONS = [
    "approved", "disapproved", "continued", "continued_indefinitely", "withdrawn",
    "did_not_take_dr", "took_dr", "took_dr_and_approved",
    "intent_to_approve", "intent_to_disapprove", "filed", "no_action", "other",
]
# Staff preliminary-recommendation categories — mirror the action disposition families so
# "was the staff rec followed?" is a direct enum comparison (no string wrangling), plus the
# staff/CEQA-specific recs (uphold neg-dec, certify EIR, pending). The verbatim wording is
# kept separately in `preliminary_recommendation`.
PRELIM_REC_CATS = [
    "approve", "disapprove", "did_not_take_dr", "took_dr", "took_dr_and_approved",
    "uphold_neg_dec", "certify_eir", "pending", "no_action", "other",
]

# ───────────────────────────────── schema ─────────────────────────────────
SCHEMA = [
    # — identity & panel keys —
    {"name": "meeting_date", "type": "scalar", "section": "Identity",
     "help": "ISO date of the hearing, YYYY-MM-DD"},
    {"name": "jurisdiction", "type": "scalar", "section": "Identity",
     "help": "Regulating body's city/county", "default": "San Francisco"},
    {"name": "supervisorial_district", "type": "scalar", "section": "Identity",
     "help": "Supervisorial district number, e.g. '11' (modern minutes only)"},
    {"name": "item", "type": "scalar", "section": "Identity",
     "help": "Agenda item number, e.g. '1', '12a'"},
    {"name": "case_number", "type": "scalar", "section": "Identity",
     "help": "Planning case number, e.g. 98.226D, 2022-001764CUA"},
    {"name": "request_type", "type": "enum", "section": "Identity", "choices": REQUEST_TYPES,
     "help": "Type of request (often inferable from the case-number suffix)"},
    {"name": "resolution_or_motion_no", "type": "scalar", "section": "Identity",
     "help": "Permanent action id, e.g. 'Motion No. 14638'"},

    # — location (geocoding / merge keys) —
    {"name": "project_address", "type": "scalar", "section": "Location",
     "help": "Street address of the project"},
    {"name": "assessor_block", "type": "scalar", "section": "Location",
     "help": "Assessor's block number"},
    {"name": "lot_number", "type": "scalar", "section": "Location",
     "help": "Lot number(s)"},

    # — zoning & project scale (density) —
    {"name": "type_district", "type": "scalar", "section": "Zoning & scale",
     "help": "Use/zoning district code, e.g. RH-2, NC-3, UMU"},
    {"name": "type_district_descr", "type": "scalar", "section": "Zoning & scale",
     "help": "Plain-English district name"},
    {"name": "height_and_bulk_district", "type": "scalar", "section": "Zoning & scale",
     "help": "Height & bulk district, e.g. 40-X, 50-N"},
    {"name": "special_use_district", "type": "scalar", "section": "Zoning & scale",
     "help": "Special use district, if any"},
    {"name": "project_descr", "type": "text", "section": "Zoning & scale",
     "help": "Full request/description text"},

    # — process & friction —
    {"name": "staff_planner", "type": "scalar", "section": "Process",
     "help": "Assigned planner from the block header, e.g. 'D. Winslow' (initial + surname; drop phone)"},
    {"name": "preliminary_recommendation", "type": "scalar", "section": "Process",
     "help": "STAFF recommendation VERBATIM, e.g. 'Approve with Conditions', 'Disapprove'"},
    {"name": "preliminary_recommendation_category", "type": "enum", "section": "Process",
     "choices": PRELIM_REC_CATS,
     "help": "Staff rec bucketed to a disposition family (compare to `action` for follow-rate)"},
    {"name": "continued_to", "type": "scalar", "section": "Process",
     "help": "If continued: target date (YYYY-MM-DD) or 'indefinite'"},
    {"name": "action", "type": "enum", "section": "Process", "choices": ACTIONS,
     "help": "Commission's disposition of the item"},

    # — politics: mobilization & votes —
    {"name": "speakers", "type": "list", "section": "Politics",
     "help": "Public speaker names (comma-separated)"},
    {"name": "support_count", "type": "int", "section": "Politics",
     "help": "# speakers in SUPPORT (the '+' marker, modern minutes)"},
    {"name": "oppose_count", "type": "int", "section": "Politics",
     "help": "# speakers in OPPOSITION (the '-' marker)"},
    {"name": "neutral_count", "type": "int", "section": "Politics",
     "help": "# neutral speakers (the '=' marker)"},
    {"name": "ayes", "type": "list", "section": "Politics",
     "help": "Commissioners voting AYE (comma-separated surnames)"},
    {"name": "noes", "type": "list", "section": "Politics",
     "help": "Commissioners voting NO"},
    {"name": "absent", "type": "list", "section": "Politics",
     "help": "Commissioners absent for the vote"},
    {"name": "recused", "type": "list", "section": "Politics",
     "help": "Commissioners recused (conflict of interest)"},
    {"name": "excused", "type": "list", "section": "Politics",
     "help": "Commissioners excused"},
    {"name": "vote", "type": "scalar", "section": "Politics",
     "help": "Vote tally, e.g. '7-0', '5-2'"},

    # — conditions —
    {"name": "conditions_imposed", "type": "enum", "section": "Conditions", "choices": ["yes", "no"],
     "help": "Did the approval carry conditions of approval? (orthogonal to `action`)"},
    {"name": "project_modified", "type": "enum", "section": "Conditions", "choices": ["yes", "no"],
     "help": "Were the project's plans revised/amended as a condition of the action?"},
    {"name": "modifications", "type": "text", "section": "Conditions",
     "help": "Conditions / modifications imposed by the Commission"},
]

# ───────────────────── derived lookups (used everywhere) ─────────────────────
FIELDS       = [f["name"] for f in SCHEMA]
FIELD_BY_NAME = {f["name"]: f for f in SCHEMA}
SECTIONS     = list(dict.fromkeys(f["section"] for f in SCHEMA))
LIST_FIELDS  = {f["name"] for f in SCHEMA if f["type"] == "list"}
INT_FIELDS   = {f["name"] for f in SCHEMA if f["type"] == "int"}
TEXT_FIELDS  = {f["name"] for f in SCHEMA if f["type"] == "text"}
ENUM_FIELDS  = {f["name"]: f["choices"] for f in SCHEMA if f["type"] == "enum"}
DEFAULTS     = {f["name"]: f.get("default", "") for f in SCHEMA}

EOJ_TOKEN = "<extra_id_0>"

# Prompt is generated from the schema so it can never drift from FIELDS.
def _build_prompt() -> str:
    lines = ["You are a strict JSON extractor. Given a block of raw meeting text, "
             "extract and return exactly one valid double-quoted JSON object with the "
             "keys below (any order).",
             'Only output the JSON—no commentary. Missing fields → empty string "" '
             "(or [] for list fields, 0 for counts).", "", "Required keys:"]
    for f in SCHEMA:
        hint = f["help"]
        if f["type"] == "enum":
            hint += " — one of: " + ", ".join(f["choices"]) + ', or ""'
        elif f["type"] == "list":
            hint += " — list of strings"
        elif f["type"] == "int":
            hint += " — integer"
        lines.append(f"- {f['name']}: {hint}")
    lines += ["", "Raw block:", ""]
    return "\n".join(lines)

PROMPT_INSTRUCTION = _build_prompt()


# ───────────────────────────── record helpers ─────────────────────────────
# `demolition` is no longer an extracted/labeled field (dropped 2026-07-03): it added
# labeling burden for a signal the description already states. Derive it on demand from
# project_descr instead — e.g. in data_collect when building the analysis table.
_DEMO_RE = re.compile(r"\bdemoli(?:sh|shed|tion)\b", re.I)


def demolition_from_descr(project_descr: str) -> str:
    """'yes' if the project description mentions demolition, else 'no'."""
    return "yes" if project_descr and _DEMO_RE.search(project_descr) else "no"


def empty_record() -> dict:
    """A schema-complete record with type-appropriate empties."""
    rec = {}
    for f in SCHEMA:
        if f["type"] == "list":
            rec[f["name"]] = []
        elif f["type"] == "int":
            rec[f["name"]] = 0
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


def _to_int(v):
    if isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"-?\d+", str(v or ""))
    return int(m.group(0)) if m else 0


def coerce_record(rec: dict) -> dict:
    """Return a schema-complete record: every field present, correctly typed,
    unknown keys dropped (after the caller has normalised typos)."""
    out = empty_record()
    for f in SCHEMA:
        n, t = f["name"], f["type"]
        if n not in rec or rec[n] is None:
            continue
        v = rec[n]
        if t == "list":
            out[n] = _to_list(v)
        elif t == "int":
            out[n] = _to_int(v)
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
    # Derived (check A): vote is recoverable from the roll call, so it is computed
    # rather than hand-labeled. Fill only when empty — never overwrite a stated
    # tally — so a QA pass can still flag ayes/noes-vs-tally mismatches.
    if not out["vote"] and out["ayes"]:
        out["vote"] = f"{len(out['ayes'])}-{len(out['noes'])}"
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
