"""LIBS-only import surface for waiter admit parsing."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_dispatcher_ingest_imports_with_libs_only_pythonpath() -> None:
    """agent_bus uvicorn uses ``libs`` on PYTHONPATH without Stargate services."""
    repo_libs = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(repo_libs)}
    tid = "550e8400-e29b-41d4-a716-446655440000"
    script = f"""
import bus_watch.endpoint_wake_dispatcher as mod
mod._reset_waiter_stamp_cache()
mod._ingest_waiter_admit_turn(
    {{
        "thread": "libs-only",
        "subject": "synthesizer generate — req",
        "body": "waiter: {tid}\\nbody",
        "turn_number": 1,
        "id": 1,
    }}
)
assert "libs-only" in mod._WAITER_THREAD_CACHE
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
