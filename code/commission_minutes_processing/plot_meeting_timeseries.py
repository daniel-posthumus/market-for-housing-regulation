#!/usr/bin/env python3
"""
plot_meeting_timeseries.py
--------------------------
Purpose : Two meeting-level time series over the full extracted corpus — staff in
          attendance, and the commissioner absence rate — each drawn as the raw
          meeting-by-meeting series behind a one-year moving average, banded by inferred
          presidency and with the remote-hearing era shaded.
Inputs  : meetings_all.csv (produced by extract_all_meetings.py)
Outputs : output/planning_commission_project/meeting_timeseries.pdf (+ .png)
Author  : Dan Post
Created : 2026-09-04

Notes
-----
The moving average is over a one-year WINDOW OF TIME (+/-183 days around each meeting), not
a fixed number of meetings. The Commission's sitting frequency varies — 35 meetings in 2000
against 66 in 2007 — so an N-meeting window would cover a different span of calendar time in
different years and the smoothed line would not be comparable along its own axis.

The absence rate is absences / (present + absent) per meeting, i.e. the share of the seated
Commission that missed that sitting. Meetings with no roll call are excluded rather than
counted as zero, and so are the handful that record absences with nobody present — those
are a missed roll call, not a meeting nobody attended, and they would otherwise appear as
100% spikes.

Years the corpus barely covers are drawn as a GAP rather than interpolated across, so a
hole in the scrape is never read as a change in the Commission. `SPARSE_YEARS` is the list
of them and is currently empty: 2018 was the one such year — two documents against 27-44
for its neighbours — and it was refilled from the S3 packet prefix on 2026-09-04, taking it
to 42 meetings, mid-range for the panel. The mechanism stays because the rule does; if a
future year comes up thin, name it there rather than letting a line run through it.

Presidency terms are INFERRED from who called each meeting to order, not read from a roster
the project holds. A raw run of the `presiding` field breaks into 188 fragments because a
vice-chair takes an occasional meeting, so the series is first smoothed by a rolling mode
over +/-7 meetings and runs shorter than six meetings are absorbed into their neighbours.
The result is an approximation of the presidency calendar — good enough to ask whether
absence or staffing tracks who is in the chair, not a substitute for the real roster.
"""
from __future__ import annotations

import csv
import datetime as dt
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                   # noqa: E402
import matplotlib.dates as mdates                                 # noqa: E402

HERE = Path(__file__).resolve().parent
CSV = HERE / "meetings_all.csv"
OUT = HERE.parents[1] / "output" / "planning_commission_project"
WINDOW_DAYS = 183          # +/- half a year
SPARSE_YEARS: set[int] = set()   # years too thin to draw through — see the note above
MODE_WINDOW = 7            # +/- meetings, for smoothing the presiding series
MIN_TERM = 6               # runs shorter than this are absorbed into their neighbours

# Residual name inconsistencies in `presiding` that the corpus-wide reconciliation misses,
# plus the parse artefacts ("Vice" with no name, an OCR "At1"). Listed rather than silently
# dropped so the reader can see exactly what was folded.
PRESIDING_ALIAS = {"Anita Theoharis": "Theoharis", "Theohars": "Theoharis"}
PRESIDING_JUNK = {"Vice", "At1", "Chiang", "Getty", "Scott"}


def load():
    rows = []
    for r in csv.DictReader(CSV.open()):
        try:
            d = dt.date(*map(int, r["meeting_date"].split("-")))
        except Exception:
            continue
        present = [x for x in r["present"].split("; ") if x]
        absent = [x for x in r["absent"].split("; ") if x]
        staff = [x for x in r["staff"].split("; ") if x]
        pres = PRESIDING_ALIAS.get(r["presiding"], r["presiding"])
        rows.append({"date": d, "n_present": len(present), "n_absent": len(absent),
                     "n_staff": len(staff), "seated": len(present) + len(absent),
                     "presiding": "" if pres in PRESIDING_JUNK else pres,
                     "remote": "Remote" in r["location"]})
    rows.sort(key=lambda r: r["date"])
    return rows



