"""AC1–AC4: durable cursor-sdk dispatch ledger."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    DispatchConflict,
    _connect,
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
    base = {
        "thread_id": "t1",
        "model": "cursor/composer-2.5",
        "dispatch_id": "disp-ac1",
        "execution_id": "exec-disp-ac1",
        "message": "hello",
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

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _ok_outcome)

    await route_mod._run_sdk_dispatch(
        req=req,
        source_repo=route_mod._CONFIG.source_repo,
        bus=bus,
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

    # Simulate _isolated_dispatch_home swapping HOME for the bridge window.
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
