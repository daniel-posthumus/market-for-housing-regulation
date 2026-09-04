#!/usr/bin/env python3
"""
meeting_headers.py
------------------
Purpose : Build the MEETING-level table that sits above the item-level one. For every
          meeting boundary in the date gold standard, cut the header window out of the
          source document and pre-fill the meeting's own attributes — type (regular /
          special / joint / closed session), scheduled and gavel times, room, roll call,
          and staff in attendance — so they can be confirmed by hand rather than typed.
Inputs  : date_boundary_app/date_gold.db (boundaries marked by hand), raw documents under
          MFHR_DATA_ROOT/meeting_minutes/<locality>/raw/
Outputs : a `meetings` table in date_gold.db (window text + pre-filled fields + status),
          and `meetings_pilot.csv` with --export.
Author  : Dan Post
Created : 2026-08-30

Notes
-----
Why a separate level: a meeting is not an item. Time of day, who sat, who staffed it, and
whether it was a regular or a special session are properties of the hearing, shared by
every item heard at it. Recording them once per meeting and joining on the date beats
repeating them on 23,000 items — and it is the natural companion to the date stage, which
already knows exactly where each meeting starts.

The window is +/-15 NON-BLANK lines around the marked boundary, matching the line numbering
the boundary app shows. Blank lines are skipped because the 1998 HTML pages pad headers
with dozens of them; +/-15 raw lines there would capture a third of a header. At 15/15 the
window carries the gavel time in 80/80 pilot meetings, meeting type in 79/80, the roll call
in 77/80, and staff in 75/80. Widening to 20/30 only improves ABSENT (64 -> 70), and most
of those misses are meetings that simply record no absences, so the default stays at the
tighter window. Both bounds are flags.

The 15 lines BEFORE a boundary are the tail of the PREVIOUS meeting. They are kept in the
window for context, and they are the reason every field is read from the date line DOWN:
the previous meeting's last vote ("AYES: ... ABSENT: Martin") is roll-call-shaped and would
otherwise be scraped as this meeting's attendance.

Usage:
  python meeting_headers.py                 # build/refresh, report pre-fill coverage
  python meeting_headers.py --before 20 --after 30
  python meeting_headers.py --export        # write meetings_pilot.csv
  python meeting_headers.py --show 3        # print a few windows with their pre-fill
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import collections
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "date_boundary_app"))

DB = HERE / "date_boundary_app" / "date_gold.db"
CSV_OUT = HERE / "date_boundary_app" / "meetings_pilot.csv"

# ── the meeting-level schema ──────────────────────────────────────────────────
MEETING_SCHEMA = [
    {"name": "meeting_type", "type": "enum",
     "choices": ["regular", "special", "joint", "closed_session", "other"],
     "help": "As the header names it; 'joint' wins when two bodies sit together"},
    {"name": "meeting_time", "type": "scalar",
     "help": "Time of day. The header's gavel time, or the called-to-order time when the "
             "header gives none"},
    {"name": "presiding", "type": "scalar",
     "help": "Surname of the member marked President on the roster; falls back to the "
             "called-to-order line where the roster gives no title"},
    {"name": "location", "type": "scalar",
     "help": "Which building, at address level — room numbers are not recorded"},
    {"name": "present", "type": "list", "help": "Commissioners present"},
    {"name": "absent", "type": "list", "help": "Commissioners absent"},
    {"name": "staff", "type": "list", "help": "Staff in attendance"},
    {"name": "joint_body", "type": "scalar", "help": "The other body, when it is a joint meeting"},
    {"name": "joint_body_present", "type": "list", "help": "That body's members present"},
    {"name": "notes", "type": "text", "help": "Anything the fields do not hold"},
]
MEETING_FIELDS = [f["name"] for f in MEETING_SCHEMA]

# ── pre-fill patterns ─────────────────────────────────────────────────────────
# "Special Off-Site Meeting" and "Special Joint Hearing" both occur, so allow a couple of
# qualifiers between the type word and the noun.
TYPE_RE = re.compile(r"(?i)\b(regular|special|joint|closed)\s+(?:[A-Za-z-]+\s+){0,2}"
                     r"(?:meeting|session|hearing)")
JOINT_RE = re.compile(r"(?i)\bjoint\b")
# A joint sitting is often not called one: 20180412 heads "SAN FRANCISCO / PLANNING
# COMMISSION / AND / BUILDING INSPECTION COMMISSION" and then says "Special Meeting". Two
# bodies named in the header is the reliable tell.
TWO_BODIES_RE = re.compile(
    r"(?i)PLANNING COMMISSION\s*\n\s*(?:AND|&)\s*\n\s*([A-Z][A-Z ]{6,}COMMISSION)")
TIME_RE = re.compile(r"\b(\d{1,2}:\d{2})\s*([APap])\.?\s?[Mm]\.?")
ORDER_RE = re.compile(r"(?i)CALLED TO ORDER\s*(?:BY\s+(?P<who>[^,\n]{0,60}?))?\s*"
                      r"(?:AT\s+(?P<at>\d{1,2}:\d{2}\s*[APap]\.?\s?[Mm]\.?))")
WEEKDAY = r"(?:MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY)"

# ── venue ────────────────────────────────────────────────────────────────────
# Composing the location by gluing together whatever a ROOM regex and a BUILDING regex
# each matched produced strings like "428 — War Memorial Building, 401 Van — Ness Avenue":
# the room prefix lost, the street split across the source's line wrap, and a document
# title dragged in. The venue is instead read as a block of lines around the date, cleaned,
# and recomposed into one canonical form per known venue.
ROOM_RE = re.compile(r"(?i)\b(?:room\s+\d{2,4}|chambers?)\b")
STREET_RE = re.compile(r"(?i)\b\d+\s+[A-Z0-9][A-Za-z0-9.'\- ]*"
                       r"(?:avenue|ave|street|st|boulevard|blvd|place|plaza|way|road)\b")
ZIP_RE = re.compile(r"(?i)\bCA\s+9\d{4}")
REMOTE_RE = re.compile(r"(?i)remote hearing|video and teleconferenc|teleconference only")
# Lines that sit in the header band but name the document, not the place.
VENUE_JUNK_RE = re.compile(
    r"(?i)^\s*(?:(?:special\s+|regular\s+|joint\s+)*(?:meeting\s+)?(?:minutes|calendar|"
    r"agenda)(?:\s+of\s+meeting)?|hearing minutes|san francisco|planning commission|"
    r"and|city and county[^\n]*|\d{1,2}:\d{2}[^\n]*|[a-z ]*meeting)\s*$")

# The two standing venues, spelled once each. The source wraps both addresses across lines,
# so reassembling them from the page is strictly worse than naming them.
WAR_MEMORIAL = "War Memorial Building, 401 Van Ness Avenue"
CITY_HALL = "City Hall, 1 Dr. Carlton B. Goodlett Place"

VENUE_BAND_BEFORE, VENUE_BAND_AFTER = 6, 6


def _venue_lines(window: str, date_line: str) -> list[str]:
    """Candidate venue lines from the band around the date line, with the source's line
    wraps repaired ("401 Van" + "Ness Avenue", "1 Dr. Carlton B. Goodlett" + "Place")."""
    lines = [l.strip() for l in window.split("\n")]
    try:
        i = lines.index(date_line.strip())
    except ValueError:
        i = len(lines) // 2
    band = lines[max(0, i - VENUE_BAND_BEFORE): i + VENUE_BAND_AFTER + 1]

    # A line that continues the address above it: lower-cased, or a short fragment that is
    # not itself a header. Without the second guard "City Hall, 1 Dr. Goodlett Place" absorbs
    # the "Thursday, March" line below it and stops looking like a venue.
    not_continuation = re.compile(
        r"(?i)^\s*(?:" + WEEKDAY + r"|\d{1,2}:\d{2}|\d{1,2},\s*\d{4}|"
        r"(?:January|February|March|April|May|June|July|August|September|October|November|"
        r"December)\b)")

    joined, buf = [], ""
    for ln in band:
        if not ln:
            continue
        if VENUE_JUNK_RE.match(ln):
            if buf:
                joined.append(buf)     # flush, never discard: a junk line ends the venue,
            buf = ""                   # it does not erase what came before it
            continue
        if (buf and not ROOM_RE.search(ln) and not not_continuation.match(ln)
                and (ln[:1].islower() or len(ln.split()) <= 2)):
            buf = f"{buf} {ln}"
        else:
            if buf:
                joined.append(buf)
            buf = ln
    if buf:
        joined.append(buf)
    return joined


def _location(window: str, date_line: str) -> str:
    """The venue at ADDRESS level: which building, not which room.

    Room and chamber numbers are dropped deliberately. What matters analytically is whether
    the Commission sat somewhere other than its usual home — an off-site hearing at a high
    school, a remote hearing — not whether it used Room 400 or Room 428 inside the same
    building. Keeping the room number only fragmented one venue into five spellings.
    """
    cand = _venue_lines(window, date_line)
    blob = " \n".join(cand)

    if REMOTE_RE.search(blob):
        return "Remote hearing"
    if "WAR MEMORIAL" in blob.upper():
        return WAR_MEMORIAL
    if "CITY HALL" in blob.upper() or "GOODLETT" in blob.upper():
        return CITY_HALL

    # somewhere else: name it and give its street address
    name = next((_clean(l) for l in cand
                 if re.search(r"(?i)school|auditorium|center|centre|library|theat|hall\b", l)
                 and not STREET_RE.search(l)), "")
    name = re.sub(r"(?i)\s*[-–]\s*(auditorium|gymnasium|cafeteria|multipurpose room).*$", "", name)
    street = next((_clean(m.group(0)) for l in cand for m in [STREET_RE.search(l)] if m), "")
    return ", ".join(x for x in (name, street) if x) or _clean(blob.split("\n")[0])


# The label may name its body ("PLANNING COMMISSIONERS PRESENT", "COMMISSIONER ABSENT") and
# the colon may sit on the following line, so the colon is optional at end of line.
PRESENT_RE = re.compile(r"(?i)(?:[A-Z]+\s+)?COMMISSIONERS?\s+PRESENT\s*:?|(?<![A-Z])PRESENT\s*:")
ABSENT_RE = re.compile(r"(?i)(?:[A-Z]+\s+)?COMMISSIONERS?\s+ABSENT\s*:?|(?<![A-Z])ABSENT\s*:")
# "STAFF IN ATTENDANCE:" is printed on one line in most eras and split as "STAFF IN" /
# "ATTENDANCE:" in others, so either half can carry the label.
# The label is broken across lines three different ways across the eras:
#   "STAFF IN ATTENDANCE:"      (one line)
#   "STAFF IN" / "ATTENDANCE:"  (2011)
#   "STAFF" / "IN ATTENDANCE:"  (2000)
# so either half must be able to carry it.
STAFF_RE = re.compile(r"(?i)STAFF\s+IN\s+ATTENDANCE\s*:?|^\s*(?:IN\s+)?ATTENDANCE\s*:"
                      r"|STAFF\s*:|^\s*STAFF\s*$")
OTHER_BODY_RE = re.compile(
    r"(?i)^\s*((?:REDEVELOPMENT AGENCY|HISTORIC PRESERVATION|RECREATION AND PARK|"
    r"BOARD OF SUPERVISORS|TRANSPORTATION AUTHORITY|BUILDING INSPECTION)[A-Z ]*)\s*:?\s*$", re.M)

# Where a roll call stops. Looking ahead for "an ALL-CAPS label ending in a colon" is not
# enough: the line that ends the ABSENT list is usually "THE MEETING WAS CALLED TO ORDER BY
# PRESIDENT CHINCHILLA AT 1:30 P.M.", which has no colon and which 2001-era pages wrap
# mid-phrase, and the line that ends STAFF IN ATTENDANCE is often a bare section letter, "A.".
STOP_RE = re.compile(
    r"(?i)^\s*(?:(?:[A-Z]+\s+)?COMMISSIONERS?\s+(?:PRESENT|ABSENT|EXCUSED|RECUSED)\s*:?"
    r"|(?:PRESENT|ABSENT|EXCUSED|RECUSED)\s*:"
    # "STAFF" alone, because the label wraps as "STAFF / IN ATTENDANCE:" (2001)
    r"|STAFF\b|THE MEETING WAS|MEETING WAS CALLED|^\s*THE\s*$"
    r"|SPEAKER KEY|SPEAKERS\s*:|ADJOURN"
    # a section letter, whether alone on its line or heading a run of text:
    # "A." and "A. CONSIDERATION OF ITEMS PROPOSED FOR CONTINUANCE" both end a list
    # A section letter heads a RUN OF CAPITALS ("A. CONSIDERATION OF ITEMS"); requiring
    # two capitals keeps it from firing on an initial in a roll call ("S. Lee; Alexander"),
    # which silently emptied two meetings' attendance.
    # (?-i: ) because the pattern's global (?i) would otherwise let "S. Lee" satisfy
    # "[A-Z]{2,}" via "Le" — which silently emptied two meetings' attendance.
    r"|(?-i:[A-Z]\.(?:\s*$|\s+[A-Z]{2,}))|\d+\.\s"
    # the name of another body, which follows the first roll call of a joint meeting
    r"|(?:BUILDING INSPECTION|HISTORIC PRESERVATION|REDEVELOPMENT AGENCY|RECREATION AND PARK)"
    r"|(?-i:[A-Z][A-Z /&-]{3,}:))")

# Roles that arrive as their own comma-separated token and belong to the name before them:
# "Gerald G. Green, Director of Planning" must not become two staff members.
ROLE_TAIL_RE = re.compile(
    r"(?i)^(?:acting\s+)?(?:deputy\s+)?(?:director(?:\s+of\s+[a-z ]+)?|zoning administrator|"
    r"city attorney|deputy city attorney|commission secretary|transcription secretary|"
    r"secretary|planning director|chief[a-z ]*)$")

# A name printed in block capitals ("WILLIAMS", "Robert PASSMORE") — the same person the
# rest of the corpus writes in title case.
def _fix_caps(tok: str) -> str:
    out = []
    for w in tok.split():
        if w.isupper() and len(w) > 1 and w.isalpha():
            w = w.title()
            # "MCGARRY".title() is "Mcgarry"; the corpus writes "McGarry"
            w = re.sub(r"^(Mc|Mac)([a-z])", lambda m: m.group(1) + m.group(2).upper(), w)
        out.append(w)
    return " ".join(out)

# A bare role that arrives as its own comma-separated token ("Hector Chinchilla, President").
ROLE_RE = re.compile(r"(?i)^(?:acting\s+)?(?:president|vice[- ]president|chair(?:person)?|"
                     r"commissioner|secretary)$")


# ── who presided ─────────────────────────────────────────────────────────────
# Read from the title beside a member's name, not from "X called the meeting to order":
# the roster always marks the President, while the called-to-order line is missing from a
# number of meetings. Three layouts occur and all three are handled:
#   1998 roll call   "PRESENT: Hector Chinchilla, President, Dennis Antenore, ..."
#   1999 masthead    "Hector Chinchilla, / President / Anita Theoharis, Vice President"
#   2001+ roll call  bare surnames, President named only in the called-to-order line,
#                    whose name often wraps ("BY PRESIDENT BRADFORD / BELL AT 1:35 p.m.")
# The name may precede or follow the title, so both orders are matched — on a
# newline-collapsed copy of the window, since every one of these forms wraps somewhere.
# A capitalised word that is not part of the surrounding boilerplate. Without this guard
# the 1999 masthead ("... Regular Meeting Hector Chinchilla, President") yields
# "Meeting Hector Chinchilla".
_NOT_NAME = (r"(?!(?:Meeting|Meetings|Regular|Special|Joint|Closed|Session|Minutes|Calendar|"
             r"Commission|Commissioners|Hearing|Present|Absent|Staff|Room|Chambers|"
             r"Attendance|AT|AM|PM|The|And)\b)")
_WORD = rf"{_NOT_NAME}[A-Z][\w.'\-]*"
# "<Name>, President" — a comma or dash directly before the title, which is what keeps
# "Anita Theoharis, Vice President" from matching (the "Vice" sits in between).
NAME_THEN_TITLE = re.compile(rf"({_WORD}(?:\s+{_WORD}){{0,2}})\s*[,\-–]\s*President\b")
# "...BY PRESIDENT BRADFORD BELL AT 1:35" — stop before the "AT <time>" tail.
TITLE_THEN_NAME = re.compile(
    rf"(?i)\bPRESIDENT\s+((?!AT\b){_WORD}(?:\s+(?!AT\b){_WORD}){{0,2}})")


HONORIFIC_RE = re.compile(r"(?i)^(?:rev|dr|mr|mrs|ms|hon)\.?$")


def _surname(name: str, roll: list[str]) -> str:
    """Reduce a presiding name to the surname the minutes themselves use.

    Naively taking the last word is wrong twice over: Shelley Bradford Bell's surname is
    "Bradford Bell", and the called-to-order line already gives surname-only forms. The
    roll call settles it — if the name appears there in FULL, strip one leading given name;
    if it is only a suffix of a roll-call entry, it is already the surname.
    """
    toks = [t for t in name.split() if t]
    while toks and (HONORIFIC_RE.match(toks[0]) or re.fullmatch(r"[A-Z]\.?", toks[0])):
        toks = toks[1:]
    if len(toks) < 2:
        return " ".join(toks) or name
    full = " ".join(toks)
    # already surname-form: it is the tail of a longer roll-call entry
    if any(e.lower().endswith(full.lower()) and len(e) > len(full) for e in roll):
        return full
    # a complete roll-call entry: the leading token is the given name
    if any(e.lower() == full.lower() for e in roll):
        return " ".join(toks[1:])
    return full


def _presiding(window: str) -> str:
    flat = re.sub(r"\s+", " ", window)
    if m := NAME_THEN_TITLE.search(flat):
        return _clean(m.group(1))
    # "...BY ACTING CHAIR FONG AT 12:20 P.M." — where no president is seated the acting
    # chair presides, and the minutes say so in the same sentence.
    if m := re.search(r"(?i)CALLED TO ORDER BY\s+(?:ACTING\s+)?(?:VICE[\s-]?)?"
                      r"(?:PRESIDENT|CHAIR(?:PERSON)?)\s+"
                      rf"((?!AT\b){_WORD}(?:\s+(?!AT\b){_WORD}){{0,2}})", flat):
        return _clean(m.group(1)).title() if m.group(1).isupper() else _clean(m.group(1))
    if m := TITLE_THEN_NAME.search(flat):
        return _clean(m.group(1)).title() if m.group(1).isupper() else _clean(m.group(1))
    return ""


def _capture(window: str, label: re.Pattern, max_lines: int = 4) -> str:
    """Text following a label, continued across wrapped lines until a stop marker."""
    lines = window.split("\n")
    for i, ln in enumerate(lines):
        m = label.search(ln)
        if not m:
            continue
        out = [ln[m.end():].lstrip(": ")]
        for nxt in lines[i + 1: i + 1 + max_lines]:
            if not nxt.strip() or STOP_RE.search(nxt):
                break
            out.append(nxt)
        return " ".join(out)
    return ""


def _clean(v: str) -> str:
    return re.sub(r"\s+", " ", (v or "")).strip(" ,;:.")


def _people(v: str, join_roles: bool = False) -> list[str]:
    """Split a roll call or staff line into people.

    join_roles is for STAFF IN ATTENDANCE, where a job title separated by a comma belongs
    to the name before it rather than being a person of its own.
    """
    v = _clean(v)
    if not v or v.lower() in ("none", "n/a"):
        return []
    parts = [p.strip(" .") for p in re.split(r"[,;]| and (?=[A-Z])", v)]
    out: list[str] = []
    for p in parts:
        if not p or ROLE_RE.match(p):
            continue                                  # a bare "President" is not a person
        if join_roles and out and ROLE_TAIL_RE.match(p):
            dash = "–" if "–" in v else "-"
            out[-1] = f"{out[-1]} {dash} {p}"         # "Green" + "Director of Planning"
            continue
        # "Hector Chinchilla, President" arrives as two tokens; "Hector Chinchilla -
        # President" arrives as one. Neither form should put a title in the list.
        p = re.sub(r"\s*[-–]\s*(?:acting\s+)?(?:president|vice[- ]president|"
                   r"chair(?:person)?|secretary)\s*$", "", p, flags=re.I).strip()
        # "President Olague", "VP Miguel" — the title is not part of the name
        p = re.sub(r"(?i)^(?:commissioner|comm\.?|president|vice[\s-]?president|vp|chair"
                   r"(?:person)?|acting\s+president)\s+", "", p).strip()
        # "Tierney, Ed.D" / "Melara, M.S.W." split into a name and a degree; drop the degree
        if re.fullmatch(r"(?:[A-Za-z]\.){1,4}[A-Za-z]?\.?|(?i:Ed\.?D|Ph\.?D|M\.?D|J\.?D|"
                        r"M\.?S\.?W|M\.?P\.?H|R\.?N|AICP|Esq)\.?", p):
            continue
        if p.lower() in ("present", "absent", "none"):
            continue
        # normalise the SPACING around a name/role dash ("Badiner -Zoning Administrator")
        # while keeping the character the source used — the corpus mixes hyphen and en dash,
        # and rewriting one as the other is a difference with no meaning. Requires
        # whitespace on a side, so hyphenated surnames ("Cleveland-Knowles") are untouched.
        p = re.sub(r"\s+([-–])\s*|\s*([-–])\s+", lambda m: f" {m.group(1) or m.group(2)} ", p)
        p = re.sub(r"\s*[-–]\s*[-–]\s*", " - ", p)     # "Boyajian - - Deputy" -> one dash
        p = re.sub(r"\s+=\s+", " - ", p)                # OCR reads the role dash as "="
        p = _fix_caps(p)
        if p:
            out.append(p)
    return out


def _migrate_keys(rec: dict) -> dict:
    """Carry a hand-confirmed record onto the current schema without changing what it says.

    2026-09-02: `scheduled_time` and `called_to_order_time` collapsed into `meeting_time`
    (they differ by minutes and the distinction earned nothing), and `adjournment_time` was
    dropped.
    """
    out = dict(rec)
    if "meeting_time" not in out:
        out["meeting_time"] = out.get("scheduled_time") or out.get("called_to_order_time") or ""
    for gone in ("scheduled_time", "called_to_order_time", "adjournment_time"):
        out.pop(gone, None)
    return {f: out.get(f, [] if sch["type"] == "list" else "")
            for f, sch in zip(MEETING_FIELDS, MEETING_SCHEMA)}


def _planning_block(text: str) -> str:
    """The Planning Commission's own portion of a joint meeting's header, from its roll
    call to the next body's."""
    m = re.search(r"(?i)(?:PLANNING\s+)?COMMISSIONERS?\s+PRESENT", text)
    if not m:
        return ""
    rest = text[m.start():]
    nxt = OTHER_BODY_RE.search(rest, 1)
    return rest[:nxt.start()] if nxt else rest


