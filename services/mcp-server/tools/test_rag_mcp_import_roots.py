"""tools.rag imports with MCP roots only — no universal-stargate on sys.path."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]


def test_rag_tool_imports_without_stargate() -> None:
    mcp = _REPO / "services" / "mcp-server"
    libs = _REPO / "libs"
    script = """
import sys
mcp, libs = sys.argv[1], sys.argv[2]
kept = [
    p for p in sys.path
    if "universal-stargate" not in p
    and "universal-llm-gateway" not in p
    and "ulg-arc-worktrees" not in p
]
sys.path[:] = [mcp, libs, *kept]
assert not any("universal-stargate" in p for p in sys.path)
import tools.rag
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = ""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(mcp), str(libs)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
