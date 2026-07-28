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
    """Install the UDS publisher used when mcp_events.record is unavailable."""
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
def FrontierSdkAutoAuthGateBlocked(  # noqa: N802
    thread_id: str,
    failure_count: int,
    budget: int,
    post_ack: bool,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.auth_gate_blocked",
        payload={
            "thread_id": thread_id,
            "failure_count": failure_count,
            "budget": budget,
            "post_ack": post_ack,
        },
        scope="node",
    )


def emit_frontier_sdk_auto_auth_gate_blocked(
    *,
    thread_id: str,
    failure_count: int,
    budget: int,
    post_ack: bool,
) -> None:
    """Emit when cursor-auto refuse-admits on auth-gate budget exhaustion."""
    _emit(
        FrontierSdkAutoAuthGateBlocked(
            thread_id=thread_id,
            failure_count=failure_count,
            budget=budget,
            post_ack=post_ack,
        )
    )
    logger.info(
        "cursor-auto auth_gate_blocked: thread_id=%s failure_count=%s "
        "budget=%s post_ack=%s",
        thread_id,
        failure_count,
        budget,
        post_ack,
    )


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
    request_id: str | None = None,
    sdk_request_id: str | None = None,
    request_id_source: str | None = None,
    sdk_run_id: str | None = None,
    sdk_agent_id: str | None = None,
    degraded_reasons: list[str] | None = None,
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
    if request_id is not None:
        payload["request_id"] = request_id
    if sdk_request_id is not None:
        payload["sdk_request_id"] = sdk_request_id
    if request_id_source is not None:
        payload["request_id_source"] = request_id_source
    if sdk_run_id is not None:
        payload["sdk_run_id"] = sdk_run_id
    if sdk_agent_id is not None:
        payload["sdk_agent_id"] = sdk_agent_id
    if degraded_reasons is not None:
        payload["degraded_reasons"] = degraded_reasons
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
    """Publish mid-run progress for a live cursor-sdk worker dispatch."""
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
    request_id: str | None = None,
    sdk_request_id: str | None = None,
    request_id_source: str | None = None,
    sdk_run_id: str | None = None,
    sdk_agent_id: str | None = None,
    degraded_reasons: list[str] | None = None,
) -> None:
    """Publish terminal success/outcome telemetry for a finished cursor-sdk worker."""
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
            request_id=request_id,
            sdk_request_id=sdk_request_id,
            request_id_source=request_id_source,
            sdk_run_id=sdk_run_id,
            sdk_agent_id=sdk_agent_id,
            degraded_reasons=degraded_reasons,
        )
    )
    logger.info(
        "cursor sdk worker completed: dispatch_id=%s thread_id=%s duration_s=%.3f "
        "tool_call_count=%s result_bytes=%s outcome=%s resolved_model=%s "
        "usage_capture_status=%s usage=%s request_id=%s sdk_request_id=%s "
        "request_id_source=%s sdk_run_id=%s sdk_agent_id=%s degraded_reasons=%s",
        dispatch_id,
        thread_id,
        duration_s,
        tool_call_count,
        result_bytes,
        outcome,
        resolved_model,
        usage_capture_status,
        usage,
        request_id,
        sdk_request_id,
        request_id_source,
        sdk_run_id,
        sdk_agent_id,
        degraded_reasons,
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
    resolved_model: str | None = None,
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
    if resolved_model is not None:
        payload["resolved_model"] = resolved_model
    return Event(
        signal="frontier.sdk.worker.queued",
        payload=payload,
        scope="node",
    )


@event_factory
def FrontierSdkImplementSourceRefUnresolved(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    execution_id: str,
) -> Event:
    return Event(
        signal="frontier.sdk.implement.source_ref_unresolved",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "execution_id": execution_id,
        },
        scope="node",
    )


