"""Non-selecting probe of one Cursor Glass tab by display and agent id.

Sequence: normalize_target → list_pages → _pick_page → probe_surface →
wake_decision → probe_agent_run_state. No sidebar click, no paste lock, no sleep.
"""

from __future__ import annotations

from typing import Any

from cursor_bridge.cdp_pages import list_pages
from cursor_bridge.chat_surface import probe_surface, wake_decision
from cursor_bridge.glass_agent_run_state import probe_agent_run_state
from cursor_bridge.wake_delivery import _pick_page, normalize_target

_SIDEBAR_ROW_CAP = 40
_PASSTHROUGH_CODES = frozenset(
    {"signed_out", "unknown", "debug_unreachable", "surface_probe_failed"}
)


def _refuse(
    code: str, reason: str, *, display: str, **extra: Any
) -> dict[str, Any]:
    return {"ok": False, "code": code, "reason": reason, "display": display, **extra}


def _rows_meta(surface: dict[str, Any]) -> tuple[int, bool]:
    agent_ids = dict(surface.get("agent_ids") or {})
    rows_seen = len(agent_ids)
    if rows_seen < len(surface.get("titles") or []):
        rows_seen = len(surface.get("titles") or [])
    rows_capped = rows_seen >= _SIDEBAR_ROW_CAP
    return rows_seen, rows_capped


async def inspect_glass_tab(*, display: str, agent_id: str) -> dict[str, Any]:
    """Probe one Cursor tab; never selects a sidebar row."""
    aid = str(agent_id or "").strip()
    disp = str(display or "").strip()
    norm = normalize_target(disp, "cursor")
    if not norm.get("ok"):
        return _refuse(
            str(norm.get("reason") or "unknown_target"),
            f"display {disp!r} is not a registered Cursor window",
            display=disp,
        )
    origin = str(norm["origin"])
    try:
        pages = list_pages(origin)
    except Exception as exc:  # noqa: BLE001
        return _refuse(
            "debug_unreachable",
            f"list_pages failed: {type(exc).__name__}",
            display=disp,
        )
    page = _pick_page(pages, "cursor")
    if page is None:
        return _refuse(
            "no_page",
            "no single Cursor Agents page on this display",
            display=disp,
        )
    ws_url = str(page.get("web_socket_debugger_url") or "")
    if not ws_url:
        return _refuse("no_page", "page has no debug websocket", display=disp)
    try:
        surface = await probe_surface(ws_url)
    except TimeoutError:
        return _refuse(
            "debug_unreachable", "surface probe timed out", display=disp
        )
    except RuntimeError as exc:
        return _refuse(
            "surface_probe_failed",
            str(exc) or "surface probe failed",
            display=disp,
        )
    state = str(surface.get("state") or "unknown")
    if state in _PASSTHROUGH_CODES or state not in {"chat_list"}:
        code = state if state in _PASSTHROUGH_CODES else "unknown"
        return _refuse(
            code,
            f"surface state {state!r}",
            display=disp,
        )
    rows_seen, rows_capped = _rows_meta(surface)
    agent_ids = dict(surface.get("agent_ids") or {})
    if aid not in agent_ids:
        return _refuse(
            "target_absent",
            f"no sidebar row for agent id {aid!r}",
            display=disp,
            rows_seen=rows_seen,
            rows_capped=rows_capped,
        )
    target_title = agent_ids.get(aid)
    active_aid = str(surface.get("active_agent_id") or "").strip()
    target_foreground = active_aid == aid
    run_state = await probe_agent_run_state(ws_url, aid)
    verdict = wake_decision(surface, agent_id=aid)
    return {
        "ok": True,
        "display": disp,
        "endpoint": f":{disp}:{aid}",
        "surface": surface,
        "target_agent_id": aid,
        "target_foreground": target_foreground,
        "target_title": target_title,
        "run_state_probe": run_state if isinstance(run_state, dict) else {},
        "rows_seen": rows_seen,
        "rows_capped": rows_capped,
        "wake_verdict": verdict,
        "ws_url": ws_url,
    }


__all__ = ["inspect_glass_tab"]