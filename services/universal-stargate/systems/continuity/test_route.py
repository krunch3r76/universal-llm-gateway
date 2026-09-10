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


def test_tape_read_envelope_carries_cells() -> None:
    from continuity_tape.messages import ContinuityMessagesEnvelope
    from systems.continuity.tape_read import build_envelope_from_tape

    tape = {
        "messages": [{"role": "user", "content": "hi", "turn_index": 1}],
        "index": [],
        "cells": [
            {
                "cp_ordinal": 1,
                "transcript_id": "uuid",
                "turn_lo": 0,
                "turn_hi": 1,
                "bus_turn_id": 10,
            }
        ],
        "segments": [{"session_id": "s1", "transcript_id": "uuid"}],
        "open_line": {"turn_count": 1, "truncated": False},
        "meta": {"messages_sha256": "abc"},
    }
    envelope = build_envelope_from_tape(
        tape,
        request={"thread": "10223", "scope": "window", "include_extras": True},
        caller_agent="cursor",
    )
    assert isinstance(envelope, ContinuityMessagesEnvelope)
    assert len(envelope.cells) == 1
    assert envelope.meta.checkpoint_turns == [10]


@pytest.mark.skip(reason="phase_3_not_landed")
def test_checkpoint_claude_ai_e2e_deferred() -> None:
    """Reopen when Phase 3 harvest lands."""
