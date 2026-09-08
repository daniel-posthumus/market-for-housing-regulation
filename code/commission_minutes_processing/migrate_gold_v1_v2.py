#!/usr/bin/env python3
"""
migrate_gold_v1_v2.py
---------------------
Purpose : Carry the 232 hand-labelled gold records from schema v1 to schema v2. It PROPOSES;
          it does not decide. Anything it cannot derive unambiguously goes into the review
          queue with a proposal attached, for a human to accept or edit.
Inputs  : labels.db (the v1 gold), the schema and helpers from extraction_common/normalize.
Outputs : bakeoff/gold/gold_v1_snapshot.json   — v1, frozen, never written again
          bakeoff/gold/gold_v2_proposed.json   — the proposal, per field, with AUTO/FLAG
          bakeoff/gold/migration_report.md     — counts by field of AUTO vs FLAG
          rows in `review_queue` for every FLAG
          (with --apply) the AUTO values written into labels.db
Author  : Dan Post
Created : 2026-09-07

Notes
-----
The v1 snapshot is written FIRST and refuses to overwrite itself. Every pre-migration number
in the memo was measured against that content; if it is lost, those numbers stop being
reproducible. It lives under bakeoff/ with the rest of the local gold artefacts, which the
repo git-ignores by policy — the same policy that keeps labels.db out of git.

Nothing here silently changes a label. `--apply` writes only the fields marked AUTO, and
only after the snapshot exists. FLAG fields keep their v1 value in the database and get a
queue row carrying the proposal, so the app shows old and proposed side by side.

Usage:
  python migrate_gold_v1_v2.py                # dry run: snapshot, propose, report
  python migrate_gold_v1_v2.py --queue        # ... and write the FLAGs to review_queue
  python migrate_gold_v1_v2.py --queue --apply  # ... and write the AUTOs into labels.db
  python migrate_gold_v1_v2.py --refresh-proposals   # the rule changed: re-propose, and
                                                     # re-open ONLY the rows it changes
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import datetime
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from extraction_common import (SCHEMA_VERSION, coerce_record, is_empty)   # noqa: E402
from normalize import (address_core, address_format_ok, descr_proposal,   # noqa: E402
                       instrument_no)
import review_queue                                                       # noqa: E402
import bakeoff_extract as BX                                              # noqa: E402

DB = HERE / "labeling_app" / "labels.db"
GOLD = HERE / "bakeoff" / "gold"
V1 = GOLD / "gold_v1_snapshot.json"
V2 = GOLD / "gold_v2_proposed.json"
REPORT = GOLD / "migration_report.md"

# Confirmed gold errors (spec §7.3): the label says "motion no. NNNNN" where the block reads
# "RESOLUTION No.:". Hard-flagged regardless of how cleanly the string parses, because it
# parses perfectly and is still wrong — which is exactly why a parse check cannot catch it.
HARD_FLAG_INSTRUMENT = {447, 448, 451}

# "DRA" (Discretionary Review Action) is the third instrument — see the note on the
# `action_instrument` enum. `D.R.A.` appears too, hence the optional dots.
_INSTRUMENT_WORD = re.compile(r"\b(motion|resolution|d\.?r\.?a\.?)\b", re.I)
# The instrument line as the minutes print it. Anchored on the label, not on the bare word,
# so prose like "on the motion of Commissioner X" is not mistaken for the instrument.
_BLOCK_INSTRUMENT = re.compile(
    r"\b(MOTION|RESOLUTION|D\.?R\.?A\.?)\s*(?:NO\.?|No\.?|#)?\s*:?\s*(\d{3,6})", re.I)


def _instrument_word(w: str) -> str:
    w = w.lower().replace(".", "")
    return w if w in ("motion", "resolution") else "dra"
_ADDRESS_SHAPE = re.compile(r"^\d+[\w\-]*(?:\s*[-–]\s*\d+[\w\-]*)?\s+\S")


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()


# ── the three field rules ────────────────────────────────────────────────────
def migrate_instrument(iid: int, v1: dict, block: str) -> dict:
    """resolution_or_motion_no -> action_instrument + action_instrument_no."""
    raw = str(v1.get("resolution_or_motion_no") or "").strip()
    words = [_instrument_word(w) for w in _INSTRUMENT_WORD.findall(raw)]
    # `\d+[A-Z]` catches the OCR'd number ("020B" for 0208) — a value that silently becomes
    # 20 if it is allowed through, so it is treated as a non-number and flagged.
    nums = re.findall(r"\b\d{2,6}\b", raw)
    dirty = re.findall(r"\b\d{2,6}[A-Za-z]\b", raw)
    prop = {"action_instrument": words[0] if words else "",
            "action_instrument_no": instrument_no(nums[0]) if nums else 0}

    # What the block itself prints for this number. The same class of error as 447/448/451
    # will be elsewhere too, and a check that only reads the label cannot see it.
    seen = {(_instrument_word(w), instrument_no(n))
            for w, n in _BLOCK_INSTRUMENT.findall(block or "")}
    from_block = [w for w, n in seen if n == prop["action_instrument_no"]]

    if not raw:
        return dict(status="AUTO", rule="blank", proposal=prop,
                    detail="v1 value was blank")
    if iid in HARD_FLAG_INSTRUMENT:
        return dict(status="FLAG", rule="confirmed_gold_error",
                    proposal={**prop, "action_instrument":
                              from_block[0] if len(from_block) == 1 else ""},
                    detail="confirmed gold error: the label says a motion, the block prints "
                           "a RESOLUTION. Read the block and pick the instrument it names.")
    if len(nums) != 1 or dirty:
        return dict(status="FLAG", rule="unparseable", proposal=prop,
                    detail=("the number is not clean digits (%s) — likely an OCR slip; read "
                            "it off the block" % ", ".join(dirty) if dirty else
                            "no number in the v1 value" if not nums else
                            f"{len(nums)} numbers in the v1 value — one item, one instrument"))
    if len(set(words)) != 1:
        # The number is unambiguous; only the word is missing or doubled. If the block names
        # exactly one instrument for that number, that is the proposal — an accept-keypress,
        # not a re-read. Still FLAG: this is inference, and inference is what a human signs.
        return dict(status="FLAG", rule="word_from_block" if len(from_block) == 1
                    else "unparseable",
                    proposal={**prop, "action_instrument":
                              from_block[0] if len(from_block) == 1 else ""},
                    detail=(f"the v1 value has no instrument word; the block prints "
                            f"{from_block[0].upper()} No. {prop['action_instrument_no']} — "
                            f"confirm" if len(from_block) == 1 else
                            "no instrument word in the v1 value and the block does not "
                            "name one for that number"))
    if from_block and prop["action_instrument"] not in from_block:
        return dict(status="FLAG", rule="block_disagrees",
                    proposal={**prop, "action_instrument": from_block[0]},
                    detail=f"the block prints {from_block[0].upper()} No. "
                           f"{prop['action_instrument_no']}, the v1 label says "
                           f"{prop['action_instrument']}")
    return dict(status="AUTO", rule="parsed", proposal=prop,
                detail="instrument word and number both parse")


def migrate_speakers(v1: dict) -> dict:
    """speakers + *_count -> speakers[{name, stance}]."""
    sp = v1.get("speakers") or []
    names = [str(s.get("name") if isinstance(s, dict) else s).strip()
             for s in sp if str(s.get("name") if isinstance(s, dict) else s).strip()]
    counts = {k: int(v1.get(k) or 0) for k in
              ("support_count", "oppose_count", "neutral_count")}
    nz = [k for k, v in counts.items() if v]

    if not names and sum(counts.values()) == 0:
        return dict(status="AUTO", rule="empty", proposal={"speakers": []},
                    detail="no speakers and no counts")
    if len(nz) == 1 and counts[nz[0]] == len(names):
        stance = nz[0].replace("_count", "")
        return dict(status="AUTO", rule="single_stance",
                    proposal={"speakers": [{"name": n, "stance": stance} for n in names]},
                    detail=f"one stance ({stance}) and its count equals the speaker list")
    prop = {"speakers": [{"name": n, "stance": ""} for n in names]}
    if sum(counts.values()) == 0:
        # v1 recorded names and left every count at zero: the label never carried a stance
        # at all. That is a v1 GAP, not a migration ambiguity — the honest v2 value is
        # "unspecified", and recovering the real stances means re-reading the block. Queued
        # so the gap is visible and workable, but it is optional work and says so.
        return dict(status="FLAG", rule="no_stances_in_v1", proposal=prop,
                    detail=f"v1 recorded {len(names)} speaker(s) and no stance counts, so "
                           f"the stances were never labelled. Blank (unspecified) is a "
                           f"valid answer — fill them in only if the block makes it easy.")
    if sum(counts.values()) != len(names):
        return dict(status="FLAG", rule="counts_mismatch", proposal=prop,
                    detail=f"the counts ({counts['support_count']}/"
                           f"{counts['oppose_count']}/{counts['neutral_count']} "
                           f"support/oppose/neutral) do not sum to the "
                           f"{len(names)} name(s) recorded — one of the two is wrong")
    return dict(status="FLAG", rule="stance_unassignable", proposal=prop,
                detail=f"{len(nz)} stances across {len(names)} speaker(s); which name took "
                       f"which stance is not recoverable from the counts")


_DESCR_DETAIL = {
    "request_for": "proposal is the description verbatim, from \"Request for...\" to the "
                   "start of the closing block — accept unless the block disagrees",
    "opener": "no \"Request for...\"; proposal runs from the opening phrase (Consideration "
              "of / Appeal of / Public hearing on) to the closing block — read before "
              "accepting",
    "after_header": "no recognised opening phrase; proposal is everything after the "
                    "Assessor's-Block citation, which is where the description usually "
                    "starts — read it, the front may need trimming",
    "none": "no recognised opening phrase in the block; write the descriptive text yourself",
}


def migrate_descr(v1: dict, block: str) -> dict:
    """project_descr — FLAG all 232. The target changed; no automatic migration is
    defensible. The proposal comes from the same rule the app's one-click button uses."""
    text, rule = descr_proposal(block)
    return dict(status="FLAG", proposal={"project_descr": text}, rule=rule,
                detail=_DESCR_DETAIL[rule])


