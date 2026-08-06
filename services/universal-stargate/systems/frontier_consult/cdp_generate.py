"""CDP substrate generate front — ``model=cdp/<picker>`` on team_dispatch.

Thin admit front over the native CDP API (``cdp_ask.client`` /
``POST /api/v1/providers/cdp/ask`` via ``claude_bundles.cdp_model_endpoint``).
Forwards optional ``purpose`` (default ``ask``) onto the satellite submit so
operator-proxy missions can set ``purpose=operator-proxy|mission`` without
bare ``project_ask``. Posts on-behalf turns as ``from=cdp`` only after harvest
proof (or failed+stall).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

from claude_bundles.cdp_model_endpoint import (
    CDP_REPLY_FROM,
    CDP_SUBSTRATE,
    DEFAULT_MAX_WALL_S,
)
from claude_bundles.cdp_model_endpoint_staging import (
    CdpStagingError,
    stage_cdp_prompt_with_skills,
)
from claude_bundles.chat_model_match import compose_cdp_model_with_effort
from model_id import ModelId

from .admission import FrontierEndpointError
from .cdp_generate_reconcile import upsert_inflight_leg
from .cdp_generate_worker import run_cdp_worker
from .handoff import create_handoff_thread, post_pointer_turn
from .handoff_response import build_handoff_result, resolve_poll_wait_seconds
from .poll_hint_events import emit_poll_hint_from_handoff

if TYPE_CHECKING:
    from fastapi import Response

    from .route import TeamDispatchGenerateBody

# Retain fire-and-forget CDP worker tasks until done (GC-safe; W9).
_CDP_WORKER_TASKS: set[asyncio.Task[None]] = set()


def is_cdp_model(model: str | None) -> bool:
    """True when ``model`` parses as the CDP substrate (``cdp/<picker>``)."""
    if not model or not str(model).strip():
        return False
    try:
        return ModelId.parse(model).backend_type == "cdp"
    except (TypeError, ValueError):
        return False


def reject_cursor_sdk_seat_with_cdp(
    *,
    seat: str | None,
    model: str | None,
    request_id: str,
) -> None:
    """Reject ``seat=cursor-sdk`` + ``model=cdp/…`` (capability positioning)."""
    if not is_cdp_model(model):
        return
    seat_norm = (seat or "").strip().lower()
    if seat_norm in {"cursor-sdk", "cursor_sdk"}:
        raise FrontierEndpointError(
            request_id=request_id,
            field="model",
            reason=(
                "model=cdp/… selects the web-anthropic-cdp substrate and cannot "
                "combine with seat=cursor-sdk"
            ),
            status_code=422,
            code="cdp_cursor_sdk_seat_rejected",
        )


def reject_dispatch_lane_with_cdp(
    *,
    dispatch_lane: str | None,
    model: str | None,
    request_id: str,
) -> None:
    """Reject ``dispatch_lane`` on CDP — code-lane routing token, not life substrate."""
    if not is_cdp_model(model):
        return
    if not dispatch_lane or not str(dispatch_lane).strip():
        return
    raise FrontierEndpointError(
        request_id=request_id,
        field="dispatch_lane",
        reason=(
            "dispatch_lane is a cursor-sdk/todo routing attribute and cannot "
            "combine with model=cdp/… (life substrate); omit dispatch_lane — "
            "use model, contract, and dispatch_thread_id to identify the leg"
        ),
        status_code=422,
        code="cdp_dispatch_lane_rejected",
    )


def reject_role_with_substrate_model(
    *,
    role: str | None,
    model: str | None,
    request_id: str,
) -> None:
    """Reject ``role`` + ``cdp/`` or ``cursor/`` (role would be silently dropped)."""
    if not role or not model:
        return
    try:
        backend = ModelId.parse(model).backend_type
    except (TypeError, ValueError):
        return
    if backend not in {"cdp", "cursor_sdk"}:
        return
    raise FrontierEndpointError(
        request_id=request_id,
        field="role",
        reason=(
            f"role={role!r} cannot combine with substrate model={model!r}; "
            "omit role (model prefix selects transport) or use a cloud model"
        ),
        status_code=422,
        code="substrate_model_role_conflict",
    )


def _stage_inputs(
    *,
    execution_id: str,
    prompt: str | None,
    sidecar_ref: str | None,
    packet_path: str | None,
    skills: list[str] | None = None,
) -> Any:
    """Stage prompt; ``skills`` → slash manifest for + → Skills attach at runtime.

    Judgment-pair skills are always ensured at staging (even when ``skills`` is
    omitted on light-bounded CDP generate).
    """
    cortex_uri = None
    if isinstance(sidecar_ref, str) and sidecar_ref.startswith("cortex://"):
        cortex_uri = sidecar_ref
    elif isinstance(packet_path, str) and packet_path.startswith("cortex://"):
        cortex_uri = packet_path
    packet_non_cortex = (
        packet_path
        if isinstance(packet_path, str) and not packet_path.startswith("cortex://")
        else None
    )
    sidecar_non_cortex = (
        sidecar_ref
        if isinstance(sidecar_ref, str) and not sidecar_ref.startswith("cortex://")
        else None
    )
    try:
        return stage_cdp_prompt_with_skills(
            execution_id=execution_id,
            prompt_text=prompt,
            prompt_uri=cortex_uri,
            packet_path=packet_non_cortex,
            sidecar_ref=sidecar_non_cortex,
            skills=skills if isinstance(skills, list) else None,
        )
    except CdpStagingError:
        raise


async def dispatch_cdp_generate(
    *,
    request_id: str,
    body: TeamDispatchGenerateBody,
    response: Response,
) -> dict[str, Any]:
    """Admit CDP generate: return poll_hint immediately; proof posts later."""
    model = compose_cdp_model_with_effort(
        str(body.model or ""),
        getattr(body, "reasoning_effort", None),
    )
    if not is_cdp_model(model):
        raise FrontierEndpointError(
            request_id=request_id,
            field="model",
            reason="dispatch_cdp_generate requires model=cdp/<picker>",
            status_code=422,
            code="cdp_model_required",
        )
    reject_cursor_sdk_seat_with_cdp(
        seat=getattr(body, "seat", None),
        model=model,
        request_id=request_id,
    )
    reject_role_with_substrate_model(
        role=getattr(body, "role", None),
        model=model,
        request_id=request_id,
    )
    reject_dispatch_lane_with_cdp(
        dispatch_lane=getattr(body, "dispatch_lane", None),
        model=model,
        request_id=request_id,
    )
    contract = body.contract
    if contract in {"implement", "wrap"}:
        raise FrontierEndpointError(
            request_id=request_id,
            field="contract",
            reason="CDP model-endpoint admits light-bounded/pure-mechanical only",
            status_code=422,
            code="cdp_contract_unsupported",
        )

    prompt = getattr(body, "prompt", None)
    sidecar_ref = getattr(body, "sidecar_ref", None)
    packet_path = getattr(body, "packet_path", None)
    if not any([prompt, sidecar_ref, packet_path]):
        from .dispatch_thread_context import resolve_generate_prompt_body

        prompt = await resolve_generate_prompt_body(
            request_id=request_id,
            role=CDP_REPLY_FROM,
            dispatch_thread_id=body.dispatch_thread_id,
            prompt=None,
            sidecar_ref=None,
            packet_path=None,
        )

    if not any([prompt, sidecar_ref, packet_path]):
        raise FrontierEndpointError(
            request_id=request_id,
            field="prompt",
            reason=(
                "CDP generate requires prompt, sidecar_ref, packet_path, "
                "or dispatch_thread body"
            ),
            status_code=422,
            code="cdp_prompt_missing",
        )

    execution_id = str(uuid.uuid4())
    skills = getattr(body, "skills", None)
    try:
        staged = _stage_inputs(
            execution_id=execution_id,
            prompt=prompt,
            sidecar_ref=sidecar_ref,
            packet_path=packet_path,
            skills=skills if isinstance(skills, list) else None,
        )
    except CdpStagingError as exc:
        field = "skills" if str(exc.code).startswith("cdp_skills") else "prompt"
        raise FrontierEndpointError(
            request_id=request_id,
            field=field,
            reason=exc.reason,
            status_code=422,
            code=exc.code,
        ) from exc

    thread_subject = f"cdp generate — {request_id}"
    pointer_body = (
        f"CDP generate admitted (model={model}, execution_id={execution_id}). "
        f"Poll poll_hint (from_agent=cdp). Terminal only after harvest proof."
    )
    thread_id = body.dispatch_thread_id
    if thread_id and str(thread_id).strip().isdigit():
        pointer_turn = await post_pointer_turn(
            request_id=request_id,
            thread_id=str(thread_id),
            to_agent=CDP_REPLY_FROM,
            subject=thread_subject,
            pointer_body=pointer_body,
            caller_agent=body.caller_agent,
        )
        after_turn = pointer_turn
    else:
        thread_id = await create_handoff_thread(
            request_id=request_id,
            to_agent=CDP_REPLY_FROM,
            subject=thread_subject,
            pointer_body=pointer_body,
            caller_agent=body.caller_agent,
            tags=[
                "agent:cdp",
                "type:generate",
                f"contract:{contract or 'light-bounded'}",
            ],
            handoff_contract=contract or "light-bounded",
            bus_lifecycle=getattr(body, "bus_lifecycle", None),
        )
        after_turn = 1

    timeout_seconds = getattr(body, "timeout_seconds", None)
    max_wall = float(timeout_seconds) if timeout_seconds else DEFAULT_MAX_WALL_S
    upsert_inflight_leg(
        execution_id=execution_id,
        request_id=request_id,
        thread_id=str(thread_id),
        pointer_turn=after_turn,
        caller_agent=body.caller_agent,
        prompt_uri=staged.prompt_uri,
        model_id=str(model),
        max_wall_s=max_wall,
    )

    purpose_raw = getattr(body, "purpose", None)
    purpose = (
        str(purpose_raw).strip()
        if isinstance(purpose_raw, str) and purpose_raw.strip()
        else "ask"
    )
    opts = getattr(body, "generation_options", None) or {}
    worker_kwargs: dict[str, Any] = {
        "execution_id": execution_id,
        "model_id": str(model),
        "thread_id": str(thread_id),
        "caller_agent": body.caller_agent,
        "prompt_uri": staged.prompt_uri,
        "request_id": request_id,
        "pointer_turn": after_turn,
        "max_wall_s": float(timeout_seconds) if timeout_seconds else None,
        "purpose": purpose,
    }
    if isinstance(opts, dict):
        if "harvest_source" in opts:
            worker_kwargs["harvest_source"] = opts["harvest_source"]
        if "expected_size" in opts:
            worker_kwargs["expected_size"] = opts["expected_size"]
        if "download_output" in opts:
            worker_kwargs["download_output"] = opts["download_output"]
    worker_task = asyncio.create_task(
        run_cdp_worker(**worker_kwargs),
        name=f"cdp-worker-{execution_id[:8]}",
    )
    _CDP_WORKER_TASKS.add(worker_task)

    def _log_worker_done(task: asyncio.Task[None]) -> None:
        _CDP_WORKER_TASKS.discard(task)
        if task.cancelled():
            from universal_logging import get_logger

            get_logger(__name__).warning(
                "cdp worker task cancelled: execution_id=%s",
                execution_id,
            )
            return
        exc = task.exception()
        if exc is not None:
            from universal_logging import get_logger

            get_logger(__name__).error(
                "cdp worker task failed: execution_id=%s err=%s",
                execution_id,
                exc,
                exc_info=exc,
            )

    worker_task.add_done_callback(_log_worker_done)

    handoff_fields = build_handoff_result(
        thread_id=str(thread_id),
        to_agent=CDP_REPLY_FROM,
        reply_from_agent=CDP_REPLY_FROM,
        after_turn=after_turn,
        poll_wait_seconds=resolve_poll_wait_seconds(caller_agent=body.caller_agent),
    )
    emit_poll_hint_from_handoff(
        request_id=request_id,
        thread_id=str(thread_id),
        caller_agent=body.caller_agent or "cursor",
        handoff_fields=handoff_fields,
    )
    response.status_code = 202
    return {
        "op": "generate",
        "status": "running",
        "execution_id": execution_id,
        "thread_id": str(thread_id),
        "thread": str(thread_id),
        "to_agent": CDP_REPLY_FROM,
        "reply_from_agent": CDP_REPLY_FROM,
        "resolved_model": model,
        "resolved_contract": contract or "light-bounded",
        "substrate": CDP_SUBSTRATE,
        "cost_source": "unavailable",
        "prompt_uri": staged.prompt_uri,
        "handoff_status": handoff_fields["handoff_status"],
        "poll_hint": handoff_fields["poll_hint"],
        "result_handle": {
            "kind": "dual",
            "execution_id": execution_id,
            "thread_id": str(thread_id),
            "substrate": CDP_SUBSTRATE,
            "durable": False,
        },
        "capabilities": {
            "role": CDP_REPLY_FROM,
            "resolved_model": model,
            "tool_surface": "cdp",
            "substrate": CDP_SUBSTRATE,
            "inline_only": True,
            "tool_access": False,
        },
        "terminal": False,
    }
