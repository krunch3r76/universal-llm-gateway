"""Shared HTTP relay to internal services (UDS or Docker network).

Infrastructure helper used by proxy modules and the ``local_api`` MCP tool.
"""

from __future__ import annotations

import json as _json
import logging
import os
import threading
from concurrent.futures import TimeoutError as FuturesTimeoutError
from queue import Empty, Queue
from typing import Any

import httpx
from mcp_events import monotonic_now, record
from transport_utils import (
    DEFAULT_AGENT_BUS_URL,
    DEFAULT_CORTEX_URL,
    DEFAULT_EMAIL_BRIDGE_URL,
    make_sync_client,
)

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 30.0
_MAX_ORPHAN_WORKERS = 4
_ROUTE_TIMEOUTS: dict[tuple[str, str, str], float] = {
    ("email-bridge", "POST", "/ingest"): 120.0,
    ("email-bridge", "POST", "/pull"): 120.0,
}
# Parameterized routes (path embeds an id) matched by suffix. review_extract
# (POST /review/{message_id}/extract) runs the probate-eml-extract pipeline —
# two sonnet stages, pipeline options.timeout_seconds=180 — so the relay budget
# must exceed the pipeline budget or the client aborts a still-running extract.
# review_dismiss has no LLM stage and stays on the default budget.
_ROUTE_SUFFIX_TIMEOUTS: list[tuple[str, str, str, float]] = [
    ("email-bridge", "POST", "/extract", 200.0),
    # Handoff wait long-polls server-side up to MAX_WAIT_SECONDS (60); the relay
    # budget must exceed it or the client aborts a still-blocking wait.
    ("agent-bus", "GET", "/wait", 75.0),
]

_SERVICES: dict[str, dict[str, str]] = {
    "journal-bridge": {
        "url": "http://journal-bridge:8200",
        "token_env": "BRIDGE_TOKEN",
    },
    "agent-bus": {
        "url": DEFAULT_AGENT_BUS_URL,
        "token_env": "AGENT_BUS_TOKEN",
    },
    "cortex-api": {
        "url": DEFAULT_CORTEX_URL,
    },
    "email-bridge": {
        "url": DEFAULT_EMAIL_BRIDGE_URL,
    },
}

_orphan_workers: list[threading.Thread] = []
_orphan_lock = threading.Lock()
# Admission cap applies only to agent-bus long-poll wait routes where timed-out
# workers may remain alive until server-side wait completes; other relay traffic
# is not throttled by this reservation.
_orphan_slot_semaphore = threading.Semaphore(_MAX_ORPHAN_WORKERS)
# Per-orphan bookkeeping: worker -> (orphaned_at_monotonic, slot_token|None).
# A slot token is released exactly once (worker finally OR reclaim sweep),
# never both, so the semaphore permit count stays consistent.
_orphan_meta: dict[threading.Thread, tuple[float, _SlotToken | None]] = {}
# A worker whose httpx call neither completes nor raises (SF1, friction 23653)
# would hold its admission slot forever and, after _MAX_ORPHAN_WORKERS such
# leaks, wedge every /wait behind relay_capacity_exhausted until restart. Cap
# how long a slot may be held; past this, reclaim the permit (the leaked daemon
# thread is accepted, but the admission slot self-heals).
_ORPHAN_MAX_LIFETIME_S: float = float(
    os.getenv("MCP_RELAY_ORPHAN_MAX_LIFETIME_S", "300")
)


class RelayCapacityError(Exception):
    """Timed-out relay workers still alive and at the orphan cap."""


class _SlotToken:
    """Idempotent handle for one orphan-cap semaphore permit.

    Both the relay worker's ``finally`` and the reclaim sweep may try to release
    the same permit; ``release`` returns True only for the first caller so the
    permit is returned to the semaphore at most once.
    """

    __slots__ = ("_sem", "_released", "_lock")

    def __init__(self, sem: threading.Semaphore) -> None:
        self._sem = sem
        self._released = False
        self._lock = threading.Lock()

    def release(self) -> bool:
        with self._lock:
            if self._released:
                return False
            self._released = True
        self._sem.release()
        return True


def resolve_timeout(service: str, method: str, path: str) -> float:
    """Return the client budget for a local relay route.

    Exact (service, method, path) match wins; otherwise a suffix rule matches
    parameterized routes whose path embeds an id; otherwise the default budget.
    """
    method = method.upper()
    exact = _ROUTE_TIMEOUTS.get((service, method, path))
    if exact is not None:
        return exact
    path_no_query = path.split("?", 1)[0]
    for svc, mth, suffix, timeout in _ROUTE_SUFFIX_TIMEOUTS:
        if service == svc and method == mth and path_no_query.endswith(suffix):
            return timeout
    return _REQUEST_TIMEOUT


