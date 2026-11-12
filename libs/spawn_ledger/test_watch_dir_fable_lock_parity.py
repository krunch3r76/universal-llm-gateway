"""C3: spawn_ledger WATCH_DIR must match bus_watch.fable_lock in every mount layout."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

_PROBE = r"""
import importlib
import json
import sys

def purge(prefixes):
    for name in list(sys.modules):
        if any(name == p or name.startswith(f"{p}.") for p in prefixes):
            del sys.modules[name]

purge(("spawn_ledger", "bus_watch.fable_lock", "ulg_paths"))
import spawn_ledger.watch_dir as sl_wd_mod
import bus_watch.fable_lock as fl_mod
importlib.reload(sl_wd_mod)
importlib.reload(fl_mod)
sl = str(sl_wd_mod.WATCH_DIR)
bw = str(fl_mod.WATCH_DIR)
print(json.dumps({"spawn_ledger": sl, "fable_lock": bw, "equal": sl == bw}))
"""


def _watch_dirs_for_env(extra_env: dict[str, str]) -> tuple[str, str, bool]:
    env = {**os.environ, "PYTHONPATH": str(REPO / "libs"), **extra_env}
    env.pop("PYTHONDONTWRITEBYTECODE", None)
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
    return payload["spawn_ledger"], payload["fable_lock"], payload["equal"]


@pytest.mark.offline
@pytest.mark.parametrize(
    "layout_id,env_builder",
    [
        ("full_checkout", lambda _tmp: {"PROJECT_ROOT": str(REPO)}),
        (
            "nested_mount",
            lambda tmp: _nested_mount_env(tmp),
        ),
        (
            "project_root_bus_watch_only",
            lambda tmp: _bus_watch_only_mount_env(tmp),
        ),
        ("project_root_unset", lambda _tmp: {"PROJECT_ROOT": ""}),
    ],
    ids=[
        "full_checkout",
        "nested_mount",
        "PROJECT_ROOT_bus_watch_only",
        "PROJECT_ROOT_unset",
    ],
)
def test_spawn_ledger_watch_dir_matches_fable_lock(
    layout_id: str,
    env_builder,
    tmp_path: Path,
) -> None:
    sl, bw, equal = _watch_dirs_for_env(env_builder(tmp_path))
    assert equal, f"{layout_id}: spawn_ledger={sl!r} fable_lock={bw!r}"


def _nested_mount_env(tmp_path: Path) -> dict[str, str]:
    nested = tmp_path / "universal-llm-gateway"
    (nested / "libs" / "bus_watch").mkdir(parents=True)
    return {"PROJECT_ROOT": str(tmp_path)}


def _bus_watch_only_mount_env(tmp_path: Path) -> dict[str, str]:
    (tmp_path / "libs" / "bus_watch").mkdir(parents=True)
    return {"PROJECT_ROOT": str(tmp_path)}
