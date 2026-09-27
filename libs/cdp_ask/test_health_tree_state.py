"""cdp_ask /health must not invoke git on the request path."""

from __future__ import annotations

import asyncio
import logging
import time
from unittest.mock import patch

import pytest
from deploy_identity.tree_state import (
    _reset_tree_state_cache_for_tests,
    note_tree_state,
)
from fastapi.testclient import TestClient

from cdp_ask.app import create_app


@pytest.mark.offline
def test_health_zero_git_subprocess(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("CDP_ASK_TREE_STATE_REFRESH", "0")
    _reset_tree_state_cache_for_tests()
    note_tree_state("clean")
    app = create_app()
    with patch(
        "deploy_identity.tree_state.resolve_tree_state",
        side_effect=AssertionError("git on request path"),
    ):
        with patch("cdp_ask.standing_pins.probe_health", return_value=({}, {})):
            with TestClient(app) as client:
                payload = client.get("/health").json()
    assert payload["tree_state"] == "clean"


@pytest.mark.offline
def test_health_uses_peek_not_resolve(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("CDP_ASK_TREE_STATE_REFRESH", "0")
    _reset_tree_state_cache_for_tests()
    note_tree_state("clean")

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
    monkeypatch.setenv("CDP_ASK_TREE_STATE_REFRESH", "0")
    _reset_tree_state_cache_for_tests()
    import deploy_identity.tree_state as ts

    note_tree_state("clean")
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
