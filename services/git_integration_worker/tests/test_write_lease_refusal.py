"""Write-lease refusal fence — R1 L1 (25956 / release-before-admit)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.config import WorkerConfig
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    ResumeBehindCaller,
    WriteLeaseHeld,
    _connect,
)
from services.git_integration_worker.cursor_sdk_nest_depth import NestParentNotLive
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _req(**overrides: object) -> CursorDispatchRequest:
    """Distinct message per dispatch_id.

    ``work_fingerprint`` is content identity and excludes ``dispatch_id``
    (``CursorDispatchLedger.work_fingerprint``). These fixtures are distinct
    writers on one lease, so the body must differ or admit raises
    ``SourceRefConflict`` before the lease gate.
    """
    dispatch_id = str(overrides.get("dispatch_id", "disp-1"))
    base = {
        "thread_id": "t1",
        "model": "cursor/composer-2.5",
        "dispatch_id": "disp-1",
        "execution_id": "exec-disp-1",
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


def _admit(
    ledger: CursorDispatchLedger,
    req: CursorDispatchRequest,
    *,
    source_repo: str = "/repo",
    lease_key: str | None = None,
    read_only: bool = False,
    nest_under: str | None = None,
    refuse_if_lease_held: bool = False,
) -> CursorDispatchResponse | None:
    return ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(req),
        source_repo=source_repo,
        lease_key=lease_key or source_repo,
        contract="implement",
        worker_instance="worker-a",
        read_only=read_only,
        nest_under=nest_under,
        refuse_if_lease_held=refuse_if_lease_held,
    )


def test_ac1_refuse_if_lease_held_raises_and_inserts_no_row() -> None:
    """AC1: typed refusal inside admit txn — zero rows for dispatch_id."""
    ledger = CursorDispatchLedger.instance()
    key = "/repo"

    _admit(ledger, _req(dispatch_id="holder"), lease_key=key)
    child = _req(dispatch_id="child-would-queue")

    with pytest.raises(WriteLeaseHeld) as exc_info:
        _admit(
            ledger,
            child,
            lease_key=key,
            refuse_if_lease_held=True,
        )
    assert exc_info.value.holder_dispatch_id == "holder"

    with _connect() as conn:
        row = conn.execute(
            "SELECT dispatch_id FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("child-would-queue",),
        ).fetchone()
    assert row is None


def test_ac2_without_flag_retains_queued_behavior() -> None:
    """AC2: back-compat — no flag ⇒ silent queue ticket."""
    ledger = CursorDispatchLedger.instance()
    key = "/repo"

    _admit(ledger, _req(dispatch_id="holder-2"), lease_key=key)
    queued = _admit(ledger, _req(dispatch_id="peer-2"), lease_key=key)
    assert queued is not None
    assert queued.status == "queued"

    with _connect() as conn:
        row = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("peer-2",),
        ).fetchone()
    assert row is not None
    assert row["status"] == "queued"


def test_ac4_legitimate_nest_still_admits_and_parks_parent() -> None:
    """AC4: nest_under == live holder — fence did not break B1 path."""
    ledger = CursorDispatchLedger.instance()
    key = "/repo"

    _admit(ledger, _req(dispatch_id="parent-nest"), lease_key=key)
    result = _admit(
        ledger,
        _req(dispatch_id="child-nest"),
        lease_key=key,
        nest_under="parent-nest",
        refuse_if_lease_held=True,
    )
    assert result is None

    with _connect() as conn:
        parent = conn.execute(
            "SELECT status, park_child_dispatch_id FROM cursor_sdk_dispatches "
            "WHERE dispatch_id='parent-nest'"
        ).fetchone()
        child = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id='child-nest'"
        ).fetchone()
    assert parent is not None
    assert parent["status"] == "parked_waiting"
    assert parent["park_child_dispatch_id"] == "child-nest"
    assert child is not None
    assert child["status"] == "admitted"


def test_ac5_read_only_admits_while_write_holder_live() -> None:
    """AC5: read_only path untouched — no queue, no refusal."""
    ledger = CursorDispatchLedger.instance()
    key = "/repo"

    _admit(ledger, _req(dispatch_id="write-holder"), lease_key=key)
    result = _admit(
        ledger,
        _req(dispatch_id="read-only-peer", thread_id="t-read"),
        lease_key=key,
        read_only=True,
        refuse_if_lease_held=True,
    )
    assert result is None

    with _connect() as conn:
        row = conn.execute(
            "SELECT status, read_only FROM cursor_sdk_dispatches "
            "WHERE dispatch_id='read-only-peer'"
        ).fetchone()
    assert row is not None
    assert row["status"] == "admitted"
    assert row["read_only"] == 1


def test_refuse_rejects_stale_nest_under() -> None:
    """nest_under mismatch still raises NestParentNotLive even with refuse flag."""
    ledger = CursorDispatchLedger.instance()
    key = "/repo"

    _admit(ledger, _req(dispatch_id="live-holder"), lease_key=key)
    with pytest.raises(NestParentNotLive):
        _admit(
            ledger,
            _req(dispatch_id="bad-nest"),
            lease_key=key,
            nest_under="wrong-parent",
            refuse_if_lease_held=True,
        )


def _row(dispatch_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT status, read_only, park_child_dispatch_id, park_resumed_by "
            "FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    return None if row is None else dict(row)


def test_caller_polls_own_queued_child_is_refused() -> None:
    """Caller-polls-own-queued-child: no child row, so there is nothing to poll.

    A resume_of whose caller_dispatch_id is the live non-read_only holder of
    the same lease raises ResumeBehindCaller before INSERT. A foreign caller
    still queues. A read-only resume of the same caller still admits.
    """
    ledger = CursorDispatchLedger.instance()
    key = "/repo"

    assert _admit(ledger, _req(dispatch_id="holder-live"), lease_key=key) is None
    holder = _row("holder-live")
    assert holder is not None
    assert holder["status"] == "admitted"

    child = _req(
        dispatch_id="child-self",
        resume_of="terminal-parent",
        caller_dispatch_id="holder-live",
    )
    with pytest.raises(ResumeBehindCaller) as exc_info:
        _admit(ledger, child, lease_key=key)
    assert exc_info.value.holder_dispatch_id == "holder-live"
    hint = exc_info.value.fix_hint
    assert "exit-then-resume_of" in hint
    assert "nest_under=holder-live" in hint
    assert "omit resume_of" in hint
    assert "does not inherit the agent store" in hint
    assert "Do not send nest_under and resume_of together" in hint

    assert _row("child-self") is None
    holder_after = _row("holder-live")
    assert holder_after is not None
    assert holder_after["status"] == "admitted"
    assert holder_after["park_child_dispatch_id"] is None

    foreign = _admit(
        ledger,
        _req(
            dispatch_id="child-foreign",
            resume_of="terminal-parent",
            caller_dispatch_id="other-seat",
        ),
        lease_key=key,
    )
    assert foreign is not None
    assert foreign.status == "queued"
    foreign_row = _row("child-foreign")
    assert foreign_row is not None
    assert foreign_row["status"] == "queued"

    read_only = _admit(
        ledger,
        _req(
            dispatch_id="child-ro",
            thread_id="t-read",
            resume_of="terminal-parent",
            caller_dispatch_id="holder-live",
        ),
        lease_key=key,
        read_only=True,
    )
    assert read_only is None
    ro_row = _row("child-ro")
    assert ro_row is not None
    assert ro_row["status"] == "admitted"
    assert ro_row["read_only"] == 1


def test_resume_of_without_caller_dispatch_id_stays_queued() -> None:
    """Missing caller_dispatch_id keeps today's queue (F3, beside the helper)."""
    ledger = CursorDispatchLedger.instance()
    key = "/repo"

    _admit(ledger, _req(dispatch_id="holder-unstamped"), lease_key=key)
    queued = _admit(
        ledger,
        _req(dispatch_id="child-unstamped", resume_of="terminal-parent"),
        lease_key=key,
    )
    assert queued is not None
    assert queued.status == "queued"
    row = _row("child-unstamped")
    assert row is not None
    assert row["status"] == "queued"


