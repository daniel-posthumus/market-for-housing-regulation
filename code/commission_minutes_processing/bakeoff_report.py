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
                               compare_field, format_ok, is_empty, SCHEMA_VERSION)
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
    # Same storage layer as the model arm (see BX.regex_preds) — otherwise the comparison
    # is partly measuring which side went through normalize.py.
    preds = {"regex": BX.regex_preds(items)}
    for p in sorted(OUT.glob("raw_*.json")):          # picks up prompt variants too
        preds[p.stem[4:]] = {int(k): v for k, v in json.loads(p.read_text()).items()}
    _warn_stale(preds)
    return items, preds


def _warn_stale(preds: dict) -> None:
    """Say which prediction files predate the current schema.

    `raw_*.json` files accumulate and this globs all of them, so a table can silently put a
    schema-v1 run (whose `speakers` are bare names and whose `resolution_or_motion_no` no
    longer exists) beside a v2 one and invite a reader to compare the columns. They are not
    comparable field-for-field. `batches.json` records the schema version a run was
    submitted under; a run with no entry, or an older version, is called out here rather
    than dropped, because the old numbers are still worth seeing in their own right.
    """
    try:
        reg = json.loads((OUT / "batches.json").read_text())
    except Exception:
        return
    stale = sorted(m for m in preds if m != "regex"
                   and (reg.get(m) or {}).get("schema_version", 1) < SCHEMA_VERSION)
    if stale:
        print(f"  ! pre-v{SCHEMA_VERSION} run(s) included: {', '.join(stale)}. Their records "
              f"were produced under an older schema and are NOT comparable field-for-field "
              f"with the v{SCHEMA_VERSION} gold — read those columns on their own terms.\n")


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
            # Denominators count only items this method actually predicted. Dividing by
            # every gold item folded missing predictions into the accuracy number and
            # capped a method at its coverage; coverage is reported on its own line below,
            # which is where a gap belongs. Every current method is at 100%, so this does
            # not move a published figure — it stops the next partial run from looking
            # inaccurate when it is merely incomplete.
            got = [it for it in have if it["id"] in P]
            got_blank = [it for it in blank if it["id"] in P]
            hit = sum(1 for it in got if cmp(P[it["id"]], it["gold"], fld))
            row["acc"][m] = 100 * hit / len(got) if got else None
            inv = sum(1 for it in got_blank if not is_empty(P[it["id"]].get(fld)))
            row["overfill"][m] = 100 * inv / len(got_blank) if got_blank else None
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
        cov = [it for it in items if it["id"] in preds[m]]
        exact = sum(1 for it in cov if all(
            cmp(preds[m][it["id"]], it["gold"], f)
            for f in FIELDS if not is_empty(it["gold"].get(f))))
        rep["overall"][m] = {"accuracy": num / den, "overfill": onum / oden,
                             "exact_record": 100 * exact / len(cov) if cov else 0.0,
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


if __name__ == "__main__" and not {"--taxonomy", "--request-type",
                                   "--write-report"} & set(sys.argv):
    which = ("test" if "--test" in sys.argv else "train" if "--train" in sys.argv else "all")
    main(which, field_match if "--exact" in sys.argv else compare_field)


# ── the scored-run report the memo is generated from ─────────────────────────
# `bakeoff_memo.py` builds every table in extraction_method_comparison.tex out of
# `bakeoff/g3_report.json`, and until 2026-09-09 nothing in the repo WROTE that file — it
# was produced once, by hand, in a session. So the memo's numbers could not be regenerated
# after a scoring change, which is the same failure `plot_extraction_accuracy.py` had.
#
# `usage` is the exception and is PRESERVED rather than recomputed: token counts are what
# the API reported for that run and there is no way to re-derive them without paying for
# the run again. Everything else — every accuracy, over-extraction rate and per-field
# number — comes from the raw predictions and the gold set, so it is recomputed here.
SUBSETS = ["all", "train", "test", "html", "pdf", "test_html", "test_pdf"]


def _subset(items, which, split):
    tr, te = set(split.get("train", [])), set(split.get("test", []))
    def era(it):
        return "html" if it["year"] <= 2014 else "pdf"
    sel = {
        "all":       lambda it: True,
        "train":     lambda it: it["id"] in tr,
        "test":      lambda it: it["id"] in te,
        "html":      lambda it: era(it) == "html",
        "pdf":       lambda it: era(it) == "pdf",
        "test_html": lambda it: it["id"] in te and era(it) == "html",
        "test_pdf":  lambda it: it["id"] in te and era(it) == "pdf",
    }[which]
    return [it for it in items if sel(it)]


def _rate(items, P, cmp) -> dict:
    """Accuracy where gold has a value, over-extraction where it does not — the two rates
    the memo keeps apart, on the items this method actually predicted."""
    hit = n = inv = blank = 0
    for it in items:
        if it["id"] not in P:
            continue
        p = P[it["id"]]
        for f in FIELDS:
            if is_empty(it["gold"].get(f)):
                blank += 1
                inv += 0 if is_empty(p.get(f)) else 1
            else:
                n += 1
                hit += 1 if cmp(p, it["gold"], f) else 0
    return {"accuracy": round(100 * hit / n, 1) if n else None, "n_scored": n,
            "over_extraction": round(100 * inv / blank, 1) if blank else None,
            "n_blank": blank}


def write_scored_report(methods: list[str], out: Path, cmp=compare_field) -> dict:
    items, preds = load()
    split = json.loads(SPLIT.read_text()) if SPLIT.exists() else {}
    prev = json.loads(out.read_text()) if out.exists() else {}
    rep = {"usage": prev.get("usage", {}),          # API-measured; never recomputed
           "gold_version": prev.get("gold_version", ""),
           "n_gold": len(items), "scores": {}}
    for m in methods:
        if m not in preds:
            print(f"  ! no predictions for {m}; skipped")
            continue
        P = preds[m]
        sc = {w: _rate(_subset(items, w, split), P, cmp) for w in SUBSETS}
        test = _subset(items, "test", split)
        by_field = {}
        for f in FIELDS:
            got = [it for it in test if it["id"] in P and not is_empty(it["gold"].get(f))]
            if got:
                by_field[f] = {"acc": round(100 * sum(
                    1 for it in got if cmp(P[it["id"]], it["gold"], f)) / len(got), 1),
                    "n": len(got)}
        sc["by_field_test"] = dict(sorted(by_field.items()))
        rep["scores"][m] = sc
        print(f"  {m:20s} all {sc['all']['accuracy']}%  test {sc['test']['accuracy']}%  "
              f"over-extraction {sc['test']['over_extraction']}%")
    out.write_text(json.dumps(rep, indent=1) + "\n")
    print("→", out)
    return rep


if __name__ == "__main__" and "--write-report" in sys.argv:
    i = sys.argv.index("--write-report")
    target = OUT / (sys.argv[i + 1] if len(sys.argv) > i + 1
                    and not sys.argv[i + 1].startswith("-") else "g3_report.json")
    ms = ["regex", "haiku-4.5-g3", "haiku-4.5-g3cache"]
    if "--methods" in sys.argv:
        ms = sys.argv[sys.argv.index("--methods") + 1].split(",")
    write_scored_report(ms, target,
                        field_match if "--exact" in sys.argv else compare_field)


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
