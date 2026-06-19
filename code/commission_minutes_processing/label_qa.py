#!/usr/bin/env python3
"""
label_qa.py — audit the existing hand-labels against their source blocks and tee
up the fix pass.

This is the gate before the 269 consolidated labels become gold. Rather than
re-eyeballing every label, it diffs each one against what the raw block actually
says (reusing autoextract's source regexes) and surfaces the *likely-wrong* ones,
ranked, so you fix those first. The checks encode the concrete failure modes the
processing review found (processing_review.md, Part B):

  action_other          stored `action` coerced to 'other' (e.g. a continuance
                        *proposal* string, not a disposition enum)        [HIGH]
  action_mismatch       source ACTION: line implies a different disposition [HIGH]
  case_number_missing   label has no case number but the block prints one   [HIGH]
  ayes_missing          block has an AYES: roll-call, label's ayes empty    [MED]
  noes_missing          block has a NOES: line, label's noes empty          [MED]
  absent_missing        block has an ABSENT: line, label's absent empty     [LOW]
  vote_missing          no vote and no ayes to derive one from              [MED]
  vote_tally_mismatch   stated tally disagrees with len(ayes)-len(noes)     [MED]
  district_missing      block states a use district, label's is empty (2014)[MED]
  request_type_blank    case suffix implies a request_type, label's empty   [LOW]
  enum_other            ceqa/demolition/request_type coerced to 'other'     [LOW]

Operates on the labeling DB (labeling_app/labels.db), where each human label is
already paired to its source block. Two modes:

  python label_qa.py                 # REPORT only: write audit CSV + print summary
  python label_qa.py --apply         # also FLAG suspect items in the app (status
                                      # → flagged, with a '[QA] …' note) so you can
                                      # filter to `flagged` and fix top-down
  python label_qa.py --apply --min-severity high   # only flag the worst

--apply never edits label *data* — it only flips status/notes, and re-running
replaces the prior '[QA] …' note (idempotent). A recurring case paired to a
continuance stub can trip action_mismatch legitimately; the note says so — judge
each block on its own text (see hand_label_review_guide.md).
"""
from __future__ import annotations
import argparse, csv, json, re, sqlite3, sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from extraction_common import coerce_record, ENUM_FIELDS, FIELDS, is_empty  # noqa: E402
from autoextract import extract, _after, _action_enum, derive_request_type, CASE_RE  # noqa: E402

# Fields safe to back-fill from the source block: high-confidence parses that the
# processing review flagged as "a parse, not a re-read". Free-text/judgement
# fields (project_descr, modifications, speaker_statements, preliminary_recommendation,
# parking, units) are intentionally excluded — those need a human.
BACKFILL_FIELDS = [
    "case_number", "request_type", "supervisorial_district", "assessor_block",
    "lot_number", "type_district", "type_district_descr",
    "height_and_bulk_district", "staff_planner", "ceqa_determination", "action",
    "ayes", "noes", "absent", "recused", "excused", "vote", "resolution_or_motion_no",
]

DB = HERE / "labeling_app" / "labels.db"

SEVERITY = {"high": 3, "med": 2, "low": 1}
# disposition families: an action_mismatch only fires across families, so harmless
# wording differences (approved vs approved_with_conditions) don't generate noise.
ACTION_FAMILY = {
    "approved": "approve", "approved_with_conditions": "approve",
    "approved_as_modified": "approve",
    "disapproved": "disapprove",
    "continued": "continue", "continued_indefinitely": "continue",
    "withdrawn": "withdraw",
    "did_not_take_dr": "no_dr", "took_dr": "dr", "took_dr_and_approved": "dr",
    "filed": "filed", "no_action": "none", "other": "",
}
NOSE_RE = re.compile(r"(?im)^\s*NOES\s*:", )
AYES_RE = re.compile(r"(?im)^\s*AYES\s*:")
ABSENT_RE = re.compile(r"(?im)^\s*ABSENT\s*:")


def _has_rollcall(block: str, regex) -> bool:
    """A roll-call line exists with a real (non-'None') value."""
    m = regex.search(block)
    if not m:
        return False
    tail = block[m.end():m.end() + 60].strip().lower()
    return bool(tail) and not tail.startswith("none")


