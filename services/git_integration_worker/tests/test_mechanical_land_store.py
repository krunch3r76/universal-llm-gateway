"""Mechanical-land store unit tests (§13 stage 3)."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from mechanical_land.schema import JournalForkRefused, UnknownJournalEvent

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.mechanical_land.store import (
    MechanicalLandStore,
    MechanicalLandStoreError,
    build_implement_admit_body,
)

pytestmark = pytest.mark.offline

_POLICY = {"models": {"implement": {"model": "cursor/composer-2.5", "knobs": {}}}}


def _plan(**overrides: object) -> dict:
    base = {
        "admitted": True,
        "kind": "implement",
        "source_ref": "todo:mechanical-land-pipeline-s3",
        "policy": _POLICY,
        "policy_sha256": "pol" + "0" * 61,
        "dispatch_thread_id": "15851",
        "caller_agent": "grok-bot-gc1",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path / "cortex"))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def test_append_cas_one_winner_under_contention(tmp_path: Path) -> None:
    store = MechanicalLandStore()
    plan = _plan()
    with (
        patch.object(store, "_mint_lane_thread", return_value="9001"),
        patch.object(
            store,
            "_post_admit",
            return_value=(200, {"dispatch_id": "d1", "execution_id": "e1"}),
        ),
    ):
        result = store.birth_from_plan(plan)
    run_id = result.run_id
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def worker(prev: int) -> None:
        barrier.wait()
        try:
            store.append_event(
                run_id=run_id,
                prev_seq=prev,
                step_id="run",
                attempt=1,
                flow="main",
                event="transition_decided",
                payload={
                    "from_step": "run",
                    "to_step": "run",
                    "attempt": 1,
                    "reason": "x",
                },
                source="reactor",
            )
            outcomes.append("ok")
        except JournalForkRefused:
            outcomes.append("fork")

    t1 = threading.Thread(target=worker, args=(3,))
    t2 = threading.Thread(target=worker, args=(3,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert outcomes.count("ok") == 1
    assert outcomes.count("fork") == 1


def test_stargate_5xx_appends_leg_admit_failed(tmp_path: Path) -> None:
    store = MechanicalLandStore()
    with (
        patch.object(store, "_mint_lane_thread", return_value="9002"),
        patch.object(store, "_post_admit", return_value=(503, {"error": "down"})),
    ):
        result = store.birth_from_plan(_plan())
    assert result.admit_ok is False
    assert result.http_status == 502
    snap = store.get_run(result.run_id)
    assert snap is not None
    events = [e["event"] for e in snap["events"]]
    assert "leg_admit_failed" in events
    assert "leg_admitted" not in events


def test_bus_down_lane_thread_unavailable_no_admit() -> None:
    store = MechanicalLandStore()

    def _fail(**_kwargs: object) -> str:
        raise MechanicalLandStoreError("lane_thread_unavailable")

    with (
        patch.object(store, "_mint_lane_thread", side_effect=_fail),
        patch.object(store, "_post_admit") as post,
    ):
        with pytest.raises(MechanicalLandStoreError, match="lane_thread_unavailable"):
            store.birth_from_plan(_plan())
        post.assert_not_called()


def test_sqlite_locked_retries_like_ledger(tmp_path: Path) -> None:
    """Store uses ledger _connect with timeout=30 (observed in cursor_dispatch_ledger)."""
    from services.git_integration_worker import cursor_dispatch_ledger as cdl

    assert "timeout=30.0" in Path(cdl.__file__).read_text(encoding="utf-8")
    conn = cdl._connect(tmp_path / "probe.db")
    conn.close()


def test_idempotency_key_returns_same_run() -> None:
    store = MechanicalLandStore()
    key = "idem-test-key"
    with (
        patch.object(store, "_mint_lane_thread", return_value="9010"),
        patch.object(
            store,
            "_post_admit",
            return_value=(200, {"dispatch_id": "d-idem", "execution_id": "e-idem"}),
        ),
    ):
        first = store.birth_from_plan(_plan(), idempotency_key=key)
        second = store.birth_from_plan(_plan(), idempotency_key=key)
    assert first.run_id == second.run_id
    assert second.http_status == 200


def test_projection_written_after_append(tmp_path: Path) -> None:
    store = MechanicalLandStore()
    with (
        patch.object(store, "_mint_lane_thread", return_value="9011"),
        patch.object(
            store,
            "_post_admit",
            return_value=(200, {"dispatch_id": "d2", "execution_id": "e2"}),
        ),
    ):
        result = store.birth_from_plan(_plan())
    proj = tmp_path / "cortex/notes/system/runs/mechanical-land" / f"{result.run_id}.md"
    assert proj.is_file()
    text = proj.read_text(encoding="utf-8")
    assert result.run_id in text
    assert "run_birthed" in text


def _birth_run(store: MechanicalLandStore) -> str:
    with (
        patch.object(store, "_mint_lane_thread", return_value="9099"),
        patch.object(
            store,
            "_post_admit",
            return_value=(200, {"dispatch_id": "d-x", "execution_id": "e-x"}),
        ),
    ):
        return store.birth_from_plan(_plan()).run_id


def _append_transition(store: MechanicalLandStore, run_id: str, prev: int) -> int:
    return store.append_event(
        run_id=run_id,
        prev_seq=prev,
        step_id="run",
        attempt=1,
        flow="main",
        event="transition_decided",
        payload={
            "from_step": "run",
            "to_step": "run",
            "attempt": 1,
            "reason": "noop",
        },
        source="reactor",
    )


def test_get_run_folds_full_journal_returns_last_20_events() -> None:
    store = MechanicalLandStore()
    run_id = _birth_run(store)
    seq = 3
    for _ in range(22):
        seq = _append_transition(store, run_id, seq)
    snap = store.get_run(run_id, event_limit=20)
    assert snap is not None
    assert snap["state"]["tip_seq"] == 25
    assert len(snap["events"]) == 20
    assert snap["events"][0]["seq"] == 6
    assert snap["events"][-1]["seq"] == 25


def test_append_and_projection_past_200_events(tmp_path: Path) -> None:
    store = MechanicalLandStore()
    run_id = _birth_run(store)
    seq = 3
    for _ in range(197):
        seq = _append_transition(store, run_id, seq)
    seq = _append_transition(store, run_id, seq)
    snap = store.get_run(run_id)
    assert snap is not None
    assert snap["state"]["tip_seq"] == 201
    proj = tmp_path / "cortex/notes/system/runs/mechanical-land" / f"{run_id}.md"
    assert proj.is_file()
    assert '"tip_seq": 201' in proj.read_text(encoding="utf-8")


def test_stargate_connect_error_appends_leg_admit_failed_503() -> None:
    store = MechanicalLandStore()
    with (
        patch.object(store, "_mint_lane_thread", return_value="9003"),
        patch.object(
            store,
            "_post_admit",
            side_effect=httpx.ConnectError("refused"),
        ),
    ):
        result = store.birth_from_plan(_plan())
    assert result.http_status == 503
    assert result.admit_error_class == "ConnectError"
    snap = store.get_run(result.run_id)
    assert snap is not None
    assert snap["state"]["status"] == "blocked"
    assert "leg_admit_failed" in [e["event"] for e in snap["events"]]


def test_stargate_timeout_appends_leg_admit_failed_503() -> None:
    store = MechanicalLandStore()
    with (
        patch.object(store, "_mint_lane_thread", return_value="9004"),
        patch.object(
            store,
            "_post_admit",
            side_effect=httpx.ReadTimeout("slow"),
        ),
        patch(
            "services.git_integration_worker.mechanical_land.store.lookup_mechanical_leg_dispatch",
            return_value=None,
        ),
        patch(
            "services.git_integration_worker.mechanical_land.store.cancel_unconfirmed_mechanical_leg_dispatches",
        ) as cancel,
    ):
        result = store.birth_from_plan(_plan())
    assert result.http_status == 503
    assert result.admit_error_class == "ReadTimeout"
    cancel.assert_not_called()


def test_read_timeout_recovers_when_ledger_has_dispatch() -> None:
    store = MechanicalLandStore()
    with (
        patch.object(store, "_mint_lane_thread", return_value="9005"),
        patch.object(
            store,
            "_post_admit",
            side_effect=httpx.ReadTimeout("slow"),
        ),
        patch(
            "services.git_integration_worker.mechanical_land.store.lookup_mechanical_leg_dispatch",
            return_value={
                "dispatch_id": "d-recovered",
                "execution_id": "e-recovered",
                "status": "running",
            },
        ),
    ):
        result = store.birth_from_plan(_plan())
    assert result.admit_ok is True
    assert result.dispatch_id == "d-recovered"
    assert result.status == "awaiting_leg"
    snap = store.get_run(result.run_id)
    assert snap is not None
    assert "leg_admitted" in [e["event"] for e in snap["events"]]


def test_append_log_event_does_not_raise_after_commit(tmp_path: Path) -> None:
    store = MechanicalLandStore()
    run_id = _birth_run(store)
    seq = store.append_event(
        run_id=run_id,
        prev_seq=3,
        step_id="run",
        attempt=1,
        flow="main",
        event="log",
        payload={"message": "projection fold smoke"},
        source="test",
    )
    assert seq == 4
    snap = store.get_run(run_id, event_limit=10)
    assert snap is not None
    assert any(e["event"] == "log" for e in snap["events"])


def test_append_unknown_fold_event_commits_without_raising() -> None:
    store = MechanicalLandStore()
    run_id = _birth_run(store)
    seq = store.append_event(
        run_id=run_id,
        prev_seq=3,
        step_id="run",
        attempt=1,
        flow="main",
        event="after_blocked_marker",
        payload={"marker": True},
        source="test",
    )
    assert seq == 4
    with pytest.raises(UnknownJournalEvent):
        store.get_run(run_id)


def test_create_thread_exception_leaves_run_blocked_not_birthing() -> None:
    store = MechanicalLandStore()

    def _boom(**_kwargs: object) -> str:
        raise RuntimeError("bus sqlite down")

    with (
        patch.object(store, "_mint_lane_thread", side_effect=_boom),
        patch.object(store, "_post_admit") as post,
    ):
        with pytest.raises(MechanicalLandStoreError, match="lane_thread_unavailable"):
            store.birth_from_plan(_plan())
        post.assert_not_called()
    with store._connect() as conn:
        row = conn.execute(
            "SELECT status FROM mechanical_land_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row["status"] == "blocked"


def test_build_implement_admit_body_stamps_mechanical_land() -> None:
    body = build_implement_admit_body(
        run_id="ml-abc123456789",
        lane_thread_id="7777",
        source_ref="todo:foo",
        policy=_POLICY,
        policy_sha256="abc",
        caller_agent="grok-bot-gc1",
    )
    ml = body["generation_options"]["mechanical_land"]
    assert ml["run_id"] == "ml-abc123456789"
    assert ml["step_id"] == "implement"
    assert body["contract"] == "implement"
    assert body["lane"] == "B"