def _presiding_joint(body: str, planning_present: list[str]) -> str:
    """Who presided, for a joint meeting: ALWAYS this Commission's president.

    A joint session is gavelled by whichever body hosts it — on 2011-03-10 the Public
    Health Commission's president called it to order — but `presiding` in this dataset means
    the Planning Commission's president, because the Planning Commission is the body whose
    decisions are the unit of analysis. Two routes, in order:

      1. a title beside a name inside Planning's own roll call ("President Olague, VP
         Miguel, ...");
      2. the called-to-order name, accepted only if that person sat with the Planning
         Commission that day.

    If neither holds, the field is left EMPTY rather than filled with the other body's
    president — a blank is a question for a human, a wrong name is a silent error.
    """
    block = _planning_block(body)
    if block:
        if m := NAME_THEN_TITLE.search(re.sub(r"\s+", " ", block)):
            v = _clean(m.group(1))
            return v.title() if v.isupper() else v
        if m := re.search(r"(?i)\bPRESIDENT\s+" + rf"({_WORD}(?:\s+{_WORD}){{0,2}})",
                          re.sub(r"\s+", " ", block)):
            v = _clean(m.group(1))
            return v.title() if v.isupper() else v
    who = _presiding(body)
    surnames = {p.split()[-1].lower() for p in planning_present if p}
    return who if who and who.split()[-1].lower() in surnames else ""


