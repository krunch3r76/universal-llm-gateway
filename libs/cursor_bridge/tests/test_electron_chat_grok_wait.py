"""Grok conversation DOM attribution after sidebar select."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest
from cursor_bridge.chat_surface import classify_surface
from cursor_bridge.electron_chat_grok_wait import (
    GROK_CONVERSATION_IDLE_ROUNDS,
    grok_conversation_attributed,
    messages_fingerprint,
    probe_grok_after_select,
)

pytestmark = pytest.mark.offline


def test_empty_conversation_attributed_when_agent_matches() -> None:
    surface = {"active_agent_id": "a1", "recent_messages": []}
    assert grok_conversation_attributed(
        surface,
        expected_agent_id="a1",
        prior_message_fp="old",
        prior_generating=False,
    )


def test_stale_messages_not_attributed() -> None:
    fp = messages_fingerprint(
        {"recent_messages": ["stale transcript line from previous chat"]}
    )
    surface = {
        "active_agent_id": "new-id",
        "recent_messages": ["stale transcript line from previous chat"],
    }
    assert (
        grok_conversation_attributed(
            surface,
            expected_agent_id="new-id",
            prior_message_fp=fp,
            prior_generating=False,
        )
        is False
    )


def test_streaming_before_select_attributed_when_bound_and_idle_after() -> None:
    surface = {
        "active_agent_id": "target-y",
        "recent_messages": ["fresh from y"],
        "has_active_chat": True,
        "generating": False,
    }
    assert (
        grok_conversation_attributed(
            surface,
            expected_agent_id="target-y",
            prior_message_fp="older-fp",
            prior_generating=True,
        )
        is True
    )


def test_fingerprint_change_while_other_bot_streaming_not_attributed() -> None:
    """Sidebar shows target Y but page prose still grows from bot X."""
    surface = {
        "active_agent_id": "bot-y",
        "recent_messages": ["bot-x streaming grew this line"],
        "has_active_chat": True,
        "generating": True,
    }
    assert (
        grok_conversation_attributed(
            surface,
            expected_agent_id="bot-y",
            prior_message_fp="older-fp",
            prior_generating=True,
        )
        is False
    )


def test_streaming_before_select_not_attributed_without_composer_binding() -> None:
    surface = {
        "active_agent_id": "target-y",
        "recent_messages": ["fresh from y"],
        "has_active_chat": False,
        "generating": False,
    }
    assert (
        grok_conversation_attributed(
            surface,
            expected_agent_id="target-y",
            prior_message_fp="older-fp",
            prior_generating=True,
        )
        is False
    )


def test_quiet_before_select_fingerprint_change_attributed() -> None:
    surface = {
        "active_agent_id": "new-id",
        "recent_messages": ["fresh line"],
    }
    assert (
        grok_conversation_attributed(
            surface,
            expected_agent_id="new-id",
            prior_message_fp="deadbeef",
            prior_generating=False,
        )
        is True
    )


def test_prior_generating_is_required_keyword() -> None:
    surface = {
        "active_agent_id": "new-id",
        "recent_messages": ["fresh line"],
    }
    with pytest.raises(TypeError):
        grok_conversation_attributed(
            surface,
            expected_agent_id="new-id",
            prior_message_fp="deadbeef",
        )


@dataclass
class _Ep:
    thread: str = ""
    slug: str = ""
    title: str | None = "Target"
    agent_id: str = "target-id"


def _chat_surface(**overrides: Any) -> dict[str, Any]:
    base = {
        "state": "chat_list",
        "active_title": "Target",
        "active_agent_id": "target-id",
        "titles": ["Target"],
        "title_counts": {"Target": 1},
        "agent_ids": {"target-id": "Target"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": ["line"],
        "message_turns": [],
        "has_active_chat": True,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_probe_waits_through_unchanged_stale_pane_then_attributes() -> None:
    stale = _chat_surface(
        active_agent_id="target-id",
        recent_messages=["stale line"],
        has_active_chat=False,
        generating=True,
    )
    bound = _chat_surface(
        recent_messages=["fresh bound line"],
        has_active_chat=True,
        generating=False,
    )
    probes = [dict(stale), dict(stale), dict(bound)]
    sleeps: list[float] = []

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0) if probes else dict(bound)

    async def _rec_sleep(delay: float) -> None:
        sleeps.append(delay)

    with (
        patch(
            "cursor_bridge.electron_chat_grok_wait.probe_surface",
            side_effect=_fake_probe,
        ),
        patch("cursor_bridge.electron_chat_grok_wait.asyncio.sleep", side_effect=_rec_sleep),
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(),
            expected_agent_id="target-id",
            prior_message_fp=messages_fingerprint(stale),
            prior_generating=True,
        )
    assert out["ok"] is True
    assert out["stale_dom"] is False
    assert len(sleeps) >= 2


@pytest.mark.asyncio
async def test_probe_stale_dom_returns_conversation_unconfirmed_after_bounded_rounds() -> (
    None
):
    stale = _chat_surface(
        has_active_chat=False,
        generating=True,
        recent_messages=["never changes"],
    )
    probe_count = 0
    sleeps: list[float] = []

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        nonlocal probe_count
        probe_count += 1
        return dict(stale)

    async def _rec_sleep(delay: float) -> None:
        sleeps.append(delay)

    with (
        patch(
            "cursor_bridge.electron_chat_grok_wait.probe_surface",
            side_effect=_fake_probe,
        ),
        patch("cursor_bridge.electron_chat_grok_wait.asyncio.sleep", side_effect=_rec_sleep),
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(),
            expected_agent_id="target-id",
            prior_message_fp="old-fp",
            prior_generating=True,
        )
    assert out["code"] == "conversation_unconfirmed"
    assert out["stale_dom"] is True
    assert probe_count == 1 + GROK_CONVERSATION_IDLE_ROUNDS
    assert len(sleeps) == GROK_CONVERSATION_IDLE_ROUNDS
    assert all(s > 0 for s in sleeps)


def test_classify_surface_output_feeds_attribution_binding() -> None:
    classified = classify_surface(
        [],
        [{"text": "Target", "selected": True, "agent_id": "target-id"}],
        generating=False,
        recent_messages=["fresh"],
        has_active_chat=True,
    )
    assert classified.get("has_active_chat") is True
    assert (
        grok_conversation_attributed(
            classified,
            expected_agent_id="target-id",
            prior_message_fp="older-fp",
            prior_generating=True,
        )
        is True
    )
