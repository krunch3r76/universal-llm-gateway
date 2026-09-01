"""Cursor SDK dispatch route — admits dispatches via cursor-sdk-bridge.

In-memory idempotency registry state is lost on worker restart (Phase 1 scope).

Phase 2 HOME isolation (T2b 2026-06-11, thread 1559): each dispatch seeds a
private HOME with copied ``cli-config.json`` (identity), XDG ``auth.json``
(credential), and user-layer Cursor settings for ``setting_sources=all``.
``Client.launch_bridge`` snapshots ``os.environ`` at ``Popen`` (no ``env=``
kwarg in cursor-sdk 0.1.8). Each dispatch records its HOME/venv override in
thread-local storage; a monkeypatch on ``_bridge_subprocess_env`` overlays it
into the bridge subprocess env at ``Popen`` time. The override is confined to
the dispatch's own worker thread, so concurrent and timed-out (orphan)
dispatches never race on shared global state and no dispatch lock is needed.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import contextmanager
from pathlib import Path
from threading import Event as _ThreadEvent
from threading import Thread
from typing import Any

import httpx
from cursor_capabilities import effective_knobs
from cursor_sdk import Client
from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from implement_admission.closeout_helpers import cortex_files_root
from implement_admission.normalize import _files_from_packet
from universal_logging import get_logger
from universal_protocol import error_envelope

from services.git_integration_worker.admission import (
    Draining503,
    WorkAdmissionController,
)
from services.git_integration_worker.config import WorkerConfig, load_config
from services.git_integration_worker.cursor_bus import CursorBusClient
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    DispatchConflict,
    PromotedDispatch,
    SourceRefConflict,
    WorkerThreadOccupied,
    WriteLeaseHeld,
)
from services.git_integration_worker.cursor_home import (
    CursorHomeConfigError,
    CursorVenvConfigError,
    build_dispatch_path_prepend,
    dispatch_git_env_vars,
    dispatch_home_path,
    operator_real_home,
    prune_stale_dispatch_homes,
    resolve_repo_venv,
    setup_cursor_dispatch_home,
    validate_repo_venv,
)
from services.git_integration_worker.cursor_models import (
    build_model_selection,
    resolve_cursor,
)
from services.git_integration_worker.cursor_sdk_association import (
    build_dispatch_association_fields,
)
from services.git_integration_worker.cursor_sdk_bridge_stderr import (
    bridge_exit_snapshot,
    start_bridge_stderr_drain,
    stop_bridge_stderr_drain,
)
from services.git_integration_worker.cursor_sdk_capture_binding import (
    CaptureBinding,
    binding_for_dispatch,
)
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    capture_wt_baseline_with_hashes,
    count_tool_calls,
    degraded_implement_reason,
    degraded_reasons_from_exception,
    empty_assistant_turn_reason,
    empty_output_degraded_reason,
    format_delivery_fallback_body,
    merge_degraded_reasons,
    prepare_closeout_delivery_async,
    read_post_wait_snapshot,
    resolve_completion_outcome,
    resolve_run_outcome_label,
    stream_only_effect_deviations,
)
from services.git_integration_worker.cursor_sdk_closeout_subject import (
    build_sdk_closeout_subject,
)
from services.git_integration_worker.cursor_sdk_closeout_trigger import (
    build_closeout_idempotency_key,
    emit_implement_closeout_trigger,
    extract_turn_number,
    normalize_closeout_source_ref,
)
from services.git_integration_worker.cursor_sdk_concurrency_posture import (
    b_worktree_materialized,
    derive_concurrency_posture,
    lane_b_worktree_missing,
    lease_is_isolated_worktree,
    write_lease_slot_limit,
)
from services.git_integration_worker.cursor_sdk_conductor_conflict import (
    find_open_conductor_holder_conn,
    should_block_implement_for_open_conductor,
)
from services.git_integration_worker.cursor_sdk_context import (
    CursorSdkParityError,
    build_agent_options,
    validate_dispatch_context,
)
from services.git_integration_worker.cursor_sdk_deliverable_truth import (
    LIGHT_BOUNDED_CONTRACT,
    light_bounded_deliverable_reason,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    sidecar_workspaces_ref,
)
from services.git_integration_worker.cursor_sdk_deliverables_expected import (
    compute_deliverables_expected,
)
from services.git_integration_worker.cursor_sdk_events import (
    emit_sdk_closeout_reconciled,
    emit_sdk_implement_unresolved_source_ref,
    emit_sdk_lane_b_mint_rolled_back,
    emit_sdk_lane_b_minted,
    emit_sdk_lane_b_worktree_missing_observed,
    emit_sdk_lane_selected,
    emit_sdk_restart_bridge_reap_failed,
    emit_sdk_worker_completed,
    emit_sdk_worker_delivery_failed,
    emit_sdk_worker_dispatched,
    emit_sdk_worker_failed,
    emit_sdk_worker_orphaned,
    emit_sdk_worker_progress,
    emit_sdk_worker_queued,
    emit_sdk_worker_resumed,
    emit_sdk_worker_timeout,
    emit_sdk_worker_unclassified_terminal,
    emit_write_lease_acquired,
    emit_write_lease_promoted,
    emit_write_lease_queue_stalled,
    emit_write_lease_released,
    terminal_emitted,
)
from services.git_integration_worker.cursor_sdk_feature_probe import (
    LOCAL_BRIDGE_PATH_LABEL,
    git_probe_degraded_reasons,
    probe_run_git_info,
)
from services.git_integration_worker.cursor_sdk_gate import (
    SdkSlotAcquireStallError,
    acquire_sdk_dispatch_slot,
    sdk_dispatch_gate_stats,
    sdk_dispatch_lane,
)
from services.git_integration_worker.cursor_sdk_implement_gate import (
    implement_gate_bypass_deviations,
)
from services.git_integration_worker.cursor_sdk_lane_branch import associate_lane_branch
from services.git_integration_worker.cursor_sdk_lane_regime import lane_b_regime_active
from services.git_integration_worker.cursor_sdk_lane_select import (
    LaneScopeRefused,
    lane_selection_predicate,
    select_lane,
    wire_lane_explicit,
)
from services.git_integration_worker.cursor_sdk_light_bounded_capture import (
    extract_instructed_paths,
    first_landed_fs_uri,
    fs_write_landed,
    light_bounded_deliverable_present,
)
from services.git_integration_worker.cursor_sdk_manifest import (
    build_effects_manifest,
    classify_mcp_capture_branch,
    merge_artifact_paths,
    merge_stream_tool_calls,
)
from services.git_integration_worker.cursor_sdk_nest_depth import (
    NestDepthExceeded,
    NestParentNotLive,
)
from services.git_integration_worker.cursor_sdk_orphan import (
    abort_orphaned_bridge,
    clear_dispatch_orphan_state,
    is_dispatch_orphaned,
    mark_dispatch_orphaned,
    reap_orphan_bridge_os,
    register_active_client,
    sweep_unowned_bridges,
)
from services.git_integration_worker.cursor_sdk_packet import (
    extract_packet_kind_from_packet,
    extract_source_ref_from_packet,
    extract_work_key_from_packet,
    infer_contract_from_text,
    resolve_prompt_preamble,
)
from services.git_integration_worker.cursor_sdk_park import (
    queue_stall_lease_keys,
    reclaim_orphan_holder,
    release_or_restore_for_child,
    release_or_restore_for_child_sync,
    transfer_capacity_after_park,
)
from services.git_integration_worker.cursor_sdk_restart_orphan import (
    emit_restart_survivor_terminal,
    load_ledger_row,
    salvage_restart_survivor_worktree,
)
from services.git_integration_worker.cursor_sdk_resume import (
    closeout_qualifies_for_resume_retain,
    load_parent_row,
    load_resume_run_context,
    persist_resume_retain,
    persist_timeout_retain,
    record_resolved_store_roots,
    reject_resume_if_ineligible,
    resolve_sdk_store_dir,
    sdk_agent_id_from_agent,
    start_or_resume_agent,
)
from services.git_integration_worker.cursor_sdk_satellite_workspace import (
    CursorWorkspaceError,
    resolve_dispatch_source_repo,
)
from services.git_integration_worker.cursor_sdk_stream_capture import (
    StreamCapture,
    finalize_request_id_capture,
    observe_run_stream,
    request_id_from_sdk_error,
)
from services.git_integration_worker.cursor_sdk_subagent_capture import (
    merge_stream_subagent_calls,
)
from services.git_integration_worker.cursor_sdk_substrate_tools import (
    SubstrateDispatchContext,
)
from services.git_integration_worker.cursor_sdk_supersede import (
    register_live_run,
    unregister_live_run,
)
from services.git_integration_worker.cursor_sdk_transcript import resolve_run_body
from services.git_integration_worker.cursor_sdk_usage_extract import (
    finalize_dispatch_usage,
    persist_dispatch_usage,
)
from services.git_integration_worker.cursor_sdk_workspace import (
    resolve_promoted_workspace,
)
from services.git_integration_worker.cursor_sdk_worktree import (
    WorktreeMintError,
    lookup_parent_lease_key,
    maybe_prune_worktree_on_terminal,
    reap_orphan_worktrees,
    resolve_admit_binding,
)
from services.git_integration_worker.cursor_sdk_worktree_prune import (
    prune_dispatch_worktree,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    lookup_dispatch_worktree,
    lookup_lane_worktree,
)
from services.git_integration_worker.git_worker_lifecycle_events import (
    FailureLayer,
    build_dispatch_error_envelope,
    emit_git_worker_dispatch_rejected,
    log_dispatch_rejection,
    request_id_from_dispatch_id,
)
from services.git_integration_worker.models.cursor_api import (
    BranchDischargeRequest,
    CursorDispatchRequest,
    CursorDispatchResponse,
)

logger = get_logger(__name__)

# Item 7 (list_runs cwd): killed — zero ULG call sites under grep (sdk019).

router = APIRouter(prefix="/api/v1/cursor", tags=["cursor-sdk"])

_CONFIG: WorkerConfig = load_config()
_SDK_BRIDGE_BIN = os.environ.get("CURSOR_SDK_BRIDGE_BIN", "").strip() or None
_SDK_TIMEOUT_S = float(os.environ.get("CURSOR_SDK_TIMEOUT", "1800"))
_SDK_TIMEOUT_BUFFER_S = 120.0
# Bounded slice for the idle-since-progress outer wait loop. Overshoot on
# timeout fire is at most one slice; ~60 wakeups per 30-min idle window.
_SDK_IDLE_WAIT_SLICE_S = 30.0


def _sdk_client_read_timeout() -> float | None:
    """Bridge HTTP read deadline (friction 23057).

    The SDK's default stream read timeout is 600s (`cursor_sdk._connect.
    DEFAULT_STREAM_TIMEOUT_SECONDS`); only bytes on the bridge response
    stream reset it, so a healthy-but-quiet tool leg (e.g. a long remote
    Playwright/upload step) trips ReadTimeout at last_byte+600s while the
    30s heartbeat keeps reporting progress — heartbeat and read deadline
    are on different clocks.

    Default: outer idle budget + 60s margin (NOT None). The outer
    idle-since-successful-toolcall watchdog governs every healthy run
    first, so this deadline never fires on a live dispatch; its sole job
    is to unblock an orphaned sync worker thread after the async side has
    already timed out. That unblock matters: the worker thread's
    ``finally`` is what releases the capacity slot (gate limit=1) and
    closes the bridge — a truly unbounded read against a wedged-but-
    connected bridge would hold the slot forever and brick the dispatch
    lane. Outer idle budget < read idle is preserved by the +60 margin.
    Set CURSOR_SDK_CLIENT_READ_TIMEOUT to override (<=0 for unbounded — not
    recommended).
    """
    raw = os.environ.get("CURSOR_SDK_CLIENT_READ_TIMEOUT", "").strip()
    if not raw:
        return _SDK_TIMEOUT_S + _SDK_TIMEOUT_BUFFER_S + 60.0
    value = float(raw)
    return value if value > 0 else None


# Finite everywhere: connect/write/pool keep genuinely-dead bridges failing
# fast; read sits just above the outer idle watchdog so it only fires to
# unblock an orphaned worker thread (see friction 23057 / _sdk_client_read_timeout).
_SDK_CLIENT_TIMEOUT = httpx.Timeout(
    connect=30.0,
    read=_sdk_client_read_timeout(),
    write=120.0,
    pool=60.0,
)
_SDK_HEARTBEAT_S = float(os.environ.get("CURSOR_SDK_HEARTBEAT", "30"))
# Default magnitude outer idle budget + 60s — a threshold, not a wall-from-
# start clock. Stale reap ages on ledger heartbeat liveness
# (``cursor_sdk_park._heartbeat_stale``: ``last_heartbeat_at`` or else
# ``started_at``), independent of tool-call idle state. Heartbeats fire
# every ``_SDK_HEARTBEAT_S`` regardless of successful tool calls, so a
# healthy long run keeps the lease; outer idle timeout and stale lease
# remain complementary (bridge-idle vs heartbeat-dead).
_STALE_LEASE_S = float(
    os.environ.get(
        "CURSOR_STALE_LEASE_S",
        str(_SDK_TIMEOUT_S + _SDK_TIMEOUT_BUFFER_S + 60.0),
    )
)
_STALE_SWEEP_S = float(os.environ.get("CURSOR_STALE_SWEEP_S", "30"))
_BRIDGE_SWEEP_S = float(os.environ.get("CURSOR_BRIDGE_SWEEP_S", "900"))
# Reap horizon for a holder that took the write lease and never armed (no
# heartbeat row at all). Must stay above _SDK_LAUNCH_TIMEOUT_S or the sweeper
# races a bridge that is still legitimately launching.
_SDK_ARM_TIMEOUT_S = float(os.environ.get("CURSOR_SDK_ARM_TIMEOUT", "300"))
_DEAD_RUN_GRACE_S = float(
    os.environ.get("CURSOR_DEAD_RUN_GRACE_S", str(2.0 * _SDK_HEARTBEAT_S))
)
# Retry-After hint (seconds) on the 503 returned while draining.
_DRAIN_RETRY_AFTER_S = int(os.environ.get("GIT_WORKER_DRAIN_RETRY_AFTER", "5"))

# Bounded retry for the pre-discovery bridge-launch transient: cursor-sdk
# intermittently exits the bridge BEFORE the discovery handshake with an empty
# "--tool-callback-auth-token" (the local callback token is momentarily
# unavailable at launch, e.g. while the cursor credential is mid-rotation).
# Pre-discovery => the agent never ran and nothing was written, so re-seeding the
# dispatch HOME (to pick up the rotated credential) and relaunching is
# side-effect-free and safe. Confirmed self-recovering 2026-06-15
# (79cc476a->e4afe1fe, 69eededb->0ae492ce); see cortex assertion 19136 /
# notes/system/threads/cursor-sdk-bridge-token-fix.md.
_SDK_LAUNCH_ATTEMPTS = max(1, int(os.environ.get("CURSOR_SDK_LAUNCH_ATTEMPTS", "3")))
_SDK_LAUNCH_BACKOFFS_S = (2.0, 5.0)

# Bridge handshake deadline — deliberately NOT _SDK_TIMEOUT_S. launch_bridge only
# spawns the bridge subprocess and completes discovery; the 1800s run/wait budget
# is for the agent's work. Sharing it meant a bridge that never armed could hold
# the exclusive write lease for half an hour before any deadline noticed, and the
# heartbeat that would reveal it does not start until launch returns
# (_start_heartbeat, below). Healthy launch-to-first-toolcall measures ~13s.
# A launch timeout is not a pre-discovery transient, so it fails on attempt 1
# rather than consuming the retry ladder.
_SDK_LAUNCH_TIMEOUT_S = float(os.environ.get("CURSOR_SDK_LAUNCH_TIMEOUT", "180"))

# Deadline for taking the FIFO capacity slot inside a gated dispatch. Reaching
# _run_sdk_dispatch_gated means the ledger already named this dispatch the write
# lease holder, and the nest-park path transfers capacity before the gated run,
# so a correct system acquires in ~0s. A longer wait is ledger/gate split-brain
# and must fail loudly: this await sits upstream of the outer watchdog, so an
# unbounded one is invisible to every timeout and every reap.
_SDK_SLOT_ACQUIRE_TIMEOUT_S = float(
    os.environ.get("CURSOR_SDK_SLOT_ACQUIRE_TIMEOUT", "300")
)
_PRE_DISCOVERY_TRANSIENT_MARKERS = ("before discovery", "--tool-callback-auth-token")


def _is_pre_discovery_transient(exc: BaseException) -> bool:
    """True iff exc is the safe-to-retry pre-discovery bridge launch transient."""
    msg = str(exc)
    return any(marker in msg for marker in _PRE_DISCOVERY_TRANSIENT_MARKERS)


def _config(request: Request) -> WorkerConfig:
    return getattr(request.app.state, "worker_config", _CONFIG)


def _mark_lane_b_abandon_disposition(*, dispatch_id: str, source_repo: Path) -> None:
    from services.git_integration_worker.cursor_sdk_lane_b_disposition import (
        mark_lane_b_disposition_for_dispatch,
    )

    mark_lane_b_disposition_for_dispatch(
        dispatch_id=dispatch_id,
        source_repo=source_repo,
        reason="abandoned",
    )


def _controller(request: Request) -> WorkAdmissionController:
    controller = getattr(request.app.state, "admission_controller", None)
    if controller is None:
        # Lifespan didn't run (some test transports skip it); construct a lazy
        # controller bound to the ledger singleton so the route still functions.
        controller = WorkAdmissionController(
            ledger=CursorDispatchLedger.instance(),
            worker_id="lazy",
            pid=0,
            worker_started_at="lazy",
        )
        request.app.state.admission_controller = controller
    return controller


def _draining_response(exc: Draining503) -> JSONResponse:
    """503 envelope + ``Retry-After`` for a dispatch rejected by drain."""
    return JSONResponse(
        status_code=503,
        content=error_envelope(
            code="GIT_WORKER_DRAINING",
            message=str(exc),
            source="gateway",
            retryable=True,
            data={"retry_after_s": _DRAIN_RETRY_AFTER_S},
        ),
        headers={"Retry-After": str(_DRAIN_RETRY_AFTER_S)},
    )


def _caller_explicitly_set_read_only(req: CursorDispatchRequest) -> bool:
    """True when the caller supplied ``read_only`` (omission is not explicit)."""
    return "read_only" in req.model_fields_set


def _effective_read_only(req: CursorDispatchRequest, contract: str) -> bool:
    """Resolve lease-exempt read-only intent once after contract classification."""
    if _caller_explicitly_set_read_only(req):
        return req.read_only
    if contract == "implement":
        return False
    if contract == "consult":
        return True
    if contract == "light-bounded":
        return False
    return False


def _emit_enriched_queued(
    *,
    req: CursorDispatchRequest,
    cached: CursorDispatchResponse,
    source_repo_str: str,
    packet_text: str,
    lease_key: str,
) -> None:
    """Emit association + ``admitted_via`` on ``worker.queued`` for all admits."""
    association = build_dispatch_association_fields(req=req, packet_text=packet_text)
    emit_sdk_worker_queued(
        dispatch_id=cached.dispatch_id,
        thread_id=cached.thread_id,
        source_repo=source_repo_str,
        queue_position=cached.queue_position,
        holder_dispatch_id=cached.holder_dispatch_id,
        holder_thread_id=cached.holder_thread_id,
        holder_resolved_model=cached.holder_resolved_model,
        holder_subject_preview=cached.holder_subject_preview,
        resolved_model=cached.model_id,
        execution_id=req.execution_id,
        admitted_via=req.admitted_via,
        asked_by=association["asked_by"],
        purpose=association["purpose"],
        story_id=association["story_id"],
        topic=association["topic"],
        nest_under=association["nest_under"],
        packet_kind=association["packet_kind"],
        model_knobs_requested=_stamp_model_knobs_requested(cached.model_id, req.model_knobs),
        queued_on=f"write_lease:{lease_key}",
    )


def _maybe_emit_giw_dispatched(
    *,
    req: CursorDispatchRequest,
    packet_text: str,
) -> None:
    """Emit GIW ``worker.dispatched`` only for nested ``admitted_via=cursor-auto``."""
    if req.admitted_via != "cursor-auto":
        return
    association = build_dispatch_association_fields(req=req, packet_text=packet_text)
    emit_sdk_worker_dispatched(
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        execution_id=req.execution_id,
        request_id=req.request_id,
        admitted_via=req.admitted_via,
        asked_by=association["asked_by"],
        purpose=association["purpose"],
        story_id=association["story_id"],
        topic=association["topic"],
        nest_under=association["nest_under"],
        packet_kind=association["packet_kind"],
        model_knobs_requested=_stamp_model_knobs_requested(req.model, req.model_knobs),
    )


def _stamp_model_knobs_requested(
    model: str,
    overrides: Mapping[str, str] | None,
) -> dict[str, str] | None:
    """Project omit-path defaults onto admit knobs for observability stamps."""
    bare = resolve_cursor(model).model_id
    stamped = effective_knobs(bare, overrides)
    return stamped or None


_DISPATCH_ROUTE = "/api/v1/cursor/dispatch"


def _reject_pre_admission(
    req: CursorDispatchRequest,
    *,
    worker_error_code: str,
    failure_layer: FailureLayer,
    http_status: int,
    detail_summary: str,
    invalid_fields: list[str] | None = None,
    retryable: bool | None = None,
    validation_stage: str = "pre_admission",
    extra_data: dict[str, Any] | None = None,
) -> JSONResponse:
    envelope = build_dispatch_error_envelope(
        execution_id=req.execution_id,
        thread_id=req.thread_id,
        dispatch_id=req.dispatch_id,
        failure_layer=failure_layer,
        http_status=http_status,
        worker_error_code=worker_error_code,
        route=_DISPATCH_ROUTE,
        method="POST",
        detail_summary=detail_summary,
        invalid_fields=invalid_fields,
        retryable=retryable,
        validation_stage=validation_stage,
    )
    emit_git_worker_dispatch_rejected(envelope)
    log_dispatch_rejection(envelope)
    content_data = extra_data if extra_data is not None else None
    return JSONResponse(
        status_code=http_status,
        content=error_envelope(
            code=worker_error_code,
            message=detail_summary,
            source="gateway",
            retryable=retryable,
            data=content_data,
        ),
    )


def _read_packet_text(req: CursorDispatchRequest, source_repo: Path) -> str:
    if req.message:
        return req.message
    if req.packet_path is None:
        raise ValueError("dispatch requires either message or packet_path")
    rel = req.packet_path.strip()
    if rel.startswith("/") or ".." in Path(rel).parts:
        raise ValueError(f"packet_path must be workspaces-relative: {rel!r}")
    packet = (source_repo / rel).resolve()
    if not packet.is_relative_to(source_repo.resolve()):
        raise ValueError(f"packet_path escapes source_repo: {rel!r}")
    if not packet.is_file():
        raise ValueError(f"packet_path not found: {rel!r}")
    return packet.read_text(encoding="utf-8")


def _branch_debt_refusal(req: CursorDispatchRequest) -> str | None:
    """Last rung: refuse a Lane-B lane that ignored its aged debt past the horizon."""
    if wire_lane_explicit(req) != "B":
        return None
    from services.git_integration_worker.cursor_sdk_branch_debt_escalation import (
        debt_admit_refusal,
    )

    return debt_admit_refusal(req.thread_id)


def _lane_branch_standing(req: CursorDispatchRequest) -> dict[str, Any]:
    """Branch obligation plus the lane's open debt, for the admit response.

    Shown at the moment a seat decides to dispatch again, which is where the
    outstanding residue can still change the decision.
    """
    if wire_lane_explicit(req) != "B" or not req.thread_id:
        return {}
    from services.git_integration_worker.cursor_sdk_branch_debt import (
        open_debts_for_thread,
    )
    from services.git_integration_worker.cursor_sdk_worktree import lane_branch_name

    try:
        debts = open_debts_for_thread(req.thread_id)
    except Exception as exc:  # standing is advisory; never block admit on it
        logger.warning(
            "lane debt standing unavailable thread_id=%s: %s", req.thread_id, exc
        )
        return {"lane_branch": lane_branch_name(req.thread_id)}
    return {
        "lane_branch": lane_branch_name(req.thread_id),
        "lane_open_debts": len(debts),
        "lane_debt_branches": [debt.branch_name for debt in debts] or None,
    }


def _resolve_prompt(req: CursorDispatchRequest, source_repo: Path) -> str:
    packet_text = _read_packet_text(req, source_repo)
    inferred_contract = None if req.message else infer_contract_from_text(packet_text)
    lane = "B" if wire_lane_explicit(req) == "B" else None
    lane_branch = None
    if lane == "B" and req.thread_id:
        from services.git_integration_worker.cursor_sdk_worktree import (
            lane_branch_name,
        )

        lane_branch = lane_branch_name(req.thread_id)
    preamble = resolve_prompt_preamble(
        handoff_contract=req.handoff_contract,
        prompt_preamble=req.prompt_preamble,
        inferred_contract=inferred_contract,
        lane=lane,
        existing_text=packet_text,
        lane_branch=lane_branch,
        dispatch_id=req.dispatch_id,
        has_packet_path=req.packet_path is not None,
        caller_agent=req.caller_agent,
    )
    return f"{preamble}{packet_text}"


async def _rollback_lane_b_mint_if_needed(
    *,
    dispatch_id: str,
    thread_id: str,
    source_repo: Path,
    minted_lane_b: bool,
    reason: str,
) -> None:
    if not minted_lane_b:
        return
    await asyncio.to_thread(
        prune_dispatch_worktree,
        dispatch_id=dispatch_id,
        source_repo=source_repo,
    )
    if lookup_dispatch_worktree(dispatch_id=dispatch_id) is None:
        emit_sdk_lane_b_mint_rolled_back(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            reason=reason,
        )


_dispatch_env = threading.local()
_BRIDGE_ENV_PATCH_INSTALLED = False
_PATH_PREPEND_KEY = "__CURSOR_SDK_PATH_PREPEND__"


def _dispatch_env_overlay() -> dict[str, str] | None:
    return getattr(_dispatch_env, "overrides", None)


def _install_bridge_env_patch() -> None:
    """Overlay the per-dispatch HOME/venv onto the bridge subprocess env.

    cursor-sdk 0.1.8 ``Bridge.launch`` builds the subprocess env from
    ``_bridge_subprocess_env()`` (``dict(os.environ)`` + SDK setdefaults)
    synchronously on the caller thread before ``Popen``. We wrap that function
    so it overlays the calling thread's dispatch override (HOME, VIRTUAL_ENV,
    PATH-prepend) read from thread-local storage, without mutating
    process-global ``os.environ``. Idempotent; patches the sync module global
    and the async module's imported binding.
    """
    global _BRIDGE_ENV_PATCH_INSTALLED
    if _BRIDGE_ENV_PATCH_INSTALLED:
        return

    from cursor_sdk import _bridge as _sdk_bridge

    _orig_env = _sdk_bridge._bridge_subprocess_env

    def _bridge_subprocess_env_with_overlay() -> Mapping[str, str]:
        env = dict(_orig_env())
        overrides = _dispatch_env_overlay()
        if overrides:
            home = overrides.get("HOME")
            if home is not None:
                env["HOME"] = home
            venv = overrides.get("VIRTUAL_ENV")
            if venv is not None:
                env["VIRTUAL_ENV"] = venv
            prepend = overrides.get(_PATH_PREPEND_KEY)
            if prepend is not None:
                cur = env.get("PATH")
                env["PATH"] = f"{prepend}{os.pathsep}{cur}" if cur else prepend
            dispatch_id = overrides.get("CURSOR_SDK_DISPATCH_ID")
            if dispatch_id is not None:
                env["CURSOR_SDK_DISPATCH_ID"] = dispatch_id
            for key, value in overrides.items():
                if key.startswith("GIT_"):
                    env[key] = value
        return env

    _sdk_bridge._bridge_subprocess_env = _bridge_subprocess_env_with_overlay
    try:
        from cursor_sdk import _async_bridge as _sdk_async_bridge

        _sdk_async_bridge._bridge_subprocess_env = _bridge_subprocess_env_with_overlay
    except Exception:  # async bridge optional; the worker uses the sync path
        logger.debug("cursor-sdk async bridge env patch skipped (module absent)")

    _BRIDGE_ENV_PATCH_INSTALLED = True
    logger.info(
        "cursor-sdk bridge subprocess-env patch installed "
        "(thread-confined HOME overlay; no os.environ mutation)"
    )


@contextmanager
def _dispatch_home_overlay(
    home: Path,
    *,
    repo_venv: Path | None = None,
    real_home: Path | str | None = None,
    dispatch_id: str | None = None,
):
    """Thread-confined HOME/venv overlay for one dispatch.

    Records the override in thread-local storage read by the patched
    ``_bridge_subprocess_env`` during ``Client.launch_bridge`` (same thread).
    No ``os.environ`` mutation and no lock: each dispatch runs in its own
    ``asyncio.to_thread`` worker thread, so overrides never collide and a
    timed-out orphan thread cannot leak HOME into a newly admitted dispatch.
    """
    overrides: dict[str, str] = {"HOME": str(home)}
    if dispatch_id is not None:
        overrides["CURSOR_SDK_DISPATCH_ID"] = dispatch_id
        overrides.update(dispatch_git_env_vars(dispatch_id))
    if repo_venv is not None:
        overrides["VIRTUAL_ENV"] = str(repo_venv)
        overrides[_PATH_PREPEND_KEY] = build_dispatch_path_prepend(
            repo_venv, real_home=real_home
        )
    prev = getattr(_dispatch_env, "overrides", None)
    _dispatch_env.overrides = overrides
    try:
        yield
    finally:
        _dispatch_env.overrides = prev


_install_bridge_env_patch()


class SdkRunAbortedError(RuntimeError):
    """SDK run aborted mid-flight (e.g. bridge ReadTimeout) — carries forensics.

    Friction 23050: a bridge death after minutes of real work must not destroy
    knowledge of what the run did. The wrapper preserves partial stream-capture
    state so the failure envelope can report elapsed time, tool-call progress,
    and the bridge state-root — and flag that side effects (browser automation,
    remote shell legs) may have continued or partially applied.
    """

    def __init__(self, message: str, *, forensics: dict[str, Any]) -> None:
        super().__init__(message)
        self.forensics = forensics


class _LiveToolCallCounter:
    """Monotonic tool-call counter shared between stream capture and heartbeat.

    ``_n`` counts every emitted tool-call observation (heartbeat
    ``tool_call_count``). ``last_progress_at`` advances only on successful
    terminal ``status == \"completed\"`` observations — the sole idle-clock
    reset site for the outer watchdog.
    """

    __slots__ = ("_last_progress_at", "_n", "_now_fn")

    def __init__(self, *, now_fn: Callable[[], float] | None = None) -> None:
        self._now_fn = now_fn or time.monotonic
        self._n = 0
        self._last_progress_at = self._now_fn()

    def bump(self, observation: object = None) -> None:
        self._n += 1
        if getattr(observation, "status", None) == "completed":
            self._last_progress_at = self._now_fn()

    def value(self) -> int:
        return self._n

    def last_progress_at(self) -> float:
        return self._last_progress_at

    def since_last_progress_s(self) -> float:
        return self._now_fn() - self._last_progress_at


async def _idle_deadline_wait(
    worker_task: asyncio.Task[Any],
    *,
    live_counter: _LiveToolCallCounter,
    idle_budget_s: float,
    now_fn: Callable[[], float] | None = None,
    wait_fn: Callable[
        [set[asyncio.Task[Any]], float],
        Awaitable[tuple[set[asyncio.Task[Any]], set[asyncio.Task[Any]]]],
    ]
    | Callable[
        ...,
        Awaitable[tuple[set[asyncio.Task[Any]], set[asyncio.Task[Any]]]],
    ]
    | None = None,
) -> bool:
    """Wait for ``worker_task`` with an idle-since-progress deadline.

    Returns ``True`` when no successful tool call occurred within
    ``idle_budget_s`` of ``live_counter.last_progress_at()``. Returns
    ``False`` when the worker task completes first.
    """
    now = now_fn or live_counter._now_fn
    wait = wait_fn or asyncio.wait
    while True:
        if worker_task.done():
            return False
        current = now()
        deadline = live_counter.last_progress_at() + idle_budget_s
        if current >= deadline:
            return True
        slice_s = min(deadline - current, _SDK_IDLE_WAIT_SLICE_S)
        done, _ = await wait({worker_task}, timeout=slice_s)
        if done:
            return False


def _start_heartbeat(
    *,
    dispatch_id: str,
    thread_id: str,
    resolved_model: str,
    execution_id: str | None = None,
    tool_call_count_fn: Callable[[], int] | None = None,
) -> tuple[Thread, _ThreadEvent]:
    stop = _ThreadEvent()
    started = time.monotonic()

    def _loop() -> None:
        while not stop.wait(_SDK_HEARTBEAT_S):
            if is_dispatch_orphaned(dispatch_id=dispatch_id):
                break
            elapsed = time.monotonic() - started
            try:
                emit_sdk_worker_progress(
                    dispatch_id=dispatch_id,
                    thread_id=thread_id,
                    resolved_model=resolved_model,
                    elapsed_s=elapsed,
                    tool_call_count=(
                        tool_call_count_fn() if tool_call_count_fn is not None else 0
                    ),
                    execution_id=execution_id,
                )
                CursorDispatchLedger.instance().bump_heartbeat(dispatch_id=dispatch_id)
            except Exception as exc:  # heartbeat must never kill the dispatch
                logger.warning(
                    "sdk heartbeat emit failed: dispatch_id=%s err=%s",
                    dispatch_id,
                    exc,
                )

    t = Thread(target=_loop, name=f"sdk-hb-{dispatch_id}", daemon=True)
    t.start()
    return t, stop


def _run_sdk_sync(
    *,
    source_repo: Path,
    binding: CaptureBinding | None = None,
    dispatch_workspace: Path,
    prompt: str,
    config_model_id: str,
    selection_overrides: dict[str, str] | None,
    dispatch_id: str,
    thread_id: str,
    resolved_model: str,
    execution_id: str | None = None,
    gate_loop: asyncio.AbstractEventLoop,
    live_counter: _LiveToolCallCounter | None = None,
    handoff_contract: str | None = None,
) -> SdkRunOutcome:
    # Pin operator home via passwd — never trust process HOME (may be a leaked
    # dispatch overlay; CURSOR_VENV_CONFIG / agent-bus:6468).
    real_home = operator_real_home()
    resume_ctx = load_resume_run_context(dispatch_id=dispatch_id)
    if resume_ctx is not None:
        dispatch_home = dispatch_home_path(resume_ctx.resume_of)
        if not dispatch_home.is_dir():
            dispatch_home = setup_cursor_dispatch_home(
                resume_ctx.resume_of, real_home=real_home
            )
        parent = load_parent_row(
            CursorDispatchLedger.instance(), parent_id=resume_ctx.resume_of
        )
        store_path = record_resolved_store_roots(
            parent_id=resume_ctx.resume_of,
            child_id=dispatch_id,
            parent_state_root=parent.state_root if parent else resume_ctx.state_root,
        )
        bridge_state = Path(store_path or resume_ctx.state_root)
    else:
        dispatch_home = setup_cursor_dispatch_home(dispatch_id, real_home=real_home)
        bridge_state = dispatch_home / "bridge-state"
        bridge_state.mkdir(parents=True, exist_ok=True)
        CursorDispatchLedger.instance().record_state_root(
            dispatch_id=dispatch_id, state_root=str(bridge_state)
        )
    repo_venv = resolve_repo_venv(real_home=real_home)
    validate_repo_venv(repo_venv)
    try:
        config = resolve_cursor(config_model_id)
        selection = build_model_selection(config, selection_overrides)
        parity = validate_dispatch_context(source_repo)
        knob_summary = {p.id: p.value for p in selection.params}
        logger.info(
            "cursor sdk dispatch start: dispatch_id=%s model=%s knobs=%s parity=%s",
            dispatch_id,
            config.model_id,
            knob_summary,
            parity,
        )
        substrate_ctx = SubstrateDispatchContext(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
        )
        agent_options = build_agent_options(
            source_repo,
            dispatch_workspace,
            selection,
            real_home=real_home,
            substrate_ctx=substrate_ctx,
            state_root=str(bridge_state),
            handoff_contract=handoff_contract,
        )

        with _dispatch_home_overlay(
            dispatch_home,
            repo_venv=repo_venv,
            real_home=real_home,
            dispatch_id=dispatch_id,
        ):
            client = None
            for attempt in range(_SDK_LAUNCH_ATTEMPTS):
                try:
                    client = Client.launch_bridge(
                        _SDK_BRIDGE_BIN,
                        workspace=str(dispatch_workspace),
                        state_root=str(bridge_state),
                        timeout=_SDK_LAUNCH_TIMEOUT_S,
                        # Friction 23057: without this the SDK's default
                        # 600s stream read timeout kills long silent tool
                        # legs despite healthy heartbeats.
                        client_timeout=_SDK_CLIENT_TIMEOUT,
                        local=agent_options.local,
                    )
                    break
                except Exception as launch_exc:  # noqa: BLE001
                    is_last = attempt + 1 >= _SDK_LAUNCH_ATTEMPTS
                    if is_last or not _is_pre_discovery_transient(launch_exc):
                        raise
                    backoff = _SDK_LAUNCH_BACKOFFS_S[
                        min(attempt, len(_SDK_LAUNCH_BACKOFFS_S) - 1)
                    ]
                    logger.warning(
                        "cursor sdk bridge pre-discovery transient: "
                        "dispatch_id=%s attempt=%d/%d err=%s; retrying in %.1fs",
                        dispatch_id,
                        attempt + 1,
                        _SDK_LAUNCH_ATTEMPTS,
                        launch_exc,
                        backoff,
                    )
                    time.sleep(backoff)
            register_active_client(dispatch_id=dispatch_id, client=client)
            bridge_tap = start_bridge_stderr_drain(
                dispatch_id=dispatch_id,
                thread_id=thread_id,
                client=client,
            )
            if live_counter is None:
                live_counter = _LiveToolCallCounter()
            hb_thread, hb_stop = _start_heartbeat(
                dispatch_id=dispatch_id,
                thread_id=thread_id,
                resolved_model=resolved_model,
                execution_id=execution_id,
                tool_call_count_fn=live_counter.value,
            )
            run_started = time.monotonic()
            agent = None
            run = None
            stream_capture = None

            def _abort_forensics(exc: BaseException) -> dict[str, Any]:
                # Friction 23050: preserve knowledge of a mid-flight bridge death.
                last_calls: list[dict[str, str]] = []
                if stream_capture is not None:
                    last_calls = [
                        {"tool_name": tc.tool_name, "status": tc.status}
                        for tc in stream_capture.tool_calls[-3:]
                    ]
                return {
                    "cause": f"{type(exc).__name__}: {exc}",
                    "elapsed_s": round(time.monotonic() - run_started, 1),
                    "stream_tool_call_count": live_counter.value(),
                    "last_tool_calls": last_calls,
                    "state_root": str(bridge_state),
                    "agent_id": getattr(agent, "id", None),
                    "run_id": getattr(run, "id", None),
                    # Assertion 31706: a refused connection only says the bridge
                    # is gone. Its exit code and dying stderr say why.
                    **bridge_exit_snapshot(bridge_tap),
                    "note": (
                        "bridge failure, not verified run death — the underlying "
                        "cursor-agent and any remote side effects (browser "
                        "automation, ssh legs) may have continued or partially "
                        "applied; verify outcome independently. Per-call telemetry: "
                        "frontier.sdk.worker.{progress,toolcall} events for this "
                        "dispatch_id."
                    ),
                }

            try:
                agent, run = start_or_resume_agent(
                    client=client,
                    agent_options=agent_options,
                    prompt=prompt,
                    resume_ctx=resume_ctx,
                )
                # Local bridge Send rejects Idempotency-Key (cloud-only in SDK v1).
                register_live_run(
                    dispatch_id=dispatch_id,
                    thread_id=thread_id,
                    source_repo=str(source_repo),
                    run=run,
                )
                CursorDispatchLedger.instance().record_sdk_identity(
                    dispatch_id=dispatch_id,
                    agent_id=sdk_agent_id_from_agent(agent),
                    run_id=getattr(run, "id", None),
                )
                # Drain the live stream BEFORE wait() — safe/additive: a fully
                # consumed stream leaves Run._terminal_result cached, so wait()
                # below returns it directly instead of issuing a second RPC.
                # This is the ONLY channel that can see a tool call the runtime
                # truncates/rejects upstream of conversation() (friction 21654).
                stream_capture = observe_run_stream(
                    run,
                    dispatch_id=dispatch_id,
                    thread_id=thread_id,
                    resolved_model=resolved_model,
                    execution_id=execution_id,
                    on_tool_call=live_counter.bump,
                )
                result = run.wait()
                usage_record = finalize_dispatch_usage(
                    stream_capture, run=run, result=result
                )
                stream_capture = StreamCapture(
                    tool_calls=stream_capture.tool_calls,
                    usage=usage_record.usage,
                    usage_capture_status=usage_record.usage_capture_status,
                    usage_total_derived=False,
                    sdk_request_id=stream_capture.sdk_request_id,
                    request_id_source=stream_capture.request_id_source,
                )
                persist_dispatch_usage(
                    CursorDispatchLedger.instance(),
                    dispatch_id=dispatch_id,
                    record=usage_record,
                )
                assert run is not None and result is not None
                stream_capture = finalize_request_id_capture(
                    stream_capture, run=run, result=result
                )
                post_wait = read_post_wait_snapshot(
                    run=run,
                    agent=agent,
                    result=result,
                    poll_fallback=True,
                )
                turns = post_wait.conversation
                artifact_paths = list(post_wait.artifact_paths)
                capture_branch = classify_mcp_capture_branch(turns)
                effects_manifest = build_effects_manifest(
                    dispatch_id=dispatch_id,
                    thread_id=thread_id,
                    turns=turns,
                    capture_branch=capture_branch,
                    contract=handoff_contract,
                )
                effects_manifest = merge_stream_tool_calls(
                    effects_manifest,
                    stream_capture.tool_calls,
                    source_repo=source_repo,
                )
                effects_manifest = merge_stream_subagent_calls(
                    effects_manifest,
                    stream_capture.tool_calls,
                )
                if artifact_paths:
                    effects_manifest = merge_artifact_paths(
                        effects_manifest,
                        artifact_paths,
                        source_repo=source_repo,
                    )
                conversation_tool_call_count = count_tool_calls(turns)
                tool_call_count = (
                    stream_capture.tool_call_count or conversation_tool_call_count
                )
                if stream_capture.tool_call_count != conversation_tool_call_count:
                    logger.info(
                        "cursor sdk stream/conversation tool-call count delta: "
                        "dispatch_id=%s stream=%d conversation=%d delta=%d "
                        "truncated_calls=%d",
                        dispatch_id,
                        stream_capture.tool_call_count,
                        conversation_tool_call_count,
                        stream_capture.tool_call_count - conversation_tool_call_count,
                        len(stream_capture.truncated_tool_calls),
                    )
                sdk_request_id = stream_capture.sdk_request_id
                request_id_source = stream_capture.request_id_source or "absent"
                git_probe = probe_run_git_info(
                    path_label=LOCAL_BRIDGE_PATH_LABEL,
                    result=result,
                )
                extra_reasons = git_probe_degraded_reasons(
                    probe=git_probe,
                    sdk_git=post_wait.sdk_git,
                    source_repo=source_repo,
                )
                stream_deviations = stream_only_effect_deviations(
                    stream_tool_calls=stream_capture.tool_calls,
                    conversation_tool_call_count=conversation_tool_call_count,
                )
                return SdkRunOutcome(
                    body=resolve_run_body(result.result, turns),
                    status=str(result.status),
                    duration_ms=result.duration_ms,
                    tool_call_count=tool_call_count,
                    effects_manifest=effects_manifest,
                    capture_branch=capture_branch,
                    tool_calls=stream_capture.tool_calls,
                    usage=stream_capture.usage,
                    usage_capture_status=stream_capture.usage_capture_status,
                    sdk_request_id=sdk_request_id,
                    request_id_source=request_id_source,
                    sdk_run_id=getattr(run, "id", None) or getattr(result, "id", None),
                    sdk_agent_id=(
                        getattr(agent, "id", None) or getattr(result, "agent_id", None)
                    ),
                    degraded_reasons=extra_reasons,
                    sdk_git=post_wait.sdk_git,
                    stream_only_deviations=stream_deviations,
                )
            except BaseException as exc:
                sdk_request_id, request_id_source = request_id_from_sdk_error(exc)
                if sdk_request_id:
                    logger.info(
                        "cursor sdk error request_id captured: dispatch_id=%s "
                        "sdk_request_id=%s source=%s",
                        dispatch_id,
                        sdk_request_id,
                        request_id_source,
                    )
                # Friction 23050: wrap any mid-flight abort (APITimeoutError /
                # bridge ReadTimeout / dying SDK) with partial forensics so the
                # failure envelope does not destroy all knowledge of the run.
                raise SdkRunAbortedError(
                    str(exc), forensics=_abort_forensics(exc)
                ) from exc
            finally:
                hb_stop.set()
                hb_thread.join(timeout=5.0)
                unregister_live_run(dispatch_id=dispatch_id)
                stop_bridge_stderr_drain(bridge_tap)
                if client is not None:
                    client.close()
                clear_dispatch_orphan_state(dispatch_id=dispatch_id)
    finally:
        # Release the capacity slot from this thread — not from the async
        # coroutine — so a timed-out orphan thread holds the slot until exit.
        # A1: if a parked parent waits for this child, transfer (no waiter wake).
        release_or_restore_for_child_sync(gate_loop, dispatch_id=dispatch_id)


async def _mark_terminal_and_promote(
    *,
    dispatch_id: str,
    terminal_status: str,
    controller: WorkAdmissionController,
    request: Request | None = None,
    emit_tag: str,
) -> None:
    """Mark terminal, release/restore lease, prune Lane-B worktree, promote FIFO head."""
    disposition = await release_or_restore_for_child(dispatch_id=dispatch_id)
    ledger = CursorDispatchLedger.instance()
    lease_key = await asyncio.to_thread(
        ledger.mark_terminal,
        dispatch_id=dispatch_id,
        terminal_status=terminal_status,
    )
    if not terminal_emitted(dispatch_id):
        orphan_row = await asyncio.to_thread(
            load_ledger_row, ledger, dispatch_id=dispatch_id
        )
        thread_id = orphan_row.thread_id if orphan_row is not None else dispatch_id
        execution_id = (
            (orphan_row.execution_id or dispatch_id)
            if orphan_row is not None
            else dispatch_id
        )
        emit_sdk_worker_unclassified_terminal(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            execution_id=execution_id,
            worker_error_code=emit_tag,
            detail_summary=terminal_status,
        )
    cfg = _config(request) if request is not None else _CONFIG
    if terminal_status == "failed":
        await asyncio.to_thread(
            _mark_lane_b_abandon_disposition,
            dispatch_id=dispatch_id,
            source_repo=cfg.source_repo,
        )
    await asyncio.to_thread(
        maybe_prune_worktree_on_terminal,
        dispatch_id=dispatch_id,
        source_repo=cfg.source_repo,
    )
    if disposition == "restored":
        # Parked parent regained capacity — siblings stay queued (Q4).
        return
    emit_write_lease_released(dispatch_id=dispatch_id, source_repo=lease_key)
    if lease_key and not controller.is_draining():
        await _promote_queued_for_lease(
            lease_key=lease_key,
            controller=controller,
            request=request,
        )


async def _promote_queued_for_lease(
    *,
    lease_key: str,
    controller: WorkAdmissionController,
    request: Request | None = None,
) -> None:
    ledger = CursorDispatchLedger.instance()
    promoted = await asyncio.to_thread(
        ledger.promote_next_queued,
        lease_key=lease_key,
        worker_instance=controller.worker_id,
    )
    if promoted is None:
        return
    emit_write_lease_promoted(
        dispatch_id=promoted.dispatch_id,
        source_repo=promoted.source_repo or lease_key,
    )
    emit_write_lease_acquired(
        dispatch_id=promoted.dispatch_id,
        source_repo=promoted.source_repo or lease_key,
    )
    await _start_promoted_dispatch(
        promoted=promoted,
        controller=controller,
        request=request,
    )


async def _start_promoted_dispatch(
    *,
    promoted: PromotedDispatch,
    controller: WorkAdmissionController,
    request: Request | None,
) -> None:
    ledger = CursorDispatchLedger.instance()
    req = ledger.load_promoted_request(promoted)
    cfg = _config(request) if request is not None else _CONFIG
    contract = (promoted.contract or "consult").lower()
    binding = binding_for_dispatch(
        cfg=cfg,
        lease_key=promoted.lease_key or promoted.source_repo,
        dispatch_source_repo=Path(promoted.source_repo or cfg.source_repo).resolve(),
    )
    dispatch_workspace = resolve_promoted_workspace(
        lease_key=promoted.lease_key or promoted.source_repo,
        source_repo=cfg.source_repo,
        cfg=cfg,
    )
    # Friction 23001: baseline capture deferred into _run_sdk_dispatch_gated
    # (post slot acquisition) — also fixes misattribution where a queued
    # dispatch's baseline included none of its predecessor's edits.
    try:
        ticket = controller.try_admit(
            "cursor_sdk",
            op_id=promoted.dispatch_id,
            route="/api/v1/cursor/dispatch",
        )
    except Draining503:
        # Race: promote_next_queued ran before drain flipped; restore FIFO head.
        await release_or_restore_for_child(dispatch_id=promoted.dispatch_id)
        await asyncio.to_thread(
            ledger.demote_admitted_to_queued,
            dispatch_id=promoted.dispatch_id,
        )
        return
    bus = CursorBusClient()
    task = controller.create_tracked_task(
        _close_ticket_after(
            _run_sdk_dispatch_gated(
                req=req,
                source_repo=cfg.source_repo,
                binding=binding,
                dispatch_workspace=dispatch_workspace,
                bus=bus,
                controller=controller,
                contract=contract,
                worktree_isolated=req.worktree_isolated,
            ),
            controller=controller,
            op_id=promoted.dispatch_id,
        ),
        op_id=promoted.dispatch_id,
    )
    ledger.register_task(promoted.dispatch_id, task)
    ticket.mark_running()
    await asyncio.to_thread(ledger.mark_running, dispatch_id=promoted.dispatch_id)
    packet_text = req.message or ""
    if req.packet_path:
        packet_text = _read_packet_text(req, cfg.source_repo) or packet_text
    _maybe_emit_giw_dispatched(req=req, packet_text=packet_text)


async def reconcile_stale_leases(
    controller: WorkAdmissionController,
    *,
    reap_only: bool = False,
    worker_cfg: WorkerConfig | None = None,
) -> None:
    """Periodic sweeper: release stale lease holders and promote queued writers.

    ``reap_only`` keeps the reap half and drops the promote half. Used while
    draining, where clearing a wedged holder is exactly what lets the drain
    finish, but starting its queued successor would admit new work into a
    worker that is shutting down.
    """
    from services.git_integration_worker.cursor_sdk_gate import (
        reclaim_cross_lane_phantom_holders,
    )
    from services.git_integration_worker.cursor_sdk_land_lease import (
        reap_stale_land_leases,
    )

    reclaimed = await reclaim_cross_lane_phantom_holders()
    if reclaimed:
        logger.info("cross-lane phantom gate holders reclaimed=%s", reclaimed)
    await asyncio.to_thread(reap_stale_land_leases)
    cfg = worker_cfg or _CONFIG
    sweep = await asyncio.to_thread(
        reap_orphan_worktrees,
        source_repo=cfg.source_repo,
        worktree_root=cfg.worktree_root,
    )
    if sweep.reaped:
        logger.info(
            "orphan worktree reaper reaped=%d salvaged=%d branches_retained=%d "
            "branches_gc=%d stale_metadata_pruned=%s",
            sweep.reaped,
            sweep.salvaged,
            sweep.branches_retained,
            sweep.branches_gc,
            sweep.stale_metadata_pruned,
        )
    ledger = CursorDispatchLedger.instance()
    orphan_ids = await asyncio.to_thread(
        ledger.orphan_holders,
        threshold_s=_STALE_LEASE_S,
        dead_run_grace_s=_DEAD_RUN_GRACE_S,
        worker_instance=controller.worker_id,
        arming_timeout_s=_SDK_ARM_TIMEOUT_S,
    )
    repos: set[str] = set()
    for dispatch_id in orphan_ids:
        orphan_row = await asyncio.to_thread(
            load_ledger_row, ledger, dispatch_id=dispatch_id
        )
        lease_key = await reclaim_orphan_holder(ledger, dispatch_id=dispatch_id)
        if lease_key:
            repos.add(lease_key)
            prune_result = await asyncio.to_thread(
                salvage_restart_survivor_worktree,
                dispatch_id=dispatch_id,
                source_repo=cfg.source_repo,
            )
            if prune_result.pruned and (
                prune_result.salvaged or prune_result.branch_retained
            ):
                logger.info(
                    "stale-lease survivor worktree salvaged dispatch_id=%s "
                    "salvaged=%s branch_retained=%s",
                    dispatch_id,
                    prune_result.salvaged,
                    prune_result.branch_retained,
                )
            if orphan_row is not None and not terminal_emitted(dispatch_id):
                emit_restart_survivor_terminal(orphan_row, bridge_aborted=False)
            emit_write_lease_released(
                dispatch_id=dispatch_id,
                source_repo=lease_key,
                stale=True,
            )
    if reap_only:
        return
    for lease_key in repos:
        await _promote_queued_for_lease(
            lease_key=lease_key,
            controller=controller,
            request=None,
        )
    for lease_key in await asyncio.to_thread(queue_stall_lease_keys, ledger):
        emit_write_lease_queue_stalled(source_repo=lease_key)


async def stale_lease_sweeper(app: FastAPI) -> None:
    """Background task started at worker lifespan."""
    while True:
        await asyncio.sleep(_STALE_SWEEP_S)
        controller = getattr(app.state, "admission_controller", None)
        if controller is None:
            continue
        try:
            await reconcile_stale_leases(
                controller,
                reap_only=controller.is_draining(),
                worker_cfg=getattr(app.state, "worker_config", None),
            )
        except Exception as exc:  # sweeper must never kill the worker
            logger.warning("stale-lease sweeper failed: %s", exc)


async def bridge_sweeper(app: FastAPI) -> None:
    """Collect aged cursor-sdk bridges that outlived their dispatch.

    Sole caller of the sweep, and it sleeps before the first pass on purpose.
    ``startup_ledger_reconcile`` looks like the natural home, but tests invoke
    that directly (``test_cursor_sdk_restart_orphan``), which would let a unit
    test kill bridge processes on the host.
    """
    del app
    while True:
        await asyncio.sleep(_BRIDGE_SWEEP_S)
        try:
            result = await asyncio.to_thread(sweep_unowned_bridges)
        except Exception as exc:  # sweeper must never kill the worker
            logger.warning("bridge sweeper failed: %s", exc)
            continue
        if result.killed or result.kill_failed:
            logger.info(
                "bridge sweep scanned=%d killed=%s kill_failed=%s",
                result.scanned,
                result.killed,
                result.kill_failed,
            )


async def startup_ledger_reconcile(app: FastAPI) -> None:
    """Reconcile restart survivors: OS-reap bridges before lease release.

    For each ledger ``running`` orphan, reap via env∧bridge identity, emit
    honest ``bridge_aborted``, then release/restore and mark terminal; finally
    promote queued heads.
    """
    removed = await asyncio.to_thread(prune_stale_dispatch_homes)
    if removed:
        logger.info("startup dispatch_home prune removed=%d", removed)
    cfg: WorkerConfig = app.state.worker_config
    wt_sweep = await asyncio.to_thread(
        reap_orphan_worktrees,
        source_repo=cfg.source_repo,
        worktree_root=cfg.worktree_root,
    )
    if wt_sweep.reaped:
        logger.info(
            "startup orphan worktree reaper reaped=%d salvaged=%d branches_retained=%d",
            wt_sweep.reaped,
            wt_sweep.salvaged,
            wt_sweep.branches_retained,
        )
    from services.git_integration_worker.cursor_sdk_gate import (
        reclaim_cross_lane_phantom_holders,
    )

    reclaimed = await reclaim_cross_lane_phantom_holders()
    if reclaimed:
        logger.info("startup cross-lane phantom gate holders reclaimed=%s", reclaimed)
    ledger = CursorDispatchLedger.instance()
    controller = app.state.admission_controller
    # Snapshot before startup_reconcile mutates status — survivors it marks
    # failed would otherwise never reach running_orphans() and skip ES terminal.
    survivors = {orphan.dispatch_id: orphan for orphan in ledger.running_orphans()}
    repos = await asyncio.to_thread(
        ledger.startup_reconcile, worker_instance=controller.worker_id
    )
    for orphan in ledger.running_orphans():
        survivors.setdefault(orphan.dispatch_id, orphan)
    for orphan in survivors.values():
        reap = await asyncio.to_thread(reap_orphan_bridge_os, orphan.dispatch_id)
        if reap.kill_failed:
            emit_sdk_restart_bridge_reap_failed(
                dispatch_id=orphan.dispatch_id,
                thread_id=orphan.thread_id,
            )
        await release_or_restore_for_child(dispatch_id=orphan.dispatch_id)
        survivor_prune = await asyncio.to_thread(
            salvage_restart_survivor_worktree,
            dispatch_id=orphan.dispatch_id,
            source_repo=cfg.source_repo,
        )
        if survivor_prune.pruned and (
            survivor_prune.salvaged or survivor_prune.branch_retained
        ):
            logger.info(
                "startup survivor worktree salvaged dispatch_id=%s salvaged=%s "
                "branch_retained=%s",
                orphan.dispatch_id,
                survivor_prune.salvaged,
                survivor_prune.branch_retained,
            )
        lease_key = await asyncio.to_thread(
            ledger.mark_terminal,
            dispatch_id=orphan.dispatch_id,
            terminal_status="failed",
        )
        emit_restart_survivor_terminal(orphan, bridge_aborted=reap.bridge_aborted)
        if lease_key:
            repos.append(lease_key)
    for lease_key in sorted(set(repos)):
        await _promote_queued_for_lease(
            lease_key=lease_key,
            controller=controller,
            request=None,
        )


async def _terminate_link(
    bus: CursorBusClient, *, thread_id: str, terminal_status: str
) -> None:
    result = await bus.terminate_dispatch(
        thread_id=thread_id, terminal_status=terminal_status
    )
    if result.status_code >= 400:
        logger.error(
            "cursor bus terminate failed: status=%s body=%s",
            result.status_code,
            result.body,
        )


async def _deliver_sdk_closeout(
    *,
    req: CursorDispatchRequest,
    source_repo: Path,
    binding: CaptureBinding | None = None,
    outcome: SdkRunOutcome,
    degraded_reason: str | None,
    bus: CursorBusClient,
    reply_to: str,
    work_item_ref: str | None,
    controller: WorkAdmissionController,
    packet_text: str = "",
    deliverables_expected: bool = False,
    light_bounded_expected_paths: tuple[str, ...] = (),
    extra_deviations: tuple[str, ...] = (),
    worktree_isolated: bool = False,
) -> None:
    from services.git_integration_worker.cursor_sdk_closeout.conductor_closeout_pager import (
        page_conductor_silence,
    )

    await page_conductor_silence(
        degraded_reason=degraded_reason,
        nest_under=req.nest_under,
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
    )
    baseline = await asyncio.to_thread(
        CursorDispatchLedger.instance().read_wt_baseline,
        dispatch_id=req.dispatch_id,
    )
    delivery = await prepare_closeout_delivery_async(
        source_repo=source_repo,
        binding=binding,
        dispatch_id=req.dispatch_id,
        outcome=outcome,
        degraded_reason=degraded_reason,
        thread_id=req.thread_id,
        work_item_ref=work_item_ref,
        baseline=baseline,
        packet_text=packet_text or None,
        deliverables_expected=deliverables_expected,
        light_bounded_expected_paths=light_bounded_expected_paths,
        execution_id=req.execution_id,
        extra_deviations=extra_deviations,
        worktree_isolated=worktree_isolated,
        resolved_model=req.model,
    )
    run_outcome = resolve_run_outcome_label(degraded_reason)
    if delivery.closeout_status.value == "partial":
        run_outcome = "degraded"
    duration_s = outcome.duration_ms / 1000.0
    completed_reasons = list(
        merge_degraded_reasons(degraded_reason, *outcome.degraded_reasons)
    )
    envelope_request_id = request_id_from_dispatch_id(req.dispatch_id)
    from systems.frontier_consult.story_wire import build_association_envelope

    association = build_association_envelope(
        purpose_body=packet_text or req.message,
        caller_agent=req.caller_agent,
        request_id=envelope_request_id,
        dispatch_id=req.dispatch_id,
        packet_path=req.packet_path,
    )
    association_fields = {
        "asked_by": association.asked_by,
        "purpose": association.purpose,
        "story_id": association.story_id,
        "admitted_via": req.admitted_via,
    }

    closeout_contract = (req.handoff_contract or "consult").lower()
    bus_result = await bus.reply(
        thread_id=req.thread_id,
        to_agent=reply_to,
        from_agent="cursor-sdk",
        subject=build_sdk_closeout_subject(req, contract=closeout_contract),
        body=delivery.body,
        allow_long_body=True,
    )

    if bus_result.status_code < 400:
        contract = (req.handoff_contract or "consult").lower()
        try:
            from systems.frontier_consult.cursor_sdk_role_delivery import (
                post_role_labeled_check_turn,
                resolve_delivery_from_role,
                should_bridge_cursor_check_review,
            )

            if should_bridge_cursor_check_review(
                contract=contract,
                resolved_model=req.model,
            ):
                delivery_role = resolve_delivery_from_role(req.model)
                if delivery_role:
                    bridge_source = (outcome.body or delivery.body or "").strip()
                    await post_role_labeled_check_turn(
                        thread_id=req.thread_id,
                        to_agent=reply_to,
                        delivery_from_role=delivery_role,
                        closeout_body=bridge_source,
                    )
        except Exception:
            logger.exception(
                "cursor check/review role bridge failed: dispatch_id=%s",
                req.dispatch_id,
            )
        # model_knobs_requested: effective/aligned knobs (omit-path defaults included),
        # projected from req.model + admit-time overrides — not raw caller wire alone.
        # Missing SDK requestId is an observability gap (R F-1), not a crash.
        # Emit with request_id_source=absent + degrade token so fleet join stays
        # diagnosable without aborting an otherwise successful closeout.
        if outcome.sdk_request_id is None:
            if "sdk_request_id_absent" not in completed_reasons:
                completed_reasons.append("sdk_request_id_absent")
            logger.warning(
                "cursor sdk completed without sdk_request_id: dispatch_id=%s "
                "request_id_source=%s",
                req.dispatch_id,
                outcome.request_id_source or "absent",
            )
        emit_sdk_worker_completed(
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            execution_id=req.execution_id,
            duration_s=duration_s,
            tool_call_count=outcome.tool_call_count,
            result_bytes=delivery.full_result_bytes,
            outcome=resolve_completion_outcome(
                run_outcome=run_outcome, delivery_ok=True
            ),
            resolved_model=req.model,
            model_knobs_requested=_stamp_model_knobs_requested(req.model, req.model_knobs),
            usage=outcome.usage,
            usage_capture_status=outcome.usage_capture_status,
            request_id=envelope_request_id,
            sdk_request_id=outcome.sdk_request_id,
            request_id_source=outcome.request_id_source or "absent",
            sdk_run_id=outcome.sdk_run_id,
            sdk_agent_id=outcome.sdk_agent_id,
            degraded_reasons=completed_reasons,
            **association_fields,
        )
        turn_number = extract_turn_number(bus_result.body)
        await emit_implement_closeout_trigger(
            body_json=delivery.body,
            source_ref=normalize_closeout_source_ref(
                work_item_ref or delivery.sidecar_ref
            ),
            idempotency_key=build_closeout_idempotency_key(
                execution_id=req.execution_id,
                thread_id=req.thread_id,
                turn_number=turn_number,
            ),
        )
        await _terminate_link(bus, thread_id=req.thread_id, terminal_status="completed")
        await _mark_terminal_and_promote(
            dispatch_id=req.dispatch_id,
            terminal_status="completed",
            controller=controller,
            emit_tag="CURSOR_CLOSEOUT_COMPLETED",
        )
        from services.git_integration_worker.cursor_sdk_packet import (
            extract_packet_kind_from_packet,
        )

        packet_kind = extract_packet_kind_from_packet(packet_text or "")
        if closeout_qualifies_for_resume_retain(
            closeout_body=delivery.body,
            packet_kind=packet_kind,
        ):
            await asyncio.to_thread(
                persist_resume_retain, dispatch_id=req.dispatch_id
            )
        return

    logger.error(
        "cursor bus reply failed: status=%s body=%s",
        bus_result.status_code,
        bus_result.body,
    )
    emit_sdk_worker_delivery_failed(
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        execution_id=req.execution_id,
        status_code=bus_result.status_code,
        result_bytes=delivery.full_result_bytes,
        sidecar_ref=delivery.sidecar_ref,
    )
    fallback_body = format_delivery_fallback_body(
        status_code=bus_result.status_code,
        sidecar_ref=delivery.sidecar_ref,
        result_bytes=delivery.full_result_bytes,
    )
    await bus.reply(
        thread_id=req.thread_id,
        to_agent=reply_to,
        from_agent="cursor-sdk",
        subject=f"cursor-sdk dispatch {req.dispatch_id} DELIVERY FAILED",
        body=fallback_body,
    )
    emit_sdk_worker_completed(
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        execution_id=req.execution_id,
        duration_s=duration_s,
        tool_call_count=outcome.tool_call_count,
        result_bytes=delivery.full_result_bytes,
        outcome=resolve_completion_outcome(run_outcome=run_outcome, delivery_ok=False),
        resolved_model=req.model,
        model_knobs_requested=_stamp_model_knobs_requested(req.model, req.model_knobs),
        usage=outcome.usage,
        usage_capture_status=outcome.usage_capture_status,
        request_id=envelope_request_id,
        sdk_request_id=outcome.sdk_request_id,
        request_id_source=outcome.request_id_source or "absent",
        sdk_run_id=outcome.sdk_run_id,
        sdk_agent_id=outcome.sdk_agent_id,
        degraded_reasons=completed_reasons,
        **association_fields,
    )
    await _terminate_link(bus, thread_id=req.thread_id, terminal_status="failed")
    await _mark_terminal_and_promote(
        dispatch_id=req.dispatch_id,
        terminal_status="failed",
        controller=controller,
        emit_tag="CURSOR_CLOSEOUT_DELIVERY_FAILED",
    )


async def _close_ticket_after(
    coro: Awaitable[None], *, controller: WorkAdmissionController, op_id: str
) -> None:
    """Run a gated dispatch coro, then close its admission ticket on-loop.

    The ticket is closed only after ``coro`` has fully completed — i.e. after the
    dispatch's ``ledger.mark_terminal`` on whichever path it exits — so the
    recomputed ``active_count`` no longer counts this dispatch and a 1->0
    transition emits ``git_worker.drain.completed`` exactly once. This runs as the
    body of the tracked dispatch task, so the close always happens on the loop.
    The ledger already holds the authoritative terminal status; the ticket's
    status is a cosmetic close marker.
    """
    try:
        await coro
    except BaseException:  # noqa: BLE001
        # Defense in depth: the gated coro finalizes its own failures; anything
        # escaping here would otherwise be swallowed by the tracked task with no
        # log (especially CancelledError), reproducing the silent-orphan signature.
        logger.exception("cursor sdk dispatch coro escaped finalize: op_id=%s", op_id)
        raise
    finally:
        controller.close_ticket(op_id, terminal_status="closed")


async def _run_sdk_dispatch_gated(
    *,
    req: CursorDispatchRequest,
    source_repo: Path,
    binding: CaptureBinding | None = None,
    dispatch_workspace: Path,
    bus: CursorBusClient,
    controller: WorkAdmissionController,
    contract: str = "consult",
    worktree_isolated: bool = False,
) -> None:
    reply_to = req.caller_agent or "dispatch"
    outer_timeout_s = _SDK_TIMEOUT_S + _SDK_TIMEOUT_BUFFER_S
    gate_loop = asyncio.get_running_loop()
    capacity_lane = sdk_dispatch_lane(
        caller_agent=req.caller_agent,
        dispatch_id=req.dispatch_id,
    )
    capacity_wait_emitted = False

    def _emit_capacity_wait() -> None:
        nonlocal capacity_wait_emitted
        if capacity_wait_emitted:
            return
        capacity_wait_emitted = True
        association = build_dispatch_association_fields(
            req=req,
            packet_text=_read_packet_text(req, source_repo)
            if req.packet_path
            else (req.message or ""),
        )
        emit_sdk_worker_queued(
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            source_repo=str(source_repo.resolve()),
            queue_position=None,
            execution_id=req.execution_id,
            resolved_model=req.model,
            admitted_via=req.admitted_via,
            asked_by=association["asked_by"],
            purpose=association["purpose"],
            story_id=association["story_id"],
            queued_on=f"capacity:{capacity_lane}",
        )

    # Acquire slot before spawning — released inside _run_sdk_sync finally block.
    # Bounded: everything below (including the outer watchdog) is downstream of
    # this await, so an unbounded wait here is unobservable — no heartbeat row,
    # no timeout event, no stale reap. Fail the dispatch instead of wedging the
    # write lease.
    try:
        await acquire_sdk_dispatch_slot(
            dispatch_id=req.dispatch_id,
            caller_agent=req.caller_agent,
            timeout=_SDK_SLOT_ACQUIRE_TIMEOUT_S,
            on_wait=_emit_capacity_wait,
        )
    except SdkSlotAcquireStallError as exc:
        logger.error(
            "cursor sdk capacity slot stalled on cross-lane phantom holder(s): "
            "dispatch_id=%s lane=%s waited=%.0fs misplaced=%s gate=%s",
            req.dispatch_id,
            exc.lane,
            exc.waited_s,
            exc.misplaced_holders,
            exc.gate_stats,
        )
        await _finalize_failed(
            req=req,
            bus=bus,
            reply_to=reply_to,
            controller=controller,
            code="CURSOR_SDK_SLOT_ACQUIRE_STALL",
            message=str(exc),
            subject_suffix="FAILED (slot acquire stall — cross-lane gate holder)",
            error="capacity slot acquire stall",
            retryable=True,
            data={
                "gate": exc.gate_stats,
                "lane": exc.lane,
                "misplaced_holders": exc.misplaced_holders,
            },
        )
        return
    except TimeoutError:
        logger.error(
            "cursor sdk capacity slot unavailable to the ledger's lease holder: "
            "dispatch_id=%s waited=%.0fs gate=%s",
            req.dispatch_id,
            _SDK_SLOT_ACQUIRE_TIMEOUT_S,
            sdk_dispatch_gate_stats(),
        )
        await _finalize_failed(
            req=req,
            bus=bus,
            reply_to=reply_to,
            controller=controller,
            code="CURSOR_SDK_SLOT_ACQUIRE_TIMEOUT",
            message=(
                "cursor-sdk dispatch holds the ledger write lease but could not "
                f"acquire the capacity slot within {_SDK_SLOT_ACQUIRE_TIMEOUT_S:.0f}s"
            ),
            subject_suffix="FAILED (slot acquire timeout)",
            error="capacity slot acquire timeout",
            retryable=True,
            data={"gate": sdk_dispatch_gate_stats()},
        )
        return

    # Friction 23001: capture the implement wt baseline here — after the FIFO
    # slot is held (predecessor edits are included) and off the admission HTTP
    # request path (caller gets 200/202 immediately; no Stargate read-timeout
    # 599 on slow dirty-checkout baselines).
    # cursor-auto maps operator implement → handoff_contract pure-mechanical
    # (wire_map.resolve_handoff_contract); both need admit_head for lane git_refs.
    if contract in ("implement", "pure-mechanical") or not _effective_read_only(
        req, contract
    ):
        write_tree = binding.write_tree if binding else source_repo
        baseline_map = await asyncio.to_thread(
            capture_wt_baseline_with_hashes,
            write_tree,
            mount_root=binding.mount_root if binding else None,
            repo_roots=binding.repo_roots if binding else None,
        )
        if baseline_map is not None:
            await asyncio.to_thread(
                CursorDispatchLedger.instance().set_wt_baseline,
                dispatch_id=req.dispatch_id,
                wt_baseline=json.dumps(baseline_map),
            )

    prompt = _resolve_prompt(req, source_repo)

    live_counter = _LiveToolCallCounter()
    worker_task = controller.create_tracked_task(
        asyncio.to_thread(
            _run_sdk_sync,
            source_repo=source_repo,
            binding=binding,
            dispatch_workspace=dispatch_workspace,
            prompt=prompt,
            config_model_id=req.model,
            selection_overrides=req.model_knobs,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            resolved_model=req.model,
            execution_id=req.execution_id,
            gate_loop=gate_loop,
            live_counter=live_counter,
            handoff_contract=contract,
        ),
        op_id=f"{req.dispatch_id}:worker",
    )

    timed_out = await _idle_deadline_wait(
        worker_task,
        live_counter=live_counter,
        idle_budget_s=outer_timeout_s,
    )

    if timed_out:
        since_last_progress_s = live_counter.since_last_progress_s()
        tool_call_count = live_counter.value()
        # Do NOT cancel worker_task. The thread is non-cancellable and owns the
        # gate slot until its finally block runs release_sdk_dispatch_slot_sync.
        # Mark orphaned first so heartbeats stop misleading observability, then
        # hard-kill the bridge — active tool legs reset the httpx read deadline
        # so read-timeout alone may never unblock (friction 23851).
        orphan_client = mark_dispatch_orphaned(dispatch_id=req.dispatch_id)
        emit_sdk_worker_timeout(
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            execution_id=req.execution_id,
            resolved_model=req.model,
            timeout_s=outer_timeout_s,
            since_last_progress_s=since_last_progress_s,
            tool_call_count=tool_call_count,
        )
        bridge_aborted = await asyncio.to_thread(
            abort_orphaned_bridge,
            dispatch_id=req.dispatch_id,
            client=orphan_client,
        )
        emit_sdk_worker_orphaned(
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            execution_id=req.execution_id,
            resolved_model=req.model,
            timeout_s=outer_timeout_s,
            bridge_aborted=bridge_aborted,
            since_last_progress_s=since_last_progress_s,
        )
        worker_task.add_done_callback(
            lambda fut: logger.warning(
                "late cursor-sdk worker completed after timeout: dispatch_id=%s "
                "bridge_aborted=%s exc=%r",
                req.dispatch_id,
                bridge_aborted,
                fut.exception() if fut.done() and not fut.cancelled() else None,
            )
        )
        env = error_envelope(
            code="CURSOR_SDK_TIMEOUT",
            message=(
                f"cursor-sdk dispatch idle timeout: no successful tool call "
                f"for {since_last_progress_s:.0f}s (budget {outer_timeout_s:.0f}s)"
            ),
            source="gateway",
        )
        await bus.reply(
            thread_id=req.thread_id,
            to_agent=reply_to,
            from_agent="cursor-sdk",
            subject=f"cursor-sdk dispatch {req.dispatch_id} FAILED (timeout)",
            body=f"```json\n{json.dumps(env, indent=2)}\n```",
        )
        await _terminate_link(bus, thread_id=req.thread_id, terminal_status="failed")
        await asyncio.to_thread(persist_timeout_retain, dispatch_id=req.dispatch_id)
        await _mark_terminal_and_promote(
            dispatch_id=req.dispatch_id,
            terminal_status="failed",
            controller=controller,
            emit_tag="CURSOR_SDK_TIMEOUT",
        )
        return

    # Worker completed — slot already released by _run_sdk_sync finally block.
    try:
        outcome = worker_task.result()
    except CursorHomeConfigError as exc:
        logger.error(
            "cursor sdk home/auth config failed: dispatch_id=%s err=%s",
            req.dispatch_id,
            exc,
        )
        env = error_envelope(
            code="CURSOR_HOME_CONFIG", message=str(exc), source="gateway"
        )
        await bus.reply(
            thread_id=req.thread_id,
            to_agent=reply_to,
            from_agent="cursor-sdk",
            subject=f"cursor-sdk dispatch {req.dispatch_id} FAILED (home/auth)",
            body=f"```json\n{json.dumps(env, indent=2)}\n```",
        )
        await _terminate_link(bus, thread_id=req.thread_id, terminal_status="failed")
        await _mark_terminal_and_promote(
            dispatch_id=req.dispatch_id,
            terminal_status="failed",
            controller=controller,
            emit_tag="CURSOR_HOME_CONFIG",
        )
        return
    except CursorVenvConfigError as exc:
        logger.error(
            "cursor sdk venv config failed: dispatch_id=%s err=%s", req.dispatch_id, exc
        )
        env = error_envelope(
            code="CURSOR_VENV_CONFIG", message=str(exc), source="gateway"
        )
        await bus.reply(
            thread_id=req.thread_id,
            to_agent=reply_to,
            from_agent="cursor-sdk",
            subject=f"cursor-sdk dispatch {req.dispatch_id} FAILED (venv config)",
            body=f"```json\n{json.dumps(env, indent=2)}\n```",
        )
        await _terminate_link(bus, thread_id=req.thread_id, terminal_status="failed")
        await _mark_terminal_and_promote(
            dispatch_id=req.dispatch_id,
            terminal_status="failed",
            controller=controller,
            emit_tag="CURSOR_VENV_CONFIG",
        )
        return
    except BaseException as exc:  # noqa: BLE001
        # BaseException (not just Exception): a dying SDK/fastmcp bridge can
        # surface as BaseException/BaseExceptionGroup. A narrow ``except
        # Exception`` would let it escape — leaving the row stuck ``running``
        # with zero delivery (the orphaned-dispatch failure mode). Finalize on
        # ANY worker outcome so there is no silent path.
        logger.exception("cursor sdk dispatch failed: dispatch_id=%s", req.dispatch_id)
        forensics = getattr(exc, "forensics", None)
        await _finalize_failed(
            req=req,
            bus=bus,
            reply_to=reply_to,
            controller=controller,
            code="CURSOR_SDK_DISPATCH",
            message=str(exc),
            subject_suffix="FAILED",
            error=f"{type(exc).__name__}: {exc}",
            exc=exc,
            data=forensics if isinstance(forensics, dict) else None,
        )
        return

    try:
        await _finalize_success(
            req=req,
            source_repo=source_repo,
            binding=binding,
            outcome=outcome,
            bus=bus,
            reply_to=reply_to,
            controller=controller,
            worktree_isolated=worktree_isolated,
        )
    except BaseException as exc:  # noqa: BLE001
        # The run succeeded but finalize (sidecar/cortex/bus) raised. Without
        # this guard the row would be stuck ``running`` despite a finished run.
        logger.exception(
            "cursor sdk closeout/delivery failed: dispatch_id=%s", req.dispatch_id
        )
        await _finalize_failed(
            req=req,
            bus=bus,
            reply_to=reply_to,
            controller=controller,
            code="CURSOR_SDK_CLOSEOUT",
            message=str(exc),
            subject_suffix="FAILED (closeout)",
            error=f"closeout {type(exc).__name__}: {exc}",
            retryable=True,
            data={
                "sidecar_ref": sidecar_workspaces_ref(req.dispatch_id),
                "recovery": "full Composer result persisted in sidecar; re-deliver from sidecar",
            },
        )


async def _finalize_failed(
    *,
    req: CursorDispatchRequest,
    bus: CursorBusClient,
    reply_to: str,
    controller: WorkAdmissionController,
    code: str,
    message: str,
    subject_suffix: str,
    error: str | None = None,
    retryable: bool = False,
    data: dict[str, Any] | None = None,
    exc: BaseException | None = None,
) -> None:
    """Single failure-finalize path: emit, deliver an error envelope, terminate,
    and mark terminal ``failed`` + promote. Guarantees no silent orphan.
    """
    effective_error = error if error is not None else f"{code}: {message}"
    degraded_reasons = degraded_reasons_from_exception(exc) if exc is not None else ()
    emit_sdk_worker_failed(
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        execution_id=req.execution_id,
        error=effective_error,
        worker_error_code=code,
        degraded_reasons=list(degraded_reasons) if degraded_reasons else None,
    )
    env_data = dict(data) if data else {}
    if degraded_reasons:
        env_data["degraded_reasons"] = list(degraded_reasons)
    env = error_envelope(
        code=code,
        message=message,
        source="gateway",
        retryable=retryable,
        data=env_data if env_data else None,
    )
    await bus.reply(
        thread_id=req.thread_id,
        to_agent=reply_to,
        from_agent="cursor-sdk",
        subject=f"cursor-sdk dispatch {req.dispatch_id} {subject_suffix}",
        body=f"```json\n{json.dumps(env, indent=2)}\n```",
    )
    await _terminate_link(bus, thread_id=req.thread_id, terminal_status="failed")
    await _mark_terminal_and_promote(
        dispatch_id=req.dispatch_id,
        terminal_status="failed",
        controller=controller,
        emit_tag=code,
    )


async def _finalize_success(
    *,
    req: CursorDispatchRequest,
    source_repo: Path,
    binding: CaptureBinding | None = None,
    outcome: SdkRunOutcome,
    bus: CursorBusClient,
    reply_to: str,
    controller: WorkAdmissionController,
    worktree_isolated: bool = False,
) -> None:
    packet_text = _read_packet_text(req, source_repo) if req.packet_path else ""
    instruction_text = packet_text if req.packet_path else (req.message or "")
    inferred_contract = (
        infer_contract_from_text(packet_text)
        if (not req.handoff_contract and not req.message and packet_text)
        else None
    )
    work_item_ref = extract_source_ref_from_packet(packet_text) if packet_text else None
    contract = (req.handoff_contract or inferred_contract or "consult").lower()
    degraded_reason = (
        degraded_implement_reason(outcome) if contract == "implement" else None
    )
    # deliverables_expected gate (todo:cursor-sdk-deliverables-expected-light-bounded
    # + todo:success-shaped-silence operator bind 6929#534): light-bounded path
    # extract, implement contract, OR commissioner-named files_expected /
    # evidence_required obligations — worker-set, not producer-declared.
    light_bounded_expected_paths = (
        extract_instructed_paths(instruction_text)
        if contract == LIGHT_BOUNDED_CONTRACT
        else ()
    )
    cortex_root = cortex_files_root()
    path_present = (
        light_bounded_deliverable_present(
            light_bounded_expected_paths,
            source_repo=source_repo,
            cortex_root=cortex_root,
        )
        if contract == LIGHT_BOUNDED_CONTRACT
        else False
    )
    manifest_landed = (
        fs_write_landed(
            outcome.effects_manifest,
            source_repo=source_repo,
            cortex_root=cortex_root,
        )
        if contract == LIGHT_BOUNDED_CONTRACT
        else False
    )
    deliverable_present = path_present or manifest_landed
    # Hollow-no-op invariant (friction 24299) applies to ALL contracts and ALL
    # run statuses: an empty body with zero tool calls is a model no-op that must
    # outrank the downstream pinned_deliverable_* reason derived in
    # prepare_closeout_delivery_async, else a secondary pin-write miss becomes the
    # primary degraded_reason and the no-op is misdiagnosed. Checked first so it
    # closes the gap left by the finished-gated empty_output guard and the
    # implement-only zero_tool_calls reason.
    # Empty-output invariant (friction 19819) applies to ALL contracts: a finished
    # run whose captured body (after transcript reconstruction in resolve_run_body)
    # is empty must never report status:complete + 0B. Implement-specific reasons
    # (run_status / zero_tool_calls) take precedence when present.
    # Closeout-truth backstop (friction 21654 fix #3): a light-bounded dispatch
    # must not claim complete when a named deliverable never landed. Holds
    # regardless of whether the #1/#2 write-path instrumentation saw the choke.
    if degraded_reason is None:
        from services.git_integration_worker.cursor_sdk_closeout.degraded_reasons import (
            conductor_closeout_degraded_reason,
        )

        _packet_kind = (
            extract_packet_kind_from_packet(packet_text) if packet_text else None
        )
        _is_conductor = _packet_kind == "conductor"
        _nested_live = False
        if _is_conductor:
            from services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons import (
                conductor_has_live_nested,
            )

            _nested_live = conductor_has_live_nested(dispatch_id=req.dispatch_id)
        _conductor_reason = conductor_closeout_degraded_reason(
            body=outcome.body,
            packet_text=packet_text or None,
            packet_kind=_packet_kind,
            nested_live=_nested_live,
        )
        if _is_conductor:
            degraded_reason = (
                _conductor_reason
                or empty_assistant_turn_reason(outcome)
                or empty_output_degraded_reason(outcome)
                or light_bounded_deliverable_reason(
                    body=outcome.body,
                    tool_calls=outcome.tool_calls,
                    contract=contract,
                    deliverable_present=deliverable_present,
                )
            )
        else:
            degraded_reason = (
                empty_assistant_turn_reason(outcome)
                or empty_output_degraded_reason(outcome)
                or _conductor_reason
                or light_bounded_deliverable_reason(
                    body=outcome.body,
                    tool_calls=outcome.tool_calls,
                    contract=contract,
                    deliverable_present=deliverable_present,
                )
            )
        # Observability: if filesystem ground truth suppressed a would-be
        # light-bounded degrade, surface it (frontier.sdk.closeout.reconciled).
        if deliverable_present and degraded_reason is None:
            suppressed_reason = light_bounded_deliverable_reason(
                body=outcome.body,
                tool_calls=outcome.tool_calls,
                contract=contract,
            )
            if suppressed_reason is not None:
                verifying_path = (
                    light_bounded_expected_paths[0]
                    if path_present and light_bounded_expected_paths
                    else first_landed_fs_uri(
                        outcome.effects_manifest,
                        source_repo=source_repo,
                        cortex_root=cortex_root,
                    )
                )
                emit_sdk_closeout_reconciled(
                    dispatch_id=req.dispatch_id,
                    thread_id=req.thread_id,
                    suppressed_reason=suppressed_reason,
                    verifying_path=verifying_path,
                )
    await _deliver_sdk_closeout(
        req=req,
        source_repo=source_repo,
        binding=binding,
        outcome=outcome,
        degraded_reason=degraded_reason,
        bus=bus,
        reply_to=reply_to,
        work_item_ref=work_item_ref,
        controller=controller,
        packet_text=packet_text,
        deliverables_expected=compute_deliverables_expected(
            contract=contract,
            instruction_text=instruction_text,
            light_bounded_expected_paths=light_bounded_expected_paths,
        ),
        light_bounded_expected_paths=light_bounded_expected_paths,
        extra_deviations=implement_gate_bypass_deviations(
            contract=contract,
            work_item_ref=work_item_ref,
        ),
        worktree_isolated=worktree_isolated,
    )


@router.post("/dispatch", response_model=CursorDispatchResponse)
async def cursor_dispatch(
    req: CursorDispatchRequest, request: Request
) -> CursorDispatchResponse:
    cfg = _config(request)
    try:
        config = resolve_cursor(req.model)
    except ValueError as exc:
        return _reject_pre_admission(
            req,
            worker_error_code="CURSOR_MODEL_UNTRUSTED",
            failure_layer="validation",
            http_status=422,
            detail_summary=str(exc),
            invalid_fields=["model"],
            validation_stage="model_resolution",
        )
    try:
        _resolve_prompt(req, cfg.source_repo)
    except ValueError as exc:
        return _reject_pre_admission(
            req,
            worker_error_code="CURSOR_PACKET_INVALID",
            failure_layer="validation",
            http_status=422,
            detail_summary=str(exc),
            invalid_fields=["packet_path"] if req.packet_path else ["message"],
        )
    try:
        parity = validate_dispatch_context(cfg.source_repo)
    except CursorSdkParityError as exc:
        return _reject_pre_admission(
            req,
            worker_error_code="CURSOR_SDK_PARITY",
            failure_layer="validation",
            http_status=422,
            detail_summary=str(exc),
        )
    logger.info(
        "cursor sdk dispatch validated (pre-admission): dispatch_id=%s thread_id=%s parity=%s",
        req.dispatch_id,
        req.thread_id,
        parity,
    )

    controller = _controller(request)
    # Early synchronous drain reject: skip creating a ledger row at all in the
    # common draining case. The binding TOCTOU guarantee is in try_admit below
    # (its drain check and ticket reservation share one synchronous frame).
    if controller.is_draining():
        return _draining_response(
            Draining503(
                f"git-integration-worker is draining (epoch={controller.drain_epoch})"
            )
        )

    debt_refusal = await asyncio.to_thread(_branch_debt_refusal, req)
    if debt_refusal is not None:
        return _reject_pre_admission(
            req,
            worker_error_code="lane_branch_debt_unpaid",
            failure_layer=FailureLayer.WORKER,
            http_status=409,
            detail_summary=debt_refusal,
            retryable=False,
        )

    admission = CursorDispatchResponse(
        admitted=True,
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        model_id=config.model_id,
        **_lane_branch_standing(req),
    )
    ledger = CursorDispatchLedger.instance()
    fingerprint = ledger.fingerprint(req)
    packet_text = (
        _read_packet_text(req, cfg.source_repo)
        if req.packet_path
        else (req.message or "")
    )
    inferred_contract = (
        infer_contract_from_text(packet_text)
        if (not req.handoff_contract and packet_text)
        else None
    )
    contract = (req.handoff_contract or inferred_contract or "consult").lower()
    effective_read_only = _effective_read_only(req, contract)
    if effective_read_only and wire_lane_explicit(req) == "B":
        return _reject_pre_admission(
            req,
            worker_error_code="CURSOR_LANE_B_READ_ONLY",
            failure_layer="validation",
            http_status=422,
            detail_summary="read_only=true is incompatible with lane='B'",
            invalid_fields=["read_only", "lane"],
        )
    if effective_read_only and contract == "implement":
        return _reject_pre_admission(
            req,
            worker_error_code="CURSOR_READONLY_IMPLEMENT_CONFLICT",
            failure_layer="validation",
            http_status=422,
            detail_summary="read_only=true is incompatible with contract=implement",
            invalid_fields=["read_only"],
        )
    candidate_source_ref = req.source_ref or extract_source_ref_from_packet(packet_text)
    packet_kind = extract_packet_kind_from_packet(packet_text) if packet_text else None
    if packet_kind == "conductor" and config.model_id == "composer-2.5":
        return _reject_pre_admission(
            req,
            worker_error_code="CURSOR_CONDUCTOR_T0_REFUSED",
            failure_layer="validation",
            http_status=422,
            detail_summary=(
                "Composer is refused on packet_kind=conductor; pin T1 Grok "
                "model=cursor/grok-4.6 model_knobs={effort:xhigh, fast:false}"
            ),
            invalid_fields=["model"],
            extra_data={
                "hint": (
                    "model=cursor/grok-4.6 model_knobs={effort:xhigh, fast:false}"
                ),
            },
        )
    candidate_work_key = req.work_key or (
        extract_work_key_from_packet(packet_text) if packet_text else None
    )
    if packet_kind == "conductor":
        if not candidate_work_key and req.source_ref:
            candidate_work_key = req.source_ref
        candidate_source_ref = None
    elif not candidate_work_key and candidate_source_ref:
        candidate_work_key = candidate_source_ref
    if contract == "implement" and not candidate_source_ref:
        emit_sdk_implement_unresolved_source_ref(
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            execution_id=req.execution_id,
        )
    files_expected = _files_from_packet(packet_text) if packet_text else []
    prior_lane = lookup_lane_worktree(thread_id=req.thread_id)
    prior_lane_tree = (
        prior_lane.worktree_path
        if prior_lane is not None and prior_lane.worktree_path.is_dir()
        else None
    )
    source_repo_str = str(cfg.source_repo.resolve())
    try:
        resolved_source_repo = resolve_dispatch_source_repo(
            req.workspace,
            hub=cfg.source_repo,
            projects_root=cfg.dispatch_workspace,
        )
    except CursorWorkspaceError as exc:
        return _reject_pre_admission(
            req,
            worker_error_code=exc.code,
            failure_layer="validation",
            http_status=422,
            detail_summary=exc.message,
            invalid_fields=["workspace"],
        )
    dispatch_git_str = str(resolved_source_repo.resolve())
    parent_isolated: bool | None = None
    inherit_parent = req.nest_under or req.resume_of
    if inherit_parent:
        parent_key = lookup_parent_lease_key(inherit_parent)
        if parent_key is not None:
            parent_isolated = lease_is_isolated_worktree(
                lease_key=parent_key,
                source_repo=source_repo_str,
            )
    try:
        selected_lane, lane_advisories, lane_reason = select_lane(
            req=req,
            regime_active=lane_b_regime_active(),
            source_repo=resolved_source_repo,
            files_expected=files_expected,
            contract=contract,
            lane_worktree=prior_lane_tree,
            parent_isolated=parent_isolated,
        )
    except LaneScopeRefused as exc:
        return _reject_pre_admission(
            req,
            worker_error_code="CURSOR_LANE_B_SCOPE_REFUSED",
            failure_layer="validation",
            http_status=422,
            detail_summary=str(exc),
            invalid_fields=["lane", "files_expected"],
        )
    req.lane = selected_lane
    if effective_read_only:
        lane_reason = "read_only"
    if lane_advisories:
        logger.info(
            "cursor sdk lane advisories dispatch_id=%s advisories=%s",
            req.dispatch_id[:8],
            lane_advisories,
        )
    resume_reject = reject_resume_if_ineligible(req)
    if resume_reject is not None:
        return resume_reject
    minted_lane_b = False
    mint_wait_ms = 0.0
    try:
        mint_started = time.monotonic()
        dispatch_workspace, lease_key = await asyncio.to_thread(
            resolve_admit_binding,
            req=req,
            source_repo=resolved_source_repo,
            hub=cfg.source_repo,
            worktree_root=cfg.worktree_root,
            dispatch_workspace_default=cfg.dispatch_workspace,
            lane=selected_lane,
        )
        mint_wait_ms = (time.monotonic() - mint_started) * 1000.0
        minted_lane_b = (
            selected_lane == "B"
            and not req.nest_under
            and req.worktree_path is None
            and not req.resume_of
            and prior_lane_tree is None
        )
        isolation_materialized = b_worktree_materialized(
            admit_lane=selected_lane,
            lease_key=lease_key,
            source_repo=dispatch_git_str,
        )
        if minted_lane_b:
            from services.git_integration_worker.cursor_sdk_worktree_registry import (
                lookup_dispatch_worktree,
            )

            record = lookup_dispatch_worktree(dispatch_id=req.dispatch_id)
            if record is not None:
                emit_sdk_lane_b_minted(
                    dispatch_id=req.dispatch_id,
                    thread_id=req.thread_id,
                    worktree_path=str(record.worktree_path),
                    branch=record.branch_name,
                    branch_point=record.branch_point,
                    mint_wait_ms=round(mint_wait_ms, 1),
                )
                await associate_lane_branch(
                    thread_id=req.thread_id,
                    branch_name=record.branch_name,
                )
    except WorktreeMintError as exc:
        return _reject_pre_admission(
            req,
            worker_error_code="CURSOR_WORKTREE_MINT_FAILED",
            failure_layer="admission",
            http_status=503,
            detail_summary=str(exc),
            retryable=getattr(exc, "retryable", False),
            validation_stage="worktree_mint",
        )
    binding = binding_for_dispatch(
        cfg=cfg,
        lease_key=lease_key,
        dispatch_source_repo=resolved_source_repo,
    )
    gate_lane = sdk_dispatch_lane(
        caller_agent=req.caller_agent,
        dispatch_id=req.dispatch_id,
    )
    concurrency_posture = derive_concurrency_posture(
        admit_lane=selected_lane,
        gate_lane=gate_lane,
        read_only=effective_read_only,
        nest_under=req.nest_under,
        worktree_path=Path(lease_key) if selected_lane == "B" else None,
        source_repo=dispatch_git_str,
    )
    if lane_b_worktree_missing(
        selected_lane=selected_lane,
        lease_key=lease_key,
        source_repo=dispatch_git_str,
    ):
        emit_sdk_lane_b_worktree_missing_observed(
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            lease_key=lease_key,
            source_repo=dispatch_git_str,
        )
        await _rollback_lane_b_mint_if_needed(
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            source_repo=resolved_source_repo,
            minted_lane_b=minted_lane_b,
            reason="b_worktree_missing",
        )
        return _reject_pre_admission(
            req,
            worker_error_code="CURSOR_LANE_B_WORKTREE_MISSING",
            failure_layer="admission",
            http_status=422,
            detail_summary=(
                "Lane-B write requires a materialized worktree "
                "(minted or inherited); shared-master lease is Lane A"
            ),
            retryable=False,
            validation_stage="lane_b_materialization",
            invalid_fields=["lane"],
            extra_data={
                "lease_key": lease_key,
                "source_repo": dispatch_git_str,
            },
        )
    slot_limit = (
        write_lease_slot_limit(admit_lane=selected_lane, posture=concurrency_posture)
        if concurrency_posture is not None
        else 1
    )
    block_key = candidate_work_key or candidate_source_ref
    if (
        should_block_implement_for_open_conductor(
            contract=contract,
            nest_under=req.nest_under,
            work_key=block_key,
        )
        and block_key
    ):
        with ledger._connect() as conn:
            holder = find_open_conductor_holder_conn(
                conn,
                work_key=block_key,
                exclude_dispatch_id=req.dispatch_id,
            )
        if holder is not None:
            return _reject_pre_admission(
                req,
                worker_error_code="CURSOR_SOURCE_REF_IN_FLIGHT",
                failure_layer="admission",
                http_status=409,
                detail_summary=(
                    f"open conductor holds work identity {block_key!r} "
                    f"(holder_dispatch_id={holder.dispatch_id!r})"
                ),
                retryable=False,
                validation_stage="ledger_conductor_open",
                extra_data={
                    "work_key": block_key,
                    "holder_dispatch_id": holder.dispatch_id,
                    "holder_thread_id": holder.thread_id,
                    "holder_kind": "conductor",
                },
            )
    try:
        cached = await asyncio.to_thread(
            ledger.admit,
            req=req,
            fingerprint=fingerprint,
            execution_id=req.execution_id,
            caller_agent=req.caller_agent,
            resolved_model=config.model_id,
            admission=admission,
            contract=contract,
            source_repo=dispatch_git_str,
            lease_key=lease_key,
            read_only=effective_read_only,
            worker_instance=controller.worker_id,
            source_ref=candidate_source_ref,
            work_key=candidate_work_key,
            force=req.force,
            nest_under=req.nest_under,
            refuse_if_lease_held=req.refuse_if_lease_held,
            concurrency_posture=concurrency_posture,
            write_lease_slot_limit=slot_limit,
            isolation_materialized=isolation_materialized,
        )
    except WriteLeaseHeld as exc:
        await _rollback_lane_b_mint_if_needed(
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            source_repo=resolved_source_repo,
            minted_lane_b=minted_lane_b,
            reason="write_lease_held",
        )
        return _reject_pre_admission(
            req,
            worker_error_code="CURSOR_WRITE_LEASE_HELD",
            failure_layer="admission",
            http_status=409,
            detail_summary=str(exc),
            retryable=True,
            validation_stage="ledger_write_lease",
            extra_data={
                "lease_key": exc.lease_key,
                "holder_dispatch_id": exc.holder_dispatch_id,
                "holder_thread_id": exc.holder_thread_id,
                "queue_depth": exc.queue_depth,
            },
        )
    except SourceRefConflict as exc:
        await _rollback_lane_b_mint_if_needed(
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            source_repo=resolved_source_repo,
            minted_lane_b=minted_lane_b,
            reason="source_ref_conflict",
        )
        if exc.work_fingerprint:
            from services.git_integration_worker.cursor_sdk_cancel_events import (
                emit_sdk_admit_duplicate_refused,
            )

            emit_sdk_admit_duplicate_refused(
                dispatch_id=req.dispatch_id,
                thread_id=req.thread_id,
                work_fingerprint=exc.work_fingerprint,
                holder_dispatch_id=exc.holder_dispatch_id,
                holder_thread_id=exc.holder_thread_id,
            )
            worker_code = "CURSOR_WORK_FINGERPRINT_IN_FLIGHT"
            validation_stage = "ledger_work_fingerprint"
        else:
            worker_code = "CURSOR_SOURCE_REF_IN_FLIGHT"
            validation_stage = "ledger_source_ref"
        return _reject_pre_admission(
            req,
            worker_error_code=worker_code,
            failure_layer="admission",
            http_status=409,
            detail_summary=str(exc),
            retryable=False,
            validation_stage=validation_stage,
            extra_data={
                "source_ref": exc.source_ref,
                "work_key": exc.work_key,
                "work_fingerprint": exc.work_fingerprint,
                "holder_dispatch_id": exc.holder_dispatch_id,
                "holder_thread_id": exc.holder_thread_id,
            },
        )
    except DispatchConflict as exc:
        await _rollback_lane_b_mint_if_needed(
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            source_repo=resolved_source_repo,
            minted_lane_b=minted_lane_b,
            reason="dispatch_conflict",
        )
        return _reject_pre_admission(
            req,
            worker_error_code="CURSOR_DISPATCH_CONFLICT",
            failure_layer="admission",
            http_status=409,
            detail_summary=str(exc),
            retryable=False,
            validation_stage="ledger_dedup",
        )
    except NestDepthExceeded as exc:
        await _rollback_lane_b_mint_if_needed(
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            source_repo=resolved_source_repo,
            minted_lane_b=minted_lane_b,
            reason="nest_depth_exceeded",
        )
        return _reject_pre_admission(
            req,
            worker_error_code="CURSOR_NEST_DEPTH_EXCEEDED",
            failure_layer="admission",
            http_status=422,
            detail_summary=str(exc),
            retryable=False,
            validation_stage="ledger_nest_depth",
        )
    except WorkerThreadOccupied as exc:
        await _rollback_lane_b_mint_if_needed(
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            source_repo=resolved_source_repo,
            minted_lane_b=minted_lane_b,
            reason="worker_thread_occupied",
        )
        return _reject_pre_admission(
            req,
            worker_error_code="CURSOR_WORKER_THREAD_OCCUPIED",
            failure_layer="admission",
            http_status=422,
            detail_summary=str(exc),
            retryable=False,
            validation_stage="ledger_thread_occupied",
            extra_data={
                "holder_dispatch_id": exc.holder_dispatch_id,
                "holder_thread_id": exc.holder_thread_id,
            },
        )
    except NestParentNotLive as exc:
        await _rollback_lane_b_mint_if_needed(
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            source_repo=resolved_source_repo,
            minted_lane_b=minted_lane_b,
            reason="nest_parent_not_live",
        )
        return _reject_pre_admission(
            req,
            worker_error_code="CURSOR_NEST_PARENT_NOT_LIVE",
            failure_layer="admission",
            http_status=422,
            detail_summary=str(exc),
            retryable=False,
            validation_stage="ledger_nest_parent",
        )
    if cached is not None:
        status_code = 202 if cached.status == "queued" else 200
        if cached.status == "queued":
            _emit_enriched_queued(
                req=req,
                cached=cached,
                source_repo_str=dispatch_git_str,
                packet_text=packet_text,
                lease_key=lease_key or dispatch_git_str,
            )
        return JSONResponse(status_code=status_code, content=cached.model_dump())

    if packet_kind == "conductor":
        ledger.merge_record_json(
            dispatch_id=req.dispatch_id,
            patch={
                "packet_kind": "conductor",
                "work_key": candidate_work_key,
            },
        )

    if req.resume_of:
        parent_row = load_parent_row(ledger, parent_id=req.resume_of)
        if parent_row is not None and parent_row.sdk_agent_id:
            store_dir = resolve_sdk_store_dir(
                parent_id=req.resume_of,
                state_root=parent_row.state_root,
            )
            store_root = str(store_dir) if store_dir is not None else ""
            emit_sdk_worker_resumed(
                dispatch_id=req.dispatch_id,
                resume_of=req.resume_of,
                sdk_agent_id=parent_row.sdk_agent_id,
                state_root=store_root,
                thread_id=req.thread_id,
                execution_id=req.execution_id,
                parent_terminal_status=getattr(
                    parent_row, "terminal_status", None
                )
                or parent_row.status,
            )

    if not effective_read_only and concurrency_posture is not None:
        emit_write_lease_acquired(
            dispatch_id=req.dispatch_id,
            source_repo=lease_key or dispatch_git_str,
        )

    emit_sdk_lane_selected(
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        lane=selected_lane,
        reason=lane_reason,
        regime_active=lane_b_regime_active(),
        contract=contract,
        selecting_predicate=lane_selection_predicate(
            reason=lane_reason,
            contract=contract,
            regime_active=lane_b_regime_active(),
        ),
    )

    # Nest park: ledger already moved parent → parked_waiting; transfer capacity
    # to the child before the gated run (child acquire is idempotent if holding).
    if req.nest_under and not effective_read_only:
        parked = await asyncio.to_thread(
            ledger.find_parked_parent_for_child, child_id=req.dispatch_id
        )
        if parked is not None and parked[0] == req.nest_under:
            nest_depth = await asyncio.to_thread(
                ledger.nest_child_depth,
                nest_under=req.nest_under,
                child_dispatch_id=req.dispatch_id,
            )
            try:
                await transfer_capacity_after_park(
                    parent_id=req.nest_under,
                    child_id=req.dispatch_id,
                    source_repo=dispatch_git_str,
                    nest_depth=nest_depth,
                )
            except Exception as exc:
                logger.error(
                    "nest park capacity transfer failed: parent=%s child=%s err=%s",
                    req.nest_under[:8],
                    req.dispatch_id[:8],
                    exc,
                )
                await release_or_restore_for_child(dispatch_id=req.dispatch_id)
                await _mark_terminal_and_promote(
                    dispatch_id=req.dispatch_id,
                    terminal_status="failed",
                    controller=controller,
                    request=request,
                    emit_tag="CURSOR_NEST_PARK_TRANSFER_FAILED",
                )
                return _reject_pre_admission(
                    req,
                    worker_error_code="CURSOR_NEST_PARK_TRANSFER_FAILED",
                    failure_layer="admission",
                    http_status=503,
                    detail_summary=str(exc),
                    retryable=True,
                )

    # Friction 23001: wt-baseline capture is deferred into
    # _run_sdk_dispatch_gated (post slot acquisition) so the admission HTTP
    # response returns immediately. Capturing here blocked the response for
    # ~26s on a 58-path dirty checkout, exceeding Stargate's read timeout and
    # producing false-negative 599s while the dispatch executed anyway.

    # Reserve the admission ticket synchronously. try_admit re-checks drain with
    # no await between the check and the reservation; if a drain began during the
    # ledger.admit await above, it raises and we roll the fresh ledger row back to
    # terminal so it never lingers as a phantom pending dispatch.
    try:
        ticket = controller.try_admit(
            "cursor_sdk",
            op_id=req.dispatch_id,
            route="/api/v1/cursor/dispatch",
        )
    except Draining503 as exc:
        await _rollback_lane_b_mint_if_needed(
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            source_repo=resolved_source_repo,
            minted_lane_b=minted_lane_b,
            reason="draining503",
        )
        await release_or_restore_for_child(dispatch_id=req.dispatch_id)
        await _mark_terminal_and_promote(
            dispatch_id=req.dispatch_id,
            terminal_status="failed",
            controller=controller,
            request=request,
            emit_tag="CURSOR_DRAINING503_ADMIT",
        )
        return _draining_response(exc)

    bus = CursorBusClient()
    task = controller.create_tracked_task(
        _close_ticket_after(
            _run_sdk_dispatch_gated(
                req=req,
                source_repo=cfg.source_repo,
                binding=binding,
                dispatch_workspace=dispatch_workspace,
                bus=bus,
                controller=controller,
                contract=contract,
                worktree_isolated=req.lane == "B" or req.worktree_isolated,
            ),
            controller=controller,
            op_id=req.dispatch_id,
        ),
        op_id=req.dispatch_id,
    )
    ledger.register_task(req.dispatch_id, task)
    ticket.mark_running()
    await asyncio.to_thread(ledger.mark_running, dispatch_id=req.dispatch_id)
    _maybe_emit_giw_dispatched(req=req, packet_text=packet_text)
    return JSONResponse(status_code=200, content=admission.model_dump())


@router.delete("/dispatch/{dispatch_id}")
async def cancel_cursor_dispatch(
    dispatch_id: str,
    request: Request,
    reason: str | None = Query(None, description="Operator cancel reason."),
    cancelled_by: str | None = Query(None, description="Seat or operator id."),
):
    """Operator cancel for queued/admitted SDK dispatches (ledger authority)."""
    from services.git_integration_worker.cursor_sdk_operator_cancel import (
        operator_cancel_dispatch,
    )

    controller = _controller(request)
    status_code, body = await operator_cancel_dispatch(
        dispatch_id=dispatch_id,
        cancel_reason=reason,
        cancelled_by=cancelled_by,
        controller=controller,
        request=request,
    )
    return JSONResponse(status_code=status_code, content=body)


@router.get(
    "/concurrency-stats",
    summary=(
        "Rolling-window write-implement overlap + post-floor ambient:* census. "
        "Omitted windows default to CURSOR_SDK_CONCURRENCY_STATS_RETENTION_DAYS "
        "(14) ending at current UTC."
    ),
)
async def cursor_concurrency_stats(
    request: Request,
    window_start: str | None = Query(
        None,
        description=(
            "ISO window start (inclusive). When omitted, defaults to "
            "CURSOR_SDK_CONCURRENCY_STATS_RETENTION_DAYS before window_end "
            "(or current UTC when window_end is also omitted)."
        ),
    ),
    window_end: str | None = Query(
        None,
        description="ISO window end (inclusive). Defaults to current UTC when omitted.",
    ),
) -> dict:
    from services.git_integration_worker.cursor_sdk_concurrency_meter import (
        active_work_lane_fields,
        concurrency_stats,
    )

    cfg = _config(request)
    closeout_root = cfg.source_repo / "tmp" / "reviews" / "closeouts"
    payload = await asyncio.to_thread(
        concurrency_stats,
        closeout_root=closeout_root,
        source_repo=cfg.source_repo,
        window_start=window_start,
        window_end=window_end,
    )
    payload.update(
        await asyncio.to_thread(active_work_lane_fields, source_repo=cfg.source_repo)
    )
    return payload


@router.get(
    "/branch-debt",
    summary="Open Lane-B branch debt: who owes which branch, and for how long.",
)
async def cursor_branch_debt() -> dict:
    from services.git_integration_worker.cursor_sdk_branch_debt import (
        lane_hygiene_snapshot,
    )

    return await asyncio.to_thread(lane_hygiene_snapshot)


@router.post(
    "/branch-discharge",
    summary="Retire a lane branch: landed (content-probed) or discard (reasoned).",
)
async def cursor_branch_discharge(
    req: BranchDischargeRequest, request: Request
) -> dict:
    """One call, both honest exits — the clean path a lane is asked to take."""
    from services.git_integration_worker.cursor_sdk_branch_discharge import discharge

    cfg = _config(request)
    result = await asyncio.to_thread(
        discharge,
        repo=cfg.source_repo,
        branch_name=req.branch,
        verb=req.verb,
        reason=req.reason,
    )
    payload = {
        "discharged": result.discharged,
        "branch": result.branch,
        "verb": result.verb,
        "tip_sha": result.tip_sha,
        "archive_tag": result.archive_tag,
        "refused_reason": result.refused_reason,
    }
    if result.probe is not None:
        payload["probe"] = {
            "landed": result.probe.landed,
            "differing_paths": result.probe.differing_paths,
            "missing_paths": result.probe.missing_paths,
        }
    if not result.discharged:
        return JSONResponse(status_code=409, content=payload)  # type: ignore[return-value]
    return payload
