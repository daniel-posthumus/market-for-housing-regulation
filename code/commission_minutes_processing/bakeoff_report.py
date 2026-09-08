#!/usr/bin/env python3
"""
bakeoff_report.py
-----------------
Purpose : Turn the raw bake-off predictions into the numbers the memo reports — accuracy by
          method and field, over-extraction (a value invented where the gold is blank), the
          per-section rollup, and a classified sample of each method's mistakes.
Inputs  : bakeoff/raw_<method>.json from bakeoff_extract.py, plus labels.db for the gold.
Outputs : bakeoff/report.json (everything the memo quotes), printed summary tables.
Author  : Dan Post
Created : 2026-09-06

Notes
-----
Two rates, deliberately kept apart:
  • ACCURACY is measured only where the gold record has a value. It answers "when there is
    something to find, does the method find it?"
  • OVER-EXTRACTION is measured only where the gold record is EMPTY. It answers "does the
    method invent something when there is nothing there?" A method can score well on the
    first and badly on the second; the regex extractor is built to stay silent, an LLM is
    not, and averaging them into one number hides exactly the difference that matters.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from extraction_common import (SCHEMA, FIELDS, FIELD_BY_NAME, field_match,  # noqa: E402
                               compare_field, format_ok, is_empty)
import bakeoff_extract as BX                                                        # noqa: E402
from autoextract import extract                                                     # noqa: E402
from extraction_common import coerce_record                                         # noqa: E402

OUT = HERE / "bakeoff"


SPLIT = OUT / "split.json"


def subset(items, which):
    """'all' | 'train' | 'test' — the frozen split, so a tuned prompt can be reported
    against items it was never shown."""
    if which == "all" or not SPLIT.exists():
        return items
    ids = set(json.loads(SPLIT.read_text())[which])
    return [it for it in items if it["id"] in ids]


def load():
    items = BX.gold()
    preds = {"regex": {it["id"]: coerce_record(extract(it["block"])) for it in items}}
    for p in sorted(OUT.glob("raw_*.json")):          # picks up prompt variants too
        preds[p.stem[4:]] = {int(k): v for k, v in json.loads(p.read_text()).items()}
    return items, preds


def main(which="all", cmp=compare_field):
    items, preds = load()
    items = subset(items, which)
    order = ["regex", "haiku-4.5", "haiku-4.5-v2", "sonnet-5", "sonnet-5-v2", "opus-5", "opus-5-v2"]
    methods = [m for m in order if m in preds] + [m for m in preds if m not in order]
    print(f"[{which}] gold {len(items)} items; comparison: {cmp.__name__}\n")

    rep = {"n_items": len(items), "subset": which, "methods": methods, "fields": {}, "sections": {},
           "overall": {}, "errors": defaultdict(list)}

    for fld in FIELDS:
        have = [it for it in items if not is_empty(it["gold"].get(fld))]
        blank = [it for it in items if is_empty(it["gold"].get(fld))]
        row = {"n_gold": len(have), "n_blank": len(blank), "acc": {}, "overfill": {}}
        for m in methods:
            P = preds[m]
            hit = sum(1 for it in have if it["id"] in P and cmp(P[it["id"]], it["gold"], fld))
            row["acc"][m] = 100 * hit / len(have) if have else None
            inv = sum(1 for it in blank if it["id"] in P and not is_empty(P[it["id"]].get(fld)))
            row["overfill"][m] = 100 * inv / len(blank) if blank else None
            for it in have:
                if it["id"] in P and not cmp(P[it["id"]], it["gold"], fld):
                    rep["errors"][f"{m}|{fld}"].append(
                        {"id": it["id"], "case": it["case_number"], "year": it["year"],
                         "gold": it["gold"].get(fld), "pred": P[it["id"]].get(fld)})
        rep["fields"][fld] = row

    for m in methods:
        num = sum(rep["fields"][f]["acc"][m] * rep["fields"][f]["n_gold"]
                  for f in FIELDS if rep["fields"][f]["acc"][m] is not None)
        den = sum(rep["fields"][f]["n_gold"] for f in FIELDS if rep["fields"][f]["acc"][m] is not None)
        onum = sum(rep["fields"][f]["overfill"][m] * rep["fields"][f]["n_blank"]
                   for f in FIELDS if rep["fields"][f]["overfill"][m] is not None)
        oden = sum(rep["fields"][f]["n_blank"] for f in FIELDS if rep["fields"][f]["overfill"][m] is not None)
        exact = sum(1 for it in items if it["id"] in preds[m] and all(
            cmp(preds[m][it["id"]], it["gold"], f)
            for f in FIELDS if not is_empty(it["gold"].get(f))))
        rep["overall"][m] = {"accuracy": num / den, "overfill": onum / oden,
                             "exact_record": 100 * exact / len(items),
                             # predictions are keyed over the whole gold set; coverage must
                             # be counted inside the subset being scored, not against it
                             "coverage": 100 * sum(1 for it in items if it["id"] in preds[m])
                                         / len(items)}

    secs = defaultdict(list)
    for f in SCHEMA:
        secs[f["section"]].append(f["name"])
    for sec, fs in secs.items():
        rep["sections"][sec] = {}
        for m in methods:
            num = sum(rep["fields"][f]["acc"][m] * rep["fields"][f]["n_gold"]
                      for f in fs if rep["fields"][f]["acc"][m] is not None)
            den = sum(rep["fields"][f]["n_gold"] for f in fs if rep["fields"][f]["acc"][m] is not None)
            rep["sections"][sec][m] = num / den if den else None

    w = max(len(f) for f in FIELDS) + 1
    print(f"{'field':{w}s} {'n':>4s} {'blank':>5s} | " +
          " | ".join(f"{m:^17s}" for m in methods))
    print(f"{'':{w}s} {'':>4s} {'':>5s} | " + " | ".join("  acc%   overfill%" for _ in methods))
    for f in FIELDS:
        r = rep["fields"][f]
        cells = []
        for m in methods:
            a = r["acc"][m]; o = r["overfill"][m]
            cells.append(f"{(a if a is not None else float('nan')):6.1f}  {(o if o is not None else float('nan')):8.1f}")
        print(f"{f:{w}s} {r['n_gold']:4d} {r['n_blank']:5d} | " + " | ".join(cells))
    print()
    for m in methods:
        o = rep["overall"][m]
        print(f"  {m:10s} accuracy {o['accuracy']:5.1f}%   over-extraction {o['overfill']:5.1f}%   "
              f"exact records {o['exact_record']:5.1f}%   coverage {o['coverage']:5.1f}%")
    print("\nby section:")
    for sec, d in rep["sections"].items():
        print(f"  {sec:18s} " + "  ".join(f"{m}={d[m]:5.1f}%" for m in methods if d[m] is not None))

    rep["errors"] = {k: v for k, v in rep["errors"].items()}
    (OUT / "report.json").write_text(json.dumps(rep, indent=1, default=str))
    print(f"\n→ {OUT / 'report.json'}")


if __name__ == "__main__" and not {"--taxonomy", "--request-type"} & set(sys.argv):
    which = ("test" if "--test" in sys.argv else "train" if "--train" in sys.argv else "all")
    main(which, field_match if "--exact" in sys.argv else compare_field)


def taxonomy(top=6, per=3):
    """A classified sample of each method's mistakes, for the memo's error section."""
    import random
    rep = json.loads((OUT / "report.json").read_text())
    random.seed(0)
    for m in rep["methods"]:
        worst = sorted(((f, rep["fields"][f]["acc"][m], rep["fields"][f]["n_gold"])
                        for f in FIELDS
                        if rep["fields"][f]["acc"][m] is not None and rep["fields"][f]["n_gold"] >= 20),
                       key=lambda x: x[1])[:top]
        print(f"\n===== {m}: weakest fields (n>=20) =====")
        for f, a, n in worst:
            errs = rep["errors"].get(f"{m}|{f}", [])
            print(f"\n  {f}  ({a:.1f}% on n={n}, {len(errs)} misses)")
            for e in random.sample(errs, min(per, len(errs))):
                g = str(e["gold"])[:64]
                p = str(e["pred"])[:64]
                print(f"     id={e['id']} {e['year']} {e['case']}\n        gold: {g!r}\n        pred: {p!r}")


if __name__ == "__main__" and "--taxonomy" in sys.argv:
    taxonomy()


def request_type_deepdive():
    """Is the model following the prompt's 'inferable from the case-number suffix' hint?

    Every request_type disagreement is scored three ways: what the human said, what the
    method said, and what the suffix alone would give. If a method's answer tracks the
    suffix where the human's does not, the prompt line is the cause, not the model.
    """
    from autoextract import derive_request_type
    items, preds = load()
    order = ["regex", "haiku-4.5", "haiku-4.5-v2", "sonnet-5", "sonnet-5-v2", "opus-5", "opus-5-v2"]
    methods = [m for m in order if m in preds]
    print(f"\n{'method':14s} {'n':>4s} {'agree':>6s} {'follows suffix':>15s} {'reads text':>11s} {'neither':>8s}")
    detail = {}
    for m in methods:
        agree = suffix = text = other = 0
        rows = []
        for it in items:
            g = it["gold"].get("request_type") or ""
            if not g or it["id"] not in preds[m]:
                continue
            p = preds[m][it["id"]].get("request_type") or ""
            suf = derive_request_type(it["case_number"] or "")
            if p == g:
                agree += 1
            elif p == suf and g != suf:
                suffix += 1
                rows.append((it["id"], it["case_number"], g, p, "followed the suffix"))
            elif p != suf and g == suf:
                text += 1
                rows.append((it["id"], it["case_number"], g, p, "ignored the suffix"))
            else:
                other += 1
                rows.append((it["id"], it["case_number"], g, p, "neither"))
        n = agree + suffix + text + other
        print(f"{m:14s} {n:4d} {100*agree/n:5.1f}% {100*suffix/n:14.1f}% {100*text/n:10.1f}% {100*other/n:7.1f}%")
        detail[m] = rows
    for m in methods:
        if not detail[m]:
            continue
        print(f"\n  --- {m}: every request_type miss ---")
        for iid, cn, g, p, why in detail[m][:14]:
            print(f"    id={iid:<6} {cn:18s} gold={g:38s} pred={p:38s} [{why}]")
    return detail


if __name__ == "__main__" and "--request-type" in sys.argv:
    request_type_deepdive()
