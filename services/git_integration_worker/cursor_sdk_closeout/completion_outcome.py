"""Delivery-failure fallback body and run/completion outcome labels.

Tiny leaf used by the SDK route after assembly: formats the bus-rejected
closeout stub and maps ``degraded_reason`` / delivery-ok into outcome strings.
No package siblings.
"""

from __future__ import annotations


def format_delivery_fallback_body(
    *,
    status_code: int,
    sidecar_ref: str,
    result_bytes: int,
) -> str:
    return (
        f"status: delivery_failed\n"
        f"bus_status_code: {status_code}\n"
        f"result_bytes: {result_bytes}\n"
        f"sidecar: {sidecar_ref}\n\n"
        "Closeout turn rejected by agent-bus. Full Composer result in sidecar."
    )


def resolve_run_outcome_label(degraded_reason: str | None) -> str:
    return "degraded" if degraded_reason else "ok"


def resolve_completion_outcome(*, run_outcome: str, delivery_ok: bool) -> str:
    if not delivery_ok:
        return "delivery_failed"
    return run_outcome
