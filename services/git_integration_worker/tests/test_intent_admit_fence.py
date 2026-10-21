"""a:39913 — a pending restart intent queues the next admit on its lane.

The armed window used to start that admit, so lane B stayed busy and the
idle boundary moved. ``force`` is not a bypass. Read-only stays unqueued
because the write-lease promoter would never rehome it.
"""

from __future__ import annotations

from typing import Any

import pytest

from scripts.model_manager.ui.controller.intent_admit_fence import (
    blocked_admit_lanes,
    pending_intent_queues_admit,
    pending_service_intent_status,
    queue_kw_for_pending_intent,
)
from scripts.model_manager.ui.controller.restart_intent_states import (
    STATUS_FORCE_REQUESTED,
    STATUS_PENDING_DRAIN,
)
from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

_REPO = "/repo/universal-llm-gateway"


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _req(**overrides: object) -> CursorDispatchRequest:
    dispatch_id = str(overrides.get("dispatch_id", "disp-fence"))
    base: dict[str, object] = {
        "thread_id": f"t-{dispatch_id}",
        "model": "cursor/composer-2.5",
        "dispatch_id": dispatch_id,
        "execution_id": f"exec-{dispatch_id}",
        "message": f"hello-{dispatch_id}",
        "lane": "B",
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
    lease_key: str,
    force: bool = False,
    queue_for_pending_intent: bool = False,
    nest_under: str | None = None,
) -> CursorDispatchResponse | None:
    return ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(req),
        source_repo=_REPO,
        lease_key=lease_key,
        contract="implement",
        worker_instance="test-worker",
        force=force,
        queue_for_pending_intent=queue_for_pending_intent,
        nest_under=nest_under,
    )


def _status(ledger: CursorDispatchLedger, dispatch_id: str) -> str:
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    assert row is not None
    return str(row["status"])


def test_pending_intent_queues_next_admit_on_that_lane() -> None:
    """Lane B is occupied and the intent is pending: the next B admit queues.

    Different lease keys so this is not the write-lease queue. ``force=True``
    still queues — the fence does not add a force bypass. A free lease on
    the same lane without the fence would start (control admit on lane A).
    """
    ledger = CursorDispatchLedger.instance()
    holder = _req(dispatch_id="holder-b")
    assert _admit(ledger, holder, lease_key="/wt/holder") is None
    ledger.mark_running(dispatch_id=holder.dispatch_id)
    assert ledger.occupied_admit_lanes() == {"B"}

    nxt = _req(dispatch_id="next-b")
    queued = _admit(
        ledger,
        nxt,
        lease_key="/wt/next",
        force=True,
        queue_for_pending_intent=True,
    )
    assert queued is not None
    assert queued.status == "queued"
    assert queued.admitted is False
    assert _status(ledger, nxt.dispatch_id) == "queued"
    assert ledger.occupied_admit_lanes() == {"B"}

    other = _req(dispatch_id="next-a", lane="A")
    assert (
        queue_kw_for_pending_intent(
            service="git_integration_worker",
            intent_status=STATUS_PENDING_DRAIN,
            occupied_lanes={"B"},
            admit_lane="A",
        )
        == {}
    )
    assert _admit(ledger, other, lease_key="/wt/lane-a") is None
    assert _status(ledger, other.dispatch_id) == "admitted"


def test_mcp_pending_blocks_mcp_lane_not_giw() -> None:
    """A pending mcp intent queues the mcp lane and leaves cursor lane B alone."""
    assert blocked_admit_lanes(service="mcp", intent_status=STATUS_PENDING_DRAIN) == {
        "mcp"
    }
    assert pending_intent_queues_admit(
        blocked_lanes=blocked_admit_lanes(
            service="mcp", intent_status=STATUS_PENDING_DRAIN
        ),
        admit_lane="mcp",
    )
    assert not pending_intent_queues_admit(
        blocked_lanes=blocked_admit_lanes(
            service="git_integration_worker",
            intent_status=STATUS_PENDING_DRAIN,
            occupied_lanes={"B"},
        ),
        admit_lane="mcp",
    )
    assert (
        blocked_admit_lanes(service="mcp", intent_status=STATUS_FORCE_REQUESTED)
        == frozenset()
    )
    assert (
        blocked_admit_lanes(
            service="git_integration_worker",
            intent_status=STATUS_FORCE_REQUESTED,
            occupied_lanes={"B"},
        )
        == frozenset()
    )


def test_read_only_and_store_miss_do_not_queue() -> None:
    """Read-only would strand on the write promoter; a store error fails open."""
    assert (
        queue_kw_for_pending_intent(
            service="git_integration_worker",
            intent_status=STATUS_PENDING_DRAIN,
            occupied_lanes={"B"},
            admit_lane="B",
            read_only=True,
        )
        == {}
    )
    assert queue_kw_for_pending_intent(
        service="git_integration_worker",
        intent_status=None,
        occupied_lanes={"B"},
        admit_lane="B",
        armed=True,
    ) == {"queue_for_pending_intent": True}

    def _boom(_service: str) -> Any:
        raise OSError("intent db locked")

    assert pending_service_intent_status("mcp", loader=_boom) is None

    class _Row:
        status = STATUS_PENDING_DRAIN

    assert (
        pending_service_intent_status("mcp", loader=lambda _service: _Row())
        == STATUS_PENDING_DRAIN
    )
    assert pending_service_intent_status("mcp", loader=lambda _service: None) is None


