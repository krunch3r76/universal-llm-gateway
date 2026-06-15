"""API-role ``op=generate`` with default agent-bus thread delivery.

``team_dispatch(op=generate)`` for API functional roles auto-provisions a bus
thread (pointer turn 1) and admits ``output_contract=thread`` delivery — mirroring
cursor-sdk generate-peer semantics. Callers poll ``poll_hint`` (agent-bus wait),
not ``pipeline(op=result)`` alone.

Explicit ``op=to_thread`` with a caller-supplied ``thread`` is unchanged.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

from .admission import (
    FrontierEndpointError,
    enforce_team_dispatch_generate_admit,
    is_cursor_sdk_generate_role,
)
from .densify_triage import validate_generate_density_intake
from .dispatch_thread_context import as_user_message, read_latest_dispatch_thread_body
from .handoff import create_handoff_thread
from .handoff_response import build_api_generate_result, build_handoff_result
from .service import FrontierGenerateRequest

if TYPE_CHECKING:
    from fastapi import Response

    from .route import TeamDispatchGenerateBody

logger = get_logger(__name__)


async def _post_api_role_dispatch_failure_turn(
    *,
    thread_id: str,
    role: str,
    request_id: str,
    caller_agent: str | None,
) -> None:
    """Best-effort failure turn when _dispatch does not admit after thread creation.

    Posts ``from=role`` so ``poll_hint`` ``completion=first_reply_from`` with
    ``from_agent=role`` terminates (``post_worker_failure_turn`` posts
    ``from=dispatch``, which does not satisfy the API-role waiter).
    """
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    allow_unset = os.getenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not token and not allow_unset:
        return
    payload = {
        "thread": thread_id,
        "from": role,
        "to": caller_agent or "dispatch",
        "subject": f"{role} generate dispatch failed ({request_id})",
        "body": (
            f"Automated {role} generate dispatch failed (admission error or "
            "non-JSON forward). Re-dispatch manually or inspect the dispatch error."
        ),
        "status": "open",
        "after_turn": 0,
    }
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            await client.post("/turns", json=payload, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning(
            "api-role generate failure turn not posted: thread=%s role=%s err=%s",
            thread_id,
            role,
            exc,
        )


async def dispatch_api_role_generate(
    *,
    request_id: str,
    body: TeamDispatchGenerateBody,
    response: Response,
) -> dict[str, Any]:
    """Auto-provision bus thread and admit API-role generate on to_thread contract."""
    role = body.role
    if getattr(body, "packet_path", None) is not None:
        raise FrontierEndpointError(
            request_id=request_id,
            field="packet_path",
            reason=(
                "API-role generate consumes dispatch-thread context; packet_path/"
                "source_ref are honored only by the cursor-sdk worker lane "
                "(role=cursor-sdk)"
            ),
            status_code=422,
            code="packet_not_supported_for_api_role",
        )
    if getattr(body, "source_ref", None) is not None:
        raise FrontierEndpointError(
            request_id=request_id,
            field="source_ref",
            reason=(
                "API-role generate consumes dispatch-thread context; packet_path/"
                "source_ref are honored only by the cursor-sdk worker lane "
                "(role=cursor-sdk)"
            ),
            status_code=422,
            code="packet_not_supported_for_api_role",
        )
    if is_cursor_sdk_generate_role(role, request_id=request_id):
        raise FrontierEndpointError(
            request_id=request_id,
            field="role",
            reason="cursor-sdk uses the dedicated SDK generate orchestrator",
            status_code=422,
        )

    enforce_team_dispatch_generate_admit(role, request_id=request_id)

    contract = body.contract
    validate_generate_density_intake(
        request_id=request_id,
        contract=contract,
        density_triage=body.density_triage,
        review_opt_out_reason_code=body.review_opt_out_reason_code,
        auto_review_child=body.auto_review_child,
    )
    if contract == "implement":
        raise FrontierEndpointError(
            request_id=request_id,
            field="contract",
            reason=(
                "op=generate is consult-only; use op=handoff with "
                "contract=implement for implement admission"
            ),
            status_code=422,
        )

    last_user = await read_latest_dispatch_thread_body(
        request_id=request_id,
        dispatch_thread_id=body.dispatch_thread_id,
    )

    thread_subject = f"{role} generate — {request_id}"
    reply_subject = f"{role} reply — {request_id[:8]}"

    thread_id = await create_handoff_thread(
        request_id=request_id,
        to_agent=role,
        subject=thread_subject,
        pointer_body=last_user[:2000],
        caller_agent=body.caller_agent,
        # type:generate + consult persistent → dispatch:close_on_read via handoff.py
        tags=[f"agent:{role}", "type:generate", f"contract:{contract}"],
        handoff_contract=contract,
        bus_lifecycle=getattr(body, "bus_lifecycle", None),
    )

    req = FrontierGenerateRequest(
        messages=as_user_message(last_user),
        model=body.model,
        role=role,
        system=body.system,
        mcp=getattr(body, "mcp", None),
        reasoning_effort=body.reasoning_effort,
        generation_options=body.generation_options,
        max_tool_turns=body.max_tool_turns,
        transcript_id=body.transcript_id,
        dispatch_thread_id=body.dispatch_thread_id,
        remote_mcp=body.remote_mcp,
        caller_agent=body.caller_agent,
        timeout_seconds=body.timeout_seconds,
        output_contract="thread",
        target_thread=thread_id,
        op="to_thread",
        reply_subject=reply_subject,
        resolved_contract=contract,
    )

    from .route import _dispatch

    dispatch_result = await _dispatch(req, response)
    if not isinstance(dispatch_result, dict):
        await _post_api_role_dispatch_failure_turn(
            thread_id=thread_id,
            role=role,
            request_id=request_id,
            caller_agent=body.caller_agent,
        )
        return dispatch_result

    if dispatch_result.get("error"):
        await _post_api_role_dispatch_failure_turn(
            thread_id=thread_id,
            role=role,
            request_id=request_id,
            caller_agent=body.caller_agent,
        )
        return dispatch_result

    resolved_model = ""
    knob = dispatch_result.get("knob_resolution")
    if isinstance(knob, dict):
        resolved_model = str(knob.get("resolved_model") or "")
    caps = dispatch_result.get("capabilities")
    if not resolved_model and isinstance(caps, dict):
        resolved_model = str(caps.get("resolved_model") or "")

    _role_agent, _family, _platform, profile = _resolve_role_profile(
        role, request_id=request_id
    )
    handoff_fields = build_handoff_result(thread_id=thread_id, to_agent=role)
    return build_api_generate_result(
        role=role,
        profile=profile,
        handoff_fields=handoff_fields,
        dispatch_result=dispatch_result,
        thread_id=thread_id,
        resolved_model=resolved_model,
        resolved_contract=contract,
        density_triage=body.density_triage,
        review_opt_out_reason_code=body.review_opt_out_reason_code,
        auto_review_child=body.auto_review_child,
    )


def _resolve_role_profile(role: str, *, request_id: str) -> tuple[str, str, str, Any]:
    from .admission import _resolve_role_or_seat_profile

    return _resolve_role_or_seat_profile(role, request_id=request_id)
