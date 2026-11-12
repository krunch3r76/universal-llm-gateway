"""Grok sidebar select and conversation DOM attribution wait."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from cursor_bridge.chat_surface import probe_surface, wake_decision

GROK_CONVERSATION_IDLE_ROUNDS = 12


def messages_fingerprint(surface: dict[str, Any]) -> str:
    """Fingerprint the latest non-empty prose line on the page."""
    messages = [
        str(m).strip() for m in (surface.get("recent_messages") or []) if str(m).strip()
    ]
    if not messages:
        return ""
    return hashlib.sha256(messages[-1].strip().encode("utf-8")).hexdigest()


def grok_conversation_attributed(
    surface: dict[str, Any],
    *,
    expected_agent_id: str,
    prior_message_fp: str,
    prior_generating: bool,
) -> bool:
    """Heuristic: ``has_active_chat`` only means a composer is present, not pane binding.

    With no pane-owner signal, prose first seen while the baseline marker is active
    is refused once the expected marker is required — fail-closed attribution."""
    expected = (expected_agent_id or "").strip()
    active = str(surface.get("active_agent_id") or "").strip()
    if not expected or active != expected:
        return False
    fp = messages_fingerprint(surface)
    if not fp:
        return True
    if prior_message_fp and fp == prior_message_fp:
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
) -> bool:
    if not active:
        return False
    expected = (expected_agent_id or "").strip()
    baseline = (baseline_agent_id or "").strip()
    if active != expected and active != baseline:
        return True
    if saw_expected_active and active != expected:
        return True
    return False


async def probe_grok_after_select(
    ws_url: str,
    *,
    endpoint: Any,
    expected_agent_id: str,
    baseline_agent_id: str,
    prior_message_fp: str,
    prior_generating: bool,
) -> dict[str, Any]:
    """Heuristic wait: re-probe until attribution or idle rounds exhaust.

    ``has_active_chat`` signals a composer, not which agent owns the pane; without a
    pane-owner signal, new prose that first appeared under the baseline agent id is
    treated as unconfirmed for the expected agent (fail-closed)."""
    meta = {
        "thread": endpoint.thread,
        "slug": endpoint.slug,
        "title": endpoint.title or None,
    }
    saw_expected_active = False
    stale_fp = prior_message_fp
    baseline_moved = False
    stale_fps: set[str] = set()
    if prior_message_fp:
        stale_fps.add(prior_message_fp)

    async def _classify(surface: dict[str, Any]) -> dict[str, Any]:
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
        )
        attributed = grok_conversation_attributed(
            surface,
            expected_agent_id=expected_agent_id,
            prior_message_fp=stale_fp,
            prior_generating=prior_generating or baseline_moved,
        )
        pane_fp = messages_fingerprint(surface)
        if pane_fp and pane_fp in stale_fps:
            attributed = False
        return {"surface": surface, "verdict": verdict, "attributed": attributed}

    async def _probe_round() -> dict[str, Any]:
        nonlocal stale_fp, baseline_moved, saw_expected_active, stale_fps
        surface = await probe_surface(ws_url)
        active = str(surface.get("active_agent_id") or "").strip()
        if _foreign_active_agent(
            active,
            expected_agent_id=expected_agent_id,
            baseline_agent_id=baseline_agent_id,
            saw_expected_active=saw_expected_active,
        ):
            state = await _classify(surface)
            return {
                **state,
                "ok": False,
                "stale_dom": True,
                "code": "conversation_unconfirmed",
                "foreign_agent_id": active,
            }
        expected = (expected_agent_id or "").strip()
        baseline = (baseline_agent_id or "").strip()
        if active and active == baseline and active != expected:
            fp = messages_fingerprint(surface)
            if fp and fp != stale_fp:
                stale_fp = fp
                stale_fps.add(fp)
                baseline_moved = True
        return await _classify(surface)

    state = await _probe_round()
    if state.get("foreign_agent_id") is not None:
        return state
    if state["attributed"] and state["verdict"].get("ok"):
        return {**state, "ok": True, "stale_dom": False}

    for _ in range(GROK_CONVERSATION_IDLE_ROUNDS):
        await asyncio.sleep(0.25)
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
    "messages_fingerprint",
    "probe_grok_after_select",
]
