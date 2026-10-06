"""Subprocess tests for scripts/mcp_bridge_steer_hook.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "scripts" / "mcp_bridge_steer_hook.py"
sys.path.insert(0, str(REPO_ROOT))

from scripts.mcp_bridge_steer_inject import (  # noqa: E402
    NATIVE_HOOK_KILL_SENTINEL_NAME,
    append_spool_entry,
    spool_path,
)


def _run_hook(payload: dict, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def _env(tmp_path: Path, dispatch_id: str) -> dict[str, str]:
    env = os.environ.copy()
    env["DATA_DIR"] = str(tmp_path / "gateway")
    env["ULG_STEER_SPOOL_DIR"] = str(tmp_path / "spool")
    env["CURSOR_SDK_DISPATCH_ID"] = dispatch_id
    env.pop("CURSOR_SDK_DISPATCH_LEDGER", None)
    return env


def _deposit(spool: Path, dispatch_id: str) -> None:
    append_spool_entry(
        dispatch_id,
        authority_turn_id="4",
        directive="hold the merge",
        ttl_s=300,
        spool_dir=spool,
        entry_id="e-hook",
    )


def test_hook_subprocess_delivers_native_tool(tmp_path: Path) -> None:
    spool = tmp_path / "spool"
    _deposit(spool, "disp-hook")
    proc = _run_hook({"tool_name": "Shell"}, _env(tmp_path, "disp-hook"))
    assert proc.returncode == 0, proc.stderr
    body = json.loads(proc.stdout)
    assert body["additional_context"].startswith("ULG_STEER:")
    data = json.loads(spool_path(spool, "disp-hook").read_text(encoding="utf-8"))
    assert data["pending"] == []
    assert data["delivered"][0]["delivered_via"] == "native_hook"


def test_hook_subprocess_skips_mcp_tool(tmp_path: Path) -> None:
    spool = tmp_path / "spool"
    _deposit(spool, "disp-mcp")
    proc = _run_hook({"tool_name": "MCP:foo"}, _env(tmp_path, "disp-mcp"))
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {}
    data = json.loads(spool_path(spool, "disp-mcp").read_text(encoding="utf-8"))
    assert [row["entry_id"] for row in data["pending"]] == ["e-hook"]


def test_hook_subprocess_sentinel_leaves_row_pending(tmp_path: Path) -> None:
    spool = tmp_path / "spool"
    _deposit(spool, "disp-kill")
    env = _env(tmp_path, "disp-kill")
    sentinel = Path(env["DATA_DIR"]) / NATIVE_HOOK_KILL_SENTINEL_NAME
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("off\n", encoding="utf-8")
    proc = _run_hook({"tool_name": "Shell"}, env)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {}
    data = json.loads(spool_path(spool, "disp-kill").read_text(encoding="utf-8"))
    assert [row["entry_id"] for row in data["pending"]] == ["e-hook"]
    assert data.get("delivered") in (None, [])
