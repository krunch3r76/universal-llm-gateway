"""C3: spawn_ledger package import closure must stay GUI/CDP-neutral."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

_PROBE = r"""
import json
import sys

for name in list(sys.modules):
    if name == "spawn_ledger" or name.startswith("spawn_ledger."):
        del sys.modules[name]

import spawn_ledger.spawn_endpoint_house_ledger  # noqa: F401

forbidden = (
    "cursor_bridge",
    "interlocutor",
    "bus_watch",
)
hits = sorted(
    name
    for name in sys.modules
    if any(name == p or name.startswith(f"{p}.") for p in forbidden)
    or "display_interaction_lock" in name
    or "fable_lock" in name
)
print(json.dumps({"hits": hits}))
"""


@pytest.mark.offline
def test_spawn_ledger_transitive_imports_avoid_gui_substrate() -> None:
    env = {
        **os.environ,
        "PROJECT_ROOT": str(REPO),
        "PYTHONPATH": str(REPO / "libs"),
    }
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["hits"] == [], payload["hits"]
