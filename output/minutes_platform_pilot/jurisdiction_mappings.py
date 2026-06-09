#!/usr/bin/env python3
"""
jurisdiction_mappings.py — PROPOSED additive per-jurisdiction synonym/mapping layer.

Status: PILOT ARTIFACT. This module is NOT yet wired into the pipeline. It is the
Task-3 deliverable of the multi-jurisdiction minutes pilot
(.claude/instructions/minutes_platform_pilot.md). It is intentionally SEPARATE and
ADDITIVE: it does not modify extraction_common.py's SCHEMA, enums, or coerce_record().
If promoted, it would live at code/commission_minutes_processing/jurisdiction_mappings.py.

What it does
------------
Different jurisdictions use different words for the same hearing types and dispositions.
This layer maps each locality's LOCAL vocabulary onto the canonical `request_type` and
`action` enums defined in extraction_common.py — WITHOUT changing those enums.

Cardinal rule honored: every mapping that is a JUDGMENT CALL (rather than an obvious
synonym) is tagged REVIEW so a human adjudicates it. A REVIEW tag means "this code will
return a best-guess enum, but do not trust it until Daniel confirms the mapping."

Coverage so far: only "Daly City", from the one real minutes document retrieved in the
pilot (2023-12-05 Planning Commission minutes). San Jose and Fremont minutes could not
be retrieved (Akamai bot-blocking — see the pilot report), so their vocab is unmapped.

Return contract
---------------
map_request_type(jurisdiction, local_term) -> (enum_value, review_flag)
map_action(jurisdiction, local_term)       -> (enum_value, review_flag)
    review_flag is True when the mapping is a judgment call needing human review,
    None when the jurisdiction/term is unknown (no guess made),
    False when it is a clean, high-confidence synonym match.
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

# Read-only import of the canonical enums purely to VALIDATE that every target we map to
# is a real enum value (guards against typos). We never mutate them.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "code" / "commission_minutes_processing"))
try:
    from extraction_common import REQUEST_TYPES, ACTIONS
except Exception:  # pragma: no cover - allows the table to be inspected standalone
    REQUEST_TYPES = ACTIONS = None

# A mapping entry is either:
#   "enum_value"                     -> clean synonym (review_flag False)
#   ("enum_value", "REVIEW: why")    -> judgment call (review_flag True); 2nd item is the note
REVIEW = "REVIEW"


# ──────────────────────────── request_type vocab ────────────────────────────
# Keys are lowercased local terms / case-number prefixes seen in that city's minutes.
REQUEST_TYPE_MAP: dict[str, dict[str, object]] = {
    "Daly City": {
        # — clean matches —
        "general plan amendment": "general_plan_amendment",
        "gpa": "general_plan_amendment",
        "rezoning": "rezoning_map_amendment",
        "conditional use": "conditional_use",
        "variance": "variance",
        # — JUDGMENT CALLS (no clean canonical home; enums are SF-shaped) —
        "planned development": ("other", "REVIEW: SF schema has no Planned-Development "
                               "request_type; 'other' loses information."),
        "pd": ("other", "REVIEW: case-prefix PD = Planned Development; maps to 'other'."),
        "major subdivision": ("other", "REVIEW: schema has no subdivision request_type."),
        "sub": ("other", "REVIEW: case-prefix SUB = subdivision; no canonical enum."),
        "precise plan": ("other", "REVIEW: no canonical enum for Precise Plan."),
        "development agreement": ("other", "REVIEW: no canonical enum for Development Agreement."),
        # — THE TRAP: Daly City 'DR' = Design Review, NOT SF Discretionary Review —
        "design review": ("other", "REVIEW: Daly City 'Design Review' is a design check, "
                          "NOT SF-style discretionary_review. Do NOT map to discretionary_review."),
        "dr": ("other", "REVIEW: case-prefix DR = Design Review here, which collides with SF's "
               "'DR'=Discretionary Review. Mapping by the letters would be WRONG."),
    },
}

# ──────────────────────────── action / disposition vocab ────────────────────
ACTION_MAP: dict[str, dict[str, object]] = {
    "Daly City": {
        # Daly City PC ACTS by motion ("Moved by ... Motion carried 4-0"); on legislative
        # items (GPA/rezoning/subdivision) it only RECOMMENDS to City Council.
        "recommend approval": ("approved", "REVIEW: PC only RECOMMENDS to Council on "
                               "legislative items; this is not a final approval like SF's."),
        "motion carried": ("approved", "REVIEW: generic carried-motion; the SUBSTANTIVE "
                           "disposition must be read from the motion text, not this phrase."),
        "approved": "approved",
        "approved with conditions": "approved_with_conditions",
        "denied": "disapproved",
        "continued": "continued",
        "withdrawn": "withdrawn",
    },
}

# Jurisdictions that appear to LACK an SF-style discretionary-review mechanism.
# Per the brief this is a SUBSTANTIVE datum (a more by-right regime) but is NOT to be
# asserted on our own authority — every entry here is a REVIEW item for Daniel.
DISCRETIONARY_REVIEW_ABSENCE_REVIEW: dict[str, str] = {
    "Daly City": ("REVIEW: No SF-style discretionary-review of by-right projects observed in "
                  "the one sampled minutes doc; Daly City's 'DR' is Design Review. Cannot tell "
                  "from one document whether DR-as-SF-means-it is absent or just unsampled."),
}


def _lookup(table: dict, jurisdiction: str, term: str):
    j = table.get(jurisdiction)
    if not j:
        return (None, None)  # unknown jurisdiction → no guess
    key = re.sub(r"\s+", " ", (term or "").strip().lower())
    val = j.get(key)
    if val is None:
        return (None, None)  # unknown term → no guess
    if isinstance(val, tuple):
        return (val[0], True)   # judgment call
    return (val, False)         # clean synonym


def map_request_type(jurisdiction: str, local_term: str):
    """-> (canonical_request_type_or_None, review_flag). See module docstring."""
    return _lookup(REQUEST_TYPE_MAP, jurisdiction, local_term)


def map_action(jurisdiction: str, local_term: str):
    """-> (canonical_action_or_None, review_flag). See module docstring."""
    return _lookup(ACTION_MAP, jurisdiction, local_term)


def _validate_targets():
    """Sanity check: every target enum value is real. Run as `python jurisdiction_mappings.py`."""
    if REQUEST_TYPES is None:
        print("(extraction_common not importable; skipping enum validation)")
        return
    bad = []
    for table, allowed, label in [(REQUEST_TYPE_MAP, REQUEST_TYPES, "request_type"),
                                  (ACTION_MAP, ACTIONS, "action")]:
        for juris, terms in table.items():
            for term, val in terms.items():
                ev = val[0] if isinstance(val, tuple) else val
                if ev not in allowed:
                    bad.append(f"{label}: {juris!r} {term!r} -> {ev!r} NOT in canonical enum")
    if bad:
        print("INVALID TARGETS FOUND:"); [print("  " + b) for b in bad]
    else:
        print("OK: all mapping targets are valid canonical enum values.")


if __name__ == "__main__":
    _validate_targets()
