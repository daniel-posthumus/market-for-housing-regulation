#!/usr/bin/env python3
"""
autoextract.py — regex/heuristic best-guess extraction from a raw project block.

Used to PRE-FILL the labeling form (so a human corrects rather than types) and to
derive a few fields in the builder. Deliberately conservative: when unsure it leaves
the field empty for the human. Output is always schema-complete (via coerce_record).
"""
from __future__ import annotations
import re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extraction_common import coerce_record, empty_record

# Suffix allows lowercase: some items print the type suffix lowercase ("2004.1234d"),
# which an uppercase-only suffix would truncate to "2004.1234" (dropping the type).
# The archive sometimes breaks a suffix with a stray space — "2006.1052E T", "97.870 C V",
# "2005.0148DD V" — and a suffix-only pattern stopped at the space, dropping the type letter
# and with it the derived request_type (14 `E`, 6 `GPA`, and the rest). The trailing group
# picks up those orphans, but only when each is a LONE capital: " D" continues a case
# number, " District" does not. It can only EXTEND a match that already started, so it
# cannot invent a case number where there was none.
# Two guards, both learned from real mis-parses:
#
# `(?![\d]|[.\-]\d)` after the numeric part — never cut a code in half. The 2015-2017
# minutes print the agenda number hard against the case number ("12.2015-006317CUA"), and
# without this the match is "12.2015": the agenda number plus the case number's year. That
# corrupted 362 items, concentrated in 2015-2017. The guard applies to the DIGITS only, so a
# letter suffix is still taken whole ("2015-002632VAR." stays VAR, not VA).
#
# A four-digit leading part must be a YEAR. Otherwise a street-address range ("1650-1680
# Mission Street") and a permit number ("2007.0619.4378") read as case numbers; two-digit
# years are unconstrained because 98.226D is real.
# "2004. 0164D" — a space after the separator. Same quirk as the header regex; without it the
# match fails and the scanner falls through to a building-permit number later in the block.
CASE_RE = re.compile(
    r"\b((?:\d{2}|(?:19|20)\d{2})[.\-][ ]?\d{3,}(?![\d]|[.\-]\d)"
    r"(?:[A-Za-z0-9/]+)?(?:[ ][A-Z](?![A-Za-z]))*)")


def normalise_case(cn: str) -> str:
    """'2006.1052E T' -> '2006.1052ET'. The spaces are a rendering artefact; the suffix is
    one token, and derive_request_type() only sees the letters if they are joined."""
    return re.sub(r"\s+", "", cn or "")
ITEM_RE = re.compile(r"^\s*(\d{1,2}[a-z]?)[.\)]\s", re.M)


def derive_request_type(case_number: str) -> str:
    """Map a case-number suffix to a request_type enum value."""
    if not case_number:
        return ""
    m = re.search(r"[.\-]\d{3,}([A-Za-z/]+)\b", case_number)
    suf = (m.group(1).upper() if m else "")
    table = [
        ("CUA", "conditional_use"), ("PCA", "planning_code_amendment"),
        ("GPA", "general_plan_amendment"), ("DRP", "discretionary_review"),
        ("DRM", "discretionary_review"), ("ENV", "ceqa_environmental"),
        ("MAP", "rezoning_map_amendment"), ("OFA", "office_allocation"),
        ("LPA", "large_project_authorization"),
    ]
    for k, v in table:
        if k in suf:
            return v
    single = {
        "C": "conditional_use", "D": "discretionary_review", "V": "variance",
        "Z": "rezoning_map_amendment", "T": "planning_code_amendment",
        "E": "ceqa_environmental", "X": "large_project_authorization",
        # `L` is an Article 10 landmark designation — the same historic-preservation family
        # as `H` (Certificate of Appropriateness). It was missing, so every designation
        # pre-filled blank and the QA check stayed silent because the suffix was unknown.
        "H": "historic", "L": "historic", "R": "downtown_project",
    }
    for ch in suf:
        if ch in single:
            return single[ch]
    return ""


