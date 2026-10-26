"""MCP cold-import chain must not require psutil at module import time."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from deploy_identity.mcp_restart_gate import COLD_IMPORTS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MCP_ROOT = _REPO_ROOT / "services" / "mcp-server"
_LIBS = _REPO_ROOT / "libs"


@pytest.mark.offline
def test_mcp_cold_imports_without_psutil_module() -> None:
    """Startup cold imports succeed when psutil is blocked in sys.modules."""
    mods = ", ".join(repr(name) for name in COLD_IMPORTS)
    libs = str(_LIBS)
    mcp = str(_MCP_ROOT)
    body = textwrap.dedent(
        f"""
        import sys
        sys.path[:0] = [{libs!r}, {mcp!r}]
        sys.modules["psutil"] = None
        for mod in ({mods},):
            __import__(mod)
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", body],
        cwd=_MCP_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
