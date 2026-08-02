"""Repo-root pytest hooks — isolate conflicting package namespaces during collection."""

from __future__ import annotations

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


@pytest.fixture(autouse=True)
def _pager_notify_isolated_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep pager dedupe/cursor state out of the operator's real home during tests."""
    monkeypatch.setenv("PAGER_NOTIFY_STATE_DIR", str(tmp_path / "pager-notify"))
