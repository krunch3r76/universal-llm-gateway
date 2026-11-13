"""Grok Bot roster list, select, spawn, and retire over CDP.

Ports come from ``config/grok-windows.json`` (:9 → 9339, :10 → 9340, :14 → 9344).
The spawn slot is process-local: a restart or a second process does not
resume it, and this module does not claim exactly-once delivery.

Create-unknown and unconfirmed steer retries do not mutate. Renderer nonce
dedupe is not assumed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any
from urllib.error import URLError

from universal_logging import get_logger

from cursor_bridge.cdp_gateway_forwarder import container_cdp_origin
from cursor_bridge.cdp_pages import list_pages
from cursor_bridge.grok_agent_subscriptions import (
    unsubscribe_grok,
)
from cursor_bridge.grok_computer import _cdp_eval, _pick_grok_page
from cursor_bridge.grok_roster_js import (
    js_list,
    js_retire,
    js_select,
)
from cursor_bridge.grok_window_bindings import cdp_port_for_display

logger = get_logger(__name__)


def _display_key(display: str | int) -> str:
    return str(display or "").strip().lstrip(":")


def _port(display: str | int) -> int | None:
    key = _display_key(display)
    return cdp_port_for_display(key) if key else None


def _unknown(display: str | int) -> dict[str, Any]:
    return {
        "ok": False,
        "code": "unknown_display",
        "reason": f"unknown grok display {display!r}",
        "display": _display_key(display),
    }


class CdpPreSendError(OSError):
    """Page lookup failed before an expression was sent.

    Callers may drop an idempotency slot on this type. Failures from
    ``_cdp_eval`` stay ordinary transport errors because the expression
    may already have run.
    """


async def _eval_display(
    display: str | int, expression: str, *, timeout_s: float = 30.0
) -> Any:
    """Evaluate on the container-aware Grok page. ``list_pages`` is off-loop."""
    port = _port(display)
    if port is None:
        raise ValueError("unknown display")
    origin = container_cdp_origin(f"http://127.0.0.1:{port}")
    try:
        pages = await asyncio.to_thread(list_pages, origin, timeout_s=5.0)
    except (OSError, TimeoutError, URLError, RuntimeError) as exc:
        raise CdpPreSendError(type(exc).__name__) from exc
    page = _pick_grok_page(pages)
    if page is None or not page.get("web_socket_debugger_url"):
        raise CdpPreSendError(f"no Grok Bot CDP page on {origin}")
    return await _cdp_eval(
        str(page["web_socket_debugger_url"]),
        expression,
        await_promise=True,
        timeout_s=timeout_s,
    )


def _cdp_error(display: str | int, exc: BaseException) -> dict[str, Any]:
    return {
        "ok": False,
        "code": "cdp_unreachable",
        "reason": type(exc).__name__,
        "display": _display_key(display),
    }


async def list_grok_agents_async(
    display: str | int,
    *,
    include_hidden: bool = True,
    include_retired: bool = False,
) -> dict[str, Any]:
    """List roster rows. Hidden included and retired excluded unless overridden."""
    if _port(display) is None:
        return _unknown(display)
    try:
        raw = await _eval_display(
            display,
            js_list(include_hidden=include_hidden, include_retired=include_retired),
        )
    except (OSError, TimeoutError, URLError, RuntimeError, ValueError) as exc:
        return _cdp_error(display, exc)
    if not isinstance(raw, dict) or raw.get("ok") is not True:
        return {
            **(raw if isinstance(raw, dict) else {}),
            "ok": False,
            "display": _display_key(display),
        }
    return {**raw, "display": _display_key(display), "cdp_port": _port(display)}


async def grok_selection_current_agent_id_async(display: str | int) -> str:
    """``currentAgentId`` from Grok client selection state (not conversation DOM)."""
    roster = await list_grok_agents_async(display, include_hidden=True)
    if roster.get("ok") is not True:
        return ""
    return str(roster.get("currentAgentId") or "").strip()


def list_grok_agents(
    display: str | int,
    *,
    include_hidden: bool = True,
    include_retired: bool = False,
) -> dict[str, Any]:
    """Synchronous wrapper for :func:`list_grok_agents_async`."""
    return asyncio.run(
        list_grok_agents_async(
            display, include_hidden=include_hidden, include_retired=include_retired
        )
    )


async def select_grok_agent_async(display: str | int, agent_id: str) -> dict[str, Any]:
    """Select ``agent_id`` and require ``currentAgentId`` to match."""
    aid = str(agent_id or "").strip()
    if not aid:
        return {
            "ok": False,
            "code": "agent_id_required",
            "reason": "agent_id is required",
        }
    if _port(display) is None:
        return _unknown(display)
    try:
        raw = await _eval_display(display, js_select(aid))
    except (OSError, TimeoutError, URLError, RuntimeError, ValueError) as exc:
        return _cdp_error(display, exc)
    if not isinstance(raw, dict):
        return {
            "ok": False,
            "code": "bad_cdp_payload",
            "display": _display_key(display),
        }
    out = {**raw, "display": _display_key(display), "agent_id": aid}
    if raw.get("ok") is not True:
        out.setdefault("code", "selection_failed")
    return out


def select_grok_agent(display: str | int, agent_id: str) -> dict[str, Any]:
    """Synchronous wrapper for :func:`select_grok_agent_async`."""
    return asyncio.run(select_grok_agent_async(display, agent_id))


def _voice_refusal(
    *, voice: str, text: str, grant: str | None, authorization_source: str | None
) -> dict[str, Any] | None:
    voice_s = (voice or "self").strip().lower()
    if voice_s not in {"self", "operator_relay"}:
        return {
            "ok": False,
            "code": "unsupported_voice",
            "reason": f"unsupported voice: {voice!r}",
        }
    if voice_s == "operator_relay" and text.strip():
        grant_s = (grant or "").strip().lower()
        if (
            grant_s not in {"operator", "explicit"}
            or not (authorization_source or "").strip()
        ):
            return {
                "ok": False,
                "code": "watermark_missing_authorization",
                "reason": "operator_relay requires grant and authorization_source",
            }
    return None


def _digest(**parts: Any) -> str:
    return hashlib.sha256(
        json.dumps(parts, sort_keys=True, default=str).encode()
    ).hexdigest()


async def spawn_grok_agent_async(
    display: str | int,
    name: str,
    *,
    description: str = "",
    hidden: bool = True,
    mailbox: str | None = None,
    thread_scope: str | None = None,
    text: str | None = None,
    voice: str = "self",
    grant: str | None = None,
    authorization_source: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Create, hide, select, optionally subscribe, then steer.

    The slot is process-local. Unknown create and unconfirmed steer retries
    do not mutate.
    """
    from cursor_bridge.grok_spawn_steps import run_spawn

    return await run_spawn(
        display,
        name,
        description=description,
        hidden=hidden,
        mailbox=mailbox,
        thread_scope=thread_scope,
        text=text,
        voice=voice,
        grant=grant,
        authorization_source=authorization_source,
        idempotency_key=idempotency_key,
    )


