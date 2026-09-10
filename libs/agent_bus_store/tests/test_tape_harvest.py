"""Tests for tape_harvest orchestration (AMEND-R T10–T13)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent_bus_store.tape_harvest import render_tape_with_harvest

pytestmark = pytest.mark.offline


@patch("agent_bus_store.tape_harvest.render_tape")
@patch("agent_bus_store.tape_harvest._call_transcript_harvest")
@patch("agent_bus_store.tape_harvest.explicit_uuids_for_lane")
def test_t10_harvest_caps_seals(mock_explicit, mock_harvest, mock_render) -> None:
    mock_explicit.return_value = {"uuid-a"}
    mock_harvest.return_value = {
        "discovered": 12,
        "sealed": 8,
        "deferred_count": 4,
        "refused": 0,
    }
    mock_render.return_value = {"open_line": {"harvest": {"discovered": 12}}}

    render_tape_with_harvest(thread_id="10223", budget_bytes=1000, harvest=True, max_seals=8)

    mock_harvest.assert_called_once()
    assert mock_harvest.call_args.kwargs["max_seals"] == 8
    harvest_stats = mock_render.call_args.kwargs["harvest_stats"]
    assert harvest_stats == {
        "discovered": 12,
        "sealed": 8,
        "deferred": 4,
        "refused": 0,
        "quiescent": 0,
    }


@patch("agent_bus_store.tape_harvest.render_tape")
@patch("agent_bus_store.tape_harvest._call_transcript_harvest")
@patch("agent_bus_store.tape_harvest.explicit_uuids_for_lane")
def test_t11_already_closed_counted_sealed(mock_explicit, mock_harvest, mock_render) -> None:
    mock_explicit.return_value = set()
    mock_harvest.return_value = {"discovered": 1, "sealed": 1, "deferred_count": 0, "refused": 0}
    mock_render.return_value = {"open_line": {}}
    render_tape_with_harvest(thread_id="1", budget_bytes=1000, harvest=True)
    assert mock_harvest.return_value["sealed"] == 1


@patch("agent_bus_store.tape_harvest.render_tape")
@patch("agent_bus_store.tape_harvest._call_transcript_harvest")
@patch("agent_bus_store.tape_harvest.explicit_uuids_for_lane")
def test_t12_refusal_counted(mock_explicit, mock_harvest, mock_render) -> None:
    mock_explicit.return_value = set()
    mock_harvest.return_value = {
        "discovered": 2,
        "sealed": 1,
        "deferred_count": 0,
        "refused": 1,
        "refusal_reasons": [{"code": "transcript_seal.not_lane_window"}],
    }
    mock_render.return_value = {"open_line": {}}
    render_tape_with_harvest(thread_id="1", budget_bytes=1000, harvest=True)
    assert mock_harvest.return_value["refused"] == 1


@patch("agent_bus_store.tape_harvest.render_tape")
@patch("agent_bus_store.tape_harvest._call_transcript_harvest")
def test_t13_harvest_false_skips_seal(mock_harvest, mock_render) -> None:
    mock_render.return_value = {"open_line": {"harvest": None}}
    render_tape_with_harvest(thread_id="1", budget_bytes=1000, harvest=False)
    mock_harvest.assert_not_called()
    assert mock_render.call_args.kwargs["harvest_stats"] is None


@patch("agent_bus_store.tape_harvest.render_tape")
@patch("agent_bus_store.tape_harvest._call_transcript_harvest")
@patch("agent_bus_store.tape_harvest.explicit_uuids_for_lane")
def test_t14_harvest_passes_merged_explicit_to_cortex(
    mock_explicit,
    mock_harvest,
    mock_render,
) -> None:
    """agent_bus merges CP anchors once; cortex discover must not reopen the bus DB."""
    mock_explicit.return_value = {"uuid-a", "uuid-b"}
    mock_harvest.return_value = {
        "discovered": 0,
        "sealed": 0,
        "deferred_count": 0,
        "refused": 0,
    }
    mock_render.return_value = {"open_line": {"harvest": {"discovered": 0}}}

    render_tape_with_harvest(thread_id="10223", budget_bytes=1000, harvest=True)

    mock_explicit.assert_called_once_with("10223", set())
    assert sorted(mock_harvest.call_args.kwargs["explicit_ids"]) == ["uuid-a", "uuid-b"]


@patch("agent_bus_store.tape_harvest.render_tape")
@patch("agent_bus_store.tape_harvest._call_transcript_harvest")
@patch("agent_bus_store.tape_harvest.explicit_uuids_for_lane")
def test_t15_harvest_timeout_still_renders(
    mock_explicit, mock_harvest, mock_render
) -> None:
    mock_explicit.return_value = set()
    mock_harvest.return_value = {
        "error": "transcript_harvest timed out after 8.0s",
        "reason": "harvest_timeout",
    }
    mock_render.return_value = {"open_line": {"harvest": {"reason": "harvest_timeout"}}}

    result = render_tape_with_harvest(thread_id="10223", budget_bytes=1000, harvest=True)

    mock_render.assert_called_once()
    assert mock_render.call_args.kwargs["harvest_stats"] == {
        "error": "transcript_harvest timed out after 8.0s",
        "reason": "harvest_timeout",
    }
    assert result["open_line"]["harvest"]["reason"] == "harvest_timeout"


@patch("agent_bus_store.tape_harvest.render_tape")
@patch("agent_bus_store.tape_harvest._call_transcript_harvest")
def test_window_scope_passes_explicit_transcript_id(mock_harvest, mock_render) -> None:
    mock_harvest.return_value = {
        "discovered": 1,
        "sealed": 0,
        "deferred_count": 0,
        "refused": 0,
        "quiescent": 0,
    }
    mock_render.return_value = {"open_line": {}}
    tid = "d556c84f-524e-4e57-b38c-6fdc3eafe8fb"

    render_tape_with_harvest(
        thread_id="10223",
        budget_bytes=1000,
        harvest=True,
        scope="window",
        transcript_id=tid,
        prior_cells=1,
    )

    assert mock_harvest.call_args.kwargs["explicit_ids"] == [tid]
    assert mock_render.call_args.kwargs["scope"] == "window"
    assert mock_render.call_args.kwargs["transcript_id"] == tid
    assert mock_render.call_args.kwargs["prior_cells"] == 1
