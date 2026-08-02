"""Regression tests — pager fail-closed under pytest + passwd-home state dir."""

from __future__ import annotations

import os
import pwd
from pathlib import Path
from unittest.mock import patch

import pytest

from pager_notify.client import NotifyResult, notify_pager, pager_enabled
from pager_notify.state import state_dir


def test_pager_enabled_false_under_pytest_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PAGER_NOTIFY_ENABLED", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_module.py::test_x")
    assert pager_enabled() is False


def test_pager_enabled_true_when_explicit_under_pytest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAGER_NOTIFY_ENABLED", "1")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_module.py::test_x")
    assert pager_enabled() is True


def test_pager_enabled_false_when_explicit_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAGER_NOTIFY_ENABLED", "0")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert pager_enabled() is False


@pytest.mark.asyncio
async def test_notify_pager_failed_pytest_without_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PAGER_NOTIFY_ENABLED", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_module.py::test_x")

    with patch("pager_notify.client.make_async_client") as mock_client:
        result = await notify_pager("subject", "body", tag="t")

    assert result == NotifyResult.failed("pytest")
    mock_client.assert_not_called()


def test_default_state_dir_ignores_poisoned_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", "/tmp/fake/cursor-dispatch-homes/auto-x-home")
    monkeypatch.delenv("PAGER_NOTIFY_STATE_DIR", raising=False)

    passwd_home = Path(pwd.getpwuid(os.getuid()).pw_dir).expanduser()
    resolved = state_dir()

    assert str(resolved).startswith(str(passwd_home))
    assert "cursor-dispatch-homes" not in str(resolved)


def test_state_dir_honors_env_set_after_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    custom = tmp_path / "late-bound"
    monkeypatch.setenv("PAGER_NOTIFY_STATE_DIR", str(custom))
    assert state_dir() == custom
