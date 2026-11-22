"""Grok window config, routing state, promote, and inbox aliases."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from agent_seat.registry import expand_recipient_slugs, normalize_bus_address
from cursor_bridge import grok_window_bindings as bindings
from cursor_bridge.grok_preferred_spawn import preferred_spawn_for
from cursor_bridge.grok_window_bindings import (
    binding_for,
    cdp_port_for_display,
    load_routing,
    promote,
    validate_state,
    write_preferred_spawn,
)
from cursor_bridge.grok_routing_events import register_uds_publisher


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "grok-window-routing.json"
    db_path = tmp_path / "messages.db"
    monkeypatch.setattr(bindings, "state_path", lambda: path)
    monkeypatch.setattr(bindings, "_agent_bus_db_path", lambda: db_path)
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    from agent_bus_store.db.connection import init_db

    init_db()
    return path


def test_cdp_port_for_known_displays() -> None:
    assert cdp_port_for_display("9") == 9339
    assert cdp_port_for_display(":10") == 9340
    assert cdp_port_for_display("14") == 9344
    assert cdp_port_for_display("11") is None


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


def test_promote_rejects_auxiliary_window(isolated_state) -> None:
    with pytest.raises(ValueError, match="gc1/gc2"):
        promote("14", updated_by="test")


def test_promote_flips_default_and_parks_insyncerator(isolated_state) -> None:
    assert bindings._agent_bus_db_path().parent == isolated_state.parent
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
    assert written["schema_version"] == 2
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


def test_preferred_spawn_roundtrip_preserves_home(isolated_state) -> None:
    state = write_preferred_spawn(
        preferred_spawn="14", source="test", updated_by="test", emit=False
    )
    assert state["preferred_spawn"] == "14"
    assert state["epoch"] == 1
    loaded = load_routing()
    assert loaded["preferred_spawn"] == "14"
    assert binding_for("grok-bot")["display"] == "9"


def test_preferred_spawn_rejects_unknown_window(isolated_state) -> None:
    with pytest.raises(ValueError, match="unknown grok window"):
        write_preferred_spawn(
            preferred_spawn="11", source="test", updated_by="test", emit=False
        )


def test_promote_still_rejects_auxiliary_and_preserves_preferred_spawn(
    isolated_state,
) -> None:
    write_preferred_spawn(
        preferred_spawn="14", source="test", updated_by="test", emit=False
    )
    with pytest.raises(ValueError, match="gc1/gc2"):
        promote("14", updated_by="test")
    state = promote("10", updated_by="test")
    assert state["preferred_spawn"] == "14"
    assert binding_for("grok-bot")["display"] == "10"


def test_preferred_spawn_resolver_blocks_when_unset_or_invalid(
    isolated_state,
) -> None:
    blocked = preferred_spawn_for("grok-bot")
    assert blocked["ok"] is False
    assert blocked["code"] == "BLOCKED"
    invalid_state = {
        **load_routing(),
        "preferred_spawn": "11",
    }
    with patch(
        "cursor_bridge.grok_preferred_spawn.load_routing", return_value=invalid_state
    ):
        invalid = preferred_spawn_for()
    assert invalid["ok"] is False
    assert invalid["code"] == "BLOCKED"
    write_preferred_spawn(
        preferred_spawn="14", source="test", updated_by="test", emit=False
    )
    resolved = preferred_spawn_for()
    assert resolved["ok"] is True
    assert resolved["display"] == "14"
    assert resolved["cdp_port"] == 9344


def test_preferred_spawn_write_emits_advisory_event(isolated_state) -> None:
    recorded: list[tuple[str, dict]] = []

    def _pub(signal: str, payload: dict) -> None:
        recorded.append((signal, dict(payload)))

    register_uds_publisher(_pub)
    write_preferred_spawn(
        preferred_spawn="14", source="set-preferred", updated_by="pytest"
    )
    assert recorded
    signal, payload = recorded[-1]
    assert signal == "grok.window.routing.preferred_spawn"
    assert payload["preferred_spawn"] == "14"
    assert payload["authority"] == "grok-window-routing"
    assert payload["projection"] == "spawn"
    assert payload["recovery"] == "write_preferred_spawn"
