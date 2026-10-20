"""Continuity resume/checkpoint side-effects (Xvfb endpoint auto-subscribe)."""

from __future__ import annotations

from typing import Any

from bus_watch.cursor_xvfb_subscription import (
    cursor_xvfb_display_for_gui_host,
    subscribe_cursor_xvfb_house,
)
from bus_watch.fable_lock import claim_fable_lock
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
    """After ``continuity(op=resume)``, upsert Xvfb subscription when tab id is known."""
    out = dict(resume_result)
    tid = str(transcript_id or "").strip()
    if not tid:
        return out
    if str(surface or "cursor").strip().lower() not in {"cursor", ""}:
        return out
    if resume_result.get("error"):
        return out
    gui_host = policy_gui_host(thread)
    if not is_cursor_xvfb_host(gui_host):
        return out
    display = cursor_xvfb_display_for_gui_host(gui_host)
    holder_id = str(holder or f"ide:{tid}").strip()
    try:
        out["fable_lock_claim"] = claim_fable_lock(
            holder_id, hop=False, root_id=thread, take_over=True
        )
    except (Exception, SystemExit) as exc:
        out["fable_lock_claim"] = {
            "ok": False,
            "reason": f"claim_failed:{type(exc).__name__}",
        }
    sub = subscribe_cursor_xvfb_house(
        thread,
        tid,
        display=display,
        bound_by="continuity-resume",
        evidence="continuity(op=resume)",
    )
    out["xvfb_subscription"] = sub
    try:
        out["xvfb_sidebar_cleanup"] = cleanup_xvfb_sidebar_on_holder_claim(
            thread, holder_id
        )
    except (Exception, SystemExit) as exc:
        out["xvfb_sidebar_cleanup"] = {
            "ok": False,
            "reason": f"cleanup_failed:{type(exc).__name__}",
        }
    return out


def maybe_subscribe_cursor_xvfb_on_checkpoint(
    *,
    thread: str,
    transcript_id: str | None,
    surface: str | None,
    checkpoint_result: dict[str, Any],
) -> dict[str, Any]:
    """After ``continuity(op=checkpoint)``, upsert subscription when tab id is known."""
    out = dict(checkpoint_result)
    tid = str(transcript_id or "").strip()
    if not tid:
        return out
    if str(surface or "").strip().lower() != "cursor":
        return out
    if checkpoint_result.get("error"):
        return out
    gui_host = policy_gui_host(thread)
    if not is_cursor_xvfb_host(gui_host):
        return out
    display = cursor_xvfb_display_for_gui_host(gui_host)
    sub = subscribe_cursor_xvfb_house(
        thread,
        tid,
        display=display,
        bound_by="continuity-checkpoint",
        evidence="continuity(op=checkpoint)",
    )
    out["xvfb_subscription"] = sub
    return out


__all__ = [
    "maybe_subscribe_cursor_xvfb_on_checkpoint",
    "maybe_subscribe_cursor_xvfb_on_resume",
]
