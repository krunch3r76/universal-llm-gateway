"""Observation events for display-chat operator paste."""

from __future__ import annotations

from universal_event_bus.events.event import Event
from universal_event_bus.events.factory import event_factory

from cdp_ask.cse_session_events import emit


@event_factory
def display_chat_pasted(
    *,
    display: str,
    app: str,
    agent_id: str,
    ok: bool,
    grant: str,
    authorization_source: str,
    sha256_dom: str | None = None,
    sha256_png: str | None = None,
    code: str | None = None,
) -> Event:
    payload: dict[str, object] = {
        "display": display,
        "app": app,
        "agent_id": agent_id,
        "ok": ok,
        "grant": grant,
        "authorization_source": authorization_source,
    }
    if sha256_dom is not None:
        payload["sha256_dom"] = sha256_dom
    if sha256_png is not None:
        payload["sha256_png"] = sha256_png
    if code is not None:
        payload["code"] = code
    return Event(
        signal="display_chat.pasted",
        role="observation",
        scope="node",
        payload=payload,
    )


__all__ = ["emit", "display_chat_pasted"]
