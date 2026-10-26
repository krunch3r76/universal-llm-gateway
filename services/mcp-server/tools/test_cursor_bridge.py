"""Offline tests for cursor_bridge direct Glass ops and legacy relay helpers."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tools.cursor_bridge import (
    BridgeOp,
    _prune_sidebar,
    _rename_tab,
    _send_msg,
    _spawn_tab,
    _wake_tab,
)

pytestmark = pytest.mark.offline


@pytest.mark.parametrize(
    "gui_host,expected_origin",
    [(":8", "http://127.0.0.1:9338"), ("orion-node", "http://127.0.0.1:19338")],
)
def test_spawn_tab_calls_launch_with_debug_url(
    gui_host: str, expected_origin: str
) -> None:
    with patch("cursor_bridge.bridge_spawn_relay.launch_cursor_agents_tab") as launch:
        launch.return_value = {"ok": True, "editor_agent_id": "a-1"}
        out = _spawn_tab(gui_host=gui_host, prompt="hello", model="Grok 4.7")
    launch.assert_called_once_with(
        "hello",
        model_query="Grok 4.7",
        model_knobs=None,
        debug_url=expected_origin,
        requested_agent_id=None,
    )
    assert out["op"] == "spawn_tab"
    assert out["gui_host"] == gui_host
    assert out["ok"] is True


def test_send_msg_bus_only_without_agent_id() -> None:
    with patch("tools.cursor_bridge._send_impl") as send_impl:
        send_impl.return_value = {"thread": {"id": "1"}, "turn_number": 2}
        with patch("tools.cursor_bridge.relay_wake_tab") as wake:
            out = _send_msg(thread="1", body="hello", after_turn=0)
    send_impl.assert_called_once()
    wake.assert_not_called()
    assert out["op"] == "send_msg"


def test_send_msg_pastes_when_agent_id_set() -> None:
    with patch("tools.cursor_bridge._send_impl") as send_impl:
        send_impl.return_value = {"thread": {"id": "20723"}, "turn_number": 7}
        with patch("tools.cursor_bridge.relay_wake_tab") as wake:
            wake.return_value = {"ok": True, "op": "wake_tab"}
            out = _send_msg(
                thread="20723",
                body="now post PONG2",
                after_turn=6,
                gui_host=":8",
                agent_id="0b2e33aa",
            )
    wake.assert_called_once_with(
        gui_host=":8",
        agent_id="0b2e33aa",
        body="now post PONG2",
    )
    assert out["op"] == "send_msg"
    assert out["glass_paste"]["ok"] is True
    assert out["editor_agent_id"] == "0b2e33aa"


def test_send_msg_glass_paste_failure_surfaces_error() -> None:
    with patch("tools.cursor_bridge._send_impl") as send_impl:
        send_impl.return_value = {"turn_number": 9}
        with patch("tools.cursor_bridge.relay_wake_tab") as wake:
            wake.return_value = {"ok": False, "error": "unknown_target"}
            out = _send_msg(thread="1", body="x", after_turn=0, agent_id="aid")
    assert out["ok"] is False
    assert out["error"] == "unknown_target"


def test_send_msg_spawn_tab_then_followup_sequence() -> None:
    """spawn_tab editor_agent_id must be passable to send_msg for Glass paste."""
    with patch("cursor_bridge.bridge_spawn_relay.launch_cursor_agents_tab") as launch:
        launch.return_value = {"ok": True, "editor_agent_id": "spawned-aid"}
        spawn_out = _spawn_tab(gui_host=":8", prompt="first", model="composer-2.5")
    aid = spawn_out["editor_agent_id"]
    with patch("tools.cursor_bridge._send_impl") as send_impl:
        send_impl.return_value = {"turn_number": 8}
        with patch("tools.cursor_bridge.relay_wake_tab") as wake:
            wake.return_value = {"ok": True}
            msg_out = _send_msg(
                thread="20723",
                body="follow-up",
                after_turn=None,
                agent_id=aid,
            )
    wake.assert_called_once_with(gui_host=":8", agent_id="spawned-aid", body="follow-up")
    assert msg_out.get("ok") is not False


def test_spawn_tab_requires_prompt() -> None:
    out = _spawn_tab(gui_host=":8", prompt=None, model=None)
    assert out["ok"] is False
    assert out["error"] == "prompt_required"


def test_spawn_tab_forwards_knobs_and_agent_id() -> None:
    with patch("cursor_bridge.bridge_spawn_relay.launch_cursor_agents_tab") as launch:
        launch.return_value = {"ok": True}
        _spawn_tab(
            gui_host=":8",
            prompt="x",
            model="gemini-4-argon",
            effort="high",
            fast=False,
            agent_id="fixed-id",
        )
    kwargs = launch.call_args.kwargs
    assert kwargs["requested_agent_id"] == "fixed-id"
    assert kwargs["model_knobs"] == {"effort": "high", "fast": "false"}


def test_bridge_op_matches_canonical_yaml_ops() -> None:
    from pathlib import Path
    from typing import get_args

    import yaml

    root = Path(__file__).resolve().parents[3]
    raw = yaml.safe_load((root / "config/mcp/canonical.yaml").read_text())
    rows = raw["tools"] if isinstance(raw, dict) else raw
    entry = next(r for r in rows if r.get("canonical_name") == "cursor_bridge")
    schema_ops = set(entry["json_schema"]["properties"]["op"]["enum"])
    assert schema_ops == set(get_args(BridgeOp))


@pytest.mark.parametrize(
    "gui_host,expected_display", [(":8", "8"), ("orion-node", "orion-node")]
)
def test_wake_tab_pastes_via_glass_services(
    gui_host: str, expected_display: str
) -> None:
    page = {"title": "Cursor Agents", "web_socket_debugger_url": "ws://fake"}
    with (
        patch("cursor_bridge.cdp_pages.list_pages", return_value=[page]),
        patch("cursor_bridge.wake_delivery._pick_page", return_value=page),
        patch("cursor_bridge.bridge_glass_relay.asyncio.run") as run,
    ):
        run.return_value = {"ok": True}
        out = _wake_tab(
            gui_host=gui_host,
            agent_id="agent-1",
            body="BRIDGE_WAKE thread=1 turn=2",
        )
    assert out["op"] == "wake_tab"
    assert out["gui_host"] == gui_host
    assert out["display"] == expected_display
    assert out["ok"] is True
    run.assert_called_once()


def test_wake_tab_requires_body() -> None:
    out = _wake_tab(gui_host=":8", agent_id="a", body="")
    assert out["ok"] is False
    assert out["error"] == "body_required"


@pytest.mark.parametrize(
    "gui_host,expected_origin",
    [(":8", "http://127.0.0.1:9338"), ("orion-node", "http://127.0.0.1:19338")],
)
def test_rename_tab_calls_sidebar_rename(gui_host: str, expected_origin: str) -> None:
    with patch(
        "cursor_bridge.bridge_glass_relay.rename_agent_row_in_sidebar"
    ) as rename:
        rename.return_value = {"ok": True, "new_title": "t"}
        out = _rename_tab(gui_host=gui_host, agent_id="aid", title="new title")
    rename.assert_called_once_with("aid", "new title", debug_url=expected_origin)
    assert out["op"] == "rename_tab"
    assert out["gui_host"] == gui_host


def test_rename_tab_requires_agent_id_and_title() -> None:
    assert (
        _rename_tab(gui_host=":8", agent_id=None, title="x")["error"]
        == "agent_id_required"
    )
    assert (
        _rename_tab(gui_host=":8", agent_id="a", title=None)["error"]
        == "title_required"
    )


@pytest.mark.parametrize(
    "gui_host,expected_origin",
    [(":8", "http://127.0.0.1:9338"), ("orion-node", "http://127.0.0.1:19338")],
)
def test_prune_sidebar_calls_predecessor_prune(
    gui_host: str, expected_origin: str
) -> None:
    with patch(
        "cursor_bridge.bridge_glass_relay.prune_cursor_agents_predecessors"
    ) as prune:
        prune.return_value = {"ok": True, "archived": []}
        out = _prune_sidebar(gui_host=gui_host, thread="18528", agent_id="keep-me")
    prune.assert_called_once_with(
        "18528",
        active_agent_id="keep-me",
        debug_url=expected_origin,
    )
    assert out["op"] == "prune_sidebar"
    assert out["gui_host"] == gui_host


def test_prune_sidebar_requires_thread_and_agent_id() -> None:
    assert (
        _prune_sidebar(gui_host=":8", thread=None, agent_id="a")["error"]
        == "thread_required"
    )
    assert (
        _prune_sidebar(gui_host=":8", thread="1", agent_id=None)["error"]
        == "agent_id_required"
    )
