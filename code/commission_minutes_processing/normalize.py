#!/usr/bin/env python3
"""
normalize.py
------------
Purpose : Convert raw extraction output — from the regex extractor or from a model — into
          storage form. Every rule here is deterministic and applied identically to both
          paths, so a value is never right in one pipeline and wrong in the other.
Inputs  : a record dict as extracted (v2 schema).
Outputs : the same record with dates in ISO, lots as ints, counts derived, addresses
          truncated at the locational gloss.
Author  : Dan Post
Created : 2026-09-07

Notes
-----
Why a layer rather than prompt instructions: the model should return what the text says,
and the text says "April 21, 2005". Asking it to also produce ISO makes formatting a
reasoning step it can fail. `continued_to` scored 0% in the first bakeoff for exactly this
reason — every value correct, every one counted wrong, because the extractor emitted the
archive's format and the schema wanted ISO.

Rule of thumb for anything added here later: if a human could write the transformation as a
regex with confidence, it belongs in this file and not in the prompt.

STORAGE vs QUERY form for the parcel keys. `lot_number` is stored as a list of ints
([9, 10]) and `assessor_block` as unpadded digits, because that is the only form in which
"009" and "9" are the same lot. The DataSF parcel query needs zero-padding (block to 4, lot
to 3) and `link_permits.py` already applies it at query time via `z4()`/`zfill(3)`. Do not
pad at storage time: padding is a property of that one API, not of the datum.
"""
from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from extraction_common import (SCHEMA, DERIVED_FIELDS, derive_speaker_counts,  # noqa: E402
                               _to_objlist)

_SPEAKER_FLD = next(f for f in SCHEMA if f["name"] == "speakers")

# ── dates ────────────────────────────────────────────────────────────────────
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
DATE_FIELDS = ("continued_to",)


def iso_date(v) -> str:
    """Parse the forms the archive actually uses; return ISO, or "" if it will not parse.

    Deliberately conservative: an unparseable string is returned as "" by `normalize_date`
    only when it is clearly not a date, and otherwise passed through untouched, so a human
    can still see what the source said.
    """
    s = str(v or "").strip()
    if not s:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    m = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", s)
    if m and m.group(1).lower() in _MONTHS:
        try:
            return datetime.date(int(m.group(3)), _MONTHS[m.group(1).lower()],
                                 int(m.group(2))).isoformat()
        except ValueError:
            return ""
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
    if m:
        mo, day, yr = (int(x) for x in m.groups())
        yr += 1900 if 90 <= yr <= 99 else (2000 if yr < 90 else 0)
        try:
            return datetime.date(yr, mo, day).isoformat()
        except ValueError:
            return ""
    return ""


# ── parcel keys ──────────────────────────────────────────────────────────────
def lot_list(v) -> list:
    """'009, 010' and '9,10' and [9, 10] all become [9, 10]. Non-numeric lots (e.g. '020A')
    keep their letter and stay strings, because dropping it would merge two parcels."""
    if isinstance(v, list):
        parts = [str(x) for x in v]
    else:
        # A list that has been through str() somewhere upstream — "[8]", "['7C']" — must
        # still parse. It reached here once, via a scalar coercion, and 122 gold records
        # silently lost their lot numbers because "[8]" matches no lot pattern at all.
        t = re.sub(r"^\s*\[|\]\s*$", "", str(v or "")).replace("'", "").replace('"', "")
        parts = re.split(r"[,;/&]| and ", t, flags=re.I)
    out = []
    for p in parts:
        # strip the debris a stringified list leaves on the individual parts too
        p = p.strip().strip("[]'\" ")
        if not p:
            continue
        # "Lot 001O" is a scanned zero read as the letter O. A lot number is digits with at
        # most one trailing letter, and O is never used as that letter precisely because it
        # is unreadable next to a zero — so an O inside a lot token is OCR, not a suffix.
        if re.fullmatch(r"[0-9Oo]+", p):
            p = re.sub(r"[Oo]", "0", p)
        else:
            m_o = re.fullmatch(r"([0-9Oo]+)([A-NP-Za-np-z])", p)
            if m_o:
                p = re.sub(r"[Oo]", "0", m_o.group(1)) + m_o.group(2)
        if p.isdigit():
            out.append(int(p))
        elif re.fullmatch(r"0*(\d+)([A-Za-z])", p):
            m = re.fullmatch(r"0*(\d+)([A-Za-z])", p)
            out.append(f"{int(m.group(1))}{m.group(2).upper()}")
    return out


