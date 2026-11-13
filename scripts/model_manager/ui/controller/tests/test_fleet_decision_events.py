"""AC: fleet decision events are emitted and never break fleet handlers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts.model_manager.ui.controller import fleet_decision_events as fde
from scripts.model_manager.ui.controller import fleet_remote
from scripts.model_manager.ui.controller.fleet_single_flight import (
    FleetOperationInProgressError,
)


@pytest.mark.asyncio
async def test_record_fleet_operation_rejected_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrency: rejected row names requested vs running op and caller."""
    emitted: list[dict[str, object]] = []

    async def _capture(**kwargs: object) -> None:
        emitted.append(dict(kwargs))

    monkeypatch.setattr(fde, "emit_fleet_operation_rejected", _capture)
    await fde.record_fleet_operation_rejected(
        operation="sync_restart",
        running_operation="rebuild_deploy",
        caller="cursor-sdk",
        reason="deploy",
    )
    assert emitted == [
        {
            "operation": "sync_restart",
            "running_operation": "rebuild_deploy",
            "caller": "cursor-sdk",
            "reason": "deploy",
        }
    ]


@pytest.mark.asyncio
async def test_record_fleet_operation_rejected_swallows_emit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Partial failure: emit exception must not propagate to the busy response path."""

    async def _boom(**_kwargs: object) -> None:
        raise RuntimeError("event service down")

    monkeypatch.setattr(fde, "emit_fleet_operation_rejected", _boom)
    await fde.record_fleet_operation_rejected(
        operation="sync_restart",
        running_operation="sync_restart",
        caller=None,
        reason=None,
    )


@pytest.mark.asyncio
async def test_record_cdp_ask_restart_decision_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emitted: list[dict[str, object]] = []

    async def _capture(**kwargs: object) -> None:
        emitted.append(dict(kwargs))

    monkeypatch.setattr(fde, "emit_fleet_cdp_ask_restart_decision", _capture)
    await fde.record_fleet_cdp_ask_restart_decision(
        phase="remote",
        action="skip",
        reason="nfs_local_phase_owns",
        hostname="jupiter",
    )
    assert emitted[0]["phase"] == "remote"
    assert emitted[0]["action"] == "skip"
    assert emitted[0]["reason"] == "nfs_local_phase_owns"
    assert emitted[0]["hostname"] == "jupiter"


@pytest.mark.asyncio
async def test_record_cdp_ask_restart_decision_swallows_emit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(**_kwargs: object) -> None:
        raise OSError("uds missing")

    monkeypatch.setattr(fde, "emit_fleet_cdp_ask_restart_decision", _boom)
    await fde.record_fleet_cdp_ask_restart_decision(
        phase="local",
        action="restart",
        reason="nfs_local_phase_owns",
    )


@pytest.mark.asyncio
async def test_fleet_api_busy_unchanged_when_rejected_emit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1: status: busy even when fleet.operation.rejected emit raises."""
    from scripts.model_manager.ui import api_dispatch

    ctl = MagicMock()
    ctl.root = Path("/hub")

    async def _busy(**_kwargs: object) -> object:
        raise FleetOperationInProgressError(
            "sync_restart", requested_operation="sync_restart"
        )

    class _Orch:
        async def sync_restart_all(self, **kwargs: object) -> object:
            raise FleetOperationInProgressError(
                "sync_restart", requested_operation="sync_restart"
            )

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.fleet.FleetOrchestrator",
        lambda **kwargs: _Orch(),
    )

    async def _boom(**_kwargs: object) -> None:
        raise RuntimeError("emit failed")

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.fleet_decision_events.emit_fleet_operation_rejected",
        _boom,
    )

    result = await api_dispatch._fleet(ctl, build=False, params={})
    assert result["status"] == "busy"
    assert result["error"] == "fleet_operation_in_progress"


@pytest.mark.asyncio
async def test_maybe_restart_emits_skip_on_not_remote_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decisions: list[dict[str, object]] = []

    async def _record(**kwargs: object) -> None:
        decisions.append(dict(kwargs))

    monkeypatch.setattr(fleet_remote, "record_fleet_cdp_ask_restart_decision", _record)
    monkeypatch.setattr(fleet_remote, "is_cdp_ask_manage_enabled", lambda: True)
    monkeypatch.setattr(
        fleet_remote,
        "cdp_ask_url_config",
        lambda: ("jupiter", 8790, "http://jupiter:8790"),
    )
    monkeypatch.setattr(
        fleet_remote,
        "resolve_cdp_ask_remote_target",
        lambda _host: ("jupiter", "10.0.0.2", "io"),
    )

    sink = MagicMock()
    await fleet_remote._maybe_restart_remote_cdp_ask(
        "not-jupiter", root=Path("/hub"), sink=sink
    )
    assert decisions[-1]["reason"] == "not_remote_target"
    assert decisions[-1]["action"] == "skip"
