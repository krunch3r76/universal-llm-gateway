"""Operator observability ledger — producer 1/2 owed_wake behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.model_manager.ui.controller.owed_wake_ledger import (
    OwedWakeDeps,
    attempt_dispatch,
    flush_pending_owed_wakes,
    is_chrome_bus_subject,
    maybe_producer1_milestone,
    producer1_cse_dispatch_enabled,
    producer2_on_restart_terminal,
    subject_qualifies_producer1,
)
from scripts.model_manager.ui.controller.restart_drain_supervise import (
    supervise_until_lifecycle,
)
from scripts.model_manager.ui.controller.restart_intent_store import (
    STATUS_COMPLETED,
    STATUS_DRAINED_RESTARTING,
    RestartIntentStore,
)


def _deps(
    *,
    post_ok: bool = True,
    endpoint_ok: bool = True,
    holder: dict | None = None,
) -> OwedWakeDeps:
    posts: list[dict] = []
    endpoints: list[dict] = []

    def post_bus_turn(*, thread_id: str, subject: str, body: str) -> bool:
        posts.append({"thread_id": thread_id, "subject": subject, "body": body})
        return post_ok

    def endpoint_wake(
        *,
        holder_id: str,
        lane_thread_id: str | None,
        wake_kind: str,
        context: dict,
    ) -> bool:
        endpoints.append(
            {
                "holder_id": holder_id,
                "lane_thread_id": lane_thread_id,
                "wake_kind": wake_kind,
                "context": context,
            }
        )
        return endpoint_ok

    default_holder = holder or {
        "holder_id": "holder-maestro",
        "seat_state": "driving",
        "chat_url": "https://claude.ai/c/x",
        "registration_id": "reg-1",
        "lane_thread_id": "18460",
    }

    return OwedWakeDeps(
        post_bus_turn=post_bus_turn,
        endpoint_wake=endpoint_wake,
        holder_lookup=lambda hid: default_holder if hid == "holder-maestro" else None,
        resolve_superseded_holder=lambda _reg: None,
        lane_uses_bridge_wake=lambda _lane: False,
    )


def test_checkpoint_subject_not_allowlisted() -> None:
    assert not subject_qualifies_producer1("CHECKPOINT 18460 abc")
    assert is_chrome_bus_subject("CHECKPOINT 18460 abc")


def test_allowlisted_subject_qualifies() -> None:
    assert subject_qualifies_producer1("MILESTONE|decision:foo|bar")


def test_checkpoint_creates_no_owed_wake_row(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", raising=False)
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    deps = _deps()
    out = maybe_producer1_milestone(
        store,
        subject="CHECKPOINT 18460 tip",
        to_agent="maestro",
        from_agent="cursor",
        thread_id="18460",
        turn_number=79,
        holder_id="holder-maestro",
        lane_thread_id="18460",
        deps=deps,
    )
    assert out["recorded"] is False
    assert store.list_open_undispatched_owed_wakes() == []


def test_allowlisted_milestone_creates_row(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "off")
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    out = maybe_producer1_milestone(
        store,
        subject="MILESTONE|decision:operator-observability-ledger|tip",
        to_agent="maestro",
        from_agent="cursor",
        thread_id="18460",
        turn_number=80,
        holder_id="holder-maestro",
        lane_thread_id="18460",
        deps=_deps(),
    )
    assert out["recorded"] is True
    assert out["created"] is True
    rows = store.list_open_undispatched_owed_wakes()
    assert len(rows) == 1
    assert len(rows[0].turn_refs) == 1


def test_two_milestones_one_open_row_second_appends_without_dispatch(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "1")
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    endpoint_calls: list[dict] = []
    deps = _deps()
    deps.endpoint_wake = lambda **kw: endpoint_calls.append(kw) or True  # type: ignore[method-assign]
    first = maybe_producer1_milestone(
        store,
        subject="MILESTONE|a|one",
        to_agent="maestro",
        from_agent="cursor",
        thread_id="18460",
        turn_number=1,
        holder_id="holder-maestro",
        lane_thread_id="18460",
        deps=deps,
    )
    assert first["created"] is True
    assert first["dispatched"] is True
    second = maybe_producer1_milestone(
        store,
        subject="MILESTONE|a|two",
        to_agent="maestro",
        from_agent="cursor",
        thread_id="18460",
        turn_number=2,
        holder_id="holder-maestro",
        lane_thread_id="18460",
        deps=deps,
    )
    assert second["created"] is False
    assert second["dispatched"] is False
    assert len(endpoint_calls) == 1
    open_rows = [
        store.get_owed_wake(r.row_id)
        for r in store.list_open_undispatched_owed_wakes()
    ]
    if not open_rows:
        with store._connect() as conn:
            row = conn.execute(
                "SELECT row_id FROM owed_wake WHERE holder_id=? AND closed_at IS NULL",
                ("holder-maestro",),
            ).fetchone()
        fetched = store.get_owed_wake(row["row_id"])
    else:
        fetched = open_rows[0]
    assert fetched is not None
    assert len(fetched.turn_refs) == 2


def test_producer1_unset_dispatches(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", raising=False)
    assert producer1_cse_dispatch_enabled() is True
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    endpoint_calls: list[dict] = []
    deps = _deps()
    deps.endpoint_wake = lambda **kw: endpoint_calls.append(kw) or True  # type: ignore[method-assign]
    out = maybe_producer1_milestone(
        store,
        subject="FACT|decision:operator-observability-ledger|gap",
        to_agent="maestro",
        from_agent="cursor",
        thread_id="18460",
        turn_number=3,
        holder_id="holder-maestro",
        lane_thread_id="18460",
        deps=deps,
    )
    assert out["recorded"] is True
    assert out["dispatched"] is True
    assert len(endpoint_calls) == 1
    assert store.list_open_undispatched_owed_wakes() == []


@pytest.mark.parametrize("raw", ["0", "false", "no", "off"])
def test_producer1_explicit_off_writes_row_without_dispatch(
    tmp_path, monkeypatch, raw: str
) -> None:
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", raw)
    assert producer1_cse_dispatch_enabled() is False
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    endpoint_calls: list[dict] = []
    deps = _deps()
    deps.endpoint_wake = lambda **kw: endpoint_calls.append(kw) or True  # type: ignore[method-assign]
    out = maybe_producer1_milestone(
        store,
        subject="FACT|decision:operator-observability-ledger|gap",
        to_agent="maestro",
        from_agent="cursor",
        thread_id="18460",
        turn_number=3,
        holder_id="holder-maestro",
        lane_thread_id="18460",
        deps=deps,
    )
    assert out["recorded"] is True
    assert out["dispatched"] is False
    assert endpoint_calls == []
    assert len(store.list_open_undispatched_owed_wakes()) == 1


def test_bus_failure_then_gate_flush_succeeds(tmp_path) -> None:
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    intent = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="d",
        reason="r",
        caller_thread_id="18374",
    )
    row, _created = store.create_owed_wake_drain_complete(
        holder_id="holder-maestro",
        restart_intent_id=intent.intent_id,
        arming_lanes=["18374"],
        lane_thread_id="18374",
        turn_ref={"restart_intent_id": intent.intent_id},
    )
    failing = _deps(post_ok=False, endpoint_ok=True)
    assert attempt_dispatch(store, row, deps=failing) is False
    assert store.get_owed_wake(row.row_id).dispatched_at is None
    ok = _deps(post_ok=True, endpoint_ok=True)
    assert flush_pending_owed_wakes(store, deps=ok) == 1
    assert store.get_owed_wake(row.row_id).dispatched_at is not None


def test_released_holder_skips_endpoint_wake(tmp_path) -> None:
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    row, _created = store.coalesce_owed_wake_milestone(
        holder_id="holder-maestro",
        lane_thread_id="18460",
        turn_ref={"thread_id": "18460", "turn_number": 1, "subject": "MILESTONE|x"},
    )
    released = {
        "holder_id": "holder-maestro",
        "seat_state": "released",
        "lane_thread_id": "18460",
    }
    deps = _deps(holder=released)
    endpoint_calls: list[dict] = []
    deps.endpoint_wake = lambda **kw: endpoint_calls.append(kw) or True  # type: ignore[method-assign]
    assert attempt_dispatch(store, row, deps=deps) is True
    assert endpoint_calls == []


def test_producer2_terminal_cas_writes_row_and_dispatches(tmp_path) -> None:
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    intent = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="d",
        reason="r",
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
        reason="lifecycle_completed:test",
    )
    completed = store.get(intent.intent_id)
    assert completed is not None
    posts: list[str] = []
    deps = _deps(post_ok=True, endpoint_ok=True)
    deps.post_bus_turn = lambda **kw: posts.append(kw["thread_id"]) or True  # type: ignore[method-assign]
    out = producer2_on_restart_terminal(
        store,
        completed,
        holder_id="holder-maestro",
        deps=deps,
    )
    assert out["arming_lanes"] == ["18460"]
    assert posts == ["18460"]
    assert store.list_open_undispatched_owed_wakes() == []


@pytest.mark.asyncio
async def test_supervise_lifecycle_completed_triggers_producer2_row(
    tmp_path, monkeypatch
) -> None:
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    intent = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="d",
        reason="r",
        caller_thread_id="18460",
    )
    store.advance_if_status(
        intent.intent_id,
        from_status="pending_drain",
        to_status=STATUS_DRAINED_RESTARTING,
        reason="drained",
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.resolve_maestro_holder_id_for_lane",
        lambda _lane: "holder-maestro",
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.build_owed_wake_deps",
        lambda: _deps(post_ok=True, endpoint_ok=True),
    )
    probe = AsyncMock(return_value=MagicMock())
    probe.return_value.detail = None

    def is_idle(_work: object) -> bool:
        return True

    lifecycle = AsyncMock(return_value="ok")

    await supervise_until_lifecycle(
        store=store,
        intent=store.get(intent.intent_id),
        probe=probe,
        is_idle=is_idle,
        lifecycle=lifecycle,
        deadline_s=1.0,
        poll_interval_s=0.01,
        ceiling="extend",
    )
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_COMPLETED
    pending = store.list_open_undispatched_owed_wakes()
    assert pending == []


def test_two_terminal_restarts_coalesce_one_open_row_then_close(
    tmp_path, monkeypatch
) -> None:
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    deps = _deps(post_ok=True, endpoint_ok=True)
    intent_a = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="d",
        reason="r",
        caller_thread_id="18460",
    )
    intent_b = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="d2",
        reason="r2",
        caller_thread_id="18460",
    )
    for intent in (intent_a, intent_b):
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
            reason="lifecycle_completed:test",
        )
        completed = store.get(intent.intent_id)
        assert completed is not None
        producer2_on_restart_terminal(
            store,
            completed,
            holder_id="holder-maestro",
            deps=deps,
        )
    with store._connect() as conn:
        open_count = conn.execute(
            "SELECT COUNT(*) AS n FROM owed_wake "
            "WHERE holder_id=? AND closed_at IS NULL",
            ("holder-maestro",),
        ).fetchone()["n"]
    assert open_count == 1
    store.close_owed_wake_for_holder("holder-maestro")
    assert store.close_owed_wake_for_holder("holder-maestro") == 0