def block_key(v) -> str:
    """Assessor block, unpadded. '0814' -> '814'; '2888A' -> '2888A'."""
    s = str(v or "").strip().upper()
    m = re.fullmatch(r"0*(\d+)([A-Z]?)", s)
    return f"{int(m.group(1))}{m.group(2)}" if m else s


def instrument_no(v) -> int:
    m = re.search(r"\d+", str(v or ""))
    return int(m.group(0)) if m else 0


# ── people ───────────────────────────────────────────────────────────────────
_HONORIFIC = re.compile(r"^(commissioner|president|vice[- ]president|mr|ms|mrs|dr)\.?\s+", re.I)


# A leading initial is printed three ways in this corpus — "B. Wycko", "B.Wycko", "R Cooper"
# — and the difference is typography, not information. Left alone it shows up as a
# gold-vs-model disagreement on a name both sides read correctly, which wastes the scarce
# input (a human's attention) on a space.
_INITIAL = re.compile(r"^([A-Z])\.?\s*(?=[A-Z][a-z])")


# "(M) Speaker" / "(F) Speaker" — the modern minutes record the gender of a speaker whose
# name was not caught. It costs nothing to keep and may be worth something later, so it is
# the one parenthetical that survives; the rest (affiliations, phone numbers, "(Project
# Sponsor)") are dropped. Stripping every parenthetical silently deleted these on three gold
# items before this was noticed.
_GENDER = re.compile(r"\(\s*([MF])\s*\)", re.I)


def clean_name(v) -> str:
    raw = str(v or "")
    g = _GENDER.search(raw)
    n = re.sub(r"\(.*?\)", "", raw).strip().strip(".,;")
    n = _HONORIFIC.sub("", n).strip()
    n = _INITIAL.sub(r"\1. ", n)
    return f"({g.group(1).upper()}) {n}".strip() if g else n


# ── address ──────────────────────────────────────────────────────────────────
# Belt-and-braces backstop to the help text: the minutes append a locational gloss after
# the street name and a model asked for "the address" will sometimes include it.
ADDRESS_GLOSS = re.compile(
    r"\s*,?\s*(?:"
    r"(?:north|south|east|west)(?:erly)?\s+side\b"
    r"|between\b|at\s+the\s+(?:corner|intersection)\b|on\s+the\s+(?:north|south|east|west)\b"
    r"|near\s+the\b|adjacent\s+to\b|\(a\.?k\.?a\b"
    r")", re.I)


def address_core(v) -> str:
    s = str(v or "").strip()
    cut = ADDRESS_GLOSS.search(s)
    if cut:
        s = s[:cut.start()]
    return re.sub(r"[\s,;]+$", "", s).strip()


def address_format_ok(v) -> bool:
    """Soft warning, not a gate: did the value arrive without the gloss already attached?"""
    return not ADDRESS_GLOSS.search(str(v or ""))


# ── the project description ──────────────────────────────────────────────────
# `project_descr` is a MECHANICAL target, extractable by rule — the same rule fills the
# app's one-click button and the migration script's proposal, so a human accepting the
# proposal and a human pressing the button produce the same string.
#
# The target was originally "the Request-for clause through the FIRST SENTENCE". The first
# labelling session killed that: the opening sentence routinely names the request type and
# the zoning district and stops before the project itself, so the rule dropped the substance
# on roughly one item in six, and on the mojibake-heavy 2000-2001 pages a stray "?" read as
# a sentence end and cut the description down to nine words. It also mis-fired the other way
# — nothing about "first sentence" is stable when the archive's punctuation is not.
#
# The rule now runs from the opening phrase to where the CLOSING BLOCK begins. That boundary
# is real: it is printed, it is the same in both eras, and it does not depend on punctuation.
# Anything finer — splitting the district out of the description, or the scale — is a
# downstream step on text we already have, rather than a boundary rule guessing in advance
# what will turn out to matter.
# The 1998-2014 pages break a word wherever an HTML tag sat inside it, almost always after
# the first letter: "P reliminary" (133 items), "R equest" (8), "S taff" (5), "M otion",
# "A YES". A keyword written literally simply misses those, and the miss is silent — the
# description runs straight through a boundary that is printed right there in the text.
# `_loose` allows whitespace between any two letters, which cannot create a false positive:
# the letters must still appear in order with nothing but space between them.
def _loose(word: str) -> str:
    return r"\s*".join(re.escape(ch) for ch in word)


# "Request by Metro PCS for Conditional Use authorization" — the requester's name sits
# between the two words often enough to matter.
_REQUEST_FOR = re.compile(
    r"\b" + _loose("Request") + r"(?:ing)?\s+(?:by\s+[\w\s.,&'’-]{1,45}?\s+)?for\b", re.I)

