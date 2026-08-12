"""Event factories for cursor-sdk worker lifecycle signals."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

logger = get_logger(__name__)

_uds_publisher: Callable[[str, dict[str, Any]], None] | None = None
_terminal_emitted: set[str] = set()


def terminal_emitted(dispatch_id: str) -> bool:
    """Return whether a foldable worker terminal was already emitted for ``dispatch_id``."""
    return dispatch_id in _terminal_emitted


def reset_terminal_emitted_registry() -> None:
    """Clear process-local terminal registry (tests only)."""
    _terminal_emitted.clear()


def _register_terminal_emitted(dispatch_id: str) -> None:
    _terminal_emitted.add(dispatch_id)


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
def FrontierSdkAutoEmptyDirectiveScopeBlocked(  # noqa: N802
    thread_id: str,
    contract: str,
    density: str | None,
    missed_tokens: tuple[str, ...],
) -> Event:
    return Event(
        signal="frontier.sdk.auto.empty_directive_scope_blocked",
        payload={
            "thread_id": thread_id,
            "contract": contract,
            "density": density,
            "missed_tokens": list(missed_tokens),
        },
        scope="node",
    )


def emit_frontier_sdk_auto_empty_directive_scope_blocked(
    *,
    thread_id: str,
    contract: str,
    density: str | None,
    missed_tokens: tuple[str, ...],
) -> None:
    """Emit when cursor-auto blocks a nest for missing actionable scope."""
    _emit(
        FrontierSdkAutoEmptyDirectiveScopeBlocked(
            thread_id=thread_id,
            contract=contract,
            density=density,
            missed_tokens=missed_tokens,
        )
    )
    logger.info(
        "cursor-auto empty_directive_scope_blocked: thread_id=%s contract=%s "
        "density=%s missed=%s",
        thread_id,
        contract,
        density,
        missed_tokens,
    )


@event_factory
def FrontierSdkAutoEmptyDirectiveScopeWaived(  # noqa: N802
    thread_id: str,
    contract: str,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.empty_directive_scope_waived",
        payload={
            "thread_id": thread_id,
            "contract": contract,
        },
        scope="node",
    )


def emit_frontier_sdk_auto_empty_directive_scope_waived(
    *,
    thread_id: str,
    contract: str,
) -> None:
    """Emit observation when body ``contract:`` waives empty-scope refuse.

    Carries *thread_id* and stamped *contract* so waive storms stay measurable.
    """
    _emit(
        FrontierSdkAutoEmptyDirectiveScopeWaived(
            thread_id=thread_id,
            contract=contract,
        )
    )
    logger.info(
        "cursor-auto empty_directive_scope_waived: thread_id=%s contract=%s",
        thread_id,
        contract,
    )


@event_factory
def FrontierSdkAutoThreadStatusRefused(  # noqa: N802
    thread_id: str,
    status: str,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.thread_status_refused",
        payload={
            "thread_id": thread_id,
            "status": status,
        },
        scope="node",
    )


def emit_frontier_sdk_auto_thread_status_refused(
    *,
    thread_id: str,
    status: str,
) -> None:
    """Emit when cursor-auto refuses nest onto a closed/blocked bus thread."""
    _emit(
        FrontierSdkAutoThreadStatusRefused(
            thread_id=thread_id,
            status=status,
        )
    )
    logger.info(
        "cursor-auto thread_status_refused: thread_id=%s status=%s",
        thread_id,
        status,
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
    asked_by: str | None = None,
    purpose: str | None = None,
    story_id: str | None = None,
    admitted_via: str | None = None,
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
    if asked_by is not None:
        payload["asked_by"] = asked_by
    if purpose is not None:
        payload["purpose"] = purpose
    if story_id is not None:
        payload["story_id"] = story_id
    if admitted_via is not None:
        payload["admitted_via"] = admitted_via
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
    execution_id: str | None = None,
) -> Event:
    # Sibling-asymmetry (intentional): progress carries resolved_model+elapsed_s for
    # liveness; completed/failed carry outcome. OQ2: resolved_model, NEVER model_entity_id.
    payload: dict[str, object] = {
        "dispatch_id": dispatch_id,
        "thread_id": thread_id,
        "resolved_model": resolved_model,
        "elapsed_s": elapsed_s,
        "tool_call_count": tool_call_count,
    }
    if execution_id:
        payload["execution_id"] = execution_id
    return Event(
        signal="frontier.sdk.worker.progress",
        payload=payload,
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
    degraded_reasons: list[str] | None = None,
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
    if degraded_reasons is not None:
        payload["degraded_reasons"] = degraded_reasons
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
    execution_id: str | None = None,
) -> None:
    """Publish mid-run progress for a live cursor-sdk worker dispatch."""
    _emit(
        FrontierSdkWorkerProgress(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            resolved_model=resolved_model,
            elapsed_s=elapsed_s,
            tool_call_count=tool_call_count,
            execution_id=execution_id,
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
    asked_by: str | None = None,
    purpose: str | None = None,
    story_id: str | None = None,
    admitted_via: str | None = None,
) -> None:
    """Publish terminal success/outcome telemetry for a finished cursor-sdk worker.

    Carries optional association fields (``asked_by``, ``purpose``, ``story_id``,
    ``admitted_via``) when the dispatch was stamped at admit time so board fold
    and story projector can reconcile nested cursor-auto rows without re-parsing
    the packet. Registered ``admitted_via`` vocabulary: ``cursor-auto``,
    ``stargate``, or unset.
    """
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
            asked_by=asked_by,
            purpose=purpose,
            story_id=story_id,
            admitted_via=admitted_via,
        )
    )
    _register_terminal_emitted(dispatch_id)
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
def FrontierSdkWorkerDispatched(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    execution_id: str,
    seat: str = "cursor-sdk",
    admitted_via: str | None = None,
    asked_by: str | None = None,
    purpose: str | None = None,
    story_id: str | None = None,
) -> Event:
    """GIW worker lane start signal after ``mark_running``.

    Emitted on the immediate admit path and on FIFO promote, but only when
    ``admitted_via == \"cursor-auto\"`` (nested cursor-auto MCP). Registered
    vocabulary: ``cursor-auto``, ``stargate``, or unset. Stargate-admitted /
    unset admits rely on Stargate's own ``FrontierSdkWorkerDispatched``; GIW must
    not emit while ledger status is ``queued``.
    """
    payload: dict[str, object] = {
        "dispatch_id": dispatch_id,
        "thread_id": thread_id,
        "execution_id": execution_id,
        "seat": seat,
    }
    if admitted_via is not None:
        payload["admitted_via"] = admitted_via
    if asked_by is not None:
        payload["asked_by"] = asked_by
    if purpose is not None:
        payload["purpose"] = purpose
    if story_id is not None:
        payload["story_id"] = story_id
    return Event(
        signal="frontier.sdk.worker.dispatched",
        payload=payload,
        scope="node",
    )


def emit_sdk_worker_dispatched(
    *,
    dispatch_id: str,
    thread_id: str,
    execution_id: str,
    admitted_via: str | None = None,
    asked_by: str | None = None,
    purpose: str | None = None,
    story_id: str | None = None,
    seat: str = "cursor-sdk",
) -> None:
    """Publish GIW worker-lane start after ``mark_running``.

    Emitted on the immediate admit path and on FIFO promote, but only when
    ``admitted_via == \"cursor-auto\"`` (nested cursor-auto MCP). Registered
    vocabulary: ``cursor-auto``, ``stargate``, or unset. Stargate-admitted /
    unset admits rely on Stargate's own ``FrontierSdkWorkerDispatched``; GIW must
    not emit while ledger status is ``queued``.
    """
    _emit(
        FrontierSdkWorkerDispatched(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            execution_id=execution_id,
            seat=seat,
            admitted_via=admitted_via,
            asked_by=asked_by,
            purpose=purpose,
            story_id=story_id,
        )
    )


@event_factory
def FrontierSdkWorkerQueued(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    source_repo: str | None,
    queue_position: int | None,
    execution_id: str | None = None,
    holder_dispatch_id: str | None = None,
    holder_thread_id: str | None = None,
    holder_resolved_model: str | None = None,
    holder_subject_preview: str | None = None,
    resolved_model: str | None = None,
    admitted_via: str | None = None,
    asked_by: str | None = None,
    purpose: str | None = None,
    story_id: str | None = None,
    queued_on: str | None = None,
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
    if execution_id is not None:
        payload["execution_id"] = execution_id
    if admitted_via is not None:
        payload["admitted_via"] = admitted_via
    if asked_by is not None:
        payload["asked_by"] = asked_by
    if purpose is not None:
        payload["purpose"] = purpose
    if story_id is not None:
        payload["story_id"] = story_id
    if queued_on is not None:
        payload["queued_on"] = queued_on
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
def SdkLaneSelected(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    lane: str,
    reason: str,
    regime_active: bool,
    contract: str,
    selecting_predicate: str,
) -> Event:
    return Event(
        signal="sdk.lane.selected",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "lane": lane,
            "reason": reason,
            "regime_active": regime_active,
            "regime_state": "on" if regime_active else "off",
            "contract": contract,
            "selecting_predicate": selecting_predicate,
        },
        scope="node",
    )


def emit_sdk_lane_selected(
    *,
    dispatch_id: str,
    thread_id: str,
    lane: str,
    reason: str,
    regime_active: bool,
    contract: str,
    selecting_predicate: str,
) -> None:
    """Emit on every admit with resolved lane, contract, and selecting predicate (S7/D6)."""
    _emit(
        SdkLaneSelected(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            lane=lane,
            reason=reason,
            regime_active=regime_active,
            contract=contract,
            selecting_predicate=selecting_predicate,
        )
    )
    logger.info(
        "sdk lane selected: dispatch_id=%s lane=%s reason=%s contract=%s "
        "regime_active=%s predicate=%s",
        dispatch_id[:8],
        lane,
        reason,
        contract,
        regime_active,
        selecting_predicate,
    )


@event_factory
def SdkLaneBMinted(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    worktree_path: str,
    branch: str,
    branch_point: str,
    mint_wait_ms: float,
) -> Event:
    return Event(
        signal="sdk.lane_b.minted",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "worktree_path": worktree_path,
            "branch": branch,
            "branch_point": branch_point,
            "mint_wait_ms": mint_wait_ms,
        },
        scope="node",
    )


def emit_sdk_lane_b_minted(
    *,
    dispatch_id: str,
    thread_id: str,
    worktree_path: str,
    branch: str,
    branch_point: str,
    mint_wait_ms: float,
) -> None:
    """Emit after a Lane-B worktree mint completes."""
    _emit(
        SdkLaneBMinted(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            worktree_path=worktree_path,
            branch=branch,
            branch_point=branch_point,
            mint_wait_ms=mint_wait_ms,
        )
    )


@event_factory
def SdkLaneBMintRolledBack(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    reason: str,
) -> Event:
    return Event(
        signal="sdk.lane_b.mint_rolled_back",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "reason": reason,
            "reason_code": reason,
        },
        scope="node",
    )


def emit_sdk_lane_b_mint_rolled_back(
    *,
    dispatch_id: str,
    thread_id: str,
    reason: str,
) -> None:
    """Post-mint admit rejection pruned the minted Lane-B tree in the same request."""
    _emit(
        SdkLaneBMintRolledBack(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            reason=reason,
        )
    )
    logger.info(
        "sdk lane_b mint rolled back: dispatch_id=%s thread_id=%s reason=%s",
        dispatch_id[:8],
        thread_id,
        reason,
    )


@event_factory
def SdkLaneBCommitted(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    head_sha: str,
    commits_ahead: int,
    files_committed: int,
) -> Event:
    return Event(
        signal="sdk.lane_b.committed",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "head_sha": head_sha,
            "commits_ahead": commits_ahead,
            "files_committed": files_committed,
        },
        scope="node",
    )


def emit_sdk_lane_b_committed(
    *,
    dispatch_id: str,
    thread_id: str,
    head_sha: str,
    commits_ahead: int,
    files_committed: int,
) -> None:
    """Emit after commit-on-terminal on a Lane-B worktree."""
    _emit(
        SdkLaneBCommitted(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            head_sha=head_sha,
            commits_ahead=commits_ahead,
            files_committed=files_committed,
        )
    )


@event_factory
def SdkLaneBSalvaged(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    head_sha: str | None,
    trigger: str,
) -> Event:
    return Event(
        signal="sdk.lane_b.salvaged",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "head_sha": head_sha,
            "trigger": trigger,
        },
        scope="node",
    )


def emit_sdk_lane_b_salvaged(
    *,
    dispatch_id: str,
    thread_id: str,
    head_sha: str | None,
    trigger: str,
) -> None:
    """Emit when a salvage commit runs during reap/restart/prune."""
    _emit(
        SdkLaneBSalvaged(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            head_sha=head_sha,
            trigger=trigger,
        )
    )


@event_factory
def SdkLaneBSalvageFailed(  # noqa: N802
    dispatch_id: str,
    branch: str,
    worktree_path: str,
    error: str | None,
) -> Event:
    return Event(
        signal="sdk.lane_b.salvage_failed",
        payload={
            "dispatch_id": dispatch_id,
            "branch": branch,
            "worktree_path": worktree_path,
            "error": error,
        },
        scope="node",
    )


def emit_sdk_lane_b_salvage_failed(
    *,
    dispatch_id: str,
    branch: str,
    worktree_path: str,
    error: str | None,
) -> None:
    """Emit when git refuses a salvage commit, leaving work only in the worktree."""
    _emit(
        SdkLaneBSalvageFailed(
            dispatch_id=dispatch_id,
            branch=branch,
            worktree_path=worktree_path,
            error=error,
        )
    )


@event_factory
def SdkLaneBBranchRetained(  # noqa: N802
    dispatch_id: str,
    branch: str,
    commits_ahead: int | None,
) -> Event:
    return Event(
        signal="sdk.lane_b.branch_retained",
        payload={
            "dispatch_id": dispatch_id,
            "branch": branch,
            "commits_ahead": commits_ahead,
        },
        scope="node",
    )


def emit_sdk_lane_b_branch_retained(
    *,
    dispatch_id: str,
    branch: str,
    commits_ahead: int | None,
) -> None:
    """Emit when prune retains an unmerged dispatch branch.

    ``commits_ahead`` may be ``None`` when the tip meter is unresolved — same
    preserve-no-data shape as structured capture (not a measured zero).
    """
    _emit(
        SdkLaneBBranchRetained(
            dispatch_id=dispatch_id,
            branch=branch,
            commits_ahead=commits_ahead,
        )
    )


@event_factory
def SdkLaneBReaped(  # noqa: N802
    dispatch_id: str,
    branch_deleted: bool,
    branch: str | None = None,
    tip_sha: str | None = None,
    reason: str | None = None,
) -> Event:
    payload: dict[str, Any] = {
        "dispatch_id": dispatch_id,
        "branch_deleted": branch_deleted,
    }
    if branch is not None:
        payload["branch"] = branch
    if tip_sha is not None:
        payload["tip_sha"] = tip_sha
    if reason is not None:
        payload["reason"] = reason
    return Event(
        signal="sdk.lane_b.reaped",
        payload=payload,
        scope="node",
    )


def emit_sdk_lane_b_reaped(
    *,
    dispatch_id: str,
    branch_deleted: bool,
    branch: str | None = None,
    tip_sha: str | None = None,
    reason: str | None = None,
) -> None:
    """Emit when a dispatch worktree is pruned or an orphan branch is reaped."""
    _emit(
        SdkLaneBReaped(
            dispatch_id=dispatch_id,
            branch_deleted=branch_deleted,
            branch=branch,
            tip_sha=tip_sha,
            reason=reason,
        )
    )


@event_factory
def SdkLaneBDispositionMarked(  # noqa: N802
    branch: str,
    reason: str,
    dispatch_id: str,
    tip_sha: str | None = None,
) -> Event:
    payload: dict[str, Any] = {
        "branch": branch,
        "reason": reason,
        "dispatch_id": dispatch_id,
    }
    if tip_sha is not None:
        payload["tip_sha"] = tip_sha
    return Event(
        signal="sdk.lane_b.disposition_marked",
        payload=payload,
        scope="node",
    )


def emit_sdk_lane_b_disposition_marked(
    *,
    branch: str,
    reason: str,
    dispatch_id: str,
    tip_sha: str | None = None,
) -> None:
    """Emit when a seat writes a salvage-branch disposition marker."""
    _emit(
        SdkLaneBDispositionMarked(
            branch=branch,
            reason=reason,
            dispatch_id=dispatch_id,
            tip_sha=tip_sha,
        )
    )


@event_factory
def SdkLaneBOrphanAged(  # noqa: N802
    branch: str,
    tip_sha: str,
    age_s: float,
    origin_dispatch_id: str | None = None,
) -> Event:
    payload: dict[str, Any] = {
        "branch": branch,
        "tip_sha": tip_sha,
        "age_s": age_s,
    }
    if origin_dispatch_id is not None:
        payload["origin_dispatch_id"] = origin_dispatch_id
    return Event(
        signal="sdk.lane_b.orphan_aged",
        payload=payload,
        scope="node",
    )


def emit_sdk_lane_b_orphan_aged(
    *,
    branch: str,
    tip_sha: str,
    age_s: float,
    origin_dispatch_id: str | None = None,
) -> None:
    """Emit once when an unmarked orphan crosses the visibility TTL."""
    _emit(
        SdkLaneBOrphanAged(
            branch=branch,
            tip_sha=tip_sha,
            age_s=age_s,
            origin_dispatch_id=origin_dispatch_id,
        )
    )


@event_factory
def SdkLaneBWorkspacesWriteRefused(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    uri: str,
) -> Event:
    return Event(
        signal="sdk.lane_b.workspaces_write_refused",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "uri": uri,
        },
        scope="node",
    )


def emit_sdk_lane_b_workspaces_write_refused(
    *,
    dispatch_id: str,
    thread_id: str,
    uri: str,
) -> None:
    """Emit when the Lane-B MCP workspaces:// write fence fires."""
    _emit(
        SdkLaneBWorkspacesWriteRefused(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            uri=uri,
        )
    )


