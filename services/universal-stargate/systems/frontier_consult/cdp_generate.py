"""CDP substrate generate front — ``model=cdp/<picker>`` on team_dispatch.

Thin admit front over ``claude_bundles.cdp_model_endpoint.run_cdp_generate``.
Posts on-behalf turns as ``from=cdp`` only after harvest proof (or failed+stall).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

from claude_bundles.cdp_model_endpoint import CDP_REPLY_FROM, CDP_SUBSTRATE
from claude_bundles.cdp_model_endpoint_staging import (
    CdpStagingError,
    stage_prompt_uri,
)
from model_id import ModelId

from .admission import FrontierEndpointError
from .cdp_generate_worker import run_cdp_worker
from .handoff import create_handoff_thread, post_pointer_turn
from .handoff_response import build_handoff_result, resolve_poll_wait_seconds
from .poll_hint_events import emit_poll_hint_from_handoff

if TYPE_CHECKING:
    from fastapi import Response

    from .route import TeamDispatchGenerateBody


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


def _stage_inputs(
    *,
    execution_id: str,
    prompt: str | None,
    sidecar_ref: str | None,
    packet_path: str | None,
) -> Any:
    cortex_uri = None
    if isinstance(sidecar_ref, str) and sidecar_ref.startswith("cortex://"):
        cortex_uri = sidecar_ref
    elif isinstance(packet_path, str) and packet_path.startswith("cortex://"):
        cortex_uri = packet_path
    return stage_prompt_uri(
        execution_id=execution_id,
        prompt_text=prompt,
        prompt_uri=cortex_uri,
        packet_path=packet_path
        if isinstance(packet_path, str) and not packet_path.startswith("cortex://")
        else None,
        sidecar_ref=sidecar_ref
        if isinstance(sidecar_ref, str) and not sidecar_ref.startswith("cortex://")
        else None,
    )


async def dispatch_cdp_generate(
    *,
    request_id: str,
    body: TeamDispatchGenerateBody,
    response: Response,
) -> dict[str, Any]:
    """Admit CDP generate: return poll_hint immediately; proof posts later."""
    model = body.model
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
    try:
        staged = _stage_inputs(
            execution_id=execution_id,
            prompt=prompt,
            sidecar_ref=sidecar_ref,
            packet_path=packet_path,
        )
    except CdpStagingError as exc:
        raise FrontierEndpointError(
            request_id=request_id,
            field="prompt",
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
        await post_pointer_turn(
            request_id=request_id,
            thread_id=str(thread_id),
            to_agent=CDP_REPLY_FROM,
            subject=thread_subject,
            pointer_body=pointer_body,
            caller_agent=body.caller_agent,
        )
        after_turn = 1
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

    asyncio.create_task(
        run_cdp_worker(
            execution_id=execution_id,
            model_id=str(model),
            thread_id=str(thread_id),
            caller_agent=body.caller_agent,
            prompt_uri=staged.prompt_uri,
            request_id=request_id,
        )
    )

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


def build_cdp_pipeline_dispatch_body(
    *,
    model: str,
    request_id: str,
) -> dict[str, Any]:
    """Pipeline-front marker: CDP uses the shared adapter, not CapabilityDispatch."""
    if not is_cdp_model(model):
        raise FrontierEndpointError(
            request_id=request_id,
            field="model",
            reason="build_cdp_pipeline_dispatch_body requires model=cdp/<picker>",
            status_code=422,
            code="cdp_model_required",
        )
    return {
        "substrate": CDP_SUBSTRATE,
        "model": model,
        "pipeline": "cdp-model-endpoint",
        "cost_source": "unavailable",
        "adapter": "claude_bundles.cdp_model_endpoint.run_cdp_generate",
    }
