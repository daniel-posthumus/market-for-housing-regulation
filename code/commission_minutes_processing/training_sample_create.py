#!/usr/bin/env python3
"""
training_sample_create.py – build consolidated training.txt from per-year labels & raw minutes

Layout (all in tagged/training/):
  tagged/
    training/
      1996_labeled.json             # JSON array of ~10–15 labeled cases
      1996_sample.txt               # or .rtf — may have multiple files per year:
      1996_sample_part2.rtf         #   1996_sample_*.txt/.rtf are all included
      1997_labeled.json
      1997_sample.rtf
      ...
      training.txt                  # OUTPUT: JSONL for train.py
      logs/
        diagnostics_1996.json
        diagnostics_1997.json
        summary.json
"""

from striprtf.striprtf import rtf_to_text
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extraction_common import FIELDS, coerce_record
from autoextract import derive_request_type

# ───────────────────────── paths ─────────────────────────
from paths import MEETING_MINUTES
base      = MEETING_MINUTES
train_dir = base / "tagged" / "training"
train_dir.mkdir(parents=True, exist_ok=True)

logdir    = train_dir / "logs"
logdir.mkdir(parents=True, exist_ok=True)

outfile   = train_dir / "training.txt"      # consolidated JSONL

# ───────────────────────── constants ─────────────────────
EOJ = "<extra_id_0>"
BLOCK_RE = re.compile(r"<<Project Start>>(.*?)<<Project End>>", re.S)

# Flexible case-number: 2 or 4 digits before dot; ≥3 digits after; optional suffix
CASE_RE  = re.compile(r"\b((?:\d{2}|\d{4})\.\d{3,}(?:[A-Z0-9/]+)?)\b")

YEAR_LABEL_RE = re.compile(r"^(?P<year>\d{4})_labeled\.json$", re.I)

# The full set of keys is now defined by the shared SCHEMA (extraction_common.FIELDS).
REQUIRED = FIELDS

# Obvious orthographic typos / capitalisation variants -> canonical key.
# These are spelling errors only, NOT semantic merges (e.g. zoning_district vs
# type_district, or nayes vs noes, are deliberately left alone). normalise_keys()
# below applies this map so the builder self-heals; the same map is used by the
# one-off cleanup that rewrote the *_labeled.json source files.
KEY_ALIASES = {
    "aciton": "action", "Action": "action",
    "caes_number": "case_number", "case_+number": "case_number",
    "case number": "case_number", "case_numer": "case_number",
    "spekaers": "speakers", "Speakers": "speakers",
    "speaker_statemetns": "speaker_statements",
    "tyope_district": "type_district", "tpye_district": "type_district",
    "type_disrict": "type_district", "type_distrct": "type_district",
    "type_district_": "type_district", "tpe_district": "type_district",
    "prjoect_address": "project_address",
    "porject_descr": "project_descr", "project descr": "project_descr",
    "project-descr": "project_descr",
    "asessor_block": "assessor_block",
    "heigh_and_bulk_district": "height_and_bulk_district",
    "preliminary_recmomendation": "preliminary_recommendation",
    "preliminary_recommendaiton": "preliminary_recommendation",
    "preliminar_recommendation": "preliminary_recommendation",
    "prleiminary_recommendation": "preliminary_recommendation",
    "special_ues_district": "special_use_district",
    "zoninig_district": "zoning_district",
    "aayes": "ayes",
    "project_category_": "project_category",
    # schema renames (old hand-label keys -> new canonical schema keys)
    "action_name": "resolution_or_motion_no",
    "zoning_district": "type_district",
    "zoning_district_descr": "type_district_descr",
    "district_type": "type_district",
    "district_type_descr": "type_district_descr",
    "address": "project_address",
    "nayes": "noes",
}

def normalise_keys(rec: dict) -> dict:
    """Strip whitespace from keys and map obvious typos to canonical names.
    On collision (canonical already present), prefer a non-empty existing value
    and drop the typo'd duplicate."""
    out = {}
    for k, v in rec.items():
        ck = KEY_ALIASES.get(k.strip(), k.strip())
        if ck in out:
            # keep whichever is non-empty; existing wins ties
            if not out[ck] and v:
                out[ck] = v
        else:
            out[ck] = v
    return out

def normalise_case(code: str) -> str:
    return code.replace(" ", "").upper() if code else ""

def read_plain_text(p: Path) -> str:
    if p.suffix.lower() == ".rtf":
        return rtf_to_text(p.read_text(encoding="utf-8", errors="ignore"))
    # default: treat as plain text (.txt, etc.)
    return p.read_text(encoding="utf-8", errors="ignore")

