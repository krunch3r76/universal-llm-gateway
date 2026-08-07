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
from .events import (
    FrontierReviewChildContextMissing,
    FrontierReviewChildPromptBind,
    FrontierSdkReviewChildSpawned,
)
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

# Prefer cursor/* Anthropic-family over anthropic/* API (house rule).
_OPENAI_EXECUTOR_ALTERNATE = "cursor/claude-opus-5"
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
    """Independence family for cross-family review (model weight-class, ¬ substrate)."""
    from implement_admission.check_review_substrate import independence_family

    return independence_family(resolved_model)


def _default_cross_family_reviewer_model() -> str:
    from implement_admission.check_review_substrate import (
        load_check_review_default_model,
    )
    from implement_admission.routing import load_route_policy

    return load_check_review_default_model(load_route_policy())


def select_cross_family_reviewer(resolved_model: str) -> ReviewerSelection | None:
    family = resolve_executor_family(resolved_model)
    if family == "openai":
        model = _OPENAI_EXECUTOR_ALTERNATE
    else:
        model = _default_cross_family_reviewer_model()
    reviewer_family = resolve_executor_family(model)
    if reviewer_family == family:
        return None
    if not _reviewer_model_admitted(model):
        return None
    return ReviewerSelection(model=model, family=reviewer_family)


def _reviewer_model_admitted(model: str) -> bool:
    """Admit cloud API and agent-substrate (cursor/cdp) reviewer models."""
    from model_id import WireModelResolutionError, resolve_wire_model_id

    try:
        resolve_wire_model_id(model, require_cloud=True)
    except WireModelResolutionError:
        return False
    return True


def is_review_child_execution(ctx: AdmissionContext) -> bool:
    """True when this admission is a review-child spawn (any substrate).

    Provenance is authoritative so API ``to_thread`` and cursor-sdk ``generate``
    children both suppress grandchild re-spawn.
    """
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


@dataclass(frozen=True, slots=True)
class _ReviewPromptBuild:
    prompt: str
    prompt_bind_mode: str
    prompt_turn_number: int | None
    latest_read_outcome: str
    bound_prompt_class: str
    bound_prompt_digest: str


def _emit_prompt_bind_instrumentation(
    *,
    parent_execution_id: str,
    child_execution_id: str | None,
    delivery_thread_id: str,
    build: _ReviewPromptBuild,
) -> None:
    publish_frontier_event(
        FrontierReviewChildPromptBind(
            parent_execution_id=parent_execution_id,
            child_execution_id=child_execution_id,
            delivery_thread_id=delivery_thread_id,
            prompt_bind_mode=build.prompt_bind_mode,
            prompt_turn_number=build.prompt_turn_number,
            latest_read_outcome=build.latest_read_outcome,
            bound_prompt_class=build.bound_prompt_class,
            bound_prompt_digest=build.bound_prompt_digest,
        )
    )


async def _build_generate_lane_review_prompt(
    *,
    request_id: str,
    parent_dispatch_thread_id: str | None,
    prompt_turn_number: int | None = None,
    prompt_bind_mode: str | None = None,
) -> _ReviewPromptBuild | None:
    """Build review-child prompt; fail closed on frozen-turn read failure (6655)."""
    from .admission import FrontierEndpointError
    from .dispatch_thread_context import (
        bound_prompt_sha256_prefix,
        classify_bound_prompt_class,
        read_dispatch_thread_body_at_turn,
    )

    thread_id = parent_dispatch_thread_id or ""
    draft_body = f"{COMPOSER_DRAFT_SENTINEL}\n# generate lane closeout for {thread_id}"
    packet_text: str | None = None
    bind_mode = prompt_bind_mode or "sentinel_fallback"
    read_outcome = "skipped"
    bound_class = "sentinel"
    bound_digest = bound_prompt_sha256_prefix(draft_body)

    if thread_id and prompt_turn_number is not None and prompt_turn_number >= 1:
        bind_mode = prompt_bind_mode or "frozen_turn"
        try:
            thread_body = await read_dispatch_thread_body_at_turn(
                request_id=request_id,
                dispatch_thread_id=thread_id,
                role="reviewer",
                turn_number=prompt_turn_number,
            )
        except FrontierEndpointError as exc:
            logger.warning(
                "review_child spawn fail-closed: frozen turn read failed "
                "thread=%s turn=%s code=%s",
                thread_id,
                prompt_turn_number,
                exc.code,
            )
            return None
        except Exception as exc:
            logger.warning(
                "review_child spawn fail-closed: frozen turn read failed "
                "thread=%s turn=%s err=%s",
                thread_id,
                prompt_turn_number,
                exc,
            )
            return None
        read_outcome = "ok"
        if thread_body.strip():
            packet_text = thread_body
            draft_body = f"{COMPOSER_DRAFT_SENTINEL}\n{thread_body}"
            bound_class = classify_bound_prompt_class(thread_body)
            bound_digest = bound_prompt_sha256_prefix(thread_body)
    elif thread_id and prompt_bind_mode == "explicit_inline":
        read_outcome = "skipped_explicit_inline"
        bound_class = "caller_prompt"

    trace_body = (
        f"{REASONING_TRACE_SENTINEL}\n# auto review child for generate/cursor-sdk"
    )
    prompt = build_generate_lane_reviewer_prompt(
        packet_text=packet_text,
        staged_draft_body=draft_body,
        reasoning_trace_body=trace_body,
    )
    return _ReviewPromptBuild(
        prompt=prompt,
        prompt_bind_mode=bind_mode,
        prompt_turn_number=prompt_turn_number,
        latest_read_outcome=read_outcome,
        bound_prompt_class=bound_class,
        bound_prompt_digest=bound_digest,
    )