# The staff roll ends in one of two ways, and both are reliable enough to cut on.
# The Commission Secretary (or Acting Commission Secretary) is always listed LAST, so the
# list ends there; and where no secretary is named, the following agenda section announces
# itself ("CONSIDERATION OF ITEMS PROPOSED FOR CONTINUANCE", "SPEAKER KEY"). Without these
# the capture ran on into the agenda, which was the single largest source of staff error.
SECRETARY_END_RE = re.compile(r"(?i)(?:acting\s+)?commission\s+secretary\.?")
AGENDA_AFTER_STAFF_RE = re.compile(
    r"(?i)\b(?:consideration\s+of\s+items|consideration\b|speaker\s+key|"
    r"items?\s+(?:to\s+be|proposed\s+for)\s+continu)")


def _staff_text(text: str) -> str:
    """The staff roll, cut at whichever endpoint comes first."""
    if not text:
        return ""
    raw = _capture(text, STAFF_RE, max_lines=12)
    if not raw:
        return ""
    cut = len(raw)
    if m := AGENDA_AFTER_STAFF_RE.search(raw):
        cut = m.start()
    if m := SECRETARY_END_RE.search(raw):
        if m.end() <= cut:
            cut = m.end()
    return raw[:cut]


def prefill(window: str, date_line: str, extended: str = "") -> dict:
    """Heuristic meeting-level pre-fill from the header window. Every value is meant to be
    confirmed by a human — this exists so confirming is a glance, not a retype.

    The window straddles two meetings, so which HALF a field is read from matters. The lines
    above the date line are the previous meeting's tail, and they carry roll-call-shaped text
    of their own: the last item's vote reads "AYES: ... ABSENT: Martin", which a scan from
    the top of the window would happily take as this meeting's absences. Everything about
    THIS meeting is therefore read from the date line down; only the adjournment time is read
    from above it, because that is the previous meeting's ending by construction.
    """
    rec = {f: ("" if s["type"] != "list" else []) for f, s in zip(MEETING_FIELDS, MEETING_SCHEMA)}
    if date_line and date_line in window:
        head, _, body = window.partition(date_line)
    else:
        head, body = "", window

    m = TYPE_RE.search(window)
    kind = (m.group(1).lower() if m else "")
    two = TWO_BODIES_RE.search(window)
    if (JOINT_RE.search(body) or two
            or len(re.findall(r"(?i)(?:COMMISSIONERS\s+)?PRESENT\s*:", body)) > 1):
        kind = "joint"
    elif kind == "closed":
        kind = "closed_session"
    rec["meeting_type"] = kind if kind in ("regular", "special", "joint", "closed_session") else ""

    # Time of day: the header's own gavel time, which is the first time after the date
    # line. The two differ by a few minutes at most ("1:30 PM" scheduled, called to order at
    # 1:38), which is not a distinction worth a second field, so the called-to-order time
    # only fills in when the header prints no time at all.
    o = ORDER_RE.search(body)
    if t := TIME_RE.search(body):
        rec["meeting_time"] = f"{t.group(1)} {t.group(2).upper()}M"
    elif o:
        rec["meeting_time"] = _clean(o.group("at")).upper().replace(".", "")

    rec["location"] = _location(window, date_line)

    # The roll call is normally inside the header window, but the 1999 layout puts the
    # masthead there and the actual "PRESENT:" line some 190 lines further down, after the
    # meeting-procedures boilerplate. `extended` is this meeting's full text up to the next
    # meeting, searched only where the window came up empty — so the usual case is
    # unaffected and cannot pick up a neighbouring meeting's roll call.
    def field(label, **kw):
        v = _people(_capture(body, label), **kw)
        return v or (_people(_capture(extended, label), **kw) if extended else v)

    rec["present"] = field(PRESENT_RE)
    rec["absent"] = field(ABSENT_RE)
    rec["staff"] = _people(_staff_text(body) or _staff_text(extended), join_roles=True)
    # A joint meeting is gavelled by whichever body hosts it, but for this dataset
    # `presiding` always means the PLANNING COMMISSION's president — the Commission whose
    # decisions are the unit of analysis. On 2011-03-10 the Public Health Commission's
    # president called the joint session to order; the field records Olague, not Tierney.
    # So the search is confined to this Commission's own roll-call block.
    if rec["meeting_type"] == "joint":
        rec["presiding"] = _presiding_joint(body, rec["present"])
    else:
        rec["presiding"] = _presiding(window)

    if rec["meeting_type"] == "joint":
        if two:
            rec["joint_body"] = re.sub(r"(?i)^department of\s+", "",
                                       _clean(two.group(1))).title()
        elif ob := OTHER_BODY_RE.search(window):
            rec["joint_body"] = re.sub(r"(?i)^department of\s+", "",
                                       _clean(ob.group(1))).title()
        rolls = list(PRESENT_RE.finditer(body))
        if len(rolls) > 1:
            rec["joint_body_present"] = _people(_capture(body[rolls[-1].start():], PRESENT_RE))

    return rec