def migrate_address(v1: dict) -> dict:
    """project_address — strip the locational gloss (§5). Validation-only under §2.4, so
    this is a tidy-up, not a blocker."""
    raw = str(v1.get("project_address") or "").strip()
    if not raw or address_format_ok(raw):
        return dict(status="AUTO", rule="already_clean", proposal={"project_address": raw},
                    detail="already free of the locational gloss")
    core = address_core(raw)
    if core and _ADDRESS_SHAPE.match(core):
        return dict(status="FLAG", rule="gloss_stripped",
                    proposal={"project_address": core},
                    detail="the gloss stripped cleanly; confirm the street address is intact")
    return dict(status="FLAG", rule="gloss_unclear", proposal={"project_address": core or raw},
                detail="the gloss did not strip cleanly — the remainder does not look like "
                       "a street address")


# ── driver ───────────────────────────────────────────────────────────────────
RULES = ("instrument", "speakers", "project_descr", "project_address")
FIELDS_OF = {"instrument": ["action_instrument", "action_instrument_no"],
             "speakers": ["speakers"],
             "project_descr": ["project_descr"],
             "project_address": ["project_address"]}


def load_v1() -> list[dict]:
    """The v1 gold, UNCOERCED — coercing here would apply the v2 shape before the migration
    has decided what the v2 value should be.

    Read from the FROZEN SNAPSHOT once one exists, not from labels.db. After `--apply` the
    database no longer holds `resolution_or_motion_no` at all, so a second run against it
    would see a blank where the v1 label had a value and quietly conclude there was nothing
    to migrate. The snapshot is what makes this script re-runnable.
    """
    snap = json.loads(V1.read_text()) if V1.exists() else None
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute("""SELECT i.id, i.year, i.case_number, i.block_text, l.data,
                                 COALESCE(l.notes,'')
                          FROM items i JOIN labels l ON l.item_id = i.id
                          WHERE l.status IN ('done','flagged','review')
                            AND l.data IS NOT NULL""").fetchall()
    con.close()
    out = []
    for iid, year, cn, block, data, notes in rows:
        rec = snap.get(str(iid)) if snap is not None else json.loads(data)
        if rec is None:
            continue                  # not in the frozen v1 gold; not this script's business
        if not (rec.get("action") or rec.get("case_number")):
            continue
        if "representativeness" in notes or "NOT yet labelled" in notes:
            continue
        out.append(dict(id=iid, year=year, case_number=cn, block=block or "", v1=rec))
    out.sort(key=lambda r: r["id"])
    return out


