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

The GUI host is **policy** (``liaison-tick.py --set gui_host=<ssh host>``), never
a constant: the operator may sit at any of several graphical hosts, each with a
Remote-SSH window into the hub, and a wrong default lands the hop — and the next
turn of premium spend — on a window nobody is watching (hops 1–2, 2026-09-11).
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
from bus_watch.ide_hop_landing import (
    AGENTS_WINDOW_APP_ID,
    focus_title_for,
    hop_header_line,
    wait_for_landed_transcript,
)
from bus_watch.liaison_digest import effective_policy
from bus_watch.state import read_state

_REPO = Path(__file__).resolve().parents[2]
HANDOFF_MSG_DIR = WATCH_DIR / "handoff-messages"
KEYSTROKE_SCRIPT = "scripts/orchestrator_tab_keystroke.py"
MESSAGE_CAP = 2048
LIVE_WATCHER_STATUSES = frozenset({"polling", "running"})
DEFAULT_REMOTE_REPO = os.environ.get("ORCHESTRATOR_REPO", str(_REPO))


def policy_gui_host(root_id: str, watch_dir: Path = WATCH_DIR) -> str | None:
    """``policy.gui_host`` from the liaison tick state; None when the operator never set it."""
    state = read_state(watch_dir / f"liaison-{root_id}.tick.json")
    host = effective_policy(state).get("gui_host")
    return str(host) if host else None


def policy_focus_title(root_id: str, watch_dir: Path = WATCH_DIR) -> str | None:
    """``policy.hop_focus_title`` — operator override of the launcher query when the
    derived ``Cursor <repo> [SSH: <host>]`` does not match the live window title."""
    state = read_state(watch_dir / f"liaison-{root_id}.tick.json")
    title = effective_policy(state).get("hop_focus_title")
    return str(title) if title else None


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


_DISCOVER_URI_SNIPPET = r"""
import glob, json, os, sys
repo = sys.argv[1]
best = (0.0, None)
for f in glob.glob(os.path.expanduser("~/.config/Cursor/User/workspaceStorage/*/workspace.json")):
    try:
        j = json.load(open(f))
    except Exception:
        continue
    uri = j.get("folder") or j.get("workspace") or ""
    if not uri.endswith(repo):
        continue
    db = os.path.join(os.path.dirname(f), "state.vscdb")
    mt = os.path.getmtime(db) if os.path.exists(db) else 0.0
    if mt > best[0]:
        best = (mt, uri)
print(best[1] or "")
"""


def discover_folder_uri(gui_host: str, *, remote_repo: str) -> str | None:
    """Folder URI of the GUI host's most recently used Cursor window on ``remote_repo``.

    Several encodings of one Remote-SSH authority accumulate in ``workspaceStorage``
    (``ssh-remote+io`` vs the hex host-config form); the entry whose ``state.vscdb``
    was written last is the live window. ``--folder-uri`` with a stale encoding
    opens a duplicate remote window instead of focusing the live one, so the URI
    is observed on the host, never guessed.
    """
    try:
        proc = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=20",
                gui_host,
                f"python3 -c {shlex.quote(_DISCOVER_URI_SNIPPET)} {shlex.quote(remote_repo)}",
            ],
            capture_output=True,
            text=True,
            timeout=45,
        )
    except subprocess.TimeoutExpired:
        return None
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    return lines[-1] if lines else None


def remote_launch_command(
    remote_msg_path: str,
    *,
    remote_repo: str,
    palette_query: str,
    raise_uri: str | None,
    focus_title: str | None = None,
) -> str:
    """Build the GUI-host command.

    Focus order: ``focus_title`` (compositor ``activate`` on the one toplevel matching
    app_id + title, verified before any key — the only raise that works for a
    native-Wayland Cursor) ≻ ``raise_uri`` (``cursor --folder-uri``, kept for
    compositors that honour it) ≻ neither (types into the focused window).
    """
    if focus_title:
        focus = (
            f"--no-raise --focus-title {shlex.quote(focus_title)} "
            f"--focus-app-id {shlex.quote(AGENTS_WINDOW_APP_ID)}"
        )
    elif raise_uri:
        focus = f"--raise-uri {shlex.quote(raise_uri)}"
    else:
        focus = "--no-raise"
    return (
        "export WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1000; "
        f"python3 {shlex.quote(f'{remote_repo}/{KEYSTROKE_SCRIPT}')} launch "
        f"--message-file {shlex.quote(remote_msg_path)} "
        f"--repo {shlex.quote(remote_repo)} "
        f"--palette-query {shlex.quote(palette_query)} "
        f"{focus}"
    )


