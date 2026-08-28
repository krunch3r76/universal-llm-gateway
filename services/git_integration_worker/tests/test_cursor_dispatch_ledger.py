"""AC1–AC4: durable cursor-sdk dispatch ledger."""

from __future__ import annotations

import json
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.app import create_app
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    DispatchConflict,
    SourceRefConflict,
    WorkerThreadOccupied,
    _connect,
)
from services.git_integration_worker.cursor_sdk_conductor_conflict import (
    find_open_conductor_holder_conn,
    should_block_implement_for_open_conductor,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "cursor-sdk-dispatch.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield db_path
    CursorDispatchLedger._instance = None


def _req(**overrides: object) -> CursorDispatchRequest:
    dispatch_id = str(overrides.get("dispatch_id", "disp-ac1"))
    base = {
        "thread_id": "t1",
        "model": "cursor/composer-2.5",
        "dispatch_id": dispatch_id,
        "execution_id": f"exec-{dispatch_id}",
        "message": f"hello-{dispatch_id}",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admission(req: CursorDispatchRequest) -> CursorDispatchResponse:
    return CursorDispatchResponse(
        admitted=True,
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        model_id="composer-2.5",
    )


def _make_controller() -> WorkAdmissionController:
    """Real admission controller bound to the reset ledger singleton.

    The direct ``_run_sdk_dispatch_gated`` caller below stubs ``_run_sdk_sync``
    and never starts a drain, so the controller only needs to spawn the inner
    worker task via ``create_tracked_task``.
    """
    return WorkAdmissionController(
        ledger=CursorDispatchLedger.instance(),
        worker_id="test-worker",
        pid=0,
        worker_started_at="test",
    )


def test_admit_idempotent_across_restart() -> None:
    """AC1: admit survives singleton drop (restart simulation)."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    fp = ledger.fingerprint(req)
    admission = _admission(req)

    assert (
        ledger.admit(
            req=req,
            fingerprint=fp,
            execution_id=req.execution_id,
            caller_agent=None,
            resolved_model="composer-2.5",
            admission=admission,
        )
        is None
    )

    CursorDispatchLedger._instance = None
    ledger2 = CursorDispatchLedger.instance()
    cached = ledger2.admit(
        req=req,
        fingerprint=fp,
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=admission,
    )
    assert cached == admission

    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM cursor_sdk_dispatches WHERE dispatch_id = ?",
            (req.dispatch_id,),
        ).fetchone()
    assert row["n"] == 1


def test_admit_fingerprint_conflict() -> None:
    """AC2: mutated payload raises DispatchConflict."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    fp = ledger.fingerprint(req)
    ledger.admit(
        req=req,
        fingerprint=fp,
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(req),
    )

    mutated = _req(model="cursor/other-model")
    with pytest.raises(DispatchConflict):
        ledger.admit(
            req=mutated,
            fingerprint=ledger.fingerprint(mutated),
            execution_id=mutated.execution_id,
            caller_agent=None,
            resolved_model="other-model",
            admission=_admission(mutated),
        )


def test_status_lifecycle() -> None:
    """AC3: admitted→running→completed/failed with terminal_at."""
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="disp-lifecycle")
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(req),
    )
    ledger.mark_running(dispatch_id=req.dispatch_id)
    ledger.mark_terminal(dispatch_id=req.dispatch_id, terminal_status="completed")

    with _connect() as conn:
        row = conn.execute(
            "SELECT status, terminal_status, terminal_at FROM cursor_sdk_dispatches "
            "WHERE dispatch_id = ?",
            (req.dispatch_id,),
        ).fetchone()
    assert row["status"] == "completed"
    assert row["terminal_status"] == "completed"
    assert row["terminal_at"] is not None

    req2 = _req(dispatch_id="disp-fail")
    ledger.admit(
        req=req2,
        fingerprint=ledger.fingerprint(req2),
        execution_id=req2.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(req2),
    )
    ledger.mark_running(dispatch_id=req2.dispatch_id)
    ledger.mark_terminal(dispatch_id=req2.dispatch_id, terminal_status="failed")

    with _connect() as conn:
        row2 = conn.execute(
            "SELECT status, terminal_status FROM cursor_sdk_dispatches "
            "WHERE dispatch_id = ?",
            (req2.dispatch_id,),
        ).fetchone()
    assert row2["status"] == "failed"
    assert row2["terminal_status"] == "failed"


