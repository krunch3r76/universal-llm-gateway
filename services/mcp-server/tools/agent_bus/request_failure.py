"""Degraded-enqueue shapes for ``agent_bus.request`` / ``hop``.

Owns the park envelope when Auto is unreachable or enqueue fails — not the
turn write. Callers (``request.py``, ``hop.py``) attach these onto an otherwise
successful send so the lane tag survives and the poller sees ``producer=none``.

Also owns the ``job_admission`` projection for the paths that never reach the
admit ladder at all. GIW's ``cursor_auto.admission_verdict`` is the authority
for a real verdict; MCP cannot import it across the service boundary
(``[universal:mcp]``), and on these paths there is no verdict to relay — the
request never got far enough to create a job.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

#: Mirrors ``cursor_auto.admission_verdict.ADMISSION_AUTHORITY``.
_ADMISSION_AUTHORITY = "cursor_auto.admit_gates.blocking_admit_gate"
_ADMISSION_RECOVERY = "agent_bus_read(job_state)"


def build_job_admission_unreached(*, reason: str, scope: str) -> dict[str, Any]:
    """State positively that no job was admitted, because none was created.

    Used when liveness refused, the enqueue POST failed, or GIW answered
    without a projection. Never a substitute for a relayed verdict.
    """
    return {
        "outcome": "not_applicable",
        "reason": reason,
        "as_of": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source": _ADMISSION_AUTHORITY,
        "scope": scope,
        "coverage": {
            "asserted": [],
            "deferred": [],
            "not_applicable": ["admit_ladder_not_reached"],
            "waived": [],
        },
        "recovery": _ADMISSION_RECOVERY,
    }


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
    """Prefer explicit reason, then worker auto_handler_status, then a generic token."""
    if enq.get("reason"):
        return str(enq["reason"])
    enqueue_data = enq.get("enqueue") or {}
    if enqueue_data.get("auto_handler_status"):
        return str(enqueue_data["auto_handler_status"])
    return str(enq.get("auto_handler_status", "no-auto-handler"))


def error_class_from_enqueue(enq: dict[str, Any]) -> str:
    """Map an enqueue-client failure onto a stable error_class token for park."""
    reason = str(enq.get("reason") or "")
    if reason == "enqueue_unreachable":
        return "enqueue_unreachable"
    enqueue_data = enq.get("enqueue") or {}
    worker_status = str(
        enqueue_data.get("auto_handler_status") or enq.get("auto_handler_status") or ""
    )
    if worker_status in {"no_live_auto_handler", "no-auto-handler"}:
        return "handler_dead"
    status = enq.get("status_code")
    if isinstance(status, int) and 500 <= status < 600:
        return "http_5xx"
    if isinstance(status, int):
        return "http_other"
    return "unknown"
