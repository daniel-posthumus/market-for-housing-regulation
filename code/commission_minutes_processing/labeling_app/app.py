#!/usr/bin/env python3
"""
app.py — local web app for hand-labeling individual planning-commission meeting
items against the full SCHEMA (extraction_common.SCHEMA).

Workflow: ingest.py builds a SQLite work-queue of project blocks with a machine
pre-fill; you open each item, see the raw text beside a pre-filled schema form,
correct it, and save. Export writes confirmed labels back to the per-year
{year}_labeled.json that training_sample_create.py consumes.

Run:
  python ingest.py            # once, to build the queue
  python app.py               # serves http://127.0.0.1:5005
Nothing leaves your machine unless you use the optional Anthropic pre-fill.
"""
from __future__ import annotations
import json, re, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from extraction_common import (SCHEMA, FIELDS, SECTIONS, coerce_record,  # noqa: E402
                               PROMPT_INSTRUCTION, parse_obj)
from autoextract import extract                                          # noqa: E402
import queue_order                                                       # noqa: E402

# labels that count as "gold so far" when measuring class balance for the queue.
CONFIRMED_STATUSES = ("done", "prelabeled", "flagged")

DB = HERE / "labels.db"
from paths import MEETING_MINUTES
BASE = MEETING_MINUTES
TRAIN_DIR = BASE / "tagged" / "training"

app = Flask(__name__, static_folder=str(HERE / "static"),
            template_folder=str(HERE / "templates"))


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── QA-flag introspection ─────────────────────────────────────────
# label_qa.py records why an item was flagged in labels.notes, as either
#   "[QA-backfilled: action,absent]"   (empty fields it auto-filled), or
#   "[QA] action_other: …; ayes_missing: …"  (suspect values, not filled).
# _qa_fields() distils a note down to the set of schema fields involved, so the
# UI can focus on one field at a time and tell when it's the ONLY thing to fix.
_BACKFILL_RE = re.compile(r"\[QA-backfilled:\s*([^\]]+)\]")
_QA_RE = re.compile(r"\[QA\]\s*(.+)$")
_CHECK_FIELD = {
    "action_other": "action", "action_mismatch": "action",
    "case_number_missing": "case_number", "request_type_blank": "request_type",
    "ayes_missing": "ayes", "noes_missing": "noes", "absent_missing": "absent",
    "district_missing": "type_district",
}


def _qa_fields(notes: str) -> list[str]:
    """Schema fields a QA note touches (back-filled or flagged), sorted."""
    notes = notes or ""
    fields: set[str] = set()
    m = _BACKFILL_RE.search(notes)
    if m:
        fields.update(f.strip() for f in m.group(1).split(",") if f.strip())
    qa = _QA_RE.search(notes)
    if qa:
        for part in qa.group(1).split(";"):
            name = part.split(":")[0].strip().strip(".")
            for check, fld in _CHECK_FIELD.items():
                if name.startswith(check):
                    fields.add(fld)
            if "action" in name.lower():     # e.g. "source ACTION→'other'"
                fields.add("action")
    return sorted(fields)


# ───────────────────────────── pages ─────────────────────────────
@app.get("/")
def index():
    return send_from_directory(HERE / "templates", "index.html")


# ───────────────────────────── api ─────────────────────────────
@app.get("/api/schema")
def api_schema():
    return jsonify({"schema": SCHEMA, "sections": SECTIONS, "fields": FIELDS})


