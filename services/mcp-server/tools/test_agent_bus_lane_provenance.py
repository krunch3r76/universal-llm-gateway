"""Gate A observation tests for agent-bus lane birth provenance.

Gate A only observes: a lane born without a complete parent association is
recorded, never refused. These tests pin what the signal counts, because the
decision to flip Gate B to 422 rests on the observed volume being zero.
"""

from __future__ import annotations

from unittest.mock import patch

from tools.agent_bus.lane_provenance import observe_unparented_birth


def test_unparented_birth_is_recorded() -> None:
    with patch("tools.agent_bus.lane_provenance.record") as rec:
        observe_unparented_birth(
            new_slug="mission-lane",
            parent_thread=None,
            lane_role=None,
            request_id="req-1",
        )

    rec.assert_called_once()
    assert rec.call_args.args[0] == "mcp.agentbus.request.lane_unparented"
    assert rec.call_args.kwargs["new_slug"] == "mission-lane"
    assert rec.call_args.kwargs["parent_thread"] == ""
    assert rec.call_args.kwargs["request_id"] == "req-1"


def test_fully_parented_birth_is_silent() -> None:
    with patch("tools.agent_bus.lane_provenance.record") as rec:
        observe_unparented_birth(
            new_slug="mission-lane",
            parent_thread="7186",
            lane_role="operator_proxy",
            request_id="req-1",
        )

    rec.assert_not_called()


def test_half_bound_lane_is_recorded() -> None:
    """``parent_thread`` and ``lane_role`` are both-or-neither; half is a defect."""
    with patch("tools.agent_bus.lane_provenance.record") as rec:
        observe_unparented_birth(
            new_slug="mission-lane",
            parent_thread="7186",
            lane_role=None,
            request_id=None,
        )

    rec.assert_called_once()
    assert rec.call_args.kwargs["parent_thread"] == "7186"
    assert rec.call_args.kwargs["lane_role"] == ""


def test_turn_on_existing_thread_is_not_a_birth() -> None:
    """No ``new_slug`` means no lane was born — nothing to observe."""
    with patch("tools.agent_bus.lane_provenance.record") as rec:
        observe_unparented_birth(
            new_slug=None,
            parent_thread=None,
            lane_role=None,
            request_id="req-1",
        )

    rec.assert_not_called()
