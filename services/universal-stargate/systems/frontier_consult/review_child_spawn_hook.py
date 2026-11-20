"""Durable post-completion review-child spawn for generate/cursor-sdk lane."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Response
from fastapi.responses import JSONResponse
from implement_admission.check_review_substrate import (
    UNKNOWN_MODEL_IDENTITY,
    ConsultantIdentity,
)
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
    abandon_spawn_state,
    delete_admission_context,
    finalize_spawn_state,
    list_pending_spawn_states,
    read_admission_context,
    read_spawn_state,
    try_claim_spawn_pending,
)
from .generate_lane_ac_observer import build_generate_lane_reviewer_prompt
from .review_caller_inventory import build_caller_inventory_block
from .skill_suggest_durable_state import DurableTerminalEvent, durable_catch_up_terminal

logger = get_logger(__name__)

# Matches densify_candidate_ready._DENSIFY_REVIEWER_EMPTY_SCOPE_PREFIX (do-not-touch).
_REVIEWER_EMPTY_SCOPE_PREFIX = "---\nfiles_expected:\n---\n\n"
_GENERATE_OP = "generate"
_CURSOR_SDK_ROLE = "cursor-sdk"
_SPAWN_PROVENANCE = "generate_review_child"

CONTEXT_MISS_MAX_ATTEMPTS = 8
CONTEXT_MISS_RETRY_SECONDS = 0.25
_PENDING_RECONCILE_INTERVAL_SECONDS = 5.0


class ReviewDefaultLoadError(Exception):
    """``load_review_default`` failed for a configured review kind."""

    def __init__(self, kind: str, cause: ValueError) -> None:
        self.kind = kind
        self.cause = cause
        super().__init__(str(cause))


@dataclass(frozen=True, slots=True)
class ReviewerSelection:
    model: str
    identity: ConsultantIdentity
    contract: str | None = None
    effort: str | None = None


_loader_error_logged: set[str] = set()


def _executor_identity(ctx: AdmissionContext) -> ConsultantIdentity:
    from implement_admission.check_review_substrate import (
        consultant_rung,
        model_identity,
    )

    return ConsultantIdentity(
        model_identity(ctx.resolved_model),
        ctx.resolved_effort or consultant_rung(ctx.resolved_model, None),
    )


def is_generate_review_child_lane_wired() -> bool:
    from .generate_admission_context_store import (
        is_generate_review_child_lane_wired as _wired,
    )

    return _wired()


def _load_review_default(kind: str):
    from implement_admission.review_defaults import load_review_default

    return load_review_default(kind)


def select_independent_reviewer(
    resolved_model: str, resolved_effort: str | None = None
) -> ReviewerSelection | None:
    """Pick a reviewer independently measured from the executor seat.

    Returns ``None`` when no admitted alternate exists or either identity or
    rung is unmeasured — fail-closed. Raises :class:`ReviewDefaultLoadError`
    when policy loading fails (caller should log once and not spin reconcile).
    """
    from implement_admission.check_review_substrate import (
        ConsultantIdentity,
        consultant_identity,
        consultant_rung,
        independently_measured,
        is_claude_identity,
        model_identity,
    )

    exec_folded = model_identity(resolved_model)
    if exec_folded == UNKNOWN_MODEL_IDENTITY:
        logger.warning(
            "review_child spawn fail-closed: unknown executor identity for %s",
            resolved_model,
        )
        return None
    kind = (
        "implementation_claude_author"
        if is_claude_identity(exec_folded)
        else "implementation"
    )
    try:
        default = _load_review_default(kind)
    except ValueError as exc:
        raise ReviewDefaultLoadError(kind, exc) from exc
    model = default.model
    effort = resolved_effort or consultant_rung(resolved_model, None)
    executor = ConsultantIdentity(exec_folded, effort)
    reviewer_identity = consultant_identity(model, None)
    if not independently_measured(executor, reviewer_identity):
        return None
    if not _reviewer_model_admitted(model):
        return None
    return ReviewerSelection(
        model=model,
        identity=reviewer_identity,
        contract=default.contract,
        effort=default.effort,
    )


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
    frozen_instruction_text: str | None = None,
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
    parent_instruction: str | None = None
    parent_instruction_bind: str | None = None

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
            parent_instruction = thread_body
            parent_instruction_bind = "frozen_turn"
            bound_class = classify_bound_prompt_class(thread_body)
            bound_digest = bound_prompt_sha256_prefix(thread_body)
    elif (
        prompt_bind_mode in ("explicit_external", "explicit_inline")
        and frozen_instruction_text is not None
        and frozen_instruction_text.strip()
    ):
        packet_text = frozen_instruction_text
        parent_instruction = frozen_instruction_text
        parent_instruction_bind = prompt_bind_mode
        read_outcome = "frozen_admit"
        bound_class = classify_bound_prompt_class(frozen_instruction_text)
        bound_digest = bound_prompt_sha256_prefix(frozen_instruction_text)
    elif thread_id and prompt_bind_mode == "explicit_inline":
        read_outcome = "skipped_explicit_inline"
        bound_class = "caller_prompt"

    trace_body = (
        f"{REASONING_TRACE_SENTINEL}\n# auto review child for generate/cursor-sdk"
    )
    caller_inventory_block = await build_caller_inventory_block(packet_text)
    prompt = build_generate_lane_reviewer_prompt(
        packet_text=packet_text,
        staged_draft_body=draft_body,
        reasoning_trace_body=trace_body,
        parent_instruction=parent_instruction,
        parent_instruction_bind=parent_instruction_bind,
        caller_inventory_block=caller_inventory_block,
    )
    return _ReviewPromptBuild(
        prompt=prompt,
        prompt_bind_mode=bind_mode,
        prompt_turn_number=prompt_turn_number,
        latest_read_outcome=read_outcome,
        bound_prompt_class=bound_class,
        bound_prompt_digest=bound_digest,
    )


def _resolve_review_work_key(parent_ctx: AdmissionContext, delivery_thread: str) -> str:
    work_key = parent_ctx.work_key
    if work_key and str(work_key).strip():
        return str(work_key).strip()
    return f"agent-bus:{delivery_thread}"


def _normalize_parent_caller_agent(parent_ctx: AdmissionContext) -> str:
    from cdp_caller_identity import normalize_cdp_caller_agent

    return normalize_cdp_caller_agent(parent_ctx.caller_agent)


def _json_response_error_code(result: JSONResponse) -> str | None:
    try:
        payload = json.loads(result.body.decode())
    except (AttributeError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if isinstance(payload, dict):
        code = payload.get("code")
        return str(code) if code is not None else None
    return None


async def _dispatch_review_child(
    *,
    request_id: str,
    delivery_thread: str,
    prompt: str,
    reviewer: ReviewerSelection,
    parent_ctx: AdmissionContext,
    parent_execution_id: str,
    prompt_turn_number: int | None = None,
    prompt_bind_mode: str | None = None,
) -> dict[str, Any]:
    """Dispatch one review child for CDP or cursor_sdk reviewer models.

    For CDP reviewers, ``abandon_spawn_state`` (when ``caller_agent`` is not
    usable) runs here after the parent's spawn claim in
    :func:`_attempt_spawn_for_completion` — not before claim.
    """
    from cdp_caller_identity import is_valid_cdp_caller_agent
    from model_id import ModelId

    from .route import TeamDispatchGenerateBody, team_dispatch

    _ = request_id
    backend = ModelId.parse(reviewer.model).backend_type
    if backend not in ("cursor_sdk", "cdp"):
        logger.warning(
            "review_child spawn unsupported reviewer backend=%s model=%s",
            backend,
            reviewer.model,
        )
        return {}

    pinned = prompt_turn_number is not None and prompt_turn_number >= 1
    snapshot = (
        not pinned
        and parent_ctx.prompt_bind_mode in ("explicit_external", "explicit_inline")
        and bool((parent_ctx.frozen_instruction_text or "").strip())
    )
    if not (pinned or snapshot):
        logger.warning(
            "review_child spawn fail-closed: missing frozen_turn pin thread=%s "
            "bind=%s snapshot=%s",
            delivery_thread,
            parent_ctx.prompt_bind_mode,
            parent_ctx.frozen_instruction_text is not None,
        )
        abandon_spawn_state(
            parent_execution_id=parent_execution_id,
            reason="missing_frozen_turn_pin",
        )
        return {}
    child_turn = prompt_turn_number if pinned else None

    work_key = _resolve_review_work_key(parent_ctx, delivery_thread)
    caller_agent = _normalize_parent_caller_agent(parent_ctx)
    contract = reviewer.contract or (
        "check-review" if backend == "cursor_sdk" else "code-review"
    )

    if backend == "cdp":
        if not is_valid_cdp_caller_agent(caller_agent):
            logger.warning(
                "review_child spawn abandoned: unusable caller_agent parent=%s",
                parent_execution_id,
            )
            abandon_spawn_state(
                parent_execution_id=parent_execution_id,
                reason="cdp_caller_agent_missing",
            )
            return {}
        if snapshot:
            from claude_bundles.cdp_model_endpoint_staging import CdpStagingError
            from claude_bundles.cdp_review_prompt_gate import enforce_review_prompt_gate

            try:
                enforce_review_prompt_gate(
                    contract=contract,
                    prompt_text=prompt,
                    dispatch_thread_id=delivery_thread,
                )
            except CdpStagingError as exc:
                logger.warning(
                    "review_child spawn abandoned: review prompt gate refused "
                    "code=%s parent=%s",
                    getattr(exc, "code", None),
                    parent_execution_id,
                )
                abandon_spawn_state(
                    parent_execution_id=parent_execution_id,
                    reason="review_prompt_gate_refused",
                )
                return {}
        child_body = TeamDispatchGenerateBody(
            op=_GENERATE_OP,
            model=reviewer.model,
            job=contract,
            reasoning_effort=reviewer.effort,
            prompt=prompt,
            dispatch_thread_id=delivery_thread,
            caller_agent=caller_agent,
            work_key=work_key,
            auto_review_child=False,
            spawn_review_provenance=_SPAWN_PROVENANCE,
            prompt_turn_number=child_turn,
            prompt_bind_mode=(
                (prompt_bind_mode or "frozen_turn") if pinned else "explicit_external"
            ),
        )
    else:
        scoped_prompt = _REVIEWER_EMPTY_SCOPE_PREFIX + prompt
        child_body = TeamDispatchGenerateBody(
            op=_GENERATE_OP,
            seat=_CURSOR_SDK_ROLE,
            dispatch_thread_id=delivery_thread,
            model=reviewer.model,
            job=contract,
            prompt=scoped_prompt,
            auto_review_child=False,
            spawn_review_provenance=_SPAWN_PROVENANCE,
            prompt_turn_number=child_turn,
            prompt_bind_mode="frozen_turn" if pinned else "explicit_external",
            lane="B",
            read_only=True,
            work_key=work_key,
            caller_agent=caller_agent or None,
        )

    result = await team_dispatch(child_body, Response())
    if isinstance(result, JSONResponse):
        logger.warning(
            "review_child dispatch refused status=%s code=%s parent=%s",
            result.status_code,
            _json_response_error_code(result),
            parent_execution_id,
        )
        return {}

    if not isinstance(result, dict):
        return {}

    child_exec = result.get("execution_id")
    if child_exec and backend == "cursor_sdk":
        from implement_admission.check_review_substrate import consultant_rung

        from .generate_admission_context_store import write_admission_context

        write_admission_context(
            execution_id=str(child_exec),
            auto_review_child=True,
            op=_GENERATE_OP,
            role=_CURSOR_SDK_ROLE,
            resolved_model=reviewer.model,
            parent_dispatch_thread_id=delivery_thread,
            dispatch_thread_id=delivery_thread,
            spawn_template_provenance=_SPAWN_PROVENANCE,
            suppress_review_spawn=True,
            resolved_effort=consultant_rung(reviewer.model, None),
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
            "review_child spawn fail-closed: no coord dispatch_thread_id (worker=%s)",
            parent_thread_id,
        )
        return {}
    build = await _build_generate_lane_review_prompt(
        request_id=request_id,
        parent_dispatch_thread_id=delivery_thread,
        prompt_turn_number=ctx.prompt_turn_number,
        prompt_bind_mode=ctx.prompt_bind_mode,
        frozen_instruction_text=ctx.frozen_instruction_text,
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
        parent_ctx=ctx,
        parent_execution_id=ctx.execution_id,
        prompt_turn_number=build.prompt_turn_number,
        prompt_bind_mode=(
            "frozen_turn"
            if build.prompt_turn_number is not None
            else build.prompt_bind_mode
        ),
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
    executor: ConsultantIdentity,
    dedupe_key: str,
) -> None:
    publish_frontier_event(
        FrontierSdkReviewChildSpawned(
            execution_id=execution_id,
            parent_execution_id=parent_execution_id,
            parent_thread_id=parent_thread_id,
            reviewer_model=reviewer.model,
            reviewer_identity=reviewer.identity.model_identity,
            reviewer_rung=reviewer.identity.rung,
            executor_identity=executor.model_identity,
            executor_rung=executor.rung,
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

    try:
        reviewer = select_independent_reviewer(ctx.resolved_model, ctx.resolved_effort)
    except ReviewDefaultLoadError as exc:
        if exc.kind not in _loader_error_logged:
            logger.error(
                "review_defaults load failed kind=%s: %s",
                exc.kind,
                exc.cause,
            )
            _loader_error_logged.add(exc.kind)
        if existing is not None and existing.state == "pending":
            abandon_spawn_state(
                parent_execution_id=execution_id,
                reason="review_default_load_failed",
            )
        return False
    if reviewer is None:
        logger.warning(
            "review_child spawn fail-closed: no independent reviewer for %s",
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
        executor=_executor_identity(ctx),
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


def _pending_spawn_candidate(
    parent_execution_id: str, parent_thread_id: str
) -> tuple[AdmissionContext, DurableTerminalEvent] | None:
    """Sync event-service read. Callers must run this off the event-loop thread.

    ``query_event_service`` inside ``durable_catch_up_terminal`` is a blocking
    HTTP call (5s timeout). On the asyncio thread it stalls bind and /health.
    """
    ctx = read_admission_context(parent_execution_id)
    if ctx is None or not should_spawn_review_child(ctx):
        return None
    terminal = durable_catch_up_terminal(
        execution_id=parent_execution_id,
        thread_id=parent_thread_id,
        dispatch_id=None,
    )
    if terminal is None:
        return None
    return ctx, terminal


async def reconcile_pending_spawns() -> None:
    rows = await asyncio.to_thread(list_pending_spawn_states)
    for row in rows:
        candidate = await asyncio.to_thread(
            _pending_spawn_candidate,
            row.parent_execution_id,
            row.parent_thread_id or "",
        )
        if candidate is None:
            continue
        ctx, terminal = candidate
        await _attempt_spawn_for_completion(terminal=terminal, ctx=ctx)


async def _listen_loop() -> None:
    # Yield before the subscribe import/handshake so scheduling this task
    # cannot do socket I/O on the turn that creates it.
    await asyncio.sleep(0)
    from event_store.client import SUBSCRIBE_OPEN_TIMEOUT_S, subscribe_events
    from transport_utils import EVENTS_QUERY_SOCK

    while True:
        try:
            async for raw in subscribe_events(
                EVENTS_QUERY_SOCK,
                filter={"signal": "frontier.sdk.worker.completed"},
                open_timeout=SUBSCRIBE_OPEN_TIMEOUT_S,
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
    _loader_error_logged.clear()
    if _listener_task is not None:
        _listener_task.cancel()
        _listener_task = None
    if _reconcile_task is not None:
        _reconcile_task.cancel()
        _reconcile_task = None
