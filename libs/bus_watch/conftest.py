"""Shared pytest fixtures for ``libs/bus_watch``."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

_CURSOR_GUI_HOST_ENV_KEYS = (
    "VSCODE_AGENT_FOLDER",
    "CURSOR_RIPGREP_PATH",
    "CURSOR_DATA_DIR",
    "SSH_CONNECTION",
    "DISPLAY",
)


def _resolve_subscription_title_offline(
    root_id: str,
    *,
    slug: str | None = None,
    existing_title: str | None = None,
    client: Any = None,
) -> str:
    """``resolve_subscription_title`` without agent-bus slug fetch (pytest)."""
    root = str(root_id).strip()
    existing = str(existing_title or "").strip()
    bare = f"{root} "
    if existing and existing not in {bare, root}:
        return existing
    slug_val = str(slug or "").strip()
    if slug_val:
        return f"{root} {slug_val}"
    return bare


def apply_xvfb_policy_test_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point liaison tick/policy writes at ``tmp_path``; stub bus-backed title fetch."""
    watch = tmp_path / "liaison-watch"
    watch.mkdir()
    monkeypatch.setattr("bus_watch.fable_lock.WATCH_DIR", watch)

    import bus_watch.cursor_xvfb_subscription as xvfb_sub

    monkeypatch.setattr(xvfb_sub, "WATCH_DIR", watch)
    kw = dict(xvfb_sub.sync_policy_gui_host.__kwdefaults__ or {})
    kw["watch_dir"] = watch
    xvfb_sub.sync_policy_gui_host.__kwdefaults__ = kw

    monkeypatch.setattr(xvfb_sub, "_fetch_thread_slug", lambda *a, **k: None)
    monkeypatch.setattr(
        xvfb_sub,
        "resolve_subscription_title",
        _resolve_subscription_title_offline,
    )
    try:
        import tools.continuity_ops as continuity_ops
    except ImportError:
        pass
    else:
        monkeypatch.setattr(
            continuity_ops,
            "resolve_subscription_title",
            _resolve_subscription_title_offline,
        )
    return watch


@pytest.fixture(scope="session", autouse=True)
def _isolate_dispatch_ledger(tmp_path_factory: pytest.TempPathFactory):
    """Point ``DATA_DIR`` at a tmp dir for the whole bus_watch session.

    Without this, ledger paths fall back to ``~/.gateway`` and pytest refuses
    to open the live dispatch ledger (``ledger_pytest_guard``). Same shape as
    ``services/git_integration_worker/tests/conftest.py::_isolate_dispatch_ledger``.
    """
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CURSOR_SDK_DISPATCH_LEDGER_ENV,
        CursorDispatchLedger,
    )
    from services.git_integration_worker.seat_write_ledger import SeatWriteLedger

    data_dir = tmp_path_factory.mktemp("bus-watch-data-dir")
    isolated_ledger = data_dir / "cursor-sdk-dispatch.db"
    prior_data_dir = os.environ.get("DATA_DIR")
    prior_dispatch_ledger = os.environ.get(CURSOR_SDK_DISPATCH_LEDGER_ENV)
    os.environ["DATA_DIR"] = str(data_dir)
    os.environ[CURSOR_SDK_DISPATCH_LEDGER_ENV] = str(isolated_ledger)
    SeatWriteLedger.reset_instance()
    CursorDispatchLedger._instance = None
    yield data_dir
    if prior_data_dir is None:
        os.environ.pop("DATA_DIR", None)
    else:
        os.environ["DATA_DIR"] = prior_data_dir
    if prior_dispatch_ledger is None:
        os.environ.pop(CURSOR_SDK_DISPATCH_LEDGER_ENV, None)
    else:
        os.environ[CURSOR_SDK_DISPATCH_LEDGER_ENV] = prior_dispatch_ledger
    SeatWriteLedger.reset_instance()
    CursorDispatchLedger._instance = None


@pytest.fixture(autouse=True)
def _pin_liaison_pager_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep liaison pager off a live email-bridge socket even if env is poisoned."""
    import bus_watch.liaison_pager as liaison_pager

    monkeypatch.setattr(
        liaison_pager,
        "_EMAIL_BRIDGE_SOCK",
        "/nonexistent/pytest-bus-watch-liaison-pager",
    )


@pytest.fixture(autouse=True)
def _isolate_liaison_watch_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Keep ``sync_policy_gui_host`` / fable lock paths off live ``tmp/watchers``."""
    return apply_xvfb_policy_test_isolation(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def _clear_detect_cursor_gui_host_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``detect_cursor_gui_host`` tests set only the env signals they assert on."""
    for key in _CURSOR_GUI_HOST_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
