"""Shared pytest fixtures for ``libs/bus_watch``."""

from __future__ import annotations

import os

import pytest


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
