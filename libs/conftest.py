"""Default-unobserved X probe so Jupiter MaxClients does not fail hermetic mint tests."""

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
