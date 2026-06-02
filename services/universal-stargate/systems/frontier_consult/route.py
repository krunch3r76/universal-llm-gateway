"""Admission gates for team/persona and raw frontier dispatch."""

from __future__ import annotations

from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

from .service import (
    FrontierEndpointError,
    FrontierGenerateRequest,
    build_dispatch_body,
)

team_router = APIRouter(prefix="/api/v1/team", tags=["team"])
frontier_router = APIRouter(prefix="/api/v1/frontier", tags=["frontier"])
logger = get_logger(__name__)

_FORWARD_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0)


# ---- dispatch-surface-split Phase 1: op-discriminated body models ----


class _DispatchCommon(BaseModel):
    """Shared fields across all op variants — not instantiated directly."""

    model_config = {"extra": "forbid"}

    messages: list[dict[str, Any]]
    system: str = ""
    reasoning_effort: str | None = None
    generation_options: dict[str, Any] | None = None
    max_tool_turns: int | None = None
    transcript_id: str | None = None
    remote_mcp: bool | None = None
    caller_agent: str | None = None
    timeout_seconds: int | None = Field(default=None, gt=0, le=86_400)


class TeamDispatchGenerateBody(_DispatchCommon):
    """``team_dispatch`` with ``op="generate"`` — result returned inline via poll.

    ``role`` selects a ``role:{slug}`` execution contract (Phase 5 of the
    agent-naming cleanup arc). Replaces the legacy ``agent`` field.

    ``dispatch_thread_id`` binds server-owned thread persistence on the
    team-dispatch pipeline (distinct from ``transcript_id`` provenance-only).
    """

    op: Literal["generate"]
    role: str
    dispatch_thread_id: str
    model: str | None = None
    # thread / subject MUST NOT appear — extra="forbid" rejects any caller that
    # supplies them (schema-level enforcement per Phase 0 contract).


class TeamDispatchToThreadBody(_DispatchCommon):
    """``team_dispatch`` with ``op="to_thread"`` — result posted to agent-bus thread.

    ``role`` selects a ``role:{slug}`` execution contract.

    ``thread`` is the agent-bus delivery target. ``dispatch_thread_id`` is the
    cortex compaction key (orthogonal — do not conflate the two).
    """

    op: Literal["to_thread"]
    role: str
    dispatch_thread_id: str
    thread: str
    subject: str | None = None
    model: str | None = None
    # result_delivery MUST NOT appear — derived from thread + role; extra="forbid"
    # rejects any caller that supplies it.


# FastAPI resolves the union via the ``op`` discriminator key.
TeamDispatchBody = Annotated[
    TeamDispatchGenerateBody | TeamDispatchToThreadBody,
    Field(discriminator="op"),
]


class FrontierDispatchGenerateBody(_DispatchCommon):
    """``frontier_dispatch`` with ``op="generate"`` — persona-free, result inline."""

    op: Literal["generate"]
    model: str
    # ``mcp`` knob is exposed only on the persona-free surface. Default is
    # False — the canonical use of frontier_dispatch is one-shot inline-substrate
    # reasoning. Pass True to enable the MCP tool loop.
    mcp: bool = False


class FrontierDispatchToThreadBody(_DispatchCommon):
    """``frontier_dispatch`` with ``op="to_thread"`` — persona-free, result on thread."""  # noqa: E501

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
) -> dict[str, Any]:
    """Translate a discriminated dispatch body into ``FrontierGenerateRequest``
    kwargs.
    """
    common: dict[str, Any] = {
        "messages": body.messages,
        "system": body.system,
        "reasoning_effort": body.reasoning_effort,
        "generation_options": body.generation_options,
        "max_tool_turns": body.max_tool_turns,
        "transcript_id": body.transcript_id,
        "remote_mcp": body.remote_mcp,
        "caller_agent": body.caller_agent,
        "timeout_seconds": body.timeout_seconds,
    }

    # Carry role / model / mcp depending on variant. ``mcp`` is exposed only
    # on the frontier (role-free) surface; team variants derive mcp from
    # the role's frontier_kind in service.build_dispatch_body.
    if hasattr(body, "role"):
        common["role"] = body.role
    if hasattr(body, "dispatch_thread_id"):
        common["dispatch_thread_id"] = body.dispatch_thread_id
    if hasattr(body, "model"):
        common["model"] = body.model
    if hasattr(body, "mcp"):
        common["mcp"] = body.mcp

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
        forward = await client.post("/api/v1/pipelines/dispatch", json=dispatch_body)

    response.status_code = forward.status_code
    try:
        return forward.json()
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


# ---- dispatch-surface-split Phase 1: op-discriminated routes ----


@team_router.post("/dispatch", status_code=202, response_model=None)
async def team_dispatch(
    body: TeamDispatchBody,
    response: Response,
) -> dict[str, Any] | JSONResponse:
    """Persona-required dispatch with explicit op discrimination.

    Two ops:
    - ``op="generate"``: returns admission record; poll
      ``pipeline(op="result", execution_id=…)`` for content.
    - ``op="to_thread"``: admits dispatch; the agent's reply lands on
      ``thread``; tracker terminal status reflects observed reply (Phase 2).

    Use ``frontier_dispatch`` for raw engine calls without a persona.
    """
    req = FrontierGenerateRequest(**_normalize_op_body(body))
    return await _dispatch(req, response)


@frontier_router.post("/dispatch", status_code=202, response_model=None)
async def frontier_dispatch(
    body: FrontierDispatchBody,
    response: Response,
) -> dict[str, Any] | JSONResponse:
    """Persona-free dispatch with explicit op discrimination.

    Two ops:
    - ``op="generate"``: returns admission record; poll
      ``pipeline(op="result", execution_id=…)`` for content.
    - ``op="to_thread"``: admits dispatch; model's reply lands on ``thread``.

    Use ``team_dispatch`` for role-envelope dispatch with team-seat assignment.
    """
    req = FrontierGenerateRequest(**_normalize_op_body(body))
    return await _dispatch(req, response)