@pytest.mark.asyncio
async def test_route_resume_behind_caller_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """422 leaves no child row and does not park or retarget the holder.

    The route calls the same lane-B mint rollback hook as WriteLeaseHeld.
    resume_of binding is ``resumed`` (nothing minted), so the hook runs with
    minted_lane_b false and the holder's lease row stays admitted.
    """
    source = tmp_path / "source"
    source.mkdir()
    worktrees = tmp_path / "worktrees"
    worktrees.mkdir()
    dispatch_ws = tmp_path / "dispatch_ws"
    dispatch_ws.mkdir()
    cfg = WorkerConfig(
        host="127.0.0.1",
        port=8091,
        source_repo=source,
        worktree_root=worktrees,
        dispatch_workspace=dispatch_ws,
        green_gate_cmd=["true"],
    )
    ledger = CursorDispatchLedger.instance()
    parent_lease = str(source.resolve())
    child_lease = str(dispatch_ws.resolve())
    assert (
        _admit(
            ledger,
            _req(dispatch_id="parent-term", thread_id="t-parent"),
            lease_key=parent_lease,
            source_repo=parent_lease,
        )
        is None
    )
    assert (
        _admit(
            ledger,
            _req(dispatch_id="holder-live", thread_id="t-holder"),
            lease_key=child_lease,
            source_repo=parent_lease,
        )
        is None
    )

    from services.git_integration_worker.routes import cursor_sdk as route_mod

    monkeypatch.setattr(
        route_mod,
        "validate_dispatch_context",
        lambda *_a, **_k: {"setting_sources": ["projectSettings"]},
    )
    monkeypatch.setattr(route_mod, "reject_resume_if_ineligible", lambda _req: None)
    rollback_calls: list[dict[str, object]] = []
    real_rollback = route_mod._rollback_lane_b_mint_if_needed

    async def _spy(**kwargs: object) -> None:
        rollback_calls.append(kwargs)
        await real_rollback(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(route_mod, "_rollback_lane_b_mint_if_needed", _spy)

    child = _req(
        dispatch_id="child-route",
        thread_id="t-child",
        resume_of="parent-term",
        caller_dispatch_id="holder-live",
        handoff_contract="freeform",
    )
    response = await route_mod.admit_cursor_dispatch(
        child,
        cfg=cfg,
        controller=WorkAdmissionController(
            ledger=ledger,
            worker_id="w",
            pid=0,
            worker_started_at=datetime.now(UTC).isoformat(),
        ),
    )
    assert response.status_code == 422
    body = json.loads(bytes(response.body).decode())
    assert body.get("code") == "CURSOR_RESUME_BEHIND_CALLER"
    assert body.get("retryable") is False
    data = body.get("data") or {}
    assert data.get("validation_stage") == "ledger_resume_behind_caller"
    hint = str(data.get("fix_hint") or "")
    assert "exit-then-resume_of" in hint
    assert "nest_under=holder-live" in hint
    assert "does not inherit the agent store" in hint
    assert _row("child-route") is None
    holder = _row("holder-live")
    assert holder is not None
    assert holder["status"] == "admitted"
    assert holder["park_child_dispatch_id"] is None
    assert rollback_calls
    assert rollback_calls[-1]["dispatch_id"] == "child-route"
    assert rollback_calls[-1]["minted_lane_b"] is False
    assert rollback_calls[-1]["reason"] == "resume_behind_caller"
