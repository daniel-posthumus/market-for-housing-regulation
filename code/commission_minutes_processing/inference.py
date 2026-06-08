#!/usr/bin/env python3
"""
inference.py
-------------
Infer JSON on a *tagged* minutes file (<<Project Start>> / End>> markers).
"""

import json, re, torch
from pathlib import Path
from datetime import datetime
from transformers import T5Tokenizer, T5ForConditionalGeneration

# ───────────────────────────────
# 1) Helpers
# ───────────────────────────────
HEADER_DATE_RE = re.compile(
    r'(Monday|Tuesday|Wednesday|Thursday|Friday),?\s*'
    r'(January|February|March|April|May|June|July|August|'
    r'September|October|November|December)\s+\d{1,2},\s+\d{4}',
    re.I
)
PROJECT_RE = re.compile(r'<<Project Start>>(.*?)<<Project End>>', re.S)

PROMPT_INSTRUCTION = """You are a strict JSON extractor. Given a block of raw meeting text, extract and return exactly one valid double-quoted JSON object with the keys below (any order). 
Only output the JSON—no commentary. Missing fields → empty string "".

Required keys:
- case_number
- project_address
- lot_number
- assessor_block
- project_descr
- type_district
- type_district_descr
- speakers
- action
- modifications
- ayes
- noes
- absent
- vote
- action_name

Raw block:
"""

REQUIRED_KEYS = [
    "case_number","project_address","lot_number","assessor_block","project_descr",
    "type_district","type_district_descr","speakers","action","modifications",
    "ayes","noes","absent","vote","action_name"
]
_scalar_re = {k: re.compile(rf'"{k}"\s*:\s*"([^"]*?)"', re.I) for k in REQUIRED_KEYS}
_list_re   = {k: re.compile(rf'"{k}"\s*:\s*(\[[^\]]*?\])', re.I) for k in REQUIRED_KEYS}

def extract_json(pred: str):
    """
    Try to pull the 15 required fields out of `pred`.
    If `pred` is not valid JSON, we still fall back to regex extraction
    so you see something, but we also print a one-time debug message.
    """
    # ---------- DEBUG: show raw string + json.loads() result ----------
    try:
        _ = json.loads(pred)                      # parses? great
    except Exception as e:
        print("\n── RAW MODEL OUTPUT ────────────────────────")
        print(pred[:800])                         # clip for readability
        print("json.loads error →", e)
        print("─────────────────────────────────────────────\n")

    # ---------- field-by-field fallback extraction ----------
    out = {}
    for k in REQUIRED_KEYS:
        if (m := _list_re[k].search(pred)):       # JSON-looking list
            try:
                out[k] = json.loads(m.group(1))
            except json.JSONDecodeError:
                out[k] = []
        elif (m := _scalar_re[k].search(pred)):   # simple string
            out[k] = m.group(1).strip()
        else:
            out[k] = ""                           # default
    return out


def clean_block(val: str) -> str:
    # keep only the first 1–4 digits
    m = re.match(r"\s*(\d{1,4})\b", val)
    return m.group(1) if m else val.strip()

# ───────────────────────────────
# 2) Paths (single-file demo)
# ───────────────────────────────
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import MEETING_MINUTES
base_dir = MEETING_MINUTES
tag_dir  = base_dir / "tagged"

target   = tag_dir / "2004" / "October_14_2004.txt"   # adjust as needed
model_dir = base_dir / "processed" / "minutes_extractor"

out_path = base_dir / "processed" / "structured_data.jsonl"
err_log  = base_dir / "processed" / "extraction_errors.log"
out_path.parent.mkdir(parents=True, exist_ok=True)

# ───────────────────────────────
# 3) Load model
# ───────────────────────────────
device    = "cuda" if torch.cuda.is_available() else "cpu"
tokenizer = T5Tokenizer.from_pretrained(model_dir)
model     = T5ForConditionalGeneration.from_pretrained(model_dir).to(device).eval()

EOJ_TOKEN = "<extra_id_0>"         # ← the marker you trained with
eos_id    = tokenizer.convert_tokens_to_ids(EOJ_TOKEN)
ban_quote = [[tokenizer.convert_tokens_to_ids("'")]]    # ban single quote (optional)

# ───────────────────────────────
# 4) Read file & build prompts
# ───────────────────────────────
text = target.read_text(encoding="utf-8", errors="ignore")

date_match = HEADER_DATE_RE.search(text)
meeting_date = ""
if date_match:
    raw = date_match.group(0).title()
    raw = re.sub(r',\s*', ', ', raw)
    meeting_date = datetime.strptime(raw, "%A, %B %d, %Y")

projects = [m.group(1).strip() for m in PROJECT_RE.finditer(text)]
print(f"{target.name}: found {len(projects)} agenda blocks")

prompts = [PROMPT_INSTRUCTION + blk for blk in projects]
enc = tokenizer(prompts, padding=True, truncation=True,
                max_length=1024, return_tensors="pt").to(device)   # was 512 (truncated ~21% of blocks)

# ───────────────────────────────
# 5) Generate predictions
# ───────────────────────────────
outs = model.generate(
    **enc,
    max_new_tokens=1024,   # was 600; p90 completion ≈ 810 tokens
    num_beams=1,
    eos_token_id=eos_id,
    bad_words_ids=ban_quote,
    do_sample=False,
    repetition_penalty=1.1
)
preds = tokenizer.batch_decode(outs, skip_special_tokens=False)
preds = [p.split(EOJ_TOKEN)[0].strip() for p in preds]
if not preds or preds[0] == "":
    print("⚠ model returned only the terminator token for a block")

# ───────────────────────────────
# 6) Write results
# ───────────────────────────────
with open(out_path, "a", encoding="utf-8") as fout, \
     open(err_log, "a", encoding="utf-8") as ferr:
    for blk, pred in zip(projects, preds):
        data  = extract_json(pred)
        # clean assessor_block
        data["assessor_block"] = clean_block(data["assessor_block"])
        entry = {
            "source_file": target.name,
            "meeting_date": str(meeting_date),
            "block_header": blk.split("\n",1)[0][:120],
            **data
        }
        try:
            fout.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            ferr.write(f"{target.name} | {entry['block_header']} | {e}\n")

print("✓ Finished – JSON lines saved to", out_path)