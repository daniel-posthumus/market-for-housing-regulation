# Meeting-date boundary marker

A local web app for hand-marking **where each meeting starts** inside a minutes document
(`.html` or `.pdf`), to build the gold standard that validates the automatic date stage,
[`../assign_meeting_dates.py`](../assign_meeting_dates.py).

```bash
cd code/commission_minutes_processing/date_boundary_app
python app.py                 # http://127.0.0.1:5006
python app.py --score --csv   # gold vs pipeline, + date_gold_score.csv
```

## Why this exists

The archive bundles several meetings into one document — the 1998–2000 monthly
compilations hold four apiece — so a document is not a meeting, and the pipeline has to
*infer* where one meeting ends and the next begins. That inference is now a separate stage
from parsing, and this app is how it gets checked against a human.

## Marking

The sidebar's **one month / year** filter is the suggested workload: one typical month per
year, 1998–2026 — **97 documents**. Most are a single meeting (one click); the monthly
compilations take four.

- Lines that mention **any** date are highlighted. `n` / `N` jump between them.
- Click a line (or `m`) where a meeting **starts** — the header line carrying its date —
  and pick the date. The date on that line is offered first, in bold.
- Everything after a mark belongs to that meeting until the next mark.
- **Save & next** (`⌘/Ctrl+Enter`) marks the document done and opens the next one.

Marks live in `date_gold.db` (`boundaries`: one row per meeting start), independent of
`labels.db`.

## Kept honest on purpose

- The document queue is built from the **raw corpus on disk**, not from `labels.db`, so it
  can't inherit the parser's idea of what a document contains.
- The view shows **every** line with a date, not the subset the detector accepts — so the
  gold standard can catch what the detector *misses*, not just what it gets wrong.
- Nothing is pre-marked, and the pipeline's answer is never shown while you mark. It only
  appears afterwards, in **Score vs pipeline**.

## Scoring

`--score` (or the toolbar button) reports:

| metric | what it answers |
|---|---|
| `boundary_precision` / `boundary_recall` | did the detector find the same **set of dates** you marked? |
| `positional_precision` / `positional_recall` | did it find them in the same **places**? |
| `block_date_accuracy` | of the parsed items in those documents, what share carry the date your marks imply? |
| `detected_not_marked` | headers the detector found that you did not mark — check these, they are where it disagrees with you |

The positional pair matters because two meetings held on the same day share a date, so a
missed boundary between them is invisible to the set comparison. That is not hypothetical:
on 1998-01-15 the Commission held its regular session and then sat jointly with the
Redevelopment Agency Commission in Room 404, and only the positional check sees it.

Per-document rows and every mismatch go to `date_gold_score.csv`. Block-level scoring
covers documents that have items in `labels.db` (the 1998–2014 HTML era); modern PDFs are
scored at the boundary level, since their items are one-meeting-per-file and take their
date from the file name.

## Meeting-level labelling

Marking a boundary says *where a meeting starts*. The meeting itself has attributes that
every item heard at it shares — time of day, whether it was a regular, special, joint or
closed session, the room, who sat, who staffed it. Those belong at the meeting level, once,
joined to items on the date, rather than repeated on 23,000 rows.

`../meeting_headers.py` cuts the ±15 non-blank lines around each marked boundary and
pre-fills twelve meeting-level fields from that window. **`/meetings`** (the toolbar link)
is where you confirm them: window on the left with the marked line highlighted, form on the
right, `⌘/Ctrl+Enter` to save and advance.

```bash
python ../meeting_headers.py               # build/refresh + coverage report
python ../meeting_headers.py --show 2      # print windows with their pre-fill
python ../meeting_headers.py --export      # meetings_pilot.csv
```

The pilot is **81 meetings** — your 80 marked boundaries plus one the date stage found that
the marking missed, carried in with `origin='detected'` so the gold set stays honest.
Pre-fill coverage on those 81: meeting type 81, scheduled time 81, present 78, staff 76,
called-to-order 74, absent 38 (most of the rest simply record no absences).

Two notes on the window. It is measured in **non-blank** lines because the 1998 HTML pages
pad headers with dozens of blanks — ±15 raw lines there would catch a third of a header.
And its *leading* lines are the tail of the **previous** meeting, which is where that
meeting's adjournment time lives.

Re-running is safe: a meeting you have confirmed is never overwritten by a refresh.

## Files
- `app.py` — Flask server, queue builder, sample picker, and the scorer.
- `templates/index.html`, `static/app.js`, `static/style.css` — the UI.
- `../meeting_headers.py` — cuts header windows and pre-fills the meeting-level fields.
- `date_gold.db` — your marks and the `meetings` table (SQLite, git-ignored).
- `meetings_pilot.csv` — the meeting-level pilot export.
- `date_gold_score.csv` — per-document gold-vs-pipeline detail (written by `--score --csv`).