@event_factory
def SdkLaneBWorktreeMissingObserved(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    lease_key: str | None,
    source_repo: str,
) -> Event:
    return Event(
        signal="sdk.lane_b.worktree_missing_observed",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "lease_key": lease_key,
            "source_repo": source_repo,
        },
        scope="node",
    )


def emit_sdk_lane_b_worktree_missing_observed(
    *,
    dispatch_id: str,
    thread_id: str,
    lease_key: str | None,
    source_repo: str,
) -> None:
    """Observe Lane-B admit lacking materialized worktree (Leg-1 honesty signal)."""
    _emit(
        SdkLaneBWorktreeMissingObserved(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            lease_key=lease_key,
            source_repo=source_repo,
        )
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
    execution_id: str | None = None,
    admitted_via: str | None = None,
    asked_by: str | None = None,
    purpose: str | None = None,
    story_id: str | None = None,
    queued_on: str | None = None,
) -> None:
    """Publish FIFO queue placement while another dispatch holds the write lease."""
    _emit(
        FrontierSdkWorkerQueued(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            source_repo=source_repo,
            queue_position=queue_position,
            execution_id=execution_id,
            holder_dispatch_id=holder_dispatch_id,
            holder_thread_id=holder_thread_id,
            holder_resolved_model=holder_resolved_model,
            holder_subject_preview=holder_subject_preview,
            resolved_model=resolved_model,
            admitted_via=admitted_via,
            asked_by=asked_by,
            purpose=purpose,
            story_id=story_id,
            queued_on=queued_on,
        )
    )


