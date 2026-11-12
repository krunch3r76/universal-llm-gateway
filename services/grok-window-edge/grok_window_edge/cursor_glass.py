"""``cursor_glass`` edge handlers: ``wake_tab`` and ``rename_tab``.

CDP-only via ``glass_cdp_core`` — no spawn ledger or agent-bus posts on edge.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse

from cursor_bridge.cursor_window_config import (
    cdp_origin_for_gui_host,
    subscription_display_for_gui_host,
    unknown_gui_host_error,
)
from cursor_bridge.glass_cdp_core import rename_tab_cdp, wake_tab_cdp
from cursor_bridge.wake_delivery import normalize_target

from .cdp import CdpPortRefusedError, assert_cdp_origin_allowed
from .config import CDP_PORT_ALLOWLIST

_SUPPORTED_OPS = frozenset({"wake_tab", "rename_tab"})


def _cdp_refusal(*, op: str, gui_host: str, exc: CdpPortRefusedError) -> dict[str, Any]:
    return {
        "ok": False,
        "code": "cdp_port_refused",
        "detail": str(exc),
        "op": op,
        "gui_host": gui_host,
    }


def _wake_tab_sync(args: dict[str, Any]) -> dict[str, Any]:
    body = str(args.get("body") or "").strip()
    if not body:
        return {
            "ok": False,
            "error": "body_required",
            "detail": "wake_tab requires body",
            "op": "wake_tab",
        }
    gui_host = str(args.get("gui_host") or ":8")
    host_check = unknown_gui_host_error(gui_host)
    if not host_check.get("ok"):
        return {
            **host_check,
            "op": "wake_tab",
        }
    gui_host = str(host_check.get("gui_host") or gui_host)
    agent_id = args.get("agent_id")
    display = subscription_display_for_gui_host(gui_host)
    target = normalize_target(display, "cursor")
    if not target.get("ok"):
        return {
            "ok": False,
            "error": "unknown_target",
            "detail": f"wake_tab unknown gui_host {gui_host!r}",
            "op": "wake_tab",
            "gui_host": gui_host,
        }
    try:
        assert_cdp_origin_allowed(str(target["origin"]))
    except CdpPortRefusedError as exc:
        return _cdp_refusal(op="wake_tab", gui_host=gui_host, exc=exc)
    res = wake_tab_cdp(
        gui_host=gui_host,
        agent_id=str(agent_id).strip() if agent_id else None,
        body=body,
        thread=None,
        allowed_ports=CDP_PORT_ALLOWLIST,
    )
    if res.get("error") == "unknown_target":
        return {
            "ok": False,
            "error": "unknown_target",
            "detail": str(res.get("detail") or ""),
            "op": "wake_tab",
        }
    return res


def _rename_tab_sync(args: dict[str, Any]) -> dict[str, Any]:
    agent_id = str(args.get("agent_id") or "").strip()
    title = str(args.get("title") or "").strip()
    gui_host = str(args.get("gui_host") or ":8")
    if not agent_id:
        return {
            "ok": False,
            "error": "agent_id_required",
            "detail": "rename_tab requires agent_id",
            "op": "rename_tab",
        }
    if not title:
        return {
            "ok": False,
            "error": "title_required",
            "detail": "rename_tab requires title",
            "op": "rename_tab",
        }
    host_check = unknown_gui_host_error(gui_host)
    if not host_check.get("ok"):
        return {
            **host_check,
            "op": "rename_tab",
        }
    gui_host = str(host_check.get("gui_host") or gui_host)
    try:
        origin = cdp_origin_for_gui_host(gui_host)
        assert_cdp_origin_allowed(origin)
    except CdpPortRefusedError as exc:
        return _cdp_refusal(op="rename_tab", gui_host=gui_host, exc=exc)
    res = rename_tab_cdp(
        gui_host=gui_host,
        agent_id=agent_id,
        title=title,
        allowed_ports=CDP_PORT_ALLOWLIST,
    )
    return res


async def cursor_glass(op: str, args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one ``cursor_glass`` op on a worker thread."""
    if op not in _SUPPORTED_OPS:
        return {
            "ok": False,
            "code": "unsupported_op",
            "detail": f"cursor_glass does not support op {op!r}",
            "op": op,
        }
    if op == "wake_tab":
        return await asyncio.to_thread(_wake_tab_sync, args)
    return await asyncio.to_thread(_rename_tab_sync, args)


def cdp_port_from_origin(origin: str) -> int | None:
    """Expose parsed port for tests."""
    return urlparse(origin).port


__all__ = ["cursor_glass", "cdp_port_from_origin"]
