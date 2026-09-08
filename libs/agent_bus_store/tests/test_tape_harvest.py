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
