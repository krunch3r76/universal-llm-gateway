"""StoreClient GIW URL resolves at construction time from env."""

from __future__ import annotations

import pytest

from bus_watch.endpoint_wake_transport import StoreClient


@pytest.fixture(autouse=True)
def _clear_giw_url_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_INTEGRATION_WORKER_URL", raising=False)
    monkeypatch.delenv("GIT_INTEGRATION_WORKER_HOST", raising=False)
    monkeypatch.delenv("GIT_INTEGRATION_WORKER_PORT", raising=False)


def test_store_client_reads_giw_url_from_env_after_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import bus_watch.endpoint_wake_transport as mod

    monkeypatch.setenv("GIT_INTEGRATION_WORKER_URL", "http://giw-env.test:8092")
    assert mod.StoreClient().giw_url == "http://giw-env.test:8092"
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_URL", "http://giw-env.test:8093")
    assert StoreClient().giw_url == "http://giw-env.test:8093"


def test_store_client_explicit_giw_url_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_URL", "http://giw-env.test:8092")
    client = StoreClient(giw_url="http://explicit.test:1")
    assert client.giw_url == "http://explicit.test:1"
