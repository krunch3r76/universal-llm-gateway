"""Standing CDP pins.toml parses via tomllib.load (binary file load)."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cdp_ask import standing_pins
from cdp_ask.standing_pins import (
    StandingPinHealth,
    _load_pins,
    _probe_lane,
    _unit_active,
    probe_health,
)

_REPO = Path(__file__).resolve().parents[2]
_PINS = _REPO / "services" / "jupiter-cdp" / "pins.toml"

_EXPECTED_LANES = frozenset({"fleet", "messages", "ess", "gopuff", "uber"})
_REQUIRED_KEYS = frozenset(
    {"port", "display", "profile", "standing", "lapsed_url_prefixes", "extra_args"}
)
_STANDING_LANES = frozenset({"fleet", "messages", "ess"})


@pytest.mark.offline
def test_committed_pins_toml_parses_lanes_table() -> None:
    with _PINS.open("rb") as f:
        table = tomllib.load(f)
    lanes = table.get("lanes")
    assert isinstance(lanes, dict)
    assert set(lanes) == _EXPECTED_LANES
    for name, row in lanes.items():
        assert isinstance(row, dict), f"lane {name!r} must be a table"
        assert _REQUIRED_KEYS <= set(row), f"lane {name!r} missing keys"


@pytest.mark.offline
def test_load_pins_uses_binary_file_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ULG_REPO", str(_REPO))
    lanes = _load_pins()
    assert set(lanes) == _EXPECTED_LANES
    assert lanes["fleet"]["port"] == 9222
    assert lanes["fleet"]["standing"] is True


@pytest.mark.offline
def test_standing_pins_include_fleet_messages_ess() -> None:
    with _PINS.open("rb") as f:
        lanes = tomllib.load(f)["lanes"]
    standing = {name for name, row in lanes.items() if row.get("standing")}
    assert standing == _STANDING_LANES


@pytest.mark.offline
def test_messages_lapsed_url_prefixes_committed() -> None:
    with _PINS.open("rb") as f:
        messages = tomllib.load(f)["lanes"]["messages"]
    assert messages["lapsed_url_prefixes"] == ["/web/welcome", "/web/authentication"]


@pytest.mark.offline
def test_unit_active_true_when_systemd_reports_active() -> None:
    proc = MagicMock(stdout="active\n")
    with patch.object(standing_pins.subprocess, "run", return_value=proc) as run:
        assert _unit_active("fleet") is True
    run.assert_called_once_with(
        ["systemctl", "--user", "is-active", "cdp-lane@fleet.service"],
        capture_output=True,
        text=True,
        check=False,
        timeout=2,
    )


@pytest.mark.offline
def test_unit_active_false_when_inactive_or_timeout() -> None:
    proc = MagicMock(stdout="inactive\n")
    with patch.object(standing_pins.subprocess, "run", return_value=proc):
        assert _unit_active("fleet") is False

    with patch.object(
        standing_pins.subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired(cmd="systemctl", timeout=2),
    ):
        assert _unit_active("fleet") is False


@pytest.mark.offline
def test_probe_lane_messages_lapsed_on_matching_url() -> None:
    row = {
        "port": 9250,
        "lapsed_url_prefixes": ["/web/welcome", "/web/authentication"],
    }
    with (
        patch.object(standing_pins, "_unit_active", return_value=True),
        patch.object(standing_pins, "_cdp_json", return_value={"Browser": "Chrome"}),
        patch.object(
            standing_pins,
            "_top_page_url",
            return_value="https://messages.example/web/welcome",
        ),
        patch.object(standing_pins, "emit_standing_lapsed") as emit_lapsed,
    ):
        health, _ = _probe_lane("messages", row, prev="UP")
    assert health.state == "LAPSED"
    emit_lapsed.assert_called_once()


@pytest.mark.offline
def test_probe_lane_down_when_unit_inactive() -> None:
    row = {"port": 9222, "lapsed_url_prefixes": []}
    with (
        patch.object(standing_pins, "_unit_active", return_value=False),
        patch.object(standing_pins, "emit_standing_down") as emit_down,
    ):
        health, _ = _probe_lane("fleet", row, prev="UP")
    assert health.state == "DOWN"
    emit_down.assert_called_once()


@pytest.mark.offline
def test_probe_health_logs_and_continues_when_pins_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("ULG_REPO", str(_REPO))
    standing_pins._prev_states.clear()
    with (
        patch.object(standing_pins, "_probe_display", side_effect=["live", "live"]),
        patch.object(standing_pins, "_load_pins", side_effect=RuntimeError("ULG_REPO not set")),
    ):
        with caplog.at_level("WARNING"):
            displays, standing = probe_health()
    assert displays == {":2": "live", ":3": "live"}
    assert standing == {}
    assert any("standing_pins probe_health failed" in r.message for r in caplog.records)


@pytest.mark.offline
def test_probe_health_projects_standing_lanes_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_REPO", str(_REPO))
    standing_pins._prev_states.clear()
    with (
        patch.object(standing_pins, "_probe_display", side_effect=["live", "live"]),
        patch.object(standing_pins, "_unit_active", return_value=True),
        patch.object(standing_pins, "_cdp_json", return_value={"Browser": "Chrome"}),
        patch.object(standing_pins, "_top_page_url", return_value="https://example.com/"),
        patch.object(standing_pins, "_cdp_roundtrip_ok", return_value=True),
        patch.object(standing_pins, "emit_standing_up"),
    ):
        displays, standing = probe_health()
    assert displays == {":2": "live", ":3": "live"}
    assert set(standing) == _STANDING_LANES
    assert all(isinstance(h, StandingPinHealth) for h in standing.values())
    assert all(h.state == "UP" for h in standing.values())


@pytest.mark.offline
def test_probe_health_keeps_prev_state_when_emit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_REPO", str(_REPO))
    standing_pins._prev_states.clear()
    standing_pins._prev_states["fleet"] = "DOWN"
    row = {"port": 9222, "lapsed_url_prefixes": [], "standing": True}
    with (
        patch.object(standing_pins, "_probe_display", side_effect=["live", "live"]),
        patch.object(standing_pins, "_load_pins", return_value={"fleet": row}),
        patch.object(standing_pins, "_unit_active", return_value=True),
        patch.object(standing_pins, "_cdp_json", return_value={"Browser": "Chrome"}),
        patch.object(standing_pins, "_top_page_url", return_value="https://example.com/"),
        patch.object(standing_pins, "_cdp_roundtrip_ok", return_value=True),
        patch.object(standing_pins, "_emit_transition", return_value=False),
    ):
        _, standing = probe_health()
    assert standing["fleet"].state == "UP"
    assert standing_pins._prev_states["fleet"] == "DOWN"


@pytest.mark.offline
def test_probe_lane_hung_when_roundtrip_fails() -> None:
    row = {"port": 9222, "lapsed_url_prefixes": []}
    with (
        patch.object(standing_pins, "_unit_active", return_value=True),
        patch.object(
            standing_pins,
            "_cdp_json",
            return_value={
                "Browser": "Chrome",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/x",
            },
        ),
        patch.object(standing_pins, "_top_page_url", return_value="https://example.com/"),
        patch.object(standing_pins, "_cdp_roundtrip_ok", return_value=False),
        patch.object(standing_pins, "emit_standing_hung") as emit_hung,
    ):
        health, _ = _probe_lane("fleet", row, prev="UP")
    assert health.state == "HUNG"
    emit_hung.assert_called_once()

