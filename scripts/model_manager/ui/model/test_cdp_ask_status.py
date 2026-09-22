"""Fail-class coverage for remote vs local cdp-ask status."""

from __future__ import annotations

import http.client
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.model_manager.ui.model import cdp_ask_status
from scripts.model_manager.ui.model.service_state import ServiceState, ServiceStatus

_REMOTE_URL = ("10.0.0.76", 8770, "http://10.0.0.76:8770")
_LOCAL_URL = ("127.0.0.1", 8770, "http://127.0.0.1:8770")


class _FakeResponse:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body


class _FakeHTTPConnection:
    def __init__(
        self,
        *_args: Any,
        exc: BaseException | None = None,
        resp: _FakeResponse | None = None,
        **_kwargs: Any,
    ) -> None:
        self._exc = exc
        self._resp = resp

    def request(self, _method: str, _path: str) -> None:
        return None

    def getresponse(self) -> _FakeResponse:
        if self._exc is not None:
            raise self._exc
        assert self._resp is not None
        return self._resp

    def close(self) -> None:
        return None


def _state(tmp_path: Path) -> ServiceState:
    return ServiceState(tmp_path)


def _forbid_hub_pid(state: ServiceState) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("remote status must not touch the hub pidfile")

    state._resolve_pid_file = _boom  # type: ignore[method-assign]
    state._write_pid_file = _boom  # type: ignore[method-assign]
    state._find_listener_pid = _boom  # type: ignore[method-assign]
    state._pid_alive = _boom  # type: ignore[method-assign]


def _patch_url(
    monkeypatch: pytest.MonkeyPatch,
    url: tuple[str, int, str],
    *,
    local: bool,
) -> None:
    monkeypatch.setattr(cdp_ask_status, "cdp_ask_manage_state", lambda: "enabled")
    monkeypatch.setattr(cdp_ask_status, "cdp_ask_url_config", lambda: url)
    monkeypatch.setattr(cdp_ask_status, "is_cdp_ask_local_host", lambda _host: local)


def _patch_http(
    monkeypatch: pytest.MonkeyPatch,
    *,
    exc: BaseException | None = None,
    resp: _FakeResponse | None = None,
) -> None:
    monkeypatch.setattr(
        http.client,
        "HTTPConnection",
        lambda *_a, **_k: _FakeHTTPConnection(exc=exc, resp=resp),
    )


def test_remote_health_ok_uses_body_pid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _state(tmp_path)
    _forbid_hub_pid(state)
    _patch_url(monkeypatch, _REMOTE_URL, local=False)
    state._port_open = lambda _port, _host: True  # type: ignore[method-assign]
    body = json.dumps(
        {"status": "ok", "pid": 289473, "registry_hygiene": "running"}
    ).encode()
    _patch_http(monkeypatch, resp=_FakeResponse(200, body))

    info = state.check_cdp_ask()

    assert info.status is ServiceStatus.RUNNING
    assert info.pid == 289473
    assert info.detail == "PID 289473; registry_hygiene=running"
    assert "PID file missing" not in info.detail


def test_remote_timeout_while_port_open_is_unhealthy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _state(tmp_path)
    _forbid_hub_pid(state)
    _patch_url(monkeypatch, _REMOTE_URL, local=False)
    state._port_open = lambda _port, _host: True  # type: ignore[method-assign]
    _patch_http(monkeypatch, exc=TimeoutError("read timed out"))

    info = state.check_cdp_ask()

    assert info.status is ServiceStatus.UNHEALTHY
    assert info.status is not ServiceStatus.STOPPED
    assert info.detail == "health probe failed: TimeoutError"
    assert "PID file missing" not in info.detail


def test_remote_port_closed_is_stopped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _state(tmp_path)
    _forbid_hub_pid(state)
    _patch_url(monkeypatch, _REMOTE_URL, local=False)
    state._port_open = lambda _port, _host: False  # type: ignore[method-assign]

    def _http_must_not_run(*_a: Any, **_k: Any) -> _FakeHTTPConnection:
        raise AssertionError("closed port must not GET /health")

    monkeypatch.setattr(http.client, "HTTPConnection", _http_must_not_run)

    info = state.check_cdp_ask()

    assert info.status is ServiceStatus.STOPPED
    assert info.detail == ""
    assert info.pid is None


def test_local_pidfile_health_ok_keeps_file_pid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _state(tmp_path)
    _patch_url(monkeypatch, _LOCAL_URL, local=True)
    state._resolve_pid_file = lambda _path: (42, None)  # type: ignore[method-assign]
    state._port_open = lambda _port, _host: True  # type: ignore[method-assign]
    state._find_listener_pid = lambda _port: None  # type: ignore[method-assign]
    state._proc_uptime_str = lambda _pid: "5m 1s"  # type: ignore[method-assign]
    body = json.dumps(
        {"status": "ok", "pid": 289473, "registry_hygiene": "running"}
    ).encode()
    _patch_http(monkeypatch, resp=_FakeResponse(200, body))

    info = state.check_cdp_ask()

    assert info.status is ServiceStatus.RUNNING
    assert info.pid == 42
    assert info.detail == "PID 42 (5m 1s); registry_hygiene=running"
