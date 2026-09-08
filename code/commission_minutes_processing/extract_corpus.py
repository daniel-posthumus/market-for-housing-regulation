#!/usr/bin/env python3
"""
extract_corpus.py
-----------------
Purpose : Run the chosen extraction configuration over the whole corpus and land the result
          somewhere durable, in three separately-kept forms, so that no later change to the
          normaliser or the schema can ever force a re-query of the API.
Inputs  : labels.db (items + blocks), the schema/prompt from extraction_common, the frozen
          split (few-shot examples are drawn from the TRAIN half only), an API key.
Outputs : under $MFHR_DATA_ROOT/extraction/<run>/
            raw/chunk-NNN.jsonl        the model's replies, untouched
            interim/chunk-NNN.jsonl    unwrapped from the evidence envelope + coerced
            clean/chunk-NNN.jsonl      normalised — the analysis input
            evidence/chunk-NNN.jsonl   the model's supporting spans
            verification_failures.csv  spans that are not verbatim in the block
            manifest.json              batch ids, token counts, cost, prompt sha, versions
Author  : Dan Post
Created : 2026-09-07

Notes
-----
THREE FORMS, KEPT SEPARATELY, is the whole point of this script. `raw` is what the API
returned and is never rewritten; `interim` is raw minus the transport envelope; `clean` is
`interim` through `normalize.py`. Every bug found during the gold work was a normalisation
bug — a scalar/list mismatch that silently emptied `lot_number` on 122 records — and it was
recoverable only because the untouched replies had been kept. Re-deriving `clean` from `raw`
is a local loop; re-querying is $70.

CHUNKED because a single batch of 16k requests at ~26 kB each would exceed the request-size
limit, and because a chunk is the unit of resume: a chunk with a results file is skipped.

Usage:
  python extract_corpus.py --submit          # send every chunk not already sent
  python extract_corpus.py --collect         # poll, download, write the three forms
  python extract_corpus.py --status
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from paths import DATA_ROOT                                            # noqa: E402
from extraction_common import (SCHEMA_VERSION, build_prompt, item_suffix,   # noqa: E402
                               prompt_sha, coerce_record, unwrap_evidence, era_of)
from normalize import normalize_record                                 # noqa: E402
import bakeoff_extract as BX                                           # noqa: E402
import provenance                                                      # noqa: E402

DB = HERE / "labeling_app" / "labels.db"
ROOT = DATA_ROOT / "extraction"
CHUNK = 1500
MODEL = "claude-haiku-4-5"
SHOTS = 6
# Haiku 4.5 list price $/MTok; the Batch API halves all four.
PRICE = {"input": 1.00, "output": 5.00, "cache_read": 0.10, "cache_write": 1.25}


def run_dir(run: str) -> Path:
    return ROOT / run


def items_to_extract() -> list[dict]:
    """Every block that is an ITEM: it carries a case number and is not agenda scaffolding."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute("""SELECT i.id, i.year, i.case_number, i.source_file, i.meeting_date,
                                 i.item_index, i.block_text
                          FROM items i JOIN labels l ON l.item_id = i.id
                          WHERE i.case_number != '' AND l.status != 'not_an_item'
                          ORDER BY i.id""").fetchall()
    con.close()
    return [dict(id=r[0], year=r[1], case_number=r[2], source_file=r[3], meeting_date=r[4],
                 item_index=r[5], block=r[6] or "") for r in rows]


def example_pool() -> list[dict]:
    """The train half only. Test items must never appear as examples: the corpus run is what
    the frozen test number is supposed to describe."""
    split = json.loads((BX.OUT / "split.json").read_text())
    train = set(split["train"])
    pool = [e for e in BX.gold() if e["id"] in train]
    for e in pool:
        e["_w"] = BX._words(e["block"])
    return pool


def submit(run: str, limit_chunks: int | None = None):
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    d = run_dir(run)
    for sub in ("raw", "interim", "clean", "evidence"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    man_f = d / "manifest.json"
    man = json.loads(man_f.read_text()) if man_f.exists() else {}
    items = items_to_extract()
    pool = example_pool()
    base = build_prompt()
    psha = prompt_sha(base)
    cl = BX.client()
    schema = BX.json_schema()
    chunks = [items[i:i + CHUNK] for i in range(0, len(items), CHUNK)]
    man.setdefault("run", run)
    man.setdefault("created", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    man.setdefault("model", MODEL)
    man.setdefault("shots", SHOTS)
    man.setdefault("prompt_sha", psha)
    man.setdefault("schema_version", SCHEMA_VERSION)
    man.setdefault("n_items", len(items))
    man.setdefault("chunks", {})
    sent = 0
    for ci, chunk in enumerate(chunks):
        key = f"chunk-{ci:03d}"
        if key in man["chunks"]:
            continue
        if limit_chunks is not None and sent >= limit_chunks:
            break
        reqs = []
        for it in chunk:
            ex = BX.few_shot_block(it["block"], pool, SHOTS, era_of(it["year"]))
            tail = ex + item_suffix(it["block"])
            reqs.append(Request(
                custom_id=f"item-{it['id']}",
                params=MessageCreateParamsNonStreaming(
                    model=MODEL, max_tokens=4000,
                    output_config={"format": {"type": "json_schema", "schema": schema}},
                    messages=[{"role": "user", "content": [
                        # constant, and first, so the cache can hold it
                        {"type": "text", "text": base,
                         "cache_control": {"type": "ephemeral"}},
                        {"type": "text", "text": tail}]}])))
        b = cl.messages.batches.create(requests=reqs)
        rid = provenance.start_run(MODEL, f"corpus-{run}-{key}", psha, variant="schema",
                                   shots=SHOTS, n_items=len(reqs), batch_id=b.id,
                                   note=f"corpus pass {run} {key}")
        man["chunks"][key] = {"batch_id": b.id, "run_id": rid, "n": len(reqs),
                              "ids": [it["id"] for it in chunk],
                              "submitted": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        man_f.write_text(json.dumps(man, indent=1))
        print(f"  {key}  {b.id}  {len(reqs)} requests")
        sent += 1
    print(f"submitted {sent} chunk(s); {len(man['chunks'])}/{len(chunks)} total")
    return man


def collect(run: str, wait: bool = True):
    d = run_dir(run)
    man = json.loads((d / "manifest.json").read_text())
    items = {it["id"]: it for it in items_to_extract()}
    cl = BX.client()
    fails, usage = [], Counter()
    for key, meta in sorted(man["chunks"].items()):
        out_raw = d / "raw" / f"{key}.jsonl"
        if out_raw.exists() and meta.get("collected"):
            continue
        while True:
            b = cl.messages.batches.retrieve(meta["batch_id"])
            if b.processing_status == "ended":
                break
            if not wait:
                print(f"  {key}: {b.processing_status}")
                break
            time.sleep(60)
        if b.processing_status != "ended":
            continue
        raws, inter, clean, evid = [], [], [], []
        errs = 0
        for res in cl.messages.batches.results(meta["batch_id"]):
            iid = int(res.custom_id.split("-")[1])
            if res.result.type != "succeeded":
                errs += 1
                continue
            u = res.result.message.usage
            usage["input"] += u.input_tokens
            usage["output"] += u.output_tokens
            usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
            usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
            try:
                raw = json.loads(next(c.text for c in res.result.message.content
                                      if c.type == "text"))
            except Exception:
                errs += 1
                continue
            it = items.get(iid, {})
            block = it.get("block", "")
            flat, spans, bad = unwrap_evidence(raw, block)
            base = {"item_id": iid, "case_number": it.get("case_number"),
                    "meeting_date": it.get("meeting_date"), "year": it.get("year"),
                    "source_file": it.get("source_file"), "item_index": it.get("item_index")}
            raws.append({**base, "reply": raw})
            inter.append({**base, **coerce_record(flat)})
            clean.append({**base, **normalize_record(coerce_record(flat))})
            if spans:
                evid.append({**base, "evidence": spans})
            fails += [(iid, f["field"], f["reason"], str(f["value"])[:300],
                       str(f["evidence"])[:300]) for f in bad]
        for sub, rows in (("raw", raws), ("interim", inter), ("clean", clean),
                          ("evidence", evid)):
            (d / sub / f"{key}.jsonl").write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
        provenance.save_predictions(meta["run_id"],
                                    {r["item_id"]: r for r in clean},
                                    {r["item_id"]: r["reply"] for r in raws})
        meta["collected"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        meta["n_ok"], meta["n_failed"] = len(raws), errs
        (d / "manifest.json").write_text(json.dumps(man, indent=1))
        print(f"  {key}: {len(raws)} ok, {errs} failed")
    if fails:
        with (d / "verification_failures.csv").open("a", newline="") as fh:
            w = csv.writer(fh)
            if fh.tell() == 0:
                w.writerow(["item_id", "field", "reason", "value", "evidence"])
            w.writerows(fails)
    man["usage"] = dict(usage)
    man["cost_usd"] = round(sum(usage[k] * PRICE[k] * 0.5 / 1e6 for k in PRICE), 2)
    (d / "manifest.json").write_text(json.dumps(man, indent=1))
    print(f"\ntokens {dict(usage)}\ncost ${man['cost_usd']}")
    return man


def status(run: str):
    d = run_dir(run)
    if not (d / "manifest.json").exists():
        print("no run yet")
        return
    man = json.loads((d / "manifest.json").read_text())
    done = sum(1 for m in man["chunks"].values() if m.get("collected"))
    ok = sum(m.get("n_ok", 0) for m in man["chunks"].values())
    print(f"run {man['run']}  model {man['model']}  prompt {man['prompt_sha'][:12]}  "
          f"schema v{man['schema_version']}")
    print(f"chunks {done}/{len(man['chunks'])} collected   records {ok}/{man['n_items']}")
    if "cost_usd" in man:
        print(f"cost so far ${man['cost_usd']}")
    cl = BX.client()
    for key, m in sorted(man["chunks"].items()):
        if m.get("collected"):
            continue
        b = cl.messages.batches.retrieve(m["batch_id"])
        rc = b.request_counts
        print(f"   {key}: {b.processing_status}  succeeded {rc.succeeded}/{m['n']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--chunks", type=int, default=None, help="submit at most this many")
    ap.add_argument("--no-wait", action="store_true")
    a = ap.parse_args()
    if a.submit:
        submit(a.run, a.chunks)
    if a.collect:
        collect(a.run, wait=not a.no_wait)
    if a.status:
        status(a.run)


if __name__ == "__main__":
    main()
