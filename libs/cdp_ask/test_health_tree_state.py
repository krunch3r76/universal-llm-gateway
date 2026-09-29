"""cdp_ask /health must not invoke git on the request path."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from unittest.mock import patch

import pytest
from deploy_identity.tree_state import (
    _reset_tree_state_cache_for_tests,
    note_tree_state,
)
from fastapi.testclient import TestClient

from cdp_ask.app import create_app
from cdp_ask.runner import (
    HarvestRootHealth,
    _reset_harvest_root_health_cache_for_tests,
    note_harvest_root_health,
    refresh_harvest_root_health,
)
from cdp_ask.standing_pins import (
    _reset_health_projection_cache_for_tests,
    note_health_projections,
)


def _disable_health_background_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDP_ASK_TREE_STATE_REFRESH", "0")
    monkeypatch.setenv("CDP_ASK_HEALTH_CACHE_REFRESH", "0")


def _stub_startup_health_refresh(
    monkeypatch: pytest.MonkeyPatch, root: str, *, harvest_ok: bool
) -> None:
    """Avoid NFS/subprocess work during TestClient startup lifespans."""

    def _harvest() -> HarvestRootHealth:
        state = HarvestRootHealth(root, harvest_ok)
        note_harvest_root_health(state)
        return state

    def _pins() -> tuple[dict[str, str], dict]:
        note_health_projections({}, {})
        return {}, {}

    monkeypatch.setattr("cdp_ask.runner.refresh_harvest_root_health", _harvest)
    monkeypatch.setattr("cdp_ask.standing_pins.refresh_health_projections", _pins)


@pytest.mark.offline
def test_health_zero_git_subprocess(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    _disable_health_background_refresh(monkeypatch)
    _reset_tree_state_cache_for_tests()
    _reset_harvest_root_health_cache_for_tests()
    _reset_health_projection_cache_for_tests()
    note_tree_state("clean")
    note_harvest_root_health(HarvestRootHealth(str(tmp_path), True))
    _stub_startup_health_refresh(monkeypatch, str(tmp_path), harvest_ok=True)
    app = create_app()
    with patch(
        "deploy_identity.tree_state.resolve_tree_state",
        side_effect=AssertionError("git on request path"),
    ):
        with TestClient(app) as client:
            payload = client.get("/health").json()
    assert payload["tree_state"] == "clean"


@pytest.mark.offline
def test_health_uses_peek_not_resolve(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    _disable_health_background_refresh(monkeypatch)
    _reset_tree_state_cache_for_tests()
    _reset_harvest_root_health_cache_for_tests()
    _reset_health_projection_cache_for_tests()
    note_tree_state("clean")
    note_harvest_root_health(HarvestRootHealth(str(tmp_path), True))
    _stub_startup_health_refresh(monkeypatch, str(tmp_path), harvest_ok=True)

    def _boom(root=None):
        time.sleep(10)
        return "dirty"

    app = create_app()
    with patch("deploy_identity.tree_state.resolve_tree_state", side_effect=_boom):
        with patch("cdp_ask.app.peek_tree_state", return_value="clean") as peek_mock:
            with TestClient(app) as client:
                started = time.monotonic()
                payload = client.get("/health").json()
                elapsed = time.monotonic() - started
    assert peek_mock.called
    assert elapsed < 2.0
    assert payload["tree_state"] == "clean"


@pytest.mark.offline
def test_health_tree_state_unknown_when_cache_stale(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    _disable_health_background_refresh(monkeypatch)
    _reset_tree_state_cache_for_tests()
    _reset_harvest_root_health_cache_for_tests()
    _reset_health_projection_cache_for_tests()
    import deploy_identity.tree_state as ts

    note_tree_state("clean")
    note_harvest_root_health(HarvestRootHealth(str(tmp_path), True))
    _stub_startup_health_refresh(monkeypatch, str(tmp_path), harvest_ok=True)
    monkeypatch.setattr(ts, "_TREE_STATE_CACHE_TTL_S", 0.0)
    app = create_app()
    with TestClient(app) as client:
        payload = client.get("/health").json()
    assert payload["tree_state"] == "unknown"


@pytest.mark.offline
def test_tree_state_refresh_loop_survives_refresh_exception(
    tmp_path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Same try/except guard as ``cdp_ask.app._tree_state_refresh_loop``."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    calls = {"n": 0}

    def _flaky_refresh() -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("injected refresh failure")

    async def _two_iterations() -> None:
        for _ in range(2):
            try:
                await asyncio.to_thread(_flaky_refresh)
            except Exception:  # noqa: BLE001 — matches app loop guard
                logging.getLogger("cdp_ask.app").warning(
                    "tree_state background refresh failed", exc_info=True
                )

    with caplog.at_level(logging.WARNING, logger="cdp_ask.app"):
        asyncio.run(_two_iterations())
    assert calls["n"] == 2
    assert "tree_state background refresh failed" in caplog.text


@pytest.mark.offline
def test_health_stalled_harvest_root_refresh_does_not_block_request_path(
    tmp_path, monkeypatch
) -> None:
    """Stalled background harvest-root stat must not block concurrent /health (AC2)."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    _disable_health_background_refresh(monkeypatch)
    _reset_tree_state_cache_for_tests()
    _reset_harvest_root_health_cache_for_tests()
    _reset_health_projection_cache_for_tests()
    note_tree_state("clean")

    def _pins() -> tuple[dict[str, str], dict]:
        note_health_projections({}, {})
        return {}, {}

    monkeypatch.setattr("cdp_ask.standing_pins.refresh_health_projections", _pins)

    accessible_calls = {"n": 0}

    def _blocking_accessible(_root) -> bool:
        accessible_calls["n"] += 1
        if accessible_calls["n"] >= 2:
            time.sleep(10)
        return False

    monkeypatch.setattr(
        "cdp_ask.runner._harvest_root_dir_accessible",
        _blocking_accessible,
    )

    app = create_app()
    with TestClient(app) as client:
        refresh_entered = threading.Event()

        def _run_refresh() -> None:
            refresh_entered.set()
            refresh_harvest_root_health()

        worker = threading.Thread(target=_run_refresh, daemon=True)
        worker.start()
        assert refresh_entered.wait(timeout=2.0)
        time.sleep(0.05)

        started = time.monotonic()
        payload = client.get("/health").json()
        elapsed = time.monotonic() - started

    assert elapsed < 2.0
    assert payload["harvest_root_ok"] is False
    assert payload["status"] == "ok"


@pytest.mark.offline
def test_health_request_path_no_subprocess(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    _disable_health_background_refresh(monkeypatch)
    _reset_tree_state_cache_for_tests()
    _reset_harvest_root_health_cache_for_tests()
    _reset_health_projection_cache_for_tests()
    note_tree_state("clean")
    note_harvest_root_health(HarvestRootHealth(str(tmp_path), True))
    _stub_startup_health_refresh(monkeypatch, str(tmp_path), harvest_ok=True)
    app = create_app()
    with TestClient(app) as client:
        with patch(
            "subprocess.run",
            side_effect=AssertionError("subprocess on /health request path"),
        ):
            payload = client.get("/health").json()
    assert payload["harvest_root_ok"] is True
