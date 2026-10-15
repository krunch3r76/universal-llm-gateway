"""Pytest plugins for every test under services/mcp-server."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_MCP_SERVER_ROOT = str(Path(__file__).resolve().parent)
if _MCP_SERVER_ROOT not in sys.path:
    sys.path.insert(0, _MCP_SERVER_ROOT)

from live_cortex_write_guard import under_live_cortex, write_mode  # noqa: E402


@pytest.fixture(autouse=True)
def forbid_live_cortex_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail a test that opens or replaces a path under a live cortex root.

    Covers root-level ``test_*.py``, ``tests/``, and ``tools/``. The predicate
    is the union of live roots, so one env assignment cannot disarm the others.
    """
    real_open = open

    def guarded_open(file, mode="r", *args, **kwargs):
        if write_mode(mode) and under_live_cortex(file):
            raise AssertionError(f"test write under live cortex root: {file}")
        return real_open(file, mode, *args, **kwargs)

    real_replace = os.replace

    def guarded_replace(src, dst, *args, **kwargs):
        if under_live_cortex(dst):
            raise AssertionError(f"test replace under live cortex root: {dst}")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr("builtins.open", guarded_open)
    monkeypatch.setattr(os, "replace", guarded_replace)
