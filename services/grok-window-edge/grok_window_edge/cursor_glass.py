"""``cursor_glass`` edge handlers: ``wake_tab`` and ``rename_tab``.

Ports the MCP ``cursor_bridge`` relay bodies. CDP dials only after
``assert_cdp_origin_allowed``; thread-based binding resolve is not used on the
edge (no ``agent_bus_store`` import).
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse

import cursor_bridge.cdp_pages as cdp_pages_module
from cursor_bridge.bridge_glass_relay import relay_rename_tab, relay_wake_tab
from cursor_bridge.cdp_pages import list_pages
from cursor_bridge.cursor_window_config import cdp_origin_for_gui_host

from .cdp import CdpPortRefusedError, assert_cdp_origin_allowed

_SUPPORTED_OPS = frozenset({"wake_tab", "rename_tab"})


def _guarded_list_pages(base_url: str, *, timeout_s: float = 3.0) -> list[dict[str, str]]:
    assert_cdp_origin_allowed(base_url)
    return list_pages(base_url, timeout_s=timeout_s)


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
    agent_id = args.get("agent_id")
    try:
        origin = cdp_origin_for_gui_host(gui_host)
        assert_cdp_origin_allowed(origin)
    except CdpPortRefusedError as exc:
        return {
            "ok": False,
            "error": "debug_unreachable",
            "detail": type(exc).__name__,
            "op": "wake_tab",
            "gui_host": gui_host,
        }
    saved_list_pages = cdp_pages_module.list_pages
    try:
        cdp_pages_module.list_pages = _guarded_list_pages
        res = relay_wake_tab(
            gui_host=gui_host,
            agent_id=str(agent_id).strip() if agent_id else None,
            body=body,
        )
    finally:
        cdp_pages_module.list_pages = saved_list_pages
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
    try:
        origin = cdp_origin_for_gui_host(gui_host)
        assert_cdp_origin_allowed(origin)
    except CdpPortRefusedError as exc:
        return {
            "ok": False,
            "error": "debug_unreachable",
            "detail": type(exc).__name__,
            "op": "rename_tab",
            "gui_host": gui_host,
        }
    thread = str(args.get("thread") or "").strip() or None
    return relay_rename_tab(
        gui_host=gui_host,
        agent_id=agent_id,
        title=title,
        thread=thread,
    )


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
