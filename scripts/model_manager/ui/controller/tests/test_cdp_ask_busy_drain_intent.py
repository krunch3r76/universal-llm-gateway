"""cdp_ask durable busy drain via run_gated on_busy (Shape D, todo:cdp-ask-durable-restart-intent)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

import scripts.model_manager.ui.api_dispatch as api_dispatch
from scripts.model_manager.ui.api_dispatch import _busy_status, orchestrate_cancel_restart_intent
from scripts.model_manager.ui.controller.restart_drain import (
    ActiveWork,
    DrainOutcome,
    LocalServiceDrainSupervisor,
    RestartDrainGate,
    run_gated,
    run_gated_drain_supervised,
)
from scripts.model_manager.ui.controller.restart_drain_witness import (
    InFlightWitness,
    LastProbeWitness,
    WitnessReport,
    probe_answer_from_resolution,
    resolve_probe_failure,
)
from scripts.model_manager.ui.controller.restart_intent_store import (
    STATUS_COMPLETED,
    STATUS_PENDING_DRAIN,
    RestartIntentStore,
)
from scripts.model_manager.ui.controller.tests.conftest import stub_process_live_probe

pytestmark = pytest.mark.offline

_SERVICE = "cdp_ask"
_HOP_ROW = {
    "busy": True,
    "holders": [{"kind": "execution", "op_id": "exec-hop-generate-1"}],
    "active_count": 1,
}


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class _StaticProbe:
    def __init__(self, work: ActiveWork) -> None:
        self.work = work
        self.calls = 0

    async def snapshot(self) -> ActiveWork:
        self.calls += 1
        return self.work


class _FlakyProbe:
    def __init__(self, sequence: list[ActiveWork | BaseException]) -> None:
        self._seq = list(sequence)
        self.calls = 0

    async def snapshot(self) -> ActiveWork:
        self.calls += 1
        if not self._seq:
            return ActiveWork(busy=False, detail={"busy": False})
        item = self._seq.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class _AlwaysTimeoutProbe:
    async def snapshot(self) -> ActiveWork:
        raise httpx.ReadTimeout("probe timeout")


class _CountingWitness:
    def __init__(self, report: WitnessReport) -> None:
        self.report = report
        self.calls = 0

    async def observe(self, service: str) -> WitnessReport:
        self.calls += 1
        return self.report


class _RecordingSupervisor:
    deadline_s = 604800.0

    def __init__(self) -> None:
        self.intents: list[Any] = []
        self.lifecycle_calls = 0
        self._block = asyncio.Event()

    async def supervise(self, intent: Any) -> None:
        self.intents.append(intent)
        await self._block.wait()


@pytest.fixture
def events_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    log: list[tuple[str, dict[str, Any]]] = []

    async def _fake_emit(signal: str, payload: dict[str, Any], **_kw: Any) -> None:
        log.append((signal, payload))

    monkeypatch.setattr("scripts.model_manager.observation_event._emit", _fake_emit)
    return log


def test_ta_run_gated_default_on_busy_soft_defer() -> None:
    """T-a: on_busy=None leaves soft busy without restart_intent_id."""
    gate = RestartDrainGate(
        probes={_SERVICE: _StaticProbe(ActiveWork(busy=True, detail=_HOP_ROW))}
    )

    async def _go() -> dict[str, Any]:
        return await run_gated(
            gate,
            "sync_restart",
            _SERVICE,
            force=False,
            lifecycle=lambda: asyncio.sleep(0, result="noop"),
            on_busy=None,
        )

    result = _run(_go())
    assert result["status"] == "deferred"
    assert result["state"] == "busy"
    assert "restart_intent_id" not in result


def test_probe_answer_from_resolution_mapping() -> None:
    resolution = _run(
        resolve_probe_failure(
            _SERVICE,
            probe_error="timeout",
            witnesses=[],
        )
    )
    assert probe_answer_from_resolution(resolution) is None


def test_t1_ac1_arm_on_busy_probe(
    tmp_path: Any,
    events_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T1: busy probe + on_busy arms restart_intent_id."""
    stub_process_live_probe(monkeypatch)
    store = RestartIntentStore(db_path=tmp_path / "t1.db")
    gate = RestartDrainGate(
        probes={_SERVICE: _StaticProbe(ActiveWork(busy=True, detail=_HOP_ROW))}
    )
    supervisor = _RecordingSupervisor()
    hook_calls = 0

    async def on_busy(_outcome: DrainOutcome) -> dict[str, Any]:
        nonlocal hook_calls
        hook_calls += 1
        return await run_gated_drain_supervised(
            gate,
            "sync_restart",
            _SERVICE,
            store=store,
            supervisor=supervisor,
            reason="test",
            intent_ttl_s=86400.0,
        )

    async def _go() -> dict[str, Any]:
        result = await run_gated(
            gate,
            "sync_restart",
            _SERVICE,
            force=False,
            lifecycle=lambda: asyncio.sleep(0, result="starting (PID 1)"),
            on_busy=on_busy,
        )
        supervisor._block.set()
        await asyncio.sleep(0)
        return result

    result = _run(_go())
    assert hook_calls == 1
    assert result.get("restart_intent_id")
    live = store.active_for_service(_SERVICE)
    assert live is not None
    assert live.status == STATUS_PENDING_DRAIN