def _after(label: str, text: str) -> str:
    m = re.search(rf"{label}\s*:?\s*(.+)", text, re.I)
    if not m:
        return ""
    val = m.group(1)
    stop = re.search(r"(RESOLUTION|MOTION|EXCUSED|ABSENT|RECUSED|NOES|NAYES|AYES|"
                     r"ACTION|SPEAKERS|PRELIMINARY|<<Project)", val, re.I)
    if stop and stop.start() > 0:
        val = val[:stop.start()]
    return val.strip()


# The minutes always print the disposition as "ACTION:" starting its own line. `_after`
# is unanchored and case-insensitive, so it happily matched the "action" inside
# "attractions"/"satisfaction" and returned that sentence as the disposition. Anchoring is
# the whole fix; the leading class allows the &nbsp; indents the archive uses.
ACTION_LINE = re.compile(r"(?im)^[^\S\r\n]*ACTION[^\S\r\n]*:[^\S\r\n]*")
# These words also occur in ordinary prose — "No Action is required of the Commission"
# truncated the disposition to "No" because the stop list matched the word `action` inside
# the value. A stop only counts as a LABEL when it starts a line or is followed by a colon.
_STOP = re.compile(
    r"(?im)^[^\S\r\n]*(?:RESOLUTION|MOTION|EXCUSED|ABSENT|RECUSED|NOES|NAYES|AYES|ACTION|"
    r"SPEAKERS?|PRELIMINARY|DRA)\b"
    r"|(?:RESOLUTION|MOTION|EXCUSED|ABSENT|RECUSED|NOES|NAYES|AYES|ACTION|SPEAKERS?|DRA)"
    r"[^\S\r\n]*\(?s?\)?[^\S\r\n]*:"
    r"|<<Project")


def action_text(block: str) -> tuple[str, str]:
    """(the ACTION line's value, everything from ACTION to the end of the block).

    The second half exists because a disposition often reads only "Approved" and then
    enumerates its conditions below; the conditions belong to this item, whereas text
    ABOVE the action can be describing a PRIOR entitlement's conditions.
    """
    m = ACTION_LINE.search(block)
    if not m:
        return "", ""
    tail = block[m.end():]
    stop = _STOP.search(tail)
    line = tail[:stop.start()] if stop and stop.start() > 0 else tail
    return line.strip(), block[m.start():]


_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}


def _iso_date(s: str) -> str:
    """'April 21, 2005' -> '2005-04-21'. Empty string when it will not parse."""
    import datetime
    s = (s or "").strip()
    n = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})$", s)
    if n:
        mo, day, yr = (int(x) for x in n.groups())
        yr += 1900 if 90 <= yr <= 99 else (2000 if yr < 90 else 0)
        try:
            return datetime.date(yr, mo, day).isoformat()
        except ValueError:
            return ""
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", s)
    if not m:
        return ""
    mo = _MONTHS.get(m.group(1).lower())
    if not mo:
        return ""
    try:
        return datetime.date(int(m.group(3)), mo, int(m.group(2))).isoformat()
    except ValueError:
        return ""


def _names(s: str) -> list[str]:
    s = re.sub(r"\b(and|None)\b", "", s, flags=re.I)
    return [p.strip() for p in re.split(r"[,;]", s) if p.strip()]


def _action_enum(txt: str) -> str:
    # Disposition FAMILY only — conditions/modification are captured by the separate
    # conditions_imposed / project_modified flags (see extract()).
    t = txt.lower()
    if not t:
        return ""
    dr = "discretionary" in t or re.search(r"\bd\.?r\.?\b", t)
    if "intent" in t and ("disapprov" in t or "denied" in t or "deny" in t):
        return "intent_to_disapprove"
    if "intent" in t and "approv" in t:
        return "intent_to_approve"
    if dr and re.search(r"(did not|not to|declin|no)\s+.{0,6}?take", t) or re.search(r"\bno dr\b", t):
        return "did_not_take_dr"
    if dr and "took" in t and "approv" in t:
        return "took_dr_and_approved"
    if dr and ("took" in t or "take" in t or "taken" in t):
        return "took_dr"
    if "indefinit" in t:
        return "continued_indefinitely"
    if "continu" in t:
        return "continued"
    if "withdrawn" in t:
        return "withdrawn"
    if "disapprov" in t or "denied" in t:
        return "disapproved"
    if "approv" in t:
        return "approved"
    if "filed" in t:
        return "filed"
    return "other"


