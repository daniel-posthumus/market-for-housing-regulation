#!/usr/bin/env python3
"""
app.py — hand-mark where each meeting starts inside a minutes document, to build a gold
standard for the automatic date-assignment stage (`../assign_meeting_dates.py`).

The archive bundles several meetings into one document (the 1998-2000 monthly
compilations hold four) and the machine has to infer where one meeting ends and the next
begins. This app is how that inference gets checked: you open a document, mark the lines
where a meeting starts and say which date it is, and the scorer compares your marks
against what the pipeline inferred — both at the boundary level (did it find the same
meeting starts?) and at the block level (did every item get the right date?).

Deliberately independent of the parser and of the inference being tested:
  • the document queue is built from the raw corpus on disk, not from labels.db;
  • the marking view shows EVERY line that mentions a date, not the subset the detector
    accepts, so the gold standard can catch the detector's blind spots as well as its
    mistakes;
  • nothing pre-marks a boundary for you, and the pipeline's answer is never shown while
    you mark — only in the score view, afterwards.

Run:
  python app.py                 # serves http://127.0.0.1:5006
  python app.py --score         # print gold-vs-pipeline agreement and exit
  python app.py --score --csv   # ...and write date_gold_score.csv

Suggested workload: one month per year (the "sample" filter). Most documents are a single
meeting and take one click; the monthly compilations take four.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask import render_template

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from paths import MEETING_MINUTES                       # noqa: E402
import assign_meeting_dates as AD                       # noqa: E402

RAW = MEETING_MINUTES / "raw"
DB = HERE / "date_gold.db"
LABELS_DB = HERE.parent / "labeling_app" / "labels.db"

# How far a detected header may sit from the line you marked and still count as the same
# meeting start. Generous: you mark the header block, the detector anchors on the date
# inside it, and a page title sits a few hundred characters above the body header.
POS_TOL = 4000

app = Flask(__name__, static_folder=str(HERE / "static"),
            template_folder=str(HERE / "templates"))

DATE_TEXT = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|"
    r"December)\s+(\d{1,2}),?\s+((?:19|20)\d{2})\b", re.I)
# dates as digits, e.g. "6/4/98" or "06-04-1998", which some PDFs use in the header
DATE_NUM = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-]((?:19|20)?\d{2})\b")

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}


# ── document text ─────────────────────────────────────────────────────────────
def doc_path(source_file: str) -> Path | None:
    """Resolve a queue id ('raw/1998/min0698-documentid=4763.htm') to a file on disk."""
    p = MEETING_MINUTES / source_file
    if p.exists():
        return p
    for ext in (".html", ".htm", ".pdf", ".txt"):        # labels.db stores stems
        if (q := p.with_suffix(p.suffix + ext)).exists():
            return q
    return None


def is_pdf(path: Path) -> bool:
    """Content, not extension. The 2000-02-03 page was scraped as a PDF but saved as
    `20000203-documentid=32.pdf.html`, so an extension check hands binary to the HTML
    parser and yields garbage."""
    try:
        with path.open("rb") as fh:
            return fh.read(5).startswith(b"%PDF")
    except OSError:
        return False


def doc_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf" or is_pdf(path):
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return "\n".join((pg.extract_text() or "") for pg in pdf.pages)
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix.lower() in (".html", ".htm"):
        from bs4 import BeautifulSoup
        return BeautifulSoup(raw, "lxml").get_text("\n")
    return raw


def iso_from_words(m: re.Match) -> str:
    mo = MONTHS.get(m.group(1).lower())
    try:
        return date(int(m.group(3)), mo, int(m.group(2))).isoformat()
    except (ValueError, TypeError):
        return ""


def iso_from_nums(m: re.Match) -> str:
    mo, da, yr = (int(g) for g in m.groups())
    yr = yr if yr > 100 else (1900 + yr if yr > 50 else 2000 + yr)
    try:
        return date(yr, mo, da).isoformat()
    except ValueError:
        return ""


def dates_in(line: str) -> list[str]:
    out = [iso_from_words(m) for m in DATE_TEXT.finditer(line)]
    out += [iso_from_nums(m) for m in DATE_NUM.finditer(line)]
    return [d for d in dict.fromkeys(out) if d]


# How many consecutive non-blank lines a single date may be spread over. The archive wraps
# headers mid-date — page 2701 of 2011 prints "Thursday, March" on one line and "10, 2011"
# on the next — so a per-line scan finds no date on either half and the header cannot be
# marked. Three covers the worst case seen ("Thursday, March" / "10," / "2011").
MAX_DATE_SPAN = 3


def dates_in_span(texts: list[str]) -> list[str]:
    """Dates in a run of lines read as one string."""
    return dates_in(" ".join(t.strip() for t in texts))


def date_starts_here(texts: list[str]) -> list[str]:
    """Dates in the joined run whose FIRST character falls in the run's first line.

    Without this anchor every line preceding a date looks like the start of a wrapped one:
    joining "City Hall, 1 Dr. Carlton B. Goodlett Place" with the two lines after it yields
    2011-03-10, but the date plainly does not begin there. Only the line the date actually
    starts in should be offered as a place to mark.
    """
    if not texts:
        return []
    joined = " ".join(t.strip() for t in texts)
    head = len(texts[0].strip())
    out = []
    for rx, conv in ((DATE_TEXT, iso_from_words), (DATE_NUM, iso_from_nums)):
        for m in rx.finditer(joined):
            if m.start() < head:
                d = conv(m)
                if d:
                    out.append(d)
    return list(dict.fromkeys(out))


# ── db ────────────────────────────────────────────────────────────────────────
def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def init_db(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS docs(
        source_file TEXT PRIMARY KEY,
        year INTEGER, month TEXT, kind TEXT,
        in_sample INTEGER DEFAULT 0,
        status TEXT DEFAULT 'todo',
        updated_at TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS boundaries(
        source_file TEXT, line_no INTEGER, meeting_date TEXT,
        PRIMARY KEY(source_file, line_no));
    """)
    cols = {r[1] for r in con.execute("PRAGMA table_info(boundaries)")}
    if "span" not in cols:                 # how many lines the marked header covers
        con.execute("ALTER TABLE boundaries ADD COLUMN span INTEGER DEFAULT 1")
    con.commit()


