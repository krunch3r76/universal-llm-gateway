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
from scripts.model_manager.ui.dispatch_monitor.core.watch import clip_text
from scripts.model_manager.ui.dispatch_monitor.core.dtos import (
    CdpLegRow,
    SdkDispatchRow,
)

DEFAULT_SELECTION_PATH = Path("/tmp/ulg-dispatch-board-selection.json")
PANE_TITLE = "transcript"
PROMPT_PANE_TITLE = "prompt"
BOARD_PANE_TITLE = "board"

SECTION_ORDER = ("tick", "lease", "sdk", "cdp", "attention")

SECTION_STATUS_LABELS = {
    "tick": "TICK",
    "lease": "LEASE",
    "sdk": "SDK",
    "cdp": "CDP",
    "attention": "ATTENTION",
}

DISPATCH_STATUS_RIGHT = " #{pane_title} · #{@dispatch_board_section} "

TRANSCRIPT_LABEL_WIDTH = 12


def format_transcript_pane_title(label: str) -> str:
    """Window status / border title for the transcript pane."""
    short = clip_text(label, TRANSCRIPT_LABEL_WIDTH)
    if not short:
        return PANE_TITLE
    return f"{PANE_TITLE} · {short}"


def is_transcript_pane_title(title: str) -> bool:
    """True when ``title`` is the transcript pane (static or row-qualified)."""
    stripped = title.strip()
    if stripped == PANE_TITLE:
        return True
    return stripped.startswith(f"{PANE_TITLE} ·")