def _reset_orphan_workers_for_tests() -> None:
    """Clear timed-out worker registry and orphan-slot reservations (tests only)."""
    global _orphan_workers, _orphan_slot_semaphore, _orphan_meta
    with _orphan_lock:
        _orphan_workers = []
        _orphan_meta = {}
    _orphan_slot_semaphore = threading.Semaphore(_MAX_ORPHAN_WORKERS)


def _route_requires_orphan_cap(service: str, method: str, path: str) -> bool:
    """Return True for agent-bus GET long-poll wait routes under the orphan cap."""
    if service != "agent-bus" or method.upper() != "GET":
        return False
    return path.split("?", 1)[0].endswith("/wait")


def _reclaim_expired_orphans() -> None:
    """Drop dead orphan workers and reclaim slots held past the lifetime cap.

    Dead workers normally released their slot in ``_relay_worker``'s finally;
    we still call ``slot.release()`` (idempotent) so a finally-skipped death
    cannot leak a permit. A worker still alive past ``_ORPHAN_MAX_LIFETIME_S``
    is treated as permanently stuck: its admission slot is reclaimed so the
    /wait route recovers capacity.
    """
    now = monotonic_now()
    with _orphan_lock:
        survivors: list[threading.Thread] = []
        for worker in _orphan_workers:
            meta = _orphan_meta.get(worker)
            if meta is None:
                logger.warning(
                    "orphan worker missing meta bookkeeping; dropping %s",
                    worker.name,
                )
                continue
            orphaned_at, slot = meta
            if not worker.is_alive():
                # Idempotent: normally finally already released; belt-and-braces
                # if the worker died without running finally (Fable F4).
                if slot is not None:
                    slot.release()
                _orphan_meta.pop(worker, None)
                continue
            if now - orphaned_at >= _ORPHAN_MAX_LIFETIME_S:
                if slot is not None:
                    slot.release()
                _orphan_meta.pop(worker, None)
                continue
            survivors.append(worker)
        _orphan_workers[:] = survivors


def _acquire_orphan_slot() -> _SlotToken:
    _reclaim_expired_orphans()
    sem = _orphan_slot_semaphore
    if not sem.acquire(blocking=False):
        raise RelayCapacityError(
            f"relay orphan worker cap reached ({_MAX_ORPHAN_WORKERS})"
        )
    return _SlotToken(sem)


def _register_orphan_worker(worker: threading.Thread, slot: _SlotToken | None) -> None:
    with _orphan_lock:
        _orphan_workers.append(worker)
        _orphan_meta[worker] = (monotonic_now(), slot)


def _relay_worker(
    *,
    service_url: str,
    request_timeout: float,
    method: str,
    path: str,
    json_body: dict[str, Any] | None,
    headers: dict[str, str],
    result_queue: Queue[tuple[str, Any]],
    slot: _SlotToken | None,
) -> None:
    client: httpx.Client | None = None
    kind: str | None = None
    payload: Any = None
    try:
        client = make_sync_client(service_url, timeout=request_timeout)
        try:
            response = client.request(method, path, json=json_body, headers=headers)
            kind = "ok"
            payload = response
        except BaseException as exc:
            kind = "err"
            payload = exc
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                logger.warning(
                    "relay worker: client.close() failed for %s %s",
                    method,
                    path,
                    exc_info=True,
                )
        if kind is not None:
            result_queue.put((kind, payload))
        if slot is not None:
            slot.release()


def _request_with_wall_clock(
    *,
    service_url: str,
    request_timeout: float,
    method: str,
    path: str,
    json_body: dict[str, Any] | None,
    headers: dict[str, str],
    wall_clock_s: float,
    enforce_orphan_cap: bool = False,
) -> httpx.Response:
    """Run a blocking relay under a hard wall-clock ceiling.

    A daemon worker owns ``make_sync_client``, ``client.request``, and
    ``client.close``. The caller returns or raises at ``wall_clock_s`` without
    synchronously closing the client or joining a timed-out worker. Timed-out
    workers remain tracked until they exit; agent-bus long-poll wait routes
    reserve an orphan slot before worker start so concurrent timeouts cannot
    exceed ``_MAX_ORPHAN_WORKERS`` live workers.
    """
    slot = _acquire_orphan_slot() if enforce_orphan_cap else None

    result_queue: Queue[tuple[str, Any]] = Queue(maxsize=1)
    worker = threading.Thread(
        target=_relay_worker,
        kwargs={
            "service_url": service_url,
            "request_timeout": request_timeout,
            "method": method,
            "path": path,
            "json_body": json_body,
            "headers": headers,
            "result_queue": result_queue,
            "slot": slot,
        },
        daemon=True,
    )
    try:
        worker.start()
    except BaseException:
        # Acquire→start failure must not leak the admission permit (Fable F5):
        # the worker never runs finally, and the sweep never sees an unregistered
        # worker, so release here or the slot is gone until restart.
        if slot is not None:
            slot.release()
        raise

    try:
        kind, payload = result_queue.get(timeout=wall_clock_s)
    except Empty:
        _register_orphan_worker(worker, slot)
        raise FuturesTimeoutError(
            f"relay wall-clock budget exceeded ({wall_clock_s}s)"
        ) from None

    if kind == "err":
        raise payload
    return payload


