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

Land identity (CDP 15456#2 / a:38362 amend): ``resume <root>`` + ``tip_cp=N`` with a
non-digit boundary + ``Liaison IDE hop`` on the first user line. Bare ``tip_cp=N``
substring match is refused — it false-oks number-prefix, other-root, and quoted
review tabs (F1).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

AGENTS_WINDOW_TITLE = "Cursor Agents"
AGENTS_WINDOW_APP_ID = "cursor"
_TIP_CP_VALUE_RE = re.compile(r"tip_cp=(\d+)(?!\d)")
_RESUME_RE = re.compile(r"(?:^|[\s\"])resume\s+(\d+)(?:\s|[\"\\n]|$)")


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


def hop_land_identity(
    message: str, *, root_id: str | None = None
) -> tuple[str | None, int | None]:
    """``(root_id, tip_cp)`` from the hop paste — tip from the Liaison line, root from resume."""
    tip: int | None = None
    header = hop_header_line(message)
    tip_m = _TIP_CP_VALUE_RE.search(header)
    if tip_m:
        tip = int(tip_m.group(1))
    root = (root_id or "").strip() or None
    if root is None:
        resume_m = _RESUME_RE.search(message or "")
        if resume_m:
            root = resume_m.group(1)
    return root, tip


def first_line_matches_land(
    first_line: str,
    *,
    root_id: str | None,
    tip_cp: int | None,
    marker: str,
) -> bool:
    """True when the first JSONL line is an exact land for this hop.

    With tip: require ``Liaison IDE hop``, ``resume <root>``, and ``tip_cp=N(?!\\d)``.
    Without tip: require the full marker substring (mtime gated by the caller).
    """
    if tip_cp is not None:
        if not root_id:
            return False
        if "Liaison IDE hop" not in first_line:
            return False
        # Bound resume so resume 154201 does not match root 15420.
        if not re.search(
            rf"(?:^|[\s\"])resume\s+{re.escape(root_id)}(?:\s|[\"\\n]|$)",
            first_line,
        ):
            return False
        if not re.search(rf"tip_cp={tip_cp}(?!\d)", first_line):
            return False
        return True
    text = (marker or "").strip()
    return bool(text) and text in first_line


def land_find_telemetry(
    *,
    root_id: str | None,
    tip_cp: int | None,
    marker: str,
    matches: int,
    matched_first_line_head: str | None = None,
) -> dict[str, Any]:
    """Needles + match count for ok and not_landed (15456 ask 3)."""
    needles: list[str] = []
    if tip_cp is not None and root_id:
        needles.append(f"resume {root_id}")
        needles.append(f"tip_cp={tip_cp}")
        needles.append("Liaison IDE hop")
    elif (marker or "").strip():
        needles.append(marker.strip())
    out: dict[str, Any] = {"needles": needles, "matches": matches}
    if matched_first_line_head is not None:
        out["matched_first_line_head"] = matched_first_line_head[:160]
    return out


def find_transcript_with_hop_header(
    marker: str,
    transcripts_dir: Path,
    *,
    root_id: str | None = None,
    tip_cp: int | None = None,
    since_epoch: float | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Transcript id whose first JSONL line is an exact land for this hop.

    Tip hops: exact ``(root, tip)`` — no mtime gate (re-hop / JSONL lag / clock skew).
    Tipless hops: full ``marker`` substring **and** ``mtime >= since_epoch - 1`` when
    ``since_epoch`` is set (recovery must not undo the wait mtime check — F3).
    """
    empty_tel = land_find_telemetry(
        root_id=root_id, tip_cp=tip_cp, marker=marker, matches=0
    )
    if not transcripts_dir.is_dir():
        return None, empty_tel
    rows: list[tuple[float, str, str]] = []
    for path in transcripts_dir.glob("*/*.jsonl"):
        try:
            with path.open(encoding="utf-8") as fh:
                first_line = fh.readline()
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if tip_cp is None and since_epoch is not None and mtime < since_epoch - 1.0:
            continue
        if not first_line_matches_land(
            first_line, root_id=root_id, tip_cp=tip_cp, marker=marker
        ):
            continue
        rows.append((mtime, path.parent.name, first_line[:160]))
    tel = land_find_telemetry(
        root_id=root_id,
        tip_cp=tip_cp,
        marker=marker,
        matches=len(rows),
        matched_first_line_head=rows[0][2] if rows else None,
    )
    if not rows:
        return None, tel
    rows.sort(key=lambda row: row[0], reverse=True)
    return rows[0][1], tel


def list_resume_transcript_ids(root_id: str, transcripts_dir: Path) -> set[str]:
    """Ids whose first line mentions ``resume <root_id>`` — pre-fire baseline for tipless."""
    found: set[str] = set()
    if not root_id or not transcripts_dir.is_dir():
        return found
    needle = re.compile(
        rf"(?:^|[\s\"])resume\s+{re.escape(root_id)}(?:\s|[\"\\n]|$)"
    )
    for path in transcripts_dir.glob("*/*.jsonl"):
        try:
            with path.open(encoding="utf-8") as fh:
                first_line = fh.readline()
        except OSError:
            continue
        if needle.search(first_line):
            found.add(path.parent.name)
    return found


def wait_for_landed_transcript(
    marker: str,
    *,
    since_epoch: float,
    transcripts_dir: Path,
    timeout_s: float = 30.0,
    poll_s: float = 2.0,
    root_id: str | None = None,
    tip_cp: int | None = None,
    pre_existing_ids: set[str] | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Transcript id whose first user message is an exact land for this hop.

    Tip hops: exact ``(root, tip)`` without mtime. Tipless: mtime after fire, or a
    new id not in ``pre_existing_ids`` that carries the full marker (15456 ask 2).
    """
    deadline = time.monotonic() + timeout_s
    last_tel = land_find_telemetry(
        root_id=root_id, tip_cp=tip_cp, marker=marker, matches=0
    )
    while True:
        if transcripts_dir.is_dir() and marker:
            found, last_tel = find_transcript_with_hop_header(
                marker,
                transcripts_dir,
                root_id=root_id,
                tip_cp=tip_cp,
                since_epoch=None if tip_cp is not None else since_epoch,
            )
            if found is not None:
                return found, last_tel
            if tip_cp is None and pre_existing_ids is not None:
                for path in transcripts_dir.glob("*/*.jsonl"):
                    tid = path.parent.name
                    if tid in pre_existing_ids:
                        continue
                    try:
                        with path.open(encoding="utf-8") as fh:
                            first_line = fh.readline()
                    except OSError:
                        continue
                    if first_line_matches_land(
                        first_line, root_id=root_id, tip_cp=None, marker=marker
                    ):
                        tel = land_find_telemetry(
                            root_id=root_id,
                            tip_cp=None,
                            marker=marker,
                            matches=1,
                            matched_first_line_head=first_line[:160],
                        )
                        return tid, tel
        if time.monotonic() >= deadline:
            return None, last_tel
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
    "first_line_matches_land",
    "focus_title_for",
    "hop_header_line",
    "hop_land_identity",
    "induction_head_line",
    "land_find_telemetry",
    "list_resume_transcript_ids",
    "transcript_byte_size",
    "wait_for_induction_landed",
    "wait_for_landed_transcript",
]