def spawn_grok_agent(
    display: str | int,
    name: str,
    *,
    description: str = "",
    hidden: bool = True,
    mailbox: str | None = None,
    thread_scope: str | None = None,
    text: str | None = None,
    voice: str = "self",
    grant: str | None = None,
    authorization_source: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Synchronous wrapper for :func:`spawn_grok_agent_async`."""
    return asyncio.run(
        spawn_grok_agent_async(
            display,
            name,
            description=description,
            hidden=hidden,
            mailbox=mailbox,
            thread_scope=thread_scope,
            text=text,
            voice=voice,
            grant=grant,
            authorization_source=authorization_source,
            idempotency_key=idempotency_key,
        )
    )


async def retire_grok_agent_async(
    display: str | int, agent_id: str, *, name: str = ""
) -> dict[str, Any]:
    """Rename with a retired suffix, hide, then unsubscribe matching rows."""
    del name
    aid = str(agent_id or "").strip()
    if not aid:
        return {"ok": False, "code": "agent_id_required"}
    if _port(display) is None:
        return _unknown(display)
    try:
        raw = await _eval_display(display, js_retire(aid))
    except (OSError, TimeoutError, URLError, RuntimeError, ValueError) as exc:
        return _cdp_error(display, exc)
    if not isinstance(raw, dict) or raw.get("ok") is not True:
        return {
            "ok": False,
            "code": str((raw or {}).get("code") or "retire_failed"),
            "display": _display_key(display),
            "detail": raw,
        }
    unsub = await asyncio.to_thread(
        unsubscribe_grok, agent_id=aid, display=_display_key(display)
    )
    ok = bool(unsub.get("ok"))
    return {
        "ok": ok,
        "code": "retired" if ok else "unsubscribe_partial",
        "agent_id": aid,
        "display": _display_key(display),
        "name": raw.get("name"),
        "hidden": raw.get("hidden"),
        "unsubscribed": unsub.get("unsubscribed"),
        "unsubscribe_errors": unsub.get("unsubscribe_errors"),
    }


def retire_grok_agent(
    display: str | int, agent_id: str, *, name: str = ""
) -> dict[str, Any]:
    """Synchronous wrapper for :func:`retire_grok_agent_async`."""
    return asyncio.run(retire_grok_agent_async(display, agent_id, name=name))


__all__ = [
    "grok_selection_current_agent_id_async",
    "list_grok_agents",
    "list_grok_agents_async",
    "retire_grok_agent",
    "retire_grok_agent_async",
    "select_grok_agent",
    "select_grok_agent_async",
    "spawn_grok_agent",
    "spawn_grok_agent_async",
]