def _prelim_cat(txt: str) -> str:
    """Bucket a verbatim staff recommendation into a PRELIM_REC_CATS family."""
    t = (txt or "").lower()
    if not t:
        return ""
    if "pending" in t:
        return "pending"
    if "certif" in t and ("eir" in t or "environmental impact report" in t):
        return "certify_eir"
    if "uphold" in t and ("negative declaration" in t or "neg dec" in t or "mitigated" in t):
        return "uphold_neg_dec"
    if "discretionary" in t and re.search(r"(do not|does not|did not|not to|not)\s+.{0,6}?take", t):
        return "did_not_take_dr"
    if "discretionary" in t and "take" in t and "approv" in t:
        return "took_dr_and_approved"
    if "discretionary" in t and "take" in t:
        return "took_dr"
    if "disapprov" in t or "denied" in t or "deny" in t:
        return "disapprove"
    if "approv" in t:
        return "approve"
    if "no action" in t or "informational" in t:
        return "no_action"
    return "other"




def extract(block: str, meeting_date: str = "") -> dict:
    """meeting_date is accepted and ignored: it belongs to the meeting, not the block, and
    is assigned by assign_meeting_dates.py. The parameter stays so existing call sites and
    the app's prefill path keep working."""
    rec = empty_record()

    cm = CASE_RE.search(block)
    if cm:
        rec["case_number"] = normalise_case(cm.group(1))
        rec["request_type"] = derive_request_type(rec["case_number"])

    # parcel / districts
    b = re.search(r"Assessor.{0,3}s?\s+Block\s+([0-9A-Z]+)", block, re.I)
    if b:
        rec["assessor_block"] = b.group(1)
    lot = re.search(r"\bLots?\s+([0-9A-Z,\s and]+?)\s+in\s+Assessor", block, re.I)
    if lot:
        rec["lot_number"] = lot.group(1).strip().rstrip(",")
    td = re.search(r"\b([A-Z]{1,4}-?\d[A-Z]?)\s*\(([^)]+?)\)\s*District", block)
    if td:
        rec["type_district"] = td.group(1)
        rec["type_district_descr"] = td.group(2).strip().lower()
    hb = re.search(r"\b(\d{2,3}-[A-Z])\b(?:\s+Height)?", block)
    if hb:
        rec["height_and_bulk_district"] = hb.group(1)

    # address: an ALL-CAPS street-ish line near the top
    addr = re.search(r"\n\s*([0-9][0-9A-Z\-/ ]{2,40}(?:STREET|AVENUE|BOULEVARD|"
                     r"ROAD|DRIVE|PLACE|WAY|TERRACE|LANE|COURT|CIRCLE))\b", block, re.I)
    if addr:
        rec["project_address"] = addr.group(1).strip().title()

    # request description: the sentence beginning "Request for ..."
    desc = re.search(r"(Request for .+?)(?:\(Continued|\(Proposed|Preliminary "
                     r"Recommendation|SPEAKERS|\n\n)", block, re.S | re.I)
    if desc:
        rec["project_descr"] = re.sub(r"\s+", " ", desc.group(1)).strip()

    # process
    pl = re.search(r"\(\s*([A-Z]\.?\s*[A-Za-z][A-Za-z'\-]+(?:-[A-Za-z'\-]+)?)\s*"
                   r":?\s*\(?\d{3}\)?[\s\-]?\d{3}", block)
    if pl:
        rec["staff_planner"] = re.sub(r"\s+", " ", pl.group(1).strip()).title()
    rec["preliminary_recommendation"] = _after("Preliminary Recommendation", block)
    rec["preliminary_recommendation_category"] = _prelim_cat(rec["preliminary_recommendation"])
    # The archive writes the target date both ways — "April 21, 2005" and "3/12/98".
    #
    # Search the ACTION LINE FIRST. A block routinely carries "(Proposed for Continuance to
    # June 20, 2024)" above its ACTION, and that parenthetical is the agenda's proposal, not
    # the Commission's decision: of the 1,552 blocks printing both, the two dates disagree on
    # 233. Scanning the whole block takes the first match, which is always the proposal.
    _CONT_DATE = r"continu\w+\s+to\s+([A-Z][a-z]+ \d{1,2},? \d{4}|\d{1,2}/\d{1,2}/\d{2,4})"
    _act_txt, _act_tail = action_text(block)
    cont = (re.search(_CONT_DATE, _act_txt + " " + _act_tail, re.I)
            or re.search(_CONT_DATE, block, re.I))
    if cont:
        # The minutes write "April 21, 2005"; the schema stores ISO. Without this the field
        # scored 0% against the gold set on 88 items — every value was RIGHT and every one
        # counted as a miss, because the comparison is exact after case/space folding.
        rec["continued_to"] = _iso_date(cont.group(1)) or cont.group(1)
    elif re.search(r"indefinite", block, re.I) and re.search(r"continu", block, re.I):
        rec["continued_to"] = "indefinite"
    action_txt, action_tail = action_text(block)
    rec["action"] = _action_enum(action_txt)
    # conditions_imposed / project_modified are orthogonal flags. `conditions_imposed` reads
    # the ACTION line AND everything after it: the disposition often says only "Approved"
    # and then enumerates the conditions below, so the ACTION line alone finds "with
    # conditions" on 10% of items while the block carries it on 32%. Text BEFORE the action
    # is deliberately excluded — a request to amend "the conditions of approval imposed
    # under Motion No. 17518" describes a PRIOR entitlement's conditions, not this one's.
    al = action_txt.lower()
    after_action = action_tail.lower()
    if re.search(r"with (the )?(following |modified )?condition", al) or (
            al and not re.search(r"continu|withdraw", al)
            and re.search(r"subject to the (following |attached )?conditions?"
                          r"|conditions? of approval", after_action)):
        rec["conditions_imposed"] = "yes"
    if "as modified" in al or "as amended" in al or "revised plan" in al or "modified condition" in al:
        rec["project_modified"] = "yes"

    # politics — confine stance counting to the SPEAKERS section (markers only
    # exist there, 2015+), so hyphens in addresses/"2-story" don't get counted.
    sp_sec = re.search(r"SPEAKERS?\s*\(?S?\)?\s*:?\s*(.*?)(?:\n\s*ACTION|\n\s*AYES|\Z)",
                       block, re.S | re.I)
    sptext = sp_sec.group(1) if sp_sec else ""
    if sptext.strip() and sptext.strip().lower() != "none":
        names = []
        for line in sptext.splitlines():
            line = line.strip().lstrip("+-=").strip()
            line = re.split(r"\s[–-]\s", line)[0].strip()   # drop "– topic"
            names += _names(line)
        rec["speakers"] = names
    rec["support_count"] = len(re.findall(r"(?m)^\s*\+\s*\S", sptext))
    rec["oppose_count"]  = len(re.findall(r"(?m)^\s*-\s*\S", sptext))
    rec["neutral_count"] = len(re.findall(r"(?m)^\s*=\s*\S", sptext))
    for fld, lab in [("ayes", "AYES"), ("noes", "NOES"), ("absent", "ABSENT"),
                     ("recused", "RECUSED"), ("excused", "EXCUSED")]:
        v = _after(lab, block)
        if v and v.lower() != "none":
            rec[fld] = _names(v)
    nm = re.search(r"((?:Motion|Resolution)\s+No\.?\s*[:#]?\s*\d+)", block, re.I)
    if nm:
        rec["resolution_or_motion_no"] = nm.group(1).strip()

    return coerce_record(rec)
