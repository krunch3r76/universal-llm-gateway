"""Thin relays for endpoint subscription ops."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from ._shared import _structured_relay_error, relay


def _relay_result(result: dict[str, Any], op: str) -> dict[str, Any]:
    if "error" in result:
        structured = _structured_relay_error(result, op=op)
        if structured is not None:
            return structured
        return {"error": f"agent-bus error: {result['error']}"}
    return result


def _subscribe_dispatch(
    *,
    agent: str,
    thread_scope: str,
    surface: str,
    display: str,
    title: str | None = None,
    agent_id: str | None = None,
    bound_by: str | None = None,
    evidence: str | None = None,
) -> dict[str, Any]:
    body = {
        "agent": agent,
        "thread_scope": thread_scope,
        "surface": surface,
        "display": display,
        "title": title,
        "agent_id": agent_id,
        "bound_by": bound_by,
        "evidence": evidence,
    }
    return _relay_result(
        relay("agent-bus", "POST", "/endpoint-subscriptions", body=body),
        "subscribe",
    )


def _unsubscribe_dispatch(
    *,
    agent: str,
    thread_scope: str,
    bound_by: str | None = None,
    evidence: str | None = None,
) -> dict[str, Any]:
    body = {
        "agent": agent,
        "thread_scope": thread_scope,
        "bound_by": bound_by,
        "evidence": evidence,
    }
    return _relay_result(
        relay(
            "agent-bus",
            "POST",
            "/endpoint-subscriptions/unsubscribe",
            body=body,
        ),
        "unsubscribe",
    )


def _subscriptions_dispatch(
    *,
    agent: str | None = None,
    thread_scope: str | None = None,
) -> dict[str, Any]:
    params = {
        key: value
        for key, value in (("agent", agent), ("thread_scope", thread_scope))
        if value is not None
    }
    path = "/endpoint-subscriptions"
    if params:
        path = f"{path}?{urlencode(params)}"
    return _relay_result(relay("agent-bus", "GET", path), "subscriptions")


def _delivery_bindings_resolve_dispatch(
    *,
    house_id: str,
    intent: str,
    agent: str | None = None,
) -> dict[str, Any]:
    query: dict[str, str] = {"house_id": house_id, "intent": intent}
    if agent is not None:
        query["agent"] = agent
    params = urlencode(query)
    path = f"/delivery-bindings/resolve?{params}"
    return _relay_result(relay("agent-bus", "GET", path), "delivery_bindings_resolve")
