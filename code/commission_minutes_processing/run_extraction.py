#!/usr/bin/env python3
"""
run_extraction.py — corpus-wide structured extraction with periodic QA checks.

Replaces inference.py's single-file, stale-15-field demo. Walks every tagged file
(1998–present), turns each `<<Project>>` block into one schema-complete record, and
writes the dataset plus a quality report. Everything is schema-aligned through
extraction_common (the single source of truth), so output can never drift from the
labeling form / training target.

Engines (swap with one flag — the rest of the run is identical):
  heuristic : autoextract regex (free, instant; the v0 / accuracy floor)
  hf        : a local fine-tuned seq2seq model (T5) prompted with PROMPT_INSTRUCTION
  anthropic : Claude few-shot, schema-constrained (needs ANTHROPIC_API_KEY + anthropic)

Periodic checks ("is extraction working?") every --check-every blocks:
  • field coverage  — % of records with a non-empty value, per key field
  • distributions   — action / request_type value counts (catches a stuck engine)
  • accuracy-vs-gold — for blocks whose case # has a hand-label, field-level accuracy
    vs that label (only fields the gold fills are scored). NB: gold is the *un-reviewed*
    hand-labels until label_qa.py is applied, so low `action` accuracy partly reflects
    dirty gold (351 were 'other'), not the engine — trust the copy fields most.

Outputs (processed/):
  structured_data.jsonl     one record per block (+ provenance)
  extracted_results.csv     flat table (lists comma-joined)
  extraction_qa_report.md   final coverage + distributions + per-field gold accuracy

Usage:
  python run_extraction.py                              # heuristic, all years, fresh
  python run_extraction.py --years 2015-2026 --check-every 1000
  python run_extraction.py --engine hf --model <dir>    # a trained model
  python run_extraction.py --resume                     # skip files already written
"""
from __future__ import annotations
import argparse, csv, json, re, sys, time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "labeling_app"))
from extraction_common import (FIELDS, LIST_FIELDS, coerce_record, field_match,  # noqa: E402
                               is_empty, parse_obj, PROMPT_INSTRUCTION)
from autoextract import extract as heuristic_extract, CASE_RE                    # noqa: E402
from ingest import parse_meeting_date, BLOCK_RE                                  # noqa: E402
from paths import MEETING_MINUTES                                               # noqa: E402

TAG = MEETING_MINUTES / "tagged"
PROC = MEETING_MINUTES / "processed"
TRAIN_DIR = TAG / "training"
OUT_JSONL = PROC / "structured_data.jsonl"
OUT_CSV = PROC / "extracted_results.csv"
QA_MD = PROC / "extraction_qa_report.md"

# fields whose coverage is the clearest "did extraction work" signal
COVERAGE_KEYS = ["case_number", "request_type", "project_address", "assessor_block",
                 "type_district", "action", "ayes", "preliminary_recommendation"]
PROVENANCE = ["source_file", "year", "item_index", "block_header"]


# ─────────────────────────────── gold (for QA) ───────────────────────────────
def load_gold() -> dict[str, dict]:
    """case_number(upper, spaceless) → coerced hand-label, from the durable
    {year}_labeled.json. Used only to score extraction, never to alter it."""
    gold = {}
    for f in sorted(TRAIN_DIR.glob("*_labeled.json")):
        try:
            for r in json.load(f.open()):
                cn = str(r.get("case_number", "")).replace(" ", "").upper()
                if cn:
                    gold[cn] = coerce_record(r)
        except Exception as e:
            print(f"  ⚠ gold {f.name}: {e}")
    return gold