def presidency_terms(rows):
    """Infer presidency terms from who called each meeting to order.

    The raw series is noisy — a vice-chair takes the odd meeting — so it is smoothed by a
    rolling mode before runs are cut, and short runs are absorbed rather than kept as
    one-meeting "terms".
    """
    seq = [r for r in rows if r["presiding"]]
    if not seq:
        return []
    names = [r["presiding"] for r in seq]
    smooth = []
    for i in range(len(names)):
        lo, hi = max(0, i - MODE_WINDOW), min(len(names), i + MODE_WINDOW + 1)
        smooth.append(Counter(names[lo:hi]).most_common(1)[0][0])

    terms = []
    for r, nm in zip(seq, smooth):
        if terms and terms[-1]["name"] == nm:
            terms[-1]["end"] = r["date"]
            terms[-1]["n"] += 1
        else:
            terms.append({"name": nm, "start": r["date"], "end": r["date"], "n": 1})

    # absorb short runs into whichever neighbour is longer
    merged = True
    while merged and len(terms) > 1:
        merged = False
        for i, t in enumerate(terms):
            if t["n"] >= MIN_TERM:
                continue
            left = terms[i - 1] if i > 0 else None
            right = terms[i + 1] if i + 1 < len(terms) else None
            keep = max([x for x in (left, right) if x], key=lambda x: x["n"])
            keep["start"] = min(keep["start"], t["start"])
            keep["end"] = max(keep["end"], t["end"])
            keep["n"] += t["n"]
            terms.pop(i)
            merged = True
            break
    # re-join neighbours that became the same person after absorbing
    out = []
    for t in terms:
        if out and out[-1]["name"] == t["name"]:
            out[-1]["end"] = t["end"]; out[-1]["n"] += t["n"]
        else:
            out.append(t)
    return out


def moving(dates, vals, window=WINDOW_DAYS):
    """One-year centred moving average, in calendar time."""
    out = []
    for i, d in enumerate(dates):
        lo, hi = d - dt.timedelta(days=window), d + dt.timedelta(days=window)
        w = [v for dd, v in zip(dates, vals) if lo <= dd <= hi]
        out.append(sum(w) / len(w) if w else None)
    return out


def split_gaps(dates, vals, years=SPARSE_YEARS):
    """Break the series at years the corpus barely covers, so no line is drawn across."""
    xs, ys = [], []
    seg_x, seg_y = [], []
    for d, v in zip(dates, vals):
        if d.year in years:
            if seg_x:
                xs.append(seg_x); ys.append(seg_y); seg_x, seg_y = [], []
            continue
        seg_x.append(d); seg_y.append(v)
    if seg_x:
        xs.append(seg_x); ys.append(seg_y)
    return xs, ys


def panel(ax, dates, vals, colour, ylabel, title, terms, remote, pct=False, label_terms=False):
    # presidency bands first, so the data sits on top of them
    for i, t in enumerate(terms):
        ax.axvspan(t["start"], t["end"], color="#0f172a", alpha=.04 if i % 2 else .085, lw=0)
    if remote:
        ax.axvspan(remote[0], remote[1], color="#7c3aed", alpha=.13, lw=0)

    for xs, ys in zip(*split_gaps(dates, vals)):
        ax.plot(xs, ys, lw=.7, color=colour, alpha=.22)
    for xs, ys in zip(*split_gaps(dates, moving(dates, vals))):
        ax.plot(xs, ys, lw=2.4, color=colour)

    ax.set_ylabel(ylabel, fontsize=10)
    ax.set_title(title, fontsize=11.5, pad=31 if label_terms else 8)
    ax.grid(axis="y", alpha=.22)
    ax.spines[["top", "right"]].set_visible(False)
    ax.xaxis.set_major_locator(mdates.YearLocator(4))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    if pct:
        ax.yaxis.set_major_formatter(lambda v, _p: f"{v*100:.0f}%")

    lo, hi = ax.get_ylim()
    if label_terms:
        # Stagger the labels over THREE rows, not two. Two was enough while 2018 was a
        # hole; refilling it resolved a Hillis term and pushed Diamond over the cut, and
        # at 16 labels a two-row stagger overprints "Olague" on its neighbours.
        shown = [t for t in terms if t["n"] >= 20]
        for i, t in enumerate(shown):
            mid = t["start"] + (t["end"] - t["start"]) / 2
            ax.annotate(t["name"], (mid, hi), xytext=(0, 3 + 11 * (i % 3)),
                        textcoords="offset points", ha="center", va="bottom",
                        fontsize=7.5, color="#334155")
    if remote:
        mid = remote[0] + (remote[1] - remote[0]) / 2
        ax.annotate("remote\nhearings", (mid, lo + (hi - lo) * .06), ha="center",
                    fontsize=7.5, color="#7c3aed")
    ax.set_ylim(lo, hi)


