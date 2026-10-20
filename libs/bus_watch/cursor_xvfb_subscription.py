"""Auto-subscribe Cursor-on-Xvfb (:8) continuity houses to endpoint wake routing."""

from __future__ import annotations

import re
from typing import Any

import httpx

from bus_watch.digest_budget import _bus

_HOLDER_RE = re.compile(r"^ide:(?P<uuid>[0-9a-f-]{36})$", re.I)


def parse_ide_holder_agent_id(holder: str) -> str | None:
    """Return transcript UUID from ``ide:<uuid>`` holder, else ``None``."""
    m = _HOLDER_RE.match(str(holder or "").strip())
    return m.group("uuid") if m else None


def cursor_xvfb_display_for_gui_host(gui_host: str | None) -> str:
    """Endpoint subscription display label for a Glass GUI host."""
    key = str(gui_host or "").strip().lstrip(":").lower()
    if key == "orion-node":
        return "orion-node"
    return "8"


def cursor_xvfb_subscription_body(
    *,
    thread_scope: str,
    agent_id: str,
    display: str = "8",
    title: str | None = None,
    bound_by: str | None = None,
    evidence: str | None = None,
) -> dict[str, Any]:
    """JSON body for ``POST /endpoint-subscriptions`` on a Glass display."""
    root = str(thread_scope).strip()
    aid = str(agent_id).strip()
    disp = str(display or "8").strip() or "8"
    if not root or not aid:
        raise ValueError("thread_scope and agent_id required")
    return {
        "agent": "cursor",
        "thread_scope": root,
        "surface": "cursor",
        "display": disp,
        "agent_id": aid,
        "title": title if title is not None else f"{root} ",
        "bound_by": bound_by,
        "evidence": evidence,
    }


def subscribe_cursor_xvfb_house(
    thread_scope: str,
    agent_id: str,
    *,
    display: str = "8",
    title: str | None = None,
    bound_by: str | None = None,
    evidence: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Upsert ``cursor`` Glass subscription for one continuity root."""
    body = cursor_xvfb_subscription_body(
        thread_scope=thread_scope,
        agent_id=agent_id,
        display=display,
        title=title,
        bound_by=bound_by,
        evidence=evidence,
    )
    owns = client is None
    try:
        if owns:
            with _bus() as bus:
                resp = bus.post("/endpoint-subscriptions", json=body)
        else:
            resp = client.post("/endpoint-subscriptions", json=body)
    except (Exception, SystemExit) as exc:
        if isinstance(exc, httpx.HTTPError):
            return {
                "ok": False,
                "reason": f"subscription_http:{type(exc).__name__}",
                "detail": str(exc)[:200],
            }
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}"[:200],
            "reason": f"subscription_failed:{type(exc).__name__}",
        }
    payload: dict[str, Any] = {}
    try:
        payload = resp.json()
    except ValueError:
        payload = {"raw": resp.text[:500]}
    ok = resp.status_code < 400
    return {
        "ok": ok,
        "status_code": resp.status_code,
        "body": payload,
        "agent_id": body["agent_id"],
        "thread_scope": body["thread_scope"],
    }


def subscribe_cursor_xvfb_from_holder(
    root_id: str,
    holder: str,
    *,
    bound_by: str | None = None,
    evidence: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Subscribe when ``holder`` is ``ide:<transcript-uuid>``."""
    aid = parse_ide_holder_agent_id(holder)
    if not aid:
        return {"ok": False, "reason": "holder_not_ide_uuid", "holder": holder}
    return subscribe_cursor_xvfb_house(
        root_id,
        aid,
        bound_by=bound_by,
        evidence=evidence,
        client=client,
    )


__all__ = [
    "cursor_xvfb_display_for_gui_host",
    "cursor_xvfb_subscription_body",
    "parse_ide_holder_agent_id",
    "subscribe_cursor_xvfb_from_holder",
    "subscribe_cursor_xvfb_house",
]
