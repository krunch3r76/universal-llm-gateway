"""Restart-intent transition history and probe-error drain survival."""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

import httpx
import pytest

from scripts.model_manager.ui.controller.restart_drain import (
    ActiveWork,
    RestartDrainGate,
    StargateIdleDrainSupervisor,
)
from scripts.model_manager.ui.controller.restart_intent_consumer import (
    STARGATE_DEADLINE_SEMANTICS,
    project_restart_intent_consumer,
)
from scripts.model_manager.ui.controller.restart_intent_lookup import (
    latest_terminal_for_service,
)
from scripts.model_manager.ui.controller.restart_intent_states import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_FORCE_REQUESTED,
    STATUS_PENDING_DRAIN,
)
from scripts.model_manager.ui.controller.restart_intent_store import RestartIntentStore

pytestmark = pytest.mark.offline


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _store(tmp_path: Any) -> RestartIntentStore:
    return RestartIntentStore(tmp_path / "restart-intents.db")


def _arm(store: RestartIntentStore, *, service: str = "stargate") -> Any:
    return store.create_intent(
        service=service,
        action="sync_restart",
        deadline_at="2026-10-08T06:00:00+00:00",
        reason="manage sync_restart (stargate idle drain)",
    )


def test_transitions_append_and_arm_reason_survives(tmp_path: Any) -> None:
    store = _store(tmp_path)
    intent = _arm(store)
    store.advance(intent.intent_id, status=STATUS_FAILED, reason="probe gave up")
    row = store.get(intent.intent_id)
    assert row is not None
    assert row.reason == "manage sync_restart (stargate idle drain)"
    assert [item["reason"] for item in row.transitions or []] == [
        "armed",
        "probe gave up",
    ]
    assert [item["status"] for item in row.transitions or []] == [
        STATUS_PENDING_DRAIN,
        STATUS_FAILED,
    ]


def test_advance_omitting_reason_raises_type_error(tmp_path: Any) -> None:
    store = _store(tmp_path)
    intent = _arm(store)
    with pytest.raises(TypeError):
        store.advance(intent.intent_id, status=STATUS_FAILED)  # type: ignore[call-arg]