def month_of(path: Path, year: int) -> str:
    """YYYY-MM for queue grouping. From the file name where it says so, else from the
    date the page states in its own title. Grouping only — never used as an answer."""
    stem = path.stem
    if m := re.match(r"((?:19|20)\d{2})-(\d{2})-(\d{2})", stem):
        return f"{m.group(1)}-{m.group(2)}"
    if m := re.match(r"((?:19|20)\d{2})(\d{2})(\d{2})", stem):
        return f"{m.group(1)}-{m.group(2)}"
    if m := re.match(r"min(\d{2})(\d{2})", stem):                 # min0698 → 1998-06
        return f"{year}-{m.group(1)}"
    try:
        td = AD.title_date(doc_text(path)[:4000])
    except Exception:
        td = ""
    return td[:7] if td else f"{year}-00"


def scan_corpus(con, years: range | None = None) -> int:
    """Refresh the document queue from the raw corpus on disk (idempotent)."""
    n = 0
    for ydir in sorted(d for d in RAW.iterdir() if d.is_dir() and d.name.isdigit()):
        year = int(ydir.name)
        if years and year not in years:
            continue
        for f in sorted(ydir.iterdir()):
            if f.suffix.lower() not in (".html", ".htm", ".pdf", ".txt"):
                continue
            src = str(f.relative_to(MEETING_MINUTES))
            if con.execute("SELECT 1 FROM docs WHERE source_file=?", (src,)).fetchone():
                continue
            con.execute("INSERT INTO docs(source_file,year,month,kind) VALUES(?,?,?,?)",
                        (src, year, month_of(f, year),
                         "pdf" if (f.suffix.lower() == ".pdf" or is_pdf(f)) else "html"))
            n += 1
    con.commit()
    return n


