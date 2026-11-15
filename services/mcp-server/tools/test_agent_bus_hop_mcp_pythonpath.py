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
from pathlib import Path

import socket

_real_connect = socket.socket.connect


def _forbidden_connect(self, address):
    raise OSError(f"socket.connect forbidden in offline probe: {address!r}")


socket.socket.connect = _forbidden_connect

probe_tmp = Path(os.environ["ULG_PROBE_TMP"])
os.environ.setdefault("DATA_DIR", str(probe_tmp / "data"))
os.environ.setdefault(
    "CURSOR_SDK_DISPATCH_LEDGER", str(probe_tmp / "cursor_sdk_dispatch_ledger")
)
Path(os.environ["DATA_DIR"]).mkdir(parents=True, exist_ok=True)

repo = os.environ["ULG_REPO"]
mcp = os.path.join(repo, "services", "mcp-server")
libs = os.path.join(repo, "libs")
sys.path = [
    p for p in sys.path if "universal-stargate" not in (p or "")
]
sys.path.insert(0, mcp)
sys.path.insert(0, libs)

_hits: dict[str, int] = {}


def _mark(name: str) -> None:
    _hits[name] = _hits.get(name, 0) + 1


_recorded: dict = {}


async def _recording_relay(*, endpoint: str, body: dict, record_prefix: str, **_kwargs):
    _recorded["endpoint"] = endpoint
    _recorded["body"] = body
    _recorded["record_prefix"] = record_prefix
    return {"execution_id": "mcp-pythonpath-probe", "status_code": 200}


def _admit_hop_seat(**_kwargs):
    return None


def _fake_fetch_thread_detail(thread_id: str):
    _mark("fetch_thread_detail")
    return {"summary": {"thread_id": thread_id}}


def _fake_assess_standing_handoff(thread_id: str, **kwargs):
    _mark("assess_standing_handoff")
    from hop_handoff import StandingHandoffFreshness

    return StandingHandoffFreshness(
        status="current",
        uri=f"cortex://notes/system/threads/{thread_id}-standing-handoff.md",
        mtime_epoch=1.0,
        age_s=1.0,
        budget="ok",
        identity="unknown",
    )


def _fake_record(event: str, **kwargs):
    _mark("mcp_events_record")


def _fake_get_thread(thread_id: str):
    _mark("get_thread")
    return {"tags": [], "thread_id": thread_id}


def _fake_load_prior_turns(thread_id: str):
    _mark("load_prior_turns")
    return []


def _fake_standing_handoff_path(thread_id: str):
    _mark("standing_handoff_path")
    return probe_tmp / f"{thread_id}-standing-handoff.md"


import agent_bus_store.db.threads as threads_mod
import hop_handoff.standing_handoff as standing_mod
import tools.agent_bus.hop as hop_mod
import tools.agent_bus.pickup_gate as pickup_mod
import tools.frontier as frontier_mod

frontier_mod._relay = _recording_relay
hop_mod._resolve_hop_seat_request_refusal = _admit_hop_seat
hop_mod._fetch_thread_detail_for_responsibility = _fake_fetch_thread_detail
hop_mod.assess_standing_handoff = _fake_assess_standing_handoff
hop_mod.record = _fake_record
threads_mod.get_thread = _fake_get_thread
pickup_mod.load_prior_turns = _fake_load_prior_turns
standing_mod.standing_handoff_path = _fake_standing_handoff_path

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

_required_hits = (
    "fetch_thread_detail",
    "assess_standing_handoff",
    "get_thread",
    "load_prior_turns",
    "standing_handoff_path",
    "mcp_events_record",
)
missing = [name for name in _required_hits if _hits.get(name, 0) < 1]
if missing:
    raise SystemExit(f"offline stubs not exercised: {missing}; hits={_hits}")
print("ok")
"""


@pytest.mark.offline
def test_agent_bus_hop_op_resolves_caller_on_mcp_pythonpath(tmp_path: Path) -> None:
    """``AGENT_BUS_OPS['hop']`` runs without Stargate on PYTHONPATH."""
    probe_tmp = tmp_path / "probe"
    probe_tmp.mkdir()
    data_dir = probe_tmp / "data"
    data_dir.mkdir()
    env = {
        **os.environ,
        "ULG_REPO": str(_REPO),
        "ULG_PROBE_TMP": str(probe_tmp),
        "DATA_DIR": str(data_dir),
        "CURSOR_SDK_DISPATCH_LEDGER": str(probe_tmp / "cursor_sdk_dispatch_ledger"),
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