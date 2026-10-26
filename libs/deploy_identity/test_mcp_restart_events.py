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
    monkeypatch.setattr(
        "scripts.model_manager.observation_event._emit_sync",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("refused")),
    )
    emit_restart_event("mcp.restart.ok", {"stamp_sha": "a" * 40, "health_code_version": "a" * 40})
