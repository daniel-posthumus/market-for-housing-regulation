Create a new Python script with the project's standard header and naming conventions.

## Instructions

1. Ask me for:
   - **Purpose**: What does this script do? (e.g., "descriptive stats for the rates data", "event study estimation")
   - **Pipeline stage**: Which directory should it live in? (`data_descr/code/`, `analysis/`, or a subdirectory)

2. Generate a filename using `snake_case` with a prefix that reflects the pipeline stage:
   - `descr_` for descriptive/data documentation scripts (go in `data_descr/code/`)
   - `pull_` for data download/ingestion scripts (go in `data_descr/code/`)
   - `analysis_` for analysis scripts (go in `analysis/`)
   - `build_` for scripts that compile outputs (e.g., LaTeX memos)
   - If the script belongs to a numbered sub-pipeline (like `bbg_vs_aterio/`), use a two-digit prefix: `01_`, `02_`, etc.

3. Create the file with this standard header:

```python
"""
<SCRIPT_NAME>.py
----------------
Purpose : <one-line description>
Inputs  : <key input files or data sources>
Outputs : <key output files, figures, tables>
Author  : Dan Post
Created : <YYYY-MM-DD>

Notes
-----
<any relevant context, or "None." if nothing to add>
"""

import pandas as pd
import numpy as np
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
RAW_DIR = Path(
    "/Users/danpost/Library/CloudStorage/"
    "GoogleDrive-danpost@stanford.edu/My Drive/ai-data-center-project"
)
REPO_DIR = Path(__file__).resolve().parent.parent  # adjust depth as needed
OUT_DIR = REPO_DIR / "<appropriate_output_path>"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Main ───────────────────────────────────────────────────────────────────

def main():
    pass


if __name__ == "__main__":
    main()
```

4. Adjust `REPO_DIR` parent depth and `OUT_DIR` path to match where the script lives in the directory tree.
5. Fill in the header fields based on the purpose I described.
6. Show me the created file path and header so I can confirm.
