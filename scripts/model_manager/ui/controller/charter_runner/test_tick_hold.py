"""Offline tests for durable charter-runner tick hold (pause / resume)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from scripts.model_manager.ui.controller.charter_runner.kernel import hold
from scripts.model_manager.ui.controller.charter_runner.kernel.host import (
    CharterRunnerTickLoop,
)
from scripts.model_manager.ui.controller.shutdown_gate import ManageShutdownGate
from scripts.model_manager.ui.model.service_state import ServiceInfo, ServiceStatus


def _healthy_state() -> MagicMock:
    state = MagicMock()
    state.check_cortex_api.return_value = ServiceInfo(
        name="Cortex", status=ServiceStatus.RUNNING
    )
    state.check_agent_bus.return_value = ServiceInfo(
        name="AgentBus", status=ServiceStatus.RUNNING
    )
    return state


@pytest.fixture(autouse=True)
def _isolate_hold_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent offline hold tests from writing into the live Event Service."""

    async def _noop(signal: str, payload: dict[str, Any], **_kw: Any) -> None:
        return None

    monkeypatch.setattr("scripts.model_manager.observation_event._emit", _noop)
    monkeypatch.setattr(
        "scripts.model_manager.observation_event_charter._emit", _noop
    )


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "charter-runner"
    d.mkdir()
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(d))
    return d


@pytest.fixture
def events_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    log: list[tuple[str, dict[str, Any]]] = []

    async def _fake_emit(signal: str, payload: dict[str, Any], **_kw: Any) -> None:
        log.append((signal, payload))

    monkeypatch.setattr("scripts.model_manager.observation_event._emit", _fake_emit)
    monkeypatch.setattr(
        "scripts.model_manager.observation_event_charter._emit", _fake_emit
    )
    return log


@pytest.mark.offline
def test_set_read_clear_round_trip(data_dir: Path) -> None:
    assert hold.read_hold(data_dir=data_dir) is None
    payload = hold.set_hold("load code", "cursor", data_dir=data_dir)
    assert payload.reason == "load code"
    assert payload.set_by == "cursor"
    got = hold.read_hold(data_dir=data_dir)
    assert got is not None
    assert got.reason == "load code"
    assert got.set_by == "cursor"
    assert hold.hold_path(data_dir=data_dir).is_file()
    assert hold.clear_hold(data_dir=data_dir) is True
    assert hold.read_hold(data_dir=data_dir) is None
    assert hold.clear_hold(data_dir=data_dir) is False


@pytest.mark.offline
def test_unparseable_file_is_fail_closed_held(data_dir: Path) -> None:
    path = hold.hold_path(data_dir=data_dir)
    path.write_text("{not-json", encoding="utf-8")
    got = hold.read_hold(data_dir=data_dir)
    assert got is not None
    assert got.reason == hold.UNPARSEABLE_REASON


@pytest.mark.offline
def test_truncated_json_is_fail_closed_held(data_dir: Path) -> None:
    path = hold.hold_path(data_dir=data_dir)
    path.write_text('{"reason": "x"', encoding="utf-8")
    got = hold.read_hold(data_dir=data_dir)
    assert got is not None
    assert got.reason == hold.UNPARSEABLE_REASON


@pytest.mark.offline
def test_loop_skips_while_held(
    data_dir: Path,
    events_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hold.set_hold("test", "pytest", data_dir=data_dir)
    gate = ManageShutdownGate()
    ticks = {"n": 0}

    async def fake_tick(self: CharterRunnerTickLoop) -> None:
        ticks["n"] += 1

    monkeypatch.setattr(CharterRunnerTickLoop, "_tick_once", fake_tick)
    loop = CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=gate,
        tick_interval_s=0.02,
    )

    async def _run() -> None:
        await loop.start()
        await asyncio.sleep(0.12)
        await loop.stop()

    asyncio.run(_run())
    assert ticks["n"] == 0
    assert "charter_tick" not in gate.snapshot().activities
    held_events = [p for s, p in events_log if s == "manage.charter.tick.held"]
    assert len(held_events) >= 1
    assert held_events[0]["reason"] == "test"


@pytest.mark.offline
def test_clear_mid_run_allows_ticks(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hold.set_hold("test", "pytest", data_dir=data_dir)
    ticks = {"n": 0}

    async def fake_tick(self: CharterRunnerTickLoop) -> None:
        ticks["n"] += 1

    monkeypatch.setattr(CharterRunnerTickLoop, "_tick_once", fake_tick)
    loop = CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        tick_interval_s=0.02,
    )

    async def _run() -> None:
        await loop.start()
        await asyncio.sleep(0.06)
        assert ticks["n"] == 0
        hold.clear_hold(data_dir=data_dir)
        await asyncio.sleep(0.1)
        await loop.stop()

    asyncio.run(_run())
    assert ticks["n"] >= 1