# Where the description stops: the labelled fields of the closing block. Two forms are
# matched — with a colon, case-insensitively, and bare UPPERCASE without one, since some
# pages print "SPEAKER(S)" as a heading. The bare form is case-SENSITIVE on purpose: a
# case-insensitive "speakers" would fire on the word inside the description.
_CLOSING = re.compile(
    r"(?:^|\s)(?:"
    r"(?i:SPEAKERS?\s*\(?s?\)?\s*:|ACTION\s*:|AYES\s*:|NOES\s*:|NAYES\s*:|ABSENT\s*:"
    r"|EXCUSED\s*:|RECUSED\s*:|MOTION\s*(?:NO\.?|#)?\s*:|RESOLUTION\s*(?:NO\.?|#)?\s*:"
    r"|DRA\s*(?:NO\.?|#)?\s*:|" + _loose("Preliminary") + r"\s+Recommendations?\s*:"
    r"|NOTE\s*:|" + _loose("Staff") + r"\s+" + _loose("Analysis") + r"\s*:?"
    r"|\(Proposed\b|\(Continued\b)"
    r"|SPEAKERS?\s*\(?[Ss]?\)?(?=\s)|ACTION(?=\s)|AYES(?=\s)"
    # An ALL-CAPS run ending in WITHDRAWN is the disposition, printed where ACTION: would
    # otherwise be: "DISCRETIONARY REVIEW WITHDRAWN", "APPEAL WITHDRAWN", "ALL DISCRETIONARY
    # REVIEW REQUESTS HAVE BEEN WITHDRAWN". It is an action, not part of the description.
    # Case-SENSITIVE on purpose: "Request for Discretionary Review of ..." is the request.
    r"|[A-Z]{2,}(?:\s+[A-Z]{2,}){0,5}\s+WITHDRAWN\b"
    r")")


# Only ~63% of blocks open with "Request for". The rest use one of a small set of other
# openers, and the help text's fallback ("the first sentence that states what is being asked
# of the Commission") points straight at them. They are kept SEPARATE from the strict rule:
# a "Request for" hit is mechanical and a labeller can accept it without reading, an opener
# hit is a proposal that wants a glance.
# "Mandatory Discretionary Review" is listed FIRST because of where it sits in the sentence,
# not because alternation order decides: `re.search` takes the leftmost match, and the bare
# "Review of" inside "Mandatory Discretionary Review of Building Permit..." starts 22
# characters later — so without this alternative the opener silently became "Review of" and
# the label lost the words that say WHICH KIND of review this is. Eighteen of the 23
# Mandatory-DR items in the gold set were being truncated that way.
#
# "Review of" is also pinned to a capital R (`(?-i:...)` inside an otherwise case-insensitive
# pattern). The lower-case form only ever occurs mid-sentence — "under the Planning
# Commission's policy requiring review of dwelling unit mergers" — where it is prose, not an
# opening phrase.
_OPENERS = re.compile(
    r"\b(?:" + _loose("Mandatory") + r"\s+Discretionary\s+Review\b"
    r"|" + _loose("Consideration") + r"\s+(?:of|to)\b"
    r"|" + _loose("Appeal") + r"\s+of\b"
    r"|" + _loose("Public") + r"\s+hearing\s+on\b"
    r"|(?-i:" + _loose("Review") + r"\s+of)\b"
    r"|" + _loose("Informational") + r"\s+(?:presentation|hearing|item)\b"
    r"|" + _loose("Adoption") + r"\s+of\b"
    r"|" + _loose("Request") + r"\s+(?:to|under)\b"
    r"|The\s+proposal\s+is\s+to\b"
    # Legislative items — the `T`/`PCA` cases. These never say "Request for" and were
    # returning a blank proposal on items whose description is the whole point of the
    # hearing: "Amendments to Planning Code Sections 803.4 and 815: Massage Services in ...".
    r"|" + _loose("Amendment") + r"s?\s+(?:to|of|relating\s+to)\b"
    r"|Planning\s+Code\s+" + _loose("Amendment") + r"s?\b"
    r"|" + _loose("Ordinance") + r"\s+(?:introduced|amending|adding|repealing)\b"
    r"|" + _loose("Initiation") + r"\s+of\b"
    r"|" + _loose("Resolution") + r"\s+(?:approving|adopting|initiating)\b"
    # Other request shapes that carry no "Request for"
    r"|" + _loose("Certification") + r"\s+of\b"
    r"|" + _loose("Report") + r"\s+on\s+Compliance\b"
    r"|(?:Two|Three|Four|Several)?\s*" + _loose("Discretionary") + r"\s+Review\s+requests?\b"
    # The words before "Variance" are part of the request ("Rear yard and non-complying
    # structure variances sought per..."), but they must be WORDS: allowing any token walks
    # backwards into the address and starts the description at "Block 4211 - Front Setback".
    r"|(?:[A-Za-z][\w-]*\s+){0,5}?Variances?\s+(?:sought|requested|under|per|pursuant)\b"
    r")", re.I)

