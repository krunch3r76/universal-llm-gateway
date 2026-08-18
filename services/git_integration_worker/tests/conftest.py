"""Pytest configuration for git-integration-worker tests."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(scope="session", autouse=True)
def _isolate_dispatch_ledger(tmp_path_factory: pytest.TempPathFactory):
    """Point ``DATA_DIR`` at a tmp dir for the whole GIW session.

    Without this, ``_ledger_path()`` falls back to ``~/.gateway`` and the suite
    writes live ledgers. Session scope because ``CursorDispatchLedger`` and
    ``SeatWriteLedger`` cache the resolved path on the singleton at first
    touch. Reset both after setting ``DATA_DIR`` so a collection-time instance
    cannot keep the live path and defeat the pytest refuse belt.
    """
    import os

    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )
    from services.git_integration_worker.seat_write_ledger import SeatWriteLedger

    data_dir = tmp_path_factory.mktemp("giw-data-dir")
    prior = os.environ.get("DATA_DIR")
    os.environ["DATA_DIR"] = str(data_dir)
    SeatWriteLedger.reset_instance()
    CursorDispatchLedger._instance = None
    yield data_dir
    if prior is None:
        os.environ.pop("DATA_DIR", None)
    else:
        os.environ["DATA_DIR"] = prior
    SeatWriteLedger.reset_instance()
    CursorDispatchLedger._instance = None


@pytest.fixture(autouse=True)
def _hop_orientation_bus_stub(monkeypatch: pytest.MonkeyPatch):
    """Keep hop tests off the network — orientation fetches lane turns for real."""
    from services.git_integration_worker.cursor_auto import hop_orientation

    monkeypatch.setattr(hop_orientation, "fetch_thread_turns", AsyncMock(return_value=[]))


@pytest.fixture(autouse=True)
def _cursor_auto_admit_bus_stubs(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Keep cursor-auto admit tests hermetic after thread-status gate landed."""
    if "cursor_auto" not in request.node.nodeid:
        yield
        return
    import services.git_integration_worker.cursor_sdk_events as cursor_sdk_events
    from services.git_integration_worker.cursor_auto import admit_gates

    monkeypatch.setattr(
        admit_gates,
        "fetch_thread_status",
        AsyncMock(return_value="active"),
    )
    monkeypatch.setattr(
        admit_gates,
        "fetch_thread_turns",
        AsyncMock(return_value=[]),
    )
    prior_publisher = cursor_sdk_events._uds_publisher
    cursor_sdk_events._uds_publisher = None
    yield
    cursor_sdk_events._uds_publisher = prior_publisher
