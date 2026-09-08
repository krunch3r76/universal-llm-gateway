"""Tests for O15 D2 lid-close seal on CHECKPOINT post."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from agent_bus_store.checkpoint_lid_seal import maybe_schedule_lid_seal_on_checkpoint
from cortex_store.transcript_cp_anchors import window_anchors_from_text

pytestmark = pytest.mark.offline


def test_window_anchors_from_text_parses_window_line() -> None:
    body = (
        "## Residue\n"
        "Window: transcript_id=9c37637d-aaaa-bbbb-cccc-ddddeeeeffff · turns@cp=87\n"
        "Settled: speech-tape arc.\n"
    )
    assert window_anchors_from_text(body) == (
        ("9c37637d-aaaa-bbbb-cccc-ddddeeeeffff", 87),
    )


def test_window_anchors_from_text_ignores_non_window_lines() -> None:
    assert window_anchors_from_text("No window anchor here.") == ()


@patch("agent_bus_store.checkpoint_lid_seal._run_lid_seals")
def test_lid_seal_scheduled_for_root_checkpoint_with_window(mock_run) -> None:
    body = "Window: transcript_id=abc-123 · turns@cp=6\n"
    maybe_schedule_lid_seal_on_checkpoint(
        thread="10223",
        subject="CHECKPOINT — accelerated window",
        body=body,
        thread_tags=["role:root"],
    )
    time.sleep(0.05)
    mock_run.assert_called_once_with(
        thread="10223",
        anchors=(("abc-123", 6),),
    )


@patch("agent_bus_store.checkpoint_lid_seal._run_lid_seals")
def test_lid_seal_skipped_without_window_anchor(mock_run) -> None:
    maybe_schedule_lid_seal_on_checkpoint(
        thread="10223",
        subject="CHECKPOINT — no window",
        body="Settled only.",
        thread_tags=["role:root"],
    )
    mock_run.assert_not_called()


@patch("agent_bus_store.checkpoint_lid_seal._run_lid_seals")
def test_lid_seal_skipped_for_non_root_lane(mock_run) -> None:
    maybe_schedule_lid_seal_on_checkpoint(
        thread="10303",
        subject="CHECKPOINT — sub lane",
        body="Window: transcript_id=abc-123 · turns@cp=1\n",
        thread_tags=["contract:implement"],
    )
    mock_run.assert_not_called()


@patch("agent_bus_store.checkpoint_lid_seal._run_lid_seals")
def test_lid_seal_skipped_for_non_checkpoint_subject(mock_run) -> None:
    maybe_schedule_lid_seal_on_checkpoint(
        thread="10223",
        subject="INFO — status fold",
        body="Window: transcript_id=abc-123 · turns@cp=1\n",
        thread_tags=["role:root"],
    )
    mock_run.assert_not_called()


@patch("agent_bus_store.tape_harvest.request_lid_close_seal")
@patch("agent_bus_store.checkpoint_lid_seal.emit_checkpoint_lid_seal_requested")
@patch("agent_bus_store.checkpoint_lid_seal.emit_checkpoint_lid_seal_failed")
def test_run_lid_seals_emits_requested_and_harvests(
    mock_failed,
    mock_requested,
    mock_harvest,
) -> None:
    from agent_bus_store.checkpoint_lid_seal import _run_lid_seals

    mock_harvest.return_value = {"sealed": 1, "discovered": 1}
    _run_lid_seals(
        thread="10223",
        anchors=(("uuid-a", 12), ("uuid-b", 34)),
    )
    assert mock_requested.call_count == 2
    mock_requested.assert_any_call(
        thread="10223",
        transcript_id="uuid-a",
        turns_at_cp=12,
    )
    assert mock_harvest.call_count == 2
    mock_harvest.assert_any_call(
        thread_id="10223",
        explicit_transcript_ids=["uuid-a"],
    )
    mock_failed.assert_not_called()


@patch("agent_bus_store.tape_harvest.request_lid_close_seal")
@patch("agent_bus_store.checkpoint_lid_seal.emit_checkpoint_lid_seal_requested")
@patch("agent_bus_store.checkpoint_lid_seal.emit_checkpoint_lid_seal_failed")
def test_run_lid_seals_emits_failed_on_harvest_error(
    mock_failed,
    mock_requested,
    mock_harvest,
) -> None:
    from agent_bus_store.checkpoint_lid_seal import _run_lid_seals

    mock_harvest.return_value = {"error": "cortex-api harvest failed: HTTP 500"}
    _run_lid_seals(thread="10223", anchors=(("uuid-a", 5),))
    mock_requested.assert_called_once()
    mock_failed.assert_called_once_with(
        thread="10223",
        transcript_id="uuid-a",
        turns_at_cp=5,
        error="cortex-api harvest failed: HTTP 500",
        detail=None,
    )


def test_request_lid_close_seal_caps_at_one() -> None:
    from agent_bus_store.tape_harvest import request_lid_close_seal

    with patch(
        "agent_bus_store.tape_harvest._call_transcript_harvest",
        return_value={"sealed": 1},
    ) as mock_call:
        request_lid_close_seal(
            thread_id="10223",
            explicit_transcript_ids=["uuid-a"],
        )
    mock_call.assert_called_once_with(
        thread_id="10223",
        explicit_ids=["uuid-a"],
        max_seals=1,
        timeout=120.0,
    )