def emit_write_lease_acquired(
    *,
    dispatch_id: str,
    source_repo: str | None,
) -> None:
    """Publish write-lease acquisition when a write-capable dispatch becomes holder."""
    _emit(
        FrontierWriteLeaseAcquired(
            dispatch_id=dispatch_id,
            source_repo=source_repo,
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
    since_last_progress_s: float | None = None,
    tool_call_count: int | None = None,
) -> Event:
    payload: dict[str, Any] = {
        "dispatch_id": dispatch_id,
        "thread_id": thread_id,
        "execution_id": execution_id,
        "resolved_model": resolved_model,
        "timeout_s": timeout_s,
    }
    if since_last_progress_s is not None:
        payload["since_last_progress_s"] = since_last_progress_s
    if tool_call_count is not None:
        payload["tool_call_count"] = tool_call_count
    return Event(
        signal="frontier.sdk.worker.timeout",
        payload=payload,
        scope="node",
    )


def emit_sdk_worker_timeout(
    *,
    dispatch_id: str,
    thread_id: str,
    execution_id: str,
    resolved_model: str,
    timeout_s: float,
    since_last_progress_s: float | None = None,
    tool_call_count: int | None = None,
) -> None:
    """Publish idle timeout when no successful tool call occurred within budget."""
    _emit(
        FrontierSdkWorkerTimeout(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            execution_id=execution_id,
            resolved_model=resolved_model,
            timeout_s=timeout_s,
            since_last_progress_s=since_last_progress_s,
            tool_call_count=tool_call_count,
        )
    )
    _register_terminal_emitted(dispatch_id)
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
    since_last_progress_s: float | None = None,
) -> Event:
    payload: dict[str, Any] = {
        "dispatch_id": dispatch_id,
        "thread_id": thread_id,
        "execution_id": execution_id,
        "resolved_model": resolved_model,
        "timeout_s": timeout_s,
        "bridge_aborted": bridge_aborted,
        "terminal_status": "failed",
    }
    if since_last_progress_s is not None:
        payload["since_last_progress_s"] = since_last_progress_s
    return Event(
        signal="frontier.sdk.worker.orphaned",
        payload=payload,
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
    since_last_progress_s: float | None = None,
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
            since_last_progress_s=since_last_progress_s,
        )
    )
    _register_terminal_emitted(dispatch_id)
    logger.error(
        "cursor sdk worker orphaned: dispatch_id=%s thread_id=%s model=%s "
        "timeout_s=%s bridge_aborted=%s",
        dispatch_id,
        thread_id,
        resolved_model,
        timeout_s,
        bridge_aborted,
    )


