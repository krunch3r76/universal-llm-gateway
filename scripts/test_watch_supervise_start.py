"""Offline guards for watch-supervise.sh cmd_start detached spawn (a:39593)."""

from __future__ import annotations

from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent / "watch-supervise.sh"


@pytest.mark.offline
def test_cmd_start_detaches_stdin_and_closes_inherited_fds() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "_close_inherited_fds" in text
    assert "setsid nohup" in text
    assert "</dev/null" in text
    assert "a:39593" in text
