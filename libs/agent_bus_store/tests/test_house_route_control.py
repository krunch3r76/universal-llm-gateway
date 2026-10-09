"""ROUTE/CONTROL envelope enforce + gc1 ruling gate (plan:fleet-roles-mvp a+c)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, init_db
from agent_bus_store.house_route_control import (
    _LIFECYCLE_KINDS,
    envelope_enforce_mode,
    gc1_acting_maestro_fact_set,
    ruling_gate_mode,
)
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline

_GC1 = "grok-bot-gc1"


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    monkeypatch.delenv("AGENT_BUS_ENVELOPE_ENFORCE", raising=False)
    monkeypatch.delenv("AGENT_BUS_RULING_GATE", raising=False)
    monkeypatch.delenv("AGENT_BUS_GC1_ACTING_MAESTRO", raising=False)
    init_db()
    app = create_app(db_path=str(db_path))
    app.dependency_overrides[require_token] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def _sidekick_thread() -> str:
    row = create_thread(
        thread_id=None,
        slug="msk-test",
        tags=["sidekick:listen"],
    )
    return row["id"]


def _envelope_body(*, authority: str = "operator", work_key: str = "plan:test") -> str:
    return (
        f"authority: {authority}\n"
        f"work_key: {work_key}\n"
        "read_shas: brief deadbeef\n"
        "notes follow\n"
    )


def _send(
    client: TestClient,
    thread_id: str,
    *,
    from_agent: str,
    subject: str,
    body: str,
    to: str = "grok-bot-msk4",
) -> int:
    resp = client.post(
        "/threads/send",
        json={
            "thread": thread_id,
            "from": from_agent,
            "to": to,
            "subject": subject,
            "body": body,
        },
    )
    return resp.status_code


def test_switch_defaults() -> None:
    assert envelope_enforce_mode() == "off"
    assert ruling_gate_mode() == "bypass"
    assert gc1_acting_maestro_fact_set() is False


@patch.dict(os.environ, {"AGENT_BUS_ENVELOPE_ENFORCE": "off"}, clear=False)
@patch("agent_bus_store.house_route_control._emit")
def test_envelope_enforce_off_logs_malformed(
    emit_mock, bus_db: TestClient
) -> None:
    thread_id = _sidekick_thread()
    status = _send(
        bus_db,
        thread_id,
        from_agent=_GC1,
        subject="ACK|plan:test",
        body="missing envelope fields\n",
    )
    assert status == 201
    signals = [call[0][0] for call in emit_mock.call_args_list]
    assert "agent_bus.route_control.envelope_malformed" in signals


@patch.dict(os.environ, {"AGENT_BUS_ENVELOPE_ENFORCE": "on"}, clear=False)
def test_envelope_enforce_on_rejects_malformed(bus_db: TestClient) -> None:
    thread_id = _sidekick_thread()
    resp = bus_db.post(
        "/threads/send",
        json={
            "thread": thread_id,
            "from": _GC1,
            "to": "grok-bot-msk4",
            "subject": "ACK|plan:test",
            "body": "no authority line\n",
        },
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["reason"] == "REJECT|MALFORMED"
    assert "authority" in detail["missing_fields"]


@patch.dict(os.environ, {"AGENT_BUS_RULING_GATE": "bypass"}, clear=False)
def test_ruling_gate_bypass_gc1_ack_without_fact(bus_db: TestClient) -> None:
    thread_id = _sidekick_thread()
    body = _envelope_body(authority="operator")
    assert (
        _send(
            bus_db,
            thread_id,
            from_agent=_GC1,
            subject="ACK|plan:test",
            body=body,
        )
        == 201
    )


@patch.dict(os.environ, {"AGENT_BUS_RULING_GATE": "on"}, clear=False)
def test_gc1_wake_accepted_with_gate_on(bus_db: TestClient) -> None:
    thread_id = _sidekick_thread()
    assert (
        _send(
            bus_db,
            thread_id,
            from_agent=_GC1,
            subject="WAKE|plan:test",
            body="wake house\n",
        )
        == 201
    )


@patch.dict(os.environ, {"AGENT_BUS_RULING_GATE": "bypass"}, clear=False)
def test_gc1_wake_accepted_with_gate_bypass(bus_db: TestClient) -> None:
    thread_id = _sidekick_thread()
    assert (
        _send(
            bus_db,
            thread_id,
            from_agent=_GC1,
            subject="WAKE|plan:test",
            body="wake\n",
        )
        == 201
    )


@patch.dict(
    os.environ,
    {"AGENT_BUS_RULING_GATE": "on", "AGENT_BUS_GC1_ACTING_MAESTRO": "1"},
    clear=False,
)
def test_ruling_gate_on_with_acting_maestro_fact_gc1_ack_accepted(
    bus_db: TestClient,
) -> None:
    thread_id = _sidekick_thread()
    body = _envelope_body(authority="operator")
    assert (
        _send(
            bus_db,
            thread_id,
            from_agent=_GC1,
            subject="ACK|plan:test",
            body=body,
        )
        == 201
    )


@patch.dict(os.environ, {"AGENT_BUS_RULING_GATE": "on"}, clear=False)
def test_ruling_gate_on_fact_cleared_gc1_ack_refused(bus_db: TestClient) -> None:
    thread_id = _sidekick_thread()
    body = _envelope_body(authority="operator")
    resp = bus_db.post(
        "/threads/send",
        json={
            "thread": thread_id,
            "from": _GC1,
            "to": "grok-bot-msk4",
            "subject": "ACK|plan:test",
            "body": body,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "route_control.gc1_ruling_refused"


@patch.dict(os.environ, {"AGENT_BUS_RULING_GATE": "on"}, clear=False)
def test_falsifier_gc1_ack_gate_on_no_fact_refused(bus_db: TestClient) -> None:
    """House must not execute gc1 ACK when gate on and no #49 fact."""
    thread_id = _sidekick_thread()
    body = _envelope_body(authority="house")
    resp = bus_db.post(
        "/threads/send",
        json={
            "thread": thread_id,
            "from": _GC1,
            "to": "grok-bot-msk4",
            "subject": "ACK|plan:fleet-roles-mvp",
            "body": body,
        },
    )
    assert resp.status_code == 422