def collect_years() -> list[int]:
    years = []
    for fp in train_dir.glob("*_labeled.json"):
        m = YEAR_LABEL_RE.match(fp.name)
        if not m:
            continue
        y = int(m.group("year"))
        # require at least one sample file for the same year (.txt or .rtf)
        has_sample = any(
            train_dir.glob(f"{y}_sample*.{ext}")
            for ext in ("txt", "rtf")
        )
        if has_sample:
            years.append(y)
    return sorted(set(years))

def load_year_labels(year: int) -> list[dict]:
    f = train_dir / f"{year}_labeled.json"
    with f.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
        if not isinstance(data, list):
            raise ValueError(f"{f.name} must be a JSON array")
        return data

def load_year_blocks(year: int) -> list[str]:
    """
    Read the year's sample file(s) (TXT/RTF) and extract project blocks.
    Accepts multiple files matching {year}_sample*.txt/.rtf.
    """
    texts = []
    for fp in sorted(list(train_dir.glob(f"{year}_sample*.txt")) + list(train_dir.glob(f"{year}_sample*.rtf"))):
        try:
            texts.append(read_plain_text(fp))
        except Exception as e:
            print(f"⚠ Skipping {fp.name}: {e}")
    if not texts:
        return []
    plain = "\n\n".join(texts)
    return [b.strip() for b in BLOCK_RE.findall(plain)]

def make_block_map(blocks: list[str]) -> dict[str, str]:
    """Map case_number → block. If duplicates, keep the longest block."""
    m = {}
    for blk in blocks:
        mm = CASE_RE.search(blk)
        code = normalise_case(mm.group(1) if mm else None)
        if not code:
            continue
        prev = m.get(code)
        if prev is None or len(blk) > len(prev):
            m[code] = blk
    return m

def ensure_required_fields(lbl: dict) -> dict:
    lab = normalise_keys(lbl)            # canonicalise typo'd / renamed keys first
    rec = coerce_record(lab)             # schema-complete, correctly typed
    if not rec.get("request_type"):      # cheap derivation from the case suffix
        rec["request_type"] = derive_request_type(rec.get("case_number", ""))
    return rec

def build_examples_for_year(year: int) -> tuple[list[dict], dict]:
    labels = load_year_labels(year)
    blocks = load_year_blocks(year)
    block_map = make_block_map(blocks)

    examples = []
    missing = []
    unmatched_blocks = set(block_map.keys())

    for lab in labels:
        lab_norm = ensure_required_fields(lab)   # canonicalise keys + fill required
        code = normalise_case(lab_norm.get("case_number"))
        raw = block_map.get(code)
        if raw is None:
            missing.append(code or "<missing case_number>")
            continue
        unmatched_blocks.discard(code)

        comp = json.dumps(lab_norm, ensure_ascii=False) + f" {EOJ}"
        examples.append({"prompt": raw + "\n\n", "completion": comp})

    stats = {
        "year": year,
        "labels": len(labels),
        "blocks_found": len(blocks),
        "paired": len(examples),
        "labels_without_block": len(missing),
        "unmatched_blocks": len(unmatched_blocks)
    }
    # write diagnostics
    diag = {
        "missing_label_case_numbers": missing,
        "unmatched_block_case_numbers": sorted(unmatched_blocks)
    }
    (logdir / f"diagnostics_{year}.json").write_text(
        json.dumps({"stats": stats, "details": diag}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"[{year}] labels={stats['labels']} blocks={stats['blocks_found']} "
          f"paired={stats['paired']} missing_labels={stats['labels_without_block']} "
          f"unmatched_blocks={stats['unmatched_blocks']}")
    return examples, stats

def main():
    years = collect_years()
    if not years:
        print("No years found: expected files like '1998_labeled.json' and '1998_sample*.rtf/txt' in tagged/training/.")
        return

    all_examples = []
    all_stats = []
    for y in years:
        ex, st = build_examples_for_year(y)
        all_examples.extend(ex)
        all_stats.append(st)

    # write consolidated training JSONL
    with outfile.open("w", encoding="utf-8") as fout:
        for ex in all_examples:
            fout.write(json.dumps(ex, ensure_ascii=False) + "\n")

    print(f"✓ Wrote {len(all_examples)} examples to {outfile}")
    # summary stats
    (logdir / "summary.json").write_text(
        json.dumps({"years": all_stats, "total_examples": len(all_examples)}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

if __name__ == "__main__":
    main()
