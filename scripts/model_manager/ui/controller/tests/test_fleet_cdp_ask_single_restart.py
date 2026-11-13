"""AC1: one fleet op must not double-restart remote cdp-ask on the NFS path."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from scripts.model_manager.ui.controller import fleet_remote
from scripts.model_manager.ui.controller.fleet import FleetOrchestrator, NullFleetSink
from scripts.model_manager.ui.controller.fleet_single_flight import (
    FleetOperationInProgressError,
)


class _Sink:
    lines: list[str]

    def __init__(self) -> None:
        self.lines = []

    def line(self, node: str, text: str) -> None:
        self.lines.append(text)

    def status(self, node: str, text: str) -> None:
        pass

    def focus(self, node: str) -> None:
        pass


@pytest.mark.asyncio
async def test_maybe_restart_skips_second_restart_when_nfs_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NFS path: remote deploy phase must not call sync_restart after local phase."""
    sink = _Sink()
    sync_mock = AsyncMock(return_value="cdp_ask restarted")
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
    monkeypatch.setattr(fleet_remote, "nfs_checkout_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(fleet_remote, "sync_restart_cdp_ask_remote", sync_mock)
    monkeypatch.setattr(
        fleet_remote.HttpActiveWorkProbe,
        "__init__",
        lambda self, *a, **k: None,
    )
    monkeypatch.setattr(
        fleet_remote.HttpActiveWorkProbe,
        "snapshot",
        AsyncMock(return_value=None),
    )

    await fleet_remote._maybe_restart_remote_cdp_ask(
        "jupiter", root=Path("/hub"), sink=sink
    )

    sync_mock.assert_not_awaited()
    assert any("skipped" in line for line in sink.lines)


@pytest.mark.asyncio
async def test_maybe_restart_rsync_path_still_sync_restarts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-NFS: remote phase still performs exactly one sync_restart."""
    sink = _Sink()
    sync_mock = AsyncMock(return_value="cdp_ask synced+restarted")
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
    monkeypatch.setattr(fleet_remote, "nfs_checkout_ready", AsyncMock(return_value=False))
    monkeypatch.setattr(fleet_remote, "sync_restart_cdp_ask_remote", sync_mock)
    monkeypatch.setattr(
        fleet_remote.HttpActiveWorkProbe,
        "__init__",
        lambda self, *a, **k: None,
    )
    monkeypatch.setattr(
        fleet_remote.HttpActiveWorkProbe,
        "snapshot",
        AsyncMock(return_value=None),
    )

    await fleet_remote._maybe_restart_remote_cdp_ask(
        "jupiter", root=Path("/hub"), sink=sink
    )

    sync_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_fleet_single_flight_rejects_overlapping_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2: second fleet_sync_restart gets busy, not a queued duplicate."""

    class _Ctl:
        root = Path("/hub")

        class _Gate:
            def set_activity(self, *a: object) -> None:
                pass

        shutdown_gate = _Gate()

        async def start_event_service(self) -> str:
            return "ok"

        restart_intent_store = object()

    orch = FleetOrchestrator(ctl=_Ctl(), root=Path("/hub"), sink=NullFleetSink())  # type: ignore[arg-type]

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.fleet.wait_event_service_healthy",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.fleet.emit_fleet_operation_started",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.fleet.record_fleet_operation_completed",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.fleet.open_fleet_window",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.fleet.clear_fleet_windows",
        AsyncMock(),
    )

    gate = asyncio.Event()

    async def _hang_body(**_kwargs: object) -> object:
        await gate.wait()
        from scripts.model_manager.ui.controller.fleet import FleetResult

        return FleetResult(
            operation="sync_restart", build=False, success=True, duration_s=0.1
        )

    monkeypatch.setattr(orch, "_sync_restart_all_locked", _hang_body)

    first = asyncio.create_task(orch.sync_restart_all(build=False, scope="all"))
    await asyncio.sleep(0.05)
    with pytest.raises(FleetOperationInProgressError) as exc:
        await orch.sync_restart_all(build=False, scope="all")
    assert exc.value.running_operation == "sync_restart"
    gate.set()
    await first
