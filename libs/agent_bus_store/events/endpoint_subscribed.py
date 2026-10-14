"""Observation events for endpoint subscription authority changes."""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory

from .publisher import emit as _publish


@event_factory
def AgentBusEndpointSubscribed(  # noqa: N802
    agent: str,
    thread_scope: str,
    surface: str | None,
    display: str | None,
    title: str | None,
    agent_id: str | None,
    subscription_id: int,
    prior_subscription_id: int | None,
    prior_surface: str | None,
    prior_display: str | None,
    prior_title: str | None,
    prior_agent_id: str | None,
    bound_by: str | None,
) -> Event:
    """Signal: mcp.agentbus.endpoint.subscribed"""
    payload: dict[str, Any] = {
        "agent": agent,
        "thread_scope": thread_scope,
        "surface": surface,
        "display": display,
        "title": title,
        "agent_id": agent_id,
        "subscription_id": subscription_id,
        "prior_subscription_id": prior_subscription_id,
        "prior_surface": prior_surface,
        "prior_display": prior_display,
        "prior_title": prior_title,
        "prior_agent_id": prior_agent_id,
        "bound_by": bound_by,
    }
    return Event(
        signal="mcp.agentbus.endpoint.subscribed",
        payload=payload,
        role="observation",
    )


@event_factory
def AgentBusEndpointUnsubscribed(  # noqa: N802
    agent: str,
    thread_scope: str,
    subscription_id: int,
    prior_subscription_id: int | None,
    bound_by: str | None,
) -> Event:
    """Signal: mcp.agentbus.endpoint.unsubscribed"""
    payload: dict[str, Any] = {
        "agent": agent,
        "thread_scope": thread_scope,
        "subscription_id": subscription_id,
        "prior_subscription_id": prior_subscription_id,
        "bound_by": bound_by,
    }
    return Event(
        signal="mcp.agentbus.endpoint.unsubscribed",
        payload=payload,
        role="observation",
    )


def emit_endpoint_subscribed(**kwargs: Any) -> None:
    """Publish after a subscribe row is appended."""
    event = AgentBusEndpointSubscribed(**kwargs)
    _publish(event.signal, event.payload, role=event.role or "observation")


def emit_endpoint_unsubscribed(**kwargs: Any) -> None:
    """Publish after an unsubscribe tombstone is appended."""
    event = AgentBusEndpointUnsubscribed(**kwargs)
    _publish(event.signal, event.payload, role=event.role or "observation")
