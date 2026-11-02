"""Unified GIW base URL builders must share giw_endpoint resolution."""

from __future__ import annotations

import pytest

from systems.cursor_catalog.config import parse_cursor_catalog_config
from systems.frontier_consult import cursor_sdk_worker_dispatch
from systems.proxy.routers.api import providers_cursor


@pytest.fixture(autouse=True)
def _clear_giw_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_INTEGRATION_WORKER_URL", raising=False)
    monkeypatch.delenv("GIT_INTEGRATION_WORKER_HOST", raising=False)
    monkeypatch.delenv("GIT_INTEGRATION_WORKER_PORT", raising=False)


def _all_three() -> tuple[str, str, str]:
    proxy = providers_cursor._worker_base_url()
    dispatch = cursor_sdk_worker_dispatch.worker_base_url()
    catalog = parse_cursor_catalog_config().worker_url
    return proxy, dispatch, catalog


def test_url_env_wins_over_host_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_URL", "http://url-wins.test:7001/")
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_HOST", "10.9.9.9")
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_PORT", "7777")
    proxy, dispatch, catalog = _all_three()
    expected = "http://url-wins.test:7001"
    assert proxy == expected
    assert dispatch == expected
    assert catalog == expected


def test_host_port_when_url_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_INTEGRATION_WORKER_URL", raising=False)
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_HOST", "10.9.9.9")
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_PORT", "7777")
    proxy, dispatch, catalog = _all_three()
    expected = "http://10.9.9.9:7777"
    assert proxy == expected
    assert dispatch == expected
    assert catalog == expected
