"""WARN-level events for best-effort session-close enrichment paths."""

from __future__ import annotations

from .dispatch_ops._shared import record


def emit_session_close_depth_decode_fallback(
    *,
    session_id: str,
    error_type: str,
) -> None:
    """Idempotent re-close could not parse prior transcript attributes JSON."""
    record(
        "mcp.session.close.depth_decode.fallback",
        session_id=session_id,
        error_type=error_type,
    )


def emit_entity_get_access_log_failed(*, entity_id: str, agent: str | None) -> None:
    record(
        "cortex.entity_get.access_log.failed",
        entity_id=entity_id,
        agent=agent or "",
    )


def emit_entity_get_archives_to_lookup_failed(*, entity_id: str) -> None:
    record(
        "cortex.entity_get.archives_to_lookup.failed",
        entity_id=entity_id,
    )


def emit_session_close_debrief_failed(
    *,
    session_id: str,
    agent: str,
    stage: str,
    detail: str = "",
    status_code: int | None = None,
) -> None:
    payload: dict[str, object] = {
        "session_id": session_id,
        "agent": agent,
        "stage": stage,
        "detail": detail,
    }
    if status_code is not None:
        payload["status_code"] = status_code
    record("mcp.session.close.debrief.failed", **payload)


__all__ = [
    "emit_entity_get_access_log_failed",
    "emit_entity_get_archives_to_lookup_failed",
    "emit_session_close_debrief_failed",
    "emit_session_close_depth_decode_fallback",
]