async def _dispatch_review_child(
    *,
    request_id: str,
    delivery_thread: str,
    prompt: str,
    reviewer: ReviewerSelection,
) -> dict[str, Any]:
    """Admit one review child on the substrate matching ``reviewer.model``.

    ``cursor/*`` → ``op=generate`` + ``seat=cursor-sdk`` (house preference).
    Cloud API models → ``op=to_thread`` + ``role=reviewer`` (existing path).
    """
    from model_id import ModelId

    from .route import TeamDispatchGenerateBody, TeamDispatchToThreadBody, team_dispatch

    use_cursor_sdk = ModelId.parse(reviewer.model).backend_type == "cursor_sdk"
    if use_cursor_sdk:
        child_body: TeamDispatchGenerateBody | TeamDispatchToThreadBody = (
            TeamDispatchGenerateBody(
                op=_GENERATE_OP,
                seat=_CURSOR_SDK_ROLE,
                dispatch_thread_id=delivery_thread,
                model=reviewer.model,
                contract="light-bounded",
                prompt=prompt,
                # Child must not cascade another review-child spawn.
                auto_review_child=False,
                spawn_review_provenance=_SPAWN_PROVENANCE,
            )
        )
        result = await team_dispatch(child_body, Response())
        child_op, child_role = _GENERATE_OP, _CURSOR_SDK_ROLE
    else:
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
        child_op, child_role = _TO_THREAD_OP, _REVIEWER_ROLE

    if not isinstance(result, dict):
        return {}
    child_exec = result.get("execution_id")
    if child_exec:
        from .generate_admission_context_store import write_admission_context

        write_admission_context(
            execution_id=str(child_exec),
            auto_review_child=True,
            op=child_op,
            role=child_role,
            resolved_model=reviewer.model,
            parent_dispatch_thread_id=delivery_thread,
            dispatch_thread_id=delivery_thread,
            spawn_template_provenance=_SPAWN_PROVENANCE,
            suppress_review_spawn=True,
        )
    return result


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
    delivery_thread = ctx.dispatch_thread_id or ctx.parent_dispatch_thread_id
    if not delivery_thread:
        logger.warning(
            "review_child spawn fail-closed: no coord dispatch_thread_id "
            "(worker=%s)",
            parent_thread_id,
        )
        return {}
    build = await _build_generate_lane_review_prompt(
        request_id=request_id,
        parent_dispatch_thread_id=delivery_thread,
        prompt_turn_number=ctx.prompt_turn_number,
        prompt_bind_mode=ctx.prompt_bind_mode,
    )
    if build is None:
        return {}
    _emit_prompt_bind_instrumentation(
        parent_execution_id=ctx.execution_id,
        child_execution_id=None,
        delivery_thread_id=delivery_thread,
        build=build,
    )
    result = await _dispatch_review_child(
        request_id=request_id,
        delivery_thread=delivery_thread,
        prompt=build.prompt,
        reviewer=reviewer,
    )
    child_execution_id = (
        str(result.get("execution_id"))
        if isinstance(result, dict) and result.get("execution_id")
        else None
    )
    if child_execution_id:
        _emit_prompt_bind_instrumentation(
            parent_execution_id=ctx.execution_id,
            child_execution_id=child_execution_id,
            delivery_thread_id=delivery_thread,
            build=build,
        )
    return result


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
