"""Job-state observer client.

The Auto worker route is removed. ``fetch_job_state`` reports that removal.
"""

from __future__ import annotations

import time
from typing import Any

from .worker_http import (
    _DEFAULT_ATTEMPT_TIMEOUTS_S,
    _DEFAULT_BACKOFF_S,
    _DEFAULT_MAX_ATTEMPTS,
    _DEFAULT_TOTAL_BUDGET_S,
)


def _fetch_job_state_once(
    *,
    thread_id: str | None,
    job_id: str | None,
    include_terminal: bool,
    base_url: str | None,
    timeout_s: float,
) -> tuple[dict[str, Any], Exception | None]:
    """The Auto job-state route is removed."""
    del base_url, timeout_s
    return (
        {
            "ok": False,
            "found": False,
            "job": None,
            "reason": "auto_arm_removed",
            "thread_id": thread_id,
            "job_id": job_id,
            "include_terminal": include_terminal,
        },
        None,
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
