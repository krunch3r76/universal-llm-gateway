"""Unit tests for cdp-ask restart-drain probe registration and gate behavior (F-4)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from scripts.model_manager.ui.controller.restart_drain import (
    ActiveWork,
    HttpActiveWorkProbe,
    RestartDrainGate,
    _default_probes,
)
from scripts.model_manager.ui.controller.restart_drain_witness import (
    UNKNOWN_NEXT_STEP,
    WitnessReport,
)

pytestmark = pytest.mark.offline


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class _StaticBusyProbe:
    def __init__(self, work: ActiveWork) -> None:
        self._work = work

    async def snapshot(self) -> ActiveWork:
        return self._work


def test_default_probes_registers_cdp_ask_when_url_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.restart_drain.cdp_ask_url_config",
        lambda: ("jupiter", 8770, "http://jupiter:8770"),
    )
    probes = _default_probes()
    assert isinstance(probes["stargate"], HttpActiveWorkProbe)
    assert isinstance(probes["git_integration_worker"], HttpActiveWorkProbe)
    assert isinstance(probes["agent_bus"], HttpActiveWorkProbe)
    assert probes["agent_bus"]._path == "/api/v1/git/active-work"
    assert isinstance(probes["cdp_ask"], HttpActiveWorkProbe)
    assert probes["cdp_ask"]._path == "/v1/project-ask/drain-state"


def test_default_probes_omits_cdp_ask_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.restart_drain.cdp_ask_url_config",
        lambda: None,
    )
    probes = _default_probes()
    assert "cdp_ask" not in probes
    assert "stargate" in probes
    assert "git_integration_worker" in probes
    assert isinstance(probes["agent_bus"], HttpActiveWorkProbe)


def test_evaluate_defers_cdp_ask_when_probe_busy() -> None:
    gate = RestartDrainGate(
        probes={
            "cdp_ask": _StaticBusyProbe(
                ActiveWork(
                    busy=True,
                    detail={
                        "busy": True,
                        "running_count": 1,
                        "execution_ids": ["exec-1"],
                    },
                )
            )
        }
    )
    outcome = _run(gate.evaluate("cdp_ask", force=False))
    assert outcome is not None
    assert outcome.state == "busy"
    assert outcome.service == "cdp_ask"
    assert outcome.active_work["execution_ids"] == ["exec-1"]


def test_agent_bus_probe_busy_when_giw_active_work_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(
        return_value={"busy": True, "dispatch_ids": ["disp-closeout"]}
    )

    class _Client:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, path: str) -> Any:
            assert path == "/api/v1/git/active-work"
            return resp

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.restart_drain.make_async_client",
        lambda *_a, **_k: _Client(),
    )
    probe = HttpActiveWorkProbe("http://127.0.0.1:8091", "/api/v1/git/active-work")
    work = _run(probe.snapshot())
    assert work.busy is True
    assert work.detail["dispatch_ids"] == ["disp-closeout"]


def test_evaluate_defers_agent_bus_when_giw_active_work_busy() -> None:
    gate = RestartDrainGate(
        probes={
            "agent_bus": _StaticBusyProbe(
                ActiveWork(
                    busy=True,
                    detail={"busy": True, "dispatch_ids": ["disp-closeout"]},
                )
            )
        }
    )
    outcome = _run(gate.evaluate("agent_bus", force=False))
    assert outcome is not None
    assert outcome.state == "busy"
    assert outcome.service == "agent_bus"
    assert outcome.active_work["dispatch_ids"] == ["disp-closeout"]


def test_evaluate_proceeds_when_force_true_despite_busy() -> None:
    gate = RestartDrainGate(
        probes={
            "cdp_ask": _StaticBusyProbe(
                ActiveWork(busy=True, detail={"running_count": 2})
            )
        }
    )
    outcome = _run(gate.evaluate("cdp_ask", force=True))
    assert outcome is None
    _run(gate.release("cdp_ask"))


# --- probe did not answer (a:36939 cdp_ask pinned; a:36595 agent_bus via GIW) ---


class _TimeoutProbe:
    """The pinned target: every probe raises inside the 5s budget."""

    async def snapshot(self) -> ActiveWork:
        raise httpx.ReadTimeout("")


class _FlakyProbe:
    """Answers the first probe, then times out like a pinned process."""

    def __init__(self, first: ActiveWork) -> None:
        self._first: ActiveWork | None = first

    async def snapshot(self) -> ActiveWork:
        if self._first is not None:
            work, self._first = self._first, None
            return work
        raise httpx.ReadTimeout("")


class _StaticWitness:
    def __init__(self, report: WitnessReport) -> None:
        self._report = report

    async def observe(self, service: str) -> WitnessReport:
        return self._report


def test_probe_timeout_with_busy_witness_defers_busy_and_names_the_work() -> None:
    witness = _StaticWitness(
        WitnessReport(
            verdict="busy",
            source="cdp_ask_execution_ledger",
            holders=[
                {
                    "kind": "execution",
                    "op_id": "69270ed1",
                    "subject_preview": "web-anthropic operator-proxy",
                }
            ],
            detail={"execution_ids": ["69270ed1"], "running_count": 1},
        )
    )
    gate = RestartDrainGate(
        probes={"cdp_ask": _TimeoutProbe()}, witnesses={"cdp_ask": [witness]}
    )
    outcome = _run(gate.evaluate("cdp_ask", force=False))
    assert outcome is not None
    assert outcome.state == "busy"
    assert "holder=execution:69270ed1" in outcome.reason
    assert "live probe failed: ReadTimeout" in outcome.reason
    result = outcome.to_result()
    assert result["status"] == "deferred"
    assert result["determination"] == "busy"
    assert result["active_work"]["probe_error"] == "ReadTimeout"
    assert result["active_work"]["witness"]["source"] == "cdp_ask_execution_ledger"
    assert "69270ed1" in result["active_work_summary"]
    # Deferred ⇒ no slot held; a later caller is not told in_progress.
    assert gate.restart_in_progress("cdp_ask") is False


def test_probe_timeout_with_no_witness_verdict_stays_deferred_unknown() -> None:
    gate = RestartDrainGate(probes={"cdp_ask": _TimeoutProbe()})
    outcome = _run(gate.evaluate("cdp_ask", force=False))
    assert outcome is not None
    assert outcome.state == "probe_error"
    assert outcome.reason == "could not determine in-flight work: ReadTimeout"
    result = outcome.to_result()
    assert result["status"] == "deferred"
    assert result["determination"] == "undetermined"
    assert result["retry_after_s"] == 30
    work = result["active_work"]
    assert work["error"] == "ReadTimeout"
    assert work["witnesses"] == [
        {
            "source": "last_probe",
            "verdict": "unknown",
            "note": "no successful probe recorded in this manage process",
        }
    ]
    assert work["next_step"] == UNKNOWN_NEXT_STEP
    assert gate.restart_in_progress("cdp_ask") is False


def test_last_probe_witness_turns_timeout_into_named_busy_deferral() -> None:
    first = ActiveWork(
        busy=True,
        detail={
            "busy": True,
            "running_count": 1,
            "execution_ids": ["69270ed1"],
            "rows": [
                {
                    "execution_id": "69270ed1",
                    "holder": "cowork:cse:abc",
                    "purpose": "operator-proxy",
                }
            ],
        },
    )
    gate = RestartDrainGate(probes={"cdp_ask": _FlakyProbe(first)})
    # A seat read busy_status a moment ago — the gate remembers the answer.
    report = _run(gate.busy_report(["cdp_ask"]))
    assert report["cdp_ask"]["determination"] == "busy"

    outcome = _run(gate.evaluate("cdp_ask", force=False))
    assert outcome is not None
    assert outcome.state == "busy"
    assert "holder=execution:69270ed1 subject=cowork:cse:abc operator-proxy" in (
        outcome.reason
    )
    assert outcome.active_work["witness"]["source"] == "last_probe"


def test_last_probe_witness_never_clears_on_a_stale_idle_answer() -> None:
    gate = RestartDrainGate(
        probes={"cdp_ask": _FlakyProbe(ActiveWork(busy=False, detail={"busy": False}))}
    )
    _run(gate.busy_report(["cdp_ask"]))
    outcome = _run(gate.evaluate("cdp_ask", force=False))
    assert outcome is not None
    assert outcome.state == "probe_error"
    note = outcome.active_work["witnesses"][-1]["note"]
    assert note.startswith("last successful probe 0s ago reported idle")


def test_probe_timeout_with_idle_witness_proceeds_and_holds_slot() -> None:
    witness = _StaticWitness(
        WitnessReport(verdict="idle", source="cdp_ask_execution_ledger")
    )
    gate = RestartDrainGate(
        probes={"cdp_ask": _TimeoutProbe()}, witnesses={"cdp_ask": [witness]}
    )
    outcome = _run(gate.evaluate("cdp_ask", force=False))
    assert outcome is None
    assert gate.restart_in_progress("cdp_ask") is True
    _run(gate.release("cdp_ask"))


def test_busy_witness_outranks_idle_witness() -> None:
    idle = _StaticWitness(WitnessReport(verdict="idle", source="ledger"))
    busy = _StaticWitness(
        WitnessReport(
            verdict="busy",
            source="stargate",
            holders=[{"kind": "pipeline", "op_id": "p-1"}],
        )
    )
    gate = RestartDrainGate(
        probes={"cdp_ask": _TimeoutProbe()}, witnesses={"cdp_ask": [idle, busy]}
    )
    outcome = _run(gate.evaluate("cdp_ask", force=False))
    assert outcome is not None
    assert outcome.state == "busy"
    assert [w["verdict"] for w in outcome.active_work["witnesses"]] == [
        "idle",
        "busy",
        "unknown",
    ]


def test_cdp_ask_inflight_witness_idle_when_only_followup_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    from scripts.model_manager.ui.controller.cdp_ask_inflight_witness import (
        CdpAskInFlightWitness,
    )

    now = time.time()
    active = {
        "reg-fu": {
            "execution_state": {
                "execution_id": "followup:abc",
                "state": "streaming",
                "kind": "followup",
                "started_at": now - 10,
                "updated_at": now,
                "holder_pid": 1,
            },
            "holder": "operator",
        }
    }
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.cdp_ask_inflight_witness._read_active",
        lambda: (active, "local:test"),
    )
    report = _run(CdpAskInFlightWitness().observe("cdp_ask"))
    assert report.verdict == "idle"


def test_cdp_ask_inflight_witness_busy_when_execution_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    from scripts.model_manager.ui.controller.cdp_ask_inflight_witness import (
        CdpAskInFlightWitness,
    )

    now = time.time()
    active = {
        "reg-ex": {
            "execution_state": {
                "execution_id": "exec-1",
                "state": "streaming",
                "kind": "execution",
                "started_at": now - 10,
                "updated_at": now,
                "holder_pid": 1,
            },
            "holder": "operator",
            "purpose": "ask",
        }
    }
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.cdp_ask_inflight_witness._read_active",
        lambda: (active, "local:test"),
    )
    report = _run(CdpAskInFlightWitness().observe("cdp_ask"))
    assert report.verdict == "busy"
    assert report.holders
    assert report.holders[0]["op_id"] == "exec-1"


def test_busy_report_probe_failure_mirrors_evaluate() -> None:
    witness = _StaticWitness(
        WitnessReport(
            verdict="busy",
            source="ledger",
            holders=[{"kind": "execution", "op_id": "69270ed1"}],
        )
    )
    gate = RestartDrainGate(
        probes={"cdp_ask": _TimeoutProbe(), "agent_bus": _TimeoutProbe()},
        witnesses={"cdp_ask": [witness]},
    )
    report = _run(gate.busy_report(["cdp_ask", "agent_bus"]))
    assert report["cdp_ask"]["busy"] is True
    assert report["cdp_ask"]["determination"] == "busy"
    assert report["cdp_ask"]["restart_would_defer"] is True
    assert report["agent_bus"]["busy"] is False
    assert report["agent_bus"]["determination"] == "undetermined"
    assert report["agent_bus"]["restart_would_defer"] is True
    assert report["agent_bus"]["active_work"]["error"] == "ReadTimeout"
