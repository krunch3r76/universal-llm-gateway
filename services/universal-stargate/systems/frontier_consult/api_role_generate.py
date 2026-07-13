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
    enforce_check_review_substrate_admission,
    enforce_team_dispatch_generate_admit,
    is_cursor_sdk_generate_role,
)
from .cursor_sdk_thread_reuse import (
    api_split_warning,
    resolve_generate_thread_targets,
)
from .densify_triage import validate_generate_density_intake
from .dispatch_thread_context import as_user_message, read_latest_dispatch_thread_body
from .handoff import (
    build_generate_dispatch_pointer,
    create_handoff_thread,
    extract_generate_pointer_summary,
    post_pointer_turn,
)
from .handoff_response import (
    build_api_generate_result,
    build_handoff_result,
    resolve_poll_wait_seconds,
)
from .service import FrontierGenerateRequest

if TYPE_CHECKING:
    from fastapi import Response

    from .route import TeamDispatchGenerateBody

logger = get_logger(__name__)


def _emit_dispatch_thread_event(event_factory: Any, **kwargs: Any) -> None:
    try:
        from systems.proxy.dependencies import get_proxy

        proxy = get_proxy()
        event_bus = getattr(proxy, "event_bus", None)
        if event_bus is not None:
            event_bus.publish_from_sync(event_factory(**kwargs))
    except Exception:
        return


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


def _read_api_role_packet_body(
    *,
    request_id: str,
    packet_path: str,
) -> str:
    from .handoff import _resolve_packet_file, _workspaces_root

    packet_file = _resolve_packet_file(_workspaces_root().resolve(), packet_path)
    if packet_file is None:
        raise FrontierEndpointError(
            request_id=request_id,
            field="packet_path",
            reason=(
                f"packet_path {packet_path!r} not found or unreadable under "
                "workspaces/cortex sandbox"
            ),
            status_code=422,
            code="packet_path_unreadable",
        )
    return packet_file.read_text(encoding="utf-8", errors="replace")


