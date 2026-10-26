"""Shared pytest fixtures for libs/ offline registry and display tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _x_display_unobserved_by_default(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """``count_x11_unix_clients`` → None unless the test opts into a live/injected count.

    Production mint on Jupiter reads ``/proc/net/unix``. Leaving that live in
    unit tests would refuse every ``register_lane`` when Xvfb is at cap.
    """
    monkeypatch.setenv("CDP_DISPLAY", ":2")
    if request.node.get_closest_marker("live_x_display"):
        return
    monkeypatch.setattr(
        "claude_bundles.x_display_capacity.count_x11_unix_clients",
        lambda display, proc_net_unix=None: None,
    )


@pytest.fixture(autouse=True)
def _pin_lanes_empty_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Host ``pins.toml`` must not change headroom math or port selection.

    Tests that cover pin reserve or pin-port exclusion replace ``_load_pin_lanes``.
    """
    monkeypatch.setattr(
        "claude_bundles.x_display_capacity._load_pin_lanes",
        lambda: {},
    )


@pytest.fixture(autouse=True)
def _cdp_accounts_config_test_map(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Tests that mint account-2 rows need a ``cdp_account_displays`` entry."""
    if request.node.get_closest_marker("offline") is None:
        return
    repo_root = Path(__file__).resolve().parent.parent
    src = repo_root / "config" / "cdp" / "accounts.yaml"
    text = src.read_text(encoding="utf-8")
    if "cdp_account_displays: {}" in text:
        text = text.replace(
            "cdp_account_displays: {}",
            'cdp_account_displays:\n  account-2: ":4"',
            1,
        )
    dest = tmp_path / "cdp-accounts.yaml"
    dest.write_text(text, encoding="utf-8")
    monkeypatch.setenv("CDP_ACCOUNTS_CONFIG_PATH", str(dest))


@pytest.fixture(autouse=True)
def _cdp_registry_seat_authority_for_offline_tests(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Hermetic registry tests bind seats against tmp dirs — act as authority."""
    if request.node.get_closest_marker("offline") is None:
        return
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")