def migrate(records: list[dict]) -> list[dict]:
    out = []
    for r in records:
        v1, blk = r["v1"], r["block"]
        moves = {"instrument": migrate_instrument(r["id"], v1, blk),
                 "speakers": migrate_speakers(v1),
                 "project_descr": migrate_descr(v1, blk),
                 "project_address": migrate_address(v1)}
        # Everything else carries over untouched.
        touched = {f for k in RULES for f in FIELDS_OF[k]} | {"resolution_or_motion_no"}
        carried = {k: v for k, v in v1.items() if k not in touched}
        v2 = dict(carried)
        for m in moves.values():
            v2.update(m["proposal"])
        out.append(dict(id=r["id"], year=r["year"], case_number=r["case_number"],
                        moves=moves, v2_proposed=v2,
                        auto={k: m["proposal"] for k, m in moves.items()
                              if m["status"] == "AUTO"}))
    return out


def queue_rows(records: list[dict], migrated: list[dict]) -> list[dict]:
    v1_by = {r["id"]: r["v1"] for r in records}
    rows = []
    for m in migrated:
        v1 = v1_by[m["id"]]
        for rule, res in m["moves"].items():
            if res["status"] != "FLAG":
                continue
            reason = "field_redefined" if rule in ("project_descr", "project_address") \
                else "migration_ambiguous"
            for fld in FIELDS_OF[rule]:
                old = (v1.get("resolution_or_motion_no") if rule == "instrument"
                       else v1.get(fld))
                if rule == "speakers":
                    old = {"speakers": v1.get("speakers") or [],
                           "support_count": v1.get("support_count") or 0,
                           "oppose_count": v1.get("oppose_count") or 0,
                           "neutral_count": v1.get("neutral_count") or 0}
                if rule == "instrument" and fld == "action_instrument_no":
                    continue          # one queue row per instrument, not two
                # `proposed` carries the VALUE for `field`, except on the instrument row,
                # where one decision sets two fields and the whole pair is proposed together.
                prop = (res["proposal"] if rule == "instrument"
                        else res["proposal"].get(fld))
                rows.append(dict(item_id=m["id"], field=fld, reason=reason,
                                 detail=res["detail"], old_value=old,
                                 proposed=prop, rule=res.get("rule", "")))
    return rows


