"""Keyed cursor-auto job-state HTTP client (observer view).

Split from ``request_worker_client`` so the retry ladder for job_state can
grow without pushing the liveness/enqueue module over the SLOC red line.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from .worker_http import (
    _DEFAULT_ATTEMPT_TIMEOUTS_S,
    _DEFAULT_BACKOFF_S,
    _DEFAULT_MAX_ATTEMPTS,
    _DEFAULT_TOTAL_BUDGET_S,
    _auto_url,
)


def _fetch_job_state_once(
    *,
    thread_id: str | None,
    job_id: str | None,
    include_terminal: bool,
    base_url: str | None,
    timeout_s: float,
) -> tuple[dict[str, Any], Exception | None]:
    """Single HTTP GET to the worker job-state endpoint."""
    params: list[str] = []
    if job_id:
        params.append(f"job_id={job_id}")
    if thread_id:
        params.append(f"thread_id={thread_id}")
    if include_terminal:
        params.append("include_terminal=true")
    qs = "&".join(params)
    url = _auto_url(f"/job-state?{qs}", base_url=base_url)
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.get(url)
        data = resp.json() if resp.content else {}
        if resp.status_code != 200:
            return (
                {
                    "ok": False,
                    "found": False,
                    "job": None,
                    "reason": "job_state_http_error",
                    "status_code": resp.status_code,
                },
                None,
            )
        return (
            {
                "ok": bool(data.get("ok", True)),
                "found": bool(data.get("found")),
                "job": data.get("job"),
                "reason": "ok" if data.get("found") else "not_found",
            },
            None,
        )
    except (httpx.HTTPError, ValueError, OSError) as exc:
        return (
            {
                "ok": False,
                "found": False,
                "job": None,
                "reason": "job_state_unreachable",
                "error": str(exc),
            },
            exc,
        )


def _job_state_retryable(result: dict[str, Any]) -> bool:
    """Transport-unknown failures only — definitive not_found does not retry."""
    reason = str(result.get("reason", ""))
    if reason == "job_state_unreachable":
        return True
    if reason == "job_state_http_error":
        status = result.get("status_code")
        return isinstance(status, int) and 500 <= status < 600
    return False


def fetch_job_state(
    *,
    thread_id: str | None = None,
    job_id: str | None = None,
    include_terminal: bool = False,
    base_url: str | None = None,
    timeout_s: float | None = None,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    attempt_timeouts_s: tuple[float, ...] = _DEFAULT_ATTEMPT_TIMEOUTS_S,
    backoff_s: tuple[float, ...] = _DEFAULT_BACKOFF_S,
    total_budget_s: float = _DEFAULT_TOTAL_BUDGET_S,
) -> dict[str, Any]:
    """GET keyed cursor-auto job observer view from the Auto worker.

    Soft-fails when the worker is unreachable so ``thread_get`` still returns
    bus metadata; callers treat a missing ``job`` as no live Auto job.

    Retries transport-unknown failures (``job_state_unreachable`` and 5xx
    ``job_state_http_error``) with the same bounded ladder as
    ``probe_auto_liveness`` (3 attempts, ≤15s). Definitive HTTP-200
    ``found:false`` / ``missing_key`` fast-fail with zero retries.

    ``timeout_s`` is a legacy single-shot override (one attempt only) for
    callers that still pass the pre-retry parameter.
    """
    if not thread_id and not job_id:
        return {
            "ok": False,
            "found": False,
            "job": None,
            "reason": "missing_key",
        }

    if timeout_s is not None:
        result, _exc = _fetch_job_state_once(
            thread_id=thread_id,
            job_id=job_id,
            include_terminal=include_terminal,
            base_url=base_url,
            timeout_s=timeout_s,
        )
        return {**result, "attempts": 1, "elapsed_s": 0.0}

    budget_start = time.monotonic()
    deadline = budget_start + total_budget_s
    last_result: dict[str, Any] = {
        "ok": False,
        "found": False,
        "job": None,
        "reason": "job_state_unreachable",
    }
    attempts_used = 0

    for attempt_idx in range(max_attempts):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break

        nominal_timeout = attempt_timeouts_s[
            min(attempt_idx, len(attempt_timeouts_s) - 1)
        ]
        attempt_timeout = min(nominal_timeout, remaining)
        attempts_used += 1

        result, _exc = _fetch_job_state_once(
            thread_id=thread_id,
            job_id=job_id,
            include_terminal=include_terminal,
            base_url=base_url,
            timeout_s=attempt_timeout,
        )
        last_result = result

        if result.get("reason") in ("ok", "not_found"):
            elapsed = time.monotonic() - budget_start
            return {**result, "attempts": attempts_used, "elapsed_s": elapsed}

        if not _job_state_retryable(result) or attempt_idx >= max_attempts - 1:
            break

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break

        nominal_backoff = backoff_s[min(attempt_idx, len(backoff_s) - 1)]
        time.sleep(min(nominal_backoff, remaining))

    elapsed = time.monotonic() - budget_start
    return {**last_result, "attempts": attempts_used, "elapsed_s": elapsed}


__all__ = ["fetch_job_state"]
