"""Admission gates for team/persona and raw frontier dispatch."""

from __future__ import annotations

from typing import Any

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


class TeamGenerateBody(BaseModel):
    model_config = {"extra": "forbid"}

    messages: list[dict[str, Any]]
    agent: str
    model: str | None = None
    system: str = ""
    tools: list[str] | None = None
    reasoning_effort: str | None = None
    generation_options: dict[str, Any] | None = None
    max_tool_turns: int | None = None
    transcript_id: str | None = None
    remote_mcp: bool | None = None
    result_delivery: dict[str, Any] | None = None
    caller_agent: str | None = None
    timeout_seconds: int | None = Field(default=None, gt=0, le=86_400)


class FrontierGenerateBody(BaseModel):
    model_config = {"extra": "forbid"}

    messages: list[dict[str, Any]]
    model: str
    system: str = ""
    reasoning_effort: str | None = None
    generation_options: dict[str, Any] | None = None
    max_tool_turns: int | None = None
    transcript_id: str | None = None
    remote_mcp: bool | None = None
    result_delivery: dict[str, Any] | None = None
    caller_agent: str | None = None
    timeout_seconds: int | None = Field(default=None, gt=0, le=86_400)


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


@team_router.post("/generate", status_code=202, response_model=None)
async def team_generate(
    body: TeamGenerateBody,
    response: Response,
) -> dict[str, Any] | JSONResponse:
    """Persona-required dispatch (team-seat consults)."""
    req = FrontierGenerateRequest(**body.model_dump())
    return await _dispatch(req, response)


@frontier_router.post("/generate", status_code=202, response_model=None)
async def frontier_generate(
    body: FrontierGenerateBody,
    response: Response,
) -> dict[str, Any] | JSONResponse:
    """Persona-free dispatch (raw engine)."""
    req = FrontierGenerateRequest(**body.model_dump())
    return await _dispatch(req, response)
