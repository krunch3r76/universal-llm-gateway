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

_recorded: dict = {}

async def _recording_relay(*, endpoint: str, body: dict, record_prefix: str, **_kwargs):
  _recorded["endpoint"] = endpoint
  _recorded["body"] = body
  _recorded["record_prefix"] = record_prefix
  return {"execution_id": "mcp-pythonpath-probe", "status_code": 200}

def _admit_hop_seat(**_kwargs):
  return None

import tools.frontier as frontier_mod
import tools.agent_bus.hop as hop_mod

frontier_mod._relay = _recording_relay
hop_mod._resolve_hop_seat_request_refusal = _admit_hop_seat

from tools.agent_bus import AGENT_BUS_OPS

hop = AGENT_BUS_OPS["hop"]
if hop is None:
    raise SystemExit("hop op missing from AGENT_BUS_OPS")

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
if "body" not in _recorded:
    raise SystemExit("hop path did not reach frontier._relay")
body = _recorded["body"]
if body.get("caller_agent") != "cursor-sdk-lane":
    raise SystemExit(
        f"caller_agent not wired on generate payload: {body.get('caller_agent')!r}"
    )
leaked = sorted(
    k for k in sys.modules if k == "systems" or k.startswith("systems.")
)
if leaked:
    raise SystemExit(f"systems import leak: {leaked}")
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