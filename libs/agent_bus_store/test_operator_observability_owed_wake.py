"""Owed_wake hooks on send, bulk mark-read, and manage notice exclusion."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.operator_observability_hooks import after_turn_created
from scripts.model_manager.ui.controller.restart_intent_store import (
    RestartIntentStore,
)


def _bus_app(tmp_path):
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return app


def _bind_store(tmp_path, monkeypatch: pytest.MonkeyPatch) -> RestartIntentStore:
    store = RestartIntentStore(db_path=tmp_path / "restart-intents.db")
    monkeypatch.setattr(
        RestartIntentStore,
        "instance",
        classmethod(lambda _cls: store),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.resolve_maestro_holder_id_for_lane",
        lambda _lane: "holder-maestro",
    )
    return store


def test_send_route_milestone_to_maestro_records_owed_wake_row(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.model_manager.ui.controller.tests.test_owed_wake_ledger import (
        _deps,
    )

    store = _bind_store(tmp_path, monkeypatch)
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "off")
    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.build_owed_wake_deps",
        lambda: _deps(),
    )
    with TestClient(_bus_app(tmp_path)) as client:
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": "owed-send-milestone",
                "from": "cursor",
                "to": "maestro",
                "subject": "bootstrap",
                "body": "seed",
            },
        )
        assert create.status_code == 201, create.text
        thread_id = create.json()["thread"]["id"]
        resp = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "maestro",
                "subject": "MILESTONE|decision:operator-observability-ledger|via-send",
                "body": "house milestone via send path",
            },
        )
        assert resp.status_code == 201, resp.text
    open_rows = store.list_open_undispatched_owed_wakes()
    assert len(open_rows) == 1
    assert open_rows[0].holder_id == "holder-maestro"


def test_second_drain_coalesce_does_not_add_open_row(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.model_manager.ui.controller.owed_wake_ledger import (
        producer2_on_restart_terminal,
    )
    from scripts.model_manager.ui.controller.restart_intent_store import (
        STATUS_COMPLETED,
        STATUS_DRAINED_RESTARTING,
    )
    from scripts.model_manager.ui.controller.tests.test_owed_wake_ledger import (
        _deps,
    )

    store = _bind_store(tmp_path, monkeypatch)
    deps = _deps(post_ok=True, endpoint_ok=True)
    for suffix in ("a", "b"):
        intent = store.create_intent(
            service="git_integration_worker",
            action="restart",
            deadline_at="d",
            reason=f"r-{suffix}",
            caller_thread_id="18460",
        )
        store.advance_if_status(
            intent.intent_id,
            from_status="pending_drain",
            to_status=STATUS_DRAINED_RESTARTING,
            reason="drained",
        )
        store.advance_if_status(
            intent.intent_id,
            from_status=STATUS_DRAINED_RESTARTING,
            to_status=STATUS_COMPLETED,
            reason="done",
        )
        completed = store.get(intent.intent_id)
        assert completed is not None
        producer2_on_restart_terminal(
            store, completed, holder_id="holder-maestro", deps=deps
        )
    with store._connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM owed_wake "
            "WHERE holder_id=? AND closed_at IS NULL",
            ("holder-maestro",),
        ).fetchone()["c"]
    assert n == 1


def test_bulk_mark_read_closes_open_owed_wake_row(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _bind_store(tmp_path, monkeypatch)
    with TestClient(_bus_app(tmp_path)) as client:
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": "owed-bulk-read",
                "from": "cursor",
                "to": "web",
                "subject": "seed",
                "body": "body",
            },
        )
        thread_id = create.json()["thread"]["id"]
        monkeypatch.setattr(
            "services.git_integration_worker.cse_session_holders.resolve_maestro_holder_id_for_lane",
            lambda lane: "holder-maestro" if lane == thread_id else None,
        )
        store.coalesce_owed_wake_milestone(
            holder_id="holder-maestro",
            lane_thread_id=thread_id,
            turn_ref={
                "thread_id": thread_id,
                "turn_number": 1,
                "subject": "MILESTONE|x",
            },
        )
        resp = client.patch(
            f"/threads/{thread_id}/turns/read-state",
            json={"turn_numbers": [1], "agent": "cursor"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["marked_read"] >= 1
    assert store.close_owed_wake_for_holder("holder-maestro") == 0


def test_manage_notice_does_not_close_open_owed_wake_row(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _bind_store(tmp_path, monkeypatch)
    store.coalesce_owed_wake_milestone(
        holder_id="holder-maestro",
        lane_thread_id="18460",
        turn_ref={"thread_id": "18460", "turn_number": 1, "subject": "MILESTONE|x"},
    )
    after_turn_created(
        thread_id="18460",
        to_agent="cursor",
        from_agent="manage",
        subject="FACT|restart_intent terminal — owed wake (intent-1)",
        turn_number=2,
    )
    with store._connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM owed_wake "
            "WHERE holder_id=? AND closed_at IS NULL",
            ("holder-maestro",),
        ).fetchone()["c"]
    assert n == 1


def test_schedule_force_gate_notice_wrapper_no_recursion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.model_manager.ui.controller import restart_drain_force_gate as gate

    called: list[dict] = []
    monkeypatch.setattr(
        gate,
        "_schedule_force_gate_notice_impl",
        lambda **kw: called.append(kw),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.owed_wake_ledger.maybe_flush_pending_owed_wakes",
        MagicMock(return_value=0),
    )
    gate._schedule_force_gate_notice(thread_id="1", subject="s", body="b")
    assert len(called) == 1
