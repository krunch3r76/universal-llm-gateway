"""Dispatch ledger path must not follow a swapped per-dispatch HOME (a:36673)."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CURSOR_SDK_DISPATCH_LEDGER_ENV,
    resolve_cursor_sdk_dispatch_ledger_path,
)

pytestmark = pytest.mark.offline


def test_ledger_path_uses_operator_home_when_home_is_dispatch_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatch_root = tmp_path / "cursor-dispatch-homes"
    dispatch_home = dispatch_root / "auto-dispatch-home"
    dispatch_home.mkdir(parents=True)
    operator_home = tmp_path / "operator-home"
    operator_home.mkdir()
    expected = (operator_home / ".gateway" / "cursor-sdk-dispatch.db").resolve()

    import services.git_integration_worker.cursor_dispatch_ledger as ledger_mod
    import services.git_integration_worker.cursor_home as home_mod

    monkeypatch.setattr(home_mod, "_DISPATCH_HOME_ROOT", dispatch_root)
    monkeypatch.setenv("HOME", str(dispatch_home))
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv(CURSOR_SDK_DISPATCH_LEDGER_ENV, raising=False)
    monkeypatch.setattr(ledger_mod, "operator_real_home", lambda **_: operator_home)

    assert resolve_cursor_sdk_dispatch_ledger_path() == expected


def test_ledger_path_honors_cursor_sdk_dispatch_ledger_env_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatch_root = tmp_path / "cursor-dispatch-homes"
    dispatch_home = dispatch_root / "auto-dispatch-home"
    dispatch_home.mkdir(parents=True)
    pinned = (tmp_path / "production" / "cursor-sdk-dispatch.db").resolve()
    monkeypatch.setenv(CURSOR_SDK_DISPATCH_LEDGER_ENV, str(pinned))
    monkeypatch.setenv("HOME", str(dispatch_home))
    monkeypatch.delenv("DATA_DIR", raising=False)

    assert resolve_cursor_sdk_dispatch_ledger_path() == pinned