@patch.dict(
    os.environ,
    {
        "AGENT_BUS_ENVELOPE_ENFORCE": "on",
        "AGENT_BUS_RULING_GATE": "on",
        "AGENT_BUS_GC1_ACTING_MAESTRO": "1",
    },
    clear=False,
)
def test_envelope_enforce_does_not_reject_gc1_operator_ack_with_fact(
    bus_db: TestClient,
) -> None:
    thread_id = _sidekick_thread()
    body = _envelope_body(authority="operator", work_key="plan:fleet-roles-mvp")
    resp = bus_db.post(
        "/threads/send",
        json={
            "thread": thread_id,
            "from": _GC1,
            "to": "grok-bot-msk4",
            "subject": "ACK|plan:fleet-roles-mvp",
            "body": body,
        },
    )
    assert resp.status_code == 201, resp.text


@patch.dict(os.environ, {"AGENT_BUS_RULING_GATE": "on"}, clear=False)
def test_lifecycle_refused_when_not_from_gc1(bus_db: TestClient) -> None:
    thread_id = _sidekick_thread()
    resp = bus_db.post(
        "/threads/send",
        json={
            "thread": thread_id,
            "from": "grok-bot-msk4",
            "to": _GC1,
            "subject": "WAKE|plan:test",
            "body": "wake\n",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "route_control.lifecycle_from_gc1_only"


def test_lifecycle_kinds_frozen() -> None:
    assert "WAKE" in _LIFECYCLE_KINDS