def update_transcript_pane_title(
    label: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> None:
    """Retitle the current tmux pane to ``transcript · <short>``; no-op outside tmux."""
    if not os.environ.get("TMUX"):
        return

    def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
        caller = run if run is not None else subprocess.run
        return caller(args, check=False, capture_output=True, text=True)

    _run(["tmux", "select-pane", "-T", format_transcript_pane_title(label)])


@dataclass(frozen=True)
class TailTarget:
    """One selectable live row.

    ``prompt_key`` is the id the prompt pane reads. For cursor-sdk that is
    the dispatch id. For CDP it is the execution id, empty when the row
    has none.
    """

    kind: str
    key: str
    label: str
    prompt_key: str = ""


def tail_targets(
    sdk: tuple[SdkDispatchRow, ...] | list[SdkDispatchRow],
    cdp: tuple[CdpLegRow, ...] | list[CdpLegRow],
) -> list[TailTarget]:
    """Live cursor-sdk rows, then live CDP rows that have a harvest key."""
    targets: list[TailTarget] = []
    for row in live_sdk(tuple(sdk)):
        targets.append(
            TailTarget(
                kind="cursor-sdk",
                key=row.dispatch_id,
                label=row.dispatch_id,
                prompt_key=row.dispatch_id,
            )
        )
    for row in live_cdp(tuple(cdp)):
        key = row.chat_url or row.registration_id
        if not key:
            continue
        targets.append(
            TailTarget(
                kind="cdp",
                key=key,
                label=row.request_id,
                prompt_key=row.execution_id or "",
            )
        )
    return targets


def advance_section(section: str, delta: int) -> str:
    """Cycle painted-section focus within the single board pane."""
    try:
        idx = SECTION_ORDER.index(section)
    except ValueError:
        idx = 0
    return SECTION_ORDER[(idx + delta) % len(SECTION_ORDER)]


def targets_in_section(
    targets: list[TailTarget], section: str
) -> list[TailTarget]:
    """Live rows selectable while ``section`` is focused (SDK/CDP only)."""
    if section == "sdk":
        return [row for row in targets if row.kind == "cursor-sdk"]
    if section == "cdp":
        return [row for row in targets if row.kind == "cdp"]
    return []


def configure_dispatch_status_hint(
    run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> None:
    """Window-scoped tmux status + pane borders for dispatch-board focus."""
    if not os.environ.get("TMUX"):
        return

    def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
        caller = run if run is not None else subprocess.run
        return caller(args, check=False, capture_output=True, text=True)

    _enable_pane_borders(_run)
    _run(["tmux", "set-option", "-w", "@dispatch_board_section", "SDK"])
    _run(["tmux", "set-option", "-w", "status-right", DISPATCH_STATUS_RIGHT])
    # Keep the curses board pane titled even when allow-rename / host
    # defaults would otherwise show the hostname (e.g. ``io``).
    _run(["tmux", "set-option", "-p", "allow-rename", "off"])
    _run(["tmux", "select-pane", "-T", BOARD_PANE_TITLE])


def publish_board_section_focus(
    section: str,
    *,
    zoom: bool = False,
    run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> None:
    """Publish focused painted section for the window status line."""
    if not os.environ.get("TMUX"):
        return
    label = SECTION_STATUS_LABELS.get(section, section.upper())
    if zoom:
        label = f"{label}·zoom"

    def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
        caller = run if run is not None else subprocess.run
        return caller(args, check=False, capture_output=True, text=True)

    _run(["tmux", "set-option", "-w", "@dispatch_board_section", label])


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
    payload = {
        "kind": target.kind,
        "key": target.key,
        "label": target.label,
        "prompt_key": target.prompt_key,
    }
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
    prompt_key = raw.get("prompt_key")
    selected = {
        "kind": kind,
        "key": key,
        "label": label if isinstance(label, str) and label else key,
    }
    if isinstance(prompt_key, str):
        selected["prompt_key"] = prompt_key
    return selected


def _enable_pane_borders(
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Show ``#{pane_title}`` on this window only.

    ``pane-border-status`` is a window option. Setting it without ``-t``
    applies to the window that owns the current pane.
    """
    run(["tmux", "set-option", "-w", "pane-border-status", "top"])
    run(["tmux", "set-option", "-w", "pane-border-format", " #{pane_title} "])


def ensure_prose_pane(
    launcher: Path,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    """Split a tmux pane titled ``transcript`` when one is not already open.

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

    _enable_pane_borders(_run)
    board = _run(["tmux", "display-message", "-p", "#{pane_id}"])
    board_id = board.stdout.strip() if board.returncode == 0 else ""
    listed = _run(["tmux", "list-panes", "-F", "#{pane_title}"])
    if listed.returncode != 0:
        return "tmux_list_failed"
    titles = [line.strip() for line in listed.stdout.splitlines()]
    if any(is_transcript_pane_title(title) for title in titles):
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
    if board_id:
        _run(["tmux", "set-option", "-p", "-t", board_id, "allow-rename", "off"])
        _run(["tmux", "select-pane", "-t", board_id, "-T", BOARD_PANE_TITLE])
    return "opened"


def ensure_prompt_pane(
    launcher: Path,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    """Split ``prompt``, or respawn it so the new selection prints.

    The printer exits after one body. A second ``p`` has to start it again.
    """
    if not os.environ.get("TMUX"):
        return "not_in_tmux"

    def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return run(args, check=False, capture_output=True, text=True)

    _enable_pane_borders(_run)
    board = _run(["tmux", "display-message", "-p", "#{pane_id}"])
    board_id = board.stdout.strip() if board.returncode == 0 else ""
    listed = _run(["tmux", "list-panes", "-F", "#{pane_id}\t#{pane_title}"])
    if listed.returncode != 0:
        return "tmux_list_failed"
    existing = ""
    for line in listed.stdout.splitlines():
        pane_id, _, title = line.partition("\t")
        if title.strip() == PROMPT_PANE_TITLE and pane_id.strip():
            existing = pane_id.strip()
            break

    def _retitle_board() -> None:
        if not board_id:
            return
        _run(["tmux", "set-option", "-p", "-t", board_id, "allow-rename", "off"])
        _run(["tmux", "select-pane", "-t", board_id, "-T", BOARD_PANE_TITLE])

    if existing:
        refreshed = _run(["tmux", "respawn-pane", "-k", "-t", existing, str(launcher)])
        if refreshed.returncode != 0:
            return "tmux_respawn_failed"
        _run(["tmux", "select-pane", "-t", existing, "-T", PROMPT_PANE_TITLE])
        _retitle_board()
        return "refreshed"
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
    _run(["tmux", "select-pane", "-t", pane_id, "-T", PROMPT_PANE_TITLE])
    _run(["tmux", "set-option", "-t", pane_id, "remain-on-exit", "on"])
    _retitle_board()
    return "opened"
