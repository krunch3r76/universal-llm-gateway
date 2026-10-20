"""Continuity resume side-effects (Xvfb :8 endpoint auto-subscribe)."""

from __future__ import annotations

from typing import Any

from bus_watch.cursor_xvfb_subscription import subscribe_cursor_xvfb_house
from bus_watch.ide_hop import cleanup_xvfb_sidebar_on_holder_claim, policy_gui_host
from cursor_bridge.cursor_agents_hop import is_cursor_xvfb_host


def maybe_subscribe_cursor_xvfb_on_resume(
    *,
    thread: str,
    transcript_id: str | None,
    surface: str | None,
    resume_result: dict[str, Any],
    holder: str | None = None,
) -> dict[str, Any]:
    """After ``continuity(op=resume)``, upsert :8 subscription when tab id is known."""
    out = dict(resume_result)
    tid = str(transcript_id or "").strip()
    if not tid:
        return out
    if str(surface or "cursor").strip().lower() not in {"cursor", ""}:
        return out
    if resume_result.get("error"):
        return out
    if not is_cursor_xvfb_host(policy_gui_host(thread)):
        return out
    holder_id = str(holder or f"ide:{tid}").strip()
    out["xvfb_sidebar_cleanup"] = cleanup_xvfb_sidebar_on_holder_claim(thread, holder_id)
    sub = subscribe_cursor_xvfb_house(
        thread,
        tid,
        bound_by="continuity-resume",
        evidence="continuity(op=resume)",
    )
    out["xvfb_subscription"] = sub
    return out


__all__ = ["maybe_subscribe_cursor_xvfb_on_resume"]
