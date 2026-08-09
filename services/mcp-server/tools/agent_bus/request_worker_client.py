"""HTTP client for the Cursor Auto worker — liveness probe + admit enqueue.

Split out of ``request.py`` (modularization: the module carried both the
request-shaping logic and its transport). Nothing here knows about bus turns;
callers own the turn write and the response shape.

Sender wire discipline (harvest-restart-propagation I3): new enqueue JSON fields
must be optional-with-default only — never rename or remove existing keys.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

_DEFAULT_WORKER_URL = "http://127.0.0.1:8091"
_AUTO_API_PREFIX = "/api/v1/git/cursor-auto"
_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_ATTEMPT_TIMEOUTS_S = (3.0, 5.0, 5.0)
_DEFAULT_BACKOFF_S = (0.5, 1.5)
_DEFAULT_TOTAL_BUDGET_S = 15.0


def _worker_base_url() -> str:
    """Resolve Auto worker base URL for mcp→host reachability.

    Prefer ``GIT_INTEGRATION_WORKER_URL`` when set. Otherwise, from the mcp
    container, use ``STARGATE_URL`` so enqueue/liveness ride the existing
    ``/api/v1/git/*`` host-side proxy (worker binds 127.0.0.1 — unreachable
    via host.docker.internal). Host-local callers fall back to loopback.
    """
    explicit = os.environ.get("GIT_INTEGRATION_WORKER_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    stargate = os.environ.get("STARGATE_URL", "").strip()
    if stargate:
        return stargate.rstrip("/")
    return _DEFAULT_WORKER_URL


def _auto_url(path: str, *, base_url: str | None = None) -> str:
    base = (base_url or _worker_base_url()).rstrip("/")
    suffix = path if path.startswith("/") else f"/{path}"
    return f"{base}{_AUTO_API_PREFIX}{suffix}"


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
    base_url: str | None = None,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    """POST admit-on-request enqueue to the Auto worker."""
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


def fetch_job_state(
    *,
    thread_id: str | None = None,
    job_id: str | None = None,
    include_terminal: bool = False,
    base_url: str | None = None,
    timeout_s: float = 3.0,
) -> dict[str, Any]:
    """GET keyed cursor-auto job observer view from the Auto worker.

    Soft-fails when the worker is unreachable so ``thread_get`` still returns
    bus metadata; callers treat a missing ``job`` as no live Auto job.
    """
    if not thread_id and not job_id:
        return {
            "ok": False,
            "found": False,
            "job": None,
            "reason": "missing_key",
        }
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
            return {
                "ok": False,
                "found": False,
                "job": None,
                "reason": "job_state_http_error",
                "status_code": resp.status_code,
            }
        return {
            "ok": bool(data.get("ok", True)),
            "found": bool(data.get("found")),
            "job": data.get("job"),
            "reason": "ok" if data.get("found") else "not_found",
        }
    except (httpx.HTTPError, ValueError, OSError) as exc:
        return {
            "ok": False,
            "found": False,
            "job": None,
            "reason": "job_state_unreachable",
            "error": str(exc),
        }


__all__ = [
    "_auto_url",
    "_worker_base_url",
    "enqueue_auto_job",
    "fetch_job_state",
    "probe_auto_liveness",
]