def emit_sdk_implement_unresolved_source_ref(
    *,
    dispatch_id: str,
    thread_id: str,
    execution_id: str,
) -> None:
    """Watchable gap when ``contract=implement`` admits without a ``source_ref``."""
    _emit(
        FrontierSdkImplementSourceRefUnresolved(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            execution_id=execution_id,
        )
    )
    logger.warning(
        "cursor sdk implement admit unresolved source_ref: dispatch_id=%s "
        "thread_id=%s execution_id=%s",
        dispatch_id,
        thread_id,
        execution_id,
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


@event_factory
def FrontierWriteLeaseQueueStalled(  # noqa: N802
    source_repo: str | None,
) -> Event:
    return Event(
        signal="frontier.sdk.worker.lease.queue_stalled",
        payload={"source_repo": source_repo},
        scope="node",
    )


def emit_write_lease_queue_stalled(*, source_repo: str | None) -> None:
    """Emit when queued writers exist but no live blocking holder can promote."""
    _emit(FrontierWriteLeaseQueueStalled(source_repo=source_repo))


@event_factory
def FrontierWriteLeaseParkEnter(  # noqa: N802
    parent_id: str,
    child_id: str,
    source_repo: str | None,
    nest_depth: int | None = None,
) -> Event:
    payload: dict[str, object] = {
        "parent_id": parent_id,
        "child_id": child_id,
        "source_repo": source_repo,
    }
    if nest_depth is not None:
        payload["nest_depth"] = nest_depth
    return Event(
        signal="frontier.sdk.worker.lease.park_enter",
        payload=payload,
        scope="node",
    )


@event_factory
def FrontierWriteLeaseParkRestore(  # noqa: N802
    parent_id: str,
    child_id: str,
    source_repo: str | None,
) -> Event:
    return Event(
        signal="frontier.sdk.worker.lease.park_restore",
        payload={
            "parent_id": parent_id,
            "child_id": child_id,
            "source_repo": source_repo,
        },
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
    resolved_model: str | None = None,
) -> None:
    """Publish FIFO queue placement while another dispatch holds the write lease."""
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
            resolved_model=resolved_model,
        )
    )


def emit_write_lease_released(
    *,
    dispatch_id: str,
    source_repo: str | None,
    stale: bool = False,
) -> None:
    """Publish write-lease release for a dispatch (including stale reclaim)."""
    _emit(
        FrontierWriteLeaseReleased(
            dispatch_id=dispatch_id,
            source_repo=source_repo,
            stale=stale,
        )
    )


def emit_write_lease_promoted(*, dispatch_id: str, source_repo: str | None) -> None:
    """Publish write-lease promotion when a queued dispatch becomes the holder."""
    _emit(
        FrontierWriteLeasePromoted(
            dispatch_id=dispatch_id,
            source_repo=source_repo,
        )
    )


def emit_write_lease_park_enter(
    *,
    parent_id: str,
    child_id: str,
    source_repo: str | None,
    nest_depth: int | None = None,
) -> None:
    """Publish nest park-enter when parent yields lease and capacity to nested child."""
    _emit(
        FrontierWriteLeaseParkEnter(
            parent_id=parent_id,
            child_id=child_id,
            source_repo=source_repo,
            nest_depth=nest_depth,
        )
    )


def emit_write_lease_park_restore(
    *, parent_id: str, child_id: str, source_repo: str | None
) -> None:
    """Publish nest park-restore when child terminal returns lease and capacity to parent."""
    _emit(
        FrontierWriteLeaseParkRestore(
            parent_id=parent_id,
            child_id=child_id,
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
    """Publish hard-timeout failure for a cursor-sdk worker that exceeded its budget."""
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
    """Publish orphaned-worker failure when the bridge exits without a clean closeout."""
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
    """Publish structured worker-runtime failure with layer and error code detail."""
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
    """Publish closeout delivery failure when the bus/sidecar post does not succeed."""
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
    """Publish closeout body relocation to a durable URI when inline size exceeds limits."""
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
