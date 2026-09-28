"""Attach claimed cursor-auto job rows to the admission snap via GIW job-state."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from claude_bundles.request_admission_census import (
    attach_claimed_job_census_rows,
    census_row_from_claimed_auto_job,
    claimed_auto_job_counts_for_census,
)

_PROBE_TIMEOUT_S = 3.0


def _worker_base_url() -> str:
    explicit = os.environ.get("GIT_INTEGRATION_WORKER_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    stargate = os.environ.get("STARGATE_URL", "").strip()
    if stargate:
        return stargate.rstrip("/")
    return "http://127.0.0.1:8091"


def fetch_claimed_job_observer(thread_id: str) -> dict[str, Any] | None:
    """Best-effort ``GET /cursor-auto/job-state`` for a supersede-candidate job."""
    tid = (thread_id or "").strip()
    if not tid:
        return None
    query = urllib.parse.urlencode({"thread_id": tid})
    url = f"{_worker_base_url()}/api/v1/git/cursor-auto/job-state?{query}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8")
    except (TimeoutError, urllib.error.URLError, OSError, urllib.error.HTTPError):
        return None
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(body, dict) or not body.get("found"):
        return None
    job = body.get("job")
    if not isinstance(job, dict):
        return None
    if not claimed_auto_job_counts_for_census(job):
        return None
    return job


def attach_claimed_job_from_worker(
    snap: dict[str, Any],
    thread_id: str,
) -> dict[str, Any]:
    """Union a live claimed Auto job on ``thread_id`` into the admission census."""
    observer = fetch_claimed_job_observer(thread_id)
    if observer is None:
        return snap
    row = census_row_from_claimed_auto_job(
        thread_id=str(observer.get("thread_id") or thread_id),
        job_id=str(observer.get("job_id") or ""),
        cse_registration_id=str(observer.get("cse_registration_id") or "") or None,
    )
    return attach_claimed_job_census_rows(snap, [row])


__all__ = [
    "attach_claimed_job_from_worker",
    "fetch_claimed_job_observer",
]