# ── window cutting ────────────────────────────────────────────────────────────
# The first agenda section ends the header region. Past it, "ABSENT:" and "AYES:" are the
# roll of a VOTE on an item, not the attendance of the meeting, and reading them as
# attendance is exactly the cross-contamination the window was meant to avoid.
AGENDA_START_RE = re.compile(r"^\s*(?:[A-Z]\.\s+[A-Z]|\d+[a-z]?\.\s+\S|"
                             r"CONSIDERATION OF ITEMS|ITEMS? (?:TO BE|PROPOSED FOR))")


# The date stage and this stage need different anchors, and only hand-marking hid it.
# For a DATE, the page title is a perfectly good anchor — it states the date correctly, which
# is why dates score 100%. For the meeting's ATTRIBUTES the title is useless: it sits in the
# navigation chrome, hundreds of lines above the header block that names the room, the gavel
# time and the roll. A human marking boundaries always marked the body header, so every gold
# meeting was anchored correctly and this gap stayed invisible until the extractor was run
# over meetings nobody had marked.
HEADER_BLOCK_RE = re.compile(
    r"(?i)^\s*(?:" + WEEKDAY + r"[,\s]|(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{1,2})")


def snap_to_header(lines: list[str], line_no: int, stop: int | None = None) -> int:
    """Move a boundary line forward to the meeting's own header block.

    Returns the first line at or after `line_no` that reads like a header — a weekday or a
    month-day — and is followed within a short run by a gavel time or a roll call. If no
    such line exists the original is kept, so a boundary already on the header stays put.
    """
    end = min(stop if stop is not None else len(lines), len(lines))
    nb = [i for i in range(line_no, end) if lines[i].strip()]
    best, best_score = line_no, 0
    for k, i in enumerate(nb[:400]):
        txt = lines[i].strip()
        if not HEADER_BLOCK_RE.match(txt):
            continue
        near = " ".join(lines[j].strip() for j in nb[k + 1: k + 7])
        far = " ".join(lines[j].strip() for j in nb[k + 1: k + 16])
        before = " ".join(lines[j].strip() for j in nb[max(0, k - 6): k])
        # The breadcrumb also carries a date, so a bare date is not enough: the real header
        # is the one seated in a room, gavelled at a time and followed by a roll call.
        score = (2 * bool(re.match(r"(?i)^" + WEEKDAY, txt))
                 + 2 * bool(TIME_RE.search(near))
                 + 2 * bool(PRESENT_RE.search(far))
                 + 2 * bool(ROOM_RE.search(before) or re.search(r"(?i)city hall|war memorial", before)))
        if score > best_score:
            best, best_score = i, score
    return best if best_score >= 4 else line_no


