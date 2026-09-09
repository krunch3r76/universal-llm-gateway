"""Legacy md-v1 reader, sealed envelope loaders, and parse hygiene (Phase 2)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from continuity_tape.messages import TOOL_MARKER_RE, ContinuityMessagesEnvelope
from continuity_tape.render_md import _EMPTY_ASSISTANT, _EMPTY_USER

_TURN_HEADING_RE = re.compile(r"^## Turn (\d+)")
_SOURCE_MD = "cursor-seal-md"


def load_sealed_envelope_from_path(path: Path) -> ContinuityMessagesEnvelope:
    data = json.loads(path.read_text(encoding="utf-8"))
    return ContinuityMessagesEnvelope.model_validate(data)


def load_sealed_envelope(
    row: dict[str, Any] | Any,
    *,
    files_root: Path,
) -> ContinuityMessagesEnvelope | None:
    """Load seal JSON for a journal row when ``verbatim_codec`` is messages-v1."""
    file_path = row["file_path"] if row is not None else None
    if not file_path:
        return None
    codec = None
    try:
        codec = row["verbatim_codec"]
    except (KeyError, TypeError, IndexError):
        pass
    if codec != "messages-v1":
        return None
    rel = str(file_path)
    name = Path(rel).name
    if name.endswith(".md"):
        name = name[:-3] + ".messages.json"
    seal_path = files_root / f"notes/system/seals/{name}"
    if not seal_path.is_file():
        return None
    return load_sealed_envelope_from_path(seal_path)


def sealed_turn_count(row: dict[str, Any] | Any) -> int:
    """Turn count from sealed row metadata or envelope."""
    try:
        codec = row["verbatim_codec"]
    except (KeyError, TypeError, IndexError):
        codec = None
    if codec == "messages-v1":
        try:
            return int(row.get("turn_count") or 0)
        except (TypeError, ValueError):
            pass
    return 0


def _normalize_md_content(role: str, content: str) -> tuple[str | None, int, int]:
    """Map md sentinels to null; count markers and user-side hits."""
    marker_count = 0
    user_marker_hits = 0
    stripped = content.strip()
    if role == "user":
        if stripped == _EMPTY_USER:
            return None, marker_count, user_marker_hits
        for paragraph in content.split("\n\n"):
            if TOOL_MARKER_RE.fullmatch(paragraph.strip()):
                user_marker_hits += 1
        return content.strip() or None, marker_count, user_marker_hits
    if role == "assistant":
        if stripped == _EMPTY_ASSISTANT:
            return None, marker_count, user_marker_hits
        for paragraph in content.split("\n\n"):
            if TOOL_MARKER_RE.fullmatch(paragraph.strip()):
                marker_count += 1
        return content.strip() or None, marker_count, user_marker_hits
    return content.strip() or None, marker_count, user_marker_hits


def parse_verbatim_md(
    verbatim: str,
    *,
    seg: Mapping[str, Any],
    session_id: str,
) -> list[dict[str, Any]]:
    """Parse ``### User`` / ``### Assistant`` blocks into speech messages (R8/R3)."""
    turn_lo = int(seg.get("turn_lo") or 0)
    turn_hi = seg.get("turn_hi")
    turn_hi_int = int(turn_hi) if turn_hi is not None else None
    transcript_id = str(seg["transcript_id"])
    window_whole = seg.get("boundary") == "window_whole"

    messages: list[dict[str, Any]] = []
    current_turn = 0
    current_role: str | None = None
    body_lines: list[str] = []
    sentinel_count = 0
    marker_count = 0
    user_marker_hits = 0

    def flush() -> None:
        nonlocal body_lines, current_role, sentinel_count, marker_count, user_marker_hits
        if current_role is None:
            body_lines = []
            return
        if turn_hi_int is not None:
            if current_turn <= turn_lo or current_turn > turn_hi_int:
                body_lines = []
                current_role = None
                return
        raw = "\n".join(body_lines).strip()
        if raw:
            if current_role == "user" and raw.strip() == _EMPTY_USER:
                sentinel_count += 1
            elif current_role == "assistant" and raw.strip() == _EMPTY_ASSISTANT:
                sentinel_count += 1
            content, m_count, u_hits = _normalize_md_content(current_role, raw)
            marker_count += m_count
            user_marker_hits += u_hits
            messages.append(
                {
                    "role": current_role,
                    "content": content,
                    "transcript_id": transcript_id,
                    "session_id": session_id,
                    "turn_index": current_turn,
                    "window_whole": window_whole,
                    "source": _SOURCE_MD,
                }
            )
        body_lines = []
        current_role = None

    for line in verbatim.splitlines():
        turn_match = _TURN_HEADING_RE.match(line)
        if turn_match:
            flush()
            current_turn = int(turn_match.group(1))
            continue
        if line.startswith("### User"):
            flush()
            current_role = "user"
            continue
        if line.startswith("### "):
            flush()
            current_role = "assistant"
            continue
        if current_role is not None:
            body_lines.append(line)
    flush()

    from cortex_store.events_tape import transcript_legacy_md_read

    transcript_legacy_md_read(
        session_id=session_id,
        sentinel_count=sentinel_count,
        marker_count=marker_count,
        user_marker_hits=user_marker_hits,
    )
    return messages


def messages_from_sealed_row(
    journal: Mapping[str, Any],
    *,
    seg: Mapping[str, Any],
    session_id: str,
    files_root: Path,
    verbatim: str | None = None,
) -> list[dict[str, Any]]:
    """Load speech for a journal row (messages-v1 envelope or md-v1 parse)."""
    codec = journal.get("verbatim_codec") or "md-v1"
    if codec == "messages-v1":
        envelope = load_sealed_envelope(journal, files_root=files_root)
        if envelope is not None:
            return _messages_from_envelope(envelope.messages, seg=seg, session_id=session_id)
    if verbatim is None:
        return []
    return parse_verbatim_md(verbatim, seg=seg, session_id=session_id)


def _messages_from_envelope(
    raw_messages: Sequence[Mapping[str, Any]],
    *,
    seg: Mapping[str, Any],
    session_id: str,
) -> list[dict[str, Any]]:
    turn_lo = int(seg.get("turn_lo") or 0)
    turn_hi = seg.get("turn_hi")
    turn_hi_int = int(turn_hi) if turn_hi is not None else None
    transcript_id = str(seg["transcript_id"])
    window_whole = seg.get("boundary") == "window_whole"
    out: list[dict[str, Any]] = []
    for msg in raw_messages:
        turn_index = int(msg.get("turn_index") or 0)
        if turn_hi_int is not None and (turn_index <= turn_lo or turn_index > turn_hi_int):
            continue
        out.append(
            {
                "role": msg.get("role"),
                "content": msg.get("content"),
                "transcript_id": transcript_id,
                "session_id": session_id,
                "turn_index": turn_index,
                "window_whole": window_whole,
                "source": msg.get("source") or "cursor-seal-messages",
            }
        )
    return out


__all__ = [
    "load_sealed_envelope",
    "load_sealed_envelope_from_path",
    "messages_from_sealed_row",
    "parse_verbatim_md",
    "sealed_turn_count",
]
