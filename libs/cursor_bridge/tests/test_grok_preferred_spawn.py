"""preferred_spawn resolver and Leg-4 counting spawn."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from cursor_bridge import grok_window_bindings as bindings
from cursor_bridge.grok_counting_spawn import spawn_counting_grok_agent
from cursor_bridge.grok_preferred_spawn import preferred_spawn_for
from cursor_bridge.grok_window_bindings import write_preferred_spawn


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "grok-window-routing.json"
    monkeypatch.setattr(bindings, "state_path", lambda: path)
    return path


def test_counting_spawn_omit_display_uses_preferred_spawn(isolated_state) -> None:
    write_preferred_spawn(
        preferred_spawn="14", source="test", updated_by="test", emit=False
    )
    with patch("cursor_bridge.grok_counting_spawn.spawn_grok_agent") as spawn_fn:
        spawn_fn.return_value = {"ok": True, "agent_id": "a1", "display": "14"}
        out = spawn_counting_grok_agent("counter-bot")
    assert out["ok"] is True
    assert out["effective_display"] == "14"
    assert spawn_fn.call_args.args[0] == "14"
    kwargs = spawn_fn.call_args.kwargs
    assert "mailbox" not in kwargs
    assert "thread_scope" not in kwargs


def test_counting_spawn_explicit_display_records_preferred_spawn(
    isolated_state,
) -> None:
    write_preferred_spawn(
        preferred_spawn="14", source="test", updated_by="test", emit=False
    )
    with patch("cursor_bridge.grok_counting_spawn.spawn_grok_agent") as spawn_fn:
        spawn_fn.return_value = {"ok": True, "agent_id": "a1", "display": "9"}
        out = spawn_counting_grok_agent("counter-bot", display="9")
    assert spawn_fn.call_args.args[0] == "9"
    assert out["preferred_spawn"] == "14"
    assert out["effective_display"] == "9"
    assert out["spawn_report"]["preferred_spawn"] == "14"
    assert out["spawn_report"]["effective_display"] == "9"
    kwargs = spawn_fn.call_args.kwargs
    assert "mailbox" not in kwargs
    assert "thread_scope" not in kwargs


def test_preferred_spawn_on_disk_invalid_window_blocks_without_raise(
    isolated_state,
) -> None:
    write_preferred_spawn(
        preferred_spawn="14", source="test", updated_by="test", emit=False
    )
    raw = json.loads(isolated_state.read_text(encoding="utf-8"))
    raw["preferred_spawn"] = "11"
    isolated_state.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    out = preferred_spawn_for()
    assert out == {
        "ok": False,
        "code": "BLOCKED",
        "reason": "preferred_spawn invalid",
    }


def test_counting_spawn_explicit_display_survives_invalid_preferred_on_disk(
    isolated_state,
) -> None:
    write_preferred_spawn(
        preferred_spawn="14", source="test", updated_by="test", emit=False
    )
    raw = json.loads(isolated_state.read_text(encoding="utf-8"))
    raw["preferred_spawn"] = "11"
    isolated_state.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    with patch("cursor_bridge.grok_counting_spawn.spawn_grok_agent") as spawn_fn:
        spawn_fn.return_value = {"ok": True, "agent_id": "a1", "display": "14"}
        out = spawn_counting_grok_agent("counter-bot", display="14")
    assert isinstance(out, dict)
    assert out.get("op") == "counting_spawn"
    assert out["effective_display"] == "14"
    assert out["preferred_spawn"] is None
    assert spawn_fn.call_args.args[0] == "14"


def test_counting_spawn_rejects_mailbox_kwargs_on_real_helper(isolated_state) -> None:
    write_preferred_spawn(
        preferred_spawn="14", source="test", updated_by="test", emit=False
    )
    with patch("cursor_bridge.grok_counting_spawn.spawn_grok_agent") as spawn_fn:
        spawn_fn.return_value = {"ok": True}
        spawn_counting_grok_agent("bot")
    if spawn_fn.call_args.kwargs.get("mailbox") is not None:
        pytest.fail("mailbox must not be passed from counting spawn")
    if spawn_fn.call_args.kwargs.get("thread_scope") is not None:
        pytest.fail("thread_scope must not be passed from counting spawn")