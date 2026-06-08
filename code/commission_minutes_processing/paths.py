#!/usr/bin/env python3
"""
paths.py — single place that resolves where the data lives.

The data corpus lives on Dropbox (out of the git repo). Every script imports
MEETING_MINUTES from here instead of hardcoding a path, so moving the data only
requires changing this file (or setting the MFHR_DATA_ROOT env var).

Override at runtime:  export MFHR_DATA_ROOT=/some/other/data
"""
from __future__ import annotations
import os
from pathlib import Path

# Default: the project's data folder on Dropbox (migrated off Google Drive 2026-06-08).
_DEFAULT_DATA_ROOT = (
    "/Users/danpost/Library/CloudStorage/Dropbox/"
    "market-for-housing-regulation/data"
)

DATA_ROOT = Path(os.environ.get("MFHR_DATA_ROOT", _DEFAULT_DATA_ROOT)).expanduser()
MEETING_MINUTES = DATA_ROOT / "meeting_minutes"
