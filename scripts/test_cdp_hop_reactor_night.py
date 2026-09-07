"""Unit tests for CDP hop reactor wait helpers and state."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cdp_hop_reactor_night import ReactorState
from cdp_hop_reactor_wait import (
    ROW_HOLD_MAX_POLLS,
    SEATED_NO_STREAM_EXECUTION,
    active_row_absent_streak,
    adoptable_lane_rows,
    build_harvest_request,
    harvest_miss_outcome,
    harvest_settled,
    idle_step,
    is_cdp_external_gate_live,
    is_http_error_envelope,
    lane_stream_rows,
    merge_active_work_rows,
    should_drop_satellite_id,
    terminal,
)


def test_build_harvest_request_chat_url_only():
    req = build_harvest_request(
        chat_url="https://claude.ai/c/x",
        registration_id="reg-1",
        satellite_execution_id="sat-uuid",
        stargate_execution_id="stg-uuid",
    )
    assert req == {"chat_url": "https://claude.ai/c/x"}
    assert "execution_id" not in req


def test_build_harvest_request_registration_when_no_chat_url():
    req = build_harvest_request(
        chat_url=None,
        registration_id="reg-1",
        satellite_execution_id="sat-uuid",
        stargate_execution_id="stg-uuid",
    )
    assert req == {"registration_id": "reg-1"}


def test_build_harvest_request_never_chat_url_with_stargate():
    req = build_harvest_request(
        chat_url="https://claude.ai/c/x",
        registration_id=None,
        satellite_execution_id=None,
        stargate_execution_id="stg-uuid",
    )
    assert "execution_id" not in req


def test_build_harvest_request_satellite_when_no_chat_url():
    req = build_harvest_request(
        chat_url=None,
        registration_id=None,
        satellite_execution_id="sat-uuid",
        stargate_execution_id="stg-uuid",
    )
    assert req["execution_id"] == "sat-uuid"


def test_merge_active_work_rows_includes_seated_rows():
    data = {
        "rows": [],
        "seated_rows": [
            {
                "parent_thread": "10196",
                "execution_id": "exec-1",
                "registration_id": "reg-1",
                "status": "running",
            }
        ],
    }
    merged = merge_active_work_rows(data)
    assert len(merged) == 1
    assert merged[0]["registration_id"] == "reg-1"


def test_is_cdp_external_gate_live():
    env = {"body": {"error": {"code": "cdp_external_gate_live", "message": "busy"}}}
    assert is_cdp_external_gate_live(env) is True


def test_active_row_absent_streak_reads_rows():
    rows = [{"parent_thread": "10188", "status": "running", "execution_id": "x"}]
    assert active_row_absent_streak(rows, "10188") is False
    assert active_row_absent_streak(rows, "9999") is True
    assert active_row_absent_streak([], "10188") is True


def test_harvest_miss_outcome():
    assert harvest_miss_outcome("not_attached") is True
    assert harvest_miss_outcome("no_reply_yet") is False
    assert harvest_miss_outcome("harvested", turns=[]) is False


def test_terminal_counter():
    assert terminal("not_attached", False, False, False, 4) is False
    assert terminal("not_attached", False, False, False, 5) is True


def test_should_drop_satellite_id_not_attached():
    assert should_drop_satellite_id({"outcome": "not_attached"}) is True
    assert should_drop_satellite_id({"outcome": "dormant"}) is True
    assert should_drop_satellite_id({"outcome": "harvested", "provenance": {}}) is False
    assert should_drop_satellite_id({"outcome": "harvested", "provenance": {"registration_id": "r1"}}) is False


def test_is_http_error_envelope():
    assert is_http_error_envelope({"status": 404, "body": {}, "code": "x"}) is True
    assert is_http_error_envelope({"status": "running", "execution_id": "e1"}) is False
    assert is_http_error_envelope({"op": "generate", "status": "running"}) is False


def test_terminal_all_harvest_outcomes_bounded():
    outcomes = [
        "harvested",
        "no_reply_yet",
        "streaming",
        "incomplete_dom",
        "unauthenticated",
        "not_attached",
        "dormant",
        "conflict",
        "unreachable",
        "refused",
    ]
    for outcome in outcomes:
        streaming = outcome == "streaming"
        if streaming:
            assert terminal(outcome, False, True, False, 2) is False
            continue
        miss_streak = 5 if outcome in {"not_attached", "conflict", "unreachable", "refused"} else 2
        assert terminal(outcome, False, False, False, miss_streak, idle_confirmations=2) is True


def test_fire_fable_accepts_202_running_body():
    """D1: success dispatch body with status='running' must not be treated as HTTP error."""
    body = {"op": "generate", "status": "running", "execution_id": "exec-1"}
    assert is_http_error_envelope(body) is False


def test_reactor_state_drops_fable_execution_id():
    raw = json.loads(
        '{"fable_episode": 1, "fable_execution_id": "old", "fable_chat_url": "https://x", "fable_thread": "10188"}'
    )
    state = ReactorState.from_json(raw)
    assert not hasattr(state, "fable_execution_id")
    assert state.fable_chat_url == "https://x"
    data = state.to_json()
    assert "fable_execution_id" not in data
    assert "fable_stargate_execution_id" in data


def test_lane_liveness_ignores_seated_rows():
    data = {
        "rows": [],
        "seated_rows": [
            {
                "parent_thread": "10196",
                "status": "running",
                "purpose": "review",
                "registration_id": "reg-review",
            }
        ],
    }
    assert lane_stream_rows(data, "10196") == []


def test_lane_liveness_reads_execution_store_rows():
    data = {
        "rows": [
            {
                "parent_thread": "10196",
                "status": "running",
                "purpose": "mission",
                "execution_id": "exec-mission",
            }
        ],
    }
    assert len(lane_stream_rows(data, "10196")) == 1


def test_lane_liveness_ignores_non_mission_purpose_row():
    data = {
        "rows": [
            {
                "parent_thread": "10196",
                "status": "running",
                "purpose": "review",
                "execution_id": "exec-review",
            }
        ],
    }
    assert lane_stream_rows(data, "10196") == []


def test_lane_liveness_ignores_seated_no_stream_sentinel():
    data = {
        "rows": [
            {
                "parent_thread": "10196",
                "status": "running",
                "purpose": "mission",
                "execution_id": SEATED_NO_STREAM_EXECUTION,
            }
        ],
    }
    assert lane_stream_rows(data, "10196") == []


def test_idle_step_settled_harvest_beats_live_row():
    harvest = {"outcome": "harvested", "turns": [{"text": "x"}], "streaming": False}
    lane_rows = [{"parent_thread": "10196", "status": "running", "purpose": "mission"}]
    idle, hold, source = idle_step(
        harvest=harvest,
        prev_cursor=0,
        lane_rows=lane_rows,
        idle_streak=0,
        row_hold_streak=0,
    )
    assert idle == 1
    assert source == "authority_settled"


def test_idle_step_tool_pause_does_not_reset_on_live_row():
    harvest = {"outcome": "harvested", "turns": [], "tool_pause": True}
    lane_rows = [{"parent_thread": "10196", "status": "running", "purpose": "mission"}]
    idle, _, source = idle_step(
        harvest=harvest,
        prev_cursor=0,
        lane_rows=lane_rows,
        idle_streak=1,
        row_hold_streak=0,
    )
    assert idle == 2
    assert source == "tool_pause"
    assert terminal("harvested", False, False, True, 2, idle_confirmations=2) is True


def test_idle_step_row_hold_is_bounded():
    harvest = {"outcome": "no_reply_yet", "turns": []}
    lane_rows = [{"parent_thread": "10196", "status": "running", "purpose": "mission"}]
    idle = 0
    hold = 0
    for _ in range(ROW_HOLD_MAX_POLLS):
        idle, hold, source = idle_step(
            harvest=harvest,
            prev_cursor=0,
            lane_rows=lane_rows,
            idle_streak=idle,
            row_hold_streak=hold,
        )
        assert source == "projection_hold"
    idle, hold, source = idle_step(
        harvest=harvest,
        prev_cursor=0,
        lane_rows=lane_rows,
        idle_streak=idle,
        row_hold_streak=hold,
    )
    assert source == "idle"
    assert idle == 1


def test_wedge_regression_thread_10196():
    data = {
        "rows": [],
        "seated_rows": [
            {
                "parent_thread": "10196",
                "status": "running",
                "purpose": "review",
                "registration_id": "95819039",
            }
        ],
    }
    assert lane_stream_rows(data, "10196") == []
    harvest = {
        "outcome": "harvested",
        "turns": [{"text": "review output"}],
        "streaming": False,
        "cursor": 0,
    }
    idle = 0
    hold = 0
    for _ in range(3):
        idle, hold, source = idle_step(
            harvest=harvest,
            prev_cursor=0,
            lane_rows=lane_stream_rows(data, "10196"),
            idle_streak=idle,
            row_hold_streak=hold,
        )
    assert source == "authority_settled"
    assert terminal("harvested", False, False, False, idle, idle_confirmations=2) is True


def test_adopt_refuses_non_mission_purpose_seated_row():
    data = {
        "seated_rows": [
            {
                "parent_thread": "10196",
                "purpose": "review",
                "registration_id": "reg-1",
                "execution_id": "exec-1",
            }
        ],
    }
    rows, disposition = adoptable_lane_rows(data, "10196")
    assert disposition == "none"
    assert rows == []


def test_adopt_refuses_when_two_mission_rows_same_parent_thread():
    data = {
        "rows": [
            {"parent_thread": "10196", "purpose": "mission", "registration_id": "a"},
            {"parent_thread": "10196", "purpose": "mission", "registration_id": "b"},
        ],
    }
    rows, disposition = adoptable_lane_rows(data, "10196")
    assert disposition == "ambiguous"
    assert len(rows) == 2


def test_adopt_binds_pinned_registration_when_state_already_seated():
    data = {
        "rows": [
            {"parent_thread": "10196", "purpose": "mission", "registration_id": "keep"},
            {"parent_thread": "10196", "purpose": "mission", "registration_id": "other"},
        ],
    }
    rows, disposition = adoptable_lane_rows(data, "10196", known_registration_id="keep")
    assert disposition == "bind"
    assert rows[0]["registration_id"] == "keep"


def test_adopt_binds_purposeless_row_on_gate_evidence_path():
    data = {"seated_rows": [{"parent_thread": "10196", "registration_id": "reg-1", "execution_id": "exec-1"}]}
    rows, disposition = adoptable_lane_rows(data, "10196", gate_evidence=True)
    assert disposition == "bind"
    assert rows[0]["registration_id"] == "reg-1"


def test_adopt_refuses_foreign_purpose_even_with_gate_evidence():
    data = {
        "seated_rows": [
            {"parent_thread": "10196", "purpose": "review", "registration_id": "reg-1"},
        ],
    }
    rows, disposition = adoptable_lane_rows(data, "10196", gate_evidence=True)
    assert disposition == "none"
    assert rows == []


def test_harvest_settled():
    assert harvest_settled({"outcome": "harvested", "turns": [{"t": 1}]}) is True
    assert harvest_settled({"outcome": "harvested", "turns": [], "streaming": True}) is False
