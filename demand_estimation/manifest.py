#!/usr/bin/env python3
"""
manifest.py — the fetch log (brief §0.3).

Every download / build is recorded as one row in ``demand/_manifest.csv`` with
columns: source, url, local_path, bytes, sha256, fetched_at, status.

Rows are keyed by (source, local_path): re-logging the same artifact updates
its row in place rather than appending duplicates, so the manifest stays a
clean current-state snapshot across resumable runs.

Status vocabulary:
  downloaded | cached | verified  -> data is present locally
  needs_key                       -> automatable but a credential is missing
  manual_required                 -> licensed/auth-walled; see _stubs/<name>
  partial                         -> some but not all of a source landed
  skipped | failed                -> see note in the report
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import demand_paths as dp

COLUMNS = ["source", "url", "local_path", "bytes", "sha256", "fetched_at", "status"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Manifest:
    """In-memory mirror of _manifest.csv, persisted on every record()."""

    def __init__(self, path: Path = dp.MANIFEST_CSV):
        self.path = Path(path)
        self.rows: dict[tuple[str, str], dict] = {}
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        with open(self.path, newline="") as fh:
            for row in csv.DictReader(fh):
                key = (row.get("source", ""), row.get("local_path", ""))
                self.rows[key] = {c: row.get(c, "") for c in COLUMNS}

    def _rel(self, local_path) -> str:
        """Store paths relative to DATA_ROOT so the manifest is portable."""
        if not local_path:
            return ""
        p = Path(local_path)
        try:
            return str(p.relative_to(dp.DATA_ROOT))
        except ValueError:
            return str(p)

    def record(
        self,
        source: str,
        *,
        url: str = "",
        local_path=None,
        bytes: int = 0,
        sha256: str = "",
        status: str = "",
        fetched_at: Optional[str] = None,
    ) -> None:
        rel = self._rel(local_path)
        key = (source, rel)
        self.rows[key] = {
            "source": source,
            "url": url,
            "local_path": rel,
            "bytes": str(bytes or ""),
            "sha256": sha256 or "",
            "fetched_at": fetched_at or _utc_now(),
            "status": status,
        }
        self.flush()

    def drop(self, source: str, local_path) -> bool:
        """Remove a row (keyed by source + path). Returns True if one was removed.
        Used to retire a stale placeholder row when a source is later resolved
        (e.g. the 511 needs_key placeholder once a key arrives and it downloads)."""
        key = (source, self._rel(local_path))
        if key in self.rows:
            del self.rows[key]
            self.flush()
            return True
        return False

    def flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # deterministic order: by source then path
        ordered = sorted(self.rows.values(), key=lambda r: (r["source"], r["local_path"]))
        tmp = self.path.with_suffix(".csv.tmp")
        with open(tmp, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(ordered)
        tmp.replace(self.path)

    # convenience queries used by build.py / the report --------------------
    def status_for(self, source: str) -> list[str]:
        return [r["status"] for (s, _), r in self.rows.items() if s == source]

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.rows.values():
            out[r["status"]] = out.get(r["status"], 0) + 1
        return out
