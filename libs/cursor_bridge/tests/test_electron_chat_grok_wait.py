"""Grok conversation DOM attribution after sidebar select."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, patch

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


@pytest.mark.asyncio
async def test_baseline_agent_id_is_required_keyword() -> None:
    with pytest.raises(TypeError):
        await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(),
            expected_agent_id="target-id",
            prior_message_fp="fp",
            prior_generating=False,
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
async def test_probe_waits_selection_on_target_while_supervisor_fingerprint_lingers() -> None:
    """Break: selection already on target must not stale_dom on first supervisor prose."""
    supervisor_line = "supervisor still visible"
    prior_fp = messages_fingerprint({"recent_messages": [supervisor_line]})
    lagging = _chat_surface(
        active_agent_id="",
        recent_messages=[supervisor_line],
        has_active_chat=True,
        generating=True,
    )
    bound = _chat_surface(
        active_agent_id="target-id",
        recent_messages=["hidden target line"],
        has_active_chat=True,
        generating=False,
    )
    probes = [dict(lagging), dict(lagging), dict(bound)]
    selection_reads = iter(["target-id", "target-id", "target-id"])

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0) if probes else dict(bound)

    async def _fake_selection(_display: object) -> str:
        del _display
        return next(selection_reads, "target-id")

    with (
        patch(
            "cursor_bridge.electron_chat_grok_wait.probe_surface",
            side_effect=_fake_probe,
        ),
        patch(
            "cursor_bridge.electron_chat_grok_wait._selection_agent_id",
            side_effect=_fake_selection,
        ),
        patch("cursor_bridge.electron_chat_grok_wait.asyncio.sleep", new_callable=AsyncMock),
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(),
            expected_agent_id="target-id",
            baseline_agent_id="supervisor-id",
            prior_message_fp=prior_fp,
            prior_generating=True,
            display="14",
        )
    assert out["ok"] is True
    assert out.get("stale_dom") is False


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
        patch(
            "cursor_bridge.electron_chat_grok_wait.asyncio.sleep",
            side_effect=_rec_sleep,
        ),
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(),
            expected_agent_id="target-id",
            baseline_agent_id="bot-x",
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
        patch(
            "cursor_bridge.electron_chat_grok_wait.asyncio.sleep",
            side_effect=_rec_sleep,
        ),
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(),
            expected_agent_id="target-id",
            baseline_agent_id="bot-x",
            prior_message_fp="old-fp",
            prior_generating=True,
        )
    assert out["code"] == "conversation_unconfirmed"
    assert out["stale_dom"] is True
    assert probe_count == 1 + GROK_CONVERSATION_IDLE_ROUNDS
    assert len(sleeps) == GROK_CONVERSATION_IDLE_ROUNDS
    assert all(s > 0 for s in sleeps)


@pytest.mark.asyncio
async def test_probe_foreign_agent_during_wait_returns_unconfirmed() -> None:
    shared = "shared stale line"
    stale_y = _chat_surface(
        active_agent_id="bot-y",
        recent_messages=[shared],
        generating=False,
        has_active_chat=False,
    )
    bot_z = _chat_surface(
        active_agent_id="bot-z",
        active_title="Bot Z",
        recent_messages=[shared],
        generating=False,
        has_active_chat=False,
    )
    misattrib = _chat_surface(
        active_agent_id="bot-y",
        recent_messages=["z line"],
        generating=False,
        has_active_chat=True,
    )
    probes = [dict(stale_y), dict(bot_z), dict(misattrib)]
    probe_calls = 0

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        nonlocal probe_calls
        probe_calls += 1
        return probes.pop(0)

    with patch(
        "cursor_bridge.electron_chat_grok_wait.probe_surface",
        side_effect=_fake_probe,
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-y"),
            expected_agent_id="bot-y",
            baseline_agent_id="bot-y",
            prior_message_fp=messages_fingerprint(stale_y),
            prior_generating=False,
        )
    assert out["ok"] is False
    assert out["stale_dom"] is True
    assert out["code"] == "conversation_unconfirmed"
    assert out.get("foreign_agent_id") == "bot-z"
    assert probe_calls == 2


@pytest.mark.asyncio
async def test_probe_expected_then_baseline_then_expected_unconfirmed() -> None:
    shared = "stale fp line"
    stale_y = _chat_surface(
        active_agent_id="bot-y",
        recent_messages=[shared],
        generating=True,
        has_active_chat=False,
    )
    baseline_x = _chat_surface(
        active_agent_id="bot-x",
        active_title="Bot X",
        recent_messages=[shared],
        generating=False,
        has_active_chat=True,
    )
    fresh_y = _chat_surface(
        active_agent_id="bot-y",
        recent_messages=["new line"],
        generating=False,
        has_active_chat=True,
    )
    probes = [dict(stale_y), dict(baseline_x), dict(fresh_y)]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0)

    with patch(
        "cursor_bridge.electron_chat_grok_wait.probe_surface",
        side_effect=_fake_probe,
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-y"),
            expected_agent_id="bot-y",
            baseline_agent_id="bot-x",
            prior_message_fp=messages_fingerprint(stale_y),
            prior_generating=True,
        )
    assert out["ok"] is False
    assert out["code"] == "conversation_unconfirmed"
    assert out.get("foreign_agent_id") == "bot-x"


@pytest.mark.asyncio
async def test_probe_baseline_then_expected_still_attributes() -> None:
    baseline_x = _chat_surface(
        active_agent_id="bot-x",
        active_title="Bot X",
        recent_messages=["older"],
        generating=True,
        has_active_chat=True,
    )
    fresh_y = _chat_surface(
        active_agent_id="bot-y",
        recent_messages=["new line"],
        generating=False,
        has_active_chat=True,
    )
    probes = [dict(baseline_x), dict(fresh_y)]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0)

    with patch(
        "cursor_bridge.electron_chat_grok_wait.probe_surface",
        side_effect=_fake_probe,
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-y"),
            expected_agent_id="bot-y",
            baseline_agent_id="bot-x",
            prior_message_fp=messages_fingerprint(baseline_x),
            prior_generating=True,
        )
    assert out["ok"] is True
    assert out["stale_dom"] is False


def _b_x_surfaces(
    *,
    active_agent_id: str,
    recent_messages: list[str],
    generating: bool = False,
    has_active_chat: bool | None = True,
) -> dict[str, Any]:
    title = "Bot X" if active_agent_id == "bot-x" else "Bot B"
    agent_ids = {"bot-b": "Bot B", "bot-x": "Bot X"}
    return _chat_surface(
        active_agent_id=active_agent_id,
        active_title=title,
        titles=["Bot B", "Bot X"],
        agent_ids=agent_ids,
        recent_messages=recent_messages,
        generating=generating,
        has_active_chat=has_active_chat,
    )


@pytest.mark.asyncio
async def test_probe_baseline_grew_then_x_shows_baseline_prose_unconfirmed() -> None:
    """F-A: X selected but pane still shows baseline transcript => unconfirmed."""
    prior_fp = messages_fingerprint({"recent_messages": ["B old"]})
    probes = [
        _b_x_surfaces(active_agent_id="bot-b", recent_messages=["B old"]),
        _b_x_surfaces(active_agent_id="bot-b", recent_messages=["B old", "B new"]),
        _b_x_surfaces(
            active_agent_id="bot-x",
            recent_messages=["B old", "B new"],
            has_active_chat=True,
        ),
    ]
    tail = probes[-1]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        if probes:
            return probes.pop(0)
        return dict(tail)

    with (
        patch(
            "cursor_bridge.electron_chat_grok_wait.probe_surface",
            side_effect=_fake_probe,
        ),
        patch(
            "cursor_bridge.electron_chat_grok_wait.asyncio.sleep",
            side_effect=lambda _d: None,
        ),
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-x", title="Bot X"),
            expected_agent_id="bot-x",
            baseline_agent_id="bot-b",
            prior_message_fp=prior_fp,
            prior_generating=False,
        )
    assert out["ok"] is False
    assert out["code"] == "conversation_unconfirmed"


@pytest.mark.asyncio
async def test_probe_baseline_grew_prior_generating_x_shows_baseline_prose_unconfirmed() -> (
    None
):
    prior_fp = messages_fingerprint({"recent_messages": ["B old"]})
    probes = [
        _b_x_surfaces(
            active_agent_id="bot-b",
            recent_messages=["B old"],
            generating=True,
            has_active_chat=True,
        ),
        _b_x_surfaces(
            active_agent_id="bot-b",
            recent_messages=["B old", "B new"],
            generating=True,
            has_active_chat=True,
        ),
        _b_x_surfaces(
            active_agent_id="bot-x",
            recent_messages=["B old", "B new"],
            generating=False,
            has_active_chat=True,
        ),
    ]
    tail = probes[-1]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        if probes:
            return probes.pop(0)
        return dict(tail)

    with (
        patch(
            "cursor_bridge.electron_chat_grok_wait.probe_surface",
            side_effect=_fake_probe,
        ),
        patch(
            "cursor_bridge.electron_chat_grok_wait.asyncio.sleep",
            side_effect=lambda _d: None,
        ),
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-x", title="Bot X"),
            expected_agent_id="bot-x",
            baseline_agent_id="bot-b",
            prior_message_fp=prior_fp,
            prior_generating=True,
        )
    assert out["ok"] is False
    assert out["code"] == "conversation_unconfirmed"


@pytest.mark.asyncio
async def test_probe_baseline_generating_then_x_shows_newer_baseline_prose_unconfirmed() -> (
    None
):
    prior_fp = messages_fingerprint({"recent_messages": ["B old"]})
    stale_x = _b_x_surfaces(
        active_agent_id="bot-x",
        recent_messages=["B old", "B new", "B newer"],
        generating=True,
        has_active_chat=True,
    )
    probes = [
        _b_x_surfaces(active_agent_id="bot-b", recent_messages=["B old"]),
        _b_x_surfaces(
            active_agent_id="bot-b",
            recent_messages=["B old", "B new"],
            generating=True,
            has_active_chat=True,
        ),
        dict(stale_x),
    ]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        if probes:
            return probes.pop(0)
        return dict(stale_x)

    with (
        patch(
            "cursor_bridge.electron_chat_grok_wait.probe_surface",
            side_effect=_fake_probe,
        ),
        patch(
            "cursor_bridge.electron_chat_grok_wait.asyncio.sleep",
            side_effect=lambda _d: None,
        ),
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-x", title="Bot X"),
            expected_agent_id="bot-x",
            baseline_agent_id="bot-b",
            prior_message_fp=prior_fp,
            prior_generating=False,
        )
    assert out["ok"] is False
    assert out["code"] == "conversation_unconfirmed"


@pytest.mark.asyncio
async def test_probe_baseline_grew_then_x_fresh_line_attributes() -> None:
    prior_fp = messages_fingerprint({"recent_messages": ["B old"]})
    bound = _b_x_surfaces(
        active_agent_id="bot-x",
        recent_messages=["B old", "B new", "X line"],
        generating=False,
        has_active_chat=True,
    )
    probes = [
        _b_x_surfaces(active_agent_id="bot-b", recent_messages=["B old"]),
        _b_x_surfaces(active_agent_id="bot-b", recent_messages=["B old", "B new"]),
        _b_x_surfaces(
            active_agent_id="bot-x",
            recent_messages=["B old", "B new"],
            has_active_chat=True,
        ),
        dict(bound),
    ]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        if probes:
            return probes.pop(0)
        return dict(bound)

    with patch(
        "cursor_bridge.electron_chat_grok_wait.probe_surface",
        side_effect=_fake_probe,
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-x", title="Bot X"),
            expected_agent_id="bot-x",
            baseline_agent_id="bot-b",
            prior_message_fp=prior_fp,
            prior_generating=False,
        )
    assert out["ok"] is True
    assert out["stale_dom"] is False
    assert out["surface"]["recent_messages"][-1] == "X line"


@pytest.mark.asyncio
async def test_probe_baseline_grew_prior_generating_then_x_line_attributes() -> None:
    prior_fp = messages_fingerprint({"recent_messages": ["B old"]})
    bound = _b_x_surfaces(
        active_agent_id="bot-x",
        recent_messages=["X line"],
        generating=False,
        has_active_chat=True,
    )
    probes = [
        _b_x_surfaces(
            active_agent_id="bot-b",
            recent_messages=["B old"],
            generating=True,
            has_active_chat=True,
        ),
        _b_x_surfaces(
            active_agent_id="bot-b",
            recent_messages=["B old", "B new"],
            generating=True,
            has_active_chat=True,
        ),
        dict(bound),
    ]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        if probes:
            return probes.pop(0)
        return dict(bound)

    with patch(
        "cursor_bridge.electron_chat_grok_wait.probe_surface",
        side_effect=_fake_probe,
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-x", title="Bot X"),
            expected_agent_id="bot-x",
            baseline_agent_id="bot-b",
            prior_message_fp=prior_fp,
            prior_generating=True,
        )
    assert out["ok"] is True
    assert out["surface"]["recent_messages"][-1] == "X line"


@pytest.mark.asyncio
async def test_probe_baseline_grew_quiet_then_x_line_attributes() -> None:
    prior_fp = messages_fingerprint({"recent_messages": ["B old"]})
    bound = _b_x_surfaces(
        active_agent_id="bot-x",
        recent_messages=["X line"],
        generating=False,
        has_active_chat=True,
    )
    probes = [
        _b_x_surfaces(active_agent_id="bot-b", recent_messages=["B old"]),
        _b_x_surfaces(active_agent_id="bot-b", recent_messages=["B old", "B new"]),
        dict(bound),
    ]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        if probes:
            return probes.pop(0)
        return dict(bound)

    with patch(
        "cursor_bridge.electron_chat_grok_wait.probe_surface",
        side_effect=_fake_probe,
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-x", title="Bot X"),
            expected_agent_id="bot-x",
            baseline_agent_id="bot-b",
            prior_message_fp=prior_fp,
            prior_generating=False,
        )
    assert out["ok"] is True
    assert out["surface"]["recent_messages"][-1] == "X line"


@pytest.mark.asyncio
async def test_probe_s1_baseline_transient_then_target_shows_prior_fp_unconfirmed() -> (
    None
):
    """F-G falsifier S1: prior fp must stay stale after baseline grows transient prose."""
    prior_fp = messages_fingerprint({"recent_messages": ["B old"]})
    tail = _b_x_surfaces(
        active_agent_id="bot-x",
        recent_messages=["B old"],
        generating=False,
        has_active_chat=True,
    )
    probes = [
        _b_x_surfaces(
            active_agent_id="bot-b",
            recent_messages=["B transient"],
            generating=True,
            has_active_chat=True,
        ),
        dict(tail),
    ]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        if probes:
            return probes.pop(0)
        return dict(tail)

    with (
        patch(
            "cursor_bridge.electron_chat_grok_wait.probe_surface",
            side_effect=_fake_probe,
        ),
        patch(
            "cursor_bridge.electron_chat_grok_wait.asyncio.sleep",
            side_effect=lambda _d: None,
        ),
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-x", title="Bot X"),
            expected_agent_id="bot-x",
            baseline_agent_id="bot-b",
            prior_message_fp=prior_fp,
            prior_generating=False,
        )
    assert out["ok"] is False
    assert out["code"] == "conversation_unconfirmed"


@pytest.mark.asyncio
async def test_probe_foreign_agent_on_first_probe_before_expected() -> None:
    """F-H/B1: foreign marker on the first probe must fail-closed before expected is seen."""
    shared = "shared stale line"
    misattrib = _chat_surface(
        active_agent_id="bot-z",
        active_title="Bot Z",
        titles=["Bot Z", "Bot Y"],
        agent_ids={"bot-z": "Bot Z", "bot-y": "Bot Y"},
        recent_messages=["z line"],
        generating=False,
        has_active_chat=True,
    )
    probes = [
        _chat_surface(
            active_agent_id="bot-z",
            active_title="Bot Z",
            titles=["Bot Z", "Bot Y"],
            agent_ids={"bot-z": "Bot Z", "bot-y": "Bot Y"},
            recent_messages=[shared],
            generating=False,
            has_active_chat=False,
        ),
        dict(misattrib),
    ]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0)

    with patch(
        "cursor_bridge.electron_chat_grok_wait.probe_surface",
        side_effect=_fake_probe,
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-y", title="Bot Y"),
            expected_agent_id="bot-y",
            baseline_agent_id="bot-x",
            prior_message_fp=messages_fingerprint({"recent_messages": [shared]}),
            prior_generating=False,
        )
    assert out["ok"] is False
    assert out["code"] == "conversation_unconfirmed"
    assert out.get("foreign_agent_id") == "bot-z"


@pytest.mark.asyncio
async def test_probe_foreign_agent_in_wait_loop_after_baseline() -> None:
    """F-H/B1: foreign agent after baseline on probe 2 must not attribute on probe 3."""
    shared = "shared stale line"
    stale_y = _chat_surface(
        active_agent_id="bot-y",
        recent_messages=[shared],
        generating=True,
        has_active_chat=False,
    )
    bot_z = _chat_surface(
        active_agent_id="bot-z",
        active_title="Bot Z",
        titles=["Bot Z", "Bot Y"],
        agent_ids={"bot-z": "Bot Z", "bot-y": "Bot Y"},
        recent_messages=[shared],
        generating=False,
        has_active_chat=False,
    )
    misattrib = _chat_surface(
        active_agent_id="bot-y",
        recent_messages=["z line"],
        generating=False,
        has_active_chat=True,
    )
    probes = [dict(stale_y), dict(bot_z), dict(misattrib)]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0)

    with patch(
        "cursor_bridge.electron_chat_grok_wait.probe_surface",
        side_effect=_fake_probe,
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-y", title="Bot Y"),
            expected_agent_id="bot-y",
            baseline_agent_id="bot-x",
            prior_message_fp=messages_fingerprint(stale_y),
            prior_generating=True,
        )
    assert out["ok"] is False
    assert out.get("foreign_agent_id") == "bot-z"


@pytest.mark.asyncio
async def test_probe_quiet_lag_target_streaming_x_line_attributes() -> None:
    """B2: honest lag while target streams must still bind fresh prose."""
    prior_fp = messages_fingerprint({"recent_messages": ["B old"]})
    bound = _b_x_surfaces(
        active_agent_id="bot-x",
        recent_messages=["X line"],
        generating=True,
        has_active_chat=None,
    )
    probes = [
        _b_x_surfaces(
            active_agent_id="bot-b",
            recent_messages=["B old"],
            generating=False,
            has_active_chat=True,
        ),
        dict(bound),
    ]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0) if probes else dict(bound)

    with patch(
        "cursor_bridge.electron_chat_grok_wait.probe_surface",
        side_effect=_fake_probe,
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-x", title="Bot X"),
            expected_agent_id="bot-x",
            baseline_agent_id="bot-b",
            prior_message_fp=prior_fp,
            prior_generating=False,
        )
    assert out["ok"] is True
    assert out["surface"]["recent_messages"][-1] == "X line"


@pytest.mark.asyncio
async def test_probe_baseline_blank_pane_then_target_shows_prior_fp_unconfirmed() -> (
    None
):
    """F-I: blank baseline pane must not waive empty-fingerprint stale guard."""
    prior_fp = messages_fingerprint({"recent_messages": ["B old"]})
    stale_target = _b_x_surfaces(
        active_agent_id="bot-x",
        recent_messages=["B old"],
        generating=False,
        has_active_chat=True,
    )
    tail = dict(stale_target)
    probes = [
        _b_x_surfaces(
            active_agent_id="bot-b",
            recent_messages=[],
            generating=False,
            has_active_chat=False,
        ),
        dict(stale_target),
    ]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        if probes:
            return probes.pop(0)
        return dict(tail)

    with (
        patch(
            "cursor_bridge.electron_chat_grok_wait.probe_surface",
            side_effect=_fake_probe,
        ),
        patch(
            "cursor_bridge.electron_chat_grok_wait.asyncio.sleep",
            side_effect=lambda _d: None,
        ),
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-x", title="Bot X"),
            expected_agent_id="bot-x",
            baseline_agent_id="bot-b",
            prior_message_fp=prior_fp,
            prior_generating=False,
        )
    assert out["ok"] is False
    assert out["code"] == "conversation_unconfirmed"


@pytest.mark.asyncio
async def test_probe_empty_active_agent_then_target_fresh_line_attributes() -> None:
    """B3/F-L: empty active marker on baseline must not be treated as foreign."""
    prior_fp = messages_fingerprint({"recent_messages": ["B old"]})
    bound = _b_x_surfaces(
        active_agent_id="bot-x",
        recent_messages=["X line"],
        generating=False,
        has_active_chat=True,
    )
    probes = [
        _chat_surface(
            active_agent_id="",
            active_title="",
            titles=["Bot B", "Bot X"],
            agent_ids={"bot-b": "Bot B", "bot-x": "Bot X"},
            recent_messages=["B old"],
            generating=False,
            has_active_chat=True,
        ),
        dict(bound),
    ]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0) if probes else dict(bound)

    with patch(
        "cursor_bridge.electron_chat_grok_wait.probe_surface",
        side_effect=_fake_probe,
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-x", title="Bot X"),
            expected_agent_id="bot-x",
            baseline_agent_id="bot-b",
            prior_message_fp=prior_fp,
            prior_generating=False,
        )
    assert out["ok"] is True
    assert out["surface"]["recent_messages"][-1] == "X line"


@pytest.mark.asyncio
async def test_probe_baseline_equals_expected_does_not_track_stale_on_self() -> None:
    """F-L: baseline-tracking must not run when baseline agent is also the expected agent."""
    fp1 = messages_fingerprint({"recent_messages": ["line one"]})
    bound = _chat_surface(
        active_agent_id="bot-y",
        active_title="Bot Y",
        recent_messages=["line two"],
        generating=False,
        has_active_chat=True,
    )
    probes = [
        _chat_surface(
            active_agent_id="bot-y",
            active_title="Bot Y",
            recent_messages=["line one"],
            generating=False,
            has_active_chat=True,
        ),
        dict(bound),
    ]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        if probes:
            return probes.pop(0)
        return dict(bound)

    with patch(
        "cursor_bridge.electron_chat_grok_wait.probe_surface",
        side_effect=_fake_probe,
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-y", title="Bot Y"),
            expected_agent_id="bot-y",
            baseline_agent_id="bot-y",
            prior_message_fp=fp1,
            prior_generating=False,
        )
    assert out["ok"] is True
    assert out["surface"]["recent_messages"][-1] == "line two"


@pytest.mark.asyncio
async def test_probe_r7_t1a_baseline_then_empty_leading_prose_on_target_unconfirmed() -> (
    None
):
    """R6-5/R6-6: baseline marker bot-b then target showing same new prose => unconfirmed."""
    prior_fp = messages_fingerprint({"recent_messages": ["B old"]})
    tail = _b_x_surfaces(
        active_agent_id="bot-x",
        recent_messages=["", "B new"],
        generating=False,
        has_active_chat=True,
    )
    probes = [
        _b_x_surfaces(
            active_agent_id="bot-b",
            recent_messages=["", "B new"],
            generating=False,
            has_active_chat=True,
        ),
        dict(tail),
    ]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0) if probes else dict(tail)

    with (
        patch(
            "cursor_bridge.electron_chat_grok_wait.probe_surface",
            side_effect=_fake_probe,
        ),
        patch(
            "cursor_bridge.electron_chat_grok_wait.asyncio.sleep",
            side_effect=lambda _d: None,
        ),
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-x", title="Bot X"),
            expected_agent_id="bot-x",
            baseline_agent_id="bot-b",
            prior_message_fp=prior_fp,
            prior_generating=False,
        )
    assert out["ok"] is False
    assert out["stale_dom"] is True
    assert out["code"] == "conversation_unconfirmed"


@pytest.mark.asyncio
async def test_probe_r7_t1b_empty_baseline_then_same_prose_on_target_unconfirmed() -> (
    None
):
    """R6-5/N1: empty baseline marker with same transient-prose pattern => unconfirmed."""
    prior_fp = messages_fingerprint({"recent_messages": ["B old"]})
    tail = _b_x_surfaces(
        active_agent_id="bot-x",
        recent_messages=["", "B new"],
        generating=False,
        has_active_chat=True,
    )
    probes = [
        _chat_surface(
            active_agent_id="",
            active_title="",
            titles=["Bot B", "Bot X"],
            agent_ids={"bot-b": "Bot B", "bot-x": "Bot X"},
            recent_messages=["", "B new"],
            generating=False,
            has_active_chat=True,
        ),
        dict(tail),
    ]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0) if probes else dict(tail)

    with (
        patch(
            "cursor_bridge.electron_chat_grok_wait.probe_surface",
            side_effect=_fake_probe,
        ),
        patch(
            "cursor_bridge.electron_chat_grok_wait.asyncio.sleep",
            side_effect=lambda _d: None,
        ),
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-x", title="Bot X"),
            expected_agent_id="bot-x",
            baseline_agent_id="",
            prior_message_fp=prior_fp,
            prior_generating=False,
        )
    assert out["ok"] is False
    assert out["stale_dom"] is True
    assert out["code"] == "conversation_unconfirmed"


@pytest.mark.asyncio
async def test_probe_r7_t1c_expected_then_empty_then_target_shows_grown_prose_unconfirmed() -> (
    None
):
    """R6-5: expected marker, empty marker, then target with prose grown under empty => unconfirmed."""
    prior_fp = messages_fingerprint({"recent_messages": ["B old"]})
    tail = _b_x_surfaces(
        active_agent_id="bot-x",
        recent_messages=["B new"],
        generating=False,
        has_active_chat=True,
    )
    probes = [
        _b_x_surfaces(
            active_agent_id="bot-x",
            recent_messages=["B old"],
            generating=False,
            has_active_chat=True,
        ),
        _chat_surface(
            active_agent_id="",
            active_title="",
            titles=["Bot B", "Bot X"],
            agent_ids={"bot-b": "Bot B", "bot-x": "Bot X"},
            recent_messages=["", "B new"],
            generating=False,
            has_active_chat=True,
        ),
        dict(tail),
    ]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0) if probes else dict(tail)

    with (
        patch(
            "cursor_bridge.electron_chat_grok_wait.probe_surface",
            side_effect=_fake_probe,
        ),
        patch(
            "cursor_bridge.electron_chat_grok_wait.asyncio.sleep",
            side_effect=lambda _d: None,
        ),
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-x", title="Bot X"),
            expected_agent_id="bot-x",
            baseline_agent_id="bot-b",
            prior_message_fp=prior_fp,
            prior_generating=False,
        )
    assert out["ok"] is False
    assert out["stale_dom"] is True
    assert out["code"] == "conversation_unconfirmed"


@pytest.mark.asyncio
async def test_probe_r7_t2_baseline_multi_grow_then_target_shows_mid_prose_unconfirmed() -> (
    None
):
    """R2/R6-1: stale fingerprint from baseline growth must block target showing mid prose."""
    prior_fp = messages_fingerprint({"recent_messages": ["B old"]})
    tail = _b_x_surfaces(
        active_agent_id="bot-x",
        recent_messages=["p2"],
        generating=False,
        has_active_chat=True,
    )
    probes = [
        _b_x_surfaces(
            active_agent_id="bot-b",
            recent_messages=["p2"],
            generating=False,
            has_active_chat=True,
        ),
        _b_x_surfaces(
            active_agent_id="bot-b",
            recent_messages=["p3"],
            generating=False,
            has_active_chat=True,
        ),
        dict(tail),
    ]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0) if probes else dict(tail)

    with (
        patch(
            "cursor_bridge.electron_chat_grok_wait.probe_surface",
            side_effect=_fake_probe,
        ),
        patch(
            "cursor_bridge.electron_chat_grok_wait.asyncio.sleep",
            side_effect=lambda _d: None,
        ),
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-x", title="Bot X"),
            expected_agent_id="bot-x",
            baseline_agent_id="bot-b",
            prior_message_fp=prior_fp,
            prior_generating=False,
        )
    assert out["ok"] is False
    assert out["code"] == "conversation_unconfirmed"


@pytest.mark.asyncio
async def test_probe_r7_t3_foreign_on_first_probe_exactly_one_round() -> None:
    """R1/R6-2: foreign agent on probe 1 must fail-closed before any further probes."""
    prior_fp = messages_fingerprint({"recent_messages": ["stale"]})
    foreign = _chat_surface(
        active_agent_id="bot-z",
        active_title="Bot Z",
        titles=["Bot Z", "Bot X"],
        agent_ids={"bot-z": "Bot Z", "bot-x": "Bot X"},
        recent_messages=["any"],
        generating=False,
        has_active_chat=False,
    )
    would_misattrib = _b_x_surfaces(
        active_agent_id="bot-x",
        recent_messages=["X fresh"],
        generating=False,
        has_active_chat=True,
    )
    probes = [dict(foreign), dict(would_misattrib)]
    probe_calls = 0

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        nonlocal probe_calls
        probe_calls += 1
        return probes.pop(0)

    with patch(
        "cursor_bridge.electron_chat_grok_wait.probe_surface",
        side_effect=_fake_probe,
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-x", title="Bot X"),
            expected_agent_id="bot-x",
            baseline_agent_id="bot-b",
            prior_message_fp=prior_fp,
            prior_generating=False,
        )
    assert out["ok"] is False
    assert out["code"] == "conversation_unconfirmed"
    assert out.get("foreign_agent_id") == "bot-z"
    assert probe_calls == 1


@pytest.mark.asyncio
async def test_probe_r7_t4_blank_baseline_then_target_streaming_attributes() -> None:
    """R3/R6-3: blank baseline pane then target streaming fresh line still attributes."""
    prior_fp = messages_fingerprint({"recent_messages": ["B old"]})
    bound = _b_x_surfaces(
        active_agent_id="bot-x",
        recent_messages=["X line"],
        generating=True,
        has_active_chat=None,
    )
    probes = [
        _b_x_surfaces(
            active_agent_id="bot-b",
            recent_messages=[],
            generating=False,
            has_active_chat=True,
        ),
        dict(bound),
    ]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0) if probes else dict(bound)

    with patch(
        "cursor_bridge.electron_chat_grok_wait.probe_surface",
        side_effect=_fake_probe,
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-x", title="Bot X"),
            expected_agent_id="bot-x",
            baseline_agent_id="bot-b",
            prior_message_fp=prior_fp,
            prior_generating=False,
        )
    assert out["ok"] is True
    assert out["surface"]["recent_messages"][-1] == "X line"


@pytest.mark.asyncio
async def test_probe_not_expected_marker_no_prose_every_round_unconfirmed() -> None:
    """F2/W38: baseline B with prior streaming; only B or empty markers, no prose."""
    prior_fp = messages_fingerprint({"recent_messages": ["p0"]})
    stale_b = _chat_surface(
        active_agent_id="bot-b",
        active_title="Bot B",
        titles=["Bot B", "Bot X"],
        agent_ids={"bot-b": "Bot B", "bot-x": "Bot X"},
        recent_messages=[],
        generating=False,
        has_active_chat=False,
    )
    stale_empty = _chat_surface(
        active_agent_id="",
        active_title="",
        titles=["Bot B", "Bot X"],
        agent_ids={"bot-b": "Bot B", "bot-x": "Bot X"},
        recent_messages=[],
        generating=False,
        has_active_chat=False,
    )
    probes = [dict(stale_b), dict(stale_empty), dict(stale_b)]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0) if probes else dict(stale_b)

    with (
        patch(
            "cursor_bridge.electron_chat_grok_wait.probe_surface",
            side_effect=_fake_probe,
        ),
        patch(
            "cursor_bridge.electron_chat_grok_wait.asyncio.sleep",
            side_effect=lambda _d: None,
        ),
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-x", title="Bot X"),
            expected_agent_id="bot-x",
            baseline_agent_id="bot-b",
            prior_message_fp=prior_fp,
            prior_generating=True,
        )
    assert out["ok"] is False
    assert out["code"] == "conversation_unconfirmed"
    assert out["stale_dom"] is True


@pytest.mark.asyncio
async def test_probe_empty_marker_title_ok_empty_pane_unconfirmed() -> None:
    """A2/A4: title-only wake ok on empty pane must stay unconfirmed for expected agent."""
    prior_fp = messages_fingerprint({"recent_messages": ["p0"]})
    empty_title_ok = _chat_surface(
        active_agent_id="",
        active_title="Bot X",
        titles=["Bot X"],
        agent_ids={},
        recent_messages=[],
        generating=False,
        has_active_chat=False,
    )
    probes = [dict(empty_title_ok)]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0) if probes else dict(empty_title_ok)

    with (
        patch(
            "cursor_bridge.electron_chat_grok_wait.probe_surface",
            side_effect=_fake_probe,
        ),
        patch(
            "cursor_bridge.electron_chat_grok_wait.asyncio.sleep",
            side_effect=lambda _d: None,
        ),
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-x", title="Bot X"),
            expected_agent_id="bot-x",
            baseline_agent_id="bot-b",
            prior_message_fp=prior_fp,
            prior_generating=True,
        )
    assert out["ok"] is False
    assert out["code"] == "conversation_unconfirmed"
    assert out["stale_dom"] is True


@pytest.mark.asyncio
async def test_probe_first_probe_attributed_ok_exits_before_foreign() -> None:
    """F3/P25: attributed+ok on probe 0 must return fresh prose, not probe foreign."""
    prior_fp = messages_fingerprint({"recent_messages": ["p0"]})
    bound = _chat_surface(
        active_agent_id="bot-x",
        active_title="Bot X",
        titles=["Bot X", "Bot Z"],
        agent_ids={"bot-x": "Bot X", "bot-z": "Bot Z"},
        recent_messages=["X fresh"],
        generating=False,
        has_active_chat=True,
    )
    foreign = _chat_surface(
        active_agent_id="bot-z",
        active_title="Bot Z",
        titles=["Bot X", "Bot Z"],
        agent_ids={"bot-x": "Bot X", "bot-z": "Bot Z"},
        recent_messages=["z line"],
        generating=False,
        has_active_chat=True,
    )
    probes = [dict(bound), dict(foreign)]
    probe_calls = 0

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        nonlocal probe_calls
        probe_calls += 1
        return probes.pop(0)

    with patch(
        "cursor_bridge.electron_chat_grok_wait.probe_surface",
        side_effect=_fake_probe,
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-x", title="Bot X"),
            expected_agent_id="bot-x",
            baseline_agent_id="",
            prior_message_fp=prior_fp,
            prior_generating=True,
        )
    assert out["ok"] is True
    assert out["surface"]["recent_messages"][-1] == "X fresh"
    assert probe_calls == 1


@pytest.mark.asyncio
async def test_probe_a5_empty_pane_generating_without_active_chat_ok() -> None:
    """A5: empty prose while generating without composer binding attributes empty pane."""
    prior_fp = messages_fingerprint({"recent_messages": ["p0"]})
    streaming_empty = _chat_surface(
        active_agent_id="bot-x",
        active_title="Bot X",
        titles=["Bot X"],
        agent_ids={"bot-x": "Bot X"},
        recent_messages=[],
        generating=True,
        has_active_chat=False,
    )

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return dict(streaming_empty)

    with (
        patch(
            "cursor_bridge.electron_chat_grok_wait.probe_surface",
            side_effect=_fake_probe,
        ),
        patch(
            "cursor_bridge.electron_chat_grok_wait.asyncio.sleep",
            side_effect=lambda _d: None,
        ),
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=_Ep(agent_id="bot-x", title="Bot X"),
            expected_agent_id="bot-x",
            baseline_agent_id="bot-b",
            prior_message_fp=prior_fp,
            prior_generating=True,
        )
    assert out["ok"] is True
    assert out["surface"]["recent_messages"] == []


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