async def dispatch_api_role_generate(
    *,
    request_id: str,
    body: TeamDispatchGenerateBody,
    response: Response,
) -> dict[str, Any]:
    """Auto-provision bus thread and admit API-role generate on to_thread contract."""
    role = body.role
    if getattr(body, "source_ref", None) is not None:
        raise FrontierEndpointError(
            request_id=request_id,
            field="source_ref",
            reason=(
                "API-role generate consumes dispatch-thread context; packet_path/"
                "source_ref are honored only by the cursor-sdk worker lane "
                "(seat=cursor-sdk)"
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

    enforce_team_dispatch_generate_admit(
        role,
        request_id=request_id,
        caller_agent=body.caller_agent,
    )
    enforce_check_review_substrate_admission(
        role,
        getattr(body, "model", None),
        request_id=request_id,
    )

    contract = body.contract
    from implement_admission.check_review_substrate import (
        is_check_review_api_role,
        resolve_check_review_model,
    )

    effective_model = getattr(body, "model", None)
    if is_check_review_api_role(role) and effective_model is None:
        effective_model = resolve_check_review_model(role, None).resolved_model
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

    packet_path = getattr(body, "packet_path", None)
    if packet_path is not None:
        last_user = _read_api_role_packet_body(
            request_id=request_id,
            packet_path=packet_path,
        )
    else:
        last_user = await read_latest_dispatch_thread_body(
            request_id=request_id,
            dispatch_thread_id=body.dispatch_thread_id,
        )

    thread_subject = f"{role} generate — {request_id}"
    reply_subject = f"{role} reply — {request_id[:8]}"

    pointer_body = build_generate_dispatch_pointer(
        lane=role,
        contract=contract,
        dispatch_thread_id=body.dispatch_thread_id,
        correlation_id=request_id,
        summary=extract_generate_pointer_summary(last_user),
    )

    (
        reuse_id,
        _parent,
        is_auto,
        reuse_after_turn,
    ) = await resolve_generate_thread_targets(
        reuse_thread=getattr(body, "reuse_thread", None),
        dispatch_thread_id=body.dispatch_thread_id,
        role_lane="api",
        split_thread=getattr(body, "split_thread", False),
    )

    if reuse_id is not None:
        _emit_dispatch_thread_event(
            _dispatch_thread_reused_event,
            thread=reuse_id,
            dispatch_thread_id=body.dispatch_thread_id,
            lane="api",
            is_auto=is_auto,
        )
        pointer_turn = await post_pointer_turn(
            request_id=request_id,
            thread_id=reuse_id,
            to_agent=role,
            subject=thread_subject,
            pointer_body=pointer_body,
            caller_agent=body.caller_agent,
        )
        thread_id = reuse_id
        after_turn = pointer_turn
    else:
        thread_id = await create_handoff_thread(
            request_id=request_id,
            to_agent=role,
            subject=thread_subject,
            pointer_body=pointer_body,
            caller_agent=body.caller_agent,
            # type:generate + consult persistent → dispatch:close_on_read via handoff.py
            tags=[f"agent:{role}", "type:generate", f"contract:{contract}"],
            handoff_contract=contract,
            bus_lifecycle=getattr(body, "bus_lifecycle", None),
        )
        after_turn = 1
        if (
            body.dispatch_thread_id
            and body.dispatch_thread_id.strip().isdigit()
        ):
            _emit_dispatch_thread_event(
                _dispatch_thread_split_event,
                thread=thread_id,
                dispatch_thread_id=body.dispatch_thread_id,
                lane="api",
            )

    req = FrontierGenerateRequest(
        messages=as_user_message(last_user),
        model=effective_model,
        role=role,
        system=body.system,
        mcp=getattr(body, "mcp", None),
        reasoning_effort=body.reasoning_effort,
        generation_options=body.generation_options,
        max_tool_turns=body.max_tool_turns,
        transcript_id=body.transcript_id,
        dispatch_thread_id=body.dispatch_thread_id,
        caller_agent=body.caller_agent,
        timeout_seconds=body.timeout_seconds,
        skills=body.skills,
        output_contract="thread",
        target_thread=thread_id,
        op="to_thread",
        reply_subject=reply_subject,
        resolved_contract=contract,
        bus_lifecycle=(
            "persistent"
            if reuse_id is not None
            else getattr(body, "bus_lifecycle", None)
        ),
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
    handoff_fields = build_handoff_result(
        thread_id=thread_id,
        to_agent=role,
        after_turn=after_turn,
        poll_wait_seconds=resolve_poll_wait_seconds(caller_agent=body.caller_agent),
    )
    result = build_api_generate_result(
        role=role,
        profile=profile,
        handoff_fields=handoff_fields,
        dispatch_result=dispatch_result,
        thread_id=thread_id,
        resolved_model=resolved_model,
        resolved_contract=contract,
        durable=False,
        density_triage=body.density_triage,
        review_opt_out_reason_code=body.review_opt_out_reason_code,
        auto_review_child=body.auto_review_child,
    )
    split_warning = api_split_warning(
        reuse_thread=reuse_id,
        parent_dispatch_thread_id=_parent,
        split_thread=getattr(body, "split_thread", False),
    )
    if split_warning:
        result["warnings"] = list(result.get("warnings") or []) + [split_warning]
    return result


def _resolve_role_profile(role: str, *, request_id: str) -> tuple[str, str, str, Any]:
    from .admission import _resolve_role_or_seat_profile

    return _resolve_role_or_seat_profile(role, request_id=request_id)


def _dispatch_thread_reused_event(**kwargs: Any) -> Any:
    from systems.pipeline.core.events.delivery import DispatchThreadReused

    return DispatchThreadReused(**kwargs)


def _dispatch_thread_split_event(**kwargs: Any) -> Any:
    from systems.pipeline.core.events.delivery import DispatchThreadSplit

    return DispatchThreadSplit(**kwargs)
