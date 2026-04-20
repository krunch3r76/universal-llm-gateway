"""Endpoint-level signals for ``/api/v1/frontier/generate``."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def FrontierEndpointRequested(  # noqa: N802
    request_id: str,
    agent: str | None,
    model: str | None,
    boot: str,
    has_tools: bool,
) -> Event:
    return Event(
        signal="frontier.endpoint.requested",
        payload={
            "request_id": request_id,
            "agent": agent,
            "model": model,
            "boot": boot,
            "has_tools": has_tools,
        },
        scope="node",
    )


@event_factory
def FrontierEndpointPersonaResolved(  # noqa: N802
    request_id: str,
    agent: str,
    frontier_kind: str | None,
    default_model: str | None,
    allowed_models_count: int,
    tools_count: int | None,
    allowed_options_count: int | None,
) -> Event:
    return Event(
        signal="frontier.endpoint.persona.resolved",
        payload={
            "request_id": request_id,
            "agent": agent,
            "frontier_kind": frontier_kind,
            "default_model": default_model,
            "allowed_models_count": allowed_models_count,
            "tools_count": tools_count,
            "allowed_options_count": allowed_options_count,
        },
        scope="node",
    )


@event_factory
def FrontierEndpointRejected(  # noqa: N802
    request_id: str,
    agent: str | None,
    field: str,
    reason: str,
) -> Event:
    return Event(
        signal="frontier.endpoint.option.rejected",
        payload={
            "request_id": request_id,
            "agent": agent,
            "field": field,
            "reason": reason,
        },
        scope="node",
    )
