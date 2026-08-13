"""Hop cadence handoff must not arm durable keep-alive (6661 sole-wake)."""

from __future__ import annotations

import pytest

from services.git_integration_worker.cursor_auto.hop_cadence import (
    build_cadence_hop_body,
)
from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
    HopDecision,
    StandingHandoffFreshness,
)

pytestmark = pytest.mark.offline


def test_cadence_hop_body_forbids_monitor_arm() -> None:
    decision = HopDecision(
        thread_id="6655",
        action="fire",
        reason="watch_seated_at",
        age_s=2000.0,
        threshold_s=1500.0,
        signal="watch_seated_at",
        handoff=StandingHandoffFreshness(
            status="current",
            uri="cortex://notes/system/threads/6655-standing-handoff.md",
            mtime_epoch=1.0,
            age_s=10.0,
        ),
    )
    body = build_cadence_hop_body(decision, registration_id="abc123")
    assert "TYPE: CONTINUITY_HANDOFF" in body
    assert "you_are:" in body
    assert "parent_thread: 6655" in body
    assert "Do NOT arm Monitor" in body
    assert "Arm Monitor + send_later" not in body
    assert "KEEP-ALIVE" in body or "keep-alive" in body.lower()
    assert "PRIMARY" in body or "primary" in body.lower()
