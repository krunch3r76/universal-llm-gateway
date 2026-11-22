"""Host read-only grok routing snapshot (one epoch, errors, write guard)."""

from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

import pytest

from cursor_bridge import grok_window_bindings as bindings
from cursor_bridge.grok_routing_snapshot import build_grok_routing_read_snapshot
from cursor_bridge.grok_window_bindings import write_preferred_spawn, write_routing


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "grok-window-routing.json"
    monkeypatch.setattr(bindings, "state_path", lambda: path)
    return path


def test_snapshot_single_epoch_and_provenance(isolated_state) -> None:
    state = write_routing(
        default_window="9",
        insyncerator_window="10",
        source="test",
        updated_by="pytest",
    )
    out = build_grok_routing_read_snapshot()
    assert out["ok"] is True
    prov = out["provenance"]
    assert prov["epoch"] == state["epoch"]
    assert prov["as_of"] == state["as_of"]
    assert prov["source"] == state["source"]
    assert out["default_window"]["display"] == "9"
    assert out["grok_bot_home"]["display"] == "9"
    assert out["grok_bot_home"]["display_scope"]
    assert out["grok_bot_home"]["display_authority"] == "recorded"


def test_absent_state_file_uses_builtin(isolated_state) -> None:
    assert not isolated_state.is_file()
    out = build_grok_routing_read_snapshot()
    assert out["ok"] is True
    assert out["provenance"]["state_file_present"] is False
    assert out["provenance"]["source"] == "builtin"
    assert out["provenance"]["epoch"] == 0


def test_corrupt_json_errors(isolated_state) -> None:
    isolated_state.write_text("{not json", encoding="utf-8")
    out = build_grok_routing_read_snapshot()
    assert out["ok"] is False
    assert out["error"]["code"] == "routing_state_corrupt"
    assert out["error"]["retryable"] is False


def test_invalid_scope_errors(isolated_state) -> None:
    isolated_state.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "epoch": 1,
                "as_of": "2020-01-01T00:00:00+00:00",
                "source": "test",
                "scope": "wrong",
                "updated_at": "2020-01-01T00:00:00+00:00",
                "updated_by": "t",
                "default_window": "9",
                "insyncerator_window": "10",
            }
        ),
        encoding="utf-8",
    )
    out = build_grok_routing_read_snapshot()
    assert out["ok"] is False
    assert out["error"]["code"] == "routing_state_invalid"


def test_unknown_window_errors(isolated_state) -> None:
    isolated_state.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "epoch": 1,
                "as_of": "2020-01-01T00:00:00+00:00",
                "source": "test",
                "scope": "grok-window-routing",
                "updated_at": "2020-01-01T00:00:00+00:00",
                "updated_by": "t",
                "default_window": "11",
                "insyncerator_window": "10",
            }
        ),
        encoding="utf-8",
    )
    out = build_grok_routing_read_snapshot()
    assert out["ok"] is False
    assert out["error"]["code"] == "routing_state_invalid"


def test_preferred_spawn_present_and_absent(isolated_state) -> None:
    write_preferred_spawn(
        preferred_spawn="14", source="test", updated_by="pytest", emit=False
    )
    present = build_grok_routing_read_snapshot()
    assert present["preferred_spawn_present"] is True
    assert present["preferred_spawn"]["display"] == "14"

    isolated_state.unlink(missing_ok=True)
    absent = build_grok_routing_read_snapshot()
    assert absent["preferred_spawn_present"] is False
    assert "preferred_spawn" not in absent


def test_reads_do_not_mutate_state_file_sha256(isolated_state) -> None:
    write_routing(
        default_window="9",
        insyncerator_window="10",
        source="test",
        updated_by="pytest",
    )
    digest_before = hashlib.sha256(isolated_state.read_bytes()).hexdigest()

    with patch.object(bindings, "write_routing") as write_mock, patch.object(
        bindings, "write_preferred_spawn"
    ) as pref_mock:
        for _ in range(3):
            build_grok_routing_read_snapshot()
        write_mock.assert_not_called()
        pref_mock.assert_not_called()

    assert hashlib.sha256(isolated_state.read_bytes()).hexdigest() == digest_before