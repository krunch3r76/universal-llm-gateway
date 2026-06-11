"""Event factories for cursor-sdk worker lifecycle signals."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

logger = get_logger(__name__)

_uds_publisher: Callable[[str, dict[str, Any]], None] | None = None


def register_cursor_sdk_event_publisher(
    publisher: Callable[[str, dict[str, Any]], None],
) -> None:
    global _uds_publisher
    _uds_publisher = publisher


try:
    from mcp_events import record
except ImportError:

    def record(signal: str, **payload: Any) -> None:  # type: ignore[misc]
        if _uds_publisher is None:
            return
        _uds_publisher(signal, dict(payload))


def _emit(event: Event) -> None:
    record(event.signal, **event.payload)


@event_factory
def FrontierSdkWorkerCompleted(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    duration_s: float,
    tool_call_count: int,
    result_bytes: int,
    outcome: str,
) -> Event:
    return Event(
        signal="frontier.sdk.worker.completed",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "duration_s": duration_s,
            "tool_call_count": tool_call_count,
            "result_bytes": result_bytes,
            "outcome": outcome,
        },
        scope="node",
    )


@event_factory
def FrontierSdkWorkerFailed(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    error: str,
) -> Event:
    return Event(
        signal="frontier.sdk.worker.failed",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "error": error,
        },
        scope="node",
    )


def emit_sdk_worker_completed(
    *,
    dispatch_id: str,
    thread_id: str,
    duration_s: float,
    tool_call_count: int,
    result_bytes: int,
    outcome: str,
) -> None:
    _emit(
        FrontierSdkWorkerCompleted(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            duration_s=duration_s,
            tool_call_count=tool_call_count,
            result_bytes=result_bytes,
            outcome=outcome,
        )
    )
    logger.info(
        "cursor sdk worker completed: dispatch_id=%s thread_id=%s duration_s=%.3f "
        "tool_call_count=%s result_bytes=%s outcome=%s",
        dispatch_id,
        thread_id,
        duration_s,
        tool_call_count,
        result_bytes,
        outcome,
    )


def emit_sdk_worker_failed(
    *,
    dispatch_id: str,
    thread_id: str,
    error: str,
) -> None:
    _emit(
        FrontierSdkWorkerFailed(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            error=error,
        )
    )
