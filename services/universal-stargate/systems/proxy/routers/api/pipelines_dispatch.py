"""Async pipeline dispatch endpoints.

Routes:
- ``POST /api/v1/pipelines/dispatch`` — admit a pipeline run and return
  ``execution_id`` immediately (HTTP 202). The DAG runs in a background
  task retained on ``app.state.pipeline_background_tasks``.
- ``GET  /api/v1/pipelines/executions/{execution_id}`` — fetch current
  tracker state; optional ``?wait=<seconds>`` short-blocks (clamped to
  stay well under the MCP 300s client read-timeout ceiling).

Invariants:
- ∀ error response: ``{"error": {"code", "message"}}`` via ``JSONResponse``
  (¬ ``HTTPException(detail=...)``) — canonical envelope for ``/api/v1/*``.
- Registry lookup happens before tracker registration so unknown-pipeline
  errors never pollute the tracker.
- Tracker ``TrackerCapacityError`` → HTTP 503; ``pipeline.dispatch.rejected``
  is emitted inside the tracker before raising.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from universal_logging import get_logger

from src.schemas.chat_completion import ChatCompletionRequest
from systems.pipeline.core.execution.dispatch_journal import fetch_terminal

from ...dependencies import get_auth_dependency, get_proxy
from ...stargate_core import StargateProxy
from .dispatch_bus_recovery import recover_execution_from_bus_thread

if TYPE_CHECKING:
    from systems.pipeline.core.execution.async_tracker import (
        PipelineExecutionTracker,
    )

logger = get_logger(__name__)
router = APIRouter(tags=["pipelines-dispatch"])


_MAX_WAIT_SECONDS = 60.0

# Pipeline ID of the raw native chat-dispatch path (renamed from
# frontier-dispatch, 2026-07-10, plan:model-dispatch-consolidation phase-1
# fork F-A). ROLE-carrying callers that reach it bare are bypassing role
# admission at the canonical door ``/api/v1/team/dispatch`` (which sets
# ``_endpoint_request_id`` on outgoing pipeline_options); we attach a
# ``hint`` to the 202 envelope so those callers see the right tool on the
# response that returned ``execution_id``. Role-LESS one-shots are
# first-class on this pipeline id — no redirect (reversal of the
# 4765-flagged nudge: role admission adds nothing to a role-less call).
_CHAT_DISPATCH_PIPELINE_ID = "chat-dispatch"
_TEAM_GENERATE_HINT = (
    "role-based consults are best dispatched via `team_dispatch` — role "
    "contracts (allowed_models / allowed_options / mcp_required / "
    "capability_tier), default_model resolution, and birth + briefing "
    'assembly are bypassed on the raw `pipeline(pipeline_id="chat-dispatch")` '
    "path."
)


def _canonical_dispatch_hint_for(dispatch: DispatchRequest) -> str | None:
    """Return the canonical-door hint when applicable, else ``None``.

    Triggers iff the dispatch targets ``chat-dispatch``, carries a ``role``
    in pipeline_options, AND lacks the ``_endpoint_request_id`` marker that
    canonical dispatch routes inject — role admission is being bypassed.
    Role-less direct one-shots are the endorsed shape on this pipeline id
    and receive no hint.
    """
    if dispatch.model != _CHAT_DISPATCH_PIPELINE_ID:
        return None
    opts = dispatch.pipeline_options or {}
    if opts.get("_endpoint_request_id"):
        return None
    if opts.get("role"):
        return _TEAM_GENERATE_HINT
    return None


def _capability_knob_echo(pipeline_options: dict[str, Any]) -> dict[str, Any] | None:
    """Minimal capabilities + knob_resolution echo when ``options.model`` present.

    Additive F-D preview for raw ``/api/v1/pipelines/dispatch`` callers.
    Returns ``None`` when model is absent (fixed-model pipelines stay
    byte-identical). Never raises — card/catalog misses degrade to
    inline-only / rejected knob projection.
    """
    raw_model = pipeline_options.get("model")
    if not isinstance(raw_model, str):
        return None
    resolved = raw_model.strip()
    if not resolved:
        return None

    from agent_seat.profiles import client_side_mcp_tool_loop_admitted
    from llm_adapters.capability_dispatch import project_knob_resolution
    from model_capabilities import CapabilityCardError

    try:
        tool_loop = client_side_mcp_tool_loop_admitted(resolved)
    except CapabilityCardError:
        tool_loop = False

    gen_params = pipeline_options.get("generation_parameters")
    effort: str | None = None
    max_out: int | None = None
    if isinstance(gen_params, dict):
        raw_effort = gen_params.get("reasoning_effort")
        if isinstance(raw_effort, str) and raw_effort.strip():
            effort = raw_effort.strip()
        raw_max = gen_params.get("max_tokens")
        if isinstance(raw_max, int):
            max_out = raw_max

    knob = project_knob_resolution(
        resolved_model=resolved,
        requested_effort=effort,
        requested_max_output=max_out,
    )
    return {
        "capabilities": {
            "resolved_model": resolved,
            "inline_only": not tool_loop,
            "tool_surface": "mcp" if tool_loop else "inline-only",
        },
        "knob_resolution": {
            "value_kind": knob.get("value_kind"),
            "reasoning_native": knob.get("reasoning_native"),
            "status": knob.get("status"),
            "parity": knob.get("parity"),
            "notes": knob.get("notes"),
        },
    }


class ResultDeliveryConfig(BaseModel):
    """Delivery hook for async pipeline results — posts to an agent-bus thread.

    When present on a dispatch request, Stargate posts a compact pointer
    envelope to the configured agent-bus thread at terminal transition
    (completed or failed).  The three bus fields are required; without all
    three the dispatch is rejected with HTTP 422 so callers discover the
    misconfiguration immediately rather than at delivery time.
    """

    # ∀ delivery request: bus_thread ∧ bus_from_agent ∧ bus_to_agent required.
    bus_thread: str
    bus_from_agent: str
    bus_to_agent: str
    bus_subject: str | None = None
    # Caller-supplied plain-text summary appended to the delivery envelope as
    # the ``"summary"`` key — useful for pre-composed human-readable context.
    bus_brief_summary: str | None = None
    # Attachment metadata forwarded verbatim to the agent-bus turn POST body.
    # Each dict must satisfy the AttachmentCreate schema (filename, path, …).
    bus_attachments: list[dict[str, Any]] | None = None
    # Thread post-delivery disposition.  ``ephemeral`` (default) closes the bus
    # thread automatically after a successful delivery POST, using an auto-generated
    # summary derived from the record's terminal state.  ``persistent`` leaves the
    # thread open for cross-agent arcs that need follow-up on the bus.
    bus_lifecycle: Literal["persistent", "ephemeral"] = "ephemeral"


class DispatchRequest(BaseModel):
    """Async pipeline dispatch request body.

    Mirrors the ``/v1/chat/completions`` pipeline invocation shape plus an
    optional ``result_delivery`` hook. Unknown keys are accepted (forwarded
    onto the synthesized ``ChatCompletionRequest`` where appropriate).

    ``caller_agent`` is optional provenance — when set, it flows into the
    tracker record and into the ``pipeline.dispatch.*`` event payloads so
    downstream observability can attribute dispatches to a specific agent.
    Not authenticated; callers can claim any identity.

    Phase 1 (dispatch-surface-split): ``output_contract``, ``target_thread``,
    and ``op`` are set by the canonical Stargate routes (``/api/v1/team/dispatch``
    and ``/api/v1/frontier/dispatch``).  Direct callers via legacy routes omit
    them; the tracker defaults to ``output_contract="inline"`` and ``op=None``.

    For ``op="to_thread"``, ``from_agent`` is the identity Stargate posts as
    when delivering the model's reply to ``target_thread`` (role name for
    team_dispatch, ``frontier:{model_short}`` for ``/api/v1/frontier/dispatch``).
    """

    model_config = {"extra": "allow"}

    model: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    pipeline_options: dict[str, Any] | None = None
    result_delivery: ResultDeliveryConfig | None = None
    caller_agent: str | None = None
    # dispatch-surface-split Phase 1: explicit op discrimination
    output_contract: Literal["inline", "thread"] = "inline"
    target_thread: str | None = None
    op: Literal["generate", "to_thread"] | None = None
    # On-behalf delivery identity (2026-05-22 architectural fix).
    from_agent: str | None = None
    reply_subject: str | None = None
    # Post-delivery thread disposition for ``op="to_thread"``. Omitted ⇒ ephemeral.
    bus_lifecycle: Literal["persistent", "ephemeral"] | None = None


def _resolve_bus_lifecycle(
    dispatch: DispatchRequest,
) -> Literal["persistent", "ephemeral"]:
    """Apply default when the caller omits ``bus_lifecycle``."""
    if dispatch.bus_lifecycle is not None:
        return dispatch.bus_lifecycle
    return "ephemeral"


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Build the canonical ``{"error": {code, message}}`` envelope."""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def _build_chat_completion_request(dispatch: DispatchRequest) -> ChatCompletionRequest:
    """Produce a ``ChatCompletionRequest`` from a validated ``DispatchRequest``.

    ``ChatCompletionRequest`` has ``extra="allow"``, so ``pipeline_options``
    and any unknown fields are preserved for downstream ``prepare_request``.

    Tracker-only fields (``result_delivery``, ``output_contract``,
    ``target_thread``, ``op``) are excluded — they are already stored on
    the tracker record and must not pollute the model-facing request shape.
    """
    tracker_only = {
        "result_delivery",
        "output_contract",
        "target_thread",
        "op",
        "from_agent",
        "reply_subject",
        "bus_lifecycle",
    }
    payload = dispatch.model_dump(exclude_none=True, exclude=tracker_only)
    return ChatCompletionRequest(**payload)


