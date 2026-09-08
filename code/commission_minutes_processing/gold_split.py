#!/usr/bin/env python3
"""
gold_split.py
-------------
Purpose : Freeze a train/test split of the hand-labelled gold set, so that any prompt
          tuned against it can still be reported honestly. Once a prompt has been chosen by
          looking at items, those items no longer measure anything; the test half exists to
          never be looked at.
Inputs  : labels.db (the gold records).
Outputs : bakeoff/split.json — the item ids on each side, the seed, and a SHA-256 of the
          gold content so a later run can prove the split still refers to the same data.
Author  : Dan Post
Created : 2026-09-06

Notes
-----
Stratified by ERA and then by YEAR. Era matters because the 1998-2014 HTML minutes and the
2015+ PDF minutes are different document formats; a split that put them unevenly on the two
sides would make the test half easier or harder than the train half for a reason that has
nothing to do with the prompt. Year matters for the same reason at finer grain — the
archive's layout drifts.

The hash covers the gold RECORDS, not the block text: re-splitting is required when the
labels change (a corrected label changes what "accuracy" means), not when a block is
re-parsed. `verify()` reports drift rather than silently re-splitting.

Usage:
  python gold_split.py                 # build (refuses to overwrite) and summarise
  python gold_split.py --verify        # has the gold changed since the split was frozen?
  python gold_split.py --extend        # new items labelled: assign ONLY those, keep the rest
  python gold_split.py --refreeze      # labels edited, membership unchanged: re-hash only
  python gold_split.py --rebuild       # deliberate re-split after the ITEM SET changes
  python gold_split.py --register-gold --note "adjudication batch 1 accepted"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bakeoff_extract as BX                                   # noqa: E402
import provenance                                              # noqa: E402

OUT = HERE / "bakeoff"
SPLIT = OUT / "split.json"
SEED = 20260906
TEST_FRACTION = 0.5


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def gold_hash(items) -> str:
    h = hashlib.sha256()
    for it in sorted(items, key=lambda r: r["id"]):
        h.update(str(it["id"]).encode())
        h.update(json.dumps(it["gold"], sort_keys=True, ensure_ascii=False).encode())
    return h.hexdigest()


def build(items):
    """Stratify by (era, year), then split each stratum with one seeded shuffle."""
    strata = defaultdict(list)
    for it in items:
        era = "html" if it["year"] <= 2014 else "modern"
        strata[(era, it["year"])].append(it["id"])
    rng = random.Random(SEED)
    train, test = [], []
    for key in sorted(strata):
        ids = sorted(strata[key])
        rng.shuffle(ids)
        cut = round(len(ids) * TEST_FRACTION)
        test += ids[:cut]
        train += ids[cut:]
    return sorted(train), sorted(test)


def summarise(items, train, test):
    by = {it["id"]: it for it in items}
    print(f"train {len(train)}   test {len(test)}   (seed {SEED})")
    for name, ids in (("train", train), ("test", test)):
        yrs = Counter(by[i]["year"] for i in ids)
        era = Counter("html" if by[i]["year"] <= 2014 else "modern" for i in ids)
        print(f"  {name:5s} era={dict(era)}  years {min(yrs)}-{max(yrs)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--refreeze", action="store_true",
                    help="labels changed but membership did not: re-hash, keep the split")
    # Extending is not the same as rebuilding. A rebuild reshuffles items that have already
    # been LOOKED AT while tuning, which quietly turns test into train; extending assigns only
    # the ids that were not in the split before and leaves every existing side alone.
    ap.add_argument("--extend", action="store_true",
                    help="stratify and assign only the items missing from the frozen split")
    # Working the adjudication queue changes gold, which changes every frozen test number.
    # Registering a version is the deliberate act that says "this snapshot is the one those
    # numbers were measured against" — hence a flag, not an automatic mint.
    ap.add_argument("--register-gold", action="store_true",
                    help="register the current gold content as a new gold_version")
    ap.add_argument("--note", default="", help="what changed, for the gold_version registry")
    a = ap.parse_args()
    items = BX.gold()
    h = gold_hash(items)
    gv = provenance.gold_version(h, register=a.register_gold, note=a.note,
                                 n_items=len(items))
    print(f"gold_version {gv}   sha {h[:16]}   n={len(items)}")
    if a.register_gold:
        print("  registered. Re-run bakeoff_report.py --test so the memo's tables track "
              "this snapshot.")

    if SPLIT.exists() and not a.rebuild:
        d = json.loads(SPLIT.read_text())
        drift = d["gold_sha256"] != h
        now, was = {it["id"] for it in items}, set(d["train"]) | set(d["test"])
        print(f"split frozen {d['created']}  ({len(d['train'])} train / {len(d['test'])} test)")
        print(f"gold hash {'MATCHES' if not drift else 'HAS CHANGED'} the frozen split")
        if drift:
            print(f"  items added: {len(now - was)}   removed: {len(was - now)}")
            if now == was:
                # Membership is intact, so only label CONTENT moved — a corrected label, or
                # a schema change to how the same label is represented. Re-splitting here
                # would be harmful, not conservative: it would move items across the
                # train/test line after the train half has already been looked at.
                print("  → membership unchanged; only label content moved. Re-freeze with "
                      "--refreeze (keeps the split, re-hashes), then --register-gold.")
            else:
                print("  → the item set itself changed; --rebuild once gold is settled. "
                      "Any number measured on the old split is no longer comparable.")
        if a.extend:
            new = sorted(now - was)
            if not new:
                print("  nothing to extend: every gold item is already on a side.")
                return
            by = {it["id"]: it for it in items}
            strata = defaultdict(list)
            for i in new:
                era = "html" if by[i]["year"] <= 2014 else "modern"
                strata[(era, by[i]["year"])].append(i)
            rng = random.Random(SEED)
            add_tr, add_te = [], []
            for key in sorted(strata):
                ids = sorted(strata[key])
                rng.shuffle(ids)
                cut = round(len(ids) * TEST_FRACTION)
                add_te += ids[:cut]
                add_tr += ids[cut:]
            d["train"] = sorted(set(d["train"]) | set(add_tr))
            d["test"] = sorted(set(d["test"]) | set(add_te))
            d["gold_sha256"], d["extended"] = h, _now_iso()
            d["n_items"] = len(items)
            SPLIT.write_text(json.dumps(d, indent=1))
            print(f"  extended: +{len(add_tr)} train, +{len(add_te)} test "
                  f"({len(new)} newly labelled items); existing membership untouched")
            summarise(items, d["train"], d["test"])
            return
        if a.refreeze:
            if now != was:
                print("  refusing to --refreeze: the ITEM SET changed, not just the labels.")
                return
            d["gold_sha256"], d["refrozen"] = h, _now_iso()
            SPLIT.write_text(json.dumps(d, indent=1))
            print(f"  re-froze the hash on the existing {len(d['train'])}/{len(d['test'])} "
                  f"split → {SPLIT}")
        if not a.verify:
            summarise(items, d["train"], d["test"])
        return

    train, test = build(items)
    OUT.mkdir(exist_ok=True)
    SPLIT.write_text(json.dumps({
        "created": _now_iso(),
        "seed": SEED, "test_fraction": TEST_FRACTION, "gold_sha256": h,
        "n_items": len(items), "train": train, "test": test}, indent=1))
    print(f"→ {SPLIT}")
    summarise(items, train, test)
    print("\nTEST IS FROZEN. Tune prompts on train; report the test number once, at the end.")


if __name__ == "__main__":
    main()