def test_fence_does_not_park_nest_parent() -> None:
    """Partial nest: a queued child must leave the live parent running."""
    ledger = CursorDispatchLedger.instance()
    parent = _req(dispatch_id="parent-b")
    assert _admit(ledger, parent, lease_key="/wt/parent") is None
    ledger.mark_running(dispatch_id=parent.dispatch_id)
    child = _req(dispatch_id="child-b", nest_under="parent-b")
    queued = _admit(
        ledger,
        child,
        lease_key="/wt/parent",
        queue_for_pending_intent=True,
        nest_under="parent-b",
    )
    assert queued is not None
    assert queued.status == "queued"
    assert _status(ledger, parent.dispatch_id) == "running"
    assert _status(ledger, child.dispatch_id) == "queued"


@pytest.mark.asyncio
async def test_promote_skipped_while_intent_still_armed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrong ordering: holder exit while another occupant remains must not start the queue."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    ledger = CursorDispatchLedger.instance()
    repo = str(route_mod._CONFIG.source_repo.resolve())
    holder = CursorDispatchRequest(
        thread_id="t-holder-arm",
        model="cursor/composer-2.5",
        dispatch_id="holder-arm",
        execution_id="exec-holder-arm",
        message="holder",
        lane="B",
    )
    successor = CursorDispatchRequest(
        thread_id="t-successor-arm",
        model="cursor/composer-2.5",
        dispatch_id="successor-arm",
        execution_id="exec-successor-arm",
        message="successor",
        lane="B",
    )
    assert (
        ledger.admit(
            req=holder,
            fingerprint=ledger.fingerprint(holder),
            execution_id=holder.execution_id,
            caller_agent=None,
            resolved_model="composer-2.5",
            admission=_admission(holder),
            source_repo=repo,
            lease_key=repo + "-wt",
            contract="implement",
            worker_instance="test-worker",
        )
        is None
    )
    ledger.mark_running(dispatch_id=holder.dispatch_id)
    queued = ledger.admit(
        req=successor,
        fingerprint=ledger.fingerprint(successor),
        execution_id=successor.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(successor),
        source_repo=repo,
        lease_key=repo + "-wt",
        contract="implement",
        worker_instance="test-worker",
        queue_for_pending_intent=True,
    )
    assert queued is not None and queued.status == "queued"

    controller = WorkAdmissionController(
        ledger=ledger,
        worker_id="test-worker",
        pid=0,
        worker_started_at="test",
    )
    controller.try_admit("cursor_sdk", op_id="still-live", route="/r")
    controller.begin_drain(
        reason="a39913",
        intent_id="intent-arm",
        drain_epoch=controller.next_epoch(),
        arm="idle",
    )
    assert controller.is_armed() is True
    assert controller.is_draining() is False
    promoted: list[str] = []

    async def _track_promote(*, lease_key: str, controller, request=None) -> None:
        promoted.append(lease_key)

    monkeypatch.setattr(route_mod, "_promote_queued_for_lease", _track_promote)
    await route_mod._mark_terminal_and_promote(
        dispatch_id=holder.dispatch_id,
        terminal_status="completed",
        controller=controller,
        emit_tag="CURSOR_TEST_INTENT_FENCE",
    )
    assert controller.is_armed() is True
    assert promoted == []
    assert _status(ledger, successor.dispatch_id) == "queued"


@pytest.mark.asyncio
async def test_cancel_promotes_fence_queue_on_a_free_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel lifts the fence without a restart: a free-lease queued row starts.

    The row was queued while the lease had no holder. release_drain runs
    before the store leaves pending_drain, so cancel must promote anyway.
    A store that is still pending_drain and was never released stays queued.
    """
    from types import SimpleNamespace

    from services.git_integration_worker.routes import cursor_sdk as route_mod
    from services.git_integration_worker.routes.admin import (
        CancelDrainRequest,
        cancel_drain,
    )

    async def _no_start(*, promoted, controller, request) -> None:
        del promoted, controller, request

    monkeypatch.setattr(route_mod, "_start_promoted_dispatch", _no_start)

    ledger = CursorDispatchLedger.instance()
    queued_req = _req(dispatch_id="free-lease-queued")
    queued = _admit(
        ledger,
        queued_req,
        lease_key="/wt/free",
        queue_for_pending_intent=True,
    )
    assert queued is not None and queued.status == "queued"

    controller = WorkAdmissionController(
        ledger=ledger,
        worker_id="test-worker",
        pid=0,
        worker_started_at="test",
    )
    controller.try_admit("cursor_sdk", op_id="live-holder", route="/r")
    epoch = controller.next_epoch()
    controller.begin_drain(
        reason="a39913",
        intent_id="intent-cancel",
        drain_epoch=epoch,
        arm="holder:live-holder",
    )
    assert controller.is_armed() is True

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(admission_controller=controller))
    )
    await cancel_drain(
        CancelDrainRequest(intent_id="intent-cancel", drain_epoch=epoch),
        request,  # type: ignore[arg-type]
    )
    assert controller.is_armed() is False
    assert _status(ledger, queued_req.dispatch_id) == "admitted"

    still = _req(dispatch_id="still-pending-store")
    still_queued = _admit(
        ledger,
        still,
        lease_key="/wt/still",
        queue_for_pending_intent=True,
    )
    assert still_queued is not None
    await route_mod.promote_queued_when_fence_lifts(
        controller=controller,
        intent_status=STATUS_PENDING_DRAIN,
    )
    assert _status(ledger, still.dispatch_id) == "queued"
    await route_mod.promote_queued_when_fence_lifts(
        controller=controller,
        intent_status=None,
    )
    assert _status(ledger, still.dispatch_id) == "admitted"
