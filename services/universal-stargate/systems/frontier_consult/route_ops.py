"""Admission gates split from route.py. Behavior unchanged."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

import httpx
from fastapi import Response
from fastapi.responses import JSONResponse
from pydantic import Field
from transport_utils import DEFAULT_STARGATE_URL, make_async_client

from .admit_provenance import (
    build_team_dispatch_admit_provenance,
    emit_admit_provenance_missing_event,
    finalize_team_dispatch_admit_response,
)
from .dispatch_thread_context import (
    as_user_message,
)
from .route_models import (
    TeamDispatchGenerateBody,
    TeamDispatchToThreadBody,
    _DispatchCommon,
)
from .route_routers import frontier_router, logger
from .service import (
    FrontierEndpointError,
    FrontierGenerateRequest,
    build_dispatch_body,
)

_FORWARD_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0)

class _FrontierDispatchCommon(_DispatchCommon):
    """Persona-free frontier dispatch still accepts OpenAI-shaped messages."""

    messages: list[dict[str, Any]]


class FrontierDispatchGenerateBody(_FrontierDispatchCommon):
    """``POST /api/v1/frontier/dispatch`` with ``op="generate"`` — persona-free."""

    op: Literal["generate"]
    model: str
    # ``mcp`` knob is exposed only on the persona-free HTTP surface. Default is
    # False — canonical persona-free use is one-shot inline-substrate reasoning.
    # Pass True to enable the MCP tool loop. Agents: prefer MCP ``team_dispatch``.
    mcp: bool = False


class FrontierDispatchToThreadBody(_FrontierDispatchCommon):
    """``POST /api/v1/frontier/dispatch`` with ``op="to_thread"`` — persona-free."""

    op: Literal["to_thread"]
    model: str
    thread: str
    subject: str | None = None
    mcp: bool = False


FrontierDispatchBody = Annotated[
    FrontierDispatchGenerateBody | FrontierDispatchToThreadBody,
    Field(discriminator="op"),
]


def _normalize_op_body(
    body: (
        TeamDispatchGenerateBody
        | TeamDispatchToThreadBody
        | FrontierDispatchGenerateBody
        | FrontierDispatchToThreadBody
    ),
    *,
    source_text: str | None = None,
) -> dict[str, Any]:
    """Translate a discriminated dispatch body into ``FrontierGenerateRequest``
    kwargs.
    """
    common: dict[str, Any] = {
        "messages": as_user_message(source_text)
        if source_text is not None
        else getattr(body, "messages", []),
        "system": body.system,
        "reasoning_effort": body.reasoning_effort,
        "generation_options": body.generation_options,
        "max_tool_turns": body.max_tool_turns,
        "transcript_id": body.transcript_id,
        "caller_agent": body.caller_agent,
        "timeout_seconds": body.timeout_seconds,
        "bus_lifecycle": body.bus_lifecycle,
    }

    # Carry role / model / mcp depending on variant. ``mcp`` is honored on every
    # variant that declares it: the frontier (role-free) surface defaults it to
    # False, the team variants default it to None (team default tools-on, unless
    # the caller opts out) — see service.build_dispatch_body + admission.
    if hasattr(body, "role") and body.role is not None:
        common["role"] = body.role
    if hasattr(body, "seat") and body.seat is not None:
        common["seat"] = body.seat
    if hasattr(body, "dispatch_thread_id"):
        common["dispatch_thread_id"] = body.dispatch_thread_id
    if hasattr(body, "model"):
        common["model"] = body.model
    if hasattr(body, "mcp"):
        common["mcp"] = body.mcp
    if hasattr(body, "job"):
        common["resolved_contract"] = body.job
    if hasattr(body, "density_triage"):
        common["density_triage"] = body.density_triage
    if hasattr(body, "review_opt_out_reason_code"):
        common["review_opt_out_reason_code"] = body.review_opt_out_reason_code
    if hasattr(body, "auto_review_child"):
        common["auto_review_child"] = body.auto_review_child
    if hasattr(body, "spawn_review_provenance"):
        common["spawn_review_provenance"] = body.spawn_review_provenance
    if hasattr(body, "cost_intent"):
        common["cost_intent"] = body.cost_intent
    if hasattr(body, "suppress_cost_warning"):
        common["suppress_cost_warning"] = body.suppress_cost_warning
    if hasattr(body, "cost_intent_reason"):
        common["cost_intent_reason"] = body.cost_intent_reason
    if hasattr(body, "packet_path"):
        common["packet_path"] = body.packet_path
    if hasattr(body, "skills"):
        common["skills"] = body.skills
    if hasattr(body, "server_tools"):
        common["server_tools"] = body.server_tools

    if body.op == "generate":
        common["output_contract"] = "inline"
        common["op"] = "generate"
        return common

    # op == "to_thread"
    thread: str = body.thread  # type: ignore[attr-defined]
    common["output_contract"] = "thread"
    common["target_thread"] = thread
    common["op"] = "to_thread"
    subject: str | None = getattr(body, "subject", None)
    if subject is not None:
        common["reply_subject"] = subject
    return common


async def _dispatch(
    req: FrontierGenerateRequest, response: Response
) -> dict[str, Any] | JSONResponse:
    try:
        from systems.proxy.dependencies import get_proxy

        proxy = get_proxy()
        event_bus = getattr(proxy, "event_bus", None)

        def _publish_event(event: Any) -> None:
            if event_bus is None:
                return
            event_bus.publish_from_sync(event)

        dispatch_body = await build_dispatch_body(req, event_publisher=_publish_event)
        pipeline_opts = dispatch_body.get("pipeline_options", {})
        preview = pipeline_opts.pop("_knob_resolution_preview", None)
        capability_preview = pipeline_opts.pop("_capability_preview", None)
    except FrontierEndpointError as exc:
        logger.warning(
            "dispatch rejected: request_id=%s field=%s reason=%s",
            exc.request_id,
            exc.field,
            exc.reason,
        )
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    async with make_async_client(
        DEFAULT_STARGATE_URL, timeout=_FORWARD_TIMEOUT
    ) as client:
        from systems.pipeline.registry.capability_source import build_tree

        tree = build_tree(proxy)
        member = tree.resolve(dispatch_body["model"])
        if member is None:
            from systems.proxy.routers.api.capabilities import _not_found

            return _not_found(tree, dispatch_body["model"])
        forward = await client.post(member.canonical_url, json=dispatch_body)

    response.status_code = forward.status_code
    try:
        result = forward.json()
    except ValueError as exc:
        logger.error(
            "dispatch forward returned non-JSON: status=%s error=%s",
            forward.status_code,
            exc,
        )
        return {
            "error": {
                "code": "dispatch_invalid_response",
                "message": forward.text[:500],
            }
        }
    if isinstance(result, dict):
        if preview is not None:
            result["knob_resolution"] = preview
        if capability_preview is not None:
            result["capabilities"] = capability_preview
    return result


# ---- dispatch-surface-split Phase 1: op-discriminated routes ----


def hop_park_release_requested(generation_options: dict[str, Any] | None) -> bool:
    """True when generation_options carries hop_park_release=true."""
    opts = generation_options or {}
    return bool(opts.get("hop_park_release"))


def claim_id_from_generation_options(
    generation_options: dict[str, Any] | None,
) -> str | None:
    """House claim id carried beside hop_park_release, or None when absent.

    Empty strings are dropped so a blank option does not become an admit field.
    """
    raw = (generation_options or {}).get("claim_id")
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    return text or None

@frontier_router.post("/dispatch", status_code=202, response_model=None)
async def frontier_dispatch(
    body: FrontierDispatchBody,
    response: Response,
) -> dict[str, Any] | JSONResponse:
    """Persona-free HTTP dispatch (internal / pipeline composition).

    Two ops:
    - ``op="generate"``: returns admission record; poll
      ``pipeline(op="result", execution_id=…)`` for content.
    - ``op="to_thread"``: admits dispatch; model's reply lands on ``thread``.

    Agents: use MCP ``team_dispatch`` (``job=freeform`` for inline-only,
    API roles for tool-loop consults). ``POST /api/v1/team/dispatch`` is the
    role-envelope HTTP twin of MCP ``team_dispatch``.

    This door posts no agent-bus admit turn. A blank caller, lane, or work_key
    is flagged on ``dispatch.admit.provenance_missing`` and on
    ``provenance_missing`` in the 202 body. The admit is not refused.
    """
    request_id = uuid.uuid4().hex[:12]
    admit_provenance = build_team_dispatch_admit_provenance(body, request_id=request_id)
    if admit_provenance is not None:
        emit_admit_provenance_missing_event(admit_provenance)
    req = FrontierGenerateRequest(**_normalize_op_body(body))
    result = await _dispatch(req, response)
    return finalize_team_dispatch_admit_response(result, admit_provenance)
