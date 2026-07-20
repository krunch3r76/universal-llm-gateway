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


def emit_frontier_event(event: Event) -> None:
    """Publish an ``Event`` via the registered publisher.

    Public counterpart to ``_emit`` for ``@event_factory`` functions defined
    in sibling modules (e.g. ``cursor_sdk_stream_capture``) that still need
    the same registered-publisher wiring this module owns.
    """
    _emit(event)


@event_factory
def FrontierSdkWorkerCompleted(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    execution_id: str,
    duration_s: float,
    tool_call_count: int,
    result_bytes: int,
    outcome: str,
    resolved_model: str,
    model_knobs_requested: dict[str, str] | None = None,
    usage: dict[str, Any] | None = None,
    usage_capture_status: str = "missing",
) -> Event:
    payload: dict[str, Any] = {
        "dispatch_id": dispatch_id,
        "thread_id": thread_id,
        "execution_id": execution_id,
        "duration_s": duration_s,
        "tool_call_count": tool_call_count,
        "result_bytes": result_bytes,
        "outcome": outcome,
        "resolved_model": resolved_model,
        "usage": usage,
        "usage_capture_status": usage_capture_status,
    }
    if model_knobs_requested is not None:
        payload["model_knobs_requested"] = model_knobs_requested
    return Event(
        signal="frontier.sdk.worker.completed",
        payload=payload,
        scope="node",
    )


@event_factory
def FrontierSdkWorkerProgress(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    resolved_model: str,
    elapsed_s: float,
    tool_call_count: int,
) -> Event:
    # Sibling-asymmetry (intentional): progress carries resolved_model+elapsed_s for
    # liveness; completed/failed carry outcome. OQ2: resolved_model, NEVER model_entity_id.
    return Event(
        signal="frontier.sdk.worker.progress",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "resolved_model": resolved_model,
            "elapsed_s": elapsed_s,
            "tool_call_count": tool_call_count,
        },
        scope="node",
        role="realtime",
    )


@event_factory
def FrontierSdkWorkerFailed(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    execution_id: str,
    error: str,
    origin_service: str = "git_worker",
    schema_version: str = "1",
    failure_layer: str | None = None,
    http_status: int | None = None,
    worker_error_code: str | None = None,
    transport_error_kind: str | None = None,
    detail_summary: str | None = None,
) -> Event:
    payload: dict[str, object] = {
        "dispatch_id": dispatch_id,
        "thread_id": thread_id,
        "execution_id": execution_id,
        "error": error,
        "origin_service": origin_service,
        "schema_version": schema_version,
    }
    if failure_layer is not None:
        payload["failure_layer"] = failure_layer
    if http_status is not None:
        payload["http_status"] = http_status
    if worker_error_code is not None:
        payload["worker_error_code"] = worker_error_code
    if transport_error_kind is not None:
        payload["transport_error_kind"] = transport_error_kind
    if detail_summary is not None:
        payload["detail_summary"] = detail_summary
    return Event(
        signal="frontier.sdk.worker.failed",
        payload=payload,
        scope="node",
    )


@event_factory
def FrontierSdkWorkerDeliveryFailed(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    execution_id: str,
    status_code: int,
    result_bytes: int,
    sidecar_ref: str,
) -> Event:
    return Event(
        signal="frontier.sdk.worker.delivery_failed",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "execution_id": execution_id,
            "status_code": status_code,
            "result_bytes": result_bytes,
            "sidecar_ref": sidecar_ref,
        },
        scope="node",
    )


def emit_sdk_worker_progress(
    *,
    dispatch_id: str,
    thread_id: str,
    resolved_model: str,
    elapsed_s: float,
    tool_call_count: int,
) -> None:
    _emit(
        FrontierSdkWorkerProgress(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            resolved_model=resolved_model,
            elapsed_s=elapsed_s,
            tool_call_count=tool_call_count,
        )
    )


def emit_sdk_worker_completed(
    *,
    dispatch_id: str,
    thread_id: str,
    execution_id: str,
    duration_s: float,
    tool_call_count: int,
    result_bytes: int,
    outcome: str,
    resolved_model: str,
    model_knobs_requested: dict[str, str] | None = None,
    usage: dict[str, Any] | None = None,
    usage_capture_status: str = "missing",
) -> None:
    _emit(
        FrontierSdkWorkerCompleted(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            execution_id=execution_id,
            duration_s=duration_s,
            tool_call_count=tool_call_count,
            result_bytes=result_bytes,
            outcome=outcome,
            resolved_model=resolved_model,
            model_knobs_requested=model_knobs_requested,
            usage=usage,
            usage_capture_status=usage_capture_status,
        )
    )
    logger.info(
        "cursor sdk worker completed: dispatch_id=%s thread_id=%s duration_s=%.3f "
        "tool_call_count=%s result_bytes=%s outcome=%s resolved_model=%s "
        "usage_capture_status=%s usage=%s",
        dispatch_id,
        thread_id,
        duration_s,
        tool_call_count,
        result_bytes,
        outcome,
        resolved_model,
        usage_capture_status,
        usage,
    )