def main():
    rows = load()
    terms = presidency_terms(rows)
    rem = [r["date"] for r in rows if r["remote"]]
    remote = (min(rem), max(rem)) if rem else None

    st = [r for r in rows if r["n_staff"] > 0]
    # A meeting with absences but NO ONE recorded present is a missed roll call, not a
    # meeting nobody attended; a 100% absence rate is an extraction artefact, not a fact.
    ab = [r for r in rows if r["n_present"] > 0]

    fig, (ax, bx) = plt.subplots(2, 1, figsize=(10.4, 7.0), sharex=True)
    panel(ax, [r["date"] for r in st], [r["n_staff"] for r in st], "#2563eb",
          "Staff in attendance", "Staff in attendance per meeting",
          terms, remote, label_terms=True)
    panel(bx, [r["date"] for r in ab], [r["n_absent"] / r["seated"] for r in ab], "#dc2626",
          "Share of seated Commission absent", "Commissioner absence rate per meeting",
          terms, remote, pct=True)

    fig.text(0.5, 0.005,
             "Faint line: one meeting. Bold line: one-year centred moving average "
             f"(±{WINDOW_DAYS} days). Grey bands: inferred presidency terms, from who called "
             "each meeting to order. Purple: remote hearings.",
             ha="center", fontsize=8.5, color="#64748b")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    for ext in ("pdf", "png"):
        fig.savefig(OUT / f"meeting_timeseries.{ext}", bbox_inches="tight", dpi=200)
    print("wrote", OUT / "meeting_timeseries.pdf")

    print(f"\n{len(terms)} inferred presidency terms "
          f"(>= {MIN_TERM} meetings; remote era {remote[0]} to {remote[1]}):")
    print(f"  {'president':12s} {'from':>10s} {'to':>12s} {'mtgs':>5s} {'absence':>8s} {'staff':>6s}")
    for t in terms:
        a = [r["n_absent"] / r["seated"] for r in ab if t["start"] <= r["date"] <= t["end"]]
        sf = [r["n_staff"] for r in st if t["start"] <= r["date"] <= t["end"]]
        print(f"  {t['name']:12s} {t['start']} {t['end']} {t['n']:5d} "
              f"{sum(a)/len(a)*100 if a else float('nan'):7.1f}% "
              f"{sum(sf)/len(sf) if sf else float('nan'):6.1f}")

    inside = [r["n_absent"] / r["seated"] for r in ab if remote and remote[0] <= r["date"] <= remote[1]]
    outside = [r["n_absent"] / r["seated"] for r in ab if not (remote and remote[0] <= r["date"] <= remote[1])]
    print(f"\n  absence rate during remote hearings: {sum(inside)/len(inside)*100:.2f}% "
          f"(n={len(inside)})  vs in person {sum(outside)/len(outside)*100:.2f}% (n={len(outside)})")


if __name__ == "__main__":
    main()
