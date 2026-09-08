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
                               is_empty,
                               DERIVED_FIELDS, UNMEASURABLE_FIELDS,
                               VALIDATION_ONLY_FIELDS, EVIDENCE_FIELDS,
                               SCHEMA_VERSION, era_of, build_prompt, item_suffix,
                               prompt_sha, unwrap_evidence, parse_obj)
from normalize import (normalize_record, descr_proposal, address_core,   # noqa: E402
                       address_format_ok)
from autoextract import extract                                          # noqa: E402
import meeting_headers                                                   # noqa: E402
import queue_order                                                       # noqa: E402
import review_queue                                                      # noqa: E402
import provenance                                                        # noqa: E402

# labels that count as "gold so far" when measuring class balance for the queue.
CONFIRMED_STATUSES = ("done", "prelabeled", "flagged")

DB = HERE / "labels.db"
from paths import MEETING_MINUTES
BASE = MEETING_MINUTES
TRAIN_DIR = BASE / "tagged" / "training"

# ── the meeting an item was heard at ──────────────────────────────────────────
# Date, meeting type, room and roll call are properties of the HEARING, not of the item, so
# they are not labeled here — they are read once per meeting by the meeting-level pipeline
# and shown above the form as context. `meeting_ordinal` (written by assign_meeting_dates)
# is what makes the join exact where a day holds two meetings.
_MEETINGS = meeting_headers.meeting_lookup()
_MEETING_SHOW = ["meeting_type", "meeting_time", "location", "joint_body",
                 "presiding", "present", "absent", "staff"]


def _has_ordinal() -> bool:
    """A labels.db built before the date stage last ran has no `meeting_ordinal`. Fall back
    to the date rather than failing the item list; the join is then ambiguous on the seven
    documents that hold two meetings on one day, and `assign_meeting_dates.py --apply` is
    what fixes that."""
    if not DB.exists():
        return False
    con = sqlite3.connect(DB)
    cols = {r[1] for r in con.execute("PRAGMA table_info(items)")}
    con.close()
    return "meeting_ordinal" in cols


HAS_ORDINAL = _has_ordinal()


