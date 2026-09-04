"""Standing CDP pins.toml parses via tomllib.load (binary file load)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from cdp_ask.standing_pins import _load_pins

_REPO = Path(__file__).resolve().parents[2]
_PINS = _REPO / "services" / "jupiter-cdp" / "pins.toml"

_EXPECTED_LANES = frozenset({"fleet", "messages", "ess", "gopuff", "uber"})
_REQUIRED_KEYS = frozenset(
    {"port", "display", "profile", "standing", "lapsed_url_prefixes", "extra_args"}
)


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