def test_legacy_db_reads_empty_transitions(tmp_path: Any) -> None:
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE restart_intents (
            intent_id TEXT PRIMARY KEY,
            service TEXT NOT NULL,
            action TEXT NOT NULL DEFAULT 'restart',
            status TEXT NOT NULL,
            drain_epoch INTEGER,
            worker_id TEXT,
            worker_started_at TEXT,
            deadline_at TEXT,
            last_seen_event_seq INTEGER NOT NULL DEFAULT 0,
            reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO restart_intents (
            intent_id, service, action, status, reason, created_at, updated_at
        ) VALUES (
            'legacy-1', 'stargate', 'sync_restart', 'failed',
            'arm reason', '2026-10-08T05:31:27+00:00', '2026-10-08T05:34:00+00:00'
        );
        """
    )
    conn.commit()
    conn.close()
    store = RestartIntentStore(path)
    row = store.get("legacy-1")
    assert row is not None
    assert row.transitions == []
    assert row.reason == "arm reason"


def test_cancel_records_its_reason(tmp_path: Any) -> None:
    store = _store(tmp_path)
    intent = _arm(store)
    cancelled = store.cancel(intent.intent_id, reason="operator cancel")
    assert cancelled.status == STATUS_CANCELLED
    assert cancelled.reason == "manage sync_restart (stargate idle drain)"
    assert (cancelled.transitions or [])[-1]["reason"] == "operator cancel"


class _ScriptedProbe:
    def __init__(self, steps: list[Any]) -> None:
        self._steps = list(steps)

    async def snapshot(self) -> ActiveWork:
        step = self._steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def _supervisor(
    probe: _ScriptedProbe,
    lifecycle: Any,
    *,
    deadline_s: float = 30.0,
) -> StargateIdleDrainSupervisor:
    gate = RestartDrainGate(probes={"stargate": probe})
    return StargateIdleDrainSupervisor(
        gate=gate,
        store=None,  # replaced by caller
        lifecycle=lifecycle,
        deadline_s=deadline_s,
        poll_interval_s=0.01,
    )


def test_probe_timeouts_then_idle_records_probe_error(tmp_path: Any) -> None:
    store = _store(tmp_path)
    intent = _arm(store)
    idle = ActiveWork(busy=False, detail={"busy": False, "total": 0})
    seen: dict[str, str] = {}

    class _Probe:
        def __init__(self) -> None:
            self.n = 0

        async def snapshot(self) -> ActiveWork:
            self.n += 1
            if self.n == 2:
                row = store.get(intent.intent_id)
                assert row is not None
                seen["status_reason"] = row.status_reason or ""
            if self.n < 3:
                raise httpx.ReadTimeout("timed out")
            return idle

    probe = _Probe()

    async def _lifecycle() -> str:
        return "stargate restarted"

    supervisor = _supervisor(probe, _lifecycle)
    supervisor.store = store
    _run(supervisor.supervise(intent))
    assert seen["status_reason"].startswith("drain_probe_exception:ReadTimeout")
    row = store.get(intent.intent_id)
    assert row is not None
    assert row.status == STATUS_COMPLETED
    blob = " ".join(item["reason"] for item in row.transitions or [])
    assert "drain_probe_exception:ReadTimeout" in blob
    assert "idle_observed" in blob


def test_probe_always_raises_self_preempts(tmp_path: Any) -> None:
    store = _store(tmp_path)
    intent = _arm(store)
    probe = _ScriptedProbe([httpx.ReadTimeout("down")] * 20)

    async def _lifecycle() -> str:
        return "forced restart"

    supervisor = _supervisor(probe, _lifecycle, deadline_s=0.05)
    supervisor.store = store
    _run(supervisor.supervise(intent))
    row = store.get(intent.intent_id)
    assert row is not None
    assert row.status == STATUS_COMPLETED
    reasons = [item["reason"] for item in row.transitions or []]
    assert any(
        item["status"] == STATUS_FORCE_REQUESTED for item in row.transitions or []
    )
    assert any(reason.startswith("idle_ceiling_reached") for reason in reasons)
    assert any("drain_probe_exception:ReadTimeout" in reason for reason in reasons)


def test_busy_until_ceiling_names_last_cause(tmp_path: Any) -> None:
    store = _store(tmp_path)
    intent = _arm(store)
    busy = ActiveWork(
        busy=True,
        detail={"busy": True, "total": 2, "requests_in_flight": 2},
    )
    probe = _ScriptedProbe([busy] * 20)

    async def _lifecycle() -> str:
        return "forced restart"

    supervisor = _supervisor(probe, _lifecycle, deadline_s=0.05)
    supervisor.store = store
    _run(supervisor.supervise(intent))
    row = store.get(intent.intent_id)
    assert row is not None
    force = next(
        item
        for item in row.transitions or []
        if item["status"] == STATUS_FORCE_REQUESTED
    )
    assert force["reason"].startswith("idle_ceiling_reached")
    assert "active_count=2" in force["reason"]


def test_lifecycle_raise_names_phase_and_class(tmp_path: Any) -> None:
    store = _store(tmp_path)
    intent = _arm(store)
    idle = ActiveWork(busy=False, detail={"busy": False, "total": 0})
    probe = _ScriptedProbe([idle])

    async def _lifecycle() -> str:
        raise RuntimeError("lifecycle boom")

    supervisor = _supervisor(probe, _lifecycle)
    supervisor.store = store
    with pytest.raises(RuntimeError):
        _run(supervisor.supervise(intent))
    row = store.get(intent.intent_id)
    assert row is not None
    assert row.status == STATUS_FAILED
    failed = (row.transitions or [])[-1]["reason"]
    assert failed == "lifecycle_exception:RuntimeError"


def test_cancel_during_wait_is_not_failed(tmp_path: Any) -> None:
    store = _store(tmp_path)
    intent = _arm(store)

    class _Block:
        async def snapshot(self) -> ActiveWork:
            await asyncio.sleep(30)
            return ActiveWork(busy=True, detail={"busy": True})

    supervisor = StargateIdleDrainSupervisor(
        gate=RestartDrainGate(probes={"stargate": _Block()}),
        store=store,
        lifecycle=_noop_lifecycle,
        deadline_s=30.0,
        poll_interval_s=0.05,
    )

    async def _scenario() -> None:
        task = asyncio.create_task(supervisor.supervise(intent))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    _run(_scenario())
    row = store.get(intent.intent_id)
    assert row is not None
    assert row.status == STATUS_PENDING_DRAIN
    assert row.status != STATUS_FAILED


async def _noop_lifecycle() -> str:
    return "unused"


def test_service_status_returns_failed_intent(tmp_path: Any) -> None:
    store = _store(tmp_path)
    intent = _arm(store)
    store.advance(
        intent.intent_id,
        status=STATUS_FAILED,
        reason="lifecycle_exception:RuntimeError",
    )
    from scripts.model_manager.ui.controller.restart_intent_lookup import (
        newest_for_service,
    )

    found = store.active_for_service("stargate") or newest_for_service(
        store, "stargate"
    )
    assert found is not None
    view = project_restart_intent_consumer(found)
    assert view["live"] is False
    assert view["status"] == STATUS_FAILED
    assert view["status_reason"] == "lifecycle_exception:RuntimeError"
    assert view["reason"] == "manage sync_restart (stargate idle drain)"
    last = latest_terminal_for_service(store, "stargate")
    assert last is not None and last.intent_id == intent.intent_id
    assert view["deadline_semantics"] == STARGATE_DEADLINE_SEMANTICS


@pytest.mark.asyncio
async def test_busy_status_hides_failed_intent_and_keeps_last(tmp_path: Any) -> None:
    from unittest.mock import MagicMock

    from scripts.model_manager.ui.api_dispatch import (
        _busy_status,
        _restart_intent_status,
    )
    from scripts.model_manager.ui.controller.restart_drain import RestartDrainGate

    store = _store(tmp_path)
    intent = _arm(store)
    idle = ActiveWork(busy=False, detail={"busy": False, "total": 0})

    async def _boom() -> str:
        raise RuntimeError("lifecycle boom")

    supervisor = _supervisor(_ScriptedProbe([idle]), _boom)
    supervisor.store = store
    with pytest.raises(RuntimeError):
        await supervisor.supervise(intent)
    gate = RestartDrainGate(
        probes={
            "stargate": _ScriptedProbe(
                [ActiveWork(busy=False, detail={"busy": False, "total": 0})]
            )
        }
    )
    ctl = MagicMock()
    ctl.restart_intent_store = store
    ctl.restart_gate = gate
    status = await _restart_intent_status(ctl, {"service": "stargate"})
    view = status["restart_intent"]
    assert view["status"] == STATUS_FAILED
    assert view["live"] is False
    assert view["status_reason"] == "lifecycle_exception:RuntimeError"
    assert view["status_reason"]
    busy = await _busy_status(ctl, service="stargate")
    assert busy["restart_intent"] is None
    last = busy["restart_intent_last"]
    assert last["intent_id"] == intent.intent_id
    assert last["status"] == "failed"
    assert last["reason"] == "lifecycle_exception:RuntimeError"
    assert last["drain_begun"] is False
    assert last["armed_at"]
    assert last["terminal_at"]


def test_terminal_status_projection_words() -> None:
    from scripts.model_manager.ui.controller.restart_intent_reason_codes import (
        TERMINAL_STATUS_PROJECTION,
    )
    from scripts.model_manager.ui.controller.restart_intent_states import (
        STATUS_ACTIVATION_UNVERIFIED,
        STATUS_TIMEOUT,
    )

    assert TERMINAL_STATUS_PROJECTION[STATUS_COMPLETED] == "fired"
    assert TERMINAL_STATUS_PROJECTION[STATUS_FORCE_REQUESTED] == "fired"
    assert TERMINAL_STATUS_PROJECTION[STATUS_FAILED] == "failed"
    assert TERMINAL_STATUS_PROJECTION[STATUS_ACTIVATION_UNVERIFIED] == "failed"
    assert TERMINAL_STATUS_PROJECTION[STATUS_TIMEOUT] == "expired"
    assert TERMINAL_STATUS_PROJECTION[STATUS_CANCELLED] == "cancelled"


def test_supervisor_reasons_use_known_codes() -> None:
    from scripts.model_manager.ui.controller.restart_intent_reason_codes import (
        ACTIVATION_NOT_APPLICABLE,
        ACTIVATION_VALIDATED,
        ACTIVATION_VERIFY_ARMED,
        ALL_REASON_CODES,
        BEGIN_DRAIN_UNREACHABLE,
        EPOCH_CHECK_MISMATCH,
        EPOCH_CHECK_OK,
        FORCE_START_COMPLETED,
        FORCE_START_PENDING,
        GENERATION_GONE,
        KILL_COMMIT,
        KILL_FAILED,
        LIFECYCLE_UNCONFIRMED,
        RECONCILE_RESUME_FAILED,
        SUPERVISOR_EXCEPTION,
        TARGET_GONE,
        UNSPECIFIED_TRANSITION,
        reason_code,
    )

    samples = [
        reason_code(SUPERVISOR_EXCEPTION, "RuntimeError: boom"),
        reason_code(EPOCH_CHECK_OK, "delivering SIGTERM"),
        reason_code(KILL_FAILED, "boom"),
        reason_code(LIFECYCLE_UNCONFIRMED, "not certified"),
        reason_code(BEGIN_DRAIN_UNREACHABLE, "SIGTERM without epoch"),
        reason_code(GENERATION_GONE, "already gone"),
        reason_code(EPOCH_CHECK_MISMATCH, "same worker generation"),
        reason_code(TARGET_GONE, "action does not start it"),
        reason_code(FORCE_START_PENDING, "left the intent pending"),
        reason_code(FORCE_START_COMPLETED, "without activation verify"),
        reason_code(ACTIVATION_NOT_APPLICABLE, "does not apply to this action"),
        reason_code(ACTIVATION_VERIFY_ARMED, "kill boundary recorded"),
        reason_code(ACTIVATION_VALIDATED),
        reason_code(RECONCILE_RESUME_FAILED),
        reason_code(KILL_COMMIT),
        reason_code(UNSPECIFIED_TRANSITION),
    ]
    for sample in samples:
        assert sample.split(":", 1)[0] in ALL_REASON_CODES


def test_unspecified_cas_is_a_named_code(tmp_path: Any) -> None:
    store = _store(tmp_path)
    intent = _arm(store)
    assert (
        store.advance_if_status(
            intent.intent_id,
            from_status=STATUS_PENDING_DRAIN,
            to_status=STATUS_FAILED,
        )
        == 1
    )
    row = store.get(intent.intent_id)
    assert row is not None
    assert (row.transitions or [])[-1]["reason"] == "unspecified_transition"
    assert row.reason == "manage sync_restart (stargate idle drain)"


def test_ttl_expiry_projects_as_expired(tmp_path: Any) -> None:
    from datetime import UTC, datetime, timedelta

    from scripts.model_manager.ui.controller.restart_intent_consumer import (
        project_restart_intent_last,
    )
    from scripts.model_manager.ui.controller.restart_intent_expiry import (
        expire_via_cancel,
    )

    store = _store(tmp_path)
    intent = store.create_intent(
        service="stargate",
        action="sync_restart",
        deadline_at="2026-10-08T06:00:00+00:00",
        reason="arm",
        intent_ttl_s=1,
    )

    async def _expire() -> bool:
        return await expire_via_cancel(
            store, intent.intent_id, now=datetime.now(UTC) + timedelta(hours=1)
        )

    assert _run(_expire()) is True
    row = store.get(intent.intent_id)
    assert row is not None
    assert row.status == STATUS_CANCELLED
    projected = project_restart_intent_last(row)
    assert projected is not None
    assert projected["status"] == "expired"
    assert projected["reason"] == "ttl_expired"


def test_waiting_note_does_not_overwrite_cancel(tmp_path: Any) -> None:
    from scripts.model_manager.ui.controller.restart_intent_lookup import (
        note_waiting_reason,
    )
    from scripts.model_manager.ui.controller.restart_intent_reason_codes import (
        CANCELLED_BY_OPERATOR,
    )

    store = _store(tmp_path)
    intent = _arm(store)
    store.cancel(intent.intent_id, reason=CANCELLED_BY_OPERATOR)
    wrote = note_waiting_reason(
        store, intent.intent_id, status_reason="drain_probe_exception:ReadTimeout"
    )
    assert wrote is False
    row = store.get(intent.intent_id)
    assert row is not None
    assert row.status_reason == CANCELLED_BY_OPERATOR


def test_store_error_during_wait_is_supervisor_exception(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    intent = _arm(store)

    def _boom(*_a: Any, **_k: Any) -> bool:
        raise RuntimeError("locked")

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.restart_drain_supervise.note_waiting_reason",
        _boom,
    )
    busy = ActiveWork(busy=True, detail={"busy": True, "total": 1})
    supervisor = _supervisor(_ScriptedProbe([busy]), _noop_lifecycle)
    supervisor.store = store
    with pytest.raises(RuntimeError):
        _run(supervisor.supervise(intent))
    row = store.get(intent.intent_id)
    assert row is not None
    assert row.status == STATUS_FAILED
    assert row.status_reason == "supervisor_exception:wait:RuntimeError"


def test_giw_deadline_semantics_unchanged() -> None:
    from scripts.model_manager.ui.controller.restart_intent_consumer import (
        DEADLINE_SEMANTICS,
    )
    from scripts.model_manager.ui.controller.restart_intent_store import Intent

    intent = Intent(
        intent_id="i",
        service="git_integration_worker",
        action="sync_restart",
        status=STATUS_PENDING_DRAIN,
        drain_epoch=None,
        worker_id=None,
        worker_started_at=None,
        deadline_at=None,
        last_seen_event_seq=0,
        reason="arm",
        kill_boundary_at=None,
        created_at="2026-10-08T05:00:00+00:00",
        updated_at="2026-10-08T05:00:00+00:00",
    )
    view = project_restart_intent_consumer(intent)
    assert view["deadline_semantics"] == DEADLINE_SEMANTICS
    assert "alert-only" in view["deadline_semantics"]