@event_factory
def FrontierSdkRestartBridgeReapFailed(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
) -> Event:
    return Event(
        signal="frontier.sdk.restart.bridge_reap_failed",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
        },
        scope="node",
    )


def emit_sdk_restart_bridge_reap_failed(
    *,
    dispatch_id: str,
    thread_id: str,
) -> None:
    """Emit when startup OS bridge reap fails but lease recovery continues."""
    _emit(
        FrontierSdkRestartBridgeReapFailed(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
        )
    )
    logger.warning(
        "cursor sdk restart bridge reap failed: dispatch_id=%s thread_id=%s",
        dispatch_id,
        thread_id,
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
    degraded_reasons: list[str] | None = None,
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
            degraded_reasons=degraded_reasons,
        )
    )
    _register_terminal_emitted(dispatch_id)


def emit_sdk_worker_unclassified_terminal(
    *,
    dispatch_id: str,
    thread_id: str,
    execution_id: str,
    worker_error_code: str,
    detail_summary: str,
) -> None:
    """Safety-net terminal when ``mark_terminal`` runs without a prior worker emit."""
    emit_sdk_worker_failed(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        execution_id=execution_id,
        error=detail_summary,
        failure_layer="unclassified_terminal",
        worker_error_code=worker_error_code,
        detail_summary=detail_summary,
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
    _register_terminal_emitted(dispatch_id)
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
def FrontierSdkCaptureDivergenceObserved(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    deviation: str,
) -> Event:
    return Event(
        signal="frontier.sdk.capture.divergence_observed",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "deviation": deviation,
        },
        scope="node",
        role="observation",
    )


