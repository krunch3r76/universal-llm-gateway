"""Shared pytest fixtures for libs/ offline registry and display tests."""

from __future__ import annotations

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
def _cdp_registry_seat_authority_for_offline_tests(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Hermetic registry tests bind seats against tmp dirs — act as authority."""
    if request.node.get_closest_marker("offline") is None:
        return
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")
