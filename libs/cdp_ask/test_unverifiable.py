"""Unverifiable-class stall vs CSE-death (a:30678)."""

from __future__ import annotations

import pytest

from cdp_ask.models import classify_stall_stage
from cdp_ask.unverifiable import (
    DEATH_STALL_STAGES,
    converse_fail_error,
    converse_stall_stage,
    failed_snapshot_fields,
    is_unverifiable_stall,
    transport_miss_fields,
)

pytestmark = pytest.mark.offline


def test_converse_fail_error_preserves_inner() -> None:
    assert converse_fail_error("model select failed: x") == "model select failed: x"
    assert converse_fail_error("  ") == "conversation failed"
    assert converse_fail_error(None) == "conversation failed"


def test_converse_stall_unknown_becomes_observer_unverified() -> None:
    assert converse_stall_stage("model select failed: x", conv_ok=False) == (
        "observer_unverified"
    )
    assert converse_stall_stage(None, conv_ok=True) is None
    assert converse_stall_stage("hit a limit", conv_ok=False) == "completion_detection"


def test_classify_conversation_failed_token() -> None:
    assert classify_stall_stage("conversation failed") == "observer_unverified"


def test_is_unverifiable_stall_death_vs_observer() -> None:
    cse = "https://claude.ai/cowork/cse_abc"
    assert is_unverifiable_stall("observer_unverified", url=cse) is True
    assert is_unverifiable_stall("unknown", "model select failed") is False
    assert is_unverifiable_stall("unknown", "wait timed out", url=cse) is True
    assert is_unverifiable_stall("weekly_limit") is False
    assert is_unverifiable_stall("unknown", "aborted") is False
    assert is_unverifiable_stall("archive_write", url=cse) is True
    assert is_unverifiable_stall("completion_detection") is False
    assert is_unverifiable_stall("unknown", url=cse, satellite_execution_id=None) is False


def test_failed_snapshot_fields_coerces_unknown() -> None:
    fields = failed_snapshot_fields(
        {
            "status": "failed",
            "stall_stage": "unknown",
            "error": "wait_assistant_reply timed out",
            "url": "https://claude.ai/cowork/cse_abc",
            "satellite_execution_id": "sat-1",
        }
    )
    assert fields["unverifiable"] is True
    assert fields["stall_stage"] == "observer_unverified"
    assert fields["extras"]["chat_url"].endswith("cse_abc")


def test_failed_snapshot_fields_skips_new_as_chat_url() -> None:
    """F1 — /new is compose fingerprint, never CSE identity."""
    fields = failed_snapshot_fields(
        {
            "status": "failed",
            "stall_stage": "unknown",
            "error": "model select failed: picker",
            "url": "https://claude.ai/new",
        }
    )
    assert fields["extras"] == {}
    assert fields["unverifiable"] is False


def test_transport_miss_fields_witness() -> None:
    cse = "https://claude.ai/cowork/cse_x"
    fields = transport_miss_fields("connection reset", cse, satellite_execution_id="sat")
    assert fields["stall_stage"] == "observer_unverified"
    assert fields["unverifiable"] is True
    assert fields["extras"]["chat_url"] == cse


def test_no_progress_in_death_stages() -> None:
    assert "no_progress" in DEATH_STALL_STAGES
    assert "wall_clock_exceeded" in DEATH_STALL_STAGES
