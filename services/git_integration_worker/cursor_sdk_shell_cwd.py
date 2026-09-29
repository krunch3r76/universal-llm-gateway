"""Shell spawn cwd for cursor-sdk bridge death and the worktree live hold.

Who calls: the bridge stderr forensics path, the lane-B reaper guard, and the
dispatch route as shell tool calls stream. Invariant: the directory a failing
``spawn /bin/bash`` uses is the in-flight shell tool's ``workingDirectory``
when that field is set, otherwise the last bash-snapshot PWD. The node
bridge's own cwd is the lane root and answers a different question.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_BASH_STATE_START = "__CURSOR_BASH_STATE_START__"
_TERMINAL_SHELL_STATUSES = frozenset({"completed", "error"})

_lock = threading.Lock()


@dataclass
class _ShellSpawn:
    """Per-dispatch shell session: snapshot PWD plus the in-flight override."""

    snapshot_pwd: str | None = None
    in_flight: str | None = None


_records: dict[str, _ShellSpawn] = {}


def reset_shell_spawn_state() -> None:
    """Drop every recorded shell cwd. Tests only; a live worker keeps one row per dispatch."""
    with _lock:
        _records.clear()


def parse_bash_snapshot_pwd(text: str | None) -> str | None:
    """Return the PWD line ``dump_bash_state`` writes after its start marker.

    The wrapper restores that text with ``eval -- "$snap"`` from fd 3 before
    the command runs. The line immediately after ``__CURSOR_BASH_STATE_START__``
    is ``$PWD`` at dump time. Returns None when the marker is absent.
    """
    if not text:
        return None
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != _BASH_STATE_START:
            continue
        if index + 1 >= len(lines):
            return None
        pwd = lines[index + 1].strip()
        if not pwd or pwd.startswith("__CURSOR"):
            return None
        return pwd
    return None


def resolve_shell_spawn_cwd(
    *,
    working_directory: str | None = None,
    snapshot_pwd: str | None = None,
) -> str | None:
    """Directory the next ``spawn /bin/bash`` will use, or None when unknown.

    ``working_directory`` is the in-flight shell tool argument and wins.
    ``snapshot_pwd`` is the session PWD the wrapper restores when that
    argument is empty. Neither value is the node process cwd.
    """
    working = (working_directory or "").strip()
    if working:
        return working
    snapshot = (snapshot_pwd or "").strip()
    return snapshot or None


def shell_spawn_cwd(dispatch_id: str | None) -> str | None:
    """Last resolved shell spawn cwd for *dispatch_id*, or None if unrecorded."""
    if not dispatch_id:
        return None
    with _lock:
        record = _records.get(dispatch_id)
        if record is None:
            return None
        return resolve_shell_spawn_cwd(
            working_directory=record.in_flight,
            snapshot_pwd=record.snapshot_pwd,
        )


def note_bash_snapshot(dispatch_id: str, text: str) -> None:
    """Record a bash-snapshot PWD parsed from *text* for *dispatch_id*.

    No-op when *text* has no start marker. Does not clear an in-flight
    ``workingDirectory``; that override still wins until the tool call ends.
    """
    pwd = parse_bash_snapshot_pwd(text)
    if not pwd or not dispatch_id:
        return
    with _lock:
        record = _records.setdefault(dispatch_id, _ShellSpawn())
        record.snapshot_pwd = pwd


def note_shell_tool_call(
    dispatch_id: str,
    *,
    tool_name: str,
    status: str,
    args: Mapping[str, Any] | None,
) -> None:
    """Update the shell spawn cwd from one shell tool-call observation.

    A non-empty ``workingDirectory`` is the in-flight spawn cwd. When the
    call reaches a terminal status, a top-level absolute ``cd`` becomes the
    snapshot PWD the next spawn restores; a subshell ``cd`` does not.
    Non-shell tools are ignored.
    """
    if (tool_name or "").casefold() != "shell" or not dispatch_id:
        return
    mapping = args if isinstance(args, Mapping) else {}
    working = mapping.get("workingDirectory") or mapping.get("working_directory")
    if not isinstance(working, str):
        working = ""
    working = working.strip()
    command = mapping.get("command")
    if not isinstance(command, str):
        command = ""
    with _lock:
        record = _records.setdefault(dispatch_id, _ShellSpawn())
        if (status or "").casefold() in _TERMINAL_SHELL_STATUSES:
            cd_target = _last_top_level_absolute_cd(command)
            if cd_target:
                record.snapshot_pwd = cd_target
            elif working:
                record.snapshot_pwd = working
            record.in_flight = None
            return
        record.in_flight = working or record.snapshot_pwd


def _last_top_level_absolute_cd(command: str) -> str | None:
    """Last absolute ``cd`` target at parenthesis depth zero, else None.

    ``( cd /tmp )`` does not change the snapshot the next spawn restores.
    Relative paths and ``$VAR`` targets are left unresolved.
    """
    depth = 0
    last: str | None = None
    index = 0
    length = len(command)
    while index < length:
        char = command[index]
        if char in "\"'":
            index = _skip_quoted(command, index)
            continue
        if char == "(":
            depth += 1
            index += 1
            continue
        if char == ")":
            depth = max(0, depth - 1)
            index += 1
            continue
        if depth == 0 and _at_cd_token(command, index):
            target, index = _read_cd_target(command, index + 2)
            if target and target.startswith("/"):
                last = target
            continue
        index += 1
    return last


def _skip_quoted(command: str, index: int) -> int:
    quote = command[index]
    index += 1
    length = len(command)
    while index < length and command[index] != quote:
        if command[index] == "\\" and quote == '"':
            index += 2
            continue
        index += 1
    return index + 1


def _at_cd_token(command: str, index: int) -> bool:
    if not command.startswith("cd", index):
        return False
    if index > 0 and (command[index - 1].isalnum() or command[index - 1] == "_"):
        return False
    end = index + 2
    if end < len(command) and (command[end].isalnum() or command[end] == "_"):
        return False
    return True


def _read_cd_target(command: str, index: int) -> tuple[str | None, int]:
    length = len(command)
    while index < length and command[index] in " \t":
        index += 1
    if index >= length or command[index] == "-":
        return None, index + 1 if index < length else index
    if command[index] in "\"'":
        quote = command[index]
        index += 1
        start = index
        while index < length and command[index] != quote:
            index += 1
        target = command[start:index]
        if index < length:
            index += 1
        return target or None, index
    start = index
    while index < length and command[index] not in " \t\n;&|":
        index += 1
    token = command[start:index]
    return token or None, index