def pick_sample(con, per_year: dict[int, str] | None = None) -> dict[int, str]:
    """Flag one month per year as the gold sample: a month of *typical* size — the one
    whose document count is the year's median (ties → earliest) — so the sample reflects an
    ordinary month's structure without making a heavy month the workload. Overridable."""
    chosen = {}
    years = [r[0] for r in con.execute("SELECT DISTINCT year FROM docs ORDER BY year")]
    for y in years:
        if per_year and y in per_year:
            chosen[y] = per_year[y]
            continue
        months = Counter(r[0] for r in con.execute(
            "SELECT month FROM docs WHERE year=? AND month NOT LIKE '%-00'", (y,)))
        if not months:
            continue
        counts = sorted(months.values())
        med = counts[len(counts) // 2]
        best = min(months.items(), key=lambda kv: (abs(kv[1] - med), int(kv[0][5:])))
        chosen[y] = best[0]
    # Round-2 documents were drawn at random, not by month, so a re-pick of the
    # one-month-per-year sample must not unflag them.
    con.execute("UPDATE docs SET in_sample=0 WHERE COALESCE(sample_round,0) != 2")
    for y, mo in chosen.items():
        con.execute("UPDATE docs SET in_sample=1 WHERE year=? AND month=? "
                    "AND COALESCE(sample_round,0) != 2", (y, mo))
    con.commit()
    return chosen


# ── api ───────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/static/<path:f>")
def static_file(f):
    return send_from_directory(app.static_folder, f)


@app.route("/api/docs")
def api_docs():
    con = db()
    where, params = ["1=1"], []
    if request.args.get("sample") == "1":
        where.append("in_sample=1")
    if y := request.args.get("year"):
        where.append("year=?"); params.append(int(y))
    if st := request.args.get("status"):
        where.append("status=?"); params.append(st)
    rows = con.execute(
        f"SELECT d.*, (SELECT COUNT(*) FROM boundaries b WHERE b.source_file=d.source_file) "
        f"AS n_marks FROM docs d WHERE {' AND '.join(where)} "
        f"ORDER BY year, month, source_file", params).fetchall()
    stats = dict(con.execute("SELECT status, COUNT(*) FROM docs WHERE in_sample=1 "
                             "GROUP BY status").fetchall())
    years = [r[0] for r in con.execute("SELECT DISTINCT year FROM docs ORDER BY year")]
    sample = dict(con.execute("SELECT DISTINCT year, month FROM docs WHERE in_sample=1"))
    con.close()
    return jsonify(docs=[dict(r) for r in rows], stats=stats, years=years, sample=sample)


@app.route("/api/doc")
def api_doc():
    src = request.args["src"]
    path = doc_path(src)
    if not path:
        return jsonify(error=f"file not found for {src}"), 404
    text = doc_text(path)
    raw = [(i, ln.strip()) for i, ln in enumerate(text.splitlines()) if ln.strip()]
    lines = []
    for k, (i, s) in enumerate(raw):
        d, span = dates_in(s), 1
        if not d:
            # the date may be wrapped: try joining this line with the next one or two
            for extra in range(1, MAX_DATE_SPAN):
                if k + extra >= len(raw):
                    break
                joined = date_starts_here([t for _n, t in raw[k:k + extra + 1]])
                if joined:
                    d, span = joined, extra + 1
                    break
        lines.append({"n": i, "text": s[:400], "dates": d, "span": span})
    con = db()
    marks = [dict(r) for r in con.execute(
        "SELECT line_no, meeting_date, COALESCE(span,1) AS span FROM boundaries "
        "WHERE source_file=? ORDER BY line_no", (src,))]
    row = con.execute("SELECT * FROM docs WHERE source_file=?", (src,)).fetchone()
    con.close()
    seen = Counter(d for ln in lines for d in ln["dates"])
    return jsonify(src=src, kind=path.suffix.lstrip("."), n_lines=len(lines),
                   lines=lines, boundaries=marks,
                   status=row["status"] if row else "todo",
                   date_menu=[d for d, _c in seen.most_common(24)])


@app.route("/api/dates", methods=["POST"])
def api_dates():
    """Dates in an arbitrary run of lines the user selected by hand."""
    body = request.get_json(force=True)
    return jsonify(dates=dates_in_span(body.get("texts") or []))


@app.route("/api/doc/save", methods=["POST"])
def api_save():
    body = request.get_json(force=True)
    src = body["src"]
    con = db()
    con.execute("DELETE FROM boundaries WHERE source_file=?", (src,))
    for b in body.get("boundaries", []):
        if b.get("meeting_date"):
            con.execute("INSERT OR REPLACE INTO boundaries(source_file,line_no,meeting_date,"
                        "span) VALUES(?,?,?,?)",
                        (src, int(b["line_no"]), b["meeting_date"], int(b.get("span") or 1)))
    con.execute("UPDATE docs SET status=?, updated_at=? WHERE source_file=?",
                (body.get("status", "done"),
                 datetime.now(timezone.utc).isoformat(timespec="seconds"), src))
    con.commit()
    con.close()
    return jsonify(ok=True)


@app.route("/api/sample", methods=["POST"])
def api_sample():
    body = request.get_json(force=True)
    con = db()
    over = {int(k): v for k, v in (body.get("months") or {}).items()}
    chosen = pick_sample(con, over)
    con.close()
    return jsonify(sample=chosen)


@app.route("/api/score")
def api_score():
    return jsonify(score(write_csv=False))



# ── meeting-level labelling ───────────────────────────────────────────────────
# A meeting is not an item: its time, type, room, roll call and staff are properties of the
# hearing, shared by every item heard at it. `../meeting_headers.py` cuts the header window
# around each marked boundary and pre-fills those fields; these routes are where they get
# confirmed.
def _mh():
    import meeting_headers
    return meeting_headers


@app.route("/meetings")
def meetings_page():
    return render_template("meetings.html")


@app.route("/api/meetings")
def api_meetings():
    con = db()
    rows = con.execute(
        "SELECT source_file, line_no, meeting_date, origin, status, data FROM meetings "
        "ORDER BY meeting_date, source_file, line_no").fetchall()
    con.close()
    out = []
    for r in rows:
        rec = json.loads(r["data"]) if r["data"] else {}
        out.append(dict(source_file=r["source_file"], line_no=r["line_no"],
                        meeting_date=r["meeting_date"], origin=r["origin"],
                        status=r["status"], meeting_type=rec.get("meeting_type", "")))
    stats = Counter(r["status"] for r in out)
    return jsonify(meetings=out, stats=dict(stats), schema=_mh().MEETING_SCHEMA)


@app.route("/api/meeting")
def api_meeting():
    con = db()
    r = con.execute("SELECT * FROM meetings WHERE source_file=? AND line_no=?",
                    (request.args["src"], int(request.args["line"]))).fetchone()
    con.close()
    if not r:
        return jsonify(error="no such meeting"), 404
    return jsonify(source_file=r["source_file"], line_no=r["line_no"],
                   meeting_date=r["meeting_date"], origin=r["origin"],
                   status=r["status"], date_line=r["date_line"],
                   window_text=r["window_text"],
                   data=json.loads(r["data"]) if r["data"] else {})


@app.route("/api/meeting/save", methods=["POST"])
def api_meeting_save():
    body = request.get_json(force=True)
    con = db()
    con.execute("UPDATE meetings SET data=?, status=?, updated_at=? "
                "WHERE source_file=? AND line_no=?",
                (json.dumps(body.get("data", {}), ensure_ascii=False),
                 body.get("status", "done"),
                 datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 body["src"], int(body["line"])))
    con.commit()
    con.close()
    return jsonify(ok=True)


@app.route("/api/meetings/export", methods=["POST"])
def api_meetings_export():
    con = db()
    path = _mh().export(con)
    n = con.execute("SELECT COUNT(*) FROM meetings WHERE status='done'").fetchone()[0]
    con.close()
    return jsonify(path=str(path), confirmed=n)


# ── scoring: gold marks vs what the pipeline inferred ─────────────────────────
def gold_by_doc(con) -> dict[str, list[tuple[int, str]]]:
    out = defaultdict(list)
    for r in con.execute("SELECT source_file, line_no, meeting_date FROM boundaries "
                         "ORDER BY source_file, line_no"):
        out[r[0]].append((r[1], r[2]))
    return out


def line_starts(text: str) -> list[int]:
    """Character offset at which each line begins."""
    offs, pos = [], 0
    for ln in text.split("\n"):
        offs.append(pos)
        pos += len(ln) + 1
    return offs


def score(write_csv: bool = False) -> dict:
    con = db()
    gold = gold_by_doc(con)
    done = {r[0] for r in con.execute("SELECT source_file FROM docs WHERE status='done'")}
    con.close()
    if not gold:
        return {"docs": 0, "note": "no gold marks yet — mark some documents first"}

    lab = sqlite3.connect(LABELS_DB) if LABELS_DB.exists() else None
    rows_out, b_tp = [], 0
    p_tp, p_gold, p_pred = [0], [0], [0]
    b_gold = b_pred = 0
    blk_hit = blk_tot = 0
    for src, marks in sorted(gold.items()):
        path = doc_path(src)
        if not path:
            continue
        text = doc_text(path)
        starts = line_starts(text)
        gold_pts = [(starts[ln] if ln < len(starts) else 0, d) for ln, d in marks]

        # (a) boundary level — did the detector find the same meeting starts?
        pred = AD.header_dates(text)
        if not pred:
            fb = AD.title_date(text) or AD.stem_date(src)
            pred = [(0, fb)] if fb else []
        gd = {d for _o, d in gold_pts}
        pd = {d for _o, d in pred}
        b_gold += len(gd); b_pred += len(pd); b_tp += len(gd & pd)

        # Positional agreement, which the date-set comparison cannot see: two meetings held
        # on the same day (a regular session followed by a joint session) share a date, so a
        # missed boundary between them is invisible above. Match each gold mark to the
        # nearest detected header of the same date within POS_TOL characters.
        used, pos_tp = set(), 0
        for goff, gdate in gold_pts:
            best, bestd = None, None
            for k, (poff, pdate) in enumerate(pred):
                if k in used or pdate != gdate:
                    continue
                dist = abs(poff - goff)
                if bestd is None or dist < bestd:
                    best, bestd = k, dist
            if best is not None and bestd <= POS_TOL:
                used.add(best); pos_tp += 1
        p_tp[0] += pos_tp; p_gold[0] += len(gold_pts); p_pred[0] += len(pred)
        extra = [f"{d}@{o}" for k, (o, d) in enumerate(pred) if k not in used]

        # (b) block level — did every parsed item get the right date?
        n_blk = n_ok = 0
        if lab:
            stem_src = src.rsplit(".", 1)[0] if src.endswith((".html", ".htm")) else src
            items = lab.execute(
                "SELECT id, block_text, meeting_date FROM items WHERE source_file=? "
                "ORDER BY item_index", (stem_src,)).fetchall()
            if items:
                offs = AD.block_offsets(text, [i[1] for i in items])
                last = None
                for (iid, _b, have), off in zip(items, offs):
                    if off is None:
                        want = last
                    else:
                        prior = [d for p, d in gold_pts if p <= off]
                        want = prior[-1] if prior else (gold_pts[0][1] if gold_pts else None)
                    last = want or last
                    if not want:
                        continue
                    n_blk += 1
                    n_ok += (have == want)
                blk_hit += n_ok; blk_tot += n_blk
        rows_out.append(dict(source_file=src, gold_dates=" ".join(sorted(gd)),
                             pred_dates=" ".join(sorted(pd)),
                             boundary_match="yes" if gd == pd else "NO",
                             detected_not_marked=" ".join(extra),
                             blocks=n_blk, blocks_correct=n_ok,
                             marked_done=src in done))
    if lab:
        lab.close()

    res = dict(docs=len(rows_out),
               boundary_precision=round(b_tp / b_pred, 4) if b_pred else None,
               boundary_recall=round(b_tp / b_gold, 4) if b_gold else None,
               positional_precision=round(p_tp[0] / p_pred[0], 4) if p_pred[0] else None,
               positional_recall=round(p_tp[0] / p_gold[0], 4) if p_gold[0] else None,
               detected_not_marked=[dict(source_file=r["source_file"],
                                         headers=r["detected_not_marked"])
                                    for r in rows_out if r["detected_not_marked"]],
               docs_with_exact_boundary_set=sum(1 for r in rows_out
                                                if r["boundary_match"] == "yes"),
               blocks_scored=blk_tot,
               block_date_accuracy=round(blk_hit / blk_tot, 4) if blk_tot else None,
               mismatches=[r for r in rows_out if r["boundary_match"] == "NO"
                           or r["blocks"] != r["blocks_correct"]][:50])
    if write_csv:
        import csv
        out = HERE / "date_gold_score.csv"
        with out.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows_out[0].keys()))
            w.writeheader(); w.writerows(rows_out)
        res["csv"] = str(out)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5006)
    ap.add_argument("--score", action="store_true", help="print gold-vs-pipeline score")
    ap.add_argument("--csv", action="store_true", help="with --score, write the per-doc CSV")
    ap.add_argument("--rescan", action="store_true", help="re-read the corpus queue")
    a = ap.parse_args()

    con = db()
    init_db(con)
    added = scan_corpus(con)
    if added or a.rescan or not con.execute(
            "SELECT 1 FROM docs WHERE in_sample=1").fetchone():
        chosen = pick_sample(con)
        print(f"queue: +{added} documents | sample month per year: "
              + ", ".join(f"{y}:{m[5:]}" for y, m in sorted(chosen.items())))
    n_docs, n_sample = con.execute(
        "SELECT COUNT(*), SUM(in_sample) FROM docs").fetchone()
    con.close()

    if a.score:
        print(json.dumps(score(write_csv=a.csv), indent=2)[:4000])
        return

    print(f"{n_docs} documents ({n_sample} in the one-month-per-year sample)")
    print(f"→ http://127.0.0.1:{a.port}")
    app.run(host="127.0.0.1", port=a.port, debug=False)


if __name__ == "__main__":
    main()
