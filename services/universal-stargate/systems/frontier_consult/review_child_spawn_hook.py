"""Durable post-completion review-child spawn for generate/cursor-sdk lane."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Response
from universal_logging import get_logger

from .cursor_sdk_generate_signals import publish_frontier_event
from .densify_triage import COMPOSER_DRAFT_SENTINEL, REASONING_TRACE_SENTINEL
from .events import FrontierReviewChildContextMissing, FrontierSdkReviewChildSpawned
from .generate_admission_context_store import (
    AdmissionContext,
    delete_admission_context,
    finalize_spawn_state,
    list_pending_spawn_states,
    read_admission_context,
    read_spawn_state,
    try_claim_spawn_pending,
)
from .light_bounded_ac_observer import build_generate_lane_reviewer_prompt
from .skill_suggest_durable_state import DurableTerminalEvent, durable_catch_up_terminal

logger = get_logger(__name__)

_DEFAULT_CROSS_FAMILY_REVIEWER = "openai/gpt-5.5"
_OPENAI_EXECUTOR_ALTERNATE = "anthropic/claude-opus-4-8"
_GENERATE_OP = "generate"
_CURSOR_SDK_ROLE = "cursor-sdk"
_REVIEWER_ROLE = "reviewer"
_TO_THREAD_OP = "to_thread"
_SPAWN_PROVENANCE = "generate_review_child"

CONTEXT_MISS_MAX_ATTEMPTS = 8
CONTEXT_MISS_RETRY_SECONDS = 0.25
_PENDING_RECONCILE_INTERVAL_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class ReviewerSelection:
    model: str
    family: str


def is_generate_review_child_lane_wired() -> bool:
    from .generate_admission_context_store import (
        is_generate_review_child_lane_wired as _wired,
    )

    return _wired()


def resolve_executor_family(resolved_model: str) -> str:
    from model_id import ModelId

    parsed = ModelId.parse(resolved_model)
    if parsed.provider == "openai":
        return "openai"
    if parsed.provider == "anthropic":
        return "anthropic"
    if parsed.provider == "google":
        return "google"
    if parsed.provider == "xai":
        return "xai"
    if parsed.backend_type == "cursor_sdk":
        return "cursor"
    if parsed.provider:
        return parsed.provider.lower()
    return "unknown"


def select_cross_family_reviewer(resolved_model: str) -> ReviewerSelection | None:
    family = resolve_executor_family(resolved_model)
    if family == "openai":
        model = _OPENAI_EXECUTOR_ALTERNATE
    else:
        model = _DEFAULT_CROSS_FAMILY_REVIEWER
    reviewer_family = resolve_executor_family(model)
    if reviewer_family == family:
        return None
    if not _reviewer_model_admitted(model):
        return None
    return ReviewerSelection(model=model, family=reviewer_family)


def _reviewer_model_admitted(model: str) -> bool:
    from model_id import WireModelResolutionError, resolve_wire_model_id

    try:
        resolve_wire_model_id(model, require_cloud=True)
    except WireModelResolutionError:
        return False
    return True


def is_review_child_execution(ctx: AdmissionContext) -> bool:
    if ctx.op != _TO_THREAD_OP:
        return False
    if ctx.role != _REVIEWER_ROLE:
        return False
    if not ctx.auto_review_child:
        return False
    return ctx.spawn_template_provenance == _SPAWN_PROVENANCE


def should_spawn_review_child(ctx: AdmissionContext) -> bool:
    if ctx.op != _GENERATE_OP or ctx.role != _CURSOR_SDK_ROLE:
        return False
    if not ctx.auto_review_child:
        return False
    if ctx.suppress_review_spawn:
        return False
    if is_review_child_execution(ctx):
        return False
    return True


async def _build_generate_lane_review_prompt(
    *,
    request_id: str,
    parent_dispatch_thread_id: str | None,
) -> str:
    thread_id = parent_dispatch_thread_id or ""
    draft_body = f"{COMPOSER_DRAFT_SENTINEL}\n# generate lane closeout for {thread_id}"
    packet_text: str | None = None
    if thread_id:
        from .dispatch_thread_context import read_latest_dispatch_thread_body

        try:
            thread_body = await read_latest_dispatch_thread_body(
                request_id=request_id,
                dispatch_thread_id=thread_id,
                role="reviewer",
            )
            if thread_body.strip():
                packet_text = thread_body
                draft_body = f"{COMPOSER_DRAFT_SENTINEL}\n{thread_body}"
        except Exception:
            pass
    trace_body = (
        f"{REASONING_TRACE_SENTINEL}\n# auto review child for generate/cursor-sdk"
    )
    return build_generate_lane_reviewer_prompt(
        packet_text=packet_text,
        staged_draft_body=draft_body,
        reasoning_trace_body=trace_body,
    )


async def spawn_generate_lane_review_child(
    *,
    request_id: str,
    ctx: AdmissionContext,
    parent_thread_id: str,
    reviewer: ReviewerSelection,
) -> dict[str, Any]:
    """Spawn review child onto the still-open coord/dispatch thread.

    ``parent_thread_id`` is the worker completion thread (may already be closed
    by cursor-sdk closeout). Delivery must use coord ``dispatch_thread_id``,
    matching densify_candidate_ready — never the worker thread alone.
    """
    from .route import TeamDispatchToThreadBody, team_dispatch

    delivery_thread = ctx.dispatch_thread_id or ctx.parent_dispatch_thread_id
    if not delivery_thread:
        logger.warning(
            "review_child spawn fail-closed: no coord dispatch_thread_id "
            "(worker=%s)",
            parent_thread_id,
        )
        return {}
    prompt = await _build_generate_lane_review_prompt(
        request_id=request_id,
        parent_dispatch_thread_id=delivery_thread,
    )
    child_body = TeamDispatchToThreadBody(
        op=_TO_THREAD_OP,
        role=_REVIEWER_ROLE,
        dispatch_thread_id=delivery_thread,
        thread=delivery_thread,
        subject=f"generate cross-family review — {request_id[:8]}",
        contract="light-bounded",
        model=reviewer.model,
        auto_review_child=True,
        read_only=True,
        spawn_review_provenance=_SPAWN_PROVENANCE,
    )
    from .densify_candidate_ready import _PromptOverride

    with _PromptOverride(prompt):
        result = await team_dispatch(child_body, Response())
    if isinstance(result, dict):
        child_exec = result.get("execution_id")
        if child_exec:
            from .generate_admission_context_store import write_admission_context

            write_admission_context(
                execution_id=str(child_exec),
                auto_review_child=True,
                op=_TO_THREAD_OP,
                role=_REVIEWER_ROLE,
                resolved_model=reviewer.model,
                parent_dispatch_thread_id=delivery_thread,
                dispatch_thread_id=delivery_thread,
                spawn_template_provenance=_SPAWN_PROVENANCE,
            )
    return result if isinstance(result, dict) else {}


def _emit_review_child_spawned(
    *,
    execution_id: str,
    parent_execution_id: str,
    parent_thread_id: str,
    reviewer: ReviewerSelection,
    dedupe_key: str,
) -> None:
    publish_frontier_event(
        FrontierSdkReviewChildSpawned(
            execution_id=execution_id,
            parent_execution_id=parent_execution_id,
            parent_thread_id=parent_thread_id,
            reviewer_model=reviewer.model,
            reviewer_family=reviewer.family,
            dedupe_key=dedupe_key,
        )
    )


def _emit_context_missing(*, execution_id: str, thread_id: str, attempts: int) -> None:
    publish_frontier_event(
        FrontierReviewChildContextMissing(
            execution_id=execution_id,
            thread_id=thread_id,
            attempts=attempts,
        )
    )


async def _attempt_spawn_for_completion(
    *,
    terminal: DurableTerminalEvent,
    ctx: AdmissionContext,
) -> bool:
    execution_id = terminal.execution_id or ctx.execution_id
    thread_id = terminal.thread_id or ""
    if not execution_id or not thread_id:
        return False

    existing = read_spawn_state(execution_id)
    if existing is not None and existing.state == "final":
        return False

    reviewer = select_cross_family_reviewer(ctx.resolved_model)
    if reviewer is None:
        logger.warning(
            "review_child spawn fail-closed: no cross-family reviewer for %s",
            ctx.resolved_model,
        )
        return False

    if existing is None and not try_claim_spawn_pending(
        parent_execution_id=execution_id,
        parent_dispatch_thread_id=ctx.dispatch_thread_id,
        parent_thread_id=thread_id,
        reviewer_model=reviewer.model,
    ):
        return False

    request_id = uuid.uuid4().hex[:12]
    spawn_result = await spawn_generate_lane_review_child(
        request_id=request_id,
        ctx=ctx,
        parent_thread_id=thread_id,
        reviewer=reviewer,
    )
    child_execution_id = (
        str(spawn_result.get("execution_id"))
        if isinstance(spawn_result, dict) and spawn_result.get("execution_id")
        else None
    )
    if not child_execution_id:
        logger.warning(
            "review_child spawn pending without child admission: parent=%s",
            execution_id,
        )
        return False

    finalize_spawn_state(
        parent_execution_id=execution_id,
        review_child_execution_id=child_execution_id,
    )
    delete_admission_context(execution_id)
    _emit_review_child_spawned(
        execution_id=child_execution_id,
        parent_execution_id=execution_id,
        parent_thread_id=thread_id,
        reviewer=reviewer,
        dedupe_key=execution_id,
    )
    return True


@dataclass(slots=True)
class _ContextMissTracker:
    execution_id: str
    thread_id: str
    dispatch_id: str | None
    attempts: int = 0


_miss_trackers: dict[str, _ContextMissTracker] = {}
_listener_task: asyncio.Task[None] | None = None
_reconcile_task: asyncio.Task[None] | None = None


async def _resolve_context_with_reconcile(
    *,
    execution_id: str,
    thread_id: str,
    dispatch_id: str | None,
) -> AdmissionContext | None:
    ctx = read_admission_context(execution_id)
    if ctx is not None:
        _miss_trackers.pop(execution_id, None)
        return ctx

    tracker = _miss_trackers.get(execution_id)
    if tracker is None:
        tracker = _ContextMissTracker(
            execution_id=execution_id,
            thread_id=thread_id,
            dispatch_id=dispatch_id,
        )
        _miss_trackers[execution_id] = tracker

    while tracker.attempts < CONTEXT_MISS_MAX_ATTEMPTS:
        tracker.attempts += 1
        await asyncio.sleep(CONTEXT_MISS_RETRY_SECONDS)
        ctx = read_admission_context(execution_id)
        if ctx is not None:
            _miss_trackers.pop(execution_id, None)
            return ctx

    _emit_context_missing(
        execution_id=execution_id,
        thread_id=thread_id,
        attempts=tracker.attempts,
    )
    _miss_trackers.pop(execution_id, None)
    return None


async def handle_worker_completed_event(
    *,
    execution_id: str,
    thread_id: str,
    dispatch_id: str | None,
) -> None:
    existing = read_spawn_state(execution_id)
    if existing is not None and existing.state == "final":
        return

    ctx = read_admission_context(execution_id)
    if ctx is None:
        ctx = await _resolve_context_with_reconcile(
            execution_id=execution_id,
            thread_id=thread_id,
            dispatch_id=dispatch_id,
        )
    if ctx is None:
        return
    if not should_spawn_review_child(ctx):
        return

    terminal = durable_catch_up_terminal(
        execution_id=execution_id,
        thread_id=thread_id,
        dispatch_id=dispatch_id,
    )
    if terminal is None:
        terminal = DurableTerminalEvent(
            signal="frontier.sdk.worker.completed",
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            execution_id=execution_id,
            payload={
                "dispatch_id": dispatch_id,
                "thread_id": thread_id,
                "execution_id": execution_id,
            },
        )
    await _attempt_spawn_for_completion(terminal=terminal, ctx=ctx)


async def reconcile_pending_spawns() -> None:
    for row in list_pending_spawn_states():
        ctx = read_admission_context(row.parent_execution_id)
        if ctx is None or not should_spawn_review_child(ctx):
            continue
        terminal = durable_catch_up_terminal(
            execution_id=row.parent_execution_id,
            thread_id=row.parent_thread_id or "",
            dispatch_id=None,
        )
        if terminal is None:
            continue
        await _attempt_spawn_for_completion(terminal=terminal, ctx=ctx)


async def _listen_loop() -> None:
    from event_store.client import subscribe_events
    from transport_utils import EVENTS_QUERY_SOCK

    while True:
        try:
            async for raw in subscribe_events(
                EVENTS_QUERY_SOCK,
                filter={"signal": "frontier.sdk.worker.completed"},
            ):
                payload = raw.get("payload")
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except json.JSONDecodeError:
                        payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                execution_id = str(
                    raw.get("execution_id") or payload.get("execution_id") or ""
                )
                thread_id = str(payload.get("thread_id") or "")
                dispatch_id = str(payload.get("dispatch_id") or "") or None
                if not execution_id or not thread_id:
                    continue
                await handle_worker_completed_event(
                    execution_id=execution_id,
                    thread_id=thread_id,
                    dispatch_id=dispatch_id,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("review_child_spawn listener error: %s", exc)
            await asyncio.sleep(1.0)


async def _pending_reconcile_loop() -> None:
    while True:
        try:
            await reconcile_pending_spawns()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("review_child pending reconcile error: %s", exc)
        await asyncio.sleep(_PENDING_RECONCILE_INTERVAL_SECONDS)


async def start_review_child_spawn_listener() -> None:
    global _listener_task, _reconcile_task
    if _listener_task is None or _listener_task.done():
        _listener_task = asyncio.create_task(
            _listen_loop(), name="review-child-spawn-listener"
        )
    if _reconcile_task is None or _reconcile_task.done():
        _reconcile_task = asyncio.create_task(
            _pending_reconcile_loop(), name="review-child-pending-reconcile"
        )


def reset_review_child_spawn_hook_for_tests() -> None:
    global _listener_task, _reconcile_task
    _miss_trackers.clear()
    if _listener_task is not None:
        _listener_task.cancel()
        _listener_task = None
    if _reconcile_task is not None:
        _reconcile_task.cancel()
        _reconcile_task = None
