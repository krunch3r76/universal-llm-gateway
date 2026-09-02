"""Observation events for public chat_session satellite routes."""

from __future__ import annotations

from universal_event_bus.events.event import Event
from universal_event_bus.events.factory import event_factory

from cdp_ask.cse_session_events import emit


@event_factory
def mcp_chat_session_harvested(
    *,
    site: str,
    conversation_id: str,
    outcome: str,
    turn_count: int = 0,
    archive_uri: str | None = None,
    archive_sha256: str | None = None,
    superseded_sha256: str | None = None,
    code: str | None = None,
    opened_on_demand: bool = False,
) -> Event:
    """Emit when harvest completes, refuses, or conflicts after classifier accept."""
    payload: dict[str, object] = {
        "site": site,
        "conversation_id": conversation_id,
        "outcome": outcome,
        "turn_count": turn_count,
    }
    if archive_uri is not None:
        payload["archive_uri"] = archive_uri
    if archive_sha256 is not None:
        payload["archive_sha256"] = archive_sha256
    if superseded_sha256 is not None:
        payload["superseded_sha256"] = superseded_sha256
    if code is not None:
        payload["code"] = code
    if opened_on_demand:
        payload["opened_on_demand"] = True
    return Event(
        signal="mcp.chat.session.harvested",
        role="observation",
        scope="node",
        payload=payload,
    )


@event_factory
def mcp_chat_session_pasted(
    *,
    site: str,
    conversation_id: str,
    ok: bool,
    url: str | None = None,
    archive_uri: str | None = None,
    archive_sha256: str | None = None,
    code: str | None = None,
) -> Event:
    """Emit after paste completes or refuses once grant and classifier passed."""
    payload: dict[str, object] = {
        "site": site,
        "conversation_id": conversation_id,
        "ok": ok,
    }
    if url is not None:
        payload["url"] = url
    if archive_uri is not None:
        payload["archive_uri"] = archive_uri
    if archive_sha256 is not None:
        payload["archive_sha256"] = archive_sha256
    if code is not None:
        payload["code"] = code
    return Event(
        signal="mcp.chat.session.pasted",
        role="observation",
        scope="node",
        payload=payload,
    )


__all__ = ["emit", "mcp_chat_session_harvested", "mcp_chat_session_pasted"]
