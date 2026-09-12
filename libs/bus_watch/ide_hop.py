"""IDE keystroke hop for the attended liaison seat.

The attended liaison lives in one Cursor IDE tab whose context grows every
turn, and every turn re-sends that context. A hop ends the tab right after a
CHECKPOINT and opens a fresh tab on the graphical host that resumes the same
root (``resume <R>``), re-arms the wake tails the old tab held, and continues
the cadence Plan -> Dispatch -> Hop -> Arm -> Harvest.

This is a third rotation next to the conductor row-hop and the headless
cursor-sdk successor in ``spawn_on_wake``: it is seat-level, attended, and
targets a GUI, so it needs neither the seat lock nor a dispatch admit.

Why keystrokes, and why no ``cursor -r``: the IDE window is a Remote-SSH
window (GUI on the graphical host, cursor-server on the hub). ``cursor -r
<repo>`` on the GUI host would open the NFS path as a *local* workspace
instead of raising the remote window, so the launch pastes into the Cursor
window that already has focus and the attended operator owns that focus.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from durable_io.atomic import durable_write_text

from bus_watch.fable_lock import WATCH_DIR

_REPO = Path(__file__).resolve().parents[2]
HANDOFF_MSG_DIR = WATCH_DIR / "handoff-messages"
KEYSTROKE_SCRIPT = "scripts/orchestrator_tab_keystroke.py"
MESSAGE_CAP = 2048
LIVE_WATCHER_STATUSES = frozenset({"polling", "running"})
DEFAULT_GUI_HOST = os.environ.get("ORCHESTRATOR_SSH_HOST", "orion-node")
DEFAULT_REMOTE_REPO = os.environ.get("ORCHESTRATOR_REPO", str(_REPO))
AGENT_TRANSCRIPTS = (
    Path.home()
    / ".cursor/projects/mnt-torus-projects-universal-llm-gateway/agent-transcripts"
)

TAIL_RECIPE = (
    "watch-supervise.sh tail --label {label}  (background Shell, block_until_ms 0, "
    "notify_on_output: closeout turn=|consult complete|stall-pop:)"
)


def live_watcher_labels(root_id: str, watch_dir: Path = WATCH_DIR) -> list[str]:
    """Labels of pollers still running for ``root_id`` — the tails a successor tab must re-arm.

    A label is the state-file stem (what ``watch-supervise.sh tail --label`` takes).
    A poller belongs to the root when its stem carries the root prefix or its
    ``thread`` is the root itself; terminal statuses are skipped because the tail
    would exit immediately and the digest already surfaces them as unrelayed.
    """
    labels: list[str] = []
    for path in sorted(watch_dir.glob("*.state.json")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            not isinstance(state, dict)
            or state.get("status") not in LIVE_WATCHER_STATUSES
        ):
            continue
        stem = path.name.removesuffix(".state.json")
        if stem.startswith(f"{root_id}-") or str(state.get("thread")) == str(root_id):
            labels.append(stem)
    return labels


def build_ide_hop_message(
    root_id: str,
    *,
    row: str,
    arm_labels: list[str],
    tip_cp_ordinal: int | None = None,
    workspace: str = "universal-llm-gateway",
    cap: int = MESSAGE_CAP,
) -> str:
    """First user message of the successor tab; ``resume <R>`` first so the fence hook fires."""
    arm_lines = [f"ARM: {TAIL_RECIPE.format(label=label)}" for label in arm_labels] or [
        "ARM: none live — Plan from the digest (`scripts/liaison-tick.py --root R --once`)."
    ]
    tip = f" tip_cp={tip_cp_ordinal}" if tip_cp_ordinal is not None else ""
    lines = [
        f"resume {root_id}",
        "",
        f"Liaison IDE hop (attended register){tip}. Use the liaison skill.",
        f"Guard: workspace must be `{workspace}` — otherwise stop and say so.",
        f"NOW: {row}",
        *arm_lines,
        "Then: harvest every wake -> fold scoreboard -> Plan -> Dispatch (+watcher) -> "
        f'CHECKPOINT -> `scripts/liaison-ide-hop.py --root {root_id} --row "<NOW>"`.',
    ]
    message = "\n".join(lines) + "\n"
    encoded = message.encode("utf-8")
    if len(encoded) > cap:
        raise ValueError(f"ide hop message exceeds {cap} bytes ({len(encoded)})")
    return message


def find_transcript_id(
    first_user_text: str, transcripts_dir: Path = AGENT_TRANSCRIPTS
) -> str | None:
    """Transcript id of the tab whose first user message contains ``first_user_text``.

    The seat cannot read its own tab id; the JSONL under agent-transcripts is the
    only place it appears, and the newest match is the live tab. Needed for
    ``continuity(op=checkpoint, surface=cursor, transcript_id=...)``.
    """
    if not transcripts_dir.is_dir():
        return None
    candidates = sorted(
        transcripts_dir.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for path in candidates:
        try:
            with path.open(encoding="utf-8") as fh:
                first_line = fh.readline()
        except OSError:
            continue
        if first_user_text in first_line:
            return path.parent.name
    return None


def remote_launch_command(
    remote_msg_path: str, *, remote_repo: str, palette_query: str
) -> str:
    return (
        "export WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1000; "
        f"python3 {shlex.quote(f'{remote_repo}/{KEYSTROKE_SCRIPT}')} launch "
        f"--message-file {shlex.quote(remote_msg_path)} "
        f"--repo {shlex.quote(remote_repo)} "
        f"--palette-query {shlex.quote(palette_query)} --no-raise"
    )


def fire_ide_hop(
    message: str,
    *,
    root_id: str,
    gui_host: str = DEFAULT_GUI_HOST,
    remote_repo: str = DEFAULT_REMOTE_REPO,
    palette_query: str = "New Chat",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Write the hop message where the GUI host sees it (NFS) and keystroke it into a new chat."""
    HANDOFF_MSG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    msg_path = HANDOFF_MSG_DIR / f"liaison-{root_id}-{stamp}.md"
    durable_write_text(msg_path, message)
    remote_msg = f"{remote_repo}/{msg_path.relative_to(_REPO)}"
    cmd = remote_launch_command(
        remote_msg, remote_repo=remote_repo, palette_query=palette_query
    )
    result: dict[str, Any] = {
        "root": root_id,
        "message_path": str(msg_path),
        "gui_host": gui_host,
        "remote_cmd": cmd,
    }
    if dry_run:
        return {"ok": True, "dry_run": True, **result}
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", gui_host, cmd],
            capture_output=True,
            text=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "phase": "ssh_timeout", **result}
    if proc.returncode != 0:
        return {
            "ok": False,
            "phase": "keystroke",
            "returncode": proc.returncode,
            "stderr": proc.stderr[-1000:],
            "stdout": proc.stdout[-1000:],
            **result,
        }
    try:
        keystroke = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        keystroke = {"raw_stdout": proc.stdout[-500:]}
    return {"ok": True, "keystroke": keystroke, **result}
