"""Focus target and landing proof for the attended IDE hop.

Why this exists: on COSMIC (jupiter) a native-Wayland Cursor cannot raise itself on
``cursor --folder-uri`` (no activation token; 2026-09-12 04:24Z the
``vscode-remote://`` URI even went to Firefox), and driving the COSMIC launcher by
keystrokes guessed wrong twice (fuzzy-matched UMLet 06:09Z, launched a second IDE
window 06:11Z). Every hop before then typed ``resume <R>`` into whatever window the
operator had in front while the seat reported ``ok: true`` because keys had been
*sent*. Two corrections live here: the target window is named by what the
compositor actually reports — ``ext_foreign_toplevel_list_v1`` on jupiter lists the
agents window as ``app_id=cursor`` / ``title="Cursor Agents"`` (no repo, no SSH
marker in the title) — and a hop is ``landed`` only when a new agent transcript
carrying the hop header appears; sent keys are not a delivered hop.
"""

from __future__ import annotations

import time
from pathlib import Path

AGENTS_WINDOW_TITLE = "Cursor Agents"
AGENTS_WINDOW_APP_ID = "cursor"


def focus_title_for(policy_override: str | None = None) -> str:
    """Title substring the hop focuses: the agents (Glass) window, unless policy names another.

    The IDE window is titled after the workspace (``… — <repo> [SSH: <host>] — Cursor``);
    the agents window is just ``Cursor Agents``. The house runs in the agents window
    (its transcripts land under the hub's agent-transcripts), so that is the default;
    ``policy.hop_focus_title`` overrides when the operator wants a different toplevel.
    """
    return policy_override or AGENTS_WINDOW_TITLE


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
    "AGENTS_WINDOW_APP_ID",
    "AGENTS_WINDOW_TITLE",
    "focus_title_for",
    "hop_header_line",
    "wait_for_landed_transcript",
]