def meeting_for(row) -> dict | None:
    keys = row.keys() if hasattr(row, "keys") else row
    m = _MEETINGS(row["source_file"], row["meeting_date"] or "",
                  row["meeting_ordinal"] if "meeting_ordinal" in keys else None)
    if not m:
        return None
    out = {k: (m.get(k) or "") for k in _MEETING_SHOW}
    out["meeting_date"] = m["meeting_date"]
    out["source_file"] = m["source_file"]
    out["hand_verified"] = bool(m.get("hand_verified"))
    out["era"] = m.get("era") or ""
    return out


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
# "[CHECK: action,units_proposed]" — written by the gold-set review pass. Same idea as
# QA-backfilled (these are the fields to look at), but it names fields the machine wants a
# second opinion on rather than ones it filled.
_CHECK_RE = re.compile(r"\[CHECK:\s*([^\]]+)\]")
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
    for rx in (_BACKFILL_RE, _CHECK_RE):
        m = rx.search(notes)
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
    era = request.args.get("era", "")                 # html_1998_2014 | pdf_2015_2026
    order = request.args.get("order", "priority")     # "priority" | "chrono"
    focus = request.args.get("focus", "").strip()     # only flags touching this field
    limit = int(request.args.get("limit", "5000"))
    where, params = [], []
    if status == "adjudicate":
        # not a label status: items carrying an unjudged machine/human disagreement
        where.append("i.id IN (SELECT item_id FROM adjudications WHERE verdict IS NULL)")
    elif status:
        where.append("l.status = ?"); params.append(status)
    if year:
        where.append("i.year = ?"); params.append(int(year))
    # Era is a document-format fact, not a label: the HTML and PDF minutes lay an item out
    # differently, so being able to work one era at a time is worth a filter.
    if era == "html_1998_2014":
        where.append("i.year <= 2014")
    elif era == "pdf_2015_2026":
        where.append("i.year > 2014")
    if q:
        where.append("(i.case_number LIKE ? OR i.block_text LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    # data is needed to score rarity; cheap enough at this corpus size.
    # `not_an_item` blocks (no case number: public comment, the director's report, the
    # weekly BOS/BOA recap, adjournment) are excluded unless explicitly asked for — they are
    # agenda scaffolding, not discretionary land-use decisions.
    if status not in ("not_an_item", "adjudicate"):
        where.append("l.status != 'not_an_item'")
    sql = ("SELECT i.id, i.year, i.meeting_date, i.case_number, i.source_file, "
           "i.item_index, l.status, l.flagged, l.data, l.notes"
           + (", i.meeting_ordinal " if HAS_ORDINAL else " ") +
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
        m = meeting_for(r)
        r["meeting_type"] = (m or {}).get("meeting_type", "")
        r["era"] = era_of(r["year"])
        r.pop("data", None); r.pop("item_index", None); r.pop("notes", None)
        r.pop("meeting_ordinal", None)
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
    rec = coerce_record(data)
    descr_text, descr_rule = descr_proposal(it["block_text"] or "")
    return jsonify({
        "id": it["id"], "year": it["year"], "meeting_date": it["meeting_date"],
        "case_number": it["case_number"], "source_file": it["source_file"],
        "block_text": it["block_text"], "meeting": meeting_for(it),
        "era": era_of(it["year"]),
        # The one-click Request-for insert (§7.1). Computed server-side so the app button
        # and the migration script's proposal come from the same rule, not two copies of it.
        "descr_proposal": {"text": descr_text, "rule": descr_rule},
        "address_ok": address_format_ok(rec.get("project_address")),
        "address_core": address_core(rec.get("project_address")),
        "evidence": _evidence_for(item_id),
        "label": {"data": rec,
                  "status": lb["status"] if lb else "todo",
                  "flagged": bool(lb["flagged"]) if lb else False,
                  "notes": lb["notes"] if lb else ""},
    })


# Model evidence spans, if any run recorded them. Highlighting the span inside the block is
# what makes an adjudication fast to judge (§7.2), so a missing table is a degraded display,
# never an error.
def _evidence_for(item_id: int) -> dict:
    con = db()
    try:
        rows = con.execute(
            """SELECT p.data, p.raw FROM predictions p JOIN extraction_runs r
                 ON r.run_id = p.run_id
               WHERE p.item_id=? ORDER BY r.created_at DESC LIMIT 1""",
            (item_id,)).fetchone()
    except sqlite3.OperationalError:
        rows = None
    con.close()
    if not rows or not rows["raw"]:
        return {}
    try:
        _, spans, _ = unwrap_evidence(json.loads(rows["raw"]), "")
    except Exception:
        return {}
    return {k: v for k, v in spans.items() if v}


@app.post("/api/item/<int:item_id>")
def api_save(item_id):
    body = request.get_json(force=True)
    # Normalise here, not in the browser: one storage rule, applied to hand labels and model
    # output alike, so a gold value and a predicted value are never compared across a
    # formatting difference that neither side chose.
    data = normalize_record(coerce_record(body.get("data", {})))
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
            rec = _anthropic_prefill(it["block_text"], it["meeting_date"], it["year"],
                                     item_id)
        except Exception as e:
            return jsonify({"error": f"anthropic prefill failed: {e}"}), 500
    else:
        rec = extract(it["block_text"], meeting_date=it["meeting_date"])
    return jsonify({"data": coerce_record(rec)})


# The old path pre-filled the assistant turn with a bare "{" and pinned Haiku 4.5, because
# that was the only way to force JSON before structured outputs existed. It 400s on Sonnet 5,
# Opus 5 and the whole 4.6+ family. Structured outputs make the reply valid by construction
# and remove the pin — same mechanism the bakeoff harness uses, so the app's pre-fill and the
# measured configuration are the same thing rather than two that drift.
PREFILL_MODEL = "claude-haiku-4-5"
PREFILL_SHOTS = 6


def _prefill_pool():
    """The train half of the frozen split — never the test half, which would leak the
    measurement into the labels. Loaded once, lazily: it reads every gold block."""
    global _POOL
    try:
        return _POOL
    except NameError:
        pass
    import bakeoff_extract as BX
    split = HERE.parent / "bakeoff" / "split.json"
    train = set(json.loads(split.read_text())["train"]) if split.exists() else None
    pool = [e for e in BX.gold() if train is None or e["id"] in train]
    for e in pool:
        e["_w"] = BX._words(e["block"])
    _POOL = pool
    return _POOL


def _anthropic_prefill(block: str, meeting_date: str, year: int = 0,
                       item_id: int = 0) -> dict:
    import bakeoff_extract as BX
    cl = BX.client()
    era = era_of(year) if year else None
    examples = BX.few_shot_block(block, _prefill_pool(), PREFILL_SHOTS, era) \
        if PREFILL_SHOTS else ""
    prefix = examples + build_prompt(era)
    msg = cl.messages.create(
        model=PREFILL_MODEL, max_tokens=4000,
        output_config={"format": {"type": "json_schema", "schema": BX.json_schema()}},
        messages=[{"role": "user", "content": prefix + item_suffix(block)}])
    raw = json.loads(next(c.text for c in msg.content if c.type == "text"))
    flat, _, fails = unwrap_evidence(raw, block)
    flat.pop("meeting_date", None)     # the date stage owns this, not the model
    rec = normalize_record(coerce_record(flat))
    # Recorded like any other extraction (§6): a pre-fill the labeller accepts becomes gold,
    # so which prompt and model produced it has to be answerable later. It is also what makes
    # the evidence spans available for highlighting the next time the item is opened.
    if item_id:
        try:
            run_id = provenance.start_run(
                PREFILL_MODEL, f"app-prefill-fs{PREFILL_SHOTS}", prompt_sha(prefix),
                shots=PREFILL_SHOTS, n_items=1, note="labelling-app pre-fill", db=DB)
            provenance.save_predictions(run_id, {item_id: rec}, {item_id: raw}, db=DB)
            provenance.log_failures(
                run_id, [(item_id, f["field"], f["reason"], str(f["value"])[:300],
                          str(f["evidence"])[:300]) for f in fails], db=DB)
        except Exception:
            pass       # a provenance write must never cost the labeller their pre-fill
    return rec


# ── adjudication ──────────────────────────────────────────────────────────────
# Where the machine and the hand label disagree, one of them is wrong and it is not always
# the machine — five gold errors have already been confirmed this way. These routes let a
# disagreement be judged directly, field by field, instead of being argued about in the
# aggregate.
@app.get("/api/adjudications/<int:item_id>")
def api_adjudications(item_id):
    con = db()
    try:
        rows = con.execute(
            "SELECT field, model, gold, pred, verdict FROM adjudications "
            "WHERE item_id=? ORDER BY field", (item_id,)).fetchall()
    except sqlite3.OperationalError:
        rows = []                       # table not built yet
    con.close()
    out = []
    for r in rows:
        out.append({"field": r["field"], "model": r["model"],
                    "gold": json.loads(r["gold"]) if r["gold"] else "",
                    "pred": json.loads(r["pred"]) if r["pred"] else "",
                    "verdict": r["verdict"]})
    return jsonify(out)


@app.post("/api/adjudication")
def api_adjudicate():
    b = request.get_json(force=True)
    if b.get("verdict") not in ("gold", "model", "both", None):
        return jsonify({"error": "bad verdict"}), 400
    con = db()
    cur = con.execute("UPDATE adjudications SET verdict=?, updated_at=? "
                      "WHERE item_id=? AND field=? AND model=?",
                      (b.get("verdict"), _now(), b["item_id"], b["field"], b["model"]))
    con.commit(); n = cur.rowcount; con.close()
    if not n:
        # a silent no-op here would look like a recorded verdict and quietly lose it
        return jsonify({"error": "no such disagreement queued", **b}), 404
    return jsonify({"ok": True})


# ── the unified review queue (spec §7.2) ─────────────────────────────────────
# One queue, four typed reasons, field-level wherever possible. The point is that migration
# review, adjudication and new labelling are one sitting rather than three flows — and that
# the same field lands consecutively, because 232 `project_descr` values in a row is far
# faster and more consistent than 232 context switches.
def _jload(v, default=""):
    if v is None or v == "":
        return default
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        return v


@app.get("/api/queue")
def api_queue():
    reason = request.args.get("reason", "")
    field = request.args.get("field", "")
    item_id = request.args.get("item_id", "")
    status = request.args.get("status", "open")
    limit = int(request.args.get("limit", "3000"))
    where, params = [], []
    for col, val in (("q.reason", reason), ("q.field", field), ("q.status", status)):
        if val:
            where.append(f"{col} = ?"); params.append(val)
    if item_id:
        where.append("q.item_id = ?"); params.append(int(item_id))
    con = db()
    try:
        rows = con.execute(
            "SELECT q.*, i.case_number, i.year, i.meeting_date FROM review_queue q "
            "JOIN items i ON i.id = q.item_id"
            + (" WHERE " + " AND ".join(where) if where else "")
            + " ORDER BY q.sort_key LIMIT ?", params + [limit]).fetchall()
    except sqlite3.OperationalError:
        rows = []                       # table not built yet
    con.close()
    return jsonify([{"id": r["id"], "item_id": r["item_id"], "field": r["field"],
                     "reason": r["reason"], "detail": r["detail"], "rule": r["rule"],
                     "old_value": _jload(r["old_value"]),
                     "proposed": _jload(r["proposed"]),
                     "evidence": r["evidence"] or "", "model": r["model"] or "",
                     "status": r["status"], "verdict": r["verdict"],
                     "case_number": r["case_number"], "year": r["year"],
                     "era": era_of(r["year"]), "meeting_date": r["meeting_date"]}
                    for r in rows])


@app.get("/api/queue/summary")
def api_queue_summary():
    try:
        rows = review_queue.tally(DB)
    except sqlite3.OperationalError:
        rows = []
    out = {}
    for reason, field, status, n in rows:
        out.setdefault(reason, {"open": 0, "done": 0, "skipped": 0, "fields": {}})
        out[reason][status] = out[reason].get(status, 0) + n
        if status == "open":
            out[reason]["fields"][field] = n
    return jsonify(out)


@app.post("/api/queue/<int:row_id>")
def api_queue_resolve(row_id):
    """Resolve one queue row.

    `action` is accept | edit | skip | reopen, and for adjudication rows the verdict
    (gold | model | both) says which value wins. Accepting writes the value into the label
    — that is the whole point of the queue: the gold set improves as it is worked, rather
    than accumulating a second store of corrections nobody merges.
    """
    b = request.get_json(force=True)
    action = b.get("action", "accept")
    con = db()
    r = con.execute("SELECT * FROM review_queue WHERE id=?", (row_id,)).fetchone()
    if not r:
        con.close()
        return jsonify({"error": "no such queue row"}), 404
    if action == "skip":
        con.execute("UPDATE review_queue SET status='skipped', updated_at=? WHERE id=?",
                    (_now(), row_id))
        con.commit(); con.close()
        return jsonify({"ok": True, "status": "skipped"})
    if action == "reopen":
        con.execute("UPDATE review_queue SET status='open', verdict=NULL, updated_at=? "
                    "WHERE id=?", (_now(), row_id))
        con.commit(); con.close()
        return jsonify({"ok": True, "status": "open"})

    verdict = b.get("verdict")
    if r["reason"] == "adjudication":
        if verdict not in ("gold", "model", "both"):
            con.close()
            return jsonify({"error": "adjudication needs a verdict"}), 400
        # 'gold' leaves the label alone; 'model' adopts the model's value; 'both' takes an
        # explicitly supplied value, because neither side was right.
        value = (_jload(r["proposed"]) if verdict == "model"
                 else b["value"] if "value" in b else None)
        # keep the store of record in step — `--tally` and the memo read it
        con.execute("UPDATE adjudications SET verdict=?, updated_at=? "
                    "WHERE item_id=? AND field=? AND model=?",
                    (verdict, _now(), r["item_id"], r["field"], r["model"] or ""))
    else:
        value = b["value"] if "value" in b else _jload(r["proposed"])
        # A blank proposal must never overwrite a real label. The rule that generated the
        # proposal can fail (no recognised opening phrase in the block); the labeller's value
        # is then the only answer there is, and accepting a blank would delete it.
        if is_empty(value) and not b.get("force"):
            lb = con.execute("SELECT data FROM labels WHERE item_id=?",
                             (r["item_id"],)).fetchone()
            cur = json.loads(lb["data"]) if lb and lb["data"] else {}
            if not is_empty(cur.get(r["field"])):
                con.close()
                return jsonify({"error": "that proposal is blank and the field is not — "
                                         "edit the field and use the form value instead",
                                "field": r["field"]}), 409

    if value is not None and r["field"]:
        lb = con.execute("SELECT data FROM labels WHERE item_id=?", (r["item_id"],)).fetchone()
        rec = coerce_record(json.loads(lb["data"]) if lb and lb["data"] else {})
        # One decision can set more than one field — splitting `resolution_or_motion_no`
        # into an instrument and a number is one judgement, not two — so a proposal that
        # arrives as a {field: value} map is applied whole.
        if isinstance(value, dict) and value and all(k in FIELDS for k in value):
            rec.update(value)
        else:
            rec[r["field"]] = value
        rec = normalize_record(coerce_record(rec))
        con.execute("UPDATE labels SET data=?, updated_at=? WHERE item_id=?",
                    (json.dumps(rec, ensure_ascii=False), _now(), r["item_id"]))
    con.execute("UPDATE review_queue SET status='done', verdict=?, resolved=?, updated_at=? "
                "WHERE id=?",
                (verdict, json.dumps(value, ensure_ascii=False) if value is not None else None,
                 _now(), row_id))
    con.commit(); con.close()
    return jsonify({"ok": True, "status": "done"})


@app.get("/api/focus_fields")
def api_focus_fields():
    """Every field any note asks to be checked, so the focus dropdown reflects the actual
    backlog instead of a list hard-coded in the template."""
    con = db()
    notes = [n for (n,) in con.execute(
        "SELECT notes FROM labels WHERE notes IS NOT NULL AND notes != ''")]
    con.close()
    seen = set()
    for n in notes:
        seen.update(_qa_fields(n))
    return jsonify(sorted(f for f in seen if f in FIELDS))


@app.get("/api/stats")
def api_stats():
    con = db()
    by_status = dict(con.execute(
        "SELECT status, COUNT(*) FROM labels GROUP BY status").fetchall())
    by_year = con.execute(
        "SELECT i.year, l.status, COUNT(*) FROM items i JOIN labels l "
        "ON l.item_id=i.id GROUP BY i.year, l.status").fetchall()
    total = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    try:
        adj = con.execute("SELECT COUNT(*) FROM adjudications WHERE verdict IS NULL").fetchone()[0]
        adj_items = con.execute("SELECT COUNT(DISTINCT item_id) FROM adjudications "
                                "WHERE verdict IS NULL").fetchone()[0]
    except sqlite3.OperationalError:
        adj = adj_items = 0
    con.close()
    yd = {}
    for y, s, c in by_year:
        yd.setdefault(y, {})[s] = c
    try:
        qopen = con2 = None
        con2 = db()
        qopen = con2.execute("SELECT COUNT(*) FROM review_queue WHERE status='open'"
                             ).fetchone()[0]
        qitems = con2.execute("SELECT COUNT(DISTINCT item_id) FROM review_queue "
                              "WHERE status='open'").fetchone()[0]
    except sqlite3.OperationalError:
        qopen = qitems = 0
    finally:
        if con2:
            con2.close()
    return jsonify({"total": total, "by_status": by_status, "by_year": yd,
                    "adjudications_pending": adj, "adjudication_items": adj_items,
                    "queue_open": qopen, "queue_items": qitems,
                    "schema_version": SCHEMA_VERSION})


@app.post("/api/export")
def api_export():
    """Write confirmed (status='done') labels to per-year {year}_labeled.json,
    backing up any existing file to <name>.preexport.bak."""
    only_done = request.args.get("all", "0") != "1"
    con = db()
    rows = con.execute(
        "SELECT i.year, i.meeting_date, i.source_file, l.data, l.status FROM items i "
        "JOIN labels l ON l.item_id=i.id").fetchall()
    con.close()
    by_year = {}
    for year, meeting_date, source_file, data, status in rows:
        if only_done and status != "done":
            continue
        rec = coerce_record(json.loads(data))
        # meeting_date is not a labeled field — it is attached here from the item, where
        # assign_meeting_dates.py put it, so the exported record still carries a date.
        rec["meeting_date"] = meeting_date
        rec["source_file"] = source_file
        by_year.setdefault(year, []).append(rec)
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