def _extended_for(lines: list[str], line_no: int, all_marks: list[int]) -> str:
    """This meeting's HEADER REGION: from its header down to the first agenda item (or the
    next meeting, whichever comes first). Wide enough to reach a roll call the 1999 layout
    prints ~190 lines below the masthead; narrow enough to exclude item votes."""
    later = [m for m in sorted(all_marks) if m > line_no]
    end = later[0] if later else len(lines)
    for i in range(line_no + 3, end):
        if AGENDA_START_RE.match(lines[i]):
            end = i
            break
    return "\n".join(l.strip() for l in lines[line_no:end])


def window_for(lines: list[str], line_no: int, before: int, after: int) -> tuple[str, str]:
    """(window_text, the marked line itself), measured in NON-BLANK lines."""
    nb = [i for i, x in enumerate(lines) if x.strip()]
    if not nb:
        return "", ""
    pos = nb.index(line_no) if line_no in nb else min(
        range(len(nb)), key=lambda k: abs(nb[k] - line_no))
    idx = nb[max(0, pos - before): pos + after + 1]
    return "\n".join(lines[i].strip() for i in idx), lines[nb[pos]].strip()


def init_db(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS meetings(
        source_file TEXT, line_no INTEGER, meeting_date TEXT,
        origin TEXT DEFAULT 'gold',      -- gold | detected (found by the date stage only)
        window_text TEXT, date_line TEXT,
        data TEXT, status TEXT DEFAULT 'todo', updated_at TEXT DEFAULT '',
        PRIMARY KEY(source_file, line_no));
    """)
    con.commit()


def build(before: int, after: int, con) -> tuple[int, int, Counter]:
    """Refresh windows + pre-fill. Never overwrites a meeting already confirmed."""
    # imported here, not at module scope: the boundary app imports this module for
    # MEETING_SCHEMA, and a module-level `import app` would close the cycle.
    import app as BA
    import assign_meeting_dates as AD

    marks = list(con.execute(
        "SELECT source_file, line_no, meeting_date FROM boundaries ORDER BY source_file, line_no"))
    added = refreshed = 0
    cov = Counter()
    cache: dict[str, list[str]] = {}

    # a meeting the date stage finds but the hand-marking missed is still a meeting; carry
    # it in so it can be labelled too, flagged by origin so the gold set stays honest
    detected: list[tuple[str, int, str]] = []
    by_doc: dict[str, list] = {}
    for src, ln, d in marks:
        by_doc.setdefault(src, []).append((ln, d))
    for src in by_doc:
        path = BA.doc_path(src)
        if not path:
            continue
        text = BA.doc_text(path)
        lines = text.split("\n")
        cache[src] = lines
        starts, off = [], 0
        for ln in lines:
            starts.append(off); off += len(ln) + 1
        # A detected header counts as "not hand-marked" only if no mark of the SAME DATE
        # sits within POS_TOL characters — the same rule the scorer uses. Comparing line
        # numbers instead would call every page-title anchor unmatched, since the title sits
        # at line 0 while the mark is on the body header two hundred lines below it.
        marked = [(starts[l] if l < len(starts) else 0, d) for l, d in by_doc[src]]
        for hoff, hdate in AD.header_dates(text):
            if any(d == hdate and abs(off - hoff) <= BA.POS_TOL for off, d in marked):
                continue
            hline = max(i for i, st in enumerate(starts) if st <= hoff)
            detected.append((src, hline, hdate))

    bounds: dict[str, list[int]] = {}
    for s_, l_, _d in marks:
        bounds.setdefault(s_, []).append(l_)
    for s_, l_, _d in detected:
        bounds.setdefault(s_, []).append(l_)

    for src, ln, mdate, origin in ([(s, l, d, "gold") for s, l, d in marks]
                                   + [(s, l, d, "detected") for s, l, d in detected]):
        if src not in cache:
            path = BA.doc_path(src)
            if not path:
                continue
            cache[src] = BA.doc_text(path).split("\n")
        win, date_line = window_for(cache[src], ln, before, after)
        rec = prefill(win, date_line, extended=_extended_for(cache[src], ln, bounds.get(src, [])))
        for f in ("meeting_type", "meeting_time", "present", "staff", "location",
                  "presiding", "absent"):
            if rec[f]:
                cov[f] += 1

        row = con.execute("SELECT status, data FROM meetings WHERE source_file=? AND line_no=?",
                          (src, ln)).fetchone()
        if row and row[0] == "done":
            # Confirmed by hand: never overwrite the values, but do bring the KEYS forward
            # when the schema changes, so a confirmed meeting does not keep carrying fields
            # that no longer exist.
            kept = _migrate_keys(json.loads(row[1]) if row[1] else {})
            con.execute("UPDATE meetings SET data=?, window_text=?, date_line=? "
                        "WHERE source_file=? AND line_no=?",
                        (json.dumps(kept, ensure_ascii=False), win, date_line, src, ln))
            continue
        if row:
            con.execute("UPDATE meetings SET window_text=?, date_line=?, data=?, "
                        "meeting_date=?, origin=? WHERE source_file=? AND line_no=?",
                        (win, date_line, json.dumps(rec, ensure_ascii=False), mdate, origin,
                         src, ln))
            refreshed += 1
        else:
            con.execute("INSERT INTO meetings(source_file,line_no,meeting_date,origin,"
                        "window_text,date_line,data) VALUES(?,?,?,?,?,?,?)",
                        (src, ln, mdate, origin, win, date_line,
                         json.dumps(rec, ensure_ascii=False)))
            added += 1
    con.commit()
    return added, refreshed, cov


def name_reducer(records) -> "callable":
    """Build the corpus-wide name reducer from a collection of records.

    Factored out so the pipeline and the gold-set scorer share one implementation: when the
    scorer kept its own copy, the two drifted and the reported agreement measured the copy.
    """
    def strip_honorific(toks: list[str]) -> list[str]:
        while toks and HONORIFIC_RE.match(toks[0]):
            toks = toks[1:]
        return toks

    # A surname counts as shared only when each claimant is seen more than once. A single
    # malformed line is otherwise enough to invent a second person: 2005-08-04 prints
    # "S. Lee; Alexander Hughes, W. Lee" — a missing comma — and that lone "Alexander
    # Hughes" made "Hughes" look ambiguous, which stopped "Kevin Hughes" reducing to
    # "Hughes" in twenty-one other meetings. Ambiguity has to be evidenced, not inferred
    # from one occurrence.
    seen: dict[tuple[str, str], int] = collections.Counter()
    full_forms: dict[tuple[str, str], collections.Counter] = {}
    for rec in records:
        for f in ("present", "absent"):
            for nm in rec.get(f) or []:
                toks = strip_honorific(nm.split())
                if len(toks) > 1:
                    sur, ini = toks[-1].lower(), toks[0][:1].upper()
                    seen[(sur, ini)] += 1
                    if len(toks[0].rstrip(".")) > 1:
                        full_forms.setdefault((sur, ini), collections.Counter())[" ".join(toks)] += 1
    # One person printed two ways is not two people. "Shelley Bradford Bell" and "Bradford
    # Bell" share a surname and differ only in whether the given name was printed, so the
    # shorter is a SUFFIX of the longer — and treating them as separate claimants made
    # "Bell" look ambiguous corpus-wide, which stopped it reducing at all. Suffix forms are
    # folded into the longer name's initial before ambiguity is judged. "Sue Lee" is not a
    # suffix of "William L. Lee", so the genuinely shared surname survives this.
    forms_by_sur: dict[str, set[str]] = {}
    for (sur, ini), cnt in list(full_forms.items()):
        for form in cnt:
            forms_by_sur.setdefault(sur, set()).add(form)
    alias: dict[tuple[str, str], str] = {}
    for sur, forms in forms_by_sur.items():
        for short in forms:
            for long in forms:
                if short != long and long.lower().endswith(" " + short.lower()):
                    alias[(sur, short.split()[0][:1].upper())] = long.split()[0][:1].upper()

    variants: dict[str, set[str]] = {}
    for (sur, ini), n in seen.items():
        if n > 1:
            variants.setdefault(sur, set()).add(alias.get((sur, ini), ini))
    shared = {k for k, v in variants.items() if len(v) > 1}

    def reduce(nm: str) -> str:
        toks = strip_honorific(nm.split())
        if not toks:
            return nm
        # A hyphenated commissioner surname is one person under either spelling:
        # "Bradford-Bell" and "Bell" are Shelley Bradford Bell. Commissioners are the only
        # names reduced here (staff keep their full printed form), and this roster has a
        # single hyphenated surname, so splitting on the hyphen is safe and makes the two
        # spellings agree.
        if "-" in toks[-1]:
            toks = toks[:-1] + toks[-1].split("-")
        if len(toks) < 2:
            return toks[-1]
        sur = toks[-1].lower()
        if sur not in shared:
            return toks[-1]
        cands = full_forms.get((sur, toks[0][:1].upper()))
        return cands.most_common(1)[0][0] if cands else " ".join(toks)

    return reduce


# Sue Lee and William L. Lee both sat, so a bare "Lee" is ambiguous — but not unresolvable.
# Sue Lee served as president, so the Lee who called a meeting to order is Sue Lee, and a
# lone Lee who did not is William L. Lee. This resolves the printings that give neither a
# given name nor an initial.
LEE_PRESIDENT = "Sue Lee"
LEE_OTHER = "William L. Lee"


def _resolve_bare_lee(rec: dict) -> None:
    """Separate the two Lees where the meeting says enough to do it.

    Sue Lee served as president, so a Lee who called a meeting to order is Sue. Beyond that
    the two partition within a meeting: a roll naming two bare Lees names both, and if one
    Lee is identified on one side of the roll, a bare Lee on the other side is the other
    person. Where the meeting says none of that, the name is LEFT as "Lee" rather than
    guessed — an earlier version assigned every unresolved case to William, which put his
    absence rate at 9% against Sue's 0.4% purely as an artefact of the tie-break.
    """
    presiding = (rec.get("presiding") or "").strip()
    present = rec.get("present") or []
    absent = rec.get("absent") or []
    bare = lambda lst: [i for i, n in enumerate(lst) if n.strip().lower() == "lee"]

    # both Lees named in one roll: the pair is (Sue, William) whichever order they print in
    for lst in (present, absent):
        b = bare(lst)
        if len(b) == 2:
            lst[b[0]], lst[b[1]] = LEE_PRESIDENT, LEE_OTHER

    named = {n for n in present + absent if n in (LEE_PRESIDENT, LEE_OTHER)}
    for lst in (present, absent):
        for i in bare(lst):
            if lst is present and presiding.lower().endswith("lee"):
                lst[i] = LEE_PRESIDENT             # the Lee who gavelled it
            elif len(named) == 1:
                lst[i] = (LEE_OTHER if LEE_PRESIDENT in named else LEE_PRESIDENT)
            # else: genuinely ambiguous — leave it as "Lee"
    rec["present"], rec["absent"] = present, absent


def apply_names(rec: dict, reduce) -> dict:
    for f in ("present", "absent"):
        rec[f] = [reduce(x) for x in rec.get(f) or []]
    rec["presiding"] = reduce(rec.get("presiding", ""))
    _resolve_bare_lee(rec)
    return rec


def reconcile_names(con) -> int:
    """Second pass: reduce people to the surname, disambiguating where a surname is shared.

    Per-meeting text cannot decide this. Which token of "Shelley Bradford Bell" is the
    surname, and whether "Lee" is safe to use alone, are properties of the ROSTER, not of
    any one meeting — and the roster is only visible once every meeting has been read. So
    the corpus is assembled first, then each name is reduced to its last token, unless two
    members share that surname: Sue Lee and William L. Lee both sat on this Commission, and
    "Lee" alone would silently merge two people's attendance.

    A confirmed record is the human's answer and is never rewritten here; it still
    contributes to the roster.
    """
    rows = con.execute("SELECT source_file, line_no, data, status FROM meetings").fetchall()
    recs = {(src, ln): json.loads(d) for src, ln, d, _st in rows if d}
    editable = {(src, ln) for src, ln, d, st in rows if d and st != "done"}
    reduce = name_reducer(recs.values())

    n = 0
    for (src, ln), rec in recs.items():
        if (src, ln) not in editable:
            continue
        before = json.dumps(rec, sort_keys=True)
        apply_names(rec, reduce)
        if json.dumps(rec, sort_keys=True) != before:
            con.execute("UPDATE meetings SET data=? WHERE source_file=? AND line_no=?",
                        (json.dumps(rec, ensure_ascii=False), src, ln))
            n += 1
    con.commit()
    return n


def export(con) -> Path:
    rows = con.execute("SELECT source_file,line_no,meeting_date,origin,status,data "
                       "FROM meetings ORDER BY meeting_date, source_file").fetchall()
    with CSV_OUT.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["source_file", "line_no", "meeting_date", "origin", "status"] + MEETING_FIELDS)
        for src, ln, d, origin, st, data in rows:
            rec = json.loads(data) if data else {}
            w.writerow([src, ln, d, origin, st] +
                       ["; ".join(rec.get(f) or []) if isinstance(rec.get(f), list)
                        else (rec.get(f) or "") for f in MEETING_FIELDS])
    return CSV_OUT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", type=int, default=15, help="non-blank lines before the mark")
    ap.add_argument("--after", type=int, default=15, help="non-blank lines after the mark")
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--show", type=int, default=0, help="print N windows with their pre-fill")
    a = ap.parse_args()

    con = sqlite3.connect(DB)
    init_db(con)
    added, refreshed, cov = build(a.before, a.after, con)
    fixed = reconcile_names(con)
    total = con.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
    n_gold = con.execute("SELECT COUNT(*) FROM meetings WHERE origin='gold'").fetchone()[0]
    print(f"meetings: {total} ({n_gold} hand-marked, {total - n_gold} detected-only) | "
          f"+{added} new, {refreshed} refreshed | window -{a.before}/+{a.after} non-blank lines")
    print("pre-fill coverage: " + ", ".join(f"{k} {v}/{total}" for k, v in cov.most_common()))
    if fixed:
        print(f"names: {fixed} record(s) reduced to corpus surname form")

    if a.show:
        for src, ln, d, win, data in con.execute(
                "SELECT source_file,line_no,meeting_date,window_text,data FROM meetings "
                "ORDER BY meeting_date LIMIT ?", (a.show,)):
            print("\n" + "=" * 78)
            print(f"{src}  line {ln}  {d}")
            print("-" * 78)
            print(win[:1200])
            print("-" * 78)
            print(json.dumps(json.loads(data), ensure_ascii=False, indent=2))

    if a.export:
        print("→", export(con).relative_to(HERE.parent.parent))
    con.close()


if __name__ == "__main__":
    main()
