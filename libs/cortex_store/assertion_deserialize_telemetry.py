"""Telemetry when assertion rows fail Pydantic decode on entity read paths."""

from __future__ import annotations

from typing import Any

from .dispatch_ops._shared import record


def emit_assertion_deserialize_skipped(
    *,
    entity_id: str,
    assertion_id: object,
    reason: str,
) -> None:
    """Emit ``cortex.assertion.deserialize_skipped`` (API shape unchanged)."""
    record(
        "cortex.assertion.deserialize_skipped",
        entity_id=entity_id,
        assertion_id=assertion_id,
        reason=reason,
    )


def assertion_deserialize_skip_reason(exc: BaseException) -> str:
    """Stable reason label for telemetry (no raw assertion payloads)."""
    return type(exc).__name__


__all__ = [
    "assertion_deserialize_skip_reason",
    "emit_assertion_deserialize_skipped",
]