def _get_tracker(proxy: StargateProxy) -> PipelineExecutionTracker | None:
    """Return the shared dispatch tracker if the pipeline system is live."""
    return getattr(proxy, "pipeline_dispatch_tracker", None)


def _iso_utc_now() -> str:
    """Return current UTC ISO-8601 Z timestamp."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@router.post("/pipelines/dispatch", status_code=202)
async def dispatch_pipeline(
    request: Request,
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Admit a pipeline run for async execution and return ``execution_id``.

    Flow:
    1. Parse + validate body (``DispatchRequest`` + ``ResultDeliveryConfig``).
    2. Verify pipeline exists in the registry (fast 404 path).
    3. Mint ``execution_id`` via the executor.
    4. Register with the tracker (emits ``pipeline.dispatch.async`` or 503
       if capacity exhausted by running executions).
    5. Prepare the ``RequestContext`` via the proxy's request preparer.
    6. Spawn ``executor.execute_async`` as a retained background task.
    7. Return HTTP 202 with ``execution_id`` and ``started_at``.
    """
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001 — caller supplied invalid JSON
        return _error_response(
            400, "invalid_json", f"Request body is not valid JSON: {exc}"
        )

    try:
        dispatch = DispatchRequest.model_validate(body)
    except ValidationError as exc:
        return _error_response(
            422,
            "validation_error",
            f"Invalid dispatch request: {exc.errors()}",
        )

    if not proxy.is_pipeline_system_ready or proxy.pipeline_registry is None:
        return _error_response(
            503, "pipeline_system_unavailable", "Pipeline execution unavailable"
        )

    if not proxy.pipeline_registry.is_pipeline(dispatch.model):
        return _error_response(
            404,
            "pipeline_not_found",
            f"Pipeline '{dispatch.model}' is not registered.",
        )

    tracker = _get_tracker(proxy)
    if tracker is None:
        return _error_response(
            503,
            "pipeline_dispatch_unavailable",
            "Async pipeline dispatch tracker is not initialized.",
        )

    executor = proxy.pipeline_executor
    if executor is None:
        return _error_response(
            503,
            "pipeline_executor_unavailable",
            "Pipeline executor is not initialized.",
        )

    execution_id = executor.generate_execution_id()
    started_at = _iso_utc_now()

    from systems.pipeline.core.execution.async_tracker import TrackerCapacityError

    delivery_payload = (
        dispatch.result_delivery.model_dump(exclude_none=True)
        if dispatch.result_delivery is not None
        else None
    )

    try:
        endpoint_request_id = (dispatch.pipeline_options or {}).get(
            "_endpoint_request_id"
        )
        tracker.register_execution(
            execution_id=execution_id,
            pipeline=dispatch.model,
            started_at=started_at,
            result_delivery=delivery_payload,
            caller_agent=dispatch.caller_agent,
            output_contract=dispatch.output_contract,
            target_thread=dispatch.target_thread,
            op=dispatch.op,
            from_agent=dispatch.from_agent,
            reply_subject=dispatch.reply_subject,
            bus_lifecycle=_resolve_bus_lifecycle(dispatch),
            endpoint_request_id=endpoint_request_id,
        )
    except TrackerCapacityError as exc:
        logger.warning("Dispatch rejected (capacity): %s", exc)
        return _error_response(
            503,
            "pipeline_dispatch_capacity_exhausted",
            str(exc),
        )

    chat_request = _build_chat_completion_request(dispatch)

    if dispatch.messages:
        request.state.pipeline_full_messages = list(dispatch.messages)

    try:
        context = await proxy.request_preparer.prepare_request(
            request,
            chat_request,
            model_override=None,
            profile_override=None,
            disable_profile=False,
            is_pipeline=True,
            skip_token_counting=True,
            requested_model=dispatch.model,
        )
    except Exception as exc:
        logger.error("Failed to prepare async dispatch request: %s", exc, exc_info=True)
        tracker.fail_execution(
            execution_id,
            code="pipeline_dispatch_preparation_failed",
            message=f"Failed to prepare request: {exc}",
        )
        return _error_response(
            500,
            "pipeline_dispatch_preparation_failed",
            f"Failed to prepare request: {exc}",
        )

    background_tasks: set[asyncio.Task[Any]] = (
        getattr(request.app.state, "pipeline_background_tasks", None) or set()
    )
    request.app.state.pipeline_background_tasks = background_tasks
    task_index: dict[str, asyncio.Task[Any]] = (
        getattr(request.app.state, "pipeline_task_index", None) or {}
    )
    request.app.state.pipeline_task_index = task_index

    task = asyncio.create_task(
        executor.execute_async(
            context,
            execution_id=execution_id,
            started_at=started_at,
            tracker=tracker,
        ),
        name=f"pipeline-dispatch-{execution_id[:8]}",
    )
    background_tasks.add(task)
    task_index[execution_id] = task

    def _on_task_done(completed_task: asyncio.Task[Any]) -> None:
        background_tasks.discard(completed_task)
        task_index.pop(execution_id, None)

    task.add_done_callback(_on_task_done)

    response_body: dict[str, Any] = {
        "execution_id": execution_id,
        "pipeline": dispatch.model,
        "started_at": started_at,
        "status": "running",
    }
    if dispatch.target_thread is not None:
        response_body["thread"] = dispatch.target_thread
    hint = _canonical_dispatch_hint_for(dispatch)
    if hint is not None:
        response_body["hint"] = hint

    echo = _capability_knob_echo(dispatch.pipeline_options or {})
    if echo is not None:
        response_body.update(echo)

    return JSONResponse(status_code=202, content=response_body)


