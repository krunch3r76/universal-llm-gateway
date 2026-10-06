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

import json
import re
import time
from pathlib import Path

AGENTS_WINDOW_TITLE = "Cursor Agents"
AGENTS_WINDOW_APP_ID = "cursor"
_TIP_CP_NEEDLE_RE = re.compile(r"tip_cp=\d+")
_TIP_CP_VALUE_RE = re.compile(r"tip_cp=(\d+)")


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


def land_find_needles(marker: str) -> list[str]:
    """Needles for header land-proof — unique ``tip_cp=N`` first, then the full marker.

    ``tip_cp=N`` is preferred so a prior ``resume <R>`` tab cannot win the recovery
    pass (a:38356 / a:38362). Full marker remains for hops that omit tip_cp.
    """
    needles: list[str] = []
    tip = _TIP_CP_NEEDLE_RE.search(marker or "")
    if tip:
        needles.append(tip.group(0))
    text = (marker or "").strip()
    if text and text not in needles:
        needles.append(text)
    return needles


def find_transcript_with_hop_header(
    marker: str,
    transcripts_dir: Path,
) -> str | None:
    """Transcript id whose first JSONL line carries the hop header — no mtime gate.

    Recovery after ``wait_for_landed_transcript`` times out: JSONL lag or an
    Agents-only compositor view can leave ``phase=not_landed`` while the
    successor already holds ``tip_cp=N`` / the hop header (a:38356, a:38362).
    Among matches, highest ``tip_cp`` then newest mtime wins (same order as
    ``find_transcript_id``).
    """
    if not transcripts_dir.is_dir():
        return None
    for needle in land_find_needles(marker):
        rows: list[tuple[int, float, str]] = []
        for path in transcripts_dir.glob("*/*.jsonl"):
            try:
                with path.open(encoding="utf-8") as fh:
                    first_line = fh.readline()
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if needle not in first_line:
                continue
            tip_m = _TIP_CP_VALUE_RE.search(first_line)
            rows.append(
                (int(tip_m.group(1)) if tip_m else -1, mtime, path.parent.name)
            )
        if rows:
            rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
            return rows[0][2]
    return None


def wait_for_landed_transcript(
    marker: str,
    *,
    since_epoch: float,
    transcripts_dir: Path,
    timeout_s: float = 30.0,
    poll_s: float = 2.0,
) -> str | None:
    """Transcript id whose first user message carries ``marker`` (land proof).

    Prefers a tab with mtime after ``since_epoch``. When ``marker`` carries a
    unique ``tip_cp=N``, also accepts that header without the mtime gate so a
    successor that already landed (partial prior fire / clock skew) is not
    reported as ``not_landed`` (a:38362). ``None`` after ``timeout_s`` means no
    hop-header transcript yet — callers run one find-transcript recovery pass
    before paging the operator.
    """
    tip_needle = None
    tip_m = _TIP_CP_NEEDLE_RE.search(marker or "")
    if tip_m:
        tip_needle = tip_m.group(0)
    deadline = time.monotonic() + timeout_s
    while True:
        if transcripts_dir.is_dir() and marker:
            # Unique tip_cp: header presence is land proof (mtime optional).
            if tip_needle:
                found = find_transcript_with_hop_header(tip_needle, transcripts_dir)
                if found is not None:
                    return found
            for path in transcripts_dir.glob("*/*.jsonl"):
                try:
                    if path.stat().st_mtime < since_epoch - 1.0:
                        continue
                    with path.open(encoding="utf-8") as fh:
                        first_line = fh.readline()
                except OSError:
                    continue
                if marker in first_line:
                    return path.parent.name
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll_s)


def induction_head_line(message: str) -> str:
    """First line of the induction block — landing marker in the holder transcript."""
    return message.strip().splitlines()[0] if message.strip() else ""


def transcript_byte_size(transcript_id: str, transcripts_dir: Path) -> int:
    """Current byte length of the holder transcript JSONL, or 0 when absent."""
    path = transcripts_dir / transcript_id / f"{transcript_id}.jsonl"
    try:
        return path.stat().st_size
    except OSError:
        return 0


def wait_for_induction_landed(
    transcript_id: str,
    marker: str,
    *,
    since_bytes: int,
    transcripts_dir: Path,
    timeout_s: float = 30.0,
    poll_s: float = 2.0,
) -> bool:
    """True when the holder transcript gains a user row carrying ``marker`` after ``since_bytes``."""
    if not marker:
        return False
    path = transcripts_dir / transcript_id / f"{transcript_id}.jsonl"
    deadline = time.monotonic() + timeout_s
    while True:
        if path.is_file():
            try:
                with path.open(encoding="utf-8") as fh:
                    fh.seek(since_bytes)
                    for line in fh:
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if row.get("role") != "user":
                            continue
                        content = (row.get("message") or {}).get("content") or []
                        text = " ".join(
                            str(block.get("text") or "") for block in content
                        )
                        if marker in text:
                            return True
            except OSError:
                pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_s)


__all__ = [
    "AGENTS_WINDOW_APP_ID",
    "AGENTS_WINDOW_TITLE",
    "find_transcript_with_hop_header",
    "focus_title_for",
    "hop_header_line",
    "induction_head_line",
    "land_find_needles",
    "transcript_byte_size",
    "wait_for_induction_landed",
    "wait_for_landed_transcript",
]
