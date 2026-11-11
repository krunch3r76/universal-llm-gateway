"""Regression: cursor_bridge ↔ agent_bus_store import graph must not cycle cdp_ask."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_LIBS = Path(__file__).resolve().parents[1]


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    libs = str(_LIBS)
    prior = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = libs if not prior else libs + os.pathsep + prior
    return env


@pytest.mark.parametrize(
    "stmt",
    [
        "from cdp_ask import create_app; create_app()",
        "import cdp_ask; import cursor_bridge.chat_surface; import cursor_bridge.chat_target; "
        "import cursor_bridge.wake_delivery; import agent_bus_store",
        "import agent_bus_store; import cursor_bridge.chat_target",
        "import cursor_bridge.electron_chat_relay",
    ],
)
def test_fresh_subprocess_import_no_cycle(stmt: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", stmt],
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"{stmt!r} failed:\n{proc.stderr}"
