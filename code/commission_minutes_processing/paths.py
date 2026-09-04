#!/usr/bin/env python3
"""
paths.py — single place that resolves where the data lives.

The data corpus lives on Dropbox (out of the git repo). Every script imports
MEETING_MINUTES from here instead of hardcoding a path, so moving the data only
requires changing this file (or setting the MFHR_DATA_ROOT env var).

The meeting-minutes corpus is organized per locality so the pipeline can scale
to the whole Bay Area. Each locality is a self-contained subtree:

    meeting_minutes/<locality>/{raw,tagged,processed,meeting_level_data}

MEETING_MINUTES points at the *active* locality (San Francisco by default), so
all downstream code stays locality-agnostic. To process another locality, set
MFHR_LOCALITY (and create its subtree) — e.g. export MFHR_LOCALITY=oakland.

Override at runtime:
    export MFHR_DATA_ROOT=/some/other/data      # move the whole corpus
    export MFHR_LOCALITY=oakland                # switch active locality
"""
from __future__ import annotations
import os
from pathlib import Path

# Default: the project's data folder on Dropbox (migrated off Google Drive 2026-06-08).
_DEFAULT_DATA_ROOT = "~/Library/CloudStorage/Dropbox/mfhr_data"

DATA_ROOT = Path(os.environ.get("MFHR_DATA_ROOT", _DEFAULT_DATA_ROOT)).expanduser()

# Active locality (snake_case, used as a directory name). Default: San Francisco.
LOCALITY = os.environ.get("MFHR_LOCALITY", "san_francisco")

# Root holding every locality's corpus; MEETING_MINUTES is the active locality's.
MEETING_MINUTES_ROOT = DATA_ROOT / "meeting_minutes"
MEETING_MINUTES = MEETING_MINUTES_ROOT / LOCALITY
