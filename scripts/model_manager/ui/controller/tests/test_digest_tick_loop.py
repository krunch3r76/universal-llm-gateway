"""Offline tests for the manage-owned digest tick loop."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from scripts.model_manager.ui.controller.digest_tick_loop import (
    DigestTickLoop,
    ensure_digest_tick_env,
)
from scripts.model_manager.ui.controller.shutdown_gate import ManageShutdownGate
from scripts.model_manager.ui.model.service_state import ServiceInfo, ServiceStatus


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture
def events_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    log: list[tuple[str, dict[str, Any]]] = []

    async def _fake_emit(signal: str, payload: dict[str, Any], **_kw: Any) -> None:
        log.append((signal, payload))

    monkeypatch.setattr(
        "scripts.model_manager.observation_event._emit", _fake_emit
    )
    return log


def _healthy_state() -> MagicMock:
    state = MagicMock()
    state.check_cortex_api.return_value = ServiceInfo(
        name="Cortex",
        status=ServiceStatus.RUNNING,
    )
    return state


def _unhealthy_state() -> MagicMock:
    state = MagicMock()
    state.check_cortex_api.return_value = ServiceInfo(
        name="Cortex",
        status=ServiceStatus.UNHEALTHY,
    )
    return state


def _loop(
    *,
    service_state: MagicMock,
    gate: ManageShutdownGate | None = None,
    interval_s: float = 0.01,
) -> DigestTickLoop:
    return DigestTickLoop(
        service_state=service_state,
        shutdown_gate=gate or ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=interval_s,
    )


@pytest.mark.offline
def test_healthy_env_calls_tick_jobs(
    monkeypatch: pytest.MonkeyPatch, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    tick_calls: list[int] = []

    def fake_tick(*, limit: int = 1) -> dict[str, Any]:
        tick_calls.append(limit)
        return {"status": "ok", "results": [], "count": 0}

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.digest_tick_loop.ensure_digest_tick_env",
        lambda _root: True,
    )
    monkeypatch.setattr(
        "cortex_store.digest_jobs.tick_jobs",
        fake_tick,
    )

    loop = _loop(service_state=_healthy_state(), interval_s=0.01)

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    _run(_exercise())
    assert tick_calls
    assert events_log[0][0] == "manage.digest.tick.started"
    assert events_log[-1][0] == "manage.digest.tick.stopped"


@pytest.mark.offline
def test_unhealthy_skips_tick(
    monkeypatch: pytest.MonkeyPatch, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    tick_calls: list[int] = []

    def fake_tick(*, limit: int = 1) -> dict[str, Any]:
        tick_calls.append(limit)
        return {"status": "ok", "results": [], "count": 0}

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.digest_tick_loop.ensure_digest_tick_env",
        lambda _root: True,
    )
    monkeypatch.setattr("cortex_store.digest_jobs.tick_jobs", fake_tick)

    loop = _loop(service_state=_unhealthy_state(), interval_s=0.01)

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    _run(_exercise())
    assert not tick_calls
    skipped = [sig for sig, _ in events_log if sig == "manage.digest.tick.skipped"]
    assert skipped
    assert any(payload.get("reason") == "cortex_api_unhealthy" for _, payload in events_log)


@pytest.mark.offline
def test_tick_error_is_non_fatal(
    monkeypatch: pytest.MonkeyPatch, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    calls = {"n": 0}

    def flaky_tick(*, limit: int = 1) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return {"status": "ok", "results": [], "count": 0}

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.digest_tick_loop.ensure_digest_tick_env",
        lambda _root: True,
    )
    monkeypatch.setattr("cortex_store.digest_jobs.tick_jobs", flaky_tick)

    loop = _loop(service_state=_healthy_state(), interval_s=0.01)

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.08)
        await loop.stop()

    _run(_exercise())
    assert calls["n"] >= 2
    assert any(sig == "manage.digest.tick.error" for sig, _ in events_log)


@pytest.mark.offline
def test_shutdown_gate_digest_tick_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = ManageShutdownGate()
    tick_started = asyncio.Event()
    release_tick = asyncio.Event()

    def slow_tick(*, limit: int = 1) -> dict[str, Any]:
        tick_started.set()
        while not release_tick.is_set():
            pass
        return {"status": "ok", "results": [], "count": 0}

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.digest_tick_loop.ensure_digest_tick_env",
        lambda _root: True,
    )
    monkeypatch.setattr("cortex_store.digest_jobs.tick_jobs", slow_tick)

    loop = _loop(service_state=_healthy_state(), gate=gate, interval_s=0.05)

    async def _exercise() -> None:
        await loop.start()
        await tick_started.wait()
        snap = gate.snapshot()
        assert "digest_tick" in snap.activities
        release_tick.set()
        await asyncio.sleep(0.05)
        await loop.stop()
        assert "digest_tick" not in gate.snapshot().activities

    _run(_exercise())


@pytest.mark.offline
def test_stop_drains_in_flight_tick(
    monkeypatch: pytest.MonkeyPatch, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    tick_started = asyncio.Event()
    release_tick = asyncio.Event()
    finished = asyncio.Event()

    def slow_tick(*, limit: int = 1) -> dict[str, Any]:
        tick_started.set()
        while not release_tick.is_set():
            pass
        finished.set()
        return {"status": "ok", "results": [], "count": 0}

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.digest_tick_loop.ensure_digest_tick_env",
        lambda _root: True,
    )
    monkeypatch.setattr("cortex_store.digest_jobs.tick_jobs", slow_tick)

    loop = _loop(service_state=_healthy_state(), interval_s=1.0)

    async def _exercise() -> None:
        await loop.start()
        await tick_started.wait()
        stop_task = asyncio.create_task(loop.stop())
        await asyncio.sleep(0.02)
        release_tick.set()
        await stop_task
        await finished.wait()

    _run(_exercise())
    assert events_log[-1][0] == "manage.digest.tick.stopped"


@pytest.mark.offline
def test_ensure_digest_tick_env_overwrites_stale_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORTEX_DIGEST_EXTRACT_BACKEND", "stargate")
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.digest_tick_loop.build_service_env",
        lambda _root: {
            "CORTEX_DIGEST_EXTRACT_BACKEND": "cdp",
            "CORTEX_DIGEST_CDP_PROJECT_UUID": "proj-1",
            "PROJECT_ASK_URL": "http://127.0.0.1:8770",
            "CORTEX_DB_PATH": "/tmp/cortex.db",
        },
    )
    assert ensure_digest_tick_env(tmp_path) is True
    assert os.environ["CORTEX_DIGEST_EXTRACT_BACKEND"] == "cdp"


@pytest.mark.offline
def test_completed_event_when_jobs_advanced(
    monkeypatch: pytest.MonkeyPatch, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.digest_tick_loop.ensure_digest_tick_env",
        lambda _root: True,
    )
    monkeypatch.setattr(
        "cortex_store.digest_jobs.tick_jobs",
        lambda *, limit=1: {"status": "ok", "results": [{"state": "STAGED"}], "count": 1},
    )

    loop = _loop(service_state=_healthy_state(), interval_s=0.01)

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    _run(_exercise())
    completed = [
        payload for sig, payload in events_log if sig == "manage.digest.tick.completed"
    ]
    assert completed
    assert completed[0]["count"] == 1