def relay(
    service: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Forward an HTTP request to an internal service (UDS or Docker network).

    Returns:
        Parsed JSON response from the service, or ``{"error": "<message>"}``.
    """
    method = method.upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        return {"error": f"Unsupported HTTP method: {method!r}"}

    if isinstance(body, str):
        try:
            body = _json.loads(body)
        except (ValueError, TypeError):
            return {"error": f"body is a string but not valid JSON: {body[:200]}"}

    svc_config = _SERVICES.get(service)
    if svc_config is None:
        return {
            "error": (f"Unknown service: {service!r}. Available: {sorted(_SERVICES)}")
        }

    service_url = svc_config["url"]
    request_timeout = resolve_timeout(service, method, path)

    token_env = svc_config.get("token_env", "")
    bearer = token or (os.environ.get(token_env, "") if token_env else "")
    headers: dict[str, str] = {}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"

    def _record_failed(
        *,
        error: str,
        duration: float,
        status: int | None = None,
        detail: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "service": service,
            "method": method,
            "path": path,
            "error": error,
            "duration_s": round(duration, 3),
            "timeout_s": request_timeout,
            **({"status": status} if status is not None else {}),
            **({"detail": detail} if detail else {}),
        }
        record("mcp.local.api.failed", **payload)

    t0 = monotonic_now()
    record(
        "mcp.local.api.called",
        service=service,
        method=method,
        path=path,
        timeout_s=request_timeout,
    )

    try:
        try:
            response = _request_with_wall_clock(
                service_url=service_url,
                request_timeout=request_timeout,
                method=method,
                path=path,
                json_body=body,
                headers=headers,
                wall_clock_s=request_timeout,
                enforce_orphan_cap=_route_requires_orphan_cap(service, method, path),
            )
        except RelayCapacityError as exc:
            duration = monotonic_now() - t0
            _record_failed(
                error="relay_capacity_exhausted",
                duration=duration,
                detail=str(exc),
            )
            return {"error": "Local relay capacity exhausted; retry later"}
        except FuturesTimeoutError as exc:
            duration = monotonic_now() - t0
            _record_failed(
                error="wall_clock_timeout",
                duration=duration,
                detail=str(exc) or f"no twin within {request_timeout}s",
            )
            return {"error": f"Request to {service} timed out"}

        duration = monotonic_now() - t0

        if response.status_code >= 400:
            _record_failed(
                error="http_error",
                status=response.status_code,
                duration=duration,
            )
            err: dict[str, Any] = {
                "error": f"HTTP {response.status_code}",
                "status_code": response.status_code,
                "body": response.text,
            }
            # Surface structured FastAPI `detail` payloads (e.g. 413
            # body_too_large) so callers can discriminate on `reason`
            # without parsing the body string.
            try:
                parsed_err = response.json()
            except Exception:
                parsed_err = None
            if isinstance(parsed_err, dict):
                detail_value = parsed_err.get("detail")
                if detail_value is not None:
                    err["detail"] = detail_value
            return err

        try:
            parsed = response.json()
        except Exception as exc:
            logger.warning(
                "Failed to parse JSON response from %s %s %s: %s",
                service,
                method,
                path,
                exc,
                exc_info=True,
            )
            _record_failed(
                error="invalid_json",
                status=response.status_code,
                duration=duration,
                detail=str(exc),
            )
            return {
                "error": "Invalid JSON response",
                "detail": str(exc),
                "text": response.text,
            }

        record(
            "mcp.local.api.completed",
            service=service,
            method=method,
            path=path,
            status=response.status_code,
            duration_s=round(duration, 3),
            timeout_s=request_timeout,
        )
        return parsed

    except httpx.RequestError as exc:
        duration = monotonic_now() - t0
        if isinstance(exc, httpx.ConnectError):
            _record_failed(error="connect_error", duration=duration, detail=str(exc))
            return {"error": f"Connection failed to {service}"}
        if isinstance(exc, httpx.TimeoutException):
            _record_failed(error="timeout", duration=duration, detail=str(exc))
            return {"error": f"Request to {service} timed out"}
        _record_failed(error="request_error", duration=duration, detail=str(exc))
        return {"error": f"Request to {service} failed"}
    except Exception as exc:
        duration = monotonic_now() - t0
        logger.error("local_api relay to %s failed: %s", service, exc, exc_info=True)
        _record_failed(error="unexpected_error", duration=duration, detail=str(exc))
        return {"error": f"Relay to {service} failed: {exc}"}
