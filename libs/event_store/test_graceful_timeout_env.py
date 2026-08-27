"""Tests for EVENT_STORE_GRACEFUL_TIMEOUT_S uvicorn Config parameterization."""

from __future__ import annotations

import pytest

from event_store.server import _graceful_shutdown_timeout_kwargs, _uvicorn_query_config_kwargs


def test_unset_env_preserves_default_uvicorn_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EVENT_STORE_GRACEFUL_TIMEOUT_S", raising=False)
    assert _graceful_shutdown_timeout_kwargs() == {}

    uds_kwargs = {"uds": "/tmp/events-query.sock", **_uvicorn_query_config_kwargs()}
    tcp_kwargs = {
        "host": "0.0.0.0",
        "port": 7102,
        **_uvicorn_query_config_kwargs(),
    }
    assert "timeout_graceful_shutdown" not in uds_kwargs
    assert "timeout_graceful_shutdown" not in tcp_kwargs
    assert uds_kwargs["log_level"] == "warning"
    assert tcp_kwargs["access_log"] is False


def test_env_one_sets_timeout_on_both_config_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVENT_STORE_GRACEFUL_TIMEOUT_S", "1")
    assert _graceful_shutdown_timeout_kwargs() == {"timeout_graceful_shutdown": 1}

    uds_kwargs = {"uds": "/tmp/events-query.sock", **_uvicorn_query_config_kwargs()}
    tcp_kwargs = {
        "host": "0.0.0.0",
        "port": 7102,
        **_uvicorn_query_config_kwargs(),
    }
    assert uds_kwargs["timeout_graceful_shutdown"] == 1
    assert tcp_kwargs["timeout_graceful_shutdown"] == 1
