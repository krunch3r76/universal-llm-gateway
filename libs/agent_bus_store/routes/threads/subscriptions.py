"""HTTP routes for endpoint subscribe, unsubscribe, and list."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from openapi_mcp.binding import x_mcp
from pydantic import BaseModel

from ...db.endpoint_subscriptions import (
    SubscribeError,
    list_subscriptions,
    subscribe_endpoint,
    unsubscribe_endpoint,
)
from ...events.endpoint_subscribed import (
    emit_endpoint_subscribed,
    emit_endpoint_unsubscribed,
)
from . import router as threads_router

router = threads_router


class SubscribeBody(BaseModel):
    agent: str
    thread_scope: str
    surface: str
    display: str
    title: str | None = None
    agent_id: str | None = None
    bound_by: str | None = None
    evidence: str | None = None


class UnsubscribeBody(BaseModel):
    agent: str
    thread_scope: str
    bound_by: str | None = None
    evidence: str | None = None
    expected_agent_id: str | None = None


def _refuse(exc: SubscribeError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": exc.code},
    )


@router.post(
    "/endpoint-subscriptions",
    openapi_extra=x_mcp("subscribe", tool="agent_bus"),
)
async def subscribe_route(body: SubscribeBody) -> dict[str, Any]:
    """Append a subscription row when the endpoint identity changes."""
    try:
        result = subscribe_endpoint(**body.model_dump())
    except SubscribeError as exc:
        raise _refuse(exc) from exc
    if result["appended"]:
        row = result["row"]
        prior = result["prior"] or {}
        emit_endpoint_subscribed(
            agent=row["agent"],
            thread_scope=row["thread_scope"],
            surface=row["surface"],
            display=row["display"],
            title=row["title"],
            agent_id=row["agent_id"],
            subscription_id=row["id"],
            prior_subscription_id=prior.get("id"),
            prior_surface=prior.get("surface"),
            prior_display=prior.get("display"),
            prior_title=prior.get("title"),
            prior_agent_id=prior.get("agent_id"),
            bound_by=row["bound_by"],
        )
    return result


@router.post(
    "/endpoint-subscriptions/unsubscribe",
    openapi_extra=x_mcp("unsubscribe", tool="agent_bus"),
)
async def unsubscribe_route(body: UnsubscribeBody) -> dict[str, Any]:
    """Append an unsubscribe tombstone for the current row."""
    result = unsubscribe_endpoint(**body.model_dump())
    if result["appended"]:
        row = result["row"]
        prior = result["prior"] or {}
        emit_endpoint_unsubscribed(
            agent=row["agent"],
            thread_scope=row["thread_scope"],
            subscription_id=row["id"],
            prior_subscription_id=prior.get("id"),
            bound_by=row["bound_by"],
        )
    return result


@router.get(
    "/endpoint-subscriptions",
    openapi_extra=x_mcp("subscriptions", tool="agent_bus"),
)
async def subscriptions_route(
    agent: str | None = None, thread_scope: str | None = None
) -> dict[str, Any]:
    """List the current subscription row per (agent, thread_scope)."""
    return {"subscriptions": list_subscriptions(agent, thread_scope)}
