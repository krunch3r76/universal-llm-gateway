"""Fail-class coverage for the RAG UDS /stats health probe."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.model_manager.ui.model.service_state import (
    ServiceState,
    ServiceStatus,
)


class _FakeResp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeClient:
    def __init__(
        self, *, resp: _FakeResp | None = None, exc: BaseException | None = None
    ) -> None:
        self._resp = resp
        self._exc = exc

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def get(self, _path: str) -> _FakeResp:
        if self._exc is not None:
            raise self._exc
        assert self._resp is not None
        return self._resp


def _state(tmp_path: Path) -> ServiceState:
    return ServiceState(tmp_path)


def test_probe_ok_returns_true_and_no_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "transport_utils.make_sync_client",
        lambda *_a, **_k: _FakeClient(resp=_FakeResp(200)),
    )
    healthy, note = _state(tmp_path)._rag_probe_uds(tmp_path / "rag.sock")
    assert healthy is True
    assert note is None


def test_probe_non_200_is_fail_closed_with_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "transport_utils.make_sync_client",
        lambda *_a, **_k: _FakeClient(resp=_FakeResp(503)),
    )
    healthy, note = _state(tmp_path)._rag_probe_uds(tmp_path / "rag.sock")
    assert healthy is False
    assert note == "/stats returned 503"


def test_probe_timeout_records_exception_class(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "transport_utils.make_sync_client",
        lambda *_a, **_k: _FakeClient(exc=TimeoutError("read timed out")),
    )
    healthy, note = _state(tmp_path)._rag_probe_uds(tmp_path / "rag.sock")
    assert healthy is False
    assert note == "TimeoutError"


def test_probe_connect_fail_records_exception_class(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "transport_utils.make_sync_client",
        lambda *_a, **_k: _FakeClient(exc=ConnectionRefusedError("refused")),
    )
    healthy, note = _state(tmp_path)._rag_probe_uds(tmp_path / "rag.sock")
    assert healthy is False
    assert note == "ConnectionRefusedError"


def test_check_rag_uds_socket_not_ready_skips_probe(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sock"
    info = _state(tmp_path)._check_rag_uds(3471126, missing, None)
    assert info.status is ServiceStatus.UNHEALTHY
    assert info.detail == "PID 3471126, socket not ready"
    assert info.pid == 3471126
    assert str(missing) in (info.health_url or "")


def test_check_rag_uds_probe_failed_includes_exception_class(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sock = tmp_path / "rag.sock"
    sock.touch()
    state = _state(tmp_path)
    monkeypatch.setattr(
        state,
        "_rag_probe_uds",
        lambda _path: (False, "TimeoutError"),
    )
    monkeypatch.setattr(state, "_find_unix_listener_pid", lambda _path: None)
    monkeypatch.setattr(state, "_proc_uptime_str", lambda _pid: "17h 42m")
    info = state._check_rag_uds(3471126, sock, None)
    assert info.status is ServiceStatus.UNHEALTHY
    assert info.detail == "PID 3471126 (17h 42m), probe failed (TimeoutError)"
    assert info.pid == 3471126


def test_check_rag_uds_stopped_when_no_pid_and_no_socket(tmp_path: Path) -> None:
    info = _state(tmp_path)._check_rag_uds(None, tmp_path / "missing.sock", None)
    assert info.status is ServiceStatus.STOPPED
    assert info.detail == ""
