"""Unit tests for MCP restart observation event publishing."""

from __future__ import annotations

import pytest

from deploy_identity.mcp_restart_events import emit_restart_event

pytestmark = pytest.mark.offline


def test_emit_restart_event_publish_error_writes_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("socket down")

    monkeypatch.setattr(
        "scripts.model_manager.observation_event._emit_sync",
        _boom,
    )
    emit_restart_event("mcp.restart.log", {"detail": "probe failed"})
    err = capsys.readouterr().err
    assert "mcp_restart_event publish failed" in err
    assert "mcp.restart.log" in err
    assert "RuntimeError" in err
    assert "socket down" in err


def test_emit_restart_event_publish_error_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_EVENTS_ENABLE", "1")
    monkeypatch.setattr(
        "scripts.model_manager.observation_event._emit_sync",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("refused")),
    )
    emit_restart_event(
        "mcp.restart.ok", {"stamp_sha": "a" * 40, "health_code_version": "a" * 40}
    )


def test_emit_restart_event_skips_live_publish_under_pytest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect_calls: list[object] = []

    def _track_connect(self: object, *_args: object, **_kwargs: object) -> None:
        connect_calls.append(self)

    monkeypatch.delenv("ULG_EVENTS_ENABLE", raising=False)
    monkeypatch.setenv("ULG_EVENTS_DISABLED", "1")
    import socket

    monkeypatch.setattr(socket.socket, "connect", _track_connect)
    emit_restart_event("mcp.restart.log", {"detail": "pytest must not publish"})
    assert connect_calls == []


def test_mcp_restart_gate_cli_skips_live_publish_in_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    python = Path.home() / ".venvs/universal/bin/python"
    env = {**dict(__import__("os").environ), "ULG_EVENTS_DISABLED": "1"}
    proc = subprocess.run(
        [
            str(python),
            "-m",
            "deploy_identity.mcp_restart_gate",
            "--workspace-root",
            str(repo),
            "emit-log",
            "subprocess guard probe",
        ],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "mcp_restart_event publish failed" not in proc.stderr
