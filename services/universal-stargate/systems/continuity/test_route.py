"""Route tests for continuity checkpoint and tape-read."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.offline


def test_checkpoint_request_model_fields() -> None:
    from systems.continuity.models import CheckpointAccepted, CheckpointRequest

    req = CheckpointRequest(
        thread="10223",
        surface="cursor",
        from_agent="cursor",
        transcript_id="550e8400-e29b-41d4-a716-446655440000",
        pre_consolidate=True,
    )
    assert req.thread == "10223"
    assert req.surface == "cursor"

    accepted = CheckpointAccepted(
        execution_id="exec-1",
        pipeline="continuity-checkpoint-v1",
        thread="10223",
        started_at="2026-09-09T00:00:00Z",
        poll_hint={"tool": "agent_bus_read"},
    )
    assert accepted.status == "running"


@pytest.mark.skip(reason="phase_3_not_landed")
def test_checkpoint_claude_ai_e2e_deferred() -> None:
    """Reopen when Phase 3 harvest lands."""
