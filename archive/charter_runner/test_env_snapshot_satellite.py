"""Offline tests for live EnvSnapshot.satellite_health probing (G4a / P4-AC1)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scripts.model_manager.ui.controller.charter_runner import env_snapshot as es


@pytest.mark.offline
@pytest.mark.asyncio
async def test_probe_satellite_health_unconfigured_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.model_manager.ui.controller import service_config as sc

    monkeypatch.setattr(sc, "cdp_ask_url_config", lambda: None)
    got = await es.probe_satellite_health()
    assert got == {"cdp": "unknown", "project_ask": "unknown"}


@pytest.mark.offline
@pytest.mark.asyncio
async def test_probe_cdp_health_connect_error_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BoomClient:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, path: str) -> Any:
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(
        "transport_utils.make_async_client",
        lambda *a, **k: _BoomClient(),
    )
    state = await es._probe_cdp_health("127.0.0.1", 8770, "http://127.0.0.1:8770")
    assert state == "down"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_probe_cdp_health_timeout_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _SlowClient:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, path: str) -> Any:
            raise httpx.TimeoutException("slow")

    monkeypatch.setattr(
        "transport_utils.make_async_client",
        lambda *a, **k: _SlowClient(),
    )
    state = await es._probe_cdp_health("127.0.0.1", 8770, "http://127.0.0.1:8770")
    assert state == "unknown"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_probe_cdp_health_ok_is_up(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value={"status": "ok", "registry_hygiene": "running"})

    class _OkClient:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, path: str) -> Any:
            assert path == "/health"
            return resp

    monkeypatch.setattr(
        "transport_utils.make_async_client",
        lambda *a, **k: _OkClient(),
    )
    state = await es._probe_cdp_health("127.0.0.1", 8770, "http://127.0.0.1:8770")
    assert state == "up"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_build_env_snapshot_uses_live_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        es,
        "probe_satellite_health",
        AsyncMock(return_value={"cdp": "down", "project_ask": "down"}),
    )
    monkeypatch.setattr(
        es,
        "resolve_attendance",
        AsyncMock(return_value="autonomous"),
    )
    snap = await es.build_env_snapshot(root_ids=["6091"])
    assert snap.satellite_health == {"cdp": "down", "project_ask": "down"}
    assert snap.substrate_up() is False
