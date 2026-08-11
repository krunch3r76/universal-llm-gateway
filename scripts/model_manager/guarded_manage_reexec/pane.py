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
    proc = run_cmd(
        ["tmux", "display-message", "-p", "-t", tmux_target, "#{pane_pid}"]
    )
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