def test_t2_ac2_idle_then_lifecycle_once(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """T2: probe busy then idle → lifecycle once, intent completed."""
    stub_process_live_probe(monkeypatch)
    store = RestartIntentStore(db_path=tmp_path / "t2.db")
    probe = _FlakyProbe(
        [
            ActiveWork(busy=True, detail=_HOP_ROW),
            ActiveWork(busy=False, detail={"busy": False}),
        ]
    )
    gate = RestartDrainGate(probes={_SERVICE: probe})
    lifecycle_calls = 0

    async def lifecycle() -> str:
        nonlocal lifecycle_calls
        lifecycle_calls += 1
        return "starting (PID 42)"

    supervisor = LocalServiceDrainSupervisor(
        gate=gate,
        service=_SERVICE,
        store=store,
        lifecycle=lifecycle,
        poll_interval_s=0.01,
        deadline_s=2.0,
    )

    async def on_busy(_outcome: DrainOutcome) -> dict[str, Any]:
        return await run_gated_drain_supervised(
            gate,
            "sync_restart",
            _SERVICE,
            store=store,
            supervisor=supervisor,
            reason="test",
            intent_ttl_s=86400.0,
        )

    async def _go() -> dict[str, Any]:
        return await run_gated(
            gate,
            "sync_restart",
            _SERVICE,
            force=False,
            lifecycle=lifecycle,
            on_busy=on_busy,
        )

    result = _run(_go())
    assert result.get("restart_intent_id")
    intent_id = result["restart_intent_id"]
    final = store.get(intent_id)
    assert final is not None
    assert final.status == STATUS_COMPLETED
    assert lifecycle_calls == 1


def test_t3_ac3_extend_no_lifecycle_under_busy(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T3: sustained busy under extend — lifecycle never runs."""
    stub_process_live_probe(monkeypatch)
    store = RestartIntentStore(db_path=tmp_path / "t3.db")
    gate = RestartDrainGate(
        probes={
            _SERVICE: _StaticProbe(ActiveWork(busy=True, detail=_HOP_ROW)),
        }
    )
    lifecycle_calls = 0
    cas_calls: list[str] = []

    async def lifecycle() -> str:
        nonlocal lifecycle_calls
        lifecycle_calls += 1
        return "starting (PID 1)"

    supervisor = LocalServiceDrainSupervisor(
        gate=gate,
        service=_SERVICE,
        store=store,
        lifecycle=lifecycle,
        poll_interval_s=0.01,
        deadline_s=0.05,
    )

    real_cas = store.cas_force_preempt_from_pending

    def _spy_cas(intent_id: str, *, reason: str) -> int:
        cas_calls.append(intent_id)
        return real_cas(intent_id, reason=reason)

    monkeypatch.setattr(store, "cas_force_preempt_from_pending", _spy_cas)

    hook_calls = 0

    async def on_busy(_outcome: DrainOutcome) -> dict[str, Any]:
        nonlocal hook_calls
        hook_calls += 1
        return await run_gated_drain_supervised(
            gate,
            "sync_restart",
            _SERVICE,
            store=store,
            supervisor=supervisor,
            reason="test",
            intent_ttl_s=86400.0,
        )

    async def _go() -> dict[str, Any]:
        return await run_gated(
            gate,
            "sync_restart",
            _SERVICE,
            force=False,
            lifecycle=lifecycle,
            on_busy=on_busy,
        )

    result = _run(_go())
    assert hook_calls == 1
    assert lifecycle_calls == 0
    assert cas_calls == []
    intent_id = result["restart_intent_id"]
    assert store.get(intent_id).status == STATUS_PENDING_DRAIN

    hook_force = 0

    async def _never(_: DrainOutcome) -> dict[str, Any] | None:
        nonlocal hook_force
        hook_force += 1
        return None

    _run(
        run_gated(
            gate,
            "sync_restart",
            _SERVICE,
            force=True,
            lifecycle=lambda: asyncio.sleep(0, result="ok"),
            on_busy=_never,
        )
    )
    assert hook_force == 0


def test_t4_ac4_cancel_intent(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """T4: cancel armed intent — lifecycle never runs."""
    stub_process_live_probe(monkeypatch)
    store = RestartIntentStore(db_path=tmp_path / "t4.db")
    gate = RestartDrainGate(
        probes={_SERVICE: _StaticProbe(ActiveWork(busy=True, detail=_HOP_ROW))}
    )
    lifecycle_calls = 0

    async def lifecycle() -> str:
        nonlocal lifecycle_calls
        lifecycle_calls += 1
        return "starting (PID 1)"

    supervisor = LocalServiceDrainSupervisor(
        gate=gate,
        service=_SERVICE,
        store=store,
        lifecycle=lifecycle,
        poll_interval_s=0.05,
    )

    async def on_busy(_outcome: DrainOutcome) -> dict[str, Any]:
        return await run_gated_drain_supervised(
            gate,
            "sync_restart",
            _SERVICE,
            store=store,
            supervisor=supervisor,
            reason="test",
            intent_ttl_s=86400.0,
        )

    async def _arm() -> str:
        result = await run_gated(
            gate,
            "sync_restart",
            _SERVICE,
            force=False,
            lifecycle=lifecycle,
            on_busy=on_busy,
        )
        return str(result["restart_intent_id"])

    intent_id = _run(_arm())
    async def _cancel_and_wait() -> dict[str, Any]:
        cancel = await orchestrate_cancel_restart_intent(
            store, intent_id=intent_id, service=_SERVICE
        )
        await asyncio.sleep(0.05)
        return cancel

    cancel = _run(_cancel_and_wait())
    assert cancel.get("status") == "ok"
    assert store.get(intent_id) is not None
    assert store.get(intent_id).status == "cancelled"
    assert lifecycle_calls == 0


def test_t5_probe_raise_witness_busy_arms_via_hook_only(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T5: probe raise + witness busy → restart_intent_id through on_busy at run_gated."""
    stub_process_live_probe(monkeypatch)
    store = RestartIntentStore(db_path=tmp_path / "t5.db")
    witness = _CountingWitness(
        WitnessReport(
            verdict="busy",
            source="fake_witness",
            holders=[{"kind": "execution", "op_id": "e1"}],
            detail={"busy": True},
        )
    )
    gate = RestartDrainGate(
        probes={_SERVICE: _FlakyProbe([httpx.ReadTimeout("probe timeout")])},
        witnesses={_SERVICE: [witness]},
    )
    supervisor = _RecordingSupervisor()

    async def on_busy(_outcome: DrainOutcome) -> dict[str, Any]:
        return await run_gated_drain_supervised(
            gate,
            "sync_restart",
            _SERVICE,
            store=store,
            supervisor=supervisor,
            reason="test",
            intent_ttl_s=86400.0,
        )

    async def _go() -> dict[str, Any]:
        result = await run_gated(
            gate,
            "sync_restart",
            _SERVICE,
            force=False,
            lifecycle=lambda: asyncio.sleep(0, result="noop"),
            on_busy=on_busy,
        )
        supervisor._block.set()
        await asyncio.sleep(0)
        return result

    result = _run(_go())
    assert result.get("restart_intent_id")
    assert witness.calls >= 1


def test_t5_last_probe_witness_variant(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """T5 variant: LastProbeWitness recent busy snapshot arms via hook."""
    stub_process_live_probe(monkeypatch)
    store = RestartIntentStore(db_path=tmp_path / "t5b.db")
    last = LastProbeWitness(ttl_s=120.0)
    last.record(_SERVICE, busy=True, detail=_HOP_ROW)
    gate = RestartDrainGate(
        probes={_SERVICE: _FlakyProbe([httpx.ReadTimeout("timeout")])},
        witnesses={_SERVICE: []},
    )
    gate._last_probe = last  # noqa: SLF001
    supervisor = _RecordingSupervisor()

    async def on_busy(_outcome: DrainOutcome) -> dict[str, Any]:
        return await run_gated_drain_supervised(
            gate,
            "sync_restart",
            _SERVICE,
            store=store,
            supervisor=supervisor,
            reason="test",
            intent_ttl_s=86400.0,
        )

    async def _go() -> dict[str, Any]:
        result = await run_gated(
            gate,
            "sync_restart",
            _SERVICE,
            force=False,
            lifecycle=lambda: asyncio.sleep(0, result="noop"),
            on_busy=on_busy,
        )
        supervisor._block.set()
        await asyncio.sleep(0)
        return result

    result = _run(_go())
    assert result.get("restart_intent_id")


def test_t6_slot_race_rejoin_or_in_progress(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T6: interleaved slot holder → rejoin or in_progress, lifecycle at most once."""
    stub_process_live_probe(monkeypatch)
    store = RestartIntentStore(db_path=tmp_path / "t6.db")
    gate = RestartDrainGate(
        probes={_SERVICE: _StaticProbe(ActiveWork(busy=True, detail=_HOP_ROW))}
    )
    lifecycle_calls = 0
    supervisor = _RecordingSupervisor()

    async def on_busy(_outcome: DrainOutcome) -> dict[str, Any]:
        await gate.evaluate(_SERVICE, force=True, supervised_drain=True)
        existing = store.active_for_service(_SERVICE)
        if existing is not None:
            await gate.release(_SERVICE)
            from scripts.model_manager.ui.controller.restart_intent_consumer import (
                drain_deferred_result,
            )

            return drain_deferred_result(
                existing, reason="drain already in progress for this service"
            )
        await gate.release(_SERVICE)
        return await run_gated_drain_supervised(
            gate,
            "sync_restart",
            _SERVICE,
            store=store,
            supervisor=supervisor,
            reason="test",
            intent_ttl_s=86400.0,
        )

    async def lifecycle() -> str:
        nonlocal lifecycle_calls
        lifecycle_calls += 1
        return "starting (PID 1)"

    async def _first() -> dict[str, Any]:
        return await run_gated(
            gate,
            "sync_restart",
            _SERVICE,
            force=False,
            lifecycle=lifecycle,
            on_busy=on_busy,
        )

    first = _run(_first())
    intent_id = first["restart_intent_id"]

    async def _race() -> dict[str, Any]:
        await gate.evaluate(_SERVICE, force=True, supervised_drain=True)
        second = await run_gated(
            gate,
            "sync_restart",
            _SERVICE,
            force=False,
            lifecycle=lifecycle,
            on_busy=on_busy,
        )
        await gate.release(_SERVICE)
        supervisor._block.set()
        return second

    second = _run(_race())
    assert second.get("state") in ("draining", "in_progress", "busy") or second.get(
        "restart_intent_id"
    ) in (intent_id, None)
    assert lifecycle_calls <= 1


def test_t7_witness_aware_probe_idle_restarts(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T7: supervisor witness probe — idle witness after raise allows restart."""
    stub_process_live_probe(monkeypatch)
    store = RestartIntentStore(db_path=tmp_path / "t7-idle.db")
    witness = _CountingWitness(
        WitnessReport(verdict="idle", source="fake", detail={"busy": False})
    )
    gate = RestartDrainGate(
        probes={_SERVICE: _FlakyProbe([httpx.ReadTimeout("t")])},
        witnesses={_SERVICE: [witness]},
    )
    lifecycle_calls = 0

    async def lifecycle() -> str:
        nonlocal lifecycle_calls
        lifecycle_calls += 1
        return "starting (PID 7)"

    supervisor = LocalServiceDrainSupervisor(
        gate=gate,
        service=_SERVICE,
        store=store,
        lifecycle=lifecycle,
        witness_aware_probe=True,
        poll_interval_s=0.01,
        deadline_s=2.0,
    )

    async def _go() -> None:
        intent = store.create_intent(
            service=_SERVICE,
            action="sync_restart",
            deadline_at=datetime.now(UTC).isoformat(),
            reason="t7",
        )
        await supervisor.supervise(intent)

    _run(_go())
    assert lifecycle_calls == 1


def test_t7_witness_aware_probe_busy_keeps_waiting(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_process_live_probe(monkeypatch)
    store = RestartIntentStore(db_path=tmp_path / "t7-busy.db")
    witness = _CountingWitness(
        WitnessReport(
            verdict="busy",
            source="fake",
            holders=[{"kind": "execution", "op_id": "x"}],
            detail={"busy": True, "active_count": 1},
        )
    )
    gate = RestartDrainGate(
        probes={_SERVICE: _AlwaysTimeoutProbe()},
        witnesses={_SERVICE: [witness]},
    )
    lifecycle_calls = 0

    async def lifecycle() -> str:
        nonlocal lifecycle_calls
        lifecycle_calls += 1
        return "starting (PID 7)"

    supervisor = LocalServiceDrainSupervisor(
        gate=gate,
        service=_SERVICE,
        store=store,
        lifecycle=lifecycle,
        witness_aware_probe=True,
        poll_interval_s=0.01,
        deadline_s=0.15,
    )

    async def _go() -> None:
        intent = store.create_intent(
            service=_SERVICE,
            action="sync_restart",
            deadline_at=datetime.now(UTC).isoformat(),
            reason="t7b",
        )
        await supervisor.supervise(intent)

    _run(_go())
    assert lifecycle_calls == 0


def test_t8_rate_limit_counts_witness_consults(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T8: probe_resolving_failure consults witness once per 5s window."""
    stub_process_live_probe(monkeypatch)
    witness = _CountingWitness(
        WitnessReport(
            verdict="busy",
            source="fake",
            holders=[{"kind": "execution", "op_id": "x"}],
            detail={"busy": True},
        )
    )
    times = iter([0.0, 0.1, 0.2, 6.0])

    def clock() -> float:
        return next(times, 6.0)

    gate = RestartDrainGate(
        probes={_SERVICE: _FlakyProbe([httpx.ReadTimeout("t")] * 10)},
        witnesses={_SERVICE: [witness]},
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.restart_drain.time.monotonic",
        clock,
    )

    async def _poll() -> None:
        for _ in range(4):
            work = await gate.probe_resolving_failure(_SERVICE)
            assert work.busy is True

    _run(_poll())
    assert witness.calls == 2


def test_t8_idle_not_cached(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    class _IdleThenUnknownWitness:
        def __init__(self) -> None:
            self.calls = 0

        async def observe(self, service: str) -> WitnessReport:
            self.calls += 1
            if self.calls == 1:
                return WitnessReport(verdict="idle", source="fake", detail={"busy": False})
            return WitnessReport(verdict="unknown", source="fake")

    witness = _IdleThenUnknownWitness()
    gate = RestartDrainGate(
        probes={_SERVICE: _AlwaysTimeoutProbe()},
        witnesses={_SERVICE: [witness]},
    )
    stub_process_live_probe(monkeypatch)

    async def _go() -> None:
        work = await gate.probe_resolving_failure(_SERVICE)
        assert work.busy is False
        with pytest.raises(httpx.ReadTimeout):
            await gate.probe_resolving_failure(_SERVICE)

    _run(_go())
    assert witness.calls == 2


def test_t9_intent_ttl_86400(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """T9: intent TTL stamped at ~86400s not default 600s."""
    stub_process_live_probe(monkeypatch)
    store = RestartIntentStore(db_path=tmp_path / "t9.db")
    gate = RestartDrainGate(
        probes={_SERVICE: _StaticProbe(ActiveWork(busy=True, detail=_HOP_ROW))}
    )
    supervisor = _RecordingSupervisor()

    async def on_busy(_outcome: DrainOutcome) -> dict[str, Any]:
        return await run_gated_drain_supervised(
            gate,
            "sync_restart",
            _SERVICE,
            store=store,
            supervisor=supervisor,
            reason="test",
            intent_ttl_s=86400.0,
        )

    async def _go() -> dict[str, Any]:
        result = await run_gated(
            gate,
            "sync_restart",
            _SERVICE,
            force=False,
            lifecycle=lambda: asyncio.sleep(0, result="noop"),
            on_busy=on_busy,
        )
        supervisor._block.set()
        await asyncio.sleep(0)
        return result

    result = _run(_go())
    intent = store.get(result["restart_intent_id"])
    assert intent is not None
    armed_at = datetime.fromisoformat(intent.armed_at.replace("Z", "+00:00"))
    expires_at = datetime.fromisoformat(intent.expires_at.replace("Z", "+00:00"))
    delta = (expires_at - armed_at).total_seconds()
    assert 86395 <= delta <= 86405


def test_t10_hook_not_called_for_in_progress_or_probe_error_or_force() -> None:
    gate = RestartDrainGate(probes={_SERVICE: _StaticProbe(ActiveWork(busy=False))})
    hook_calls = 0

    async def hook(_: DrainOutcome) -> dict[str, Any] | None:
        nonlocal hook_calls
        hook_calls += 1
        return None

    async def _in_progress() -> dict[str, Any]:
        await gate.evaluate(_SERVICE, force=True, supervised_drain=True)
        try:
            return await run_gated(
                gate,
                "sync_restart",
                _SERVICE,
                force=False,
                lifecycle=lambda: asyncio.sleep(0, result="ok"),
                on_busy=hook,
            )
        finally:
            await gate.release(_SERVICE)

    result = _run(_in_progress())
    assert result["state"] == "in_progress"
    assert hook_calls == 0

    gate2 = RestartDrainGate(
        probes={_SERVICE: _FlakyProbe([httpx.ReadTimeout("t")])},
        witnesses={_SERVICE: []},
    )
    hook_calls = 0

    async def _probe_error() -> dict[str, Any]:
        return await run_gated(
            gate2,
            "sync_restart",
            _SERVICE,
            force=False,
            lifecycle=lambda: asyncio.sleep(0, result="ok"),
            on_busy=hook,
        )

    pe = _run(_probe_error())
    assert pe["state"] == "probe_error"
    assert hook_calls == 0

    gate3 = RestartDrainGate(
        probes={_SERVICE: _StaticProbe(ActiveWork(busy=True, detail=_HOP_ROW))}
    )
    hook_calls = 0

    async def _force() -> dict[str, Any]:
        return await run_gated(
            gate3,
            "sync_restart",
            _SERVICE,
            force=True,
            lifecycle=lambda: asyncio.sleep(0, result="starting (PID 1)"),
            on_busy=hook,
        )

    _run(_force())
    assert hook_calls == 0


def test_t11_recall_while_intent_armed_in_progress_no_hook(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T11: second call while intent armed → in_progress, hook not called."""
    stub_process_live_probe(monkeypatch)
    store = RestartIntentStore(db_path=tmp_path / "t11.db")
    gate = RestartDrainGate(
        probes={_SERVICE: _StaticProbe(ActiveWork(busy=True, detail=_HOP_ROW))}
    )
    supervisor = _RecordingSupervisor()
    hook_calls = 0

    async def on_busy(_outcome: DrainOutcome) -> dict[str, Any]:
        nonlocal hook_calls
        hook_calls += 1
        return await run_gated_drain_supervised(
            gate,
            "sync_restart",
            _SERVICE,
            store=store,
            supervisor=supervisor,
            reason="test",
            intent_ttl_s=86400.0,
        )

    async def _first() -> dict[str, Any]:
        result = await run_gated(
            gate,
            "sync_restart",
            _SERVICE,
            force=False,
            lifecycle=lambda: asyncio.sleep(0, result="noop"),
            on_busy=on_busy,
        )
        return result

    first = _run(_first())
    assert hook_calls == 1
    intent_id = first["restart_intent_id"]

    hook_calls = 0

    async def _second() -> dict[str, Any]:
        await gate.evaluate(_SERVICE, force=True, supervised_drain=True)
        second = await run_gated(
            gate,
            "sync_restart",
            _SERVICE,
            force=False,
            lifecycle=lambda: asyncio.sleep(0, result="noop"),
            on_busy=on_busy,
        )
        await gate.release(_SERVICE)
        supervisor._block.set()
        return second

    second = _run(_second())
    assert hook_calls == 0
    assert "restart_intent_id" not in second or second.get("state") == "in_progress"
    assert store.active_for_service(_SERVICE) is not None
    assert store.active_for_service(_SERVICE).intent_id == intent_id
