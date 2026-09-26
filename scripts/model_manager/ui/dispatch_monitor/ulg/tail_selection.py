"""Which live row the transcript pane follows.

The selection is a file the pane reads. It is not a field on the projection.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scripts.model_manager.ui.dispatch_monitor.core.board_lines import (
    live_cdp,
    live_sdk,
)
from scripts.model_manager.ui.dispatch_monitor.core.dtos import (
    CdpLegRow,
    SdkDispatchRow,
)

DEFAULT_SELECTION_PATH = Path("/tmp/ulg-dispatch-board-selection.json")
PANE_TITLE = "dispatch-prose"


@dataclass(frozen=True)
class TailTarget:
    """One selectable live row."""

    kind: str
    key: str
    label: str


def tail_targets(
    sdk: tuple[SdkDispatchRow, ...] | list[SdkDispatchRow],
    cdp: tuple[CdpLegRow, ...] | list[CdpLegRow],
) -> list[TailTarget]:
    """Live cursor-sdk rows, then live CDP rows that have a harvest key."""
    targets: list[TailTarget] = []
    for row in live_sdk(tuple(sdk)):
        targets.append(
            TailTarget(kind="cursor-sdk", key=row.dispatch_id, label=row.dispatch_id)
        )
    for row in live_cdp(tuple(cdp)):
        key = row.chat_url or row.registration_id
        if not key:
            continue
        targets.append(TailTarget(kind="cdp", key=key, label=row.request_id))
    return targets


def move_selection(index: int, count: int, verb: str) -> tuple[int, bool]:
    """Move ``index`` or arm the tail. ``verb`` is ``up``, ``down``, or ``enter``."""
    if count <= 0:
        return 0, False
    if verb == "down":
        return min(index + 1, count - 1), False
    if verb == "up":
        return max(index - 1, 0), False
    if verb == "enter":
        return min(max(index, 0), count - 1), True
    return min(max(index, 0), count - 1), False


def write_selection(path: Path, target: TailTarget) -> None:
    """Atomically publish the row the pane should follow."""
    payload = {"kind": target.kind, "key": target.key, "label": target.label}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(path)


def read_selection(path: Path) -> dict[str, str] | None:
    """Return the published row, or None when the file is missing or unreadable."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    key = raw.get("key")
    if not isinstance(kind, str) or not isinstance(key, str) or not kind or not key:
        return None
    label = raw.get("label")
    return {
        "kind": kind,
        "key": key,
        "label": label if isinstance(label, str) and label else key,
    }


def ensure_prose_pane(
    launcher: Path,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    """Split a tmux pane titled ``dispatch-prose`` when one is not already open.

    Outside tmux this returns ``not_in_tmux`` and does not spawn a process.
    """
    if not os.environ.get("TMUX"):
        return "not_in_tmux"

    def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return run(
            args,
            check=False,
            capture_output=True,
            text=True,
        )

    listed = _run(["tmux", "list-panes", "-F", "#{pane_title}"])
    if listed.returncode != 0:
        return "tmux_list_failed"
    titles = [line.strip() for line in listed.stdout.splitlines()]
    if PANE_TITLE in titles:
        return "already_open"
    opened = _run(
        [
            "tmux",
            "split-window",
            "-v",
            "-l",
            "14",
            "-d",
            "-P",
            "-F",
            "#{pane_id}",
            str(launcher),
        ]
    )
    if opened.returncode != 0 or not opened.stdout.strip():
        return "tmux_split_failed"
    pane_id = opened.stdout.strip().splitlines()[-1]
    _run(["tmux", "select-pane", "-t", pane_id, "-T", PANE_TITLE])
    _run(["tmux", "set-option", "-t", pane_id, "remain-on-exit", "on"])
    return "opened"
