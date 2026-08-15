"""Tests for MCP restart busy probe — CSE + life activity composite gate."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from scripts.model_manager.ui.controller.mcp_restart_probe import McpBusyProbe
from scripts.model_manager.ui.controller.restart_drain import (
    ActiveWork,
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


def test_default_probes_registers_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.restart_drain.cdp_ask_url_config",
        lambda: ("jupiter", 8770, "http://jupiter:8770"),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.mcp_restart_probe.cdp_ask_url_config",
        lambda: ("jupiter", 8770, "http://jupiter:8770"),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.mcp_restart_probe.resolve_mcp_health_probe_url",
        lambda: "https://mcp.example/health",
    )
    probes = _default_probes()
    assert "mcp" in probes
    assert isinstance(probes["mcp"], McpBusyProbe)


def test_mcp_probe_ignores_live_cse_when_mcp_idle() -> None:
    probe = McpBusyProbe(
        cdp_probe=_StaticBusyProbe(
            ActiveWork(
                busy=True,
                detail={
                    "busy": True,
                    "running_count": 0,
                    "live_cse_count": 1,
                    "effective_count": 1,
                },
            )
        ),
        mcp_probe=_StaticBusyProbe(
            ActiveWork(busy=False, detail={"busy": False, "in_flight": 0})
        ),
    )
    work = _run(probe.snapshot())
    assert work.busy is False
    assert "cdp_ask_live" not in work.detail["busy_reasons"]


def test_mcp_probe_defers_on_life_activity_when_cdp_idle() -> None:
    probe = McpBusyProbe(
        cdp_probe=_StaticBusyProbe(
            ActiveWork(
                busy=False,
                detail={
                    "busy": False,
                    "running_count": 0,
                    "live_cse_count": 0,
                    "effective_count": 0,
                },
            )
        ),
        mcp_probe=_StaticBusyProbe(
            ActiveWork(
                busy=True,
                detail={"busy": True, "in_flight": 0, "life_hot": True, "life_idle_s": 12},
            )
        ),
    )
    work = _run(probe.snapshot())
    assert work.busy is True
    assert "mcp_session_hot" in work.detail["busy_reasons"]


def test_mcp_probe_ignores_live_cse_when_endpoint_busy_flag_stale() -> None:
    """A stale tab observation must not become restart busy."""
    probe = McpBusyProbe(
        cdp_probe=_StaticBusyProbe(
            ActiveWork(
                busy=False,
                detail={
                    "busy": False,
                    "running_count": 0,
                    "live_cse_count": 2,
                    "effective_count": 2,
                },
            )
        ),
        mcp_probe=_StaticBusyProbe(ActiveWork(busy=False, detail={"busy": False})),
    )
    work = _run(probe.snapshot())
    assert work.busy is False


def test_mcp_probe_ignores_open_attachment_when_no_execution_is_running() -> None:
    """Open attachment telemetry does not represent a running execution."""
    probe = McpBusyProbe(
        cdp_probe=_StaticBusyProbe(
            ActiveWork(
                busy=False,
                detail={
                    "busy": False,
                    "open_attachment_count": 2,
                    "live_cse_count": 1,
                    "running_count": 0,
                    "effective_count": 2,
                },
            )
        ),
        mcp_probe=_StaticBusyProbe(ActiveWork(busy=False, detail={"busy": False})),
    )

    work = _run(probe.snapshot())

    assert work.busy is False


def test_evaluate_defers_mcp_when_composite_busy() -> None:
    gate = RestartDrainGate(
        probes={
            "mcp": McpBusyProbe(
                cdp_probe=_StaticBusyProbe(
                    ActiveWork(
                        busy=True,
                        detail={"busy": True, "live_cse_count": 1, "running_count": 1},
                    )
                ),
                mcp_probe=None,
            )
        }
    )
    outcome = _run(gate.evaluate("mcp", force=False))
    assert outcome is not None
    assert outcome.state == "busy"
    assert outcome.service == "mcp"


def test_mcp_probe_soft_fails_unreachable_mcp_when_cdp_ok() -> None:
    class _Boom:
        async def snapshot(self) -> ActiveWork:
            raise OSError("connection refused")

    probe = McpBusyProbe(
        cdp_probe=_StaticBusyProbe(
            ActiveWork(
                busy=False,
                detail={
                    "busy": False,
                    "running_count": 0,
                    "live_cse_count": 0,
                    "effective_count": 0,
                },
            )
        ),
        mcp_probe=_Boom(),  # type: ignore[arg-type]
    )
    work = _run(probe.snapshot())
    assert work.busy is False
    assert "mcp_probe_error" in work.detail