def emit_sdk_capture_divergence_observed(
    *,
    dispatch_id: str,
    thread_id: str,
    deviation: str,
) -> None:
    """Emit one observation when closeout capture sees a divergence token."""
    _emit(
        FrontierSdkCaptureDivergenceObserved(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            deviation=deviation,
        )
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


@event_factory
def FrontierSdkCloseoutSidecarReceiptFailed(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    reason: str,
    sidecar_path: str,
) -> Event:
    return Event(
        signal="frontier.sdk.closeout.sidecar_receipt_failed",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "reason": reason,
            "sidecar_path": sidecar_path,
        },
        scope="node",
        role="observation",
    )


def emit_sdk_closeout_sidecar_receipt_failed(
    *,
    dispatch_id: str,
    thread_id: str,
    reason: str,
    sidecar_path: str,
) -> None:
    """Emit when repo sidecar cannot persist parseable structured_closeout_full."""
    _emit(
        FrontierSdkCloseoutSidecarReceiptFailed(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            reason=reason,
            sidecar_path=sidecar_path,
        )
    )
    logger.error(
        "cursor sdk closeout sidecar receipt failed: dispatch_id=%s thread_id=%s "
        "reason=%s sidecar_path=%s",
        dispatch_id,
        thread_id,
        reason,
        sidecar_path,
    )


