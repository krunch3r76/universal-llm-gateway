"""Tests for hub-side lane lineage relay reader."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tools.agent_bus.cse_lineage_reader import (
    LaneLineageUnreachable,
    read_lane_lineage,
)


def test_relay_reader_returns_associated_lineage() -> None:
    with patch(
        "tools.agent_bus.lane_associations._lane_current_impl",
        return_value={
            "thread_id": "99",
            "parent_thread": "42",
            "lane_role": "side",
            "association_id": 3,
            "state": "associated",
        },
    ):
        result = read_lane_lineage("99")
    assert result is not None
    assert result["association_id"] == 3
    assert result["parent_thread"] == "42"
    assert result["lineage_observed_at"] is not None


def test_relay_reader_none_when_unbound() -> None:
    with patch(
        "tools.agent_bus.lane_associations._lane_current_impl",
        return_value={"thread_id": "99", "state": "none"},
    ):
        assert read_lane_lineage("99") is None


def test_relay_reader_raises_on_unreachable_errors() -> None:
    with patch(
        "tools.agent_bus.lane_associations._lane_current_impl",
        return_value={"error": "agent-bus down"},
    ):
        with pytest.raises(LaneLineageUnreachable):
            read_lane_lineage("99")


def test_relay_reader_wraps_relay_exceptions() -> None:
    with patch(
        "tools.agent_bus.lane_associations._lane_current_impl",
        side_effect=RuntimeError("transport reset"),
    ):
        with pytest.raises(LaneLineageUnreachable, match="transport reset"):
            read_lane_lineage("99")