# Where the location header stops and the description starts, for the blocks that open with
# neither a request phrase nor a recognised legislative one. The header is stereotyped —
# "... Lot 72A in Assessor's Block 3540, six unit residential condominium conversion ..." —
# so the description begins after the block/lot citation. Lower confidence than an explicit
# opener, and reported as such (`after_header`) so the labeller reads it rather than
# accepting it blind.
_HEADER_END = re.compile(
    r"\bAssessor'?[’']?s?\s+Block\s+\d+[A-Za-z]?"
    # A trailing lot list. Each entry must START WITH A DIGIT — under re.I a bare [A-Z]
    # class also matches lower case, and "…Block 3549, Lot 064, located at…" then swallows
    # the word "located" and begins the description mid-phrase.
    r"(?:\s*(?:,|and|/)\s*(?:Lots?\s*)?\d+[A-Za-z]?)*"
    r"\s*(?:[-–—:,]|\bin\b)?\s*", re.I)


def _clause_from(text: str, at: int) -> str:
    """From `at` to the start of the closing block (or the end of the text)."""
    tail = text[at:]
    end = _CLOSING.search(tail)
    return re.sub(r"\s+", " ", tail[:end.start()] if end else tail).strip()


def request_for_clause(block: str) -> str:
    """The verbatim description, starting at the "Request for..." phrase.

    Returns "" when the block has no such phrase; `descr_proposal` handles the fallback.
    """
    text = re.sub(r"[ \t]*\n[ \t]*", " ", str(block or ""))     # unwrap hard line breaks
    m = _REQUEST_FOR.search(text)
    return _clause_from(text, m.start()) if m else ""


def descr_proposal(block: str) -> tuple:
    """(text, rule) for `project_descr`, where rule is 'request_for' | 'opener' | 'none'.

    The rule is returned rather than hidden so callers can say how much to trust it: the
    migration report separates the two, and the app labels its button accordingly.

    The two patterns compete on POSITION, not on precedence. Preferring "Request for"
    wherever it appears reads across a mis-split block: item 6109 holds two agenda items,
    an Appeal at character 114 and a Request for Discretionary Review at 1391, and a
    precedence rule hands back the second item's description for the first item's label.
    Leftmost is also simply what "where the description starts" means.
    """
    text = re.sub(r"[ \t]*\n[ \t]*", " ", str(block or ""))
    # The description lives BEFORE the closing block, so that is where the opening phrase is
    # looked for. Without the bound, "Preliminary Recommendation: Informational Presentation
    # and Public Comment" reads as an opener and the label starts inside the staff
    # recommendation — 3,000 characters past where the description actually began.
    stop = _CLOSING.search(text)
    head = text[:stop.start()] if stop else text
    rf, op = _REQUEST_FOR.search(head), _OPENERS.search(head)
    if rf and (not op or rf.start() <= op.start()):
        return _clause_from(text, rf.start()), "request_for"
    if op:
        return _clause_from(text, op.start()), "opener"
    # No recognised opening phrase: fall back to "everything after the location header".
    # Only when that leaves real prose behind — a header with nothing after it means the
    # description is somewhere this rule cannot see, and a blank is the honest answer.
    hdr = None
    for hdr in _HEADER_END.finditer(head):
        pass
    if hdr:
        tail = _clause_from(text, hdr.end())
        if len(tail) >= 40:
            return tail, "after_header"
    return "", "none"


# ── the whole record ─────────────────────────────────────────────────────────
# Several planners are recorded on one item ("C. Nikitas/E. Watty"). The separator is
# arbitrary — slash, comma, semicolon — so it is normalised to one form.
_NAME_SEP = re.compile(r"\s*[/;]\s*")


