"""Worker-side admission + cooperative-drain tests (Phase 1).

Covers the ``WorkAdmissionController`` contract for
``task:git-worker-event-driven-drain``: admission rejection while draining,
in-flight completion across a drain, the synchronous admit/drain TOCTOU
guarantee, exactly-once ``git_worker.drain.completed`` on the 1->0 transition,
orphan exclusion, the no-bare-``create_task`` route invariant, and
``begin_drain`` idempotency. The unchanged FIFO gate serialization (AC-10) is
covered by ``test_gate_serialization.py``.
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker import git_worker_drain_events as drain_events
from services.git_integration_worker.admission import (
    Draining503,
    WorkAdmissionController,
)
from services.git_integration_worker.app import create_app
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

_INTEGRATE_BODY = {
    "arc": "x",
    "phase": "p",
    "worktree_path": "/tmp/wt",
    "approval": "ok",
    "expected_diff_sha256": "a" * 64,
    "remove_worktree": False,
}


@pytest.fixture(autouse=True)
def _reset_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Capture drain-signal emissions instead of publishing them.

    Patches the ``emit_*`` wrappers (which sit above ``record``), so a test that
    begins a drain never depends on whether ``mcp_events`` or a UDS publisher is
    wired in this environment.
    """
    started: list[dict] = []
    completed: list[dict] = []
    rejected: list[dict] = []
    monkeypatch.setattr(
        drain_events, "emit_drain_started", lambda **k: started.append(k)
    )
    monkeypatch.setattr(
        drain_events, "emit_drain_completed", lambda **k: completed.append(k)
    )
    monkeypatch.setattr(
        drain_events, "emit_admission_rejected", lambda **k: rejected.append(k)
    )
    return SimpleNamespace(started=started, completed=completed, rejected=rejected)


def _controller() -> WorkAdmissionController:
    return WorkAdmissionController(
        ledger=CursorDispatchLedger.instance(),
        worker_id="test-worker",
        pid=1234,
        worker_started_at="2026-01-01T00:00:00+00:00",
    )


def _admit_ledger_dispatch(ledger: CursorDispatchLedger, dispatch_id: str) -> None:
    """Admit + mark-running a dispatch in the ledger DB without a live task."""
    req = CursorDispatchRequest(
        thread_id="t1",
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        message="hello",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id="t1",
            model_id="composer-2.5",
        ),
    )
    ledger.mark_running(dispatch_id=dispatch_id)


# --------------------------------------------------------------------- AC-1
def test_admit_rejected_when_draining(events: SimpleNamespace) -> None:
    """Mutating routes 503 while draining; read-only routes still serve 200."""
    app = create_app()
    controller = _controller()
    app.state.admission_controller = controller
    controller.begin_drain(
        reason="test", intent_id="i1", drain_epoch=controller.next_epoch()
    )
    client = TestClient(app)

    resp = client.post("/api/v1/git/integrate", json=_INTEGRATE_BODY)
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == "GIT_WORKER_DRAINING"
    assert body["retryable"] is True
    assert resp.headers.get("Retry-After")
    assert any(r["route"] == "/api/v1/git/integrate" for r in events.rejected)

    state = client.get("/api/v1/git/admin/drain-state")
    assert state.status_code == 200
    assert state.json()["draining"] is True


# --------------------------------------------------------------------- AC-2
def test_inflight_integrate_completes_during_drain(events: SimpleNamespace) -> None:
    """A running integrate is never force-aborted by a drain begun mid-flight."""
    controller = _controller()
    ticket = controller.try_admit(
        "git_integrate", op_id="op1", route="/api/v1/git/integrate"
    )
    ticket.mark_running()
    assert controller.active_count() == 1

    controller.begin_drain(
        reason="r", intent_id="i1", drain_epoch=controller.next_epoch()
    )
    # The running ticket survives the drain: still counted, still running.
    assert controller.active_count() == 1
    assert ticket.state == "running"
    assert events.completed == []

    # It completes on its own terms; only then does in-flight work drop to zero.
    controller.close_ticket("op1", terminal_status="completed")
    assert controller.active_count() == 0
    assert len(events.completed) == 1


# --------------------------------------------------------------------- AC-3
@pytest.mark.asyncio
async def test_dispatch_ticket_closed_after_terminal(events: SimpleNamespace) -> None:
    """``_close_ticket_after`` closes the dispatch ticket only once the gated
    coro has fully returned (i.e. after ``mark_terminal``), so
    ``drain.completed`` cannot fire until the dispatch is genuinely done.
    """
    from services.git_integration_worker.routes.cursor_sdk import _close_ticket_after

    controller = _controller()
    ticket = controller.try_admit(
        "cursor_sdk", op_id="d1", route="/api/v1/cursor/dispatch"
    )
    ticket.mark_running()
    controller.begin_drain(
        reason="r", intent_id="i1", drain_epoch=controller.next_epoch()
    )

    completed_during_body: list[int] = []

    async def fake_gated() -> None:
        # Mid-dispatch: still in flight, so drain.completed must NOT have fired.
        assert controller.active_count() == 1
        completed_during_body.append(len(events.completed))

    await _close_ticket_after(fake_gated(), controller=controller, op_id="d1")

    assert completed_during_body == [0]
    assert controller.active_count() == 0
    assert len(events.completed) == 1