@event_factory
def FrontierSdkWorkerQueued(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    source_repo: str | None,
    queue_position: int | None,
    holder_dispatch_id: str | None = None,
    holder_thread_id: str | None = None,
    holder_resolved_model: str | None = None,
    holder_subject_preview: str | None = None,
) -> Event:
    payload: dict[str, object] = {
        "dispatch_id": dispatch_id,
        "thread_id": thread_id,
        "source_repo": source_repo,
        "queue_position": queue_position,
    }
    if holder_dispatch_id is not None:
        payload["holder_dispatch_id"] = holder_dispatch_id
    if holder_thread_id is not None:
        payload["holder_thread_id"] = holder_thread_id
    if holder_resolved_model is not None:
        payload["holder_resolved_model"] = holder_resolved_model
    if holder_subject_preview is not None:
        payload["holder_subject_preview"] = holder_subject_preview
    return Event(
        signal="frontier.sdk.worker.queued",
        payload=payload,
        scope="node",
    )


@event_factory
def FrontierWriteLeaseAcquired(  # noqa: N802
    dispatch_id: str,
    source_repo: str | None,
) -> Event:
    return Event(
        signal="frontier.sdk.worker.lease.acquired",
        payload={"dispatch_id": dispatch_id, "source_repo": source_repo},
        scope="node",
    )


@event_factory
def FrontierWriteLeaseReleased(  # noqa: N802
    dispatch_id: str,
    source_repo: str | None,
    stale: bool = False,
) -> Event:
    return Event(
        signal="frontier.sdk.worker.lease.released",
        payload={
            "dispatch_id": dispatch_id,
            "source_repo": source_repo,
            "stale": stale,
        },
        scope="node",
    )


@event_factory
def FrontierWriteLeasePromoted(  # noqa: N802
    dispatch_id: str,
    source_repo: str | None,
) -> Event:
    return Event(
        signal="frontier.sdk.worker.lease.promoted",
        payload={"dispatch_id": dispatch_id, "source_repo": source_repo},
        scope="node",
    )


def emit_sdk_worker_queued(
    *,
    dispatch_id: str,
    thread_id: str,
    source_repo: str | None,
    queue_position: int | None,
    holder_dispatch_id: str | None = None,
    holder_thread_id: str | None = None,
    holder_resolved_model: str | None = None,
    holder_subject_preview: str | None = None,
) -> None:
    _emit(
        FrontierSdkWorkerQueued(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            source_repo=source_repo,
            queue_position=queue_position,
            holder_dispatch_id=holder_dispatch_id,
            holder_thread_id=holder_thread_id,
            holder_resolved_model=holder_resolved_model,
            holder_subject_preview=holder_subject_preview,
        )
    )


def emit_write_lease_released(
    *,
    dispatch_id: str,
    source_repo: str | None,
    stale: bool = False,
) -> None:
    _emit(
        FrontierWriteLeaseReleased(
            dispatch_id=dispatch_id,
            source_repo=source_repo,
            stale=stale,
        )
    )


def emit_write_lease_promoted(*, dispatch_id: str, source_repo: str | None) -> None:
    _emit(
        FrontierWriteLeasePromoted(
            dispatch_id=dispatch_id,
            source_repo=source_repo,
        )
    )


@event_factory
def FrontierSdkWorkerTimeout(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    execution_id: str,
    resolved_model: str,
    timeout_s: float,
) -> Event:
    return Event(
        signal="frontier.sdk.worker.timeout",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "execution_id": execution_id,
            "resolved_model": resolved_model,
            "timeout_s": timeout_s,
        },
        scope="node",
    )


def emit_sdk_worker_timeout(
    *,
    dispatch_id: str,
    thread_id: str,
    execution_id: str,
    resolved_model: str,
    timeout_s: float,
) -> None:
    _emit(
        FrontierSdkWorkerTimeout(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            execution_id=execution_id,
            resolved_model=resolved_model,
            timeout_s=timeout_s,
        )
    )
    logger.error(
        "cursor sdk worker timeout: dispatch_id=%s thread_id=%s model=%s timeout_s=%s",
        dispatch_id,
        thread_id,
        resolved_model,
        timeout_s,
    )


