"""Pytest plugins for tools/ test modules."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_MCP_SERVER_ROOT = Path(__file__).resolve().parents[1]
_root = str(_MCP_SERVER_ROOT)
if _root in sys.path:
    sys.path.remove(_root)
sys.path.insert(0, _root)

_tools = sys.modules.get("tools")
if _tools is not None:
    _tools_file = (getattr(_tools, "__file__", None) or "").replace("\\", "/")
    if "mcp-server" not in _tools_file:
        for key in list(sys.modules):
            if key == "tools" or key.startswith("tools."):
                del sys.modules[key]

_mcp_events = sys.modules.get("mcp_events")
if _mcp_events is not None:
    _events_file = (getattr(_mcp_events, "__file__", None) or "").replace("\\", "/")
    if "mcp-server" not in _events_file:
        del sys.modules["mcp_events"]


def _live_cortex_files_root() -> Path:
    """Root cortex_files_root() would use before a test redirects it."""
    raw = os.environ.get("CORTEX_FILES_ROOT")
    if raw:
        return Path(raw).resolve()
    container = Path("/data/files")
    if container.is_dir():
        return container.resolve()
    return (Path.home() / "mcp-data" / "files").resolve()


_LIVE_CORTEX_FILES_ROOT = _live_cortex_files_root()


def _under_live_cortex(path: object) -> bool:
    if isinstance(path, int) or not isinstance(path, (str, Path)):
        return False
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    try:
        resolved.relative_to(_LIVE_CORTEX_FILES_ROOT)
    except ValueError:
        return False
    return True


def _write_mode(mode: object) -> bool:
    text = mode if isinstance(mode, str) else ""
    return any(flag in text for flag in ("w", "a", "x", "+"))


@pytest.fixture(autouse=True)
def forbid_live_cortex_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail a test that opens or replaces a path under the live cortex root."""
    real_open = open

    def guarded_open(file, mode="r", *args, **kwargs):
        if _write_mode(mode) and _under_live_cortex(file):
            raise AssertionError(f"test write under live cortex root: {file}")
        return real_open(file, mode, *args, **kwargs)

    real_replace = os.replace

    def guarded_replace(src, dst, *args, **kwargs):
        if _under_live_cortex(dst):
            raise AssertionError(f"test replace under live cortex root: {dst}")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr("builtins.open", guarded_open)
    monkeypatch.setattr(os, "replace", guarded_replace)