def apply_auto(migrated: list[dict], db: Path = DB) -> int:
    """Write ONLY the AUTO fields into labels.db. FLAG fields keep their v1 value until a
    human resolves the queue row."""
    con = sqlite3.connect(db)
    n = 0
    for m in migrated:
        row = con.execute("SELECT data FROM labels WHERE item_id=?", (m["id"],)).fetchone()
        if not row or not row[0]:
            continue
        rec = json.loads(row[0])
        before = json.dumps(rec, sort_keys=True)
        for prop in m["auto"].values():
            rec.update(prop)
        rec.pop("resolution_or_motion_no", None)   # replaced by the instrument pair
        rec.setdefault("action_instrument", "")
        rec.setdefault("action_instrument_no", 0)
        rec = coerce_record(rec)
        if json.dumps(rec, sort_keys=True) != before:
            con.execute("UPDATE labels SET data=? WHERE item_id=?",
                        (json.dumps(rec, ensure_ascii=False), m["id"]))
            n += 1
    con.commit()
    con.close()
    return n


def refresh_proposals(records: list[dict], migrated: list[dict]) -> dict:
    """Re-propose `project_descr` after a change to the rule, without re-doing settled work.

    A rule change mid-session is the thing the spec warns about, and the reason is that it
    makes gold internally inconsistent — half the field labelled against one target, half
    against another. The cheap fix is not to re-ask every question: a row whose settled value
    ALREADY satisfies the new rule needs nothing, because the labeller and the new rule agree.
    Only the rows where they disagree come back, and they come back showing what the labeller
    chose rather than the v1 value they already replaced.
    """
    import review_queue
    by_block = {r["id"]: r["block"] for r in records}
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("""SELECT q.id, q.item_id, q.status, l.data FROM review_queue q
                          JOIN labels l ON l.item_id = q.item_id
                          WHERE q.reason='field_redefined' AND q.field='project_descr'"""
                       ).fetchall()
    norm = lambda v: re.sub(r"[^a-z0-9]", "", str(v or "").lower())     # noqa: E731
    now = _now()
    tal = Counter()
    for r in rows:
        text, rule = descr_proposal(by_block.get(r["item_id"], ""))
        cur = json.loads(r["data"] or "{}").get("project_descr", "")
        detail = _DESCR_DETAIL[rule]
        if r["status"] == "open":
            con.execute("UPDATE review_queue SET proposed=?, rule=?, detail=?, updated_at=? "
                        "WHERE id=?",
                        (json.dumps(text, ensure_ascii=False), rule, detail, now, r["id"]))
            tal["still open, proposal refreshed"] += 1
            continue
        # Settled: does what they chose already satisfy the new rule? An EMPTY proposal
        # counts as satisfied — when the rule can propose nothing (no opening phrase in the
        # block), a hand-written value is the best answer that exists and re-opening the row
        # would only offer to replace it with a blank.
        if not text or norm(cur) == norm(text) or norm(text) in norm(cur):
            con.execute("UPDATE review_queue SET status='done', resolved=?, proposed=?, "
                        "rule=?, detail=?, updated_at=? WHERE id=?",
                        (json.dumps(cur, ensure_ascii=False),
                         json.dumps(text, ensure_ascii=False), rule, detail, now, r["id"]))
            tal["settled and still valid — left alone"] += 1
        else:
            con.execute("""UPDATE review_queue SET status='open', verdict=NULL, resolved=NULL,
                           old_value=?, proposed=?, rule=?, detail=?, updated_at=?
                           WHERE id=?""",
                        (json.dumps(cur, ensure_ascii=False),
                         json.dumps(text, ensure_ascii=False), rule,
                         "the rule changed and your value no longer matches it — " + detail,
                         now, r["id"]))
            tal["re-opened: the rule change moves this one"] += 1
    con.commit()
    con.close()
    return tal


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def report(records: list[dict], migrated: list[dict]) -> str:
    tal = defaultdict(Counter)
    for m in migrated:
        for rule, res in m["moves"].items():
            tal[rule][res["status"]] += 1
    lines = ["# Gold migration v1 → v2", "",
             f"Schema version {SCHEMA_VERSION}. {len(records)} hand-labelled records.", "",
             "| rule | fields | AUTO | FLAG |", "|---|---|---:|---:|"]
    for rule in RULES:
        lines.append("| `%s` | %s | %d | %d |"
                     % (rule, ", ".join("`%s`" % f for f in FIELDS_OF[rule]),
                        tal[rule]["AUTO"], tal[rule]["FLAG"]))
    lines += ["", "## Why the FLAGs", ""]
    for rule in RULES:
        flagged = [m["moves"][rule] for m in migrated if m["moves"][rule]["status"] == "FLAG"]
        if not flagged:
            continue
        why = Counter(f["rule"] for f in flagged)
        sample = {f["rule"]: f["detail"] for f in flagged}
        lines.append(f"### `{rule}` — {len(flagged)} flagged")
        lines.append("")
        for tag, n in why.most_common():
            lines.append(f"- **{n} × `{tag}`** — {sample[tag]}")
        lines.append("")
    rules_used = Counter(m["moves"]["project_descr"].get("rule") for m in migrated)
    lines += ["## `project_descr` proposals by rule", "",
              "| rule | n | what the labeller does |", "|---|---:|---|",
              "| `request_for` | %d | accept unless the block disagrees |"
              % rules_used["request_for"],
              "| `opener` | %d | read the proposed sentence, then accept or edit |"
              % rules_used["opener"],
              "| `none` | %d | write it — no recognised opener in the block |"
              % rules_used["none"], ""]
    lines += ["## Schema changes this migration forced", "",
              "- **`action_instrument` gained a third value, `dra`.** Eleven "
              "discretionary-review items record a `DRA#: 0013` (Discretionary Review "
              "Action) in the block, which is neither a motion nor a resolution. The "
              "two-value enum would have blanked a real, printed value on all eleven; "
              "the enum was widened rather than the data discarded.",
              "- **`project_address` is validation-only.** The parcel join keys on "
              "(`assessor_block`, `lot_number`), which score 99-100%, and `link_permits.py` "
              "has always joined that way. The address checks that linkage and is displayed; "
              "it is not a load-bearing merge key, and the geocoder is off the critical "
              "path. `format_ok` is a soft warning, not a gate.", ""]
    hard = [m["id"] for m in migrated if m["id"] in HARD_FLAG_INSTRUMENT]
    lines += ["## Hard-flagged", "",
              "Items %s: the v1 label names a motion where the block prints a RESOLUTION. "
              "Confirmed gold errors, flagged regardless of how cleanly the string parses."
              % ", ".join(str(i) for i in sorted(hard)), ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", action="store_true", help="write the FLAGs to review_queue")
    ap.add_argument("--apply", action="store_true",
                    help="write the AUTO values into labels.db (requires the v1 snapshot)")
    ap.add_argument("--refresh-proposals", action="store_true",
                    help="the project_descr rule changed: re-propose, re-open only what moves")
    a = ap.parse_args()

    records = load_v1()
    if a.refresh_proposals:
        tal = refresh_proposals(records, migrate(records))
        print("project_descr proposals refreshed against the current rule:")
        for k, n in tal.most_common():
            print(f"  {n:4d}  {k}")
        return

    GOLD.mkdir(parents=True, exist_ok=True)
    snap = {str(r["id"]): r["v1"] for r in records}
    if V1.exists():
        old = json.loads(V1.read_text())
        same = _sha(old) == _sha(snap)
        print(f"v1 snapshot exists ({len(old)} records) — {'unchanged' if same else 'DIFFERS'}"
              f" from the database now.")
        if not same:
            print("  Not overwriting. The snapshot is what the pre-migration numbers were "
                  "measured against; if the database has moved on, that is the fact to "
                  "keep, not to erase.")
    else:
        V1.write_text(json.dumps(snap, indent=1, ensure_ascii=False))
        print(f"→ {V1}  ({len(snap)} records, sha {_sha(snap)[:12]})")

    migrated = migrate(records)
    V2.write_text(json.dumps(migrated, indent=1, ensure_ascii=False))
    rep = report(records, migrated)
    REPORT.write_text(rep + "\n")
    print(f"→ {V2}\n→ {REPORT}\n")
    print(rep)

    rows = queue_rows(records, migrated)
    if a.queue:
        n = review_queue.enqueue(rows, replace_reason=None)
        print(f"\nreview_queue: {n} rows added ({len(rows)} proposed; the rest were "
              f"already queued).")
    else:
        print(f"\n{len(rows)} queue rows would be written. Re-run with --queue.")

    if a.apply:
        if not V1.exists():
            print("refusing to --apply without a v1 snapshot.")
            return
        n = apply_auto(migrated)
        print(f"labels.db: {n} records updated with their AUTO values.")


if __name__ == "__main__":
    main()