@event_factory
def FrontierSdkWorkerOrphaned(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    execution_id: str,
    resolved_model: str,
    timeout_s: float,
    bridge_aborted: bool,
) -> Event:
    return Event(
        signal="frontier.sdk.worker.orphaned",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "execution_id": execution_id,
            "resolved_model": resolved_model,
            "timeout_s": timeout_s,
            "bridge_aborted": bridge_aborted,
            "terminal_status": "failed",
        },
        scope="node",
    )


def emit_sdk_worker_orphaned(
    *,
    dispatch_id: str,
    thread_id: str,
    execution_id: str,
    resolved_model: str,
    timeout_s: float,
    bridge_aborted: bool,
) -> None:
    _emit(
        FrontierSdkWorkerOrphaned(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            execution_id=execution_id,
            resolved_model=resolved_model,
            timeout_s=timeout_s,
            bridge_aborted=bridge_aborted,
        )
    )
    logger.error(
        "cursor sdk worker orphaned: dispatch_id=%s thread_id=%s model=%s "
        "timeout_s=%s bridge_aborted=%s",
        dispatch_id,
        thread_id,
        resolved_model,
        timeout_s,
        bridge_aborted,
    )


def emit_sdk_worker_failed(
    *,
    dispatch_id: str,
    thread_id: str,
    execution_id: str,
    error: str,
    failure_layer: str = "worker_runtime",
    worker_error_code: str | None = None,
    detail_summary: str | None = None,
) -> None:
    _emit(
        FrontierSdkWorkerFailed(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            execution_id=execution_id,
            error=error,
            failure_layer=failure_layer,
            worker_error_code=worker_error_code,
            detail_summary=detail_summary or error,
        )
    )


def emit_sdk_worker_delivery_failed(
    *,
    dispatch_id: str,
    thread_id: str,
    execution_id: str,
    status_code: int,
    result_bytes: int,
    sidecar_ref: str,
) -> None:
    _emit(
        FrontierSdkWorkerDeliveryFailed(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            execution_id=execution_id,
            status_code=status_code,
            result_bytes=result_bytes,
            sidecar_ref=sidecar_ref,
        )
    )
    logger.error(
        "cursor sdk worker delivery failed: dispatch_id=%s thread_id=%s "
        "status_code=%s result_bytes=%s sidecar=%s",
        dispatch_id,
        thread_id,
        status_code,
        result_bytes,
        sidecar_ref,
    )


@event_factory
def FrontierSdkCloseoutRelocated(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    execution_id: str,
    uri: str,
    body_chars: int,
    tier: str,
) -> Event:
    return Event(
        signal="frontier.sdk.closeout.relocated",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "execution_id": execution_id,
            "uri": uri,
            "body_chars": body_chars,
            "tier": tier,
        },
        scope="node",
    )


def emit_sdk_closeout_relocated(
    *,
    dispatch_id: str,
    thread_id: str,
    execution_id: str,
    uri: str,
    body_chars: int,
    tier: str,
) -> None:
    _emit(
        FrontierSdkCloseoutRelocated(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            execution_id=execution_id,
            uri=uri,
            body_chars=body_chars,
            tier=tier,
        )
    )
    logger.info(
        "cursor sdk closeout relocated: dispatch_id=%s thread_id=%s tier=%s "
        "body_chars=%s uri=%s",
        dispatch_id,
        thread_id,
        tier,
        body_chars,
        uri,
    )


@event_factory
def FrontierSdkCloseoutReconciled(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    suppressed_reason: str,
    verifying_path: str,
) -> Event:
    return Event(
        signal="frontier.sdk.closeout.reconciled",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "suppressed_reason": suppressed_reason,
            "verifying_path": verifying_path,
        },
        scope="node",
    )


def emit_sdk_closeout_reconciled(
    *,
    dispatch_id: str,
    thread_id: str,
    suppressed_reason: str,
    verifying_path: str,
) -> None:
    """Emitted when filesystem ground truth suppresses a would-be light-bounded
    ``stated_intent_no_write`` / ``deliverable_write_choked`` degrade because the
    packet-declared deliverable is verified present on disk/cortex (the SDK stream
    missed the write, e.g. a cortex sidecar; cf. the 22454 ``zero_tool_calls`` gap).
    """
    _emit(
        FrontierSdkCloseoutReconciled(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            suppressed_reason=suppressed_reason,
            verifying_path=verifying_path,
        )
    )
    logger.info(
        "cursor sdk closeout reconciled: dispatch_id=%s thread_id=%s "
        "suppressed_reason=%s verifying_path=%s",
        dispatch_id,
        thread_id,
        suppressed_reason,
        verifying_path,
    )