def audit_record(label: dict, block: str) -> list[tuple[str, str, str]]:
    """Return a list of (check, severity, detail) for one label vs its block."""
    src = extract(block)                       # source-derived record
    issues: list[tuple[str, str, str]] = []

    # — action —
    act = (label.get("action") or "").strip().lower()
    if act == "other":
        issues.append(("action_other", "high",
                       f"action='other'; source ACTION→'{src['action'] or '?'}'"))
    elif act and src["action"] and src["action"] != "other":
        if ACTION_FAMILY.get(act, act) != ACTION_FAMILY.get(src["action"], src["action"]):
            issues.append(("action_mismatch", "high",
                           f"label action='{act}' vs source='{src['action']}'"))

    # — identity —
    if not (label.get("case_number") or "").strip() and CASE_RE.search(block):
        issues.append(("case_number_missing", "high",
                       f"block has case {CASE_RE.search(block).group(1)}"))
    if not (label.get("request_type") or "").strip():
        rt = derive_request_type(label.get("case_number") or src["case_number"])
        if rt:
            issues.append(("request_type_blank", "low", f"suffix implies '{rt}'"))

    # — roll call —
    ayes = label.get("ayes") or []
    noes = label.get("noes") or []
    if not ayes and _has_rollcall(block, AYES_RE):
        issues.append(("ayes_missing", "med", "block has an AYES: line"))
    if not noes and _has_rollcall(block, NOSE_RE):
        issues.append(("noes_missing", "med", "block has a NOES: line"))
    if not (label.get("absent") or []) and _has_rollcall(block, ABSENT_RE):
        issues.append(("absent_missing", "low", "block has an ABSENT: line"))

    # — vote —
    vote = (label.get("vote") or "").strip()
    if not vote and not ayes and _has_rollcall(block, AYES_RE):
        issues.append(("vote_missing", "med", "no vote/ayes but block has AYES:"))
    m = re.match(r"\s*(\d+)\s*-\s*(\d+)\s*$", vote)
    if m and ayes:
        if int(m.group(1)) != len(ayes) or int(m.group(2)) != len(noes):
            issues.append(("vote_tally_mismatch", "med",
                           f"tally {vote} vs ayes/noes {len(ayes)}/{len(noes)}"))

    # — districts (the 2014 gap) —
    if not (label.get("type_district") or "").strip() and src["type_district"]:
        issues.append(("district_missing", "med",
                       f"block states district '{src['type_district']}'"))

    # — enum coercion casualties —
    for f in ("ceqa_determination", "demolition"):
        if (label.get(f) or "").strip().lower() == "other":
            issues.append(("enum_other", "low", f"{f}='other'"))

    return issues


def _strip_qa_note(note: str) -> str:
    return re.sub(r"\s*\[QA[^\]]*\][^\[]*", "", note or "").strip()