@event_factory
def FrontierSdkCloseoutRelayed(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    execution_id: str,
    closeout_status: str,
    receipt_path: str,
    asked_by: str,
    purpose: str,
    story_id: str,
) -> Event:
    return Event(
        signal="frontier.sdk.closeout.relayed",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "execution_id": execution_id,
            "closeout_status": closeout_status,
            "receipt_path": receipt_path,
            "asked_by": asked_by,
            "purpose": purpose,
            "story_id": story_id,
        },
        scope="node",
        role="observation",
    )


def emit_sdk_closeout_relayed(
    *,
    dispatch_id: str,
    thread_id: str,
    execution_id: str,
    closeout_status: str,
    receipt_path: str,
    asked_by: str,
    purpose: str,
    story_id: str,
) -> None:
    """Emit when cursor-auto relays operator CLOSEOUT after nested SDK terminal."""
    _emit(
        FrontierSdkCloseoutRelayed(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            execution_id=execution_id,
            closeout_status=closeout_status,
            receipt_path=receipt_path,
            asked_by=asked_by,
            purpose=purpose,
            story_id=story_id,
        )
    )
    logger.info(
        "cursor sdk closeout relayed: dispatch_id=%s thread_id=%s "
        "closeout_status=%s story_id=%s asked_by=%s purpose=%s receipt_path=%s",
        dispatch_id,
        thread_id,
        closeout_status,
        story_id,
        asked_by,
        purpose,
        receipt_path,
    )
