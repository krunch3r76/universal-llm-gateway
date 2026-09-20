"""Drain semantics: CSE holder occupancy never blocks drain."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.app import create_app
from services.git_integration_worker.cse_session_holders import (
    ensure_schema,
    upsert_holder,
)
from services.git_integration_worker.cursor_auto.queue import reset_queue_for_tests
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger

_CSE_URL = "https://claude.ai/cowork/cse_drain1"


@pytest.fixture(autouse=True)
def _reset_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    reset_queue_for_tests(durable=False)
    yield
    CursorDispatchLedger._instance = None
    reset_queue_for_tests(durable=False)


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    completed: list[dict] = []
    from services.git_integration_worker import git_worker_drain_events as drain_events

    monkeypatch.setattr(
        drain_events, "emit_drain_completed", lambda **k: completed.append(k)
    )
    return SimpleNamespace(completed=completed)


def test_holder_only_occupancy_does_not_block_drain(
    events: SimpleNamespace,
) -> None:
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(conn, chat_url=_CSE_URL, lane_thread_id="11667")
        conn.commit()
    controller = WorkAdmissionController(
        ledger=ledger,
        worker_id="test",
        pid=1,
        worker_started_at="2026-01-01T00:00:00+00:00",
    )
    assert controller.active_ops() == []
    controller.begin_drain(
        reason="test",
        intent_id="intent-1",
        drain_epoch=1,
    )
    controller._maybe_emit_drain_completed()
    assert events.completed, "drain.completed expected with holder-only occupancy"
    op_kinds = {op.get("kind") for op in controller.active_ops()}
    assert "cowork_cse" not in op_kinds


def test_active_work_exposes_cse_holder_sibling() -> None:
    app = create_app()
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(conn, chat_url=_CSE_URL, lane_thread_id="11667")
        conn.commit()
    with TestClient(app) as tc:
        resp = tc.get("/api/v1/git/active-work")
    assert resp.status_code == 200
    data = resp.json()
    holders = data.get("cse_holders") or []
    assert any(h.get("holder_id") == "cse_drain1" for h in holders)
    assert data["write_lease"].get("holder_dispatch_id") is None
    assert not any(
        op.get("kind") == "cowork_cse" for op in data.get("active_ops") or []
    )
