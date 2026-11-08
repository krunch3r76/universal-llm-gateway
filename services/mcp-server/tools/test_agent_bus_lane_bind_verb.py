"""Request-surface ``lane_bind`` / ``lane_current`` verbs — V7 envelope."""

from __future__ import annotations

from unittest.mock import patch

from contract_vocab import CANONICAL_CONTRACTS

from tools.agent_bus.lane_associations import (
    _lane_bind_dispatch,
    _lane_current_dispatch,
)
from tools.agent_bus.send import _send_impl


def test_lane_bind_not_in_canonical_contracts() -> None:
    assert "lane_bind" not in CANONICAL_CONTRACTS
    assert "lane_current" not in CANONICAL_CONTRACTS


def test_lane_bind_rejects_missing_thread() -> None:
    result = _lane_bind_dispatch(parent_thread="7182", lane_role="sub_mission")
    assert result["reason"] == "lane_bind_thread_required"


def test_lane_bind_rejects_incomplete_parent_or_role() -> None:
    result = _lane_bind_dispatch(thread="99", lane_role="sub_mission")
    assert result["reason"] == "lane_bind_incomplete"


def test_lane_bind_rejects_client_ordering_tokens() -> None:
    result = _lane_bind_dispatch(
        thread="99",
        parent_thread="7182",
        lane_role="sub_mission",
        id=1,
    )
    assert result["reason"] == "client_ordering_token"


def test_lane_bind_relays_post() -> None:
    captured: dict[str, object] = {}

    def fake_relay(service, method, path, **kwargs):
        captured["service"] = service
        captured["method"] = method
        captured["path"] = path
        captured["body"] = kwargs.get("body")
        return {
            "thread_id": "99",
            "parent_thread_id": "7182",
            "lane_role": "sub_mission",
            "id": 7,
            "state": "associated",
        }

    with (
        patch("tools.agent_bus.lane_associations.relay", side_effect=fake_relay),
        patch("tools.agent_bus.lane_associations.record"),
    ):
        result = _lane_bind_dispatch(
            thread=99,
            parent_thread="7182",
            lane_role="sub_mission",
            bound_by="cursor",
        )
    assert captured["method"] == "POST"
    assert captured["path"] == "/threads/99/lane-bind"
    assert captured["body"]["parent_thread_id"] == "7182"
    assert captured["body"]["lane_role"] == "sub_mission"
    assert result["id"] == 7
    assert result["state"] == "associated"


def test_lane_current_relays_get() -> None:
    with (
        patch(
            "tools.agent_bus.lane_associations.relay",
            return_value={
                "thread_id": "99",
                "parent_thread": "7182",
                "lane_role": "sub_mission",
                "association_id": 7,
                "state": "associated",
            },
        ),
        patch("tools.agent_bus.lane_associations.record"),
    ):
        result = _lane_current_dispatch(thread_id="99")
    assert result["parent_thread"] == "7182"
    assert result["state"] == "associated"


def test_lane_current_rejects_missing_thread() -> None:
    result = _lane_current_dispatch()
    assert result["reason"] == "lane_current_thread_required"


def test_send_impl_forwards_lane_fields() -> None:
    captured: dict[str, object] = {}

    def fake_relay(service, method, path, **kwargs):
        captured["body"] = kwargs.get("body")
        return {
            "send_path": "new",
            "thread": {"id": "99"},
            "turn": {"turn_number": 1},
        }

    with (
        patch("tools.agent_bus.send.relay", side_effect=fake_relay),
        patch("tools.agent_bus.send.record"),
        patch(
            "claude_bundles.mission_close_debrief_auto.attach_mission_debrief_notify",
            side_effect=lambda result, **_: result,
        ),
    ):
        _send_impl(
            new_slug="v7-probe",
            thread=None,
            to="cursor",
            subject="s",
            body="b",
            from_agent="cursor",
            summary=None,
            tags=None,
            lifecycle_state=None,
            after_turn=0,
            status="open",
            mark_read=False,
            close=False,
            attachments=None,
            allow_long_body=False,
            parent_thread="7182",
            lane_role="parallel",
        )
    assert captured["body"]["parent_thread"] == "7182"
    assert captured["body"]["lane_role"] == "parallel"