@router.get("/pipelines/executions/{execution_id}")
async def get_pipeline_execution(
    execution_id: str,
    wait: float = Query(
        0.0,
        ge=0.0,
        description=(
            "Optional short-poll window in seconds (clamped to 60). "
            "Stays well under the MCP 300s client read-timeout."
        ),
    ),
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Fetch tracker state for an async-dispatched pipeline execution."""
    tracker = _get_tracker(proxy)
    if tracker is None:
        return _error_response(
            503,
            "pipeline_dispatch_unavailable",
            "Async pipeline dispatch tracker is not initialized.",
        )

    wait_clamped = min(max(0.0, wait), _MAX_WAIT_SECONDS)
    record = await tracker.wait_for_terminal(execution_id, wait_clamped)
    if record is None:
        journal_record = await fetch_terminal(
            execution_id,
            event_bus=getattr(proxy, "event_bus", None),
        )
        if journal_record is not None:
            return JSONResponse(status_code=200, content=journal_record)
        recovered = await recover_execution_from_bus_thread(
            execution_id,
            url=tracker._agent_bus_url,
            auth_token=tracker._agent_bus_token,
            wait_seconds=wait_clamped,
        )
        if recovered is not None:
            return JSONResponse(status_code=200, content=recovered)
        return _error_response(
            404,
            "execution_id_expired_or_unknown",
            f"Unknown or expired execution_id '{execution_id}'.",
        )

    return JSONResponse(status_code=200, content=record.to_dict())


@router.get("/pipelines/dispatch/stats")
async def get_dispatch_stats(
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Return tracker occupancy snapshot."""
    tracker = _get_tracker(proxy)
    if tracker is None:
        return _error_response(
            503,
            "pipeline_dispatch_unavailable",
            "Async pipeline dispatch tracker is not initialized.",
        )

    now = time.monotonic()
    running = 0
    completed = 0
    failed = 0
    oldest_terminal: float | None = None
    oldest_running: float | None = None
    for record in tracker.records.values():
        if record.status == "running":
            running += 1
            age = now - record.started_at_monotonic
            if oldest_running is None or age > oldest_running:
                oldest_running = age
        elif record.status == "completed":
            completed += 1
            if record.completed_at_monotonic is not None:
                age = now - record.completed_at_monotonic
                if oldest_terminal is None or age > oldest_terminal:
                    oldest_terminal = age
        elif record.status == "failed":
            failed += 1
            if record.completed_at_monotonic is not None:
                age = now - record.completed_at_monotonic
                if oldest_terminal is None or age > oldest_terminal:
                    oldest_terminal = age

    return JSONResponse(
        status_code=200,
        content={
            "running": running,
            "completed": completed,
            "failed": failed,
            "terminal": completed + failed,
            "max_records": tracker.max_records,
            "retention_seconds": tracker.retention_seconds,
            "oldest_terminal_age_seconds": oldest_terminal,
            "oldest_running_age_seconds": oldest_running,
        },
    )


@router.delete("/pipelines/executions/{execution_id}")
async def cancel_pipeline_execution(
    request: Request,
    execution_id: str,
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Cancel an in-flight async-dispatched pipeline execution."""
    tracker = _get_tracker(proxy)
    if tracker is None:
        return _error_response(
            503,
            "pipeline_dispatch_unavailable",
            "Async pipeline dispatch tracker is not initialized.",
        )

    record = tracker.get(execution_id)
    if record is None:
        return _error_response(
            404,
            "execution_id_expired_or_unknown",
            f"Unknown or expired execution_id '{execution_id}'.",
        )

    if record.status in {"completed", "failed"}:
        return JSONResponse(status_code=200, content=record.to_dict())

    task_index: dict[str, asyncio.Task[Any]] = getattr(
        request.app.state, "pipeline_task_index", {}
    )
    task = task_index.get(execution_id)
    if task is None or task.done():
        logger.warning(
            "Cancel requested for execution_id=%s but no task tracked; "
            "marking tracker failed.",
            execution_id,
        )
        tracker.fail_execution(
            execution_id,
            code="pipeline_execution_cancelled",
            message="Cancel requested; no live task found.",
        )
    else:
        task.cancel()

    event_bus = getattr(proxy, "event_bus", None)
    if event_bus is not None:
        from systems.pipeline.core.events.dispatch import PipelineDispatchCancelled

        asyncio.create_task(
            event_bus.publish_nowait(
                PipelineDispatchCancelled(
                    pipeline_id=record.pipeline,
                    execution_id=execution_id,
                    source="operator",
                )
            )
        )

    terminal_record = await tracker.wait_for_terminal(execution_id, timeout_seconds=5.0)
    payload = (
        terminal_record.to_dict()
        if terminal_record is not None
        else {"execution_id": execution_id, "status": "unknown"}
    )
    return JSONResponse(status_code=200, content=payload)