# ─────────────────────────────── engines ───────────────────────────────
def make_engine(name: str, model: str | None):
    if name == "heuristic":
        return lambda blk, md: heuristic_extract(blk, meeting_date=md)

    if name == "hf":
        import torch
        from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
        model_id = model or str(PROC / "minutes_extractor")
        tok = AutoTokenizer.from_pretrained(model_id)
        mdl = AutoModelForSeq2SeqLM.from_pretrained(model_id)
        dev = "mps" if torch.backends.mps.is_available() else "cpu"
        mdl.to(dev).eval()

        def run(blk, md):
            ids = tok(PROMPT_INSTRUCTION + blk, return_tensors="pt",
                      truncation=True, max_length=1024).to(dev)
            with torch.no_grad():
                gen = mdl.generate(**ids, max_new_tokens=1024, num_beams=1)
            obj = parse_obj(tok.decode(gen[0], skip_special_tokens=True)) or {}
            obj.setdefault("meeting_date", md)
            return coerce_record(obj)
        return run

    if name == "anthropic":
        import anthropic
        client = anthropic.Anthropic()
        mdl_id = model or "claude-haiku-4-5-20251001"

        def run(blk, md):
            msg = client.messages.create(
                model=mdl_id, max_tokens=1500,
                messages=[{"role": "user", "content": PROMPT_INSTRUCTION + blk +
                           f"\n\n(meeting_date is {md})"},
                          {"role": "assistant", "content": "{"}])
            obj = parse_obj("{" + msg.content[0].text) or {}
            obj.setdefault("meeting_date", md)
            return coerce_record(obj)
        return run

    raise ValueError(f"unknown engine {name}")


# ─────────────────────────────── QA accumulator ───────────────────────────────
class QA:
    def __init__(self, gold):
        self.gold = gold
        self.n = 0
        self.cov = Counter()
        self.actions = Counter()
        self.reqs = Counter()
        self.g_hit = Counter(); self.g_tot = Counter()
        self.g_matched = 0

    def update(self, rec):
        self.n += 1
        for k in COVERAGE_KEYS:
            if not is_empty(rec.get(k)):
                self.cov[k] += 1
        self.actions[rec.get("action") or "(empty)"] += 1
        self.reqs[rec.get("request_type") or "(empty)"] += 1
        cn = str(rec.get("case_number", "")).replace(" ", "").upper()
        g = self.gold.get(cn)
        if g:
            self.g_matched += 1
            for k in FIELDS:
                if is_empty(g.get(k)):
                    continue
                self.g_tot[k] += 1
                if field_match(rec, g, k):
                    self.g_hit[k] += 1

    def gold_acc(self):
        h, t = sum(self.g_hit.values()), sum(self.g_tot.values())
        return (h / t) if t else 0.0

    def snapshot(self) -> str:
        cov = " ".join(f"{k}={self.cov[k]/self.n:.0%}" for k in COVERAGE_KEYS[:5]) if self.n else ""
        ga = self.gold_acc()
        return (f"[{self.n}] cov: {cov} | gold-matched {self.g_matched} "
                f"field-acc {ga:.1%}")

    def report_md(self, engine, secs) -> str:
        L = [f"# Extraction QA — engine `{engine}`", "",
             f"Records: **{self.n}**  ·  runtime {secs:.0f}s  ·  "
             f"gold-matched blocks {self.g_matched}", "",
             "## Field coverage (% non-empty)", "", "| field | coverage |", "|---|---|"]
        for k in COVERAGE_KEYS:
            L.append(f"| {k} | {self.cov[k]/self.n:.1%} |" if self.n else f"| {k} | – |")
        L += ["", "## action distribution", "", "| value | n |", "|---|---|"]
        for v, c in self.actions.most_common():
            L.append(f"| {v} | {c} |")
        L += ["", "## request_type distribution", "", "| value | n |", "|---|---|"]
        for v, c in self.reqs.most_common():
            L.append(f"| {v} | {c} |")
        L += ["", f"## Accuracy vs gold ({self.g_matched} matched blocks)",
              f"*Gold = un-reviewed hand-labels; trust copy fields over enums until "
              f"label_qa.py is applied.* Overall field accuracy: **{self.gold_acc():.1%}**",
              "", "| field | acc | n |", "|---|---|---|"]
        for k in FIELDS:
            if self.g_tot[k]:
                L.append(f"| {k} | {self.g_hit[k]/self.g_tot[k]:.0%} | {self.g_tot[k]} |")
        return "\n".join(L) + "\n"


