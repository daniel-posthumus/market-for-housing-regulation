#!/usr/bin/env python3
"""
plot_extraction_accuracy.py
---------------------------
Purpose : Chart how meeting-level extraction accuracy moved across the three hand-labelling
          rounds, separating what the rules scored on documents they had NEVER seen (the
          frozen, out-of-sample number) from what they score after that round's
          disagreements were used to fix them (in-sample).
Inputs  : the round-by-round figures recorded below, each produced by scoring the machine's
          recomputed output against date_gold.db and printed in the session log.
Outputs : output/planning_commission_project/extraction_accuracy.pdf (+ .png)
Author  : Dan Post
Created : 2026-09-03

Notes
-----
The honest series is the FROZEN one: 92.1% on round 2, 95.3% on round 3. Both were measured
before any rule saw the round in question, so the rise between them is evidence that fixes
made after round 2 generalised rather than being fitted to it.

The corrected series is not a generalisation claim. It shows how much of each round's error
was addressable once seen — the gap between the two lines on a given round is the share of
errors that turned out to be a named, fixable cause rather than irreducible noise.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                   # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "output" / "planning_commission_project"

# (round label, meetings, frozen out-of-sample %, % after that round's fixes)
ROUNDS = [
    ("Round 1\n81 meetings",  81, None, 94.0),
    ("Round 2\n21 meetings",  21, 92.1, 97.9),
    ("Round 3\n19 meetings",  19, 95.3, 96.5),
    ("Round 4\n14 meetings",  14, 96.0, 98.4),
]
# corpus-wide agreement after each round's fixes were applied
CORPUS = [94.0, 94.6, 95.3, 95.8]

# Error rate by era, on the whole gold set at the current state. The eras are split at 2002
# because that is where the archive changes format: 1998-2001 pages wrap their header
# labels mid-phrase ("STAFF" / "IN ATTENDANCE:", "THE" / "MEETING WAS CALLED TO ORDER"),
# which is what truncated the captures.
CUTS = [
    ("2002 onwards",            3.83, 116, 1044),
    ("1999\u20132001",              11.11,  11,   99),
    ("1999\u20132001\nexcluding staff", 5.68, 11, 88),
]


def main():
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(12.4, 4.8),
                                 gridspec_kw={"width_ratios": [1.45, 1]})

    # ── left: accuracy across rounds ──
    x = range(len(ROUNDS))
    frozen = [r[2] for r in ROUNDS]
    fixed = [r[3] for r in ROUNDS]
    ax.plot(x, fixed, "o-", color="#94a3b8", lw=1.8, ms=7,
            label="After that round's fixes (in-sample)")
    fx = [i for i, v in enumerate(frozen) if v is not None]
    ax.plot(fx, [frozen[i] for i in fx], "o-", color="#2563eb", lw=2.6, ms=9,
            label="Held out, rules frozen (out-of-sample)")
    ax.plot(x, CORPUS, "s--", color="#16a34a", lw=1.5, ms=6, alpha=.85,
            label="Whole gold corpus after that round")
    for i, v in enumerate(frozen):
        if v is not None:
            ax.annotate(f"{v:.1f}%", (i, v), textcoords="offset points", xytext=(0, -18),
                        ha="center", color="#2563eb", fontweight="bold", fontsize=10)
    for i, v in enumerate(fixed):
        ax.annotate(f"{v:.1f}%", (i, v), textcoords="offset points", xytext=(0, 10),
                    ha="center", color="#475569", fontsize=9)
    ax.set_xticks(list(x))
    ax.set_xticklabels([r[0] for r in ROUNDS], fontsize=9)
    ax.set_ylim(88, 101)
    ax.set_ylabel("Field values matching the hand labels (%)", fontsize=10)
    ax.set_title("Accuracy across hand-labelling rounds", fontsize=11.5, pad=10)
    ax.grid(axis="y", alpha=.25)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(fontsize=8.5, loc="lower right", frameon=False)

    # ── right: where the error actually is ──
    labels = [c[0] for c in CUTS]
    vals = [c[1] for c in CUTS]
    colors = ["#16a34a", "#dc2626", "#f59e0b"]
    bars = bx.bar(range(len(CUTS)), vals, color=colors, width=.62)
    for i, (b, c) in enumerate(zip(bars, CUTS)):
        bx.annotate(f"{c[1]:.1f}%", (b.get_x() + b.get_width() / 2, c[1]),
                    textcoords="offset points", xytext=(0, 4), ha="center",
                    fontweight="bold", fontsize=10)
        bx.annotate(f"{c[2]} mtgs\n{c[3]} values",
                    (b.get_x() + b.get_width() / 2, 0.4), ha="center", va="bottom",
                    fontsize=8, color="white")
    bx.set_xticks(range(len(CUTS)))
    bx.set_xticklabels(labels, fontsize=9)
    bx.set_ylabel("Field values disagreeing (%)", fontsize=10)
    bx.set_title("Where the remaining error is", fontsize=11.5, pad=10)
    bx.set_ylim(0, 13)
    bx.grid(axis="y", alpha=.25)
    bx.spines[["top", "right"]].set_visible(False)

    fig.text(0.5, -0.04,
             "Left: dates are not shown \u2014 100% correct in every round (1,621 of 1,621 items), "
             "never re-tuned.   "
             "Right: the early era's error is almost entirely one field.",
             ha="center", fontsize=8.5, color="#64748b")
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(OUT / f"extraction_accuracy.{ext}", bbox_inches="tight", dpi=200)
    print("wrote", OUT / "extraction_accuracy.pdf")


if __name__ == "__main__":
    main()
