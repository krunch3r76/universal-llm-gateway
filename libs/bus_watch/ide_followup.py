"""IDE follow-up keystroke — paste induction into the lock-holder tab (R10a)."""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from typing import Any

from durable_io.atomic import durable_write_text

from bus_watch.fable_lock import read_lock
from bus_watch.ide_budget import AGENT_TRANSCRIPTS, ide_holder_transcript
from bus_watch.ide_hop import (
    _REPO,
    DEFAULT_REMOTE_REPO,
    GUI_HOST_UNSET_FIX,
    HANDOFF_MSG_DIR,
    KEYSTROKE_SCRIPT,
    policy_focus_title,
)

NAMED_FOLLOWUP_SCRIPT = "scripts/stall_tab_followup_keystroke.py"
from bus_watch.ide_hop_landing import (
    AGENTS_WINDOW_APP_ID,
    focus_title_for,
    induction_head_line,
    transcript_byte_size,
    wait_for_induction_landed,
)


def remote_followup_command(
    remote_msg_path: str,
    *,
    remote_repo: str,
    focus_title: str | None = None,
    no_raise: bool = False,
) -> str:
    """Build the GUI-host followup command: paste and Ctrl+Enter into the focused IDE tab.

    This path does not send Ctrl+T, so it does not open a new tab. Glass is a
    different toplevel and is not selected here.
    """
    if no_raise:
        focus = "--no-raise"
    else:
        title = focus_title or focus_title_for()
        focus = (
            f"--no-raise --focus-title {shlex.quote(title)} "
            f"--focus-app-id {shlex.quote(AGENTS_WINDOW_APP_ID)}"
        )
    return (
        "export WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1000; "
        f"python3 {shlex.quote(f'{remote_repo}/{KEYSTROKE_SCRIPT}')} followup "
        f"--message-file {shlex.quote(remote_msg_path)} "
        f"--repo {shlex.quote(remote_repo)} "
        f"{focus}"
    )


def remote_named_followup_command(
    remote_msg_path: str,
    *,
    remote_repo: str,
    chat_title: str,
    focus_title: str | None = None,
) -> str:
    """Same-tab followup: compositor focus + Ctrl+K ``chat_title`` + Ctrl+Enter."""
    window = focus_title or focus_title_for()
    return (
        "export WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1000; "
        f"python3 {shlex.quote(f'{remote_repo}/{NAMED_FOLLOWUP_SCRIPT}')} "
        f"--message-file {shlex.quote(remote_msg_path)} "
        f"--chat-title {shlex.quote(chat_title)} "
        f"--repo {shlex.quote(remote_repo)} "
        f"--focus-title {shlex.quote(window)} "
        f"--focus-app-id {shlex.quote(AGENTS_WINDOW_APP_ID)}"
    )


def fire_ide_followup(
    message: str,
    *,
    root_id: str,
    gui_host: str | None,
    remote_repo: str = DEFAULT_REMOTE_REPO,
    dry_run: bool = False,
    no_raise: bool = False,
    landing_timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Paste induction into the lock-holder IDE tab; landing = user row with WAKE <root>."""
    if not gui_host:
        return {
            "ok": False,
            "phase": "gui_host_unset",
            "root": root_id,
            "fix": GUI_HOST_UNSET_FIX,
        }
    lock = read_lock(root_id)
    transcript_id = ide_holder_transcript(lock)
    if not transcript_id:
        return {
            "ok": False,
            "phase": "not_ide_holder",
            "root": root_id,
            "holder": lock.get("holder"),
            "fix": "claim ide:<transcript_id> on the live Cursor tab before followup",
        }
    focus_title = None if no_raise else focus_title_for(policy_focus_title(root_id))
    HANDOFF_MSG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    msg_path = HANDOFF_MSG_DIR / f"liaison-{root_id}-followup-{stamp}.md"
    durable_write_text(msg_path, message)
    remote_msg = f"{remote_repo}/{msg_path.relative_to(_REPO)}"
    cmd = remote_followup_command(
        remote_msg,
        remote_repo=remote_repo,
        focus_title=focus_title,
        no_raise=no_raise,
    )
    marker = induction_head_line(message)
    since_bytes = transcript_byte_size(transcript_id, AGENT_TRANSCRIPTS)
    result: dict[str, Any] = {
        "root": root_id,
        "message_path": str(msg_path),
        "gui_host": gui_host,
        "holder_transcript_id": transcript_id,
        "focus_title": focus_title,
        "remote_cmd": cmd,
        "induction_head": marker,
    }
    if dry_run:
        return {"ok": True, "dry_run": True, "induction": message, **result}
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
    landed = wait_for_induction_landed(
        transcript_id,
        marker,
        since_bytes=since_bytes,
        transcripts_dir=AGENT_TRANSCRIPTS,
        timeout_s=landing_timeout_s,
    )
    if not landed:
        return {
            "ok": False,
            "phase": "not_landed",
            "keystroke": keystroke,
            "fix": (
                f"holder transcript {transcript_id!r} did not gain a user row with "
                f"{marker!r} — paste/submit missed the live composer"
            ),
            **result,
        }
    return {
        "ok": True,
        "landed": True,
        "holder_transcript_id": transcript_id,
        "keystroke": keystroke,
        **result,
    }


def fire_transcript_followup(
    message: str,
    *,
    transcript_id: str,
    chat_title: str,
    gui_host: str | None,
    remote_repo: str = DEFAULT_REMOTE_REPO,
    dry_run: bool = False,
    landing_timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Paste into a named Agents chat; landing = user row on ``transcript_id``."""
    if not gui_host:
        return {
            "ok": False,
            "phase": "gui_host_unset",
            "transcript_id": transcript_id,
            "fix": GUI_HOST_UNSET_FIX,
        }
    HANDOFF_MSG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    msg_path = HANDOFF_MSG_DIR / f"stall-followup-{transcript_id[:8]}-{stamp}.md"
    durable_write_text(msg_path, message)
    remote_msg = f"{remote_repo}/{msg_path.relative_to(_REPO)}"
    cmd = remote_named_followup_command(
        remote_msg,
        remote_repo=remote_repo,
        chat_title=chat_title,
    )
    marker = induction_head_line(message)
    since_bytes = transcript_byte_size(transcript_id, AGENT_TRANSCRIPTS)
    result: dict[str, Any] = {
        "transcript_id": transcript_id,
        "chat_title": chat_title,
        "message_path": str(msg_path),
        "gui_host": gui_host,
        "remote_cmd": cmd,
        "induction_head": marker,
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
    landed = wait_for_induction_landed(
        transcript_id,
        marker,
        since_bytes=since_bytes,
        transcripts_dir=AGENT_TRANSCRIPTS,
        timeout_s=landing_timeout_s,
    )
    if not landed:
        return {
            "ok": False,
            "phase": "not_landed",
            "keystroke": keystroke,
            "fix": (
                f"transcript {transcript_id!r} did not gain {marker!r} — "
                "Ctrl+K missed the named chat or submit missed the composer"
            ),
            **result,
        }
    return {"ok": True, "landed": True, "keystroke": keystroke, **result}


__all__ = [
    "fire_ide_followup",
    "fire_transcript_followup",
    "remote_followup_command",
    "remote_named_followup_command",
]
