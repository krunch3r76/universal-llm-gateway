"""Title-addressed focus target and landing proof for the attended IDE hop.

Why this exists: on COSMIC (jupiter) an ordinary client cannot focus another
window — there is no focus API, and a native-Wayland Cursor cannot raise itself
on ``cursor --folder-uri`` without an activation token (2026-09-12 04:24Z the
``vscode-remote://`` URI even went to Firefox). Every hop before 2026-09-12 06:00Z
therefore typed ``resume <R>`` into whatever window the operator had in front,
and the seat reported ``ok: true`` because the keys had been *sent*. Two
corrections live here: the window is addressed by **title** through the COSMIC
launcher (privileged; activates a toplevel by title, the same raise the operator
does by hand), and a hop is ``landed`` only when a new agent transcript carrying
the hop header appears — sent keys are not a delivered hop.
"""

from __future__ import annotations

import binascii
import json
import time
from pathlib import Path
from urllib.parse import unquote

REMOTE_SSH_PREFIX = "vscode-remote://ssh-remote+"


def ssh_host_name_from_uri(folder_uri: str) -> str | None:
    """Remote-SSH host label Cursor shows in the window title (``[SSH: <host>]``).

    The authority after ``ssh-remote+`` is either the bare host (``io``) or the
    hex-encoded JSON host config (``7b22686f73744e616d65223a22696f227d`` →
    ``{"hostName": "io"}``); both forms accumulate in ``workspaceStorage``.
    """
    uri = unquote(folder_uri or "")
    if not uri.startswith(REMOTE_SSH_PREFIX):
        return None
    authority = uri[len(REMOTE_SSH_PREFIX) :].split("/", 1)[0]
    if not authority:
        return None
    try:
        decoded = json.loads(binascii.unhexlify(authority).decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return authority
    host = decoded.get("hostName") if isinstance(decoded, dict) else None
    return str(host) if host else authority


def focus_title_for(remote_repo: str, host_name: str | None) -> str:
    """Launcher query that matches only the Remote-SSH Cursor window on ``remote_repo``.

    Cursor titles a remote window ``<editor> — <repo> [SSH: <host>] — Cursor``; the
    repo name plus the SSH marker never matches an application entry, so Enter in
    the launcher focuses the window instead of launching a second Cursor.
    """
    repo_name = Path(remote_repo).name
    return f"{repo_name} [SSH: {host_name}]" if host_name else repo_name


def hop_header_line(message: str) -> str:
    """The hop's identifying line (``Liaison IDE hop … tip_cp=N``) used as the landing marker."""
    for line in message.splitlines():
        if line.startswith("Liaison IDE hop"):
            return line
    return message.strip().splitlines()[0] if message.strip() else ""


def wait_for_landed_transcript(
    marker: str,
    *,
    since_epoch: float,
    transcripts_dir: Path,
    timeout_s: float = 30.0,
    poll_s: float = 2.0,
) -> str | None:
    """Transcript id of a tab born after ``since_epoch`` whose first user message carries ``marker``.

    The agent transcript is the only place a new tab's first message is observable
    from the hub, so it is the landing proof; ``None`` after ``timeout_s`` means the
    keys went somewhere other than a fresh Cursor chat.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        if transcripts_dir.is_dir():
            for path in transcripts_dir.glob("*/*.jsonl"):
                try:
                    if path.stat().st_mtime < since_epoch - 1.0:
                        continue
                    with path.open(encoding="utf-8") as fh:
                        first_line = fh.readline()
                except OSError:
                    continue
                if marker and marker in first_line:
                    return path.parent.name
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll_s)


__all__ = [
    "REMOTE_SSH_PREFIX",
    "focus_title_for",
    "hop_header_line",
    "ssh_host_name_from_uri",
    "wait_for_landed_transcript",
]
