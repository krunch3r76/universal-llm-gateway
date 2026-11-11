"""Continuity resume/checkpoint side-effects (Xvfb endpoint auto-subscribe)."""

from __future__ import annotations

from typing import Any

from bus_watch.cursor_xvfb_subscription import (
    cursor_xvfb_display_for_gui_host,
    detect_cursor_gui_host,
    resolve_subscription_title,
    subscribe_cursor_xvfb_house,
    sync_policy_gui_host,
)
from bus_watch.fable_lock import claim_fable_lock
from bus_watch.hop_rebind import (
    _list_cursor_subscriptions,
    current_root_subscription_agent_id,
    rebind_predecessor_subscriptions,
)
from bus_watch.ide_hop import cleanup_xvfb_sidebar_on_holder_claim
from bus_watch.ide_hop_retire import retire_departing_tab
from bus_watch.liaison_continuity import liaison_ide_hop, liaison_tick_once
from host_predicates.cursor_hosts import is_cursor_xvfb_host


def _prior_agent_id_from_displaced(displaced_holder: str | None) -> str | None:
    raw = str(displaced_holder or "").strip()
    if not raw:
        return None
    if raw.startswith("ide:"):
        return raw[4:].strip() or None
    if ":" in raw:
        return raw.split(":", 1)[1].strip() or None
    return raw


def _count_subscribed_root_heads(root: str) -> int:
    root_s = str(root or "").strip()
    if not root_s:
        return 0
    try:
        rows = _list_cursor_subscriptions()
    except (Exception, SystemExit):
        return 0
    return sum(
        1
        for row in rows
        if str(row.get("thread_scope") or "").strip() == root_s
        and str(row.get("state") or "") == "subscribed"
    )


def _subscribe_xvfb_tab(
    thread: str,
    tid: str,
    *,
    bound_by: str,
    evidence: str,
) -> dict[str, Any]:
    gui_host = detect_cursor_gui_host(transcript_id=tid, root_id=thread)
    if gui_host is None:
        return {"ok": False, "reason": "gui_host_unresolved"}
    if not is_cursor_xvfb_host(gui_host):
        return {"ok": False, "reason": "not_xvfb_host", "gui_host": gui_host}
    sync_policy_gui_host(thread, gui_host)
    display = cursor_xvfb_display_for_gui_host(gui_host)
    title = resolve_subscription_title(thread)
    return subscribe_cursor_xvfb_house(
        thread,
        tid,
        display=display,
        title=title,
        bound_by=bound_by,
        evidence=evidence,
    )


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
    gui_host = detect_cursor_gui_host(transcript_id=tid, root_id=thread)
    if gui_host is None:
        out["xvfb_subscription"] = {"ok": False, "reason": "gui_host_unresolved"}
        return out
    if not is_cursor_xvfb_host(gui_host):
        return out
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
    lock_claim = out.get("fable_lock_claim") if isinstance(out.get("fable_lock_claim"), dict) else {}
    displaced = lock_claim.get("displaced_holder")
    prior_root_agent_id = _prior_agent_id_from_displaced(
        str(displaced) if displaced else None
    )
    ambiguous_prior = False
    if not prior_root_agent_id:
        if _count_subscribed_root_heads(thread) > 1:
            ambiguous_prior = True
        else:
            try:
                prior_root_agent_id = current_root_subscription_agent_id(thread)
            except (Exception, SystemExit):
                prior_root_agent_id = None
    sub = _subscribe_xvfb_tab(
        thread,
        tid,
        bound_by="continuity-resume",
        evidence="continuity(op=resume)",
    )
    out["xvfb_subscription"] = sub
    if ambiguous_prior:
        out["child_lane_rebind"] = {"ok": False, "reason": "ambiguous_prior"}
    else:
        try:
            out["child_lane_rebind"] = rebind_predecessor_subscriptions(
                thread,
                tid,
                prior_agent_id=prior_root_agent_id,
            )
        except (Exception, SystemExit) as exc:
            out["child_lane_rebind"] = {
                "ok": False,
                "reason": f"rebind_failed:{type(exc).__name__}",
            }
    prior = str(prior_root_agent_id or "").strip()
    if prior and prior != tid:
        try:
            out["predecessor_retire"] = retire_departing_tab(
                thread,
                f"ide:{prior}",
                transcript_id=prior,
            )
        except (Exception, SystemExit) as exc:
            out["predecessor_retire"] = {
                "ok": False,
                "reason": f"retire_failed:{type(exc).__name__}",
            }
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
    gui_host = detect_cursor_gui_host(transcript_id=tid, root_id=thread)
    if gui_host is None:
        out["xvfb_subscription"] = {"ok": False, "reason": "gui_host_unresolved"}
        return out
    if not is_cursor_xvfb_host(gui_host):
        return out
    sub = _subscribe_xvfb_tab(
        thread,
        tid,
        bound_by="continuity-checkpoint",
        evidence="continuity(op=checkpoint)",
    )
    out["xvfb_subscription"] = sub
    return out


def continuity_liaison_tick(
    *,
    thread: str,
    register: str | None = None,
    holder: str | None = None,
) -> dict[str, Any]:
    """MCP ``continuity(op=tick)`` — one liaison digest for a continuity root."""
    try:
        return liaison_tick_once(
            thread,
            register=register,
            holder=holder,
        )
    except (Exception, SystemExit) as exc:
        if isinstance(exc, OSError):
            detail = str(exc)[:200]
        else:
            detail = f"{type(exc).__name__}: {exc}"[:200]
        return {"ok": False, "reason": "tick_failed", "detail": detail}


def continuity_liaison_hop(
    *,
    thread: str,
    transcript_id: str | None = None,
    row: str | None = None,
    gui_host: str | None = None,
    tip_cp: int | None = None,
    find_transcript: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    model_query: str | None = None,
) -> dict[str, Any]:
    """MCP ``continuity(op=hop)`` — attended IDE hop for a continuity root."""
    return liaison_ide_hop(
        thread,
        transcript_id=transcript_id,
        row=row,
        gui_host=gui_host,
        tip_cp=tip_cp,
        find_transcript=find_transcript,
        dry_run=dry_run,
        force=force,
        model_query=model_query,
    )


__all__ = [
    "continuity_liaison_hop",
    "continuity_liaison_tick",
    "maybe_subscribe_cursor_xvfb_on_checkpoint",
    "maybe_subscribe_cursor_xvfb_on_resume",
]
