#!/usr/bin/env python3
"""
util.py — shared HTTP + checksum helpers for the demand collectors.

Operating rules implemented here (brief §0):
  * polite User-Agent + >=1s spacing between requests to the same host,
  * retry with exponential backoff (max 3 attempts),
  * streaming downloads (never load multi-GB files into memory),
  * idempotent + resumable: an existing, non-empty file with the right size
    (and matching checksum when one is supplied) is NOT re-fetched.
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Optional

import requests

# Repo root = two levels up from this file (demand_estimation/util.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent


def resolve_census_key() -> Optional[str]:
    """
    Find the Census API key without ever hard-coding it. Resolution order:
      1. ``CENSUS_API_KEY`` environment variable,
      2. ``CENSUS_API_KEY.txt`` at the repo root (gitignored),
      3. ``demand_estimation/.census_api_key`` (gitignored).
    Returns the stripped key, or None if no source has one.
    """
    env = os.environ.get("CENSUS_API_KEY")
    if env and env.strip():
        return env.strip()
    for candidate in (
        _REPO_ROOT / "api_keys" / "CENSUS_API_KEY.txt",
        _REPO_ROOT / "CENSUS_API_KEY.txt",
        _REPO_ROOT / "demand_estimation" / ".census_api_key",
    ):
        if candidate.exists():
            text = candidate.read_text().strip()
            if text:
                return text
    return None


def resolve_data_gov_key() -> Optional[str]:
    """api.data.gov key (used by the FBI Crime Data Explorer). Env
    ``API_DATA_GOV_KEY`` then ``api_keys/api_data_gov.txt``."""
    env = os.environ.get("API_DATA_GOV_KEY")
    if env and env.strip():
        return env.strip()
    for candidate in (_REPO_ROOT / "api_keys" / "api_data_gov.txt",
                      _REPO_ROOT / "api_data_gov.txt"):
        if candidate.exists():
            text = candidate.read_text().strip()
            if text:
                return text
    return None


def resolve_511_key() -> Optional[str]:
    """
    Find the 511 Bay Area API key (same discipline as the Census key):
      1. ``BAY511_API_KEY`` or ``MTC_511_API_KEY`` environment variable,
      2. ``511_API_KEY.txt`` at the repo root or under ``demand_estimation/``.
    Returns the stripped key, or None if absent (the caller then degrades to
    ``needs_key`` — no scraping, no workaround).
    """
    for var in ("BAY511_API_KEY", "MTC_511_API_KEY"):
        v = os.environ.get(var)
        if v and v.strip():
            return v.strip()
    for candidate in (
        _REPO_ROOT / "api_keys" / "511_API_KEY.txt",
        _REPO_ROOT / "511_API_KEY.txt",
        _REPO_ROOT / "demand_estimation" / "511_API_KEY.txt",
    ):
        if candidate.exists():
            text = candidate.read_text().strip()
            if text:
                return text
    return None

USER_AGENT = (
    "market-for-housing-regulation academic research "
    "(Daniel Posthumus; danielposthumus335@gmail.com)"
)

# Minimum seconds between successive requests (politeness / rate-limit).
MIN_REQUEST_SPACING = 1.0
_last_request_at = 0.0


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def _throttle() -> None:
    global _last_request_at
    dt = time.time() - _last_request_at
    if dt < MIN_REQUEST_SPACING:
        time.sleep(MIN_REQUEST_SPACING - dt)
    _last_request_at = time.time()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    max_attempts: int = 3,
    timeout: int = 120,
    **kwargs,
) -> requests.Response:
    """HTTP request with polite spacing + exponential backoff on 5xx/network."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        _throttle()
        try:
            resp = session.request(method, url, timeout=timeout, **kwargs)
            if resp.status_code >= 500:
                raise requests.HTTPError(f"{resp.status_code} server error", response=resp)
            return resp
        except (requests.RequestException, requests.HTTPError) as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            time.sleep(2 ** attempt)  # 2s, 4s
    raise RuntimeError(f"request failed after {max_attempts} attempts: {url}") from last_exc


def download(
    session: requests.Session,
    url: str,
    dest: Path,
    *,
    expected_sha256: Optional[str] = None,
    timeout: int = 600,
) -> dict:
    """
    Stream ``url`` to ``dest`` idempotently.

    Returns a dict: {bytes, sha256, status} where status is one of
    ``downloaded`` | ``cached`` | ``verified`` (cached + checksum match).

    If the file already exists and is non-empty, it is not re-downloaded; when
    ``expected_sha256`` is given it is verified instead.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and dest.stat().st_size > 0:
        size = dest.stat().st_size
        if expected_sha256:
            actual = sha256_file(dest)
            if actual.lower() == expected_sha256.lower():
                return {"bytes": size, "sha256": actual, "status": "verified"}
            # checksum mismatch -> re-download below
        else:
            return {"bytes": size, "sha256": sha256_file(dest), "status": "cached"}

    tmp = dest.with_suffix(dest.suffix + ".part")
    h = hashlib.sha256()
    nbytes = 0
    resp = request_with_retry(session, "GET", url, stream=True, timeout=timeout)
    resp.raise_for_status()
    with open(tmp, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            if not chunk:
                continue
            fh.write(chunk)
            h.update(chunk)
            nbytes += len(chunk)
    digest = h.hexdigest()

    if expected_sha256 and digest.lower() != expected_sha256.lower():
        tmp.unlink(missing_ok=True)
        raise ValueError(
            f"checksum mismatch for {url}: got {digest}, expected {expected_sha256}"
        )

    tmp.replace(dest)
    return {"bytes": nbytes, "sha256": digest, "status": "downloaded"}


def get_json(session: requests.Session, url: str, **kwargs) -> dict:
    resp = request_with_retry(session, "GET", url, **kwargs)
    resp.raise_for_status()
    return resp.json()
