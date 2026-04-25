# ruff: noqa: N802
"""Request snapshot event signals — before/after records for pipeline evaluation.

These events carry structured request/response payloads that the event service
routes to the `request_snapshots` table for pipeline quality evaluation.

The signal prefix `request.snapshot.*` is used by the event service to identify
snapshot events for special routing.

Signals:
    request.snapshot.received  — raw request as received by Stargate
    request.snapshot.routed    — request after routing/profile resolution
    request.snapshot.completed — response body for non-streaming requests
    request.snapshot.failed    — error details on failure
"""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory

REQUEST_SNAPSHOT_RECEIVED = "request.snapshot.received"
REQUEST_SNAPSHOT_ROUTED = "request.snapshot.routed"
REQUEST_SNAPSHOT_COMPLETED = "request.snapshot.completed"
REQUEST_SNAPSHOT_FAILED = "request.snapshot.failed"


@event_factory
def RequestSnapshotReceived(
    *,
    request_id: str,
    model_id: str,
    messages: list[dict[str, Any]],
    is_pipeline: bool,
) -> Event:
    """Snapshot the raw incoming request before routing."""
    return Event(
        signal=REQUEST_SNAPSHOT_RECEIVED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "messages": messages,
            "is_pipeline": is_pipeline,
            "phase": "received",
        },
    )


@event_factory
def RequestSnapshotRouted(
    *,
    request_id: str,
    model_id: str,
    gateway_id: str,
    profile_name: str | None = None,
) -> Event:
    """Snapshot the routing decision (model, gateway, profile)."""
    return Event(
        signal=REQUEST_SNAPSHOT_ROUTED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "profile_name": profile_name,
            "phase": "routed",
        },
    )


@event_factory
def RequestSnapshotCompleted(
    *,
    request_id: str,
    model_id: str,
    gateway_id: str,
    content: str,
    usage: dict[str, Any] | None = None,
    duration_s: float,
) -> Event:
    """Snapshot the completed response (non-streaming only).

    Note: `content` is truncated to 4096 characters before emission.
    """
    return Event(
        signal=REQUEST_SNAPSHOT_COMPLETED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "content": content[:4096],
            "usage": usage,
            "duration_s": duration_s,
            "phase": "completed",
        },
    )


@event_factory
def RequestSnapshotFailed(
    *,
    request_id: str,
    model_id: str,
    error: str,
    error_code: str | None = None,
    error_source: str | None = None,
    error_data: dict[str, Any] | None = None,
    caller_hint: dict[str, Any] | None = None,
) -> Event:
    """Snapshot a request failure.

    Mirrors the enriched fields on the lifecycle ``request.failed`` event so
    snapshot consumers (pipeline evaluator, agent diagnostics) see the same
    structured envelope and caller hint.

    Note: `error` is truncated to 2048 characters before emission.
    """
    return Event(
        signal=REQUEST_SNAPSHOT_FAILED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "error": error[:2048],
            "error_code": error_code,
            "error_source": error_source,
            "error_data": error_data,
            "caller_hint": caller_hint,
            "phase": "failed",
        },
    )
