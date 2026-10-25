"""Operator observability ledger — producer 1/2 owed_wake behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.model_manager.ui.controller import owed_wake_ledger as owed_wake_mod
from scripts.model_manager.ui.controller.owed_wake_ledger import (
    OwedWakeDeps,
    attempt_dispatch,
    flush_pending_owed_wakes,
    is_chrome_bus_subject,
    maybe_flush_pending_owed_wakes,
    maybe_producer1_milestone,
    producer1_cse_dispatch_enabled,
    producer2_on_restart_terminal,
    project_stale_unread_milestone_owed_wakes,
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


def test_milestone_cdp_reply_in_subject_is_not_chrome() -> None:
    assert not is_chrome_bus_subject("MILESTONE|cdp reply latency fixed")
    assert subject_qualifies_producer1("MILESTONE|cdp reply latency fixed")
    assert not subject_qualifies_producer1("FACT|cdp reply latency fixed")


@pytest.mark.parametrize(
    "subject",
    [
        "CHECKPOINT",
        "LANE CLOSEOUT",
        "cdp generate",
        "cdp reply",
        "cursor-sdk generate",
    ],
)
def test_chrome_markers_match_prefix_only(subject: str) -> None:
    assert is_chrome_bus_subject(subject)
    assert is_chrome_bus_subject(subject.upper())


def test_allowlisted_subject_qualifies() -> None:
    assert subject_qualifies_producer1("MILESTONE|decision:foo|bar")


@pytest.mark.parametrize(
    "subject",
    [
        "PLAN r2|scope",
        "VERDICT #12|outcome",
        "FAILED|work-key",
        "BLOCKED|work-key",
        "LANDED|sha",
        "QUESTION|blocker",
        "RULING|work-key",
        "AMEND #712|scope",
        "SEAT_REGISTRATION|hop",
        "ESCALATED|work-key",
        "READY|leg",
    ],
)
def test_r3_spaced_milestone_heads_qualify(subject: str) -> None:
    assert subject_qualifies_producer1(subject)


@pytest.mark.parametrize(
    "subject",
    [
        "FACT|pickup|x",
        "PICKUP|work-1",
        "UPDATE|work-1",
        "HOP|lane-1",
        "ACK|batch",
        "STATUS|ack-batch|leg1",
        "ROUTE|control",
    ],
)
def test_r3_non_milestone_heads_excluded(subject: str) -> None:
    assert not subject_qualifies_producer1(subject)


@pytest.mark.parametrize("to_agent", ["maestro", "web-anthropic"])
def test_checkpoint_creates_no_owed_wake_row(
    tmp_path, monkeypatch, to_agent: str
) -> None:
    monkeypatch.delenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", raising=False)
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    deps = _deps()
    out = maybe_producer1_milestone(
        store,
        subject="CHECKPOINT 18460 tip",
        to_agent=to_agent,
        from_agent="cursor",
        thread_id="18460",
        turn_number=79,
        holder_id="holder-maestro",
        lane_thread_id="18460",
        deps=deps,
    )
    assert out["recorded"] is False
    assert store.list_open_undispatched_owed_wakes() == []


def test_cdp_generate_chrome_creates_no_owed_wake_row_web_anthropic(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", raising=False)
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    out = maybe_producer1_milestone(
        store,
        subject="cdp generate",
        to_agent="web-anthropic",
        from_agent="cursor",
        thread_id="18460",
        turn_number=81,
        holder_id="holder-maestro",
        lane_thread_id="18460",
        deps=_deps(),
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


def test_allowlisted_milestone_web_anthropic_creates_row_and_requests_dispatch(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "1")
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    endpoint_calls: list[dict] = []
    deps = _deps()
    deps.endpoint_wake = lambda **kw: endpoint_calls.append(kw) or True  # type: ignore[method-assign]
    out = maybe_producer1_milestone(
        store,
        subject="MILESTONE|decision:operator-observability-ledger|web",
        to_agent="web-anthropic",
        from_agent="cursor",
        thread_id="18460",
        turn_number=82,
        holder_id="holder-maestro",
        lane_thread_id="18460",
        deps=deps,
    )
    assert out["recorded"] is True
    assert out["created"] is True
    assert out["dispatched"] is True
    assert len(endpoint_calls) == 1
    assert store.list_open_undispatched_owed_wakes() == []


@pytest.mark.parametrize("to_agent", ["maestro", "web-anthropic"])
def test_two_milestones_one_open_row_second_appends_without_dispatch(
    tmp_path, monkeypatch, to_agent: str
) -> None:
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "1")
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    endpoint_calls: list[dict] = []
    deps = _deps()
    deps.endpoint_wake = lambda **kw: endpoint_calls.append(kw) or True  # type: ignore[method-assign]
    first = maybe_producer1_milestone(
        store,
        subject="MILESTONE|a|one",
        to_agent=to_agent,
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
        to_agent=to_agent,
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
        store.get_owed_wake(r.row_id) for r in store.list_open_undispatched_owed_wakes()
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
        subject="MILESTONE|decision:operator-observability-ledger|gap",
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
        subject="MILESTONE|decision:operator-observability-ledger|gap",
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


def _stale_turn(
    *, turn_number: int, age_minutes: float, to_agent: str = "maestro"
) -> dict:
    return {
        "turn_number": turn_number,
        "subject": "MILESTONE|stale|backstop",
        "to": to_agent,
        "from": "cursor",
        "read_at": None,
        "created_at": (datetime.now(UTC) - timedelta(minutes=age_minutes)).isoformat(),
    }


def test_stale_unread_milestone_projected_on_lazy_flush(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "off")
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    old = _stale_turn(turn_number=5, age_minutes=16)
    young = _stale_turn(turn_number=6, age_minutes=5)
    monkeypatch.setattr(owed_wake_mod, "_lanes_for_stale_projection", lambda: ["18460"])
    monkeypatch.setattr(
        owed_wake_mod,
        "_fetch_turns_for_lane",
        lambda _lane: [old, young],
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.resolve_maestro_holder_id_for_lane",
        lambda _lane: "holder-maestro",
    )
    projected = project_stale_unread_milestone_owed_wakes(store)
    assert projected == 1
    rows = store.list_open_undispatched_owed_wakes()
    assert len(rows) == 1
    assert rows[0].turn_refs[0]["turn_number"] == 5


def test_stale_projection_skips_turn_already_dispatched(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "off")
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    row, _ = store.coalesce_owed_wake_milestone(
        holder_id="holder-maestro",
        lane_thread_id="18460",
        turn_ref={
            "thread_id": "18460",
            "turn_number": 5,
            "subject": "MILESTONE|stale|backstop",
        },
    )
    store.mark_owed_wake_dispatched(row.row_id, outcome="dispatched")
    stale = _stale_turn(turn_number=5, age_minutes=16)
    monkeypatch.setattr(owed_wake_mod, "_lanes_for_stale_projection", lambda: ["18460"])
    monkeypatch.setattr(owed_wake_mod, "_fetch_turns_for_lane", lambda _lane: [stale])
    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.resolve_maestro_holder_id_for_lane",
        lambda _lane: "holder-maestro",
    )
    assert project_stale_unread_milestone_owed_wakes(store) == 0
    fetched = store.get_owed_wake(row.row_id)
    assert fetched is not None
    assert len(fetched.turn_refs) == 1


def test_stale_unread_younger_than_15_min_not_projected(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "off")
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    young = _stale_turn(turn_number=7, age_minutes=10)
    monkeypatch.setattr(owed_wake_mod, "_lanes_for_stale_projection", lambda: ["18460"])
    monkeypatch.setattr(owed_wake_mod, "_fetch_turns_for_lane", lambda _lane: [young])
    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.resolve_maestro_holder_id_for_lane",
        lambda _lane: "holder-maestro",
    )
    assert project_stale_unread_milestone_owed_wakes(store) == 0
    assert store.list_open_undispatched_owed_wakes() == []


def test_maybe_flush_deps_unavailable_keeps_open_row(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "off")
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    maybe_producer1_milestone(
        store,
        subject="MILESTONE|decision:operator-observability-ledger|hold",
        to_agent="maestro",
        from_agent="cursor",
        thread_id="18460",
        turn_number=90,
        holder_id="holder-maestro",
        lane_thread_id="18460",
        deps=_deps(),
    )
    monkeypatch.setattr(owed_wake_mod, "_lanes_for_stale_projection", lambda: [])
    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.build_owed_wake_deps",
        MagicMock(side_effect=RuntimeError("deps down")),
    )
    assert maybe_flush_pending_owed_wakes(store=store) == 0
    open_rows = store.list_open_undispatched_owed_wakes()
    assert len(open_rows) == 1
    assert open_rows[0].dispatched_at is None


def test_bus_failure_then_gate_flush_succeeds(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(owed_wake_mod, "_lanes_for_stale_projection", lambda: [])
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


def test_coalesce_owed_wake_milestone_idempotent_on_thread_turn(tmp_path) -> None:
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    turn_ref = {
        "thread_id": "18460",
        "turn_number": 3,
        "subject": "MILESTONE|decision:foo",
    }
    store.coalesce_owed_wake_milestone(
        holder_id="holder-maestro",
        lane_thread_id="18460",
        turn_ref=turn_ref,
    )
    store.coalesce_owed_wake_milestone(
        holder_id="holder-maestro",
        lane_thread_id="18460",
        turn_ref=dict(turn_ref),
    )
    row = store.list_open_undispatched_owed_wakes()[0]
    assert len(row.turn_refs) == 1
    store.coalesce_owed_wake_milestone(
        holder_id="holder-maestro",
        lane_thread_id="18460",
        turn_ref={**turn_ref, "turn_number": 4},
    )
    row = store.list_open_undispatched_owed_wakes()[0]
    assert len(row.turn_refs) == 2


def test_fetch_turns_for_lane_sends_compact_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict] = []

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, path, *, params=None, headers=None):
            captured.append(dict(params or {}))
            body = MagicMock()
            body.status_code = 200
            body.json.return_value = {"turns": []}
            return body

    monkeypatch.setattr(
        "transport_utils.make_sync_client",
        lambda *a, **k: _Client(),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.agent_bus_request_headers",
        lambda: {},
    )
    owed_wake_mod._fetch_turns_for_lane("18460")
    assert captured
    assert captured[0].get("compact") == "true"


def test_attempt_backoff_then_held(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(owed_wake_mod, "_lanes_for_stale_projection", lambda: [])
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    row, _ = store.coalesce_owed_wake_milestone(
        holder_id="holder-maestro",
        lane_thread_id="18460",
        turn_ref={
            "thread_id": "18460",
            "turn_number": 1,
            "subject": "MILESTONE|x",
        },
    )
    deps = _deps(endpoint_ok=False)
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    clock = {"now": base}

    class _FakeDateTime:
        UTC = UTC
        fromisoformat = staticmethod(datetime.fromisoformat)

        @staticmethod
        def now(tz=None):
            return clock["now"]

    monkeypatch.setattr(owed_wake_mod, "datetime", _FakeDateTime)

    future = (base + timedelta(minutes=5)).isoformat()
    store.set_owed_wake_next_attempt(row.row_id, future)
    flush_pending_owed_wakes(store, deps=deps)
    refreshed = store.get_owed_wake(row.row_id)
    assert refreshed is not None
    assert refreshed.attempt_count == 0

    for i in range(5):
        clock["now"] = base + timedelta(hours=i + 1)
        flush_pending_owed_wakes(store, deps=deps)

    final = store.get_owed_wake(row.row_id)
    assert final is not None
    assert final.dispatched_at is not None
    assert store.list_open_undispatched_owed_wakes() == []


def test_failure_clock_sets_backoff_and_holds_at_fifth(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schedule retries from the wake's return time, and hold only at five failures.

    The failing endpoint_wake advances the fake clock by ten seconds before
    returning, so flush start and failure time differ. After failures one
    through four, next_attempt_at equals that failure time plus 30, 60, 120,
    and 240 seconds. A flush one second before that instant leaves
    attempt_count unchanged. The row stays open until the fifth failure sets
    attempt_count to 5 and marks it held.
    """
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "1")
    monkeypatch.setattr(owed_wake_mod, "_lanes_for_stale_projection", lambda: [])
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    row, _ = store.coalesce_owed_wake_milestone(
        holder_id="holder-maestro",
        lane_thread_id="18460",
        turn_ref={
            "thread_id": "18460",
            "turn_number": 1,
            "subject": "MILESTONE|x",
        },
    )
    deps = _deps(endpoint_ok=False)
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    clock = {"now": base}
    wake_latency = timedelta(seconds=10)

    class _FakeDateTime:
        UTC = UTC
        fromisoformat = staticmethod(datetime.fromisoformat)

        @staticmethod
        def now(tz=None):
            return clock["now"]

    monkeypatch.setattr(owed_wake_mod, "datetime", _FakeDateTime)

    def endpoint_wake(**_kwargs: object) -> bool:
        clock["now"] = clock["now"] + wake_latency
        return False

    deps.endpoint_wake = endpoint_wake  # type: ignore[method-assign]

    for attempt, delay_s in enumerate((30, 60, 120, 240), start=1):
        started = clock["now"]
        flush_pending_owed_wakes(store, deps=deps)
        failure_at = clock["now"]
        assert failure_at == started + wake_latency
        refreshed = store.get_owed_wake(row.row_id)
        assert refreshed is not None
        assert refreshed.attempt_count == attempt
        assert refreshed.dispatched_at is None
        assert (
            refreshed.next_attempt_at
            == (failure_at + timedelta(seconds=delay_s)).isoformat()
        )
        clock["now"] = failure_at + timedelta(seconds=delay_s - 1)
        flush_pending_owed_wakes(store, deps=deps)
        during = store.get_owed_wake(row.row_id)
        assert during is not None
        assert during.attempt_count == attempt
        assert during.dispatched_at is None
        clock["now"] = failure_at + timedelta(seconds=delay_s)

    started = clock["now"]
    flush_pending_owed_wakes(store, deps=deps)
    assert clock["now"] == started + wake_latency
    final = store.get_owed_wake(row.row_id)
    assert final is not None
    assert final.attempt_count == 5
    assert final.dispatched_at is not None
    assert store.list_open_undispatched_owed_wakes() == []


