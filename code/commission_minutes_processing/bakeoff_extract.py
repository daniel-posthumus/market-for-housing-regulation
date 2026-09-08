#!/usr/bin/env python3
"""
bakeoff_extract.py
------------------
Purpose : Measure, field by field, how the deterministic extractor compares with Claude on
          the SAME blocks — so the schema can be split into "regex is already right here"
          and "this needs a model", and so the cheapest adequate model is chosen by
          measurement rather than by guess.
Inputs  : labels.db (the hand-labelled gold records + their source blocks), the schema and
          prompt from extraction_common, and an API key at ../../api_keys/claude_api.txt.
Outputs : bakeoff/raw_<method>.json  (one predicted record per item, per method)
          bakeoff/batches.json       (batch ids, so a run can be resumed)
          bakeoff/scores.csv         (method x field accuracy)
Author  : Dan Post
Created : 2026-09-06

Notes
-----
Scoring reuses `extraction_common.field_match` — the SAME comparison the trainer and the
QA report use — so a number here means what it means everywhere else in the project. Only
fields NON-EMPTY IN THE GOLD RECORD are scored: rewarding a method for leaving a field
blank when the gold is blank would put the regex extractor, which leaves most things blank,
at the top of the table for the wrong reason.

The models get exactly the prompt the schema generates (`PROMPT_INSTRUCTION`) plus the raw
block — no few-shot examples. That is the honest floor: few-shot would raise the model
numbers and cannot raise the regex number, so leaving it out keeps the comparison about the
method rather than about how much prompt engineering each side received.

Usage:
  python bakeoff_extract.py --submit          # build + send the batches
  python bakeoff_extract.py --collect         # poll, download, score
  python bakeoff_extract.py --score-only      # re-score what is already downloaded
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from extraction_common import (SCHEMA, FIELDS, EXTRACTED_FIELDS,  # noqa: E402
                               EVIDENCE_FIELDS, build_prompt, item_suffix, prompt_sha,
                               coerce_record, compare_field, field_match, is_empty,
                               unwrap_evidence, SCHEMA_VERSION, era_of)
from autoextract import extract                                     # noqa: E402
from normalize import normalize_record                              # noqa: E402
import provenance                                                   # noqa: E402

DB = HERE / "labeling_app" / "labels.db"
OUT = HERE / "bakeoff"
KEY = HERE.parents[1] / "api_keys" / "claude_api.txt"

# Effort is NOT sent to Haiku 4.5 — the parameter errors on that model. The 5-series pair
# run at `low`: this is structured extraction from a short block, the workload class where
# low effort holds quality, and it keeps latency and cost honest for a 16k-item corpus.
# ── the prompt ───────────────────────────────────────────────────────────────
# There used to be a v1/v2 A/B here on an added blank policy — the hypothesis being that a
# model over-fills not because it cannot tell, but because nothing ever told it that silence
# is a valid answer. v2 won, so under schema v2 the blank policy is generated as part of the
# prompt itself (`extraction_common.BLANK_POLICY`, emitted by `build_prompt`) and there is no
# longer a variant to choose. The old runs stay scoreable: scoring reads whatever
# raw_*.json files are on disk, including raw_haiku-4.5-v2.json.
#
# `--variant` therefore takes one value. It is kept rather than deleted so an old command
# line fails loudly on the removed choice instead of silently running a different prompt.
PROMPTS = {"schema": build_prompt}

# ── few-shot ─────────────────────────────────────────────────────────────────
# Zero-shot was the honest floor for the method comparison; it is not the right way to RUN
# the extractor. Most of what the model still gets wrong is a CONVENTION rather than a
# reading failure — that `project_address` is the street address and not the minutes'
# cross-street gloss, that "Approved as proposed" on a DR case means the Commission declined
# to take DR, that a blank is a legitimate answer. Conventions are what examples teach well
# and prose teaches badly.
#
# Examples are drawn ONLY from the train half of the frozen split, and retrieved by content
# overlap with the item being extracted, so a discretionary-review item is shown other
# discretionary-review items.
#
# Retrieval is ERA-MATCHED (spec §4.3): the 1998-2014 HTML minutes and the 2015+ PDF minutes
# are different documents, and an example from the wrong era teaches the wrong block anatomy
# — which is exactly the failure this configuration is most exposed to, since every example
# available today is HTML-era. When an era has no examples we say so and fall back rather
# than silently showing the mismatched ones as if they were fine.
_ERA_WARNED = set()


def _words(t):
    return {w for w in re.findall(r"[a-z]{4,}", (t or "").lower())}


def few_shot_block(block: str, pool: list, k: int, era: str | None = None) -> str:
    """The k most similar labelled examples from the pool, as worked examples."""
    if era:
        same = [e for e in pool if era_of(e["year"]) == era]
        if len(same) >= k:
            pool = same
        elif era not in _ERA_WARNED:
            _ERA_WARNED.add(era)
            print(f"  ! era {era}: only {len(same)} of {k} examples available in the train "
                  f"half; falling back to cross-era retrieval. These examples teach a "
                  f"different block anatomy — treat the pre-fill as less trustworthy.",
                  file=sys.stderr)
    bw = _words(block)
    scored = sorted(pool, key=lambda e: -len(bw & e["_w"]) / (len(bw | e["_w"]) or 1))
    out = ["Here are worked examples from this corpus. Follow the conventions they show —"
           " especially about what to leave blank and how much text to copy.\n"]
    for e in scored[:k]:
        out.append("BLOCK:\n" + e["block"].strip()[:1800])
        out.append("JSON:\n" + json.dumps(
            {f: e["gold"][f] for f in EXTRACTED_FIELDS if f in e["gold"]},
            ensure_ascii=False) + "\n")
    return "\n".join(out) + "\n---\n\n"

MODELS = {
    "haiku-4.5": dict(model="claude-haiku-4-5", effort=None),
    "sonnet-5":  dict(model="claude-sonnet-5",  effort="low"),
    "opus-5":    dict(model="claude-opus-5",    effort="low"),
}


def json_schema(evidence: bool = True) -> dict:
    """The response shape, generated from SCHEMA so it can never drift from the fields.

    Derived fields are omitted — the model is never asked for a value Python can compute.
    On EVIDENCE_FIELDS the value is wrapped as {"value", "evidence"}: `evidence` must be a
    verbatim substring of the block, which is what makes an invented value detectable at
    extraction time rather than merely discounted at scoring time.
    """
    def bare(f):
        t = f["type"]
        if t == "list":
            return {"type": "array", "items": {"type": "string"}}
        if t == "list_of_objects":
            # Generated from `item_schema`, so a key added to the shape reaches the model
            # instead of being silently absent from the response contract.
            ch = f.get("item_choices") or {}
            props = {k: ({"type": "string", "enum": list(ch[k]) + [""]} if k in ch
                         else {"type": "string"})
                     for k in (f.get("item_schema") or {"name": "str"})}
            return {"type": "array", "items": {
                "type": "object", "properties": props,
                "required": list(props), "additionalProperties": False}}
        if t == "int":
            return {"type": "integer"}
        if t == "enum":
            # "" is a real answer — "the minutes do not say" — so it is in the vocabulary
            return {"type": "string", "enum": list(f["choices"]) + [""]}
        return {"type": "string"}

    props = {}
    for f in SCHEMA:
        if f.get("derived"):
            continue
        n = f["name"]
        if evidence and n in EVIDENCE_FIELDS:
            props[n] = {"type": "object",
                        "properties": {"value": bare(f),
                                       "evidence": {"type": "string"}},
                        "required": ["value", "evidence"], "additionalProperties": False}
        else:
            props[n] = bare(f)
    return {"type": "object", "properties": props,
            "required": list(props), "additionalProperties": False}


def gold() -> list[dict]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    # `review` is included deliberately: an item flagged for a second look is still a hand
    # label, and excluding it would shrink the gold set — and silently invalidate the frozen
    # split — every time something is queued for review. What is excluded is the
    # representativeness draw, which carries a machine pre-fill and no human judgement yet.
    rows = con.execute("""SELECT i.id, i.year, i.case_number, i.source_file, l.data,
                                 i.block_text, COALESCE(l.notes,''), l.status
                          FROM items i JOIN labels l ON l.item_id = i.id
                          WHERE l.status IN ('done','flagged','review')
                            AND l.data IS NOT NULL""").fetchall()
    con.close()
    out = []
    for iid, year, cn, src, data, block, notes, status in rows:
        rec = json.loads(data)
        if not (rec.get("action") or rec.get("case_number")):
            continue                      # never labelled at all
        # The draw markers exclude a machine PRE-FILL, not a finished label. Testing the
        # note text alone kept the whole modern stratum out of the gold set after it had been
        # hand-labelled — the note still says which draw the item came from, which is
        # provenance worth keeping, not a reason to discard the work.
        if "NOT yet labelled" in notes:
            continue
        if "representativeness" in notes and status != "done":
            continue
        out.append(dict(id=iid, year=year, case_number=cn, source_file=src,
                        gold=coerce_record(rec), block=block))
    out.sort(key=lambda r: r["id"])
    return out


def client():
    import anthropic
    return anthropic.Anthropic(api_key=KEY.read_text().strip())


def _load_batches() -> dict:
    """Batch registry. Old runs stored a bare id per key; new runs store a dict with the
    run_id, so a collection can attribute its predictions to the configuration that made
    them. Both shapes are read."""
    f = OUT / "batches.json"
    raw = json.loads(f.read_text()) if f.exists() else {}
    return {k: (v if isinstance(v, dict) else {"batch_id": v}) for k, v in raw.items()}


def _assemble(it, prompt_base, pool, shots, cache_order=False):
    """(prefix, full user message) for one item.

    `cache_order` puts the INSTRUCTIONS first, before the retrieved examples. The
    instructions are identical on every request and the examples are not, so only this
    ordering leaves a constant leading span for the prompt cache to hold — about 3,600 of
    the 10,100 input tokens per item. In the default ordering the examples come first and
    nothing is cacheable at all.
    """
    ex = few_shot_block(it["block"], pool, shots, era_of(it["year"])) if shots else ""
    prefix = (prompt_base + ex) if cache_order else (ex + prompt_base)
    return prefix, prefix + item_suffix(it["block"])


def submit(items, variant="schema", only=None, shots=0, pool=None, tag="",
           cache_order=False):
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    cl, schema = client(), json_schema()
    ids = _load_batches()
    prompt_base = PROMPTS[variant]()
    if shots:
        for e in pool:
            e["_w"] = _words(e["block"])
    gv = _gold_version(items)
    for name, cfg in MODELS.items():
        if only and name not in only:
            continue
        reqs, shas = [], set()
        for it in items:
            prefix, content = _assemble(it, prompt_base, pool, shots, cache_order)
            shas.add(prompt_sha(prefix))
            oc = {"format": {"type": "json_schema", "schema": schema}}
            if cfg["effort"]:
                oc["effort"] = cfg["effort"]
            if cache_order:
                # two blocks: the constant instructions, marked for caching, then the rest
                body = [{"type": "text", "text": prompt_base,
                         "cache_control": {"type": "ephemeral"}},
                        {"type": "text", "text": content[len(prompt_base):]}]
            else:
                body = content
            reqs.append(Request(
                custom_id=f"item-{it['id']}",
                params=MessageCreateParamsNonStreaming(
                    model=cfg["model"], max_tokens=4000, output_config=oc,
                    messages=[{"role": "user", "content": body}])))
        b = cl.messages.batches.create(requests=reqs)
        key = name + (f"-{tag}" if tag else "")
        # Retrieved examples make the prefix per-item, so there is no single prefix sha for
        # a few-shot run. Record the one sha when it is constant (zero-shot) and the sha of
        # the sorted set otherwise — either way, a prompt change moves the recorded value.
        psha = (next(iter(shas)) if len(shas) == 1
                else prompt_sha("|".join(sorted(shas))))
        run_id = provenance.start_run(
            cfg["model"], key, psha, gold_version=gv, variant=variant, shots=shots,
            n_items=len(reqs), batch_id=b.id,
            note=f"bakeoff submit ({len(shas)} distinct prefixes)")
        ids[key] = {"batch_id": b.id, "run_id": run_id, "model": cfg["model"],
                    "variant": variant, "shots": shots, "prompt_sha": psha,
                    "schema_version": SCHEMA_VERSION, "gold_version": gv}
        print(f"  {key:14s} batch {b.id}  run {run_id}  "
              f"({len(reqs)} requests, {b.processing_status})")
    OUT.mkdir(exist_ok=True)
    (OUT / "batches.json").write_text(json.dumps(ids, indent=2))
    return ids


def _gold_version(items) -> str:
    """Name the gold snapshot without importing gold_split (which imports this module)."""
    import hashlib
    h = hashlib.sha256()
    for it in sorted(items, key=lambda r: r["id"]):
        h.update(str(it["id"]).encode())
        h.update(json.dumps(it["gold"], sort_keys=True, ensure_ascii=False).encode())
    return provenance.gold_version(h.hexdigest())


def collect(items):
    cl = client()
    ids = _load_batches()
    blocks = {it["id"]: it["block"] for it in items}
    for name, meta in ids.items():
        bid = meta["batch_id"]
        while True:
            b = cl.messages.batches.retrieve(bid)
            if b.processing_status == "ended":
                break
            print(f"  {name}: {b.processing_status} "
                  f"(done {b.request_counts.succeeded}/{sum(vars(b.request_counts).values())})",
                  flush=True)
            time.sleep(30)
        preds, raws, ev, fails, errs = {}, {}, {}, [], 0
        for res in cl.messages.batches.results(bid):
            iid = int(res.custom_id.split("-")[1])
            if res.result.type != "succeeded":
                errs += 1
                continue
            try:
                txt = next(x.text for x in res.result.message.content if x.type == "text")
                raw = json.loads(txt)
            except Exception:
                errs += 1
                continue
            raws[iid] = raw
            # Unwrap BEFORE coercion: the {value, evidence} wrapper is a transport shape,
            # not a schema field, and everything downstream expects flat records.
            flat, spans, bad = unwrap_evidence(raw, blocks.get(iid, ""))
            preds[iid] = normalize_record(coerce_record(flat))
            if spans:
                ev[iid] = spans
            fails += [(iid, f["field"], f["reason"], str(f["value"])[:300],
                       str(f["evidence"])[:300]) for f in bad]
        (OUT / f"raw_{name}.json").write_text(json.dumps(preds, indent=1))
        if ev:
            (OUT / f"evidence_{name}.json").write_text(json.dumps(ev, indent=1))
        run_id = meta.get("run_id")
        if run_id:
            provenance.save_predictions(run_id, preds, raws)
            provenance.log_failures(run_id, fails)
        by_reason = {}
        for _, _, reason, _, _ in fails:
            by_reason[reason] = by_reason.get(reason, 0) + 1
        print(f"  {name:10s} {len(preds)} records, {errs} failed"
              + (f", {len(fails)} evidence failures {by_reason}" if fails else ""))


def score(items):
    methods = ["regex"] + list(MODELS)
    preds = {"regex": {it["id"]: coerce_record(extract(it["block"])) for it in items}}
    for name in MODELS:
        p = OUT / f"raw_{name}.json"
        preds[name] = {int(k): v for k, v in json.loads(p.read_text()).items()} if p.exists() else {}
    rows = []
    for fld in FIELDS:
        scored = [it for it in items if not is_empty(it["gold"].get(fld))]
        row = {"field": fld, "n_gold": len(scored)}
        for m in methods:
            hit = sum(1 for it in scored
                      if it["id"] in preds[m] and field_match(preds[m][it["id"]], it["gold"], fld))
            row[m] = round(100 * hit / len(scored), 1) if scored else None
        rows.append(row)
    OUT.mkdir(exist_ok=True)
    with (OUT / "scores.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["field", "n_gold"] + methods)
        w.writeheader()
        w.writerows(rows)
    tot = {m: (sum(r[m] * r["n_gold"] for r in rows if r[m] is not None)
               / sum(r["n_gold"] for r in rows if r[m] is not None)) for m in methods}
    print(f"\n{'field':36s} {'n':>4s} " + " ".join(f"{m:>10s}" for m in methods))
    for r in rows:
        print(f"  {r['field']:34s} {r['n_gold']:4d} " +
              " ".join(f"{(r[m] if r[m] is not None else 0):9.1f}%" for m in methods))
    print(f"  {'— WEIGHTED OVERALL —':34s} {sum(r['n_gold'] for r in rows):4d} " +
          " ".join(f"{tot[m]:9.1f}%" for m in methods))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--score-only", action="store_true")
    ap.add_argument("--variant", default="schema", choices=["schema"],
                    help="the schema-generated prompt; the old v1/v2 A/B is settled (see "
                         "the PROMPTS comment)")
    ap.add_argument("--only", default=None, help="comma-separated model names")
    ap.add_argument("--shots", type=int, default=0, help="few-shot examples, drawn from train")
    ap.add_argument("--on", default="all", choices=["all", "train", "test"])
    ap.add_argument("--tag", default="", help="suffix for the output file name")
    ap.add_argument("--cache-order", action="store_true",
                    help="instructions first, marked for the prompt cache (see _assemble)")
    a = ap.parse_args()
    items = gold()
    split_f = OUT / "split.json"
    if split_f.exists():
        sp = json.loads(split_f.read_text())
        train_ids, test_ids = set(sp["train"]), set(sp["test"])
        pool = [it for it in items if it["id"] in train_ids]
        if a.on == "train":
            items = pool
        elif a.on == "test":
            items = [it for it in items if it["id"] in test_ids]
    else:
        pool = items
    print(f"gold items: {len(items)}  (years {min(i['year'] for i in items)}"
          f"-{max(i['year'] for i in items)})")
    if a.submit:
        submit(items, a.variant, a.only.split(",") if a.only else None,
               a.shots, pool, a.tag, a.cache_order)
    if a.collect:
        collect(items)
    if a.collect or a.score_only:
        score(items)


if __name__ == "__main__":
    main()
