"""HTTP client for the Cursor Auto worker — liveness probe + admit enqueue.

Split out of ``request.py`` (modularization: the module carried both the
request-shaping logic and its transport). Nothing here knows about bus turns;
callers own the turn write and the response shape.

Sender wire discipline (harvest-restart-propagation I3): new enqueue JSON fields
must be optional-with-default only — never rename or remove existing keys.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from .job_state_client import fetch_job_state
from .worker_http import (
    _DEFAULT_ATTEMPT_TIMEOUTS_S,
    _DEFAULT_BACKOFF_S,
    _DEFAULT_MAX_ATTEMPTS,
    _DEFAULT_TOTAL_BUDGET_S,
    _auto_url,
    _worker_base_url,
)

# Re-export for callers that imported URL helpers from this module.
__all__ = [
    "_auto_url",
    "_worker_base_url",
    "enqueue_auto_job",
    "fetch_job_state",
    "probe_auto_liveness",
]


def _classify_probe_error(
    result: dict[str, Any],
    exc: Exception | None,
) -> str:
    reason = str(result.get("reason", ""))
    if reason == "no_live_handler":
        return "handler_dead"
    if reason == "liveness_http_error":
        status = result.get("status_code")
        if isinstance(status, int) and 500 <= status < 600:
            return "http_5xx"
        return "http_other"
    if exc is not None:
        if isinstance(exc, httpx.ReadTimeout):
            return "read_timeout"
        if isinstance(exc, httpx.ConnectTimeout):
            return "connect_timeout"
        if isinstance(exc, httpx.ConnectError):
            return "connect_refused"
        if isinstance(exc, ValueError):
            return "parse_error"
    return "unknown"


def _probe_retryable(result: dict[str, Any]) -> bool:
    reason = str(result.get("reason", ""))
    if reason == "liveness_unreachable":
        return True
    if reason == "liveness_http_error":
        status = result.get("status_code")
        return isinstance(status, int) and 500 <= status < 600
    return False


def _probe_auto_liveness_once(
    *,
    base_url: str | None,
    timeout_s: float,
) -> tuple[dict[str, Any], Exception | None]:
    """Single HTTP GET to the worker liveness endpoint."""
    url = _auto_url("/liveness", base_url=base_url)
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.get(url)
        if resp.status_code != 200:
            return (
                {
                    "live": False,
                    "reason": "liveness_http_error",
                    "status_code": resp.status_code,
                },
                None,
            )
        data = resp.json()
        return (
            {
                "live": bool(data.get("live")),
                "liveness": data,
                "reason": "ok" if data.get("live") else "no_live_handler",
            },
            None,
        )
    except (httpx.HTTPError, ValueError, OSError) as exc:
        return (
            {
                "live": False,
                "reason": "liveness_unreachable",
                "error": str(exc),
            },
            exc,
        )


def probe_auto_liveness(
    *,
    base_url: str | None = None,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    attempt_timeouts_s: tuple[float, ...] = _DEFAULT_ATTEMPT_TIMEOUTS_S,
    backoff_s: tuple[float, ...] = _DEFAULT_BACKOFF_S,
    total_budget_s: float = _DEFAULT_TOTAL_BUDGET_S,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """Probe git_integration_worker Auto liveness (arm predicate).

    Retries transport-unknown failures (``liveness_unreachable`` and 5xx
    ``liveness_http_error``) with bounded backoff. Definitive HTTP-200
    ``live:false`` (``no_live_handler``) fast-fails with zero retries (F1).

    Default schedule: 3 attempts with nominal per-attempt timeouts 3s → 5s → 5s,
    backoff ~0.5s / ~1.5s between attempts, monotonic total probe budget ≤15s.
    Sleeps and per-attempt timeouts clamp to remaining budget. Callers whose
    own timeout is shorter than this ladder may observe probe exhaustion before
    the full nominal schedule completes.

    Returns ``{live: bool, reason, attempts, elapsed_s, error_class, ...}``.
    Transport exhaustion ⇒ ``live=False`` (never claim armed without enqueue).

    ``timeout_s`` is a legacy single-shot override (one attempt only) for callers
    that still pass the pre-retry parameter.
    """
    if timeout_s is not None:
        result, exc = _probe_auto_liveness_once(base_url=base_url, timeout_s=timeout_s)
        elapsed = 0.0
        return {
            **result,
            "attempts": 1,
            "elapsed_s": elapsed,
            "error_class": _classify_probe_error(result, exc),
        }

    budget_start = time.monotonic()
    deadline = budget_start + total_budget_s
    last_result: dict[str, Any] = {"live": False, "reason": "liveness_unreachable"}
    last_exc: Exception | None = None
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

        result, exc = _probe_auto_liveness_once(
            base_url=base_url,
            timeout_s=attempt_timeout,
        )
        last_result = result
        last_exc = exc

        if result.get("live") or result.get("reason") == "no_live_handler":
            elapsed = time.monotonic() - budget_start
            return {
                **result,
                "attempts": attempts_used,
                "elapsed_s": elapsed,
                "error_class": _classify_probe_error(result, exc),
            }

        if not _probe_retryable(result) or attempt_idx >= max_attempts - 1:
            break

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break

        nominal_backoff = backoff_s[min(attempt_idx, len(backoff_s) - 1)]
        time.sleep(min(nominal_backoff, remaining))

    elapsed = time.monotonic() - budget_start
    return {
        **last_result,
        "attempts": attempts_used,
        "elapsed_s": elapsed,
        "error_class": _classify_probe_error(last_result, last_exc),
    }


def enqueue_auto_job(
    *,
    thread_id: str,
    turn_number: int,
    subject: str,
    body: str,
    from_agent: str,
    to_agent: str,
    desired_model: str,
    desired_effort: str,
    contract: str,
    require_attended: bool = False,
    escalation: str | None = None,
    request_id: str | None = None,
    cse_chat_url: str | None = None,
    cse_registration_id: str | None = None,
    continuity_hop: bool = False,
    lane: str | None = None,
    base_url: str | None = None,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    """POST admit-on-request enqueue to the Auto worker.

    ``lane`` is optional GIW checkout isolation (``A``|``B``). I3: omit the
    JSON key when unset so older GIW receivers keep ``select_lane`` defaults.
    """
    url = _auto_url("/enqueue", base_url=base_url)
    payload = {
        "thread_id": str(thread_id),
        "turn_number": int(turn_number),
        "subject": subject,
        "body": body,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "desired_model": desired_model,
        "desired_effort": desired_effort,
        "contract": contract,
        "require_attended": bool(require_attended),
    }
    if escalation:
        payload["escalation"] = escalation
    if request_id:
        payload["request_id"] = request_id
    if cse_chat_url:
        payload["cse_chat_url"] = cse_chat_url
    if cse_registration_id:
        payload["cse_registration_id"] = cse_registration_id
    if continuity_hop:
        payload["continuity_hop"] = True
    if lane:
        payload["lane"] = lane
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(url, json=payload)
        data = resp.json() if resp.content else {}
        if resp.status_code == 200 and data.get("ok"):
            return {
                "ok": True,
                "handler_status": "auto-admit-armed",
                "enqueue": data,
            }
        return {
            "ok": False,
            "handler_status": data.get("handler_status", "no-auto-handler"),
            "enqueue": data,
            "status_code": resp.status_code,
        }
    except (httpx.HTTPError, ValueError, OSError) as exc:
        return {
            "ok": False,
            "handler_status": "no-auto-handler",
            "reason": "enqueue_unreachable",
            "error": str(exc),
        }