def normalize_record(rec: dict) -> dict:
    """Apply every storage rule. Idempotent: normalising twice changes nothing."""
    out = dict(rec)
    if "staff_planner" in out and str(out["staff_planner"] or "").strip():
        out["staff_planner"] = ", ".join(
            clean_name(x) for x in _NAME_SEP.split(str(out["staff_planner"])) if x.strip())

    for f in DATE_FIELDS:
        if f in out and str(out[f] or "").strip():
            iso = iso_date(out[f])
            out[f] = iso if iso else out[f]          # leave an unparseable string visible

    if "lot_number" in out:
        out["lot_number"] = lot_list(out["lot_number"])
    if "assessor_block" in out:
        out["assessor_block"] = block_key(out["assessor_block"])
    if "action_instrument_no" in out:
        out["action_instrument_no"] = instrument_no(out["action_instrument_no"])
    if "project_address" in out:
        out["project_address"] = address_core(out["project_address"])

    if isinstance(out.get("speakers"), list):
        # Route through _to_objlist first: a v1 record's speakers are bare strings, and
        # filtering to dicts here would silently drop every one of them.
        out["speakers"] = [dict(s, name=clean_name(s.get("name")))
                           for s in _to_objlist(out["speakers"], _SPEAKER_FLD)]
    # counts are derived, always — a model-supplied count is discarded
    out.update(derive_speaker_counts(out.get("speakers", [])))
    return out


# ── tests ────────────────────────────────────────────────────────────────────
def _test():
    cases = []
    ok = lambda name, got, want: cases.append((name, got == want, got, want))  # noqa: E731

    ok("iso month", iso_date("April 21, 2005"), "2005-04-21")
    ok("iso slash 2-digit", iso_date("3/12/98"), "1998-03-12")
    ok("iso slash 4-digit", iso_date("12/5/2011"), "2011-12-05")
    ok("iso passthrough", iso_date("2005-04-21"), "2005-04-21")
    ok("iso invalid", iso_date("13/45/99"), "")
    ok("iso empty", iso_date(""), "")

    ok("lot padded", lot_list("009, 010"), [9, 10])
    ok("lot bare", lot_list("9,10"), [9, 10])
    ok("lot single", lot_list("001"), [1])
    ok("lot letter", lot_list("020A"), ["20A"])
    ok("lot 'and'", lot_list("5 and 6"), [5, 6])
    ok("lot already list", lot_list([9, 10]), [9, 10])

    ok("block strip", block_key("0814"), "814")
    ok("block letter", block_key("2888A"), "2888A")

    ok("instrument no", instrument_no("Motion No. 14638"), 14638)
    ok("instrument none", instrument_no(""), 0)

    ok("name honorific", clean_name("Commissioner Moore"), "Moore")
    ok("name parens", clean_name("Sue Hestor (SF Tomorrow)"), "Sue Hestor")

    ok("addr gloss", address_core("1233 Howard Street, east side between 8th and 9th Streets"),
       "1233 Howard Street")
    ok("addr between", address_core("945 Vermont Street, between 21st and 22nd Streets"),
       "945 Vermont Street")
    ok("addr aka", address_core("200 California Street (a.k.a. 201 Front Street)"),
       "200 California Street")
    ok("addr range kept", address_core("1233-1237 Howard Street"), "1233-1237 Howard Street")
    ok("addr clean", address_core("1400 Mission Street"), "1400 Mission Street")
    ok("addr format flag", address_format_ok("1233 Howard Street, east side between"), False)
    ok("addr format ok", address_format_ok("1233 Howard Street"), True)

    r = normalize_record({"speakers": [{"name": "Commissioner Moore", "stance": "oppose"},
                                       {"name": "A Smith", "stance": "support"},
                                       {"name": "B", "stance": "support"}],
                          "support_count": 99,          # model-supplied, must be discarded
                          "continued_to": "April 21, 2005", "lot_number": "009, 010",
                          "assessor_block": "0814",
                          "project_address": "1233 Howard Street, east side between 8th"})
    ok("record date", r["continued_to"], "2005-04-21")
    ok("record lots", r["lot_number"], [9, 10])
    ok("record block", r["assessor_block"], "814")
    ok("record addr", r["project_address"], "1233 Howard Street")
    # "A Smith" -> "A. Smith": a bare leading initial is canonicalised (see _INITIAL)
    ok("record names", [s["name"] for s in r["speakers"]], ["Moore", "A. Smith", "B"])
    ok("counts derived", (r["support_count"], r["oppose_count"], r["neutral_count"]), (2, 1, 0))
    ok("idempotent", normalize_record(r), r)

    bad = [c for c in cases if not c[1]]
    for name, good, got, want in cases:
        if not good:
            print(f"  FAIL {name}: got {got!r} want {want!r}")
    print(f"normalize.py: {len(cases) - len(bad)}/{len(cases)} tests pass")
    return not bad


if __name__ == "__main__":
    sys.exit(0 if _test() else 1)
