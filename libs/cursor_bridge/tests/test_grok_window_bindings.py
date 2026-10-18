"""Grok window config, routing state, promote, and inbox aliases."""

from __future__ import annotations

import json

import pytest
from agent_seat.registry import expand_recipient_slugs, normalize_bus_address
from cursor_bridge import grok_window_bindings as bindings
from cursor_bridge.grok_window_bindings import binding_for, promote, validate_state


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "grok-window-routing.json"
    monkeypatch.setattr(bindings, "state_path", lambda: path)
    return path


def test_builtin_initial_state_when_file_missing(isolated_state) -> None:
    assert binding_for("grok-bot")["display"] == "9"
    assert binding_for("grok-bot")["cdp_port"] == 9339
    assert binding_for("grok-bot")["source"] == "builtin"
    assert binding_for("impresario")["display"] == "9"
    assert binding_for("grok-bot-gc1")["display"] == "9"
    assert binding_for("grok-coordinator-1")["display"] == "9"
    assert binding_for("stage-manager")["display"] == "9"
    assert binding_for("insyncerator")["display"] == "10"
    assert binding_for("insyncerator")["cdp_port"] == 9340
    assert binding_for("syncerator")["display"] == "10"
    assert binding_for("grok-bot-gc2")["display"] == "10"
    assert binding_for("cursor") is None


def test_promote_flips_default_and_parks_insyncerator(isolated_state) -> None:
    state = promote("10", updated_by="test")
    assert state["default_window"] == "10"
    assert state["insyncerator_window"] == "9"
    assert state["epoch"] == 1
    assert state["source"] == "promote"
    assert state["scope"] == "grok-window-routing"
    assert state["updated_by"] == "test"
    assert binding_for("grok-bot")["cdp_port"] == 9340
    assert binding_for("insyncerator")["display"] == "9"
    assert binding_for("syncerator")["cdp_port"] == 9339
    assert binding_for("grok-bot-gc1")["display"] == "10"
    assert binding_for("grok-coordinator-1")["display"] == "10"
    assert binding_for("grok-bot-gc2")["display"] == "9"
    written = json.loads(isolated_state.read_text(encoding="utf-8"))
    assert written["schema_version"] == 1
    again = promote(":9", updated_by="test")
    assert again["epoch"] == 2
    assert again["default_window"] == "9"
    assert again["insyncerator_window"] == "10"


def test_state_rejects_unknown_window(isolated_state) -> None:
    with pytest.raises(ValueError, match="unknown grok window"):
        validate_state(
            {
                "schema_version": 1,
                "epoch": 1,
                "as_of": "2026-10-17T00:00:00+00:00",
                "source": "set-routing",
                "scope": "grok-window-routing",
                "updated_at": "2026-10-17T00:00:00+00:00",
                "updated_by": "test",
                "default_window": "11",
                "insyncerator_window": "9",
            }
        )


def test_inbox_aliases_share_insyncerator(isolated_state) -> None:
    assert normalize_bus_address("syncerator") == "insyncerator"
    assert normalize_bus_address("grok-bot-gc2") == "insyncerator"
    expanded = set(expand_recipient_slugs("insyncerator"))
    assert {"insyncerator", "grok-bot-gc2", "syncerator"} <= expanded
    assert "grok-bot" not in expanded
