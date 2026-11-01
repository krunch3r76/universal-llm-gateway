"""Repo-root pytest hooks — isolate conflicting package namespaces during collection."""

from __future__ import annotations

import os

# Fail-closed: never page the operator's phone during pytest (collection/fixtures too).
os.environ["PAGER_NOTIFY_ENABLED"] = "0"
# Fail-closed: never publish manage/mcp restart observation events during pytest.
os.environ.setdefault("ULG_EVENTS_DISABLED", "1")

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent
LIBS = REPO_ROOT / "libs"
STARGATE = REPO_ROOT / "services" / "universal-stargate"
GATEWAY = REPO_ROOT / "services" / "_universal-llm-gateway"
MCP_SERVER = REPO_ROOT / "services" / "mcp-server"
STARGATE_TESTS = STARGATE / "tests"

_OBSOLETE_QUEUE_TESTS = frozenset(
    {
        "test_queue_router_integration.py",
        "test_queue_resource_stress.py",
        "test_queue_resource_verification.py",
    }
)


def _path_str(path: Path) -> str:
    return str(path)


def _ensure_path(path: Path, *, position: int = 0) -> None:
    entry = _path_str(path)
    if entry in sys.path:
        sys.path.remove(entry)
    sys.path.insert(position, entry)


def _purge_namespace(prefix: str, required_marker: str) -> None:
    marker = required_marker.replace("\\", "/")
    for key in list(sys.modules):
        if key != prefix and not key.startswith(f"{prefix}."):
            continue
        mod = sys.modules.get(key)
        mod_file = (getattr(mod, "__file__", None) or "").replace("\\", "/")
        if marker not in mod_file:
            del sys.modules[key]


def _configure_for_path(path: str) -> None:
    _ensure_path(LIBS, position=0)
    if "/services/universal-stargate/" in path:
        _purge_namespace("src", "services/universal-stargate")
        _purge_namespace("systems", "services/universal-stargate")
        _purge_namespace("tests", "services/universal-stargate/tests")
        _ensure_path(REPO_ROOT, position=0)
        _ensure_path(STARGATE, position=0)
        if "/services/universal-stargate/tests/" in path:
            _ensure_path(STARGATE_TESTS, position=0)
        return
    if "/services/_universal-llm-gateway/" in path:
        _purge_namespace("src", "services/_universal-llm-gateway")
        _purge_namespace("core", "services/_universal-llm-gateway")
        _purge_namespace("tools", "services/_universal-llm-gateway")
        _ensure_path(GATEWAY, position=0)
        return
    if "/services/mcp-server/" in path:
        _purge_namespace("tools", "services/mcp-server")
        _purge_namespace("mcp_events", "services/mcp-server")
        _ensure_path(MCP_SERVER, position=0)
        return
    if path.startswith(_path_str(REPO_ROOT / "tests" / "unit" / "mcp_server")):
        _purge_namespace("tools", "services/mcp-server")
        _purge_namespace("mcp_events", "services/mcp-server")
        _ensure_path(MCP_SERVER, position=0)
        return
    if path.startswith(_path_str(REPO_ROOT / "tests")):
        _purge_namespace("src", "services/_universal-llm-gateway")
        _ensure_path(GATEWAY, position=0)
    if "/scripts/" in path or path.startswith(_path_str(REPO_ROOT / "scripts")):
        _ensure_path(REPO_ROOT, position=0)


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    if collection_path.name in _OBSOLETE_QUEUE_TESTS:
        return True
    return None


@pytest.hookimpl(tryfirst=True)
def pytest_collectstart(collector: pytest.Collector) -> None:
    node_path = getattr(collector, "path", None)
    if node_path is None:
        return
    _configure_for_path(str(node_path).replace("\\", "/"))


@pytest.fixture(scope="session", autouse=True)
def _isolate_dispatch_ledger(tmp_path_factory: pytest.TempPathFactory):
    """Pin ``DATA_DIR`` and dispatch ledger for all pytest (libs + services).

    Without this, ``_ledger_path()`` falls back to ``~/.gateway`` and suites
    write live ledgers. Session scope because ``CursorDispatchLedger`` and
    ``SeatWriteLedger`` cache the resolved path on the singleton at first touch.
    """
    import os

    from services.git_integration_worker.cursor_dispatch_ledger import (
        CURSOR_SDK_DISPATCH_LEDGER_ENV,
        CursorDispatchLedger,
    )
    from services.git_integration_worker.seat_write_ledger import SeatWriteLedger

    data_dir = tmp_path_factory.mktemp("pytest-data-dir")
    isolated_ledger = data_dir / "cursor-sdk-dispatch.db"
    prior_data_dir = os.environ.get("DATA_DIR")
    prior_dispatch_ledger = os.environ.get(CURSOR_SDK_DISPATCH_LEDGER_ENV)
    os.environ["DATA_DIR"] = str(data_dir)
    os.environ[CURSOR_SDK_DISPATCH_LEDGER_ENV] = str(isolated_ledger)
    SeatWriteLedger.reset_instance()
    CursorDispatchLedger._instance = None
    try:
        yield data_dir
    finally:
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
def _pin_isolated_dispatch_ledger_per_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Override inherited ``CURSOR_SDK_DISPATCH_LEDGER`` with a per-test tmp ledger.

    A dispatch-shell export of the live path still wins unless each test
    monkeypatches it away. Function scope keeps holder/ledger rows from leaking.
    Singletons are reset before and after each test so no instance cached on a
    test's tmp path survives into the next test or past the last one.
    """
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CURSOR_SDK_DISPATCH_LEDGER_ENV,
        CursorDispatchLedger,
    )
    from services.git_integration_worker.seat_write_ledger import SeatWriteLedger

    root = tmp_path / "giw-ledger-root"
    root.mkdir()
    monkeypatch.setenv("DATA_DIR", str(root))
    monkeypatch.setenv(
        CURSOR_SDK_DISPATCH_LEDGER_ENV, str(root / "cursor-sdk-dispatch.db")
    )
    SeatWriteLedger.reset_instance()
    CursorDispatchLedger._instance = None
    try:
        yield root
    finally:
        SeatWriteLedger.reset_instance()
        CursorDispatchLedger._instance = None


@pytest.fixture(autouse=True)
def _pager_notify_isolated_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep pager dedupe/cursor state out of the operator's real home during tests."""
    monkeypatch.setenv("PAGER_NOTIFY_STATE_DIR", str(tmp_path / "pager-notify"))
