"""Hermetic contract: live probe generate kwargs always opt out of auto-review."""

from __future__ import annotations

from scripts.model_manager.ui.dispatch_monitor.ulg.live_probe_generate import (
    live_probe_generate_kwargs,
)


def test_forces_auto_review_child_false_by_default() -> None:
    kwargs = live_probe_generate_kwargs()
    assert kwargs["auto_review_child"] is False
    assert kwargs["lane"] == "A"


def test_overrides_cannot_win_over_false() -> None:
    kwargs = live_probe_generate_kwargs(
        auto_review_child=True,
        contract="light-bounded",
        seat="cursor-sdk",
    )
    assert kwargs["auto_review_child"] is False
    assert kwargs["contract"] == "light-bounded"
    assert kwargs["seat"] == "cursor-sdk"
