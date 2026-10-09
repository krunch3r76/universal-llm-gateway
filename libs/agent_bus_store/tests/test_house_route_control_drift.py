"""LOG-ONLY D7/D8 drift observability (plan:fleet-roles-mvp slice d)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, create_turn, init_db
from agent_bus_store.house_route_control import (
    _reset_drift_counters_for_tests,
    drift_d7d8_mode,
    route_control_drift_summary,
)
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline

_GC1 = "grok-bot-gc1"
_MSK = "grok-bot-msk4"


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    monkeypatch.delenv("AGENT_BUS_ENVELOPE_ENFORCE", raising=False)
    monkeypatch.delenv("AGENT_BUS_RULING_GATE", raising=False)
    monkeypatch.delenv("AGENT_BUS_GC1_ACTING_MAESTRO", raising=False)
    monkeypatch.setenv("AGENT_BUS_DRIFT_D7D8", "log-only")
    init_db()
    app = create_app(db_path=str(db_path))
    app.dependency_overrides[require_token] = lambda: None
    _reset_drift_counters_for_tests()
    yield TestClient(app)
    app.dependency_overrides.clear()


def _sidekick_thread() -> str:
    row = create_thread(
        thread_id=None,
        slug="msk-drift",
        tags=["sidekick:listen"],
    )
    return row["id"]


def _control_request(
    thread_id: str,
    *,
    from_agent: str = _GC1,
    to_agent: str = _MSK,
    subject: str = "WAKE|plan:drift-test",
) -> None:
    create_turn(
        thread_id=thread_id,
        from_agent=from_agent,
        to_agent=to_agent,
        subject=subject,
        body="wake\n",
    )


@patch.dict(os.environ, {"AGENT_BUS_ENVELOPE_ENFORCE": "off"}, clear=False)
@patch("agent_bus_store.house_route_control._emit")
def test_d7_seeded_bypass_turn_increments_bypass(emit_mock, bus_db: TestClient) -> None:
    thread_id = _sidekick_thread()
    resp = bus_db.post(
        "/threads/send",
        json={
            "thread": thread_id,
            "from": _GC1,
            "to": _MSK,
            "subject": "ACK|plan:drift-test",
            "body": "missing envelope\n",
        },
    )
    assert resp.status_code == 201, resp.text
    summary = route_control_drift_summary()
    assert summary["d7_control_total"] == 1
    assert summary["d7_bypass_count"] == 1
    assert summary["d7_bypass_rate"] == 1.0
    signals = [call[0][0] for call in emit_mock.call_args_list]
    assert "agent_bus.route_control.drift_d7_bypass" in signals


@patch("agent_bus_store.house_route_control._emit")
def test_d8_seeded_misreply_increments(emit_mock, bus_db: TestClient) -> None:
    thread_id = _sidekick_thread()
    _control_request(thread_id)
    resp = bus_db.post(
        "/threads/send",
        json={
            "thread": thread_id,
            "from": _MSK,
            "to": "cursor",
            "subject": "ACK|plan:drift-test",
            "body": (
                "authority: operator\n"
                "work_key: plan:drift-test\n"
                "read_shas: brief deadbeef\n"
            ),
        },
    )
    assert resp.status_code == 201, resp.text
    assert route_control_drift_summary()["d8_misreply_count"] == 1
    signals = [call[0][0] for call in emit_mock.call_args_list]
    assert "agent_bus.route_control.drift_d8_reply_path" in signals


@patch("agent_bus_store.house_route_control._emit")
def test_d8_correct_reply_not_counted(emit_mock, bus_db: TestClient) -> None:
    thread_id = _sidekick_thread()
    _control_request(thread_id)
    resp = bus_db.post(
        "/threads/send",
        json={
            "thread": thread_id,
            "from": _MSK,
            "to": _GC1,
            "subject": "ACK|plan:drift-test",
            "body": (
                "authority: operator\n"
                "work_key: plan:drift-test\n"
                "read_shas: brief deadbeef\n"
            ),
        },
    )
    assert resp.status_code == 201, resp.text
    assert route_control_drift_summary()["d8_misreply_count"] == 0
    signals = [call[0][0] for call in emit_mock.call_args_list]
    assert "agent_bus.route_control.drift_d8_reply_path" not in signals


@patch("agent_bus_store.house_route_control._emit")
def test_falsifier_misreply_must_increment_counter(emit_mock) -> None:
    """Seeded mis-reply that is not counted must fail this test."""
    _reset_drift_counters_for_tests()
    prior = {
        "from_agent": _GC1,
        "to_agent": _MSK,
        "subject": "WAKE|plan:drift-test",
        "body": "wake\n",
        "turn_number": 1,
    }
    from agent_bus_store.house_route_control import observe_drift_d8_reply_path

    observe_drift_d8_reply_path(
        thread_id="999",
        from_agent=_MSK,
        to_agent="cursor",
        tags=["sidekick:listen"],
        prior_turn=prior,
    )
    assert route_control_drift_summary()["d8_misreply_count"] == 1
    emit_mock.assert_called()


def test_drift_d7d8_default_log_only() -> None:
    env = os.environ.pop("AGENT_BUS_DRIFT_D7D8", None)
    try:
        assert drift_d7d8_mode() == "log-only"
    finally:
        if env is not None:
            os.environ["AGENT_BUS_DRIFT_D7D8"] = env
