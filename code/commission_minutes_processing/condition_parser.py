#!/usr/bin/env python3
"""
condition_parser.py
-------------------
Purpose : The rules that turn a Commission document into conditions: which pages are a
          motion, where its Exhibit A starts and stops, and where each condition inside it
          begins and ends. Pure functions over page "lines"; no network, no file layout.
Inputs  : a PyMuPDF document (for `page_lines`) or the lines JSON `collect_conditions.py`
          caches for every pulled document
Outputs : motion sections with their metadata, and one dict per condition
Author  : Dan Post
Created : 2026-09-10

Notes
-----
Kept apart from `collect_conditions.py` so it can be hashed and frozen for a validation
round (CLAUDE.md: freeze and hash before labelling; do not touch a rule while labelling is
in progress). Everything that decides a boundary is in this file.

Formats the rules were written against, oldest first --- each is why one rule exists:

  * 1990s adopted motions survive as scans inside later packets: "CITY PLANNING
    COMMISSION ... MOTION NO. 13457", an EXHIBIT A, conditions numbered and unheaded, no
    bold (OCR has none).
  * 2008--2010: "Exhibit A / Conditions of Approval", conditions numbered `1.` with no
    heading at all ("1. This authorization is for ...").
  * 2011--2014: "Exhibit A" then standing blocks (AUTHORIZATION, RECORDATION ...,
    SEVERABILITY ...) and then "Conditions of approval, Compliance, Monitoring, and
    Reporting" with section heads (PERFORMANCE, PROVISIONS, OPERATION, MONITORING) and
    conditions that are *named but not numbered*: a bold "Validity and Expiration." opens
    each one. The text layer carries no number because the page shows none.
  * 2016--: the same skeleton with the conditions numbered and named, "1. Validity.", the
    name in bold.
  * 2022--: "Project Summary and Draft Motion" as one document, sections in Title Case, and
    the number printed as its own text line beside the name --- so lines that share a
    baseline are merged before anything else is read.
  * Adopted motions from the department's vault are signed scans with an OCR layer: the
    2016 skeleton with no font information, so a name is read from text (a short Title
    Case run before the first full stop) whenever bold is absent.

Each modern condition ends "For information about compliance, contact ...". That line is
moved into `compliance_contact`, never left in the body.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ═══════════════════════════════════════════════════════════════════════════
# lines
# ═══════════════════════════════════════════════════════════════════════════
BOLD_FONT = re.compile(r"(?i)bold|black|heavy|semibold|demi")


def page_lines(page, textpage=None) -> dict:
    """One page as {n, w, h, ocr, lines:[{t, x, y, sp:[[text, bold, italic], ...]}]}.
    Lines sharing a baseline (|dy| < 3pt) are merged left to right: the 2022 template
    prints the condition number and its name as separate text lines on one visual line."""
    d = page.get_text("dict", textpage=textpage) if textpage else page.get_text("dict")
    # Text comes back in the page's unrotated space. A page the PDF rotates for display (the
    # vault's scans set /Rotate 90 or 270 on a landscape sheet) would otherwise read with x
    # and y swapped --- lines from different columns merged into one --- so every box is
    # carried into the page as it is shown.
    rot = page.rotation_matrix if page.rotation else None
    raw = []
    for b in d.get("blocks", []):
        for l in b.get("lines", []):
            bb = l["bbox"]
            if rot is not None:
                import pymupdf
                r = pymupdf.Rect(bb) * rot
                bb = (r.x0, r.y0, r.x1, r.y1)
            sp = []
            for s in l.get("spans", []):
                if not s["text"]:
                    continue
                bold = int(bool(s["flags"] & 16) or bool(BOLD_FONT.search(s.get("font", ""))))
                ital = int(bool(s["flags"] & 2) or "italic" in s.get("font", "").lower())
                sp.append([s["text"].replace("\u00a0", " "), bold, ital])
            t = "".join(x[0] for x in sp)
            if t.strip():
                raw.append({"x": round(bb[0], 1), "y": round(bb[1], 1),
                            "y1": round(bb[3], 1), "sp": sp})
    lines = merge_baselines([{"x": r["x"], "y": r["y"], "sp": r["sp"]} for r in raw])
    return {"n": page.number + 1, "w": round(page.rect.width), "h": round(page.rect.height),
            "ocr": bool(textpage), "rot": page.rotation, "lines": lines, "merged": 2}


BASELINE_TOL = 3.5


def merge_baselines(lines: list[dict], original: bool = True) -> list[dict]:
    """One visual line from the fragments the text layer lists separately. Two rules:

    * the original one --- a fragment on the same baseline (|dy| < 3) listed after a line and
      to its right continues it, however far right (a running header's "RECORD NO." beside
      its "Motion No.");
    * fragments listed out of order, or up to BASELINE_TOL points off the baseline, join
      only when they are side by side --- the gap between the estimated right edge of the
      left one and the start of the right one under ADJACENT_GAP points. The 2010 packets
      print a condition's number as its own object a point below and left of its first line
      ("9." at y 89 beside "Noise shall be ..." at y 88), a lettered title's letter left of
      its name ("D." at y 648, "Performance" at y 645), a bold name listed after the body
      text it heads. A letterhead sidebar on the title's baseline ("San Francisco," 235
      points right of "Resolution No. 20259") is not beside it and stays apart.

    A cached page has had the first rule applied at extraction, and a cached line's y is its
    first fragment's, so re-applying it would not reproduce what extraction did; cached pages
    get the second rule only (`original=False`)."""
    frags = sorted(lines, key=lambda l: (l["y"], l["x"]))
    groups: list[dict] = []
    for l in frags:
        tgt = None
        for g in reversed(groups[-4:]):
            dy = abs(g["y"] - l["y"])
            if original and dy < 3 and l["x"] > g["xr"] and g is groups[-1]:
                tgt = g
                break
            if dy <= BASELINE_TOL and _side_by_side(g, l):
                tgt = g
                break
        if tgt is None:
            groups.append({"y": l["y"], "xr": l["x"], "frags": [l]})
        else:
            tgt["frags"].append(l)
            tgt["xr"] = max(tgt["xr"], l["x"])
    out = []
    for g in groups:
        g["frags"].sort(key=lambda l: l["x"])
        sp = []
        for k, l in enumerate(g["frags"]):
            sp += ([[" ", 0, 0]] if k else []) + [list(x) for x in l["sp"]]
        t = re.sub(r"\s+", " ", "".join(x[0] for x in sp)).strip()
        out.append({"x": g["frags"][0]["x"], "y": min(l["y"] for l in g["frags"]), "sp": sp,
                    "t": t})
    return out


ADJACENT_GAP = 60.0
CHAR_W = 5.5                    # points per character, for a right edge the cache does not keep


def _side_by_side(g: dict, l: dict) -> bool:
    def text(f):
        return f.get("t") or "".join(x[0] for x in f["sp"])
    for f in g["frags"]:
        left, right = (f, l) if f["x"] <= l["x"] else (l, f)
        gap = right["x"] - (left["x"] + CHAR_W * len(text(left)))
        if -15 <= gap <= ADJACENT_GAP:
            return True
    return False


def normalise_pages(pages: list[dict]) -> list[dict]:
    """Bring cached pages up to the current line rules: a page extracted before the merge
    above was written is merged now. Idempotent; marks the page."""
    for pg in pages:
        if pg.get("merged") != 2 and pg.get("lines"):
            pg["lines"] = merge_baselines(pg["lines"], original=False)
            pg["merged"] = 2
    return pages


def page_text(pg: dict) -> str:
    return "\n".join(l["t"] for l in pg["lines"])


def has_text(pg: dict) -> bool:
    return sum(len(l["t"]) for l in pg["lines"]) >= 40


# ═══════════════════════════════════════════════════════════════════════════
# what a page is
# ═══════════════════════════════════════════════════════════════════════════
PLAN_MARK = re.compile(r"(?i)\b(sheet\s+(no|number|title|index)|drawing\s+(title|list|no)|"
                       r"scale\s*:|project\s+data|job\s+no\.?|a\d\.\d{1,2}\b)")
MAP_TITLE = re.compile(r"(?i)^(parcel map|block book map|sanborn map\*?|zoning map|aerial photo|"
                       r"site photos?|context photos?|height (and|&) bulk map|land use map|"
                       r"exhibit [b-z]\b.{0,60}(plans?|renderings|maps?|photos?))")


def is_plan_page(pg: dict) -> bool:
    """A plan sheet or a map, not text. Letter is 612x792 and legal 612x1008; anything
    bigger or landscape and sparse is a drawing, and so is a page carrying the title-block
    vocabulary of one."""
    if pg.get("stub") == "plan":        # a cached stub keeps its verdict, not its text
        return True
    w, h = pg["w"], pg["h"]
    if h > 1100 or w > 1000 or (w > h * 1.05 and len(pg["lines"]) < 60):
        return True
    t = page_text(pg)
    if len(PLAN_MARK.findall(t)) >= 2:
        return True
    first = next((l["t"] for l in pg["lines"] if l["y"] < 0.2 * h and len(l["t"]) > 3), "")
    return bool(MAP_TITLE.match(first))


# ═══════════════════════════════════════════════════════════════════════════
# motion sections
# ═══════════════════════════════════════════════════════════════════════════
TITLE_RX = [
    re.compile(r"(?i)^subject to:?\s*\(select"),
    re.compile(r"(?i)^(?:san francisco\s+)?(?:city\s+)?planning commission\s+(?:draft\s+)?"
               r"(?:motion|resolution)\b"),
    re.compile(r"(?i)^project summary and draft motion$"),
    re.compile(r"(?i)^(?:draft\s+)?(?:motion|resolution)\s+no\.?\s*[:#]?\s*(?:\d{3,6}|x{3,})$"),
]
OLD_TITLE_A = re.compile(r"(?i)^(?:san francisco\s+)?city planning commission$")
OLD_TITLE_B = re.compile(r"(?i)^(?:motion|resolution)\s+no\.?\s*[:#]?\s*\d{3,6}")
MONTH = (r"(?:January|February|March|April|May|June|July|August|September|October|November|"
         r"December)")
DATE = rf"({MONTH}\s+\d{{1,2}},?\s+\d{{4}})"
MOTION_NO = re.compile(r"(?i)\bmotion\s+(?:no\.?|number|#)\s*[:.]?\s*(\d{4,6})\b")
RESOLUTION_NO = re.compile(r"(?i)\bresolution\s+(?:no\.?|number|#)\s*[:.]?\s*(\d{4,6})\b")
PLACEHOLDER = re.compile(r"(?i)\b(?:motion|resolution)\s+(?:no\.?\s*)?[:#]?\s*x{3,}|draft\s+motion|"
                         r"draft\s+resolution")
# The suffix may follow a space ("2005.1066 CP") but is never a word: OCR runs the next
# word on ("2013.1037O Motion" read as "2013.10370MOTION").
CASE_NO = re.compile(r"(?i)\b(?:case|record|file)\s+(?:no\.?|number|#)\s*[:.]?\s*"
                     r"((?:19|20)?\d{2}[.\-]\d{3,6}(?:\s?(?!(?:MOTION|HEARING|DATE|RECORD|"
                     r"PROJECT|BLOCK|ADDRESS|PAGE|ZONING)\b)[A-Z]{1,16})?)\b")
HEARING = re.compile(rf"(?i)hearing\s+date:?\s*{DATE}")
ADOPTED = re.compile(rf"(?i)\bADOPTED\s*(?:the foregoing\s+(?:motion|resolution)\s+on)?\s*[:.]?"
                     rf"\s*{DATE}")


TITLE_PAGE_CUE = re.compile(r"(?i)hearing\s+date|preamble|adopting\s+findings|whereas|"
                            r"(?:case|record|file)\s+no|subject to:")


def _title_line(pg: dict, l: dict, prev: list[dict]) -> bool:
    """A motion's title line: short, centred or wholly bold, in the upper part of the page
    but below the running header (which repeats "Motion No." on every page). A body line
    that wraps to begin "Planning Commission Resolution No. 16418 (dated ...)" is none of
    those things and must not open a section."""
    if l["y"] < 0.09 * pg["h"] or l["y"] > 0.6 * pg["h"]:
        return False
    t = l["t"]
    if TITLE_RX[0].match(t):
        return True
    centred_or_bold = l["x"] > 95 or all(s[1] for s in l["sp"] if s[0].strip())
    if len(t) > 60 or not centred_or_bold or re.search(r"[(),;]", t):
        return False
    if TITLE_RX[1].match(t) or TITLE_RX[2].match(t) or TITLE_RX[3].match(t):
        return True
    return bool(OLD_TITLE_B.match(t) and any(OLD_TITLE_A.match(p["t"]) for p in prev[-4:]))


def is_title_page(pg: dict) -> bool:
    ls = pg["lines"]
    return bool(TITLE_PAGE_CUE.search(page_text(pg))) and \
        any(_title_line(pg, l, ls[:i]) for i, l in enumerate(ls[:40]))


@dataclass
class Section:
    start: int                      # index into the pages list
    end: int                        # inclusive
    exa: tuple | None = None        # (page index, line index) of the Exhibit A heading
    exa_end: tuple | None = None    # first (page index, line index) past Exhibit A
    meta: dict = field(default_factory=dict)


def find_sections(pages: list[dict]) -> list[Section]:
    """Split a document into motion (or resolution) sections. A section opens at a title
    page and runs to the page before the next one, or to the first plan sheet after its
    Exhibit A, or to the end. A packet can hold several: two draft motions for one project,
    or an earlier adopted motion attached as an exhibit to a new one."""
    normalise_pages(pages)
    starts = [i for i, pg in enumerate(pages) if has_text(pg) and is_title_page(pg)]
    # a title block that spills onto a second page is one section, not two
    starts = [s for k, s in enumerate(starts) if k == 0 or s - starts[k - 1] > 1]
    secs = []
    for k, s in enumerate(starts):
        e = (starts[k + 1] - 1) if k + 1 < len(starts) else len(pages) - 1
        sec = Section(start=s, end=e)
        sec.meta = section_meta(pages[s:e + 1])
        locate_exhibit_a(pages, sec)
        if sec.exa is None:         # no conditions: the section is the motion, not its maps
            for i in range(s + 1, sec.end + 1):
                if is_plan_page(pages[i]):
                    sec.end = i - 1
                    break
        secs.append(sec)
    return secs


TITLE_NO = re.compile(r"(?i)^(?:san francisco\s+)?(?:city\s+)?(?:planning commission\s+)?"
                      r"(motion|resolution)\s+(?:no\.?|number|#)?\s*[:.]?\s*(\d{4,6})\b")
PLACEHOLDER_X = re.compile(r"(?i)\b(?:motion|resolution)\s+(?:no\.?\s*)?[:#]?\s*x{3,}")
DRAFT_TITLE = re.compile(r"(?i)\bdraft\s+(?:motion|resolution)\b")


def section_meta(pgs: list[dict]) -> dict:
    """What the section is. A draft is a section whose title says so ("Planning Commission
    Draft Motion", the running header "Draft Motion") or whose number is a placeholder
    ("Motion No. XXXX"). The words "draft motion" in running text are not evidence: adopted
    motions say "staff prepared a draft motion". The number is read from the title line
    first --- "Planning Commission Motion 18026" has no "No." --- and from the text only
    when there is no title number; a real title number also overrides every placeholder."""
    head = "\n".join(page_text(p) for p in pgs[:2])
    allt = "\n".join(page_text(p) for p in pgs[:40])
    short = [l["t"] for p in pgs[:2] for l in p["lines"] if len(l["t"]) <= 60]
    tn = next((TITLE_NO.match(t) for t in short if TITLE_NO.match(t)), None)
    # A real number in the title settles it: a draft's title never has one. Adopted files
    # do carry template leftovers --- "Motion No. XXXXX" in the body, a running header still
    # reading "Draft Motion No. 18124" --- so placeholders decide only when there is no title
    # number.
    draft = (not tn) and (bool(PLACEHOLDER_X.search(allt)) or
                          any(DRAFT_TITLE.search(t) for t in short))
    if tn:
        kind, number = tn.group(1).lower(), tn.group(2)
    else:
        mo = MOTION_NO.search(head)
        re_ = RESOLUTION_NO.search(head)
        kind = ("resolution" if re_ and (not mo or re_.start() < mo.start()) else "motion")
        num = (re_ if kind == "resolution" else mo)
        number = num.group(1) if num else ""
    # The running header prints the motion's own case number on every page ("CASE NO
    # 2012.0409B"); the findings cite other cases once each. The most frequent is the case.
    from collections import Counter
    cns = Counter(re.sub(r"\s+", "", m.group(1)).upper()
                  for m in CASE_NO.finditer("\n".join(page_text(p) for p in pgs[:15])))
    cn = cns.most_common(1)[0][0] if cns else ""
    hd = HEARING.search(head)
    ad = ADOPTED.search(allt)
    return {"instrument": kind, "number": "" if draft else number,
            "draft": draft, "case_no": cn,
            "hearing_date": re.sub(r"\s+", " ", hd.group(1)) if hd else "",
            "adoption_date": re.sub(r"\s+", " ", ad.group(1)) if ad and not draft else "",
            "decides": _decision(allt)}


def _decision(t: str) -> str:
    m = re.search(r"(?i)hereby\s+(approves|disapproves|denies|adopts|recommends|certifies|"
                  r"takes|does not take|continues)", t)
    return m.group(1).lower() if m else ""


EXA = re.compile(r"(?i)^exhibit\s+a\s*[:.\-–—]?\s*(?:conditions\s+of\s+approval.*)?$")
EXA_LIST = re.compile(r"(?i)^(attachments?|exhibits?|enclosures?)\s*:?$|^exhibit\s+[a-z]\s*[–—\-:]")
EX_NEXT = re.compile(r"(?i)^exhibit\s+[b-z]\b")
DECISION = re.compile(r"(?i)^decision$|hereby\s+(approves|disapproves|adopts)\b")
COA_ONLY = re.compile(r"(?i)^conditions\s+of\s+approval\.?$")
# What a conditions exhibit says near its top: the words themselves, the AUTHORIZATION block
# that opens the 2011-- template, or the 2008--2010 first condition ("This authorization is
# for a conditional use ..."). An "Exhibit A" with none of these within its first 40 lines
# is some other attachment --- a draft resolution, a development agreement, the
# department's response to a CEQA appeal, a mitigation monitoring table, a legal
# description --- and holds no conditions of approval.
COA_CUE = re.compile(r"(?i)conditions?\s+of\s+approv|^authorization\.?$|this\s+authorization\s+is|"
                     r"authorization\s+(?:is\s+)?for\s+a|the\s+following\s+conditions|"
                     r"conditions\s+(?:set\s+forth|attached|imposed|to\s+be\s+met)|^performance\b|"
                     r"^general\s+conditions")
CUE_WINDOW = 40
# A packet binds the next document straight after the motion, often with no "Exhibit B"
# between them: a blank separator sheet, the Director's memo on a development agreement
# ("TO: ... FROM: ..."), the executive summary of the next item, a CEQA exemption form.
BLANK_SHEET = re.compile(r"(?i)^(?:this\s+)?page\s+(?:is\s+)?(?:intentionally\s+)?(?:left\s+)?blank\.?$|"
                         r"^intentionally\s+(?:left\s+)?blank\.?$")
NEW_DOC = re.compile(r"(?i)^(?:executive\s+summary|memo(?:randum)?|ceqa\s+categorical\s+exemption|"
                     r"(?:applicant'?s\s+)?affidavit\b|"
                     r"certificate\s+of\s+determination|legislative\s+digest|"
                     r"discretionary\s+review\s+analysis|notice\s+of\s+(?:public\s+)?hearing)\b")


def is_separator_page(pg: dict) -> bool:
    t = [l["t"] for l in pg["lines"] if not PAGE_NO.match(l["t"].strip())]
    return bool(t) and len(" ".join(t)) < 120 and any(BLANK_SHEET.match(x.strip()) for x in t)


def is_new_document(pg: dict) -> bool:
    """The first page of another document: a memo block (TO: and FROM: near the top) or a
    known document title in the top quarter of the page."""
    top = [l["t"].strip() for l in pg["lines"] if l["y"] < 0.3 * pg["h"]]
    memo = any(re.match(r"(?i)^to\s*:", t) for t in top) and \
        any(re.match(r"(?i)^from\s*:", t) for t in top)
    return memo or any(NEW_DOC.match(t) for t in top[:8])


def is_next_exhibit(t: str) -> bool:
    """"EXHIBIT B" or "Exhibit B – Plans" is a heading; "Exhibit C. The amendment extends the
    approval to February 9, 2015." is a sentence that wrapped, and ends nothing."""
    m = EX_NEXT.match(t.strip())
    if not m:
        return False
    rest = t.strip()[m.end():].lstrip(" :.-–—")
    return len(rest.split()) <= 6 and not re.search(r"\.\s+\S", rest)


def locate_exhibit_a(pages: list[dict], sec: Section):
    """The Exhibit A heading is a short line reading "EXHIBIT A", not the attachments list
    that names it ("Exhibit A – Conditions of Approval" beside "Exhibit B – Plans") and not
    the decision paragraph that refers to it. It is looked for after the DECISION where the
    motion has one. It ends at the next exhibit heading, a plan or map page, or the end of
    the section."""
    after_decision = False
    lines = [(i, j, l) for i in range(sec.start, sec.end + 1)
             for j, l in enumerate(pages[i]["lines"])]
    has_decision = any(DECISION.search(l["t"]) for _, _, l in lines)
    for k, (i, j, l) in enumerate(lines):
        t = l["t"]
        if DECISION.search(t):
            after_decision = True
        if has_decision and not after_decision:
            continue
        nxt = next((x[2]["t"] for x in lines[k + 1:k + 3]), "")
        prv = lines[k - 1][2]["t"] if k else ""
        if EXA.match(t) and not EX_NEXT.match(nxt) and not EXA_LIST.match(prv) \
                and len(t) <= 60:
            sec.exa = (i, j)
            break
    if sec.exa is None:         # 2008-era and 1990s: "Conditions of Approval" alone
        after_decision = not has_decision
        for i, j, l in lines:
            if DECISION.search(l["t"]):
                after_decision = True
            if after_decision and COA_ONLY.match(l["t"]):
                sec.exa = (i, j)
                break
    if sec.exa is None:         # the vault's 2018-- scans: no "EXHIBIT A" line survives in the
        # text layer, and the exhibit opens with its AUTHORIZATION block after the decision.
        # The anchor sits just before that line, so the block is read as the exhibit's first.
        after_decision = not has_decision
        for k, (i, j, l) in enumerate(lines):
            if DECISION.search(l["t"]):
                after_decision = True
            if after_decision and re.fullmatch(r"(?i)authorization\.?", l["t"].strip()) and \
                    any(re.match(r"(?i)recordation\s+of\s+conditions", x[2]["t"].strip())
                        for x in lines[k + 1:k + 30]):
                sec.exa = (i, j - 1)
                sec.meta["exhibit_a_from"] = "authorization block"
                break
    if sec.exa is None:
        return
    i0, j0 = sec.exa
    win = [l["t"].strip() for i in range(i0, sec.end + 1) for j, l in enumerate(pages[i]["lines"])
           if (i, j) >= (i0, j0)][:CUE_WINDOW]
    if not any(COA_CUE.search(t) for t in win):
        sec.meta["exhibit_a_not_conditions"] = win[1] if len(win) > 1 else ""
        sec.exa = None
        return
    for i in range(i0, sec.end + 1):
        pg = pages[i]
        if i > i0 and (is_plan_page(pg) or not has_text(pg) and not pg.get("ocr") or
                       is_separator_page(pg) or is_new_document(pg)):
            sec.exa_end = (i, 0)
            sec.end = i - 1
            return
        for j, l in enumerate(pg["lines"]):
            if (i, j) <= (i0, j0):
                continue
            if is_next_exhibit(l["t"]):
                sec.exa_end = (i, j)
                sec.end = i if j > 0 else i - 1
                return
    sec.exa_end = (sec.end + 1, 0)


# ═══════════════════════════════════════════════════════════════════════════
# headers and footers
# ═══════════════════════════════════════════════════════════════════════════
def _shape(t: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", t.strip().lower()))


PAGE_NO = re.compile(r"(?i)^(page\s+)?(\d{1,3}|[ivx]{1,5})(\s+of\s+\d{1,3})?$|^page\s+\w+$")
# The word processor's file path the old motions print at the foot of their last page ("EW:
# G:\\Documents\\CUs\\...\\Final Motion.doc", "LJM:g:\\wp51\\..."), its wrapped tail, and the
# old site's HTML "Back to top" link. None of it is the condition it follows.
FILE_PATH = re.compile(r"(?i)(?:^|[\s:])[a-z]:\\\S|^\S.{0,80}\.docx?$|^back\s+to\s+top\.?$|"
                       r"^return\s+to\s+the\s+planning\s+department[’']?s\s+home\s*page\.?$|"
                       r"^to\s+the\s+planning\s+department[’']?s\s+home\s*page\.?$|^return$")
# A page header set as text inside the page rather than in its margin --- the scanned 1990s
# and 2001 motions, and the HTML copies, which keep "Block 146; Lot 02 Motion No. 16073
# Exhibit A Page 2" inline wherever the page broke. A motion number followed within a few
# words by "Page" is that header, wherever it sits.
HEADER_ANY = re.compile(r"(?i)\bmoti\S{0,3}n\s+n[o0]\.?\s*\d{4,6}\b.{0,30}?\bpage\b")
# What else of such a header OCR splits off in the top band: the bare words and numbers of
# "Exhibit A / Page 4", and the block-and-lot line.
HEADER_BITS = re.compile(r"(?i)^\W{0,3}(?:exhibit(?:\s+a)?|page(?:\s*\S{1,3})?|[a-z]|\d{1,2}|"
                         r"(?:assessor'?s\s+)?block\s+\d+\w*[,;]?\s*lot\s+\d+\w*)\W{0,3}$")
FOOTER = re.compile(r"(?i)^(san francisco\s*)?planning department$|^www\.sf-?planning\.org$|"
                    r"^san francisco$")


def strip_running(pages: list[dict], idx: list[int],
                  stats_idx: list[int] | None = None) -> dict[int, list[dict]]:
    """Body lines per page: drop what repeats in the top or bottom band across the section's
    pages (running header, footer) and bare page numbers. Repetition, not position alone,
    because the first condition on a page can sit high on it."""
    top, bot = Counter_(), Counter_()
    for i in (stats_idx or idx):
        pg = pages[i]
        for l in pg["lines"]:
            if l["y"] < 0.14 * pg["h"]:
                top[_shape(l["t"])] += 1
            elif l["y"] > 0.9 * pg["h"]:
                bot[_shape(l["t"])] += 1
    need = 2 if len(stats_idx or idx) >= 3 else 99
    out = {}
    for i in idx:
        pg = pages[i]
        keep = []
        # The scanned motions set a header block of six or seven short lines down the right of
        # the page (PLANNING COMMISSION / Case No. / address / Block, Lot / Motion No. /
        # Exhibit A / 2 / Page) reaching a fifth of the way down. Everything down to the lowest
        # line in the top quarter that opens like a header line is header.
        # (Short lines only: a body line can wrap to open "Record No. 2022-006563CUA and
        # subject to ...", and that is not a header.)
        cues = [l["y"] for l in pg["lines"] if l["y"] < 0.25 * pg["h"] and
                len(l["t"].strip()) <= 45 and HEADER_OPEN.match(l["t"].strip())]
        y_cut = max(cues) if cues else -1
        for l in pg["lines"]:
            if l["y"] <= y_cut:
                continue
            s = _shape(l["t"])
            band_top, band_bot = l["y"] < 0.14 * pg["h"], l["y"] > 0.9 * pg["h"]
            if (band_top and top[s] >= need) or (band_bot and bot[s] >= need):
                continue
            if (band_top or band_bot) and (PAGE_NO.match(l["t"].strip()) or
                                           FOOTER.match(l["t"].strip())):
                continue
            # The department's footer logo sits a little above the bottom band on the vault's
            # scans (y = 0.896 of the page), where only its exact words are safe to match;
            # OCR spells it differently on every page ("PLANNING DEP/~RTMENT"), so repetition
            # cannot catch it either.
            low = l["y"] > 0.87 * pg["h"] and _letters_upper(l["t"]) > 0.8
            if low and (FOOTER.match(l["t"].strip()) or FOOTER_OCR.search(l["t"])):
                continue
            if l["y"] > 0.925 * pg["h"] and len(l["t"].split()) <= 3 and \
                    _letters_upper(l["t"]) > 0.8:
                continue
            if FILE_PATH.search(l["t"].strip()) or BLANK_SHEET.match(l["t"].strip()):
                continue
            # A running header that OCR reads differently on every page ("CasE' No.",
            # "Motiòn No. 16283") escapes the repetition count; in the top band a motion or
            # case number is header, never condition text.
            if band_top and (HEADER_CUE.search(l["t"]) or HEADER_BITS.match(l["t"].strip())):
                continue
            if HEADER_ANY.search(l["t"]) and len(l["t"]) < 120:
                continue
            keep.append(l)
        out[i] = keep
    return out


# Anchored at the start of the line: that is where a running header puts the number, and a
# condition near the top of a page may cite one ("16. Client Age Range. Condition No. 2 of
# Motion No. 13536 is changed ...").
HEADER_OPEN = re.compile(r"(?i)^\W{0,3}(?:(?:san\s+francisco\s+)?(?:city\s+)?planning\s+commission\b|"
                         r"moti\S{0,3}n\s+n[o0]\b|(?:case|record)\S{0,2}\s*n[o0]\b|"
                         r"exhibit\s*a\W{0,2}$|page\s*\w{0,3}\W{0,2}$|block\s+\d+\w*[,;]?\s*lot\b)")
HEADER_CUE = re.compile(r"(?i)^\W{0,3}(?:(?:san\s+francisco\s+)?(?:city\s+)?planning\s+commission\s+)?"
                        r"(?:moti\S{0,3}n\s+n[o0]\b|(?:case|record)\S{0,2}\s*n[o0]\b)")
FOOTER_OCR = re.compile(r"(?i)^\s*(san\s+fran\w*|planning\s+dep\S*)\b.{0,12}$")


class Counter_(dict):
    def __missing__(self, k):
        return 0


# ═══════════════════════════════════════════════════════════════════════════
# conditions
# ═══════════════════════════════════════════════════════════════════════════
# The delimiter must be followed by white space: a wrapped line reading "314), the Project
# Sponsor shall pay ..." is the tail of "Section 414 (formerly" and not condition 314.
NUM = re.compile(r"^(\d{1,3})\s*[.)](?=\s|$|[A-Z])")    # "1.Validity" runs on, capitalised
LETTER = re.compile(r"^([A-Z])\s*[.)]\s+(?=\S)")
# The 1998--2009 motions nest their numbering: "GENERAL CONDITIONS: / A. The Approved
# Project / (1) This approval is to allow ..." (2008), or "2. GENERAL CONDITIONS. / A.
# Community Liaison / The Project Sponsor shall ..." (2001). The condition is the deepest
# level that is enumerated; the levels above it are sections.
PAREN = re.compile(r"^\(\s*(\d{1,3})\s*\)\s*(?=\S)")         # OCR spaces it: "(1 )"
# OCR on the vault's signed scans garbles the words but not their shape ("For
# iriformntian about compliance", "For .information", "For inforn2ation"), so the match
# is on shape: For, an i-word, an ab-word, a c...mpl-word.
COMPLIANCE = re.compile(r"(?i)^f[o0]r\s*[.,]?\s*i[a-z0-9]{6,12}\s+a[b6][a-z0-9]{2,4}\s+"
                        r"c[a-z0-9]{0,2}mpl[a-z0-9]*,?\s*(?:c[o0]ntact\s*)?")
# What OCR reads a digit as, for repairing a condition number the sequence expects.
OCR_DIGIT = {"S": "58", "s": "58", "O": "0", "o": "0", "l": "1", "I": "1", "i": "1",
             "B": "8", "Z": "2", "z": "2", "G": "6", "g": "9", "q": "9", "t": "1"}
OCR_NUM = re.compile(r"^([0-9SsOolIiBZzGgqt]{1,2})\s*\.(?=\s)")


def ocr_number(t: str, expected: int) -> str | None:
    """The expected condition number, if an OCR line opens with a misread of it ("S." where
    8 is due). Only ever the expected number: a repair that could produce any number would
    be a guess, and one that must produce the next number in sequence is a reading."""
    m = OCR_NUM.match(t)
    if not m or m.group(1).isdigit():
        return None
    want = str(expected)
    tok = m.group(1)
    if len(tok) != len(want):
        return None
    ok = all(c == w or w in OCR_DIGIT.get(c, "") for c, w in zip(tok, want))
    return want if ok else None
COA_PART = re.compile(r"(?i)^conditions\s+of\s+approval,?\s+compliance")
# The boilerplate's capital F, anywhere in a line: a condition's own text may say "for
# information about" in lower case and mean it.
COMPLIANCE_IN = re.compile(r"\bF[o0]r\s*[.,]?\s*i[a-z0-9]{6,12}\s+a[b6][a-z0-9]{2,4}\b"
                           r"(?=\s*$|\s+c[a-z0-9]{0,2}mpl)")
COMPLIANCE_TAIL = re.compile(r"(?i)^(?:i[a-z0-9]{6,12}\s+)?(?:a[b6][a-z0-9]{2,4}\s+)?"
                             r"(?:c[a-z0-9]{0,2}mpl[a-z0-9]*,?\s*)?(?:c[o0]ntact\s*)?")
COA_TITLE = re.compile(r"(?i)^(?:exhibit\s+a\b.*|conditions\s+of\s+approval\.?)$")
SMALL = {"and", "of", "the", "to", "for", "on", "in", "at", "with", "a", "an", "or", "by",
         "from", "per", "as", "&", "-", "–", "—", "/"}


def _letters_upper(t: str) -> float:
    a = [c for c in t if c.isalpha()]
    return sum(c.isupper() for c in a) / len(a) if a else 0.0


def _bold_prefix(sp: list) -> str:
    out = ""
    for text, bold, _ in sp:
        if not text.strip() and out:
            out += text
            continue
        if not bold:
            break
        out += text
    return out.strip()


def is_section_heading(l: dict) -> bool:
    """A standing block: PERFORMANCE, DESIGN – COMPLIANCE AT PLAN STAGE, MONITORING - AFTER
    ENTITLEMENT, and in 2022 the same in Title Case. Short, no terminal period, not
    enumerated, and either upper case or wholly bold."""
    t = l["t"].strip()
    if not t or len(t) > 80 or t.endswith((".", ",", ";")) or NUM.match(t) or \
            re.match(r"^[a-z]\s*[.)]", t) or EXA.match(t):
        return False
    if len(t) < 4 or not re.search(r"[A-Za-z]{3}", t) or re.fullmatch(DATE, t):
        return False
    words = t.split()
    all_bold = all(s[1] for s in l["sp"] if s[0].strip())
    if _letters_upper(t) >= 0.85 and len(words) <= 10:
        return True
    return all_bold and len(words) <= 8 and \
        all(w[0].isupper() or w.lower() in SMALL for w in words if w[0].isalpha())


MODAL = {"shall", "must", "will", "may", "should"}
SENTENCE_OPENERS = {"the", "this", "that", "these", "those", "all", "any", "each", "every", "no",
                    "if", "when", "where", "whenever", "wherever", "prior", "pursuant", "should",
                    "a", "an", "for", "in", "on", "at", "to", "upon", "once", "after", "before",
                    "within", "unless", "until", "it", "there", "such", "only", "per", "as",
                    "project", "applicant", "sponsor", "owner", "operator"}
ABBREV = {"no", "nos", "sec", "secs", "st", "ave", "blvd", "inc", "co", "corp", "mr", "ms",
          "mrs", "dr", "u.s", "approx", "e.g", "i.e", "etc", "vs"}


def heading_from_text(t: str) -> str:
    """A name read from plain text, for documents with no font information: the run before
    the first full stop, if it is short and Title Case. "Expiration and Renewal. Should a
    ..." gives a name; "This authorization is for a Conditional Use ..." does not."""
    m = re.match(r"^(.{2,90}?)[.:](?:\s|$)", t)
    if not m:
        return ""
    h = m.group(1).strip()
    words = [w for w in re.split(r"\s+", h) if w]
    if not words or len(words) > 12:
        return ""
    # "This Motion shall supersede all Conditions of Approval in Motion No. 17897" stops at
    # "No." and is Title Case enough, but it is a sentence: a name has no modal verb, and a
    # full stop after an abbreviation ends nothing.
    if any(w.lower() in MODAL for w in words) or words[-1].lower() in ABBREV:
        return ""
    content = [w for w in words if w.lower() not in SMALL and w[0].isalpha()]
    if not content or not content[0][0].isupper():
        return ""
    if sum(w[0].isupper() for w in content) / len(content) >= 0.5:
        return h
    # A short noun phrase in sentence case is a name too ("Garbage, composting and recycling
    # storage."); a sentence opens with a determiner or a subordinator, and has a verb.
    if len(words) <= 8 and words[0].lower().strip(",") not in SENTENCE_OPENERS:
        return h
    return ""


def _line_step(body: list[tuple]) -> float:
    """The typical distance between consecutive lines on a page: the median of the
    positive gaps under 30 points."""
    gaps = sorted(b[1]["y"] - a[1]["y"] for a, b in zip(body, body[1:])
                  if a[0] == b[0] and 0 < b[1]["y"] - a[1]["y"] < 30)
    return gaps[len(gaps) // 2] if gaps else 0.0


def _at(l: dict, margin) -> bool:
    return margin is not None and abs(round(l["x"]) - margin) <= 6


def _margin_of(body: list[tuple], rx) -> float | None:
    from collections import Counter
    xs = [round(l["x"]) for _, l in body if rx.match(l["t"])]
    return Counter(xs).most_common(1)[0][0] if xs else None


def _enum_title(t: str, rx, caps_only: bool = False) -> bool:
    """An enumerated line that is a title --- "A. The Approved Project", "2. GENERAL
    CONDITIONS." --- and not a sentence: short, and no punctuation followed by more text."""
    rest = rx.sub("", t.strip(), 1).strip()
    words = rest.split()
    if not words or re.search(r"[.;:,]\s+\S", rest):
        return False
    # all-caps part titles run long ("CONDITIONS TO BE MET PRIOR TO THE ISSUANCE OF A
    # BUILDING OR SITE PERMIT", fourteen words); a Title Case one is a name, and short
    if _letters_upper(rest) >= 0.85:
        return len(words) <= 16
    return not caps_only and len(words) <= 12 and \
        all(w[0].isupper() or w.lower() in SMALL for w in words if w[0].isalpha())


def _bold_starts(body: list[tuple], lm: float) -> int:
    """How many lines would open a condition under the bold-name rule."""
    n = 0
    for _, l in body:
        if l["x"] > lm + 6 or is_section_heading(l):
            continue
        t = l["t"].strip()
        if LETTER.match(t) or PAREN.match(t):
            continue                    # an enumerated line is not a bold-named one
        bp = _bold_prefix(l["sp"])
        rest = t[len(bp):].lstrip() if t.startswith(bp) else ""
        if re.search(r"[A-Za-z]{3}", bp) and bp[:1].isalpha() and \
                (bp.endswith((".", ":")) or rest[:1] in (".", ":")) and not NUM.match(bp):
            n += 1
    return n


def _numbered_margin(body: list[tuple]) -> float | None:
    xs = [round(l["x"]) for _, l in body if NUM.match(l["t"])]
    if not xs:
        return None
    from collections import Counter
    return Counter(xs).most_common(1)[0][0]


def parse_section(pages: list[dict], sec: Section) -> list[dict]:
    """One dict per condition in the section's Exhibit A. A condition opens at a numbered
    line at the numbering margin whose number continues the sequence, or --- in the
    unnumbered 2011--2014 template --- at a line that opens with a bold name. Section heads
    are carried as `section_heading`; a head followed by prose and no condition (the
    AUTHORIZATION or SEVERABILITY block) becomes a row of its own, flagged `implicit`."""
    if sec.exa is None:
        return []
    i0, j0 = sec.exa
    i1, j1 = sec.exa_end
    idx = list(range(i0, min(i1, len(pages) - 1) + 1))
    kept = strip_running(pages, idx, list(range(sec.start, min(sec.end, len(pages) - 1) + 1)))
    body = []
    for i in idx:
        for l in kept[i]:
            j = pages[i]["lines"].index(l)
            if (i, j) <= (i0, j0) or (i, j) >= (i1, j1):
                continue
            body.append((i, l))
    if not body:
        return []
    ocr = any(pages[i].get("ocr") for i in idx)
    has_bold = any(s[1] for _, l in body for s in l["sp"] if s[0].strip() and
                   not is_section_heading(l))
    nm = _numbered_margin(body)
    num_lines = [l for _, l in body if NUM.match(l["t"]) and _at(l, nm)]
    nums = {int(NUM.match(l["t"]).group(1)) for l in num_lines}
    # A numbered level whose items are all-caps titles ("1. COMPLIANCE WITH OTHER
    # REQUIREMENTS") is a level of parts, not of conditions. Title Case is not enough:
    # the 2022 template prints "1. Validity." on a line of its own.
    num_parts = bool(num_lines) and \
        sum(_enum_title(l["t"], NUM, caps_only=True) for l in num_lines) >= 0.6 * len(num_lines)
    pm = _margin_of(body, PAREN)
    pnums = {int(PAREN.match(l["t"]).group(1)) for _, l in body
             if PAREN.match(l["t"]) and _at(l, pm)}
    n_paren = sum(1 for _, l in body if PAREN.match(l["t"]) and _at(l, pm))
    lmg = _margin_of(body, LETTER)
    letters = {LETTER.match(l["t"]).group(1) for _, l in body
               if LETTER.match(l["t"]) and _at(l, lmg)}
    lm = min(l["x"] for _, l in body)
    step = _line_step(body)
    # Numbered means an actual 1, 2, 3 at the numbering margin, not two stray digits.
    if {1, 2, 3} <= nums and not num_parts:
        style = "numbered"
    elif {1, 2} <= pnums and n_paren >= 3 and (lmg is None or pm > lmg + 6):
        style = "paren"
    elif {"A", "B", "C"} <= letters and _bold_starts(body, lm) < 3:
        style = "lettered"
    elif {1, 2, 3} <= nums:
        style = "numbered"
    else:
        style = "bold_heading" if has_bold else "unknown"
    label = ""                          # the letter of the current lettered section
    expected_letter = "A"
    max_seen = 0                        # the highest condition number read so far
    paren_under_letter = False          # lettered style: inside a title-only letter's (n) items
    prev_y = None

    rows, cur = [], None
    part = "conditions"
    if any(COA_PART.match(l["t"]) for _, l in body):
        part = "preamble"               # blocks before the "Conditions of Approval, ..." line
    section = ""
    expected = 1
    pending_head = None                 # a section head with nothing under it yet

    def open_row(pageno, no, head, text, method, implicit=False, gap=False):
        nonlocal cur
        cur = {"condition_no": no, "heading": head, "lines": [text] if text else [],
               "compliance": [], "section_heading": section, "part": part,
               "page_start": pageno, "page_end": pageno, "parse_method": method,
               "implicit": implicit, "sequence_gap": gap, "in_compliance": False}
        rows.append(cur)

    for k, (i, l) in enumerate(body):
        t = l["t"].strip()
        pageno = pages[i]["n"]
        gap_above = (l["y"] - prev_y) if prev_y is not None and l["y"] > prev_y else 0
        prev_y = l["y"]
        if COA_PART.match(t):
            part, section, pending_head = "conditions", "", None
            continue
        if re.match(r"(?i)^monitoring,?\s+and\s+reporting$", t):
            continue                    # the second line of the split COA_PART heading
        # A template glitch numbers a condition's compliance line as if it were the next
        # condition ("4. For information about compliance, contact ..."): it is the line of
        # the condition above it, and the number is nobody's.
        mnum = NUM.match(t)
        if mnum and cur is not None and COMPLIANCE.match(t[mnum.end():].strip()):
            t = t[mnum.end():].strip()
        if COMPLIANCE.match(t) and cur is not None:
            cur["compliance"].append(COMPLIANCE.sub("", t))
            cur["in_compliance"] = True
            cur["page_end"] = pageno
            continue
        if cur is not None and cur.get("force_compliance"):
            # the rest of a compliance phrase that began at the end of the previous line
            cur["force_compliance"] = False
            cur["compliance"].append(COMPLIANCE_TAIL.sub("", t))
            cur["page_end"] = pageno
            continue
        # The compliance line can start mid-line, after the condition's last sentence, and
        # the phrase can break across lines ("... Maintenance Standards. For" / "information
        # about compliance, contact ..."): test the line joined to the next.
        nxt_t = body[k + 1][1]["t"].strip() if k + 1 < len(body) else ""
        cm = COMPLIANCE_IN.search(t + " " + nxt_t)
        if cm and 0 < cm.start() < len(t) and cur is not None and not cur["in_compliance"]:
            before = t[:cm.start()].strip()
            if before:
                cur["lines"].append(before)
            if cm.end() <= len(t):
                cur["compliance"].append(COMPLIANCE.sub("", t[cm.start():]))
            cur["in_compliance"] = True
            cur["force_compliance"] = cm.end() > len(t)
            cur["page_end"] = pageno
            continue
        m = NUM.match(t)
        start = None
        if style == "numbered" and not m and nm is not None and abs(round(l["x"]) - nm) <= 6:
            fix = ocr_number(t, expected)
            if fix:
                start = ("numbered", fix, OCR_NUM.sub("", t, 1).strip(), False)
                expected = int(fix) + 1
        if style == "numbered" and m and abs(round(l["x"]) - nm) <= 6:
            n = int(m.group(1))
            # The sequence may restart (a sub-list of 1, 2 under one heading, then 10 again)
            # or jump (16, then 20): a number above the highest yet, by a few, continues it.
            if n == expected or n == 1 or n in (expected + 1, expected + 2) or \
                    max_seen < n <= max_seen + 5:
                start = ("numbered", str(n), t[m.end():].strip(), n not in (expected, 1))
                expected = n + 1
                max_seen = max(max_seen, n)
        elif style == "paren":
            pmatch = PAREN.match(t)
            if pmatch and _at(l, pm):
                n = int(pmatch.group(1))
                if n in (expected, 1, expected + 1, expected + 2):
                    start = ("paren", f"{label}({n})", t[pmatch.end():].strip().lstrip(". "),
                             n not in (expected, 1))
                    expected = n + 1
            elif (LETTER.match(t) and _at(l, lmg) and _enum_title(t, LETTER)) or \
                    (NUM.match(t) and _enum_title(t, NUM, caps_only=True)):
                # A titled part. If (n) items follow, it is their section; if prose follows
                # directly ("E. Jobs Housing Linkage Program" / "In compliance with ..."),
                # it is a condition in its own right, read as a block under its title.
                lm_ = LETTER.match(t)
                label = lm_.group(1) if lm_ else ""
                section = (LETTER if lm_ else NUM).sub("", t, 1).strip().rstrip(".:")
                pending_head, cur, expected = (pageno, section), None, 1
                continue
        elif style == "lettered":
            lmatch = LETTER.match(t)
            pmatch = PAREN.match(t)
            if lmatch and _at(l, lmg):
                ch = lmatch.group(1)
                nxt = chr(ord(expected_letter) + 1)
                if ch in (expected_letter, "A", nxt):
                    start = ("lettered", ch, t[lmatch.end():].strip(),
                             ch not in (expected_letter, "A"))
                    expected_letter = chr(ord(ch) + 1)
                    paren_under_letter = False
            elif pmatch and lmg is not None and l["x"] >= lmg - 6 and cur is not None and \
                    (paren_under_letter or (cur["parse_method"].startswith("lettered") and
                                            not cur["lines"] and cur["heading"])):
                # (the items' indent varies from part to part --- x 144 under one letter, 108
                # under the next --- so no single margin is asked of them)
                # A lettered title with (1), (2) items under it and no prose of its own
                # ("D. Performance" / "(1). A site permit ..."): the items are the conditions
                # and the letter is their section.
                if not paren_under_letter:
                    section, label = cur["heading"], cur["condition_no"]
                    rows.remove(cur)
                    cur, paren_under_letter, expected = None, True, 1
                n = int(pmatch.group(1))
                if n in (expected, 1, expected + 1, expected + 2):
                    start = ("paren", f"{label}({n})", t[pmatch.end():].strip().lstrip(". "),
                             n not in (expected, 1))
                    expected = n + 1
            elif NUM.match(t) and _enum_title(t, NUM, caps_only=True):
                # the letters run on across parts (D under GENERAL CONDITIONS, E under
                # the next part), so the expected letter carries over; a part with prose and
                # no letters ("5. SEVERABILITY" / "If any clause ...") is a block of its own
                section = NUM.sub("", t, 1).strip().rstrip(".:")
                pending_head, cur, paren_under_letter = (pageno, section), None, False
                continue
        elif style == "bold_heading" and l["x"] <= lm + 6 and not is_section_heading(l):
            bp = _bold_prefix(l["sp"])
            rest = t[len(bp):].lstrip() if t.startswith(bp) else ""
            if re.search(r"[A-Za-z]{3}", bp) and bp[:1].isalpha() and \
                    (bp.endswith((".", ":")) or rest[:1] in (".", ":")) and not NUM.match(bp):
                start = ("bold_heading", None, t, False)
        if start is None and is_section_heading(l):
            section = t
            pending_head = (pageno, t)
            cur = None
            continue
        if start:
            method, no, text, gap = start
            head = ""
            if method == "numbered":
                sp = _after_number(l["sp"])
                bp = _bold_prefix(sp) if sp else ""
                if bp and len(bp) <= 150 and not NUM.match(bp) and re.search(r"[A-Za-z]", bp):
                    head = bp.rstrip(".: ")
                    text = text[len(bp):].lstrip(" .:") if text.startswith(bp) else text
                    method = "numbered+bold"
                else:
                    head = heading_from_text(text)
                    if head:
                        text = text[len(head):].lstrip(" .:")
                        method = "numbered+text"
            elif method in ("paren", "lettered"):
                if method == "lettered" and _enum_title(t, LETTER):
                    head, text = text.rstrip(".: "), ""
                else:
                    head = heading_from_text(text)
                    if head:
                        text = text[len(head):].lstrip(" .:")
            else:
                bp = _bold_prefix(l["sp"])
                head = bp.rstrip(".: ")
                text = t[len(bp):].lstrip(" .:") if t.startswith(bp) else t
            head = re.sub(r"\s+", " ", head).strip()
            rep_ = re.match(re.escape(head) + r"\s*[.:]\s*", text) if head else None
            if rep_:
                # the name repeated, with its full stop, at the head of the text ("Community
                # Liaison" in spaced bold over "Community Liaison. Within 10 days ...")
                text = text[rep_.end():]
            if not m and method.startswith("numbered"):
                method += "+ocrdigit"
            open_row(pageno, no, head, text, method + ("+ocr" if ocr else ""), gap=gap)
            pending_head = None
            continue
        if cur is None:
            if pending_head is not None:
                if section == pending_head[1] and _letters_upper(section) >= 0.85 and \
                        _letters_upper(t) >= 0.85 and len(t.split()) <= 6:
                    # "3. CONDITIONS TO BE MET PRIOR TO THE ISSUANCE OF A BUILDING (OR SITE)"
                    # / "PERMIT." --- the title wraps; the second line is not prose under it
                    section = f"{section} {t}".rstrip(".:")
                    pending_head = (pending_head[0], section)
                    continue
                open_row(pending_head[0], None, pending_head[1], t,
                         "section_block" + ("+ocr" if ocr else ""), implicit=True)
                pending_head = None
            continue
        if cur["in_compliance"]:
            # the compliance line wraps ("... at 415-575-6863," / "www.sf-planning.org");
            # anything after it that is not a new condition is the compliance tail
            # and a long compliance line ("For information about compliance with the fixed
            # mechanical objects such as rooftop air conditioning," / "restaurant ventilation
            # systems ... contact the") runs on until it ends a sentence, a URL or a number
            last_c = cur["compliance"][-1] if cur["compliance"] else ""
            open_c = bool(last_c) and not re.search(
                r"(?:[.)]|\.(?:org|com|gov)/?|www\.\S+|\d{4}|\.aspx?)\s*$", last_c)
            if open_c or len(t) < 60 or re.search(r"(?i)www\.|\.org|\d{3}[.\-]\d{4}", t):
                cur["compliance"].append(t)
                continue
            cur["in_compliance"] = False
        # Two unnumbered paragraphs under the exhibit's own title ("Wherever 'Project
        # Sponsor' is used ..." and "This authorization is for ...") are two blocks; a
        # paragraph break is a line gap well over the section's line step, back at the margin.
        if cur["implicit"] and COA_TITLE.match(cur["heading"] or "") and cur["lines"] and \
                step and gap_above > 1.6 * step and l["x"] <= lm + 6:
            open_row(pageno, None, cur["heading"], t,
                     "section_block" + ("+ocr" if ocr else ""), implicit=True)
            continue
        cur["lines"].append(t)
        cur["page_end"] = pageno
    out = []
    for r in rows:
        body_txt = _join(r.pop("lines"))
        comp = _join(r.pop("compliance"))
        r.pop("in_compliance")
        r.pop("force_compliance", None)
        r["body"] = body_txt
        r["compliance_contact"] = comp
        out.append(r)
    for k, r in enumerate(out, 1):
        r["ordinal"] = k
        r["style"] = style
        r["parse_confidence"] = _confidence(r, style, ocr)
    return out


def _after_number(sp: list) -> list:
    """The spans of a numbered line with the number itself removed."""
    t = "".join(s[0] for s in sp)
    m = NUM.match(t.strip())
    if not m:
        return sp
    drop = len(t) - len(t.lstrip()) + m.end()
    out, n = [], 0
    for text, b, it in sp:
        if n + len(text) <= drop:
            n += len(text)
            continue
        cut = max(0, drop - n)
        out.append([text[cut:], b, it])
        n += len(text)
    while out and not out[0][0].strip():
        out.pop(0)
    return out


NUMBER_WORDS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                "eleven", "twelve", "fifteen", "eighteen", "twenty", "thirty", "sixty", "ninety",
                "half", "full", "multi", "non", "self"}


def _join(lines: list[str]) -> str:
    s = ""
    for t in lines:
        t = t.strip()
        if not t:
            continue
        if s.endswith("-") and t[:1].islower():
            # "recycl-" / "ing" is one word; "three-" / "year" is a compound that keeps it
            last = re.split(r"[\s(]", s[:-1])[-1].lower()
            s = (s + t) if (last in NUMBER_WORDS or last.isdigit()) else (s[:-1] + t)
        elif s.endswith(("www.sf-", "sf-")):
            s += t
        else:
            s = (s + " " + t) if s else t
    s = HEADER_INLINE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


# The header as the HTML copies and some scans leave it inside the text once lines are
# joined: "Block 146; Lot 02 Motion No. 16073 Exhibit A Page 2".
HEADER_INLINE = re.compile(r"(?i)(?:\b(?:assessor[’']?s\s+)?block\s+\d+\w*[;,]?\s*lot\s+\d+\w*\s+)?"
                           r"\bmoti\S{0,3}n\s+n[o0]\.?\s*\d{4,6}\s+(?:\S{0,3}\s+)?exhibit\s*a\b"
                           r"\W{0,4}(?:\d{1,3}\s*)?page\b\s?\w{0,2}\b")


def _confidence(r: dict, style: str, ocr: bool) -> str:
    n = len(r["body"])
    if n > 6000 or n < 15:
        return "low"
    if r["implicit"] or r["sequence_gap"]:
        return "medium"
    if ocr and not r["heading"] and style != "numbered":
        return "low"
    if ocr or style == "unknown" or r["parse_method"].startswith("numbered+text"):
        return "medium"
    return "high"


# ═══════════════════════════════════════════════════════════════════════════
# derived fields
# ═══════════════════════════════════════════════════════════════════════════
HAS_DOLLAR = re.compile(r"\$\s?\d")
HAS_PERCENT = re.compile(r"(?i)\d\s?%|\bpercent\b")
# The figure may sit in the parentheses of "three (3) years", so a closing bracket may
# stand between it and the unit.
QUANTITY = re.compile(r"(?i)\b\d[\d,.]*\)?\s*(?:\(\d+\)\s*)?(?:units?|feet|foot|ft\b|square\s+feet|"
                      r"sq\.?\s?ft|gsf|stories|story|spaces?|hours?|days?|weeks?|months?|"
                      r"years?|a\.?m\.?|p\.?m\.?|decibels?|dba?\b|seats?|persons?|people|"
                      r"trees?|bicycles?|bikes?|parking|inches|acres?|minutes?)")
CODE_CITE = re.compile(r"(?i)\b(?:planning\s+code\s+)?(?:sections?|sec\.|§§?)\s*"
                       r"(\d{2,3}(?:\.\d+)?[A-Z]?)(?:\s*\([a-z0-9]+\))*")
FORMERLY = re.compile(r"(?i)\bsections?\s+(4\d\d[A-Z]?(?:\.\d+)?)\s*\(\s*formerly\s+"
                      r"(?:sections?\s+)?(3\d\d[A-Z]?(?:\.\d+)?|1\d\d(?:\.\d+)?)\s*\)")


def code_sections(t: str) -> list[str]:
    return list(dict.fromkeys(m.group(1).upper() for m in CODE_CITE.finditer(t or "")))


def learn_renumbering(texts) -> dict[str, str]:
    """The Article 4 recodification moved the fee sections from the 300s to the 400s, and
    the motions say so: "Section 414 (formerly 314)". The map is read from the documents
    themselves rather than typed, and the brief's one stated pair (§315 → §415, the
    inclusionary requirement) is checked against it rather than assumed."""
    from collections import Counter
    c = Counter()
    for t in texts:
        for m in FORMERLY.finditer(t or ""):
            c[(m.group(2).upper(), m.group(1).upper())] += 1
    best: dict[str, tuple[str, int]] = {}
    for (old, new), n in c.items():
        if old not in best or n > best[old][1]:
            best[old] = (new, n)
    return {old: new for old, (new, n) in best.items()}