def backfill_from_source(label: dict, block: str) -> tuple[dict, list[str]]:
    """Return (merged_label, changed_fields). ADDITIVE only: a field is filled from
    the source-derived record solely when the label's value is empty (or an enum
    coerced to 'other') AND the source has a confident value. A real human value is
    never overwritten."""
    src = extract(block)
    merged = dict(label)
    changed = []
    for f in BACKFILL_FIELDS:
        cur, new = label.get(f), src.get(f)
        if is_empty(new) or (f in ENUM_FIELDS and str(new).lower() == "other"):
            continue
        replace = is_empty(cur) or (f in ENUM_FIELDS and str(cur).strip().lower() == "other")
        if replace and cur != new:
            merged[f] = new
            changed.append(f)
    return (coerce_record(merged), changed) if changed else (label, [])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="flag suspect items in the app (default: report only)")
    ap.add_argument("--backfill", action="store_true",
                    help="with --apply: additively fill recoverable empty/'other' "
                         "fields from the source block (DB backed up; never "
                         "overwrites a real human value; changed items flagged)")
    ap.add_argument("--min-severity", choices=["low", "med", "high"], default="low",
                    help="only count/flag items with an issue at/above this severity")
    ap.add_argument("--status", default="prelabeled,done",
                    help="comma list of label statuses to audit (default: prelabeled,done)")
    ap.add_argument("--out", default=None, help="audit CSV path")
    ap.add_argument("--db", default=None, help="override labels.db path")
    args = ap.parse_args(argv)

    db_path = Path(args.db) if args.db else DB
    if not db_path.exists():
        sys.exit(f"no labels.db at {db_path} — run labeling_app/ingest.py first")
    statuses = [s.strip() for s in args.status.split(",") if s.strip()]
    min_sev = SEVERITY[args.min_severity]

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        f"SELECT i.id, i.year, i.case_number, i.source_file, i.block_text, "
        f"l.data, l.status, l.notes FROM items i JOIN labels l ON l.item_id=i.id "
        f"WHERE l.status IN ({','.join('?'*len(statuses))})", statuses).fetchall()

    out_path = Path(args.out) if args.out else (HERE / "label_audit.csv")
    audited = flagged = 0
    by_check = Counter()
    csv_rows = []
    to_flag = []
    to_backfill = []          # (item_id, merged_json, changed_fields, notes)
    fill_counts = Counter()
    for r in rows:
        audited += 1
        try:
            label = coerce_record(json.loads(r["data"]) if r["data"] else {})
        except Exception:
            continue
        if args.backfill:
            merged, changed = backfill_from_source(label, r["block_text"] or "")
            if changed:
                to_backfill.append((r["id"], json.dumps(merged, ensure_ascii=False),
                                    changed, r["notes"]))
                for f in changed:
                    fill_counts[f] += 1
        issues = audit_record(label, r["block_text"] or "")
        if not issues:
            continue
        top_sev = max(SEVERITY[s] for _, s, _ in issues)
        for chk, sev, _ in issues:
            by_check[chk] += 1
        if top_sev < min_sev:
            continue
        flagged += 1
        detail = "; ".join(f"{c}:{d}" for c, s, d in issues)
        csv_rows.append({"item_id": r["id"], "year": r["year"],
                         "case_number": r["case_number"], "status": r["status"],
                         "source_file": r["source_file"],
                         "checks": ",".join(sorted({c for c, _, _ in issues})),
                         "top_severity": [k for k, v in SEVERITY.items() if v == top_sev][0],
                         "detail": detail})
        to_flag.append((r["id"], r["notes"], detail))

    # write report
    with out_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, ["item_id", "year", "case_number", "status",
                                "top_severity", "checks", "source_file", "detail"])
        w.writeheader()
        for row in sorted(csv_rows, key=lambda x: (-SEVERITY[x["top_severity"]], x["year"])):
            w.writerow(row)

    print(f"audited {audited} labels (status in {statuses})")
    print(f"flagged {flagged} with an issue ≥ {args.min_severity}")
    print("issue counts by check (all severities):")
    for chk, n in by_check.most_common():
        print(f"  {chk:22s} {n}")
    print(f"audit CSV → {out_path}")
    if args.backfill:
        print(f"\nback-fill candidates: {len(to_backfill)} items, by field:")
        for f, n in fill_counts.most_common():
            print(f"  {f:24s} {n}")

    if not args.apply:
        print("\n(report only — re-run with --apply to flag these in the labeling app,"
              " add --backfill to also pre-fill recoverable fields)")
        con.close()
        return

    # --apply: back up the DB before any mutation
    import shutil
    bak = db_path.with_suffix(".db.qa.bak")
    shutil.copy2(db_path, bak)
    print(f"\nbacked up labels.db → {bak}")

    if args.backfill:
        for item_id, merged_json, changed, notes in to_backfill:
            note = _strip_qa_note(notes)
            tag = f"[QA-backfilled: {','.join(changed)}]"[:300]
            note = (note + " " + tag).strip() if note else tag
            con.execute("UPDATE labels SET data=?, status='flagged', flagged=1, "
                        "notes=? WHERE item_id=?", (merged_json, note, item_id))
        print(f"✓ back-filled {len(to_backfill)} items from source (additive) and "
              f"flagged them for confirmation.")

    backfilled_ids = {x[0] for x in to_backfill}
    flag_only = [t for t in to_flag if t[0] not in backfilled_ids]
    for item_id, notes, detail in flag_only:
        note = _strip_qa_note(notes)
        qa = f"[QA] {detail}"[:480]
        note = (note + " " + qa).strip() if note else qa
        con.execute("UPDATE labels SET status='flagged', flagged=1, notes=? "
                    "WHERE item_id=?", (note, item_id))
    con.commit()
    con.close()
    print(f"✓ flagged {len(flag_only)} more suspect items with a [QA] note.")
    print("  Open the app, filter to 'flagged', and review top-down "
          "(back-filled values are pre-filled — confirm or correct, then Save).")


if __name__ == "__main__":
    main()
