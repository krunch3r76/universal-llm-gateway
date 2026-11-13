"""Grok sidebar select and conversation DOM attribution wait."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from cursor_bridge.chat_surface import probe_surface, wake_decision

GROK_CONVERSATION_IDLE_ROUNDS = 12
_GROK_PANE_NUDGE_ROUND = 6


def messages_fingerprint(surface: dict[str, Any]) -> str:
    """Fingerprint the latest non-empty prose line on the page."""
    messages = [
        str(m).strip() for m in (surface.get("recent_messages") or []) if str(m).strip()
    ]
    if not messages:
        return ""
    return hashlib.sha256(messages[-1].strip().encode("utf-8")).hexdigest()


def grok_pane_positive_signal(
    surface: dict[str, Any],
    *,
    expected_agent_id: str,
    selection_agent_id: str,
) -> bool:
    """Pane attribution matches ``expected_agent_id`` (DOM agent id or composer bind)."""
    expected = (expected_agent_id or "").strip()
    if not expected:
        return False
    sel = (selection_agent_id or "").strip()
    if sel != expected:
        return False
    active = str(surface.get("active_agent_id") or "").strip()
    return active == expected


def grok_conversation_attributed(
    surface: dict[str, Any],
    *,
    expected_agent_id: str,
    prior_message_fp: str,
    prior_generating: bool,
    selection_agent_id: str = "",
) -> bool:
    """Heuristic: ``has_active_chat`` only means a composer is present, not pane binding.

    With no pane-owner signal, prose first seen while any marker other than the
    expected agent is active is refused once the expected marker is required —
    fail-closed attribution."""
    expected = (expected_agent_id or "").strip()
    active = str(surface.get("active_agent_id") or "").strip()
    sel = (selection_agent_id or "").strip()
    fp = messages_fingerprint(surface)
    if sel == expected:
        if not fp:
            return True
        if active != expected:
            return False
    if not expected or active != expected:
        if sel == expected and not active and not fp:
            return True
        return False
    if not fp:
        return True
    if prior_message_fp and fp == prior_message_fp:
        if sel == expected:
            return False
        return False
    if not prior_generating:
        return True
    return surface.get("has_active_chat") is True and surface.get("generating") is False


def _foreign_active_agent(
    active: str,
    *,
    expected_agent_id: str,
    baseline_agent_id: str,
    saw_expected_active: bool,
    selection_agent_id: str,
) -> bool:
    if not active:
        if (selection_agent_id or "").strip() == (expected_agent_id or "").strip():
            return False
        return False
    expected = (expected_agent_id or "").strip()
    baseline = (baseline_agent_id or "").strip()
    if active != expected and active != baseline:
        return True
    if saw_expected_active and active != expected:
        return True
    return False


async def _selection_agent_id(display: str | int | None) -> str:
    if display is None:
        return ""
    from cursor_bridge.grok_agent_ops import grok_selection_current_agent_id_async

    return await grok_selection_current_agent_id_async(display)


async def probe_grok_after_select(
    ws_url: str,
    *,
    endpoint: Any,
    expected_agent_id: str,
    baseline_agent_id: str,
    prior_message_fp: str,
    prior_generating: bool,
    display: str | int | None = None,
) -> dict[str, Any]:
    """Heuristic wait: re-probe until attribution or idle rounds exhaust.

    Uses Grok ``currentAgentId`` (selection app state) as a positive switch signal
    while the conversation pane may still show the prior agent's transcript.
    """
    meta = {
        "thread": endpoint.thread,
        "slug": endpoint.slug,
        "title": endpoint.title or None,
    }
    saw_expected_active = False
    baseline_moved = False
    stale_fps: set[str] = set()
    if prior_message_fp:
        stale_fps.add(prior_message_fp)
    pane_nudged = False

    async def _classify(
        surface: dict[str, Any], *, selection_agent_id: str
    ) -> dict[str, Any]:
        nonlocal saw_expected_active
        active = str(surface.get("active_agent_id") or "").strip()
        if active == (expected_agent_id or "").strip():
            saw_expected_active = True
        verdict = wake_decision(
            surface,
            thread=str(meta["thread"] or ""),
            slug=str(meta["slug"] or ""),
            title=meta["title"],
            agent_id=expected_agent_id,
            selection_agent_id=selection_agent_id,
        )
        attributed = grok_conversation_attributed(
            surface,
            expected_agent_id=expected_agent_id,
            prior_message_fp=prior_message_fp,
            prior_generating=prior_generating or baseline_moved,
            selection_agent_id=selection_agent_id,
        )
        pane_fp = messages_fingerprint(surface)
        if (
            pane_fp
            and pane_fp in stale_fps
            and not grok_pane_positive_signal(
                surface,
                expected_agent_id=expected_agent_id,
                selection_agent_id=selection_agent_id,
            )
        ):
            attributed = False
        return {"surface": surface, "verdict": verdict, "attributed": attributed}

    async def _probe_round() -> dict[str, Any]:
        nonlocal baseline_moved, pane_nudged
        selection_agent_id = await _selection_agent_id(display)
        surface = await probe_surface(ws_url)
        active = str(surface.get("active_agent_id") or "").strip()
        if _foreign_active_agent(
            active,
            expected_agent_id=expected_agent_id,
            baseline_agent_id=baseline_agent_id,
            saw_expected_active=saw_expected_active,
            selection_agent_id=selection_agent_id,
        ):
            state = await _classify(surface, selection_agent_id=selection_agent_id)
            return {
                **state,
                "ok": False,
                "stale_dom": True,
                "code": "conversation_unconfirmed",
                "foreign_agent_id": active,
            }
        if active != (expected_agent_id or "").strip():
            fp = messages_fingerprint(surface)
            if fp and fp not in stale_fps:
                stale_fps.add(fp)
                baseline_moved = True
        return await _classify(surface, selection_agent_id=selection_agent_id)

    state = await _probe_round()
    if state.get("foreign_agent_id") is not None:
        return state
    if state["attributed"] and state["verdict"].get("ok"):
        return {**state, "ok": True, "stale_dom": False}

    for round_i in range(GROK_CONVERSATION_IDLE_ROUNDS):
        await asyncio.sleep(0.25)
        if (
            not pane_nudged
            and display is not None
            and round_i >= _GROK_PANE_NUDGE_ROUND
        ):
            selection_agent_id = await _selection_agent_id(display)
            if selection_agent_id == (expected_agent_id or "").strip() and not state.get(
                "attributed"
            ):
                from cursor_bridge.grok_agent_ops import select_grok_agent_async

                await select_grok_agent_async(display, expected_agent_id)
                pane_nudged = True
        state = await _probe_round()
        if state.get("foreign_agent_id") is not None:
            return state
        if state["attributed"] and state["verdict"].get("ok"):
            return {**state, "ok": True, "stale_dom": False}

    stale = not state["attributed"]
    return {
        **state,
        "ok": state["verdict"].get("ok") is True and not stale,
        "stale_dom": stale,
        "code": "conversation_unconfirmed" if stale else None,
    }


__all__ = [
    "GROK_CONVERSATION_IDLE_ROUNDS",
    "grok_conversation_attributed",
    "grok_pane_positive_signal",
    "messages_fingerprint",
    "probe_grok_after_select",
]