def test_stale_unread_milestone_16min_one_row_one_followup_resolve_at_wake(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """16-minute unread milestone: one row, one followup; resolve_attended at wake."""
    from services.git_integration_worker.cse_session_holders import (
        _import_cse_session_warm_module,
    )

    warm = _import_cse_session_warm_module()

    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "1")
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    stale = _stale_turn(turn_number=5, age_minutes=16)
    monkeypatch.setattr(owed_wake_mod, "_lanes_for_stale_projection", lambda: ["18460"])
    monkeypatch.setattr(owed_wake_mod, "_fetch_turns_for_lane", lambda _lane: [stale])
    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.resolve_maestro_holder_id_for_lane",
        lambda _lane: "holder-maestro",
    )
    assert project_stale_unread_milestone_owed_wakes(store) == 1
    assert len(store.list_open_undispatched_owed_wakes()) == 1

    stored_url = "https://claude.ai/cowork/cse_stored"
    live_url = "https://claude.ai/cowork/cse_live"
    resolve_threads: list[str | None] = []
    followup_calls: list[dict] = []

    def _relay_attended(*, parent_thread=None, timeout_s=30.0):
        resolve_threads.append(parent_thread)
        return {
            "state": "current",
            "current": {"chat_url": live_url, "registration_id": "reg-live"},
        }

    def _relay_followup(**kwargs):
        followup_calls.append(dict(kwargs))
        return {"ok": True, "send_verified": True}

    monkeypatch.setattr(warm, "relay_attended", _relay_attended)
    monkeypatch.setattr(warm, "relay_followup", _relay_followup)

    holder = {
        "holder_id": "holder-maestro",
        "seat_state": "driving",
        "chat_url": stored_url,
        "registration_id": "reg-stored",
        "lane_thread_id": "18460",
    }
    deps = _deps(holder=holder)
    deps.endpoint_wake = (  # type: ignore[method-assign]
        lambda *, holder_id, lane_thread_id, wake_kind, context: warm.dispatch_cse_holder_followup(
            holder=context["holder"]
        )
    )

    assert flush_pending_owed_wakes(store, deps=deps) == 1
    assert resolve_threads == ["12286"]
    assert len(followup_calls) == 1
    assert followup_calls[0]["chat_url"] == live_url
    assert followup_calls[0]["chat_url"] != stored_url
    assert store.list_open_undispatched_owed_wakes() == []


