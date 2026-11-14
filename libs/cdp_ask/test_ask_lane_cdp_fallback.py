"""cdp_ask routes browser CDP to fleet when ask lane is absent from pins.toml."""

from __future__ import annotations

from pathlib import Path

import pytest

from cdp_ask.standing_pins import resolve_cdp_ask_browser_cdp_url

pytestmark = pytest.mark.offline


def test_resolve_cdp_ask_browser_falls_back_to_fleet_when_ask_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing ask lane configuration must select fleet :9222, not :9223."""
    repo = tmp_path / "ulg"
    repo.mkdir()
    pins_dir = repo / "services" / "jupiter-cdp"
    pins_dir.mkdir(parents=True)
    (pins_dir / "pins.toml").write_text(
        '[lanes.fleet]\nport = 9222\nstanding = true\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ULG_REPO", str(repo))
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_LEDGER", str(tmp_path / "ledger"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    url, via = resolve_cdp_ask_browser_cdp_url()
    assert url == "http://127.0.0.1:9222"
    assert via == "fleet"


def test_resolve_cdp_ask_browser_uses_ask_lane_when_configured() -> None:
    lanes = {
        "ask": {"port": 9223},
        "fleet": {"port": 9222},
    }
    url, via = resolve_cdp_ask_browser_cdp_url(lanes=lanes)
    assert url == "http://127.0.0.1:9223"
    assert via == "ask"
