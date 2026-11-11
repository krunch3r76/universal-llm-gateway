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
) -> bool:
    """True when sidebar selection and message pane belong to ``expected_agent_id``."""
    expected = (expected_agent_id or "").strip()
    active = str(surface.get("active_agent_id") or "").strip()
    if not expected or active != expected:
        return False
    fp = messages_fingerprint(surface)
    if not fp:
        return True
    return not (prior_message_fp and fp == prior_message_fp)


async def probe_grok_after_select(
    ws_url: str,
    *,
    endpoint: Any,
    expected_agent_id: str,
    prior_message_fp: str,
) -> dict[str, Any]:
    """Re-probe until the conversation DOM matches ``expected_agent_id`` or idles out."""
    meta = {
        "thread": endpoint.thread,
        "slug": endpoint.slug,
        "title": endpoint.title or None,
    }

    async def _classify(surface: dict[str, Any]) -> dict[str, Any]:
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
            prior_message_fp=prior_message_fp,
        )
        return {"surface": surface, "verdict": verdict, "attributed": attributed}

    first = await probe_surface(ws_url)
    state = await _classify(first)
    if state["attributed"] and state["verdict"].get("ok"):
        return {**state, "ok": True, "stale_dom": False}

    prev = messages_fingerprint(first)
    for _ in range(GROK_CONVERSATION_IDLE_ROUNDS):
        await asyncio.sleep(0)
        nxt = await probe_surface(ws_url)
        state = await _classify(nxt)
        if state["attributed"] and state["verdict"].get("ok"):
            return {**state, "ok": True, "stale_dom": False}
        fp = messages_fingerprint(nxt)
        if fp == prev:
            break
        prev = fp

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