@app.get("/api/items")
def api_items():
    status = request.args.get("status", "")
    year = request.args.get("year", "")
    q = request.args.get("q", "").strip()
    order = request.args.get("order", "priority")     # "priority" | "chrono"
    focus = request.args.get("focus", "").strip()     # only flags touching this field
    limit = int(request.args.get("limit", "5000"))
    where, params = [], []
    if status:
        where.append("l.status = ?"); params.append(status)
    if year:
        where.append("i.year = ?"); params.append(int(year))
    if q:
        where.append("(i.case_number LIKE ? OR i.block_text LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    # data is needed to score rarity; cheap enough at this corpus size.
    sql = ("SELECT i.id, i.year, i.meeting_date, i.case_number, i.source_file, "
           "i.item_index, l.status, l.flagged, l.data, l.notes "
           "FROM items i JOIN labels l ON l.item_id = i.id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    con = db()
    rows = [dict(r) for r in con.execute(sql, params).fetchall()]

    for r in rows:                                    # distil QA notes → fields
        r["qa_fields"] = _qa_fields(r.get("notes"))
    if focus:                                         # keep only flags touching `focus`
        rows = [r for r in rows if focus in r["qa_fields"]]

    if order == "priority":
        confirmed = [d for (d,) in con.execute(
            "SELECT data FROM labels WHERE status IN (%s)" %
            ",".join("?" * len(CONFIRMED_STATUSES)), CONFIRMED_STATUSES).fetchall()]
        rows = queue_order.prioritize(rows, confirmed)
    else:
        rows.sort(key=lambda r: (r["year"], r["source_file"], r["item_index"]))
    # When focusing one field, float items where it's the ONLY flag to the top —
    # those are the quick "just fix `focus` and Save" cases. Stable: keeps order.
    if focus:
        rows.sort(key=lambda r: r["qa_fields"] != [focus])
    con.close()

    rows = rows[:limit]
    for r in rows:                                    # don't ship block data to the list
        r.pop("data", None); r.pop("item_index", None); r.pop("notes", None)
        r["qa_only"] = r["qa_fields"] == [focus] if focus else False
    return jsonify(rows)


@app.get("/api/item/<int:item_id>")
def api_item(item_id):
    con = db()
    it = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    lb = con.execute("SELECT * FROM labels WHERE item_id=?", (item_id,)).fetchone()
    con.close()
    if not it:
        return jsonify({"error": "not found"}), 404
    data = json.loads(lb["data"]) if lb and lb["data"] else coerce_record({})
    return jsonify({
        "id": it["id"], "year": it["year"], "meeting_date": it["meeting_date"],
        "case_number": it["case_number"], "source_file": it["source_file"],
        "block_text": it["block_text"],
        "label": {"data": coerce_record(data),
                  "status": lb["status"] if lb else "todo",
                  "flagged": bool(lb["flagged"]) if lb else False,
                  "notes": lb["notes"] if lb else ""},
    })


@app.post("/api/item/<int:item_id>")
def api_save(item_id):
    body = request.get_json(force=True)
    data = coerce_record(body.get("data", {}))
    status = body.get("status", "done")
    flagged = 1 if body.get("flagged") else 0
    notes = body.get("notes", "")
    con = db()
    con.execute(
        "INSERT INTO labels(item_id,data,status,flagged,notes,updated_at) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET "
        "data=excluded.data, status=excluded.status, flagged=excluded.flagged, "
        "notes=excluded.notes, updated_at=excluded.updated_at",
        (item_id, json.dumps(data, ensure_ascii=False), status, flagged, notes, _now()))
    con.commit(); con.close()
    return jsonify({"ok": True})


@app.post("/api/prefill/<int:item_id>")
def api_prefill(item_id):
    backend = request.args.get("backend", "heuristic")
    con = db()
    it = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    con.close()
    if not it:
        return jsonify({"error": "not found"}), 404
    if backend == "anthropic":
        try:
            rec = _anthropic_prefill(it["block_text"], it["meeting_date"])
        except Exception as e:
            return jsonify({"error": f"anthropic prefill failed: {e}"}), 500
    else:
        rec = extract(it["block_text"], meeting_date=it["meeting_date"])
    return jsonify({"data": coerce_record(rec)})


def _anthropic_prefill(block: str, meeting_date: str) -> dict:
    import anthropic
    client = anthropic.Anthropic()
    model = "claude-haiku-4-5-20251001"
    msg = client.messages.create(
        model=model, max_tokens=1500,
        messages=[{"role": "user",
                   "content": PROMPT_INSTRUCTION + block +
                   f"\n\n(meeting_date is {meeting_date})"},
                  {"role": "assistant", "content": "{"}])
    obj = parse_obj("{" + msg.content[0].text) or {}
    if not obj.get("meeting_date"):
        obj["meeting_date"] = meeting_date
    return obj


@app.get("/api/stats")
def api_stats():
    con = db()
    by_status = dict(con.execute(
        "SELECT status, COUNT(*) FROM labels GROUP BY status").fetchall())
    by_year = con.execute(
        "SELECT i.year, l.status, COUNT(*) FROM items i JOIN labels l "
        "ON l.item_id=i.id GROUP BY i.year, l.status").fetchall()
    total = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    con.close()
    yd = {}
    for y, s, c in by_year:
        yd.setdefault(y, {})[s] = c
    return jsonify({"total": total, "by_status": by_status, "by_year": yd})


@app.post("/api/export")
def api_export():
    """Write confirmed (status='done') labels to per-year {year}_labeled.json,
    backing up any existing file to <name>.preexport.bak."""
    only_done = request.args.get("all", "0") != "1"
    con = db()
    rows = con.execute(
        "SELECT i.year, l.data, l.status FROM items i JOIN labels l "
        "ON l.item_id=i.id").fetchall()
    con.close()
    by_year = {}
    for year, data, status in rows:
        if only_done and status != "done":
            continue
        by_year.setdefault(year, []).append(coerce_record(json.loads(data)))
    written = {}
    for year, recs in by_year.items():
        out = TRAIN_DIR / f"{year}_labeled.json"
        if out.exists():
            out.with_suffix(".json.preexport.bak").write_text(
                out.read_text(encoding="utf-8"), encoding="utf-8")
        out.write_text(json.dumps(recs, ensure_ascii=False, indent=4) + "\n",
                       encoding="utf-8")
        written[year] = len(recs)
    return jsonify({"written": written, "total": sum(written.values()),
                    "filter": "done" if only_done else "all"})


@app.get("/static/<path:p>")
def static_files(p):
    return send_from_directory(HERE / "static", p)


if __name__ == "__main__":
    if not DB.exists():
        print("No labels.db — run `python ingest.py` first.")
        sys.exit(1)
    app.run(host="127.0.0.1", port=5005, debug=False)
