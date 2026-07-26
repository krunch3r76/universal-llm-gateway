"""One-shot lease-snapshot fetch for cold-start reconcile (G5.1)."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

import httpx

_DEFAULT_URL = os.environ.get("GIT_INTEGRATION_WORKER_URL", "http://127.0.0.1:8091")
_PATH = "/api/v1/git/admin/lease-snapshot"
_MAX_ATTEMPTS = 3
_TIMEOUT_S = 5.0
_BACKOFF_S = 0.25


def worker_url(base: str | None = None) -> str:
    return (base or _DEFAULT_URL).rstrip("/")


def fetch_lease_snapshot(
    *,
    base_url: str | None = None,
    source_repo: str | None = None,
    get_json: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Perform exactly one GET lease-snapshot, with bounded seed-time retry."""
    url = worker_url(base_url)
    params = f"?source_repo={source_repo}" if source_repo else ""
    target = f"{url}{_PATH}{params}"

    if get_json is not None:
        try:
            body = get_json(target)
            return body if isinstance(body, dict) else None
        except Exception:
            return None

    last_error: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            with httpx.Client(timeout=_TIMEOUT_S) as client:
                response = client.get(target)
                response.raise_for_status()
                body = response.json()
                return body if isinstance(body, dict) else None
        except Exception as exc:  # noqa: BLE001 — seed degrade, not crash
            last_error = exc
            if attempt + 1 < _MAX_ATTEMPTS:
                time.sleep(_BACKOFF_S * (attempt + 1))
    _ = last_error
    return None
