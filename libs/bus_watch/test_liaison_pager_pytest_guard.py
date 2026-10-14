"""Regression — ``page_liaison`` must not curl email-bridge under pytest."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import bus_watch.liaison_pager as liaison_pager
from bus_watch.liaison_pager import page_liaison


def test_page_liaison_blocked_under_pytest_even_when_socket_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    sock = tmp_path / "fake-bridge.sock"
    sock.write_text("", encoding="utf-8")
    monkeypatch.setattr(liaison_pager, "_EMAIL_BRIDGE_SOCK", str(sock))
    monkeypatch.setenv("PYTEST_CURRENT_TEST", f"{__file__}::test_x")

    with patch("bus_watch.liaison_pager.subprocess.run") as mock_run:
        result = page_liaison("10479", "subject", "body")

    mock_run.assert_not_called()
    assert result == {"ok": False, "reason": "pytest"}


def test_page_liaison_missing_socket_when_not_pytest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(
        liaison_pager,
        "_EMAIL_BRIDGE_SOCK",
        "/nonexistent/pytest-bus-watch-liaison-pager-off",
    )

    with patch("bus_watch.liaison_pager.subprocess.run") as mock_run:
        result = page_liaison("10479", "subject", "body")

    mock_run.assert_not_called()
    assert result == {"ok": False, "reason": "pager_unavailable"}
