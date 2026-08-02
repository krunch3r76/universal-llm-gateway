"""Boot-import smoke gate for the mcp-server container entrypoint.

Unit tests import modules individually, which walks a different import order
than ``CMD ["python", "server.py"]``. A cycle that only closes on the
container's order therefore passes every offline test and crash-loops the
container on restart (2026-08-02: ``file_editor`` -> ``filesystem/__init__``
-> ``_ops_text`` -> ``file_editor``, 13 green tests, container down).

The subprocess is the point: importing ``server`` inside the pytest process
would reuse modules pytest already imported and mask exactly that class of
cycle.
"""

from __future__ import annotations  # noqa: I001

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline

_MCP_SERVER_ROOT = Path(__file__).resolve().parents[1]
_IMPORT_TIMEOUT_S = 120


def _import_in_container_order(module: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=_MCP_SERVER_ROOT,
        capture_output=True,
        text=True,
        timeout=_IMPORT_TIMEOUT_S,
        check=False,
    )


def _failure_detail(proc: subprocess.CompletedProcess[str]) -> str:
    return f"exit={proc.returncode}\n--- stderr ---\n{proc.stderr[-4000:]}"


def test_server_entrypoint_imports() -> None:
    """``import server`` from the container's cwd must succeed."""
    proc = _import_in_container_order("server")
    assert proc.returncode == 0, _failure_detail(proc)


@pytest.mark.parametrize(
    "module",
    [
        "tools.file_editor",
        "tools.filesystem",
        "tools.filesystem._files_dispatcher",
    ],
)
def test_filesystem_tool_modules_import_standalone(module: str) -> None:
    """Each cycle-prone filesystem module must import as the first import.

    ``import server`` only proves the graph resolves when server's own import
    order primes it. Importing each module cold catches a cycle that server's
    ordering happens to paper over.
    """
    proc = _import_in_container_order(module)
    assert proc.returncode == 0, _failure_detail(proc)
