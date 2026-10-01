"""Tmux pane identity checks keyed to the live manage PID.

Window/pane names are not authority. Before quit, the runner must prove the
tmux target hosts the process that answered ``whoami`` (same PID, or manage as
a descendant of ``#{pane_pid}`` when a shell still owns the pane).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .checks import RefuseFinding

RunCmd = Callable[[list[str]], subprocess.CompletedProcess[str]]
TreeContainsFn = Callable[[int, int], bool]


def ppid_of(pid: int) -> int | None:
    """Return the parent pid from ``/proc/<pid>/stat``, or None if unreadable."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    lparen = raw.find("(")
    rparen = raw.rfind(")")
    if lparen < 0 or rparen < 0 or rparen + 2 >= len(raw):
        return None
    parts = raw[rparen + 2 :].split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def pid_descends_from(pid: int, ancestor: int) -> bool:
    """True when ``pid`` is ``ancestor`` or a descendant in the process tree."""
    if pid == ancestor:
        return True
    seen: set[int] = set()
    cur = pid
    while cur > 0 and cur not in seen:
        if cur == ancestor:
            return True
        seen.add(cur)
        parent = ppid_of(cur)
        if parent is None or parent == cur:
            return False
        cur = parent
    return False


def read_tmux_pane_pid(
    tmux_target: str,
    *,
    run_cmd: RunCmd,
) -> tuple[int | None, dict[str, Any]]:
    """Resolve ``#{pane_pid}`` for ``tmux_target``; None when unobservable."""
    proc = run_cmd(["tmux", "display-message", "-p", "-t", tmux_target, "#{pane_pid}"])
    detail = {
        "tmux_target": tmux_target,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }
    if proc.returncode != 0:
        return None, detail
    text = (proc.stdout or "").strip()
    if not text.isdigit():
        return None, detail
    return int(text), detail


def find_tmux_target_hosting_manage(
    manage_pid: int,
    *,
    run_cmd: RunCmd,
    tree_contains_fn: TreeContainsFn | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Scan tmux panes for one whose #{pane_pid} tree contains manage."""
    contains = tree_contains_fn or pid_descends_from
    proc = run_cmd(
        [
            "tmux",
            "list-panes",
            "-a",
            "-F",
            "#{session_name}:#{window_index}.#{pane_index}\t#{pane_pid}",
        ]
    )
    detail: dict[str, Any] = {
        "returncode": proc.returncode,
        "stdout_lines": (proc.stdout or "").count("\n"),
    }
    if proc.returncode != 0:
        return None, detail
    for line in (proc.stdout or "").splitlines():
        if "\t" not in line:
            continue
        target, pane_pid_s = line.split("\t", 1)
        if not pane_pid_s.strip().isdigit():
            continue
        pane_pid = int(pane_pid_s.strip())
        if contains(manage_pid, pane_pid):
            return target.strip(), detail | {"matched_pane_pid": pane_pid}
    return None, detail


def observe_tmux_pane_hosts_manage(
    *,
    tmux_target: str,
    manage_pid: int,
    run_cmd: RunCmd,
    tree_contains_fn: TreeContainsFn | None = None,
) -> RefuseFinding | None:
    """Refuse when the tmux target does not host the live manage PID.

    Match is against process identity: pane PID equal to manage, or manage in
    the pane PID's descendant tree — never window/pane name alone.
    """
    contains = tree_contains_fn or pid_descends_from
    pane_pid, detail = read_tmux_pane_pid(tmux_target, run_cmd=run_cmd)
    if pane_pid is None:
        return RefuseFinding(
            reason="tmux_pane_unobservable",
            offenders=[detail | {"manage_pid": manage_pid}],
        )
    if not contains(manage_pid, pane_pid):
        return RefuseFinding(
            reason="tmux_pane_pid_mismatch",
            offenders=[
                {
                    "tmux_target": tmux_target,
                    "pane_pid": pane_pid,
                    "manage_pid": manage_pid,
                    "match_rule": "manage_pid == pane_pid or descends_from(pane_pid)",
                }
            ],
        )
    return None


def spawn_successor_pane(
    *,
    tmux_target: str,
    repo_root: Path,
    python_bin: str,
    record_path: Path,
    run_cmd: RunCmd,
) -> str | None:
    """Split a new tmux pane running armed manage; return pane id or None."""
    cmd = (
        f"cd {repo_root} && "
        f"MANAGE_HANDOVER_RECORD={record_path} "
        f"{python_bin} -m scripts.model_manager.ui"
    )
    proc = run_cmd(
        [
            "tmux",
            "split-window",
            "-d",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            tmux_target,
            cmd,
        ]
    )
    if proc.returncode != 0:
        return None
    pane_id = (proc.stdout or "").strip()
    return pane_id or None


def kill_successor_pane(
    pane_id: str | None,
    *,
    run_cmd: RunCmd,
) -> None:
    """Tear down a successor pane when arm/quit is refused or aborted."""
    if not pane_id:
        return
    run_cmd(["tmux", "kill-pane", "-t", pane_id])