@pytest.mark.asyncio
async def test_ledger_non_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC4: deleted ledger row does not block Phase-1 terminate path."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="1604",
        model="cursor/composer-2.5",
        dispatch_id="disp-nonauth",
        execution_id="exec-nonauth",
        message="hello",
    )
    fp = ledger.fingerprint(req)
    ledger.admit(
        req=req,
        fingerprint=fp,
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id="composer-2.5",
        ),
    )
    ledger.mark_running(dispatch_id=req.dispatch_id)

    with _connect() as conn:
        conn.execute(
            "DELETE FROM cursor_sdk_dispatches WHERE dispatch_id = ?",
            (req.dispatch_id,),
        )

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    bus.terminate_dispatch = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    def _ok_outcome(**_kwargs: object):
        from services.git_integration_worker.cursor_sdk_closeout import SdkRunOutcome

        return SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=100,
            tool_call_count=0,
        )

    async def _noop_acquire(
        *,
        dispatch_id: str | None = None,
        timeout: float | None = None,
        **_kwargs: object,
    ) -> str:
        return dispatch_id or "test-slot"

    monkeypatch.setattr(route_mod, "acquire_sdk_dispatch_slot", _noop_acquire)
    monkeypatch.setattr(route_mod, "_run_sdk_sync", _ok_outcome)

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=route_mod._CONFIG.source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        bus=bus,
        controller=_make_controller(),
    )

    bus.terminate_dispatch.assert_awaited_once_with(
        thread_id="1604", terminal_status="completed"
    )


