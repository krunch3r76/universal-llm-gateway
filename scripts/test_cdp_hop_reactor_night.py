"""Unit tests for CDP hop reactor wait helpers and state."""

from __future__ import annotations

import json

from cdp_hop_reactor_night import ReactorState
from cdp_hop_reactor_wait import (
    active_row_absent_streak,
    build_harvest_request,
    harvest_miss_outcome,
    is_cdp_external_gate_live,
    terminal,
)


def test_build_harvest_request_chat_url_only():
    req = build_harvest_request(
        chat_url="https://claude.ai/c/x",
        satellite_execution_id="sat-uuid",
        stargate_execution_id="stg-uuid",
    )
    assert req == {"chat_url": "https://claude.ai/c/x"}
    assert "execution_id" not in req


def test_build_harvest_request_never_chat_url_with_stargate():
    req = build_harvest_request(
        chat_url="https://claude.ai/c/x",
        satellite_execution_id=None,
        stargate_execution_id="stg-uuid",
    )
    assert "execution_id" not in req


def test_build_harvest_request_satellite_when_no_chat_url():
    req = build_harvest_request(
        chat_url=None,
        satellite_execution_id="sat-uuid",
        stargate_execution_id="stg-uuid",
    )
    assert req["execution_id"] == "sat-uuid"


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
