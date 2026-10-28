"""Invalidate lane TAB_READY after bridge rename so the tab must re-post TAB_READY."""

from __future__ import annotations

CONSUMERS: tuple[str, ...] = ("mcp",)

from typing import Any

from cursor_bridge.chat_target import name_instruction


def tab_ready_instruction(title: str) -> str:
    """Paste-ready reminder for the in-tab agent after a bridge-driven rename."""
    return name_instruction(title)


def post_tab_gone_for_lane(thread_id: str, *, reason: str = "bridge_rename") -> dict[str, Any]:
    """Post TAB_GONE from cursor so lane readiness epoch advances before a fresh TAB_READY."""
    tid = str(thread_id or "").strip()
    if not tid:
        return {"ok": False, "error": "thread_required"}
    body = (
        f"TAB readiness cleared after {reason}. "
        "Post a fresh TAB_READY only after the sidebar shows the expected title."
    )
    from bus_watch.digest_budget import _bus

    payload = {
        "thread": tid,
        "from": "cursor",
        "to": "web-anthropic",
        "subject": "TAB_GONE",
        "body": body,
    }
    try:
        with _bus() as client:
            resp = client.post("/threads/send", json=payload)
    except Exception as exc:  # noqa: BLE001 — relay boundary
        return {"ok": False, "error": "tab_gone_send_failed", "detail": str(exc)[:200]}
    if resp.status_code >= 400:
        return {
            "ok": False,
            "error": "tab_gone_http",
            "detail": f"http_{resp.status_code}",
            "body": resp.text[:300],
        }
    try:
        data = resp.json()
    except ValueError:
        return {"ok": False, "error": "tab_gone_non_json"}
    turn = data.get("turn") if isinstance(data.get("turn"), dict) else {}
    return {
        "ok": True,
        "thread_id": tid,
        "turn_number": turn.get("turn_number"),
        "tab_ready_rehandshake": True,
    }


__all__ = ["post_tab_gone_for_lane", "tab_ready_instruction"]
