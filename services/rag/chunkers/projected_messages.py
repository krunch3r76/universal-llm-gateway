"""Sniff messages-format JSON and chunk ``project_messages`` markdown.

``.json`` files are classified by a bounded read. Transcripts go through
``message_projection.project_messages`` one turn at a time, so a heading is
only a boundary the projector emitted. Tape, raw staging, fine-tune fixtures,
and unstructured session renders return no chunks so the indexer can drop
prior rows for that path.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from message_projection.project import project_messages

from services.rag.chunkers._sizing import CHUNK_HARD_CAP_BYTES
from services.rag.chunkers.paragraph_utils import (
    Chunk,
    _annotate_chunk_indices,
    _utf8_cut,
)

logger = logging.getLogger(__name__)

KNOWN_ROLES = frozenset({"user", "assistant", "system", "tool"})
SKIP_REASONS = frozenset(
    {
        "parse_error",
        "generic_messages_shape",
        "tape",
        "raw",
        "raw_render",
        "oversize_unsplittable",
        "unsupported_shape",
    }
)
SEAL_SCHEMA = "ulg.continuity.messages/1"
SNIFF_BYTES = 65_536
MAX_JSON_BYTES = 64 * 1024 * 1024
_PART_RESERVE = 24
_TAPE_KEYS = ("cells", "open_line", "index")

_seen_skips: set[tuple[str, int, str]] = set()
_skip_counts: dict[str, int] = {}


class UseSealChunker:
    """Sealed ``*.messages.json`` still belongs to ``chunk_messages``."""


def reset_skip_log() -> None:
    """Clear dedupe state. Tests use this so cases do not share a process log."""
    _seen_skips.clear()
    _skip_counts.clear()


def skip_counts() -> dict[str, int]:
    """Return per-reason skip counts for the current process run."""
    return dict(_skip_counts)


def emit_skip_summary() -> dict[str, int]:
    """Log one summary line of skip counts and return the same mapping."""
    counts = skip_counts()
    parts = [f"{reason}={counts.get(reason, 0)}" for reason in sorted(SKIP_REASONS)]
    logger.info("rag_chunk_skip_summary %s", " ".join(parts))
    return counts


def log_skip(path: Path, mtime_ns: int, reason: str) -> None:
    """Log one skip line per ``(path, mtime, reason)`` and count every call."""
    if reason not in SKIP_REASONS:
        raise ValueError(f"unknown skip reason: {reason}")
    _skip_counts[reason] = _skip_counts.get(reason, 0) + 1
    key = (str(path), mtime_ns, reason)
    if key in _seen_skips:
        return
    _seen_skips.add(key)
    logger.info("rag_chunk_skip path=%s reason=%s", path, reason)


def _mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _decode_sniff_prefix(raw: bytes) -> None:
    """Accept UTF-8 sniff bytes; ignore an incomplete code point at the tail."""
    for trim in range(min(4, len(raw) + 1)):
        try:
            raw[: len(raw) - trim].decode("utf-8")
            return
        except UnicodeDecodeError:
            continue


def _skip(path: Path, reason: str) -> list[Chunk]:
    log_skip(path, _mtime_ns(path), reason)
    return []


def transcript_render_path(path: Path) -> bool:
    """True when ``path`` sits under ``notes/system/transcripts``."""
    parts = [part.lower() for part in path.parts]
    for index in range(len(parts) - 2):
        if (
            parts[index] == "notes"
            and parts[index + 1] == "system"
            and parts[index + 2] == "transcripts"
        ):
            return True
    return False


def unstructured_session_render(path: Path, text: str) -> bool:
    """Session-close markdown with no ``## Turn`` structure."""
    if path.suffix.lower() not in {".md", ".mdc"} or not transcript_render_path(path):
        return False
    return re.search(r"^## Turn \d+", text, re.MULTILINE) is None


def _is_raw_name(path: Path) -> bool:
    """True when a ``.json`` name has a ``raw`` segment before the suffix.

    ``snap.raw.json``, ``h.raw.messages.json``, and ``x.raw.staging.json``
    match. A seal named ``session.messages.json`` does not.
    """
    name = path.name.lower()
    if not name.endswith(".json"):
        return False
    return "raw" in name.split(".")[:-1]


