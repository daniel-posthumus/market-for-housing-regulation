#!/usr/bin/env python3
"""
queue_order.py — order the labeling work-queue so a top-down pass is both
year-representative and rare-class-first.

Two problems with the old `ORDER BY year, source_file, item_index LIMIT 5000`:
  1. it marched chronologically, so you ground through 1998 (already 40% of the
     labels — see processing_review.md) before ever reaching later years; and
  2. with 9,081 items > 5,000, the later years were unreachable from the list.

This module fixes both with one ordering. Each candidate item gets a *rarity
score* from how under-represented its request_type/action classes are in the
labels you already have, then items are interleaved round-robin by year. So the
top of the queue cycles 1998→…→2014, and within each year it surfaces the
item that fills the scarcest class first. Label top-down and you fill rare
classes evenly across the whole period — exactly the composition the extractor
needs (and the learning curve assumes).

Pure functions, no Flask/DB deps, so they're unit-testable (`python queue_order.py`).
"""
from __future__ import annotations
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from extraction_common import parse_obj   # noqa: E402

# Classes whose balance we care about (the binding constraints for the model).
PRIORITY_FIELDS = ("request_type", "action")
# "enough" examples of a class before it stops being prioritised.
DEFAULT_TARGET = 12


def _as_dict(data) -> dict:
    if isinstance(data, dict):
        return data
    if not data:
        return {}
    return parse_obj(data) or {}


def class_counts(confirmed, fields=PRIORITY_FIELDS) -> dict[str, Counter]:
    """Count class occurrences across the labels you already have (the gold-so-far).
    `confirmed` is an iterable of label data (dicts or JSON strings)."""
    counts = {f: Counter() for f in fields}
    for data in confirmed:
        d = _as_dict(data)
        for f in fields:
            v = str(d.get(f) or "").strip().lower()
            if v:
                counts[f][v] += 1
    return counts


def deficits(counts: dict[str, Counter], target: int = DEFAULT_TARGET) -> dict[str, dict]:
    """Per class, how many more labels we want: max(0, target - have)."""
    return {f: {cls: max(0, target - n) for cls, n in counts[f].items()}
            for f in counts}


def item_score(data, defs: dict[str, dict], target: int = DEFAULT_TARGET) -> tuple[float, str]:
    """Rarity score for a candidate item + the class driving it. A class never
    seen in the gold set has the maximum deficit (= target)."""
    d = _as_dict(data)
    best_score, best_cls = 0.0, ""
    for f in PRIORITY_FIELDS:
        v = str(d.get(f) or "").strip().lower()
        if not v:
            continue
        defi = defs.get(f, {}).get(v, target)   # unseen class → full target deficit
        if defi > best_score:
            best_score, best_cls = float(defi), f"{f}={v}"
    return best_score, best_cls


def prioritize(candidates, confirmed, *, target: int = DEFAULT_TARGET):
    """Order `candidates` (list of dicts each with at least 'year' and 'data')
    rare-class-first, interleaved round-robin by year. Returns a new list with
    'score' and 'rare_class' added to each item. Stable within (year, score)."""
    defs = deficits(class_counts(confirmed), target=target)
    enriched = []
    for c in candidates:
        s, cls = item_score(c.get("data"), defs, target=target)
        c = dict(c); c["score"], c["rare_class"] = s, cls
        enriched.append(c)

    buckets: dict = defaultdict(list)
    for i, c in enumerate(enriched):
        buckets[c.get("year")].append((i, c))          # carry idx for stable tiebreak
    for y in buckets:
        buckets[y].sort(key=lambda ic: (-ic[1]["score"], ic[0]))
    years = sorted(buckets, key=lambda y: (y is None, y))   # chronological cycle

    out = []
    while any(buckets[y] for y in years):
        for y in years:
            if buckets[y]:
                out.append(buckets[y].pop(0)[1])
    return out


# ───────────────────────────── self-test ─────────────────────────────
if __name__ == "__main__":
    confirmed = (
        [{"request_type": "conditional_use", "action": "approved"}] * 20 +
        [{"request_type": "discretionary_review", "action": "did_not_take_dr"}] * 15
    )
    cands = [
        {"id": 1, "year": 1998, "data": {"request_type": "conditional_use", "action": "approved"}},
        {"id": 2, "year": 1998, "data": {"request_type": "office_allocation", "action": "approved"}},
        {"id": 3, "year": 2014, "data": {"request_type": "variance", "action": "disapproved"}},
        {"id": 4, "year": 2005, "data": {"request_type": "conditional_use", "action": "approved"}},
    ]
    ordered = prioritize(cands, confirmed, target=12)
    print("order (id · year · score · rare_class):")
    for c in ordered:
        print(f"  {c['id']} · {c['year']} · {c['score']:.0f} · {c['rare_class']}")
    ids = [c["id"] for c in ordered]
    # rare classes (office_allocation/variance, fully unseen) must lead their years;
    # years must be interleaved (not all 1998 first).
    assert ids[0] in (2, 3), ids          # a rare item leads
    assert ids != [1, 2, 3, 4], "should not be input order"
    yrs = [c["year"] for c in ordered]
    assert yrs[0] != yrs[1], f"years should interleave, got {yrs}"
    print("✓ self-test passed")