def test_read_milestone_and_young_unread_no_row_no_followup(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """read_at milestone and 10-minute unread produce no row and no followup."""
    from services.git_integration_worker.cse_session_holders import (
        _import_cse_session_warm_module,
    )

    warm = _import_cse_session_warm_module()

    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "1")
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    read_turn = _stale_turn(turn_number=5, age_minutes=16)
    read_turn["read_at"] = datetime.now(UTC).isoformat()
    young = _stale_turn(turn_number=6, age_minutes=10)
    monkeypatch.setattr(owed_wake_mod, "_lanes_for_stale_projection", lambda: ["18460"])
    monkeypatch.setattr(
        owed_wake_mod, "_fetch_turns_for_lane", lambda _lane: [read_turn, young]
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.resolve_maestro_holder_id_for_lane",
        lambda _lane: "holder-maestro",
    )
    followup = MagicMock()
    monkeypatch.setattr(warm, "relay_followup", followup)
    assert project_stale_unread_milestone_owed_wakes(store) == 0
    assert store.list_open_undispatched_owed_wakes() == []
    assert flush_pending_owed_wakes(store, deps=_deps()) == 0
    followup.assert_not_called()


def test_two_allowlisted_milestones_one_burst_one_followup(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two allowlisted milestones in one open burst coalesce to one followup."""
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
    assert first["created"] is True and first["dispatched"] is True
    assert second["created"] is False and second["dispatched"] is False
    assert len(endpoint_calls) == 1


def test_attempt_dispatch_skips_when_turn_refs_already_read(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    row_id = store.coalesce_owed_wake_milestone(
        holder_id="holder-maestro",
        lane_thread_id="18460",
        turn_ref={"thread_id": "15851", "turn_number": 2753, "subject": "QUESTION|x"},
    )[0].row_id
    row = store.get_owed_wake(row_id)
    assert row is not None
    monkeypatch.setattr(owed_wake_mod, "_turn_refs_all_read", lambda _refs: True)
    assert attempt_dispatch(store, row, deps=_deps()) is True
    updated = store.get_owed_wake(row_id)
    assert updated is not None
    assert updated.dispatched_at is not None


def test_attempt_dispatch_skips_when_endpoint_wake_already_covers_turn(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RestartIntentStore(db_path=tmp_path / "db.sqlite")
    row_id = store.coalesce_owed_wake_milestone(
        holder_id="holder-maestro",
        lane_thread_id="18460",
        turn_ref={"thread_id": "12286", "turn_number": 4683, "subject": "DONE|x"},
    )[0].row_id
    row = store.get_owed_wake(row_id)
    assert row is not None
    monkeypatch.setattr(owed_wake_mod, "_turn_refs_all_read", lambda _refs: False)
    monkeypatch.setattr(
        owed_wake_mod, "_endpoint_wake_already_delivered", lambda _refs, agent: True
    )
    endpoints: list[dict] = []
    deps = _deps()
    deps.endpoint_wake = lambda **kw: endpoints.append(kw) or True  # type: ignore[method-assign]
    assert attempt_dispatch(store, row, deps=deps) is True
    assert endpoints == []