def _is_raw_meta(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    meta = data.get("meta")
    if not isinstance(meta, dict) or meta.get("provenance") != "raw":
        return False
    return not meta.get("sealed_at") and not meta.get("handoff")


def _is_seal(path: Path, data: object) -> bool:
    if not path.name.lower().endswith(".messages.json"):
        return False
    if not isinstance(data, dict):
        return False
    return data.get("schema") == SEAL_SCHEMA and not _is_raw_meta(data)


def _is_tape(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    if isinstance(data.get("messages"), list):
        return False
    return any(key in data for key in _TAPE_KEYS)


def _message_rows(data: object) -> tuple[list[object] | None, dict]:
    if isinstance(data, dict) and isinstance(data.get("messages"), list):
        meta = data.get("meta")
        return data["messages"], meta if isinstance(meta, dict) else {}
    if isinstance(data, list):
        return data, {}
    return None, {}


def _is_fine_tune_dataset(data: object) -> bool:
    if not isinstance(data, list) or not data:
        return False
    return all(isinstance(row, dict) and "messages" in row for row in data)


def _transcript_rows(rows: list[object]) -> str | None:
    """Return ``ok``, ``generic``, or ``unsupported`` for a candidate row list."""
    if not rows or not all(isinstance(row, dict) and "role" in row for row in rows):
        return "unsupported"
    roles = [str(row.get("role")) for row in rows if isinstance(row, dict)]
    if any(role not in KNOWN_ROLES for role in roles):
        return "generic"
    if "user" in roles and "assistant" in roles:
        return "ok"
    return "generic"


def chunk_json_file(path: Path) -> list[Chunk] | UseSealChunker:
    """Classify one JSON file and chunk it, skip it, or defer to the seal chunker.

    Size is checked before the read. The sniff prefix decides whether the
    rest of a large file is worth parsing. Skip results are empty chunk
    lists so ``_handle_empty_chunks`` can drop that path's prior rows.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return _skip(path, "parse_error")
    if size <= 0 or size > MAX_JSON_BYTES:
        return _skip(path, "parse_error" if size <= 0 else "oversize_unsplittable")
    if _is_raw_name(path):
        return _skip(path, "raw")
    seal_name = path.name.lower().endswith(".messages.json")
    try:
        with path.open("rb") as fh:
            prefix = fh.read(min(size, SNIFF_BYTES))
    except OSError:
        return _skip(path, "parse_error")
    _decode_sniff_prefix(prefix)
    stripped = prefix.lstrip()
    if not seal_name and not stripped.startswith((b"{", b"[")):
        return _skip(path, "unsupported_shape")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _skip(path, "parse_error")
    if _is_seal(path, data):
        return UseSealChunker()
    if _is_raw_meta(data):
        return _skip(path, "raw")
    if _is_tape(data):
        return _skip(path, "tape")
    if _is_fine_tune_dataset(data):
        return _skip(path, "generic_messages_shape")
    rows, meta = _message_rows(data)
    if rows is None:
        return _skip(path, "unsupported_shape")
    kind = _transcript_rows(rows)
    if kind == "generic":
        return _skip(path, "generic_messages_shape")
    if kind != "ok":
        return _skip(path, "unsupported_shape")
    messages = [row for row in rows if isinstance(row, dict)]
    sections = _projected_sections(messages, meta)
    chunks = _chunk_projected(path, sections)
    if chunks is None:
        return _skip(path, "oversize_unsplittable")
    return _annotate_chunk_indices(chunks)


def _chunk_projected(path: Path, sections: list[tuple[str, str]]) -> list[Chunk] | None:
    chunks: list[Chunk] = []
    for heading, body in sections:
        parts = _split_turn(heading, body)
        if parts is None:
            return None
        for part in parts:
            if len(part.encode("utf-8")) > CHUNK_HARD_CAP_BYTES:
                return None
            chunks.append(
                Chunk(
                    text=part,
                    metadata={"source": str(path), "heading": heading},
                )
            )
    return chunks


def _projected_sections(messages: list[dict], meta: dict) -> list[tuple[str, str]]:
    """Return the heading and body of each turn ``project_messages`` emits.

    Each row is projected by itself. A line inside a message body cannot
    become a turn boundary, including a quoted ``## Turn N - `` line whose
    number is the next integer.
    """
    sections: list[tuple[str, str]] = []
    observed = meta if isinstance(meta, dict) else {}
    for index, raw in enumerate(messages, start=1):
        if not isinstance(raw, dict):
            continue
        row = raw
        if not isinstance(raw.get("turn_index"), int):
            row = dict(raw)
            row["turn_index"] = index
        rendered = project_messages([row], observed)
        if not rendered:
            continue
        heading, sep, body = rendered.partition("\n\n")
        sections.append((heading, body if sep else ""))
    return sections


def _split_turn(heading: str, body: str) -> list[str] | None:
    budget = CHUNK_HARD_CAP_BYTES - len(heading.encode("utf-8")) - 2 - _PART_RESERVE
    if budget < 1:
        return None
    pieces = _split_fenced(body, budget)
    if pieces is None:
        return None
    total = len(pieces)
    rendered: list[str] = []
    for index, piece in enumerate(pieces, start=1):
        head = heading if total == 1 else f"{heading} (part {index}/{total})"
        text = f"{head}\n\n{piece}" if piece else head
        if len(text.encode("utf-8")) > CHUNK_HARD_CAP_BYTES:
            return None
        rendered.append(text)
    return rendered


def _split_fenced(body: str, budget: int) -> list[str] | None:
    """Split a turn body on paragraphs, then lines, then a UTF-8 hard cut.

    A cut that lands inside a fence closes that fence with the opener's
    backtick length and reopens the same opener, info string included.
    """
    if not body:
        return [""]
    paragraphs = re.split(r"\n\n+", body)
    pieces: list[str] = []
    buf: list[str] = []
    fence: str | None = None

    def emit() -> None:
        nonlocal buf, fence
        if not buf:
            return
        text = "\n\n".join(buf)
        reopen = fence
        if fence is not None:
            text = f"{text}\n{_close_for(fence)}"
        pieces.append(text)
        buf = []
        fence = None
        if reopen is not None:
            buf.append(reopen)
            fence = reopen

    for para in paragraphs:
        if _fits(buf, para, fence, budget):
            buf.append(para)
            fence = _fence_after(buf)
            continue
        emit()
        if _fits(buf, para, fence, budget):
            buf.append(para)
            fence = _fence_after(buf)
            continue
        ok, fence = _split_paragraph(para, budget, pieces, fence)
        if not ok:
            return None
        buf = [fence] if fence else []
    emit()
    balanced: list[str] = []
    for piece in pieces:
        stripped = piece.strip()
        if stripped == "" or (
            _parse_fence_line(stripped) is not None and "\n" not in stripped
        ):
            continue
        if not _piece_has_payload(piece):
            continue
        opener = _fence_after([piece])
        if opener is not None:
            piece = f"{piece}\n{_close_for(opener)}"
        balanced.append(piece)
    return balanced


def _piece_has_payload(piece: str) -> bool:
    """True when a piece contains non-structural, non-whitespace text."""
    for line in piece.split("\n"):
        parsed = _parse_fence_line(line)
        if parsed is not None and parsed[1].strip() == "":
            continue
        if line.strip():
            return True
    return False


def _fits(buf: list[str], para: str, _fence: str | None, budget: int) -> bool:
    trial = buf + [para]
    text = "\n\n".join(trial)
    opener = _fence_after(trial)
    if opener is not None:
        text = f"{text}\n{_close_for(opener)}"
    return len(text.encode("utf-8")) <= budget


def _parse_fence_line(line: str) -> tuple[int, str] | None:
    """Return backtick run length and info string for a fence line."""
    if not line.startswith("```"):
        return None
    length = 0
    for char in line:
        if char != "`":
            break
        length += 1
    if length < 3:
        return None
    return length, line[length:]


def _close_for(opener: str) -> str:
    """Closing fence using the opener's backtick length and no info string."""
    parsed = _parse_fence_line(opener)
    if parsed is None:
        return "```"
    return "`" * parsed[0]


def _is_close(line: str, opener: str) -> bool:
    opened = _parse_fence_line(opener)
    parsed = _parse_fence_line(line)
    if opened is None or parsed is None:
        return False
    length, info = parsed
    return length == opened[0] and info.strip() == ""


def _fence_after(blocks: list[str]) -> str | None:
    """Return the open fence line, matching closes of that same run length."""
    opener: str | None = None
    for block in blocks:
        for line in block.split("\n"):
            if _parse_fence_line(line) is None:
                continue
            if opener is None:
                opener = line
            elif _is_close(line, opener):
                opener = None
    return opener


def _split_paragraph(
    para: str, budget: int, pieces: list[str], fence: str | None
) -> tuple[bool, str | None]:
    lines = para.split("\n")
    buf: list[str] = [fence] if fence else []
    local_fence = fence

    def emit_lines() -> None:
        nonlocal buf, local_fence
        if not buf:
            return
        text = "\n".join(buf)
        reopen = local_fence
        if local_fence is not None and not _ends_with_close(text, local_fence):
            text = f"{text}\n{_close_for(local_fence)}"
        pieces.append(text)
        buf = []
        local_fence = None
        if reopen is not None:
            buf = [reopen]
            local_fence = reopen

    for line in lines:
        if _line_fits(buf, line, budget):
            buf.append(line)
            local_fence = _fence_after(buf)
            continue
        emit_lines()
        if _line_fits(buf, line, budget):
            buf.append(line)
            local_fence = _fence_after(buf)
            continue
        carried = local_fence
        ok, open_fence = _hard_cut_line(line, budget, pieces, local_fence)
        if not ok:
            return False, None
        if carried is not None:
            local_fence = carried
            buf = [carried]
        else:
            local_fence = open_fence
            buf = [open_fence] if open_fence else []
    emit_lines()
    return True, local_fence


def _ends_with_close(text: str, opener: str) -> bool:
    last = text.rstrip("\n").split("\n")[-1]
    return _is_close(last, opener)


def _line_fits(buf: list[str], line: str, budget: int) -> bool:
    trial_lines = buf + [line]
    trial = "\n".join(trial_lines)
    opener = _fence_after(trial_lines)
    close = f"\n{_close_for(opener)}" if opener is not None else ""
    return len((trial + close).encode("utf-8")) <= budget


def _hard_cut_suffix(fence: str | None, line: str) -> str:
    if fence is not None:
        return "\n" + _close_for(fence)
    parsed = _parse_fence_line(line)
    if parsed is None:
        return ""
    return "\n" + ("`" * parsed[0])


def _hard_cut_line(
    line: str, budget: int, pieces: list[str], fence: str | None
) -> tuple[bool, str | None]:
    prefix = f"{fence}\n" if fence else ""
    suffix = _hard_cut_suffix(fence, line)
    room = budget - len(prefix.encode("utf-8")) - len(suffix.encode("utf-8"))
    if room < 1:
        return False, fence
    raw = line.encode("utf-8")
    first = True
    while raw:
        head, raw = _utf8_cut(raw, room)
        if not head:
            return False, fence
        text = prefix + head.decode("utf-8")
        if fence is not None or (first and _parse_fence_line(line) is not None):
            text = f"{text}{suffix}"
        if len(text.encode("utf-8")) > budget:
            return False, fence
        pieces.append(text)
        first = False
        if fence is None and _parse_fence_line(line) is not None:
            fence = line.split("\n", 1)[0]
            prefix = f"{fence}\n"
            suffix = "\n" + _close_for(fence)
            room = budget - len(prefix.encode("utf-8")) - len(suffix.encode("utf-8"))
            if room < 1:
                return False, fence
    return True, fence
