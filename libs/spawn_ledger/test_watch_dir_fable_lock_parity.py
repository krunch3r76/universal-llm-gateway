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


def _watch_dirs_for_env(
    extra_env: dict[str, str] | None = None,
    *,
    cwd: Path | None = None,
    drop_env: frozenset[str] = frozenset(),
) -> tuple[str, str, bool]:
    env = {k: v for k, v in os.environ.items() if k not in drop_env}
    env["PYTHONPATH"] = str(REPO / "libs")
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=cwd or REPO,
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
    "layout_id,env_builder,cwd_builder,drop_env",
    [
        (
            "full_checkout",
            lambda _tmp: {"PROJECT_ROOT": str(REPO)},
            lambda _tmp: None,
            frozenset(),
        ),
        (
            "nested_mount",
            lambda tmp: _nested_mount_env(tmp),
            lambda _tmp: None,
            frozenset(),
        ),
        (
            "project_root_bus_watch_only",
            lambda tmp: _bus_watch_only_mount_env(tmp),
            lambda _tmp: None,
            frozenset(),
        ),
        (
            "project_root_empty_string_resolves_dot",
            lambda _tmp: {"PROJECT_ROOT": ""},
            lambda _tmp: None,
            frozenset(),
        ),
        (
            "project_root_absent_libs_anchor_fallback",
            lambda _tmp: {},
            lambda tmp: tmp,
            frozenset({"PROJECT_ROOT"}),
        ),
        (
            "project_root_invalid_falls_back_to_libs_anchor",
            lambda tmp: {"PROJECT_ROOT": str(tmp / "empty_mount")},
            lambda tmp: tmp,
            frozenset(),
        ),
    ],
    ids=[
        "full_checkout",
        "nested_mount",
        "PROJECT_ROOT_bus_watch_only",
        "PROJECT_ROOT_empty_string",
        "PROJECT_ROOT_absent_fallback",
        "PROJECT_ROOT_invalid_no_bus_watch",
    ],
)
def test_spawn_ledger_watch_dir_matches_fable_lock(
    layout_id: str,
    env_builder,
    cwd_builder,
    drop_env: frozenset[str],
    tmp_path: Path,
) -> None:
    extra = env_builder(tmp_path)
    cwd = cwd_builder(tmp_path)
    sl, bw, equal = _watch_dirs_for_env(extra, cwd=cwd, drop_env=drop_env)
    assert equal, f"{layout_id}: spawn_ledger={sl!r} fable_lock={bw!r}"
    assert sl == bw


def _nested_mount_env(tmp_path: Path) -> dict[str, str]:
    nested = tmp_path / "universal-llm-gateway"
    (nested / "libs" / "bus_watch").mkdir(parents=True)
    return {"PROJECT_ROOT": str(tmp_path)}


def _bus_watch_only_mount_env(tmp_path: Path) -> dict[str, str]:
    (tmp_path / "libs" / "bus_watch").mkdir(parents=True)
    return {"PROJECT_ROOT": str(tmp_path)}


@pytest.mark.offline
def test_fallback_layouts_byte_identical_watch_dir(tmp_path: Path) -> None:
    """Fallback paths must match with subprocess cwd outside the checkout."""
    empty = tmp_path / "away"
    empty.mkdir()
    sl1, bw1, _ = _watch_dirs_for_env(
        {},
        cwd=empty,
        drop_env=frozenset({"PROJECT_ROOT"}),
    )
    invalid = tmp_path / "no_ulg"
    invalid.mkdir()
    sl2, bw2, _ = _watch_dirs_for_env(
        {"PROJECT_ROOT": str(invalid)},
        cwd=empty,
    )
    assert sl1 == bw1 == sl2 == bw2
