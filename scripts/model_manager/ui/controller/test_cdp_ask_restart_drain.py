"""Unit tests for cdp-ask restart-drain probe registration and gate behavior (F-4)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from scripts.model_manager.ui.controller.restart_drain import (
    ActiveWork,
    HttpActiveWorkProbe,
    RestartDrainGate,
    _default_probes,
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
    assert isinstance(probes["cdp_ask"], HttpActiveWorkProbe)
    assert probes["cdp_ask"]._path == "/v1/project-ask/active-work"


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
