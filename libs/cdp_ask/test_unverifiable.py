"""Unverifiable-class stall vs CSE-death (a:30678)."""

from __future__ import annotations

import pytest
from claude_bundles.chat_model_match import select_no_attest_status

from cdp_ask.models import classify_stall_stage
from cdp_ask.unverifiable import (
    DEATH_STALL_STAGES,
    UNVERIFIABLE_STALL_STAGES,
    WALL_CLOCK_EXCEEDED_ABORT_UNCONFIRMED,
    converse_fail_error,
    converse_stall_stage,
    failed_snapshot_fields,
    is_unverifiable_stall,
    model_select_status_lines,
    transport_miss_fields,
    wall_abort_unconfirmed,
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


def test_select_no_attest_record_is_not_observer_unverified() -> None:
    record = select_no_attest_status(
        requested="fable-5.1",
        before="Model: Opus 5.5 Medium",
        after="Model: Opus 5.5 High",
        matched="Fable 5.1For your toughest challenges",
        path="discover",
        available=["Fable 5.1For your toughest challenges"],
        as_of="2026-09-22T00:00:00+00:00",
    )
    error = f"model select failed: {record}"
    assert classify_stall_stage(error) == "select_no_attest"
    assert converse_stall_stage(error, conv_ok=False) == "select_no_attest"
    assert converse_stall_stage("model select failed: x", conv_ok=False) == (
        "observer_unverified"
    )
    fields = failed_snapshot_fields(
        {
            "status": "failed",
            "stall_stage": "unknown",
            "error": error,
            "url": "https://claude.ai/cowork/cse_abc",
            "satellite_execution_id": "sat-1",
        }
    )
    assert fields["stall_stage"] == "select_no_attest"
    assert fields["unverifiable"] is False
    assert fields["retain_reason"] is None
    text = "\n".join(model_select_status_lines(error))
    assert "- step: `select_no_attest`" in text
    assert "- before: `Model: Opus 5.5 Medium`" in text
    assert "- after: `Model: Opus 5.5 High`" in text
    assert "- matched: `Fable 5.1For your toughest challenges`" in text
    assert "- requested: `fable-5.1`" in text
    assert "- path: `discover`" in text
    assert "- as_of: `2026-09-22T00:00:00+00:00`" in text
    assert "- source: `cdp.model_select`" in text
    assert "- menu_label_glued: `True`" in text
    assert "- matched_is_chip: `False`" in text
    assert "- effort_only_chip_change: `True`" in text
    assert (
        is_unverifiable_stall(
            "select_no_attest", error, url=fields["extras"]["chat_url"]
        )
        is False
    )


def test_is_unverifiable_stall_death_vs_observer() -> None:
    cse = "https://claude.ai/cowork/cse_abc"
    assert is_unverifiable_stall("observer_unverified", url=cse) is True
    assert is_unverifiable_stall("unknown", "model select failed") is False
    assert is_unverifiable_stall("unknown", "wait timed out", url=cse) is True
    assert is_unverifiable_stall("weekly_limit") is False
    assert is_unverifiable_stall("unknown", "aborted") is False
    assert is_unverifiable_stall("archive_write", url=cse) is True
    assert is_unverifiable_stall("completion_detection") is False
    assert (
        is_unverifiable_stall("unknown", url=cse, satellite_execution_id=None) is False
    )


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
    fields = transport_miss_fields(
        "connection reset", cse, satellite_execution_id="sat"
    )
    assert fields["stall_stage"] == "observer_unverified"
    assert fields["unverifiable"] is True
    assert fields["extras"]["chat_url"] == cse


def test_no_progress_in_death_stages() -> None:
    assert "no_progress" in DEATH_STALL_STAGES
    assert "wall_clock_exceeded" in DEATH_STALL_STAGES


def test_wall_abort_unconfirmed_predicate() -> None:
    stage = WALL_CLOCK_EXCEEDED_ABORT_UNCONFIRMED
    assert stage in UNVERIFIABLE_STALL_STAGES
    assert stage not in DEATH_STALL_STAGES
    assert wall_abort_unconfirmed({"error": "stop missed"}, sat_id="sat") is True
    assert wall_abort_unconfirmed({"status_code": 409}, sat_id="sat") is True
    assert wall_abort_unconfirmed({}, sat_id="sat") is True
    assert wall_abort_unconfirmed(None, sat_id="sat") is True
    assert (
        wall_abort_unconfirmed({"ok": True, "status": "aborted"}, sat_id="sat") is False
    )
    assert (
        wall_abort_unconfirmed({"ok": True, "status_code": 200}, sat_id="sat") is False
    )
    assert wall_abort_unconfirmed({}, sat_id=None) is False