# --------------------------------------------------------------------- AC-4
def test_toctou_admit_then_drain_counts_or_rejects(events: SimpleNamespace) -> None:
    """The load-bearing admit/drain TOCTOU guarantee.

    An op admitted just before a drain is counted **synchronously at
    admission** — while still ``pending``, before it ever marks running — so a
    drain that begins immediately after can never find it invisible. A fresh
    admission after the drain is rejected. This test fails if ``try_admit``
    defers counting to ``mark_running`` (the race-admitted op would read as 0)
    or if ``begin_drain``'s snapshot were taken before that op was counted.
    """
    controller = _controller()

    ticket = controller.try_admit(
        "git_integrate", op_id="op1", route="/api/v1/git/integrate"
    )
    # Counted immediately, while still pending (NOT yet marked running).
    assert ticket.state == "pending"
    assert controller.active_count() == 1
    assert any(o["op_id"] == "op1" for o in controller.active_ops())

    snapshot = controller.begin_drain(
        reason="r", intent_id="i1", drain_epoch=controller.next_epoch()
    )
    # The race-admitted op remains visible to the drain and its snapshot.
    assert controller.active_count() == 1
    assert snapshot["active_count"] == 1
    assert any(o["op_id"] == "op1" for o in snapshot["active_ops"])
    # A pending ticket admitted before the drain aborts on its post-wait recheck.
    assert ticket.should_proceed() is False

    # A new admission after the drain is rejected outright.
    with pytest.raises(Draining503):
        controller.try_admit(
            "git_integrate", op_id="op2", route="/api/v1/git/integrate"
        )
    assert len(events.rejected) == 1


# --------------------------------------------------------------------- AC-5
def test_drain_completed_emitted_once_on_1_to_0(events: SimpleNamespace) -> None:
    """``drain.completed`` fires exactly once, on the 1->0 transition."""
    controller = _controller()
    ticket = controller.try_admit("git_integrate", op_id="op1", route="r")
    ticket.mark_running()
    controller.begin_drain(
        reason="r", intent_id="i1", drain_epoch=controller.next_epoch()
    )
    assert events.completed == []

    controller.close_ticket("op1", terminal_status="completed")
    assert len(events.completed) == 1

    # Idempotent: re-closing / re-checking the same epoch never re-emits.
    controller.close_ticket("op1", terminal_status="completed")
    controller._maybe_emit_drain_completed()
    assert len(events.completed) == 1


# --------------------------------------------------------------------- AC-6
def test_drain_state_shape(events: SimpleNamespace) -> None:
    """``drain_state`` carries the full snapshot incl. worker generation id."""
    controller = _controller()
    state = controller.drain_state()
    assert set(state) == {
        "draining",
        "drain_epoch",
        "intent_id",
        "worker_id",
        "pid",
        "worker_started_at",
        "active_count",
        "active_ops",
        "deadline_at",
        "drain_started_at",
        "drain_started_monotonic",
    }
    assert state["draining"] is False
    assert state["active_count"] == 0
    assert state["deadline_at"] is None

    controller.begin_drain(reason="r", intent_id="i9", drain_epoch=5, deadline_s=30.0)
    after = controller.drain_state()
    assert after["draining"] is True
    assert after["drain_epoch"] == 5
    assert after["intent_id"] == "i9"
    assert after["deadline_at"] is not None
    assert after["drain_started_at"] is not None
    assert isinstance(after["drain_started_monotonic"], float)


# --------------------------------------------------------------------- AC-7
def test_orphan_dispatch_not_counted() -> None:
    """A 'running' ledger row with no live task is an orphan, excluded from the
    active count so a crashed dispatch cannot wedge a drain open.
    """
    controller = _controller()
    ledger = CursorDispatchLedger.instance()
    _admit_ledger_dispatch(ledger, "orphan-1")

    assert ledger.active_snapshot()["running"] == 0
    assert controller.active_count() == 0
    assert controller.active_ops() == []


# --------------------------------------------------------------------- AC-8
def test_no_direct_create_task_in_routes() -> None:
    """Background work in routes/ must go through ``create_tracked_task`` so it
    is drain-tracked; a bare ``asyncio.create_task`` would be untracked.
    """
    from services.git_integration_worker import routes

    routes_dir = pathlib.Path(routes.__file__).resolve().parent
    offenders = [
        py.name
        for py in sorted(routes_dir.glob("*.py"))
        if "asyncio.create_task" in py.read_text(encoding="utf-8")
    ]
    assert offenders == [], (
        "routes/ must spawn background work via controller.create_tracked_task, "
        f"not bare asyncio.create_task; offenders: {offenders}"
    )


# --------------------------------------------------------------------- AC-9
def test_begin_drain_idempotent(events: SimpleNamespace) -> None:
    """Re-driving ``begin_drain`` with the same intent+epoch does not re-emit."""
    controller = _controller()
    epoch = controller.next_epoch()
    first = controller.begin_drain(reason="r", intent_id="i1", drain_epoch=epoch)
    n_started = len(events.started)

    second = controller.begin_drain(reason="r", intent_id="i1", drain_epoch=epoch)
    assert len(events.started) == n_started  # no second drain.started
    assert first["drain_epoch"] == second["drain_epoch"] == epoch
    assert second["draining"] is True
