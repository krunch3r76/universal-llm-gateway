"""agent_bus ``hop`` must resolve on MCP process PYTHONPATH (no ``systems``)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_MCP = _REPO / "services" / "mcp-server"
_LIBS = _REPO / "libs"

_MCP_HOP_PROBE = r"""
import asyncio
import os
import sys

repo = os.environ["ULG_REPO"]
mcp = os.path.join(repo, "services", "mcp-server")
libs = os.path.join(repo, "libs")
sys.path = [
    p for p in sys.path if "universal-stargate" not in (p or "")
]
sys.path.insert(0, mcp)
sys.path.insert(0, libs)

from tools.agent_bus import AGENT_BUS_OPS

hop = AGENT_BUS_OPS["hop"]
if hop is None:
    raise SystemExit("hop op missing from AGENT_BUS_OPS")

# Early caller resolution on the hop path (op=hop entry); must not import systems.*
result = asyncio.run(
    hop(
        thread="77",
        reason="mcp-pythonpath-probe",
        from_agent="web-anthropic",
        caller_agent="cursor-sdk-lane",
    )
)
if not isinstance(result, dict):
    raise SystemExit(f"unexpected hop result type: {type(result)!r}")
if result.get("reason") == "hop_caller_agent_required":
    raise SystemExit("caller resolution failed unexpectedly")
print("ok")
"""


@pytest.mark.offline
def test_agent_bus_hop_op_resolves_caller_on_mcp_pythonpath() -> None:
    """``AGENT_BUS_OPS['hop']`` runs without Stargate on PYTHONPATH."""
    env = {
        **os.environ,
        "ULG_REPO": str(_REPO),
        "PYTHONPATH": f"{_LIBS}{os.pathsep}{_MCP}",
    }
    proc = subprocess.run(
        [sys.executable, "-c", _MCP_HOP_PROBE],
        cwd=_MCP,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, (
        f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    )
    assert proc.stdout.strip().endswith("ok")


@pytest.mark.offline
def test_stargate_cdp_admit_identity_reexports_caller_gate() -> None:
    """Stargate admit module keeps ``is_valid_cdp_caller_agent`` on its import path."""
    from systems.frontier_consult.cdp_admit_identity import is_valid_cdp_caller_agent

    assert is_valid_cdp_caller_agent("cursor-sdk-lane") is True
    assert is_valid_cdp_caller_agent("dispatch") is False