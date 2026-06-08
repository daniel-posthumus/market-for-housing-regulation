# Environment & Reproducibility Notes

This documents how to reproduce both pipelines end-to-end, the system-level
dependencies beyond `pip`, and a few pre-existing issues you should know about.

## Quick start

```bash
# 1. Create the virtual environment with miniforge Python 3.12
/Users/danpost/miniforge3/bin/python3.12 -m venv .venv
source .venv/bin/activate

# 2. Install pinned dependencies
pip install --upgrade pip
pip install -r requirements.txt          # ranges (kept compatible with the code)
#   or, for an exact bit-for-bit env:
# pip install -r requirements.lock.txt    # frozen output of `pip freeze`

# 3. Smoke test
python -c "import pandas, geopandas, torch, transformers, bs4, pdfplumber, fredapi; print('ok')"
```

Verified on macOS (Apple Silicon), Python **3.12.10**, in this repo:
all 23 third-party imports succeed; `pandas 2.3.3`, `numpy 2.2.6`,
`transformers 4.57.6`, `datasets 2.21.0`, `torch 2.12.0`, `geopandas 1.1.3`.

## Why versions are pinned with upper bounds

The cleaning/ML code was written against an older API surface. The pins in
`requirements.txt` keep it runnable:

- **`pandas<3.0`** — `08a`, `20`, `21` call `DataFrame.applymap()`, which is
  **removed in pandas 3.0** (deprecated since 2.1). Pinned to the 2.x line.
- **`transformers<5.0`** / **`datasets<3.0`** — `train.py` and `inference.py`
  use the 4.x `Seq2SeqTrainer` / `Seq2SeqTrainingArguments` API
  (`evaluation_strategy=...`, etc.), which 5.x renames.
- **`numpy<2.3`** — keeps the geopandas + torch wheels on tested combinations.

`requirements.lock.txt` is the full `pip freeze` if you want an exact replica.

## System-level dependencies (not pip-installable)

### GDAL / GEOS / PROJ — for `geopandas` (`21_exploratory_graphics.py`)
In this environment geopandas installed cleanly via binary wheels
(`pyogrio`, `pyproj`, `shapely` bundle their own GDAL/GEOS/PROJ), so **no system
GDAL was required**. If wheels are unavailable on another machine:
```bash
brew install gdal geos proj
```

### R — for `rpy2` (`05a_state_trifectas.py` → `05b_state_trifectas.Rmd`)
`05a` uses `rpy2` to render the R Markdown scraper. Requires a working R
(`R 4.5.2` here) plus these R packages:
```r
install.packages(c("rmarkdown", "tidyverse", "rvest", "httr"))
```
Note: `rpy2` was built against a Homebrew R, not the CRAN R.framework, so it
falls back to **ABI mode** (a harmless `Error importing in API mode … Trying to
import in ABI mode.` message) and connects fine. To silence it, install the
matching R.framework or rebuild `rpy2` against your R.

### `pdftotext` (optional, used for ad-hoc PDF inspection)
`pdfplumber` (pip) covers the pipeline. The reports were produced with the
system `poppler` `pdftotext` for spot checks: `brew install poppler`.

## Pre-existing issues flagged (NOT changed — see processing_review.md)

1. **Exposed secret** — `code/cleaning_code/08_acs_pull.py` hard-codes a Census
   API key. Recommend moving it to a `.env` file and loading via `python-dotenv`
   (already in `requirements.txt`):
   ```python
   from dotenv import load_dotenv; load_dotenv()
   API_KEY = os.environ["CENSUS_API_KEY"]
   ```
   Then rotate the leaked key and add `.env` to `.gitignore`.
2. **Cross-project path** — `code/cleaning_code/07_llm_regulations.py` reads from
   a *separate* `~/SIEPR-HOUSING-POLICY` directory, not this repo. It will fail
   unless that project is also present. The LLM index inputs do exist here under
   `data/llm_regulatory_measurement/`; the script should be repointed there.
3. **Path bug** — `code/analysis_code/01_election_land_use_scatters.py` builds a
   malformed path (`f'{clean_data}master_county_level'`, missing `/` and `.csv`).

## Data & Git LFS

Large inputs (`*.dta`, several `*.csv`) are tracked with **Git LFS** per
`.gitattributes`; run `git lfs install` after cloning. The fine-tuned model dir
`data/meeting_minutes/processed/minutes_extractor/` is **gitignored** — recreate
it by running `train.py`. The `data/meeting_minutes/` corpus is ~18 GB.

## What was NOT run

The full pipelines were not executed end-to-end here (they need external API
keys — Census, FRED, Redivis — and GPU time for training). Reproducibility was
validated at the import/API level and via the hardened scraper's dry-run.