def fire_ide_hop(
    message: str,
    *,
    root_id: str,
    gui_host: str | None,
    remote_repo: str = DEFAULT_REMOTE_REPO,
    palette_query: str = "New Chat",
    dry_run: bool = False,
    no_raise: bool = False,
    landing_timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Write the hop message where the GUI host sees it (NFS) and keystroke it into a new chat.

    Refuses when ``gui_host`` is unset (no fallback host) and when the host has no
    Cursor window on ``remote_repo`` to raise (no fallback focus): firing at a
    guessed display or a guessed window is the failure this module exists to
    prevent — hops 1–3 on 2026-09-11 landed on an unattended host and in Firefox.
    The agents window (``app_id=cursor``, title ``Cursor Agents`` — what the compositor
    reports, no repo or SSH text) is focused through ``zcosmic_toplevel_manager_v1``
    and verified activated before any key is sent; ``cursor --folder-uri`` cannot
    raise a native-Wayland Cursor (2026-09-12 04:24Z it handed the remote URI to
    Firefox) and every hop up to 06:11Z that day typed into whatever window was in
    front. ``no_raise`` skips the focus step for an operator who is on the window and
    says so. ``ok`` means **landed**: a new agent transcript carrying the hop header
    appeared after the keystrokes — sent keys are not a hop.
    """
    if not gui_host:
        return {
            "ok": False,
            "phase": "gui_host_unset",
            "root": root_id,
            "fix": f"scripts/liaison-tick.py --root {root_id} --set gui_host=<ssh host>",
        }
    raise_uri = (
        None if no_raise else discover_folder_uri(gui_host, remote_repo=remote_repo)
    )
    if not raise_uri and not no_raise:
        return {
            "ok": False,
            "phase": "no_cursor_window_for_repo",
            "root": root_id,
            "gui_host": gui_host,
            "fix": f"open {remote_repo} in Cursor on {gui_host} (Remote-SSH) before hopping",
        }
    focus_title = None if no_raise else focus_title_for(policy_focus_title(root_id))
    HANDOFF_MSG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    msg_path = HANDOFF_MSG_DIR / f"liaison-{root_id}-{stamp}.md"
    durable_write_text(msg_path, message)
    remote_msg = f"{remote_repo}/{msg_path.relative_to(_REPO)}"
    cmd = remote_launch_command(
        remote_msg,
        remote_repo=remote_repo,
        palette_query=palette_query,
        raise_uri=raise_uri,
        focus_title=focus_title,
    )
    result: dict[str, Any] = {
        "root": root_id,
        "message_path": str(msg_path),
        "gui_host": gui_host,
        "raise_uri": raise_uri,
        "focus_title": focus_title,
        "remote_cmd": cmd,
    }
    if dry_run:
        return {"ok": True, "dry_run": True, **result}
    fired_at = time.time()
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
    landed_id = wait_for_landed_transcript(
        hop_header_line(message),
        since_epoch=fired_at,
        transcripts_dir=AGENT_TRANSCRIPTS,
        timeout_s=landing_timeout_s,
    )
    if landed_id is None:
        return {
            "ok": False,
            "phase": "not_landed",
            "keystroke": keystroke,
            "fix": (
                "no new Cursor chat carries the hop header — the keys went to another "
                f"window; check the launcher matched {focus_title!r} on {gui_host}"
            ),
            **result,
        }
    return {
        "ok": True,
        "landed_transcript_id": landed_id,
        "keystroke": keystroke,
        **result,
    }