def test_ledger_db_path_stable_across_home_swap(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (prod 'no such table: cursor_sdk_dispatches', 2026-06-11).

    With DATA_DIR unset, _ledger_path() resolves HOME-relative. The dispatch path
    swaps os.environ["HOME"] for cursor-sdk-bridge isolation, so any ledger op
    performed inside that swap must still reach the DB created at construction
    (pre-swap), not an empty <swapped-home>/.gateway DB. Every other test sets
    DATA_DIR, which masked this in CI — so this test deliberately does not.
    """
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    swapped_home = tmp_path / "dispatch-home"
    swapped_home.mkdir()
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.setenv("HOME", str(real_home))
    CursorDispatchLedger._instance = None

    ledger = CursorDispatchLedger.instance()  # table created under real_home
    req = _req()
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(req),
    )

    # Defensive: a HOME change must not move the ledger DB path (captured pre-swap).
    monkeypatch.setenv("HOME", str(swapped_home))

    # Pre-fix, this raised sqlite3.OperationalError("no such table: ...").
    ledger.bump_heartbeat(dispatch_id=req.dispatch_id)

    assert ledger._db_path == real_home / ".gateway" / "cursor-sdk-dispatch.db"
    CursorDispatchLedger._instance = None


def test_execution_id_stable_across_restart() -> None:
    """AC3: re-admit after singleton reset returns same persisted execution_id."""
    ledger = CursorDispatchLedger.instance()
    req = _req(execution_id="exec-stable-1")
    fp = ledger.fingerprint(req)
    admission = _admission(req)

    assert (
        ledger.admit(
            req=req,
            fingerprint=fp,
            execution_id=req.execution_id,
            caller_agent=None,
            resolved_model="composer-2.5",
            admission=admission,
        )
        is None
    )

    CursorDispatchLedger._instance = None
    ledger2 = CursorDispatchLedger.instance()
    cached = ledger2.admit(
        req=req,
        fingerprint=fp,
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=admission,
    )
    assert cached == admission

    with _connect() as conn:
        row = conn.execute(
            "SELECT execution_id FROM cursor_sdk_dispatches WHERE dispatch_id = ?",
            (req.dispatch_id,),
        ).fetchone()
    assert row["execution_id"] == "exec-stable-1"


def test_execution_id_fingerprint_conflict() -> None:
    """AC4: changed execution_id for same dispatch_id raises DispatchConflict."""
    ledger = CursorDispatchLedger.instance()
    req = _req(execution_id="exec-a")
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(req),
    )

    swapped = _req(execution_id="exec-b")
    with pytest.raises(DispatchConflict):
        ledger.admit(
            req=swapped,
            fingerprint=ledger.fingerprint(swapped),
            execution_id=swapped.execution_id,
            caller_agent=None,
            resolved_model="composer-2.5",
            admission=_admission(swapped),
        )


def _admit(
    ledger: CursorDispatchLedger,
    req: CursorDispatchRequest,
    *,
    source_repo: str = "/repo",
    contract: str = "consult",
    read_only: bool = False,
    worker_instance: str = "worker-a",
    source_ref: str | None = None,
    work_key: str | None = None,
    force: bool = False,
) -> CursorDispatchResponse | None:
    return ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(req),
        source_repo=source_repo,
        contract=contract,
        read_only=read_only,
        worker_instance=worker_instance,
        source_ref=source_ref,
        work_key=work_key,
        force=force,
    )


def test_writer_lease_contention_queues_ac1() -> None:
    """AC1: second writer on same repo is INSERTed queued (not DispatchConflict)."""
    ledger = CursorDispatchLedger.instance()
    repo = "/mnt/torus/projects/universal-llm-gateway"
    _admit(
        ledger,
        _req(dispatch_id="writer-1"),
        source_repo=repo,
        contract="implement",
    )
    req2 = _req(dispatch_id="writer-2")
    queued = ledger.admit(
        req=req2,
        fingerprint=ledger.fingerprint(req2),
        execution_id=req2.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(req2),
        source_repo=repo,
        contract="implement",
        worker_instance="worker-a",
    )
    assert queued is not None
    assert queued.status == "queued"
    assert queued.queue_position == 1
    with _connect() as conn:
        row = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("writer-2",),
        ).fetchone()
    assert row["status"] == "queued"


def test_read_only_exempt_while_writer_active_ac2() -> None:
    """AC2: read_only dispatch admits despite active writer."""
    ledger = CursorDispatchLedger.instance()
    repo = "/mnt/torus/projects/universal-llm-gateway"
    _admit(ledger, _req(dispatch_id="writer-1"), source_repo=repo)
    reader = _req(dispatch_id="reader-1", message="read only")
    reader_ro = CursorDispatchRequest(**{**reader.model_dump(), "read_only": True})
    assert (
        ledger.admit(
            req=reader_ro,
            fingerprint=ledger.fingerprint(reader_ro),
            execution_id=reader_ro.execution_id,
            caller_agent=None,
            resolved_model="composer-2.5",
            admission=_admission(reader_ro),
            source_repo=repo,
            read_only=True,
            worker_instance="worker-a",
        )
        is None
    )


def test_prior_instance_survivor_does_not_block_ac7() -> None:
    """AC7: prior worker_instance row does not hold the write lease after terminal."""
    ledger = CursorDispatchLedger.instance()
    repo = "/mnt/torus/projects/universal-llm-gateway"
    survivor = _req(dispatch_id="survivor")
    _admit(
        ledger,
        survivor,
        source_repo=repo,
        contract="implement",
        worker_instance="prior-instance",
    )
    ledger.mark_running(dispatch_id=survivor.dispatch_id)
    ledger.mark_terminal(dispatch_id=survivor.dispatch_id, terminal_status="failed")
    assert (
        _admit(
            ledger,
            _req(dispatch_id="new-writer"),
            source_repo=repo,
            contract="implement",
            worker_instance="worker-a",
        )
        is None
    )


def test_same_instance_admitted_blocks_ac7() -> None:
    """AC7: same-instance admitted writer causes second writer to queue."""
    ledger = CursorDispatchLedger.instance()
    repo = "/mnt/torus/projects/universal-llm-gateway"
    _admit(
        ledger,
        _req(dispatch_id="held-writer"),
        source_repo=repo,
        contract="implement",
        worker_instance="worker-a",
    )
    req2 = _req(dispatch_id="blocked-writer")
    queued = ledger.admit(
        req=req2,
        fingerprint=ledger.fingerprint(req2),
        execution_id=req2.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(req2),
        source_repo=repo,
        contract="implement",
        worker_instance="worker-a",
    )
    assert queued is not None
    assert queued.status == "queued"


def test_fingerprint_includes_read_only_ac8() -> None:
    """AC8: flipped read_only is a fingerprint conflict, identical payload idempotent."""
    ledger = CursorDispatchLedger.instance()
    base = _req(dispatch_id="fp-ro")
    writer = base
    reader = CursorDispatchRequest(**{**base.model_dump(), "read_only": True})
    assert ledger.fingerprint(writer) != ledger.fingerprint(reader)

    _admit(ledger, writer, source_repo="/repo")
    cached = ledger.admit(
        req=writer,
        fingerprint=ledger.fingerprint(writer),
        execution_id=writer.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(writer),
        source_repo="/repo",
        worker_instance="worker-a",
    )
    assert cached == _admission(writer)

    with pytest.raises(DispatchConflict):
        ledger.admit(
            req=reader,
            fingerprint=ledger.fingerprint(reader),
            execution_id=reader.execution_id,
            caller_agent=None,
            resolved_model="composer-2.5",
            admission=_admission(reader),
            source_repo="/repo",
            read_only=True,
            worker_instance="worker-a",
        )


def test_baseline_after_lease_ac9() -> None:
    """AC9: implement admit leaves wt_baseline NULL until set_wt_baseline."""
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="baseline-after")
    _admit(
        ledger,
        req,
        source_repo="/repo",
        contract="implement",
    )
    with _connect() as conn:
        row = conn.execute(
            "SELECT wt_baseline FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    assert row["wt_baseline"] is None

    ledger.set_wt_baseline(
        dispatch_id=req.dispatch_id, wt_baseline='{"path": "sha"}'
    )
    with _connect() as conn:
        row2 = conn.execute(
            "SELECT wt_baseline FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    assert row2["wt_baseline"] == '{"path": "sha"}'


def test_implement_vs_implement_queues_ac5() -> None:
    """AC5: second implement writer on same repo queues."""
    ledger = CursorDispatchLedger.instance()
    repo = "/mnt/torus/projects/universal-llm-gateway"
    _admit(
        ledger,
        _req(dispatch_id="impl-1"),
        source_repo=repo,
        contract="implement",
    )
    req2 = _req(dispatch_id="impl-2")
    queued = ledger.admit(
        req=req2,
        fingerprint=ledger.fingerprint(req2),
        execution_id=req2.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(req2),
        source_repo=repo,
        contract="implement",
        worker_instance="worker-a",
    )
    assert queued is not None
    assert queued.status == "queued"


def test_promote_next_queued_fifo_ac7() -> None:
    """AC7: promote advances FIFO head to admitted."""
    ledger = CursorDispatchLedger.instance()
    repo = "/repo"
    _admit(ledger, _req(dispatch_id="w1"), source_repo=repo, contract="implement")
    for dispatch_id in ("w2", "w3"):
        req = _req(dispatch_id=dispatch_id)
        ledger.admit(
            req=req,
            fingerprint=ledger.fingerprint(req),
            execution_id=req.execution_id,
            caller_agent=None,
            resolved_model="composer-2.5",
            admission=_admission(req),
            source_repo=repo,
            contract="implement",
            worker_instance="worker-a",
        )
    ledger.mark_terminal(dispatch_id="w1", terminal_status="completed")
    promoted = ledger.promote_next_queued(source_repo=repo, worker_instance="worker-a")
    assert promoted is not None
    assert promoted.dispatch_id == "w2"
    with _connect() as conn:
        row = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("w2",),
        ).fetchone()
    assert row["status"] == "admitted"


def test_queued_idempotent_replay_ac9() -> None:
    """AC9: replay of queued dispatch_id returns same ticket."""
    ledger = CursorDispatchLedger.instance()
    repo = "/repo"
    _admit(ledger, _req(dispatch_id="w1"), source_repo=repo, contract="implement")
    req2 = _req(dispatch_id="w2")
    first = ledger.admit(
        req=req2,
        fingerprint=ledger.fingerprint(req2),
        execution_id=req2.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(req2),
        source_repo=repo,
        contract="implement",
        worker_instance="worker-a",
    )
    replay = ledger.admit(
        req=req2,
        fingerprint=ledger.fingerprint(req2),
        execution_id=req2.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(req2),
        source_repo=repo,
        contract="implement",
        worker_instance="worker-a",
    )
    assert replay == first
    assert replay is not None
    assert replay.status == "queued"


def test_durable_queue_order_survives_restart_ac6() -> None:
    """AC6: FIFO order reconstructs after ledger singleton reset."""
    ledger = CursorDispatchLedger.instance()
    repo = "/repo"
    _admit(ledger, _req(dispatch_id="w1"), source_repo=repo, contract="implement")
    for dispatch_id in ("w2", "w3"):
        req = _req(dispatch_id=dispatch_id)
        ledger.admit(
            req=req,
            fingerprint=ledger.fingerprint(req),
            execution_id=req.execution_id,
            caller_agent=None,
            resolved_model="composer-2.5",
            admission=_admission(req),
            source_repo=repo,
            contract="implement",
            worker_instance="worker-a",
        )
    CursorDispatchLedger._instance = None
    ledger2 = CursorDispatchLedger.instance()
    ledger2.mark_terminal(dispatch_id="w1", terminal_status="completed")
    promoted = ledger2.promote_next_queued(source_repo=repo, worker_instance="worker-a")
    assert promoted is not None
    assert promoted.dispatch_id == "w2"


def test_stale_release_never_two_admitted_ac8_ac10() -> None:
    """AC8/AC10: stale release frees lease; promotion never double-admits."""
    ledger = CursorDispatchLedger.instance()
    repo = "/repo"
    _admit(ledger, _req(dispatch_id="stale-1"), source_repo=repo, contract="implement")
    req2 = _req(dispatch_id="stale-2")
    ledger.admit(
        req=req2,
        fingerprint=ledger.fingerprint(req2),
        execution_id=req2.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(req2),
        source_repo=repo,
        contract="implement",
        worker_instance="worker-a",
    )
    released = ledger.release_stale_writer(dispatch_id="stale-1")
    assert released == repo
    promoted = ledger.promote_next_queued(source_repo=repo, worker_instance="worker-a")
    assert promoted is not None
    assert promoted.dispatch_id == "stale-2"
    with _connect() as conn:
        active = conn.execute(
            "SELECT COUNT(*) AS n FROM cursor_sdk_dispatches "
            "WHERE source_repo=? AND status IN ('admitted','running')",
            (repo,),
        ).fetchone()
    assert active["n"] == 1


def test_dispatch_status_by_thread_absent() -> None:
    ledger = CursorDispatchLedger.instance()
    assert ledger.dispatch_status_by_thread(thread_id="missing-thread") is None


def test_dispatch_status_by_thread_queued() -> None:
    ledger = CursorDispatchLedger.instance()
    repo = "/repo"
    _admit(ledger, _req(dispatch_id="w1", thread_id="t-queued"), source_repo=repo, contract="implement")
    req2 = _req(dispatch_id="w2", thread_id="t-queued")
    ledger.admit(
        req=req2,
        fingerprint=ledger.fingerprint(req2),
        execution_id=req2.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(req2),
        source_repo=repo,
        contract="implement",
        worker_instance="worker-a",
    )
    row = ledger.dispatch_status_by_thread(thread_id="t-queued")
    assert row is not None
    assert row["dispatch_id"] == "w2"
    assert row["status"] == "queued"
    assert row["read_only"] is False
    assert row["queued_at"] is not None


def test_dispatch_status_by_thread_admitted() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="admitted-1", thread_id="t-admitted")
    _admit(ledger, req, source_repo="/repo", contract="implement", read_only=True)
    row = ledger.dispatch_status_by_thread(thread_id="t-admitted")
    assert row is not None
    assert row["dispatch_id"] == "admitted-1"
    assert row["status"] == "admitted"
    assert row["read_only"] is True


def test_dispatch_status_by_thread_running_includes_liveness_fields() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="running-1", thread_id="t-running")
    _admit(ledger, req, source_repo="/repo", contract="implement")
    ledger.mark_running(dispatch_id=req.dispatch_id)
    ledger.bump_heartbeat(dispatch_id=req.dispatch_id)
    row = ledger.dispatch_status_by_thread(thread_id="t-running")
    assert row is not None
    assert row["dispatch_id"] == "running-1"
    assert row["status"] == "running"
    assert row["execution_id"] == req.execution_id
    assert row["started_at"] is not None
    assert row["last_heartbeat_at"] is not None


def test_dispatch_status_endpoint_known_thread() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="endpoint-1", thread_id="t-endpoint")
    _admit(ledger, req, source_repo="/repo", contract="implement", read_only=True)
    ledger.mark_running(dispatch_id=req.dispatch_id)
    ledger.bump_heartbeat(dispatch_id=req.dispatch_id)
    app = create_app()
    client = TestClient(app)
    resp = client.get(
        "/api/v1/git/admin/dispatch-status",
        params={"thread_id": "t-endpoint"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dispatch_id"] == "endpoint-1"
    assert body["status"] == "running"
    assert body["read_only"] is True
    assert body["execution_id"] == req.execution_id
    assert body["started_at"] is not None
    assert body["last_heartbeat_at"] is not None
    assert "queued_at" in body


def test_dispatch_status_endpoint_unknown_thread() -> None:
    app = create_app()
    client = TestClient(app)
    resp = client.get(
        "/api/v1/git/admin/dispatch-status",
        params={"thread_id": "unknown-thread"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"thread_id": "unknown-thread", "status": None}


_SOURCE_REF = "todo:cursor-sdk-same-source-ref-dedupe"
_REPO = "/mnt/torus/projects/universal-llm-gateway"


def test_same_source_ref_second_implement_rejected_ac1() -> None:
    """AC1: twin implement same source_ref without force is rejected, not queued."""
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger,
        _req(dispatch_id="impl-a", thread_id="5601"),
        source_repo=_REPO,
        contract="implement",
        source_ref=_SOURCE_REF,
    )
    req2 = _req(dispatch_id="impl-b", thread_id="5602")
    with pytest.raises(SourceRefConflict) as exc_info:
        _admit(
            ledger,
            req2,
            source_repo=_REPO,
            contract="implement",
            source_ref=_SOURCE_REF,
        )
    err = exc_info.value
    assert err.holder_dispatch_id == "impl-a"
    assert err.holder_thread_id == "5601"
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("impl-b",),
        ).fetchone()
    assert row["n"] == 0


def test_same_source_ref_reject_includes_holder_ac2() -> None:
    """AC2: reject identifies in-flight holder dispatch_id and thread_id."""
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger,
        _req(dispatch_id="holder-1", thread_id="t-holder"),
        source_repo=_REPO,
        contract="implement",
        source_ref=_SOURCE_REF,
    )
    with pytest.raises(SourceRefConflict) as exc_info:
        _admit(
            ledger,
            _req(dispatch_id="dup-1", thread_id="t-dup"),
            source_repo=_REPO,
            contract="implement",
            source_ref=_SOURCE_REF,
        )
    err = exc_info.value
    assert err.holder_dispatch_id == "holder-1"
    assert err.holder_thread_id == "t-holder"


def test_different_source_ref_same_repo_still_queues_ac3() -> None:
    """AC3: different source_ref on same repo serializes via write-lease FIFO."""
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger,
        _req(dispatch_id="todo-a"),
        source_repo=_REPO,
        contract="implement",
        source_ref="todo:alpha",
    )
    queued = _admit(
        ledger,
        _req(dispatch_id="todo-b"),
        source_repo=_REPO,
        contract="implement",
        source_ref="todo:beta",
    )
    assert queued is not None
    assert queued.status == "queued"


def test_force_bypasses_same_ref_but_still_fifo_ac4() -> None:
    """AC4: force skips same-ref reject but still queues behind active repo lease."""
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger,
        _req(dispatch_id="holder"),
        source_repo=_REPO,
        contract="implement",
        source_ref=_SOURCE_REF,
    )
    forced = _admit(
        ledger,
        _req(dispatch_id="forced-twin"),
        source_repo=_REPO,
        contract="implement",
        source_ref=_SOURCE_REF,
        force=True,
    )
    assert forced is not None
    assert forced.status == "queued"
    with _connect() as conn:
        row = conn.execute(
            "SELECT status, source_ref FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            ("forced-twin",),
        ).fetchone()
    assert row["status"] == "queued"
    assert row["source_ref"] == _SOURCE_REF


def test_missing_source_ref_no_same_ref_reject_ac5() -> None:
    """AC5: unresolved source_ref does not trigger same-ref gate."""
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger,
        _req(dispatch_id="no-ref-1"),
        source_repo=_REPO,
        contract="implement",
        source_ref=None,
    )
    queued = _admit(
        ledger,
        _req(dispatch_id="no-ref-2"),
        source_repo=_REPO,
        contract="implement",
        source_ref=None,
    )
    assert queued is not None
    assert queued.status == "queued"


def test_completed_same_source_ref_allows_new_implement() -> None:
    ledger = CursorDispatchLedger.instance()
    req1 = _req(dispatch_id="done-1")
    _admit(
        ledger,
        req1,
        source_repo=_REPO,
        contract="implement",
        source_ref=_SOURCE_REF,
    )
    ledger.mark_terminal(dispatch_id=req1.dispatch_id, terminal_status="completed")
    assert (
        _admit(
            ledger,
            _req(dispatch_id="done-2"),
            source_repo=_REPO,
            contract="implement",
            source_ref=_SOURCE_REF,
        )
        is None
    )


def test_source_ref_persisted_on_admit_ac5() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger,
        _req(dispatch_id="persist-ref"),
        source_repo=_REPO,
        contract="implement",
        source_ref=_SOURCE_REF,
    )
    with _connect() as conn:
        row = conn.execute(
            "SELECT source_ref FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("persist-ref",),
        ).fetchone()
    assert row["source_ref"] == _SOURCE_REF


def test_concurrent_same_source_ref_exactly_one_non_terminal() -> None:
    """AC1 concurrency: interleaved twin admits leave exactly one non-terminal row."""
    ledger = CursorDispatchLedger.instance()
    repo = _REPO
    source_ref = "todo:concurrent-twin"
    barrier = threading.Barrier(2)
    outcomes: list[tuple[str, str]] = []

    def _attempt(dispatch_id: str) -> None:
        req = _req(dispatch_id=dispatch_id, thread_id=f"t-{dispatch_id}")
        try:
            barrier.wait(timeout=5)
            result = ledger.admit(
                req=req,
                fingerprint=ledger.fingerprint(req),
                execution_id=req.execution_id,
                caller_agent=None,
                resolved_model="composer-2.5",
                admission=_admission(req),
                source_repo=repo,
                contract="implement",
                worker_instance="worker-a",
                source_ref=source_ref,
            )
            label = "admitted" if result is None else str(result.status)
            outcomes.append((dispatch_id, label))
        except SourceRefConflict:
            outcomes.append((dispatch_id, "rejected"))

    threads = [
        threading.Thread(target=_attempt, args=("twin-a",)),
        threading.Thread(target=_attempt, args=("twin-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(outcomes) == 2
    assert sum(1 for _id, label in outcomes if label == "rejected") == 1
    assert sum(1 for _id, label in outcomes if label == "admitted") == 1
    with _connect() as conn:
        active = conn.execute(
            "SELECT COUNT(*) AS n FROM cursor_sdk_dispatches "
            "WHERE source_ref=? AND status IN ('queued','admitted','running')",
            (source_ref,),
        ).fetchone()
    assert active["n"] == 1


_WORK_KEY = "abc123workkey" * 4  # 64 hex-like


def test_same_work_key_consult_rejected() -> None:
    """L2 backstop: consult contract guards on work_key."""
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger,
        _req(dispatch_id="consult-h1", thread_id="t-h1"),
        source_repo=_REPO,
        contract="consult",
        work_key=_WORK_KEY,
    )
    with pytest.raises(SourceRefConflict) as exc_info:
        _admit(
            ledger,
            _req(dispatch_id="consult-h2", thread_id="t-h2"),
            source_repo=_REPO,
            contract="consult",
            work_key=_WORK_KEY,
        )
    err = exc_info.value
    assert err.work_key == _WORK_KEY
    assert err.holder_dispatch_id == "consult-h1"


def test_same_work_key_consult_reject_includes_holder() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger,
        _req(dispatch_id="wk-holder", thread_id="t-wk"),
        source_repo=_REPO,
        contract="consult",
        work_key=_WORK_KEY,
    )
    with pytest.raises(SourceRefConflict) as exc_info:
        _admit(
            ledger,
            _req(dispatch_id="wk-dup", thread_id="t-dup"),
            source_repo=_REPO,
            contract="consult",
            work_key=_WORK_KEY,
        )
    err = exc_info.value
    assert err.holder_dispatch_id == "wk-holder"
    assert err.holder_thread_id == "t-wk"


def _insert_status_row(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    status: str,
    thread_id: str = "t1",
    source_repo: str = _REPO,
) -> None:
    req = _req(dispatch_id=dispatch_id, thread_id=thread_id)
    wf = ledger.work_fingerprint(req)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, "
            " message_present, status, record_json, source_repo, lease_key, "
            " read_only, work_fingerprint) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dispatch_id,
                ledger.fingerprint(req),
                thread_id,
                req.execution_id,
                "composer-2.5",
                1,
                status,
                json.dumps({"model": "cursor/composer-2.5", "message": req.message}),
                source_repo,
                source_repo,
                0,
                wf,
            ),
        )


def test_cancel_queued_cas() -> None:
    ledger = CursorDispatchLedger.instance()
    _insert_status_row(ledger, dispatch_id="q-cancel", status="queued")
    result = ledger.cancel_dispatch(
        dispatch_id="q-cancel", cancel_reason="test", cancelled_by="operator"
    )
    assert result.outcome == "cancelled"
    assert result.row["status"] == "cancelled"
    assert result.row["terminal_status"] == "cancelled"
    assert result.needs_promote is False
    record = json.loads(result.row["record_json"])
    assert record["cancel_reason"] == "test"
    assert record["cancelled_by"] == "operator"
    from services.git_integration_worker.cursor_dispatch_ledger import DispatchNotFound

    with pytest.raises(DispatchNotFound):
        ledger.cancel_dispatch(dispatch_id="missing-id")


def test_cancel_queued_wrong_status_noop_race() -> None:
    ledger = CursorDispatchLedger.instance()
    _insert_status_row(ledger, dispatch_id="running-x", status="running")
    with pytest.raises(Exception) as exc_info:
        ledger.cancel_dispatch(dispatch_id="running-x")
    from services.git_integration_worker.cursor_dispatch_ledger import (
        NotCancellableRunning,
    )

    assert isinstance(exc_info.value, NotCancellableRunning)


def test_cancel_admitted_promotes_fifo() -> None:
    ledger = CursorDispatchLedger.instance()
    _insert_status_row(ledger, dispatch_id="admitted-h", status="admitted")
    _insert_status_row(ledger, dispatch_id="queued-next", status="queued")
    result = ledger.cancel_dispatch(dispatch_id="admitted-h")
    assert result.outcome == "cancelled"
    assert result.needs_promote is True
    promoted = ledger.promote_next_queued(source_repo=_REPO, worker_instance="live")
    assert promoted is not None
    assert promoted.dispatch_id == "queued-next"


def test_cancel_running_refuses_typed() -> None:
    ledger = CursorDispatchLedger.instance()
    _insert_status_row(ledger, dispatch_id="run-x", status="running")
    from services.git_integration_worker.cursor_dispatch_ledger import (
        NotCancellableRunning,
    )

    with pytest.raises(NotCancellableRunning) as exc_info:
        ledger.cancel_dispatch(dispatch_id="run-x")
    err = exc_info.value
    assert err.status == "running"
    assert err.dispatch_id == "run-x"


def test_cancel_idempotent_replay() -> None:
    ledger = CursorDispatchLedger.instance()
    _insert_status_row(ledger, dispatch_id="cancelled-x", status="queued")
    first = ledger.cancel_dispatch(dispatch_id="cancelled-x")
    second = ledger.cancel_dispatch(dispatch_id="cancelled-x")
    assert first.outcome == "cancelled"
    assert second.outcome == "already_terminal"
    assert second.row["status"] == "cancelled"
    req = _req(dispatch_id="cancelled-x")
    replay = ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(req),
    )
    assert replay is not None
    assert replay.status == "cancelled"


def test_work_fingerprint_refuses_active_peer() -> None:
    ledger = CursorDispatchLedger.instance()
    shared_message = "same-content-payload"
    req1 = _req(dispatch_id="fp-hold", thread_id="t-fp", message=shared_message)
    _admit(ledger, req1, source_repo=_REPO, contract="consult")
    req2 = _req(dispatch_id="fp-dup", thread_id="t-fp", message=shared_message)
    with pytest.raises(SourceRefConflict) as exc_info:
        _admit(ledger, req2, source_repo=_REPO, contract="consult")
    err = exc_info.value
    assert err.work_fingerprint is not None
    assert err.holder_dispatch_id == "fp-hold"


def test_work_fingerprint_force_bypasses() -> None:
    ledger = CursorDispatchLedger.instance()
    shared_message = "force-shared-content"
    req1 = _req(dispatch_id="fp-h1", thread_id="t-force", message=shared_message)
    _admit(ledger, req1, source_repo=_REPO, contract="consult")
    req2 = _req(dispatch_id="fp-h2", thread_id="t-force-2", message=shared_message)
    result = _admit(ledger, req2, source_repo=_REPO, contract="consult", force=True)
    assert result is None or result.status in ("admitted", "queued")


def test_work_fingerprint_null_skips() -> None:
    ledger = CursorDispatchLedger.instance()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, "
            " message_present, status, record_json, source_repo, read_only) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "null-fp",
                "fp",
                "t-null",
                "exec-null",
                "composer-2.5",
                0,
                "admitted",
                "{}",
                _REPO,
                0,
            ),
        )
    req = CursorDispatchRequest(
        thread_id="t-other",
        model="cursor/composer-2.5",
        dispatch_id="null-fp2",
        execution_id="exec-null-fp2",
        message="only-message",
    )
    assert ledger.work_fingerprint(req) is not None
    result = _admit(ledger, req, source_repo=_REPO, contract="consult")
    assert result is None or result.status == "admitted"


def test_second_live_admit_on_same_thread_raises_occupied() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger,
        _req(dispatch_id="occ-hold", thread_id="t-occ"),
        source_repo=_REPO,
        contract="consult",
    )
    with pytest.raises(WorkerThreadOccupied) as exc_info:
        _admit(
            ledger,
            _req(dispatch_id="occ-dup", thread_id="t-occ", message="other"),
            source_repo="/other-repo",
            contract="consult",
        )
    err = exc_info.value
    assert err.holder_dispatch_id == "occ-hold"
    assert err.holder_thread_id == "t-occ"


def test_work_fingerprint_allows_after_terminal() -> None:
    ledger = CursorDispatchLedger.instance()
    shared_message = "after-terminal-shared"
    req1 = _req(dispatch_id="term-h", thread_id="t-after", message=shared_message)
    _admit(ledger, req1, source_repo=_REPO, contract="consult")
    ledger.mark_terminal(dispatch_id="term-h", terminal_status="failed")
    req2 = _req(dispatch_id="term-h2", thread_id="t-after", message=shared_message)
    result = _admit(ledger, req2, source_repo=_REPO, contract="consult")
    assert result is None or result.status in ("admitted", "queued")


def test_fresh_ledger_schema_includes_cancelled_and_fingerprint(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='cursor_sdk_dispatches'"
        ).fetchone()
        cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(cursor_sdk_dispatches)")
        }
    assert ddl is not None
    assert "'cancelled'" in ddl["sql"]
    assert "work_fingerprint" in cols


def test_terminal_row_bytes_stable_across_reopen(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="term-persist")
    _admit(ledger, req, source_repo=_REPO, contract="consult")
    ledger.mark_terminal(dispatch_id="term-persist", terminal_status="completed")
    with _connect() as conn:
        before = conn.execute(
            "SELECT dispatch_id, status, terminal_status, terminal_at, record_json "
            "FROM cursor_sdk_dispatches WHERE dispatch_id='term-persist'"
        ).fetchone()
    CursorDispatchLedger._instance = None
    ledger2 = CursorDispatchLedger.instance()
    with ledger2._connect() as conn:
        after = conn.execute(
            "SELECT dispatch_id, status, terminal_status, terminal_at, record_json "
            "FROM cursor_sdk_dispatches WHERE dispatch_id='term-persist'"
        ).fetchone()
    assert dict(before) == dict(after)


_CONDUCTOR_WORK_KEY = "todo:conductor-open-fixture"


def test_open_conductor_holder_visible_in_ledger() -> None:
    """Phase 2: conductor work_key + packet_kind is ledger-visible."""
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="cond-open-1", thread_id="t-cond")
    _admit(
        ledger,
        req,
        source_repo=_REPO,
        contract="light-bounded",
        work_key=_CONDUCTOR_WORK_KEY,
    )
    ledger.merge_record_json(
        dispatch_id="cond-open-1",
        patch={"packet_kind": "conductor"},
    )
    with ledger._connect() as conn:
        holder = find_open_conductor_holder_conn(
            conn,
            work_key=_CONDUCTOR_WORK_KEY,
        )
    assert holder is not None
    assert holder.dispatch_id == "cond-open-1"
    assert holder.thread_id == "t-cond"


def test_should_block_top_level_implement_not_nested() -> None:
    assert should_block_implement_for_open_conductor(
        contract="implement",
        nest_under=None,
        work_key=_CONDUCTOR_WORK_KEY,
    )
    assert not should_block_implement_for_open_conductor(
        contract="implement",
        nest_under="parent-dispatch",
        work_key=_CONDUCTOR_WORK_KEY,
    )
