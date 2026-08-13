"""Degraded-enqueue shapes for ``agent_bus.request`` / ``hop``.

Owns the park envelope when Auto is unreachable or enqueue fails — not the
turn write. Callers (``request.py``, ``hop.py``) attach these onto an otherwise
successful send so the lane tag survives and the poller sees ``producer=none``.
"""

from __future__ import annotations

from typing import Any


def annotate_poll_hint_no_producer(poll_hint: dict[str, Any]) -> dict[str, Any]:
    """Mark a poll hint as having no Auto producer (degraded arm)."""
    return {**poll_hint, "producer": "none"}


def build_enqueue_failure(
    *,
    reason: str,
    attempts: int,
    error_class: str,
    elapsed_s: float,
) -> dict[str, Any]:
    """Park envelope for a failed liveness probe or enqueue POST."""
    return {
        "reason": reason,
        "attempts": max(0, int(attempts)),
        "error_class": error_class,
        "elapsed_s": max(0.0, float(elapsed_s)),
        "terminal_park": True,
    }


def error_class_from_liveness(liveness: dict[str, Any]) -> str:
    """Map a liveness-probe dict onto a stable error_class token for park."""
    if liveness.get("error_class"):
        return str(liveness["error_class"])
    reason = str(liveness.get("reason", ""))
    if reason == "no_live_handler":
        return "handler_dead"
    if reason == "liveness_http_error":
        status = liveness.get("status_code")
        if isinstance(status, int) and 500 <= status < 600:
            return "http_5xx"
        return "http_other"
    return "unknown"


def enqueue_failure_reason(enq: dict[str, Any]) -> str:
    """Prefer explicit reason, then worker handler_status, then a generic token."""
    if enq.get("reason"):
        return str(enq["reason"])
    enqueue_data = enq.get("enqueue") or {}
    if enqueue_data.get("handler_status"):
        return str(enqueue_data["handler_status"])
    return str(enq.get("handler_status", "no-auto-handler"))


def error_class_from_enqueue(enq: dict[str, Any]) -> str:
    """Map an enqueue-client failure onto a stable error_class token for park."""
    reason = str(enq.get("reason") or "")
    if reason == "enqueue_unreachable":
        return "enqueue_unreachable"
    enqueue_data = enq.get("enqueue") or {}
    worker_status = str(
        enqueue_data.get("handler_status") or enq.get("handler_status") or ""
    )
    if worker_status in {"no_live_auto_handler", "no-auto-handler"}:
        return "handler_dead"
    status = enq.get("status_code")
    if isinstance(status, int) and 500 <= status < 600:
        return "http_5xx"
    if isinstance(status, int):
        return "http_other"
    return "unknown"
