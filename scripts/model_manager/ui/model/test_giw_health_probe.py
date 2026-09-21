"""Fail-class coverage for the GIW TCP /health probe."""

from __future__ import annotations

import http.client
from pathlib import Path
from typing import Any

import pytest

from scripts.model_manager.ui.model.service_state import (
    ServiceState,
    ServiceStatus,
)


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
        self._call_count = 0

    def request(self, _method: str, _path: str) -> None:
        return None

    def getresponse(self) -> _FakeResponse:
        self._call_count += 1
        if self._exc is not None:
            raise self._exc
        assert self._resp is not None
        return self._resp

    def close(self) -> None:
        return None


def _state(tmp_path: Path) -> ServiceState:
    return ServiceState(tmp_path)


def test_giw_probe_ok_returns_true_and_no_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        http.client,
        "HTTPConnection",
        lambda *_a, **_k: _FakeHTTPConnection(
            resp=_FakeResponse(200, b'{"status":"ok"}')
        ),
    )
    healthy, note = _state(tmp_path)._git_integration_worker_probe_http("127.0.0.1", 8091)
    assert healthy is True
    assert note is None


def test_giw_probe_non_200_is_fail_closed_with_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        http.client,
        "HTTPConnection",
        lambda *_a, **_k: _FakeHTTPConnection(resp=_FakeResponse(503)),
    )
    healthy, note = _state(tmp_path)._git_integration_worker_probe_http("127.0.0.1", 8091)
    assert healthy is False
    assert note == "/health returned 503"


def test_giw_probe_timeout_records_exception_class(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[_FakeHTTPConnection] = []

    def _factory(*_a: Any, **_k: Any) -> _FakeHTTPConnection:
        conn = _FakeHTTPConnection(exc=TimeoutError("read timed out"))
        calls.append(conn)
        return conn

    monkeypatch.setattr(http.client, "HTTPConnection", _factory)
    healthy, note = _state(tmp_path)._git_integration_worker_probe_http("127.0.0.1", 8091)
    assert healthy is False
    assert note == "TimeoutError"
    assert len(calls) == 2


def test_giw_probe_connect_fail_records_exception_class(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        http.client,
        "HTTPConnection",
        lambda *_a, **_k: _FakeHTTPConnection(
            exc=ConnectionRefusedError("refused")
        ),
    )
    healthy, note = _state(tmp_path)._git_integration_worker_probe_http("127.0.0.1", 8091)
    assert healthy is False
    assert note == "ConnectionRefusedError"


def test_giw_probe_timeout_first_ok_second(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    responses = [
        _FakeHTTPConnection(exc=TimeoutError("read timed out")),
        _FakeHTTPConnection(resp=_FakeResponse(200, b'{"status":"ok"}')),
    ]

    def _factory(*_a: Any, **_k: Any) -> _FakeHTTPConnection:
        return responses.pop(0)

    monkeypatch.setattr(http.client, "HTTPConnection", _factory)
    healthy, note = _state(tmp_path)._git_integration_worker_probe_http("127.0.0.1", 8091)
    assert healthy is True
    assert note is None


def test_check_giw_probe_failed_includes_exception_class(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _state(tmp_path)
    monkeypatch.setattr(state, "_resolve_pid_file", lambda _path: (4054740, None))
    monkeypatch.setattr(state, "_port_open", lambda _port, _host: True)
    monkeypatch.setattr(state, "_find_listener_pid", lambda _port: None)
    monkeypatch.setattr(
        state,
        "_git_integration_worker_probe_http",
        lambda _host, _port: (False, "TimeoutError"),
    )
    monkeypatch.setattr(state, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(state, "_proc_uptime_str", lambda _pid: "1h 1m")
    info = state.check_git_integration_worker()
    assert info.status is ServiceStatus.RUNNING
    assert info.detail == "PID 4054740 (1h 1m), probe failed (TimeoutError)"


def test_check_giw_timeout_both_pid_port_live_not_unhealthy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _state(tmp_path)
    monkeypatch.setattr(state, "_resolve_pid_file", lambda _path: (4054740, None))
    monkeypatch.setattr(state, "_port_open", lambda _port, _host: True)
    monkeypatch.setattr(state, "_find_listener_pid", lambda _port: None)
    monkeypatch.setattr(
        state,
        "_git_integration_worker_probe_http",
        lambda _host, _port: (False, "TimeoutError"),
    )
    monkeypatch.setattr(state, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(state, "_proc_uptime_str", lambda _pid: "1h 1m")
    info = state.check_git_integration_worker()
    assert info.status is ServiceStatus.RUNNING
    assert "probe failed (TimeoutError)" in info.detail


def test_check_giw_dead_pid_port_open_unhealthy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _state(tmp_path)
    monkeypatch.setattr(state, "_resolve_pid_file", lambda _path: (999999, None))
    monkeypatch.setattr(state, "_port_open", lambda _port, _host: True)
    monkeypatch.setattr(state, "_find_listener_pid", lambda _port: None)
    monkeypatch.setattr(
        state,
        "_git_integration_worker_probe_http",
        lambda _host, _port: (False, "TimeoutError"),
    )
    monkeypatch.setattr(state, "_pid_alive", lambda _pid: False)
    monkeypatch.setattr(state, "_proc_uptime_str", lambda _pid: None)
    info = state.check_git_integration_worker()
    assert info.status is ServiceStatus.UNHEALTHY
    assert "probe failed (TimeoutError)" in info.detail
