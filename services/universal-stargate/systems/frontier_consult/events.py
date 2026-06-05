"""Endpoint-level signals for team/frontier generate routes."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def FrontierEndpointRequested(  # noqa: N802
    request_id: str,
    agent: str | None,
    model: str | None,
) -> Event:
    """Endpoint admission for team/frontier generate routes.

    Role-vs-direct is encoded by ``agent``: if non-null, this was a
    role-based team dispatch; if null, this was the direct frontier dispatch path.

    ``has_tools`` field retired with the public ``tools`` parameter
    (todo:retire-tools-param-from-dispatch-mcp-surface) — the field could
    no longer fire True after the surface graduation.
    """
    return Event(
        signal="frontier.endpoint.requested",
        payload={
            "request_id": request_id,
            "agent": agent,
            "model": model,
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
    allowed_options_count: int | None,
) -> Event:
    """Persona resolution at /team/dispatch and frontier_dispatch endpoints.

    tools_count retired per todo:retire-tools-allowlist-as-caller-concern.
    """
    return Event(
        signal="frontier.endpoint.persona.resolved",
        payload={
            "request_id": request_id,
            "agent": agent,
            "frontier_kind": frontier_kind,
            "default_model": default_model,
            "allowed_models_count": allowed_models_count,
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


@event_factory
def FrontierHandoffRequested(  # noqa: N802
    request_id: str,
    role: str,
    to_agent: str,
    handoff_contract: str | None = None,
    model: str | None = None,
) -> Event:
    """Handoff admission — seat resolved, thread creation pending.

    ``handoff_contract`` is the resolved work-intent (``consult`` | ``implement``).
    ``model`` is the canonical synthetic seat slug when ``model`` was the selector.
    """
    payload: dict[str, str | None] = {
        "request_id": request_id,
        "role": role,
        "to_agent": to_agent,
        "handoff_contract": handoff_contract,
    }
    if model:
        payload["model"] = model
    return Event(
        signal="frontier.handoff.requested",
        payload=payload,
        scope="node",
    )


@event_factory
def FrontierHandoffCreated(  # noqa: N802
    request_id: str,
    to_agent: str,
    thread_id: str,
) -> Event:
    """Handoff thread created on agent-bus."""
    return Event(
        signal="frontier.handoff.created",
        payload={
            "request_id": request_id,
            "to_agent": to_agent,
            "thread_id": thread_id,
        },
        scope="node",
    )
