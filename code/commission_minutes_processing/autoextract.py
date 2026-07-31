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
CASE_RE = re.compile(r"\b((?:\d{2}|\d{4})[.\-]\d{3,}(?:[A-Za-z0-9/]+)?)\b")
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
        "Z": "rezoning_map_amendment", "T": "text_amendment",
        "E": "ceqa_environmental", "X": "large_project_authorization",
        "H": "historic", "R": "downtown_project",
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




def extract(block: str, meeting_date: str = "", jurisdiction: str = "San Francisco") -> dict:
    rec = empty_record()
    rec["meeting_date"] = meeting_date
    rec["jurisdiction"] = jurisdiction

    cm = CASE_RE.search(block)
    if cm:
        rec["case_number"] = cm.group(1)
        rec["request_type"] = derive_request_type(cm.group(1))
    im = ITEM_RE.search(block)
    if im:
        rec["item"] = im.group(1)

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
    sd = re.search(r"\(District\s+(\d+)\)", block)
    if sd:
        rec["supervisorial_district"] = sd.group(1)

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
    cont = re.search(r"continu\w+\s+to\s+([A-Z][a-z]+ \d{1,2},? \d{4})", block, re.I)
    if cont:
        rec["continued_to"] = cont.group(1)
    elif re.search(r"indefinite", block, re.I) and re.search(r"continu", block, re.I):
        rec["continued_to"] = "indefinite"
    action_txt = _after("ACTION", block)
    rec["action"] = _action_enum(action_txt)
    # conditions_imposed / project_modified are orthogonal flags derived from the ACTION text
    al = action_txt.lower()
    if re.search(r"with (the )?(following |modified )?condition", al):
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
