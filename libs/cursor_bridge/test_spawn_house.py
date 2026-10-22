"""Offline tests for atomic spawn_house orchestration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cursor_bridge.glass_services_js import launch_new_agent_tab_js
from cursor_bridge.spawn_house import spawn_house

pytestmark = pytest.mark.offline

# BridgeOp parity checked against tools.cursor_bridge in test_bridge_op_enum_includes_spawn_house.


def test_launch_js_emits_house_knobs_and_composer_id() -> None:
    js = launch_new_agent_tab_js(
        "hi",
        "gemini-4-argon",
        {"effort": "high", "fast": "false"},
        requested_agent_id="uuid-1",
    )
    assert "houseKnobs" in js
    assert '"effort": "high"' in js
    assert "uuid-1" in js


def test_spawn_house_rolls_back_subscription_on_launch_failure(tmp_path: Path) -> None:
    client = MagicMock()
    client.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"thread": {"id": "19499"}},
    )

    class _BusCtx:
        def __enter__(self) -> MagicMock:
            return client

        def __exit__(self, *args: object) -> None:
            return None

    with (
        patch("bus_watch.digest_budget._bus", return_value=_BusCtx()),
        patch(
            "cursor_bridge.spawn_house.subscribe_cursor_xvfb_house",
            return_value={"ok": True, "agent_id": "aid-1"},
        ) as sub,
        patch(
            "cursor_bridge.spawn_house.unsubscribe_cursor_xvfb_house",
            return_value={"ok": True},
        ) as unsub,
        patch(
            "cursor_bridge.spawn_house.launch_cursor_agents_tab",
            return_value={"ok": False, "reason": "no_page"},
        ),
        patch("cursor_bridge.spawn_house.WATCH_DIR", tmp_path),
    ):
        out = spawn_house(
            slug="stage-mission",
            prompt="start",
            agent_id="aid-1",
            gui_host=":8",
        )
    assert out["ok"] is False
    assert out["error"] == "launch_failed"
    sub.assert_called_once()
    unsub.assert_called_once()


def test_spawn_house_subscription_before_launch_order(tmp_path: Path) -> None:
    order: list[str] = []
    client = MagicMock()
    client.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"thread": {"id": "19500"}},
    )

    class _BusCtx:
        def __enter__(self) -> MagicMock:
            return client

        def __exit__(self, *args: object) -> None:
            return None

    def _sub(*args: object, **kwargs: object) -> dict[str, object]:
        order.append("subscribe")
        return {"ok": True}

    def _launch(*args: object, **kwargs: object) -> dict[str, object]:
        order.append("launch")
        return {"ok": True}

    with (
        patch("bus_watch.digest_budget._bus", return_value=_BusCtx()),
        patch(
            "cursor_bridge.spawn_house.subscribe_cursor_xvfb_house", side_effect=_sub
        ),
        patch(
            "cursor_bridge.spawn_house.launch_cursor_agents_tab", side_effect=_launch
        ),
        patch("cursor_bridge.spawn_house.WATCH_DIR", tmp_path),
    ):
        out = spawn_house(
            slug="m", prompt="go", agent_id="a1", watch_dir=tmp_path
        )
    assert out["ok"] is True
    assert order == ["subscribe", "launch"]
    tick = tmp_path / "liaison-19500.tick.json"
    assert tick.is_file()


def test_bridge_op_enum_includes_spawn_house() -> None:
    from typing import get_args

    from tools.cursor_bridge import BridgeOp as ToolBridgeOp

    assert "spawn_house" in get_args(ToolBridgeOp)