@pytest.mark.offline
def test_hold_survives_restart_simulation(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hold.set_hold("survive", "pytest", data_dir=data_dir)
    ticks = {"n": 0}

    async def fake_tick(self: CharterRunnerTickLoop) -> None:
        ticks["n"] += 1

    monkeypatch.setattr(CharterRunnerTickLoop, "_tick_once", fake_tick)

    async def _one() -> None:
        loop = CharterRunnerTickLoop(
            service_state=_healthy_state(),
            shutdown_gate=ManageShutdownGate(),
            tick_interval_s=0.02,
        )
        await loop.start()
        await asyncio.sleep(0.06)
        await loop.stop()

    asyncio.run(_one())
    assert ticks["n"] == 0
    assert hold.read_hold(data_dir=data_dir) is not None

    async def _two() -> None:
        loop = CharterRunnerTickLoop(
            service_state=_healthy_state(),
            shutdown_gate=ManageShutdownGate(),
            tick_interval_s=0.02,
        )
        await loop.start()
        await asyncio.sleep(0.06)
        await loop.stop()

    asyncio.run(_two())
    assert ticks["n"] == 0


@pytest.mark.offline
def test_held_heartbeat_rate_limited(
    data_dir: Path,
    events_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hold.set_hold("hb", "pytest", data_dir=data_dir)

    async def fake_tick(self: CharterRunnerTickLoop) -> None:
        return None

    monkeypatch.setattr(CharterRunnerTickLoop, "_tick_once", fake_tick)
    loop = CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        tick_interval_s=0.01,
    )

    async def _run() -> None:
        await loop.start()
        await asyncio.sleep(0.08)
        await loop.stop()

    asyncio.run(_run())
    held_events = [s for s, _ in events_log if s == "manage.charter.tick.held"]
    # start() force + loop; rate-limit keeps this to one (force sets timestamp).
    assert len(held_events) == 1


@pytest.mark.offline
def test_hold_status_safe_to_quit(data_dir: Path) -> None:
    from scripts.model_manager.ui.controller.service_ctl.core import ServiceController

    # Construct minimal gate path without full ServiceController boot.
    gate = ManageShutdownGate()
    hold.set_hold("quiet", "pytest", data_dir=data_dir)
    held = hold.read_hold(data_dir=data_dir)
    assert held is not None
    tick_in_flight = "charter_tick" in gate.snapshot().activities
    assert tick_in_flight is False
    assert held is not None and not tick_in_flight  # safe_to_quit

    gate.set_activity("charter_tick", True)
    tick_in_flight = "charter_tick" in gate.snapshot().activities
    assert tick_in_flight is True
    assert not (held is not None and not tick_in_flight)

    # Exercise ServiceController methods when possible via direct hold helpers.
    _ = ServiceController  # import-smoke; methods use hold module directly
    payload = hold.hold_as_dict(held)
    assert payload is not None
    assert payload["reason"] == "quiet"


@pytest.mark.offline
def test_service_controller_pause_resume_status(
    data_dir: Path,
    events_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from scripts.model_manager.ui.controller.service_ctl.core import ServiceController

    ctl = ServiceController(workspace_root=tmp_path)
    # Ensure hold uses our temp data dir (env already set by data_dir fixture).

    async def _run() -> None:
        paused = await ctl.charter_pause(reason="ops", set_by="test")
        assert paused["held"] is True
        assert paused["reason"] == "ops"
        status = await ctl.charter_hold_status()
        assert status["held"] is True
        assert status["safe_to_quit"] is True
        resumed = await ctl.charter_resume()
        assert resumed["held"] is False
        assert resumed["was_held"] is True
        status2 = await ctl.charter_hold_status()
        assert status2["held"] is False
        assert status2["safe_to_quit"] is False

    asyncio.run(_run())
    assert any(s == "manage.charter.tick.paused" for s, _ in events_log)
    assert any(s == "manage.charter.tick.resumed" for s, _ in events_log)


@pytest.mark.offline
def test_hold_file_schema(data_dir: Path) -> None:
    hold.set_hold("schema", "pytest", data_dir=data_dir)
    raw = json.loads(hold.hold_path(data_dir=data_dir).read_text(encoding="utf-8"))
    assert set(raw.keys()) == {"schema_version", "reason", "set_by", "set_at"}
    assert raw["schema_version"] == 1
