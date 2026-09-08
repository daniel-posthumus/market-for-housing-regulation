#!/usr/bin/env python3
"""
provenance.py
-------------
Purpose : Record WHICH configuration produced any given extracted value. The schema, the
          prompt and the model will all move several times before the corpus run is final,
          and a value with no provenance is a value that cannot be re-derived, compared, or
          thrown away with confidence.
Inputs  : labels.db (the local store; git-ignored, same file the labelling app uses).
Outputs : three tables in that file — `extraction_runs`, `predictions`,
          `verification_failures` — created on demand.
Author  : Dan Post
Created : 2026-09-07

Notes
-----
APPEND, NEVER OVERWRITE (spec §6). A run id is minted per submission and every prediction
carries it, so re-running the corpus adds rows rather than replacing them. `predictions` is
keyed on (run_id, item_id): a second write of the same pair is ignored, not updated, which
makes a resumed collection idempotent without letting a later run silently rewrite history.

`gold_version` is a REGISTERED name for a gold snapshot, not a hash printed inline. Working
the adjudication queue changes gold, which changes every frozen test number; a number is
only interpretable if it names the snapshot it was measured against. The registry lives in
bakeoff/gold_versions.json — a plain file, so it survives a database rebuild and is
diffable in git alongside the memo that quotes its numbers.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from extraction_common import SCHEMA_VERSION                          # noqa: E402

DB = HERE / "labeling_app" / "labels.db"
GOLD_VERSIONS = HERE / "bakeoff" / "gold_versions.json"

DDL = """
CREATE TABLE IF NOT EXISTS extraction_runs(
    run_id         TEXT PRIMARY KEY,
    created_at     TEXT,
    model          TEXT,       -- e.g. claude-haiku-4-5
    method_tag     TEXT,       -- e.g. fs6v2, matching the bakeoff naming
    prompt_sha     TEXT,       -- SHA-256 of the assembled cacheable prefix
    schema_version INTEGER,
    gold_version   TEXT,       -- for scored runs: which gold snapshot
    variant        TEXT,
    shots          INTEGER,
    n_items        INTEGER,
    batch_id       TEXT,
    note           TEXT
);
CREATE TABLE IF NOT EXISTS predictions(
    run_id     TEXT NOT NULL REFERENCES extraction_runs(run_id),
    item_id    INTEGER NOT NULL,
    data       TEXT,           -- the normalised record, JSON
    raw        TEXT,           -- the model's reply before unwrapping/normalisation
    created_at TEXT,
    PRIMARY KEY (run_id, item_id)
);
CREATE TABLE IF NOT EXISTS verification_failures(
    run_id   TEXT NOT NULL,
    item_id  INTEGER NOT NULL,
    field    TEXT NOT NULL,
    reason   TEXT,             -- missing_evidence | evidence_not_in_block | ...
    value    TEXT,
    evidence TEXT,
    PRIMARY KEY (run_id, item_id, field)
);
CREATE INDEX IF NOT EXISTS ix_pred_item ON predictions(item_id);
CREATE INDEX IF NOT EXISTS ix_vf_field  ON verification_failures(field, reason);
"""


def connect(db: Path = DB) -> sqlite3.Connection:
    con = sqlite3.connect(db)
    con.executescript(DDL)
    return con


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# ── gold versions ────────────────────────────────────────────────────────────
def _registry() -> list[dict]:
    return json.loads(GOLD_VERSIONS.read_text()) if GOLD_VERSIONS.exists() else []


def gold_version(sha: str, register: bool = False, note: str = "", n_items: int = 0) -> str:
    """Name for the gold snapshot with this content hash.

    An unregistered snapshot is reported as such rather than silently minted: a number
    measured against gold nobody has declared a version of is a number nobody can cite.
    """
    reg = _registry()
    for r in reg:
        if r["gold_sha256"] == sha:
            return r["version"]
    if not register:
        return "unregistered:" + sha[:12]
    v = "g%d" % (len(reg) + 1)
    reg.append({"version": v, "gold_sha256": sha, "created": now(),
                "n_items": n_items, "note": note})
    GOLD_VERSIONS.parent.mkdir(exist_ok=True)
    GOLD_VERSIONS.write_text(json.dumps(reg, indent=1))
    return v


# ── runs ─────────────────────────────────────────────────────────────────────
def start_run(model: str, method_tag: str, prompt_sha: str, *, gold_version: str = "",
              variant: str = "", shots: int = 0, n_items: int = 0, batch_id: str = "",
              note: str = "", db: Path = DB) -> str:
    run_id = "%s-%s" % (datetime.datetime.now().strftime("%Y%m%dT%H%M%S"),
                        uuid.uuid4().hex[:6])
    con = connect(db)
    con.execute("INSERT INTO extraction_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, now(), model, method_tag, prompt_sha, SCHEMA_VERSION,
                 gold_version, variant, shots, n_items, batch_id, note))
    con.commit()
    con.close()
    return run_id


def save_predictions(run_id: str, records: dict, raws: dict | None = None,
                     db: Path = DB) -> int:
    """Append predictions for a run. A pair already stored is left alone, never rewritten."""
    con = connect(db)
    rows = [(run_id, int(i), json.dumps(r, ensure_ascii=False),
             json.dumps((raws or {}).get(i), ensure_ascii=False) if raws else None, now())
            for i, r in records.items()]
    cur = con.executemany("INSERT OR IGNORE INTO predictions VALUES (?,?,?,?,?)", rows)
    con.commit()
    n = cur.rowcount
    con.close()
    return n


def log_failures(run_id: str, failures: list[tuple], db: Path = DB) -> int:
    """failures: (item_id, field, reason, value, evidence)."""
    con = connect(db)
    con.executemany("INSERT OR REPLACE INTO verification_failures VALUES (?,?,?,?,?,?)",
                    [(run_id,) + tuple(f) for f in failures])
    con.commit()
    con.close()
    return len(failures)


def run_summary(db: Path = DB) -> list[dict]:
    con = connect(db)
    rows = con.execute("""SELECT r.run_id, r.created_at, r.model, r.method_tag,
                                 substr(r.prompt_sha,1,12), r.schema_version, r.gold_version,
                                 (SELECT COUNT(*) FROM predictions p WHERE p.run_id=r.run_id),
                                 (SELECT COUNT(*) FROM verification_failures v
                                    WHERE v.run_id=r.run_id)
                          FROM extraction_runs r ORDER BY r.created_at""").fetchall()
    con.close()
    keys = ["run_id", "created_at", "model", "method_tag", "prompt_sha", "schema_version",
            "gold_version", "n_pred", "n_fail"]
    return [dict(zip(keys, r)) for r in rows]


if __name__ == "__main__":
    hdr = ("run_id", "created", "model", "tag", "prompt", "sv", "gold", "pred", "fail")
    print("%-24s %-20s %-18s %-8s %-13s %2s %-10s %5s %5s" % hdr)
    for r in run_summary():
        print("%-24s %-20s %-18s %-8s %-13s %2s %-10s %5d %5d"
              % (r["run_id"], r["created_at"][:19], r["model"], r["method_tag"],
                 r["prompt_sha"], r["schema_version"], r["gold_version"],
                 r["n_pred"], r["n_fail"]))