# ─────────────────────────────── main ───────────────────────────────
def year_dirs(years: set[int] | None):
    for d in sorted(p for p in TAG.iterdir() if p.is_dir() and p.name.isdigit()):
        if years is None or int(d.name) in years:
            yield int(d.name), d


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", choices=["heuristic", "hf", "anthropic"], default="heuristic")
    ap.add_argument("--model", default=None, help="model dir/id for hf/anthropic")
    ap.add_argument("--years", help="e.g. 2015-2026 or 1998,2014 (default: all)")
    ap.add_argument("--limit", type=int, default=0, help="cap #blocks (0 = all)")
    ap.add_argument("--check-every", type=int, default=1000, help="QA snapshot cadence")
    ap.add_argument("--resume", action="store_true", help="skip files already in output")
    args = ap.parse_args(argv)

    years = None
    if args.years:
        years = set()
        for part in args.years.split(","):
            if "-" in part:
                a, b = part.split("-"); years.update(range(int(a), int(b) + 1))
            else:
                years.add(int(part))

    PROC.mkdir(parents=True, exist_ok=True)
    done_files: set[str] = set()
    if args.resume and OUT_JSONL.exists():
        for line in OUT_JSONL.open():
            try:
                done_files.add(json.loads(line)["source_file"])
            except Exception:
                pass
        print(f"resume: {len(done_files)} source files already done")
    else:
        for p in (OUT_JSONL, OUT_CSV):                  # back up any stale output once
            if p.exists():
                p.rename(p.with_suffix(p.suffix + ".bak"))

    engine = make_engine(args.engine, args.model)
    gold = load_gold()
    qa = QA(gold)
    print(f"engine={args.engine}  gold labels={len(gold)}  "
          f"years={sorted(years) if years else 'all'}")

    t0 = time.time()
    mode = "a" if args.resume else "w"
    fout = OUT_JSONL.open(mode, encoding="utf-8")
    try:
        for year, ydir in year_dirs(years):
            for f in sorted(ydir.glob("*.txt")):
                rel = str(f.relative_to(MEETING_MINUTES))
                if rel in done_files:
                    continue
                text = f.read_text(encoding="utf-8", errors="replace")
                mdate = parse_meeting_date(f.name, year)
                for idx, blk in enumerate(b.strip() for b in BLOCK_RE.findall(text)):
                    if not blk:
                        continue
                    rec = coerce_record(engine(blk, mdate))
                    out = {"source_file": rel, "year": year, "item_index": idx,
                           "block_header": blk.splitlines()[0][:120], **rec}
                    fout.write(json.dumps(out, ensure_ascii=False) + "\n")
                    qa.update(rec)
                    if qa.n % args.check_every == 0:
                        print(qa.snapshot()); fout.flush()
                    if args.limit and qa.n >= args.limit:
                        raise StopIteration
    except StopIteration:
        pass
    finally:
        fout.close()

    secs = time.time() - t0
    print("\n" + qa.snapshot())
    QA_MD.write_text(qa.report_md(args.engine, secs), encoding="utf-8")
    write_csv()
    print(f"\n✓ extraction complete: {qa.n} records → {OUT_JSONL}")
    print(f"  QA report → {QA_MD}")


def write_csv():
    cols = PROVENANCE + FIELDS
    with OUT_JSONL.open() as fin, OUT_CSV.open("w", newline="", encoding="utf-8") as fout:
        w = csv.DictWriter(fout, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for line in fin:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            row = dict(rec)
            for k in LIST_FIELDS:
                if isinstance(row.get(k), list):
                    row[k] = ", ".join(str(x) for x in row[k])
            w.writerow(row)
    print(f"  CSV → {OUT_CSV}")


if __name__ == "__main__":
    main()
