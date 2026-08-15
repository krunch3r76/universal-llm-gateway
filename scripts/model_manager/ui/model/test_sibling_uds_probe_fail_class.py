"""Fail-class coverage for cloud-proxy and generic UDS health probes."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.model_manager.ui.model.service_state import (
    ServiceOwnership,
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


def test_cloud_proxy_probe_ok_returns_true_and_no_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "transport_utils.make_sync_client",
        lambda *_a, **_k: _FakeClient(resp=_FakeResp(200)),
    )
    healthy, note = _state(tmp_path)._cloud_proxy_probe_uds(tmp_path / "cp.sock")
    assert healthy is True
    assert note is None


def test_cloud_proxy_probe_non_200_is_fail_closed_with_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "transport_utils.make_sync_client",
        lambda *_a, **_k: _FakeClient(resp=_FakeResp(503)),
    )
    healthy, note = _state(tmp_path)._cloud_proxy_probe_uds(tmp_path / "cp.sock")
    assert healthy is False
    assert note == "/health returned 503"


def test_cloud_proxy_probe_timeout_records_exception_class(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "transport_utils.make_sync_client",
        lambda *_a, **_k: _FakeClient(exc=TimeoutError("read timed out")),
    )
    healthy, note = _state(tmp_path)._cloud_proxy_probe_uds(tmp_path / "cp.sock")
    assert healthy is False
    assert note == "TimeoutError"


def test_cloud_proxy_probe_connect_fail_records_exception_class(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "transport_utils.make_sync_client",
        lambda *_a, **_k: _FakeClient(exc=ConnectionRefusedError("refused")),
    )
    healthy, note = _state(tmp_path)._cloud_proxy_probe_uds(tmp_path / "cp.sock")
    assert healthy is False
    assert note == "ConnectionRefusedError"


def test_check_cloud_proxy_uds_probe_failed_includes_exception_class(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sock = tmp_path / "cloud-proxy.sock"
    sock.touch()
    state = _state(tmp_path)
    monkeypatch.setattr(
        state,
        "_cloud_proxy_probe_uds",
        lambda _path: (False, "TimeoutError"),
    )
    monkeypatch.setattr(state, "_find_unix_listener_pid", lambda _path: None)
    monkeypatch.setattr(state, "_proc_uptime_str", lambda _pid: "3h 12m")
    monkeypatch.setattr(
        "scripts.model_manager.ui.model.service_state.read_cloud_proxy_socket_path",
        lambda: sock,
    )
    monkeypatch.setattr(
        state,
        "_resolve_pid_file",
        lambda _path: (4242, None),
    )
    info = state._check_cloud_proxy_uds()
    assert info.status is ServiceStatus.UNHEALTHY
    assert info.detail == "PID 4242 (3h 12m), probe failed (TimeoutError)"
    assert info.pid == 4242
    assert info.health_url == f"unix://{sock}/health"


def test_probe_uds_health_ok_returns_true_and_no_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "transport_utils.make_sync_client",
        lambda *_a, **_k: _FakeClient(resp=_FakeResp(200)),
    )
    healthy, note = _state(tmp_path)._probe_uds_health(tmp_path / "ev.sock", "/health")
    assert healthy is True
    assert note is None


def test_probe_uds_health_non_200_names_endpoint_and_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "transport_utils.make_sync_client",
        lambda *_a, **_k: _FakeClient(resp=_FakeResp(502)),
    )
    healthy, note = _state(tmp_path)._probe_uds_health(tmp_path / "ev.sock", "/health")
    assert healthy is False
    assert note == "/health returned 502"


def test_probe_uds_health_timeout_records_exception_class(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "transport_utils.make_sync_client",
        lambda *_a, **_k: _FakeClient(exc=TimeoutError("read timed out")),
    )
    healthy, note = _state(tmp_path)._probe_uds_health(tmp_path / "ev.sock", "/ready")
    assert healthy is False
    assert note == "TimeoutError"


def test_probe_uds_health_connect_fail_records_exception_class(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "transport_utils.make_sync_client",
        lambda *_a, **_k: _FakeClient(exc=ConnectionRefusedError("refused")),
    )
    healthy, note = _state(tmp_path)._probe_uds_health(tmp_path / "ev.sock")
    assert healthy is False
    assert note == "ConnectionRefusedError"


def test_check_uds_service_probe_failed_includes_exception_class(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sock = tmp_path / "events-query.sock"
    sock.touch()
    pid_file = tmp_path / "events.pid"
    state = _state(tmp_path)
    monkeypatch.setattr(
        state,
        "_probe_uds_health",
        lambda _path, _ep="/health": (False, "TimeoutError"),
    )
    monkeypatch.setattr(state, "_find_unix_listener_pid", lambda _path: None)
    monkeypatch.setattr(state, "_proc_uptime_str", lambda _pid: "1h 4m")
    monkeypatch.setattr(
        state,
        "_resolve_pid_file",
        lambda _path: (9090, None),
    )
    monkeypatch.setattr(state, "_pid_is_managed", lambda _pid, _pred: True)
    info = state._check_uds_service(
        name="Events",
        pid_file=pid_file,
        socket_path=sock,
        health_endpoint="/health",
    )
    assert info.status is ServiceStatus.UNHEALTHY
    assert info.detail == "PID 9090 (1h 4m), probe failed (TimeoutError)"
    assert info.pid == 9090
    assert info.health_url == f"unix://{sock}/health"
    assert info.ownership is ServiceOwnership.MANAGED
