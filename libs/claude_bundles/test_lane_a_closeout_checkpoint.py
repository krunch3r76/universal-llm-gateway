"""Unit tests for lane-A CLOSEOUT checkpoint fail-closed gate."""

from __future__ import annotations

import pytest

from claude_bundles.lane_a_closeout_checkpoint import (
    LANE_A_CHECKPOINT_FIX_HINT,
    is_lane_a_closeout,
    refusal_envelope,
    validate_lane_a_closeout_checkpoint,
)

pytestmark = pytest.mark.offline


def test_missing_checkpoint_refused() -> None:
    body = "TYPE: CLOSEOUT\nstatus: complete\n\n| status | complete |"
    verdict = validate_lane_a_closeout_checkpoint(body=body)
    assert verdict.ok is False
    assert verdict.reason == "lane_a_checkpoint_missing"
    assert verdict.missed_tokens == ("checkpoint:",)
    env = refusal_envelope(verdict)
    assert env["status"] == "blocked"
    assert env["fix_hint"] == LANE_A_CHECKPOINT_FIX_HINT


def test_deferred_passes() -> None:
    body = (
        "TYPE: CLOSEOUT\n"
        "status: complete\n"
        "checkpoint: deferred: foreign WIP on shared checkout\n"
    )
    verdict = validate_lane_a_closeout_checkpoint(body=body)
    assert verdict.ok is True
    assert verdict.checkpoint_value == "deferred: foreign WIP on shared checkout"


def test_committed_passes() -> None:
    body = (
        "TYPE: CLOSEOUT\n"
        "checkpoint: committed abc1234 paths=3\n"
    )
    assert validate_lane_a_closeout_checkpoint(body=body).ok is True


def test_committed_with_pending_passes() -> None:
    """Row 18 — mixed committed+pending token must clear the fail-closed gate."""
    body = (
        "TYPE: CLOSEOUT\n"
        "checkpoint: committed abc1234 paths=3 (+2 pending)\n"
    )
    verdict = validate_lane_a_closeout_checkpoint(body=body)
    assert verdict.ok is True
    assert verdict.checkpoint_value == "committed abc1234 paths=3 (+2 pending)"


def test_nothing_authored_passes() -> None:
    body = "TYPE: CLOSEOUT\ncheckpoint: nothing_authored\n"
    assert validate_lane_a_closeout_checkpoint(body=body).ok is True


def test_malformed_checkpoint_refused() -> None:
    body = "TYPE: CLOSEOUT\ncheckpoint: committed-but-no-sha\n"
    verdict = validate_lane_a_closeout_checkpoint(body=body)
    assert verdict.ok is False
    assert verdict.reason == "lane_a_checkpoint_malformed"


def test_non_closeout_passes_without_checkpoint() -> None:
    assert is_lane_a_closeout(body="TYPE: DIRECTIVE\ncontract: implement\n") is False
    assert validate_lane_a_closeout_checkpoint(
        body="TYPE: DIRECTIVE\ncontract: implement\n"
    ).ok is True


def test_malformed_doubled_checkpoint_normalizes() -> None:
    from claude_bundles.lane_a_closeout_checkpoint import normalize_checkpoint_value

    raw = "checkpoint: committed f230fa040476144e73827520ee5a78d470a24107 paths=5"
    body = f"TYPE: CLOSEOUT\ncheckpoint: `{raw}`\n"
    verdict = validate_lane_a_closeout_checkpoint(body=body)
    assert verdict.ok, verdict.reason
    assert normalize_checkpoint_value(f"`{raw}`") == (
        "committed f230fa040476144e73827520ee5a78d470a24107 paths=5"
    )
