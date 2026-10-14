"""Successor pane shell command must mirror ./manage bootstrap (friction:39210)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.model_manager.guarded_manage_reexec.pane import (
    build_successor_pane_shell_command,
    spawn_successor_pane,
)


def test_successor_shell_command_launches_via_manage_entry(tmp_path: Path) -> None:
    """Breaks when successor skips manage's .env.local / PROJECT_ROOT bootstrap."""
    repo = tmp_path / "hub"
    repo.mkdir()
    (repo / "manage").write_text("#!/usr/bin/env bash\n")
    record = tmp_path / "armed.json"

    cmd = build_successor_pane_shell_command(repo_root=repo, record_path=record)

    assert f"MANAGE_HANDOVER_RECORD={record}" in cmd.replace("'", "")
    assert str(repo / "manage") in cmd
    assert "-m scripts.model_manager.ui" not in cmd
    assert "exec " in cmd


def test_successor_shell_command_rejects_pre_fix_python_module_shape(
    tmp_path: Path,
) -> None:
    """Fails on 9e1e2db1 pane command: bare ``python -m scripts.model_manager.ui``."""
    repo = tmp_path / "hub"
    repo.mkdir()
    (repo / "manage").write_text("#!/usr/bin/env bash\n")
    record = tmp_path / "armed.json"
    python_bin = "/home/op/.venvs/universal/bin/python"

    cmd = build_successor_pane_shell_command(repo_root=repo, record_path=record)
    legacy_shape = (
        f"cd {repo} && "
        f"MANAGE_HANDOVER_RECORD={record} "
        f"{python_bin} -m scripts.model_manager.ui"
    )

    assert cmd != legacy_shape
    assert f"{python_bin} -m scripts.model_manager.ui" not in cmd


def test_spawn_successor_pane_passes_manage_bootstrap_to_tmux(tmp_path: Path) -> None:
    """Breaks when tmux split-window shell omits ./manage entry."""
    repo = tmp_path / "hub"
    repo.mkdir()
    (repo / "manage").write_text("#!/usr/bin/env bash\n")
    record = tmp_path / "armed.json"
    captured: list[list[str]] = []

    def run_cmd(args: list[str]) -> subprocess.CompletedProcess[str]:
        captured.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="%99")

    pane_id = spawn_successor_pane(
        tmux_target="0:0",
        repo_root=repo,
        python_bin="/ignored/python",
        record_path=record,
        run_cmd=run_cmd,
    )

    assert pane_id == "%99"
    assert len(captured) == 1
    shell_cmd = captured[0][-1]
    assert str(repo / "manage") in shell_cmd
    assert "-m scripts.model_manager.ui" not in shell_cmd
    assert captured[0][:3] == ["tmux", "split-window", "-d"]
