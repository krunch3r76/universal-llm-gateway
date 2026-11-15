"""fs recent_commits handler must import on MCP process PYTHONPATH (no top-level services/)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[4]
_MCP = _REPO / "services" / "mcp-server"
_LIBS = _REPO / "libs"

_MCP_RECENT_COMMITS_PROBE = r"""
import os
import sys

repo = os.environ["ULG_REPO"]
libs = os.path.join(repo, "libs")
mcp = os.path.join(repo, "services", "mcp-server")
sys.path = [p for p in sys.path if p]
sys.path.insert(0, mcp)
sys.path.insert(0, libs)

for entry in sys.path:
    if not entry:
        continue
    base = os.path.basename(os.path.normpath(entry))
    if base == "services":
        raise SystemExit(f"forbidden services/ on PYTHONPATH: {entry!r}")

from git_integrate.events import emit_git_log_read  # noqa: F401
from tools.filesystem._recent_commits import recent_commits_impl  # noqa: F401

if "git_integrate.ops_common" in sys.modules:
    raise SystemExit("git_integrate.ops_common loaded during recent_commits import")
print("ok")
"""


@pytest.mark.offline
def test_recent_commits_imports_on_mcp_pythonpath() -> None:
    """recent_commits handler imports without top-level services/ on PYTHONPATH."""
    env = {
        **os.environ,
        "ULG_REPO": str(_REPO),
        "PYTHONPATH": f"{_LIBS}{os.pathsep}{_MCP}",
    }
    proc = subprocess.run(
        [sys.executable, "-c", _MCP_RECENT_COMMITS_PROBE],
        cwd=_MCP,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, (
        f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    )
    assert proc.stdout.strip().endswith("ok")