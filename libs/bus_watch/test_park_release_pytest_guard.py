"""park_release refuses live ledger under pytest (r1-8)."""

from __future__ import annotations

from unittest.mock import patch

from bus_watch.spawn_wake.park_release import arm_hop_park_release


def test_arm_hop_park_release_false_when_connect_refused() -> None:
    body: dict = {"reuse_thread": "t"}
    with patch(
        "services.git_integration_worker.cursor_dispatch_ledger._connect",
        side_effect=RuntimeError("pytest guard"),
    ):
        assert arm_hop_park_release(body, "work-key") is False
    assert "generation_options" not in body
