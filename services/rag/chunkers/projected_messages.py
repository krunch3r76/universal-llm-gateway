"""Sniff messages-format JSON and chunk ``project_messages`` markdown.

``.json`` files are classified by a bounded read. Transcripts go through
``message_projection.project_messages`` and are split on ``## Turn N - ``
headings. Tape, raw staging, fine-tune fixtures, and unstructured session
renders return no chunks so the indexer can drop prior rows for that path.
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
_TURN_HEADING = re.compile(r"^## Turn (\d+) - .*$")
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
    return path.name.lower().endswith(".raw.json")


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
    try:
        prefix = path.read_bytes()[: min(size, SNIFF_BYTES)]
        prefix.decode("utf-8")
    except (OSError, UnicodeError):
        return _skip(path, "parse_error")
    stripped = prefix.lstrip()
    if not stripped.startswith((b"{", b"[")):
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
    projected = project_messages(messages, meta)
    chunks = _chunk_projected(path, projected)
    if chunks is None:
        return _skip(path, "oversize_unsplittable")
    return _annotate_chunk_indices(chunks)


def _chunk_projected(path: Path, markdown: str) -> list[Chunk] | None:
    sections = _accepted_turns(markdown)
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


def _accepted_turns(markdown: str) -> list[tuple[str, str]]:
    """Keep a ``## Turn N - `` heading only when ``N`` is the next expected number."""
    sections: list[tuple[str, list[str]]] = []
    expected: int | None = None
    heading = ""
    body: list[str] = []
    for line in markdown.split("\n"):
        match = _TURN_HEADING.match(line)
        if match:
            number = int(match.group(1))
            if expected is None or number == expected:
                if heading or body:
                    sections.append((heading, body))
                heading = line
                body = []
                expected = number + 1
                continue
        body.append(line)
    if heading or body:
        sections.append((heading, body))
    return [(head, "\n".join(lines).strip("\n")) for head, lines in sections]


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

    A cut that lands inside a fence closes the fence on the current piece
    and reopens it on the next piece.
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
            text = f"{text}\n```"
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
        if not _split_paragraph(para, budget, pieces, fence):
            return None
        fence = _fence_after(pieces[-1].split("\n")) if pieces else fence
        buf = [fence] if fence else []
    emit()
    balanced: list[str] = []
    for piece in pieces:
        if (
            piece.strip() in {"", "```"}
            or piece.strip().startswith("```")
            and "\n" not in piece.strip()
        ):
            continue
        if piece.count("```") % 2 == 1:
            piece = f"{piece}\n```"
        balanced.append(piece)
    return balanced


def _fits(buf: list[str], para: str, fence: str | None, budget: int) -> bool:
    trial = buf + [para]
    text = "\n\n".join(trial)
    if _fence_after(trial) is not None:
        text = f"{text}\n```"
    return len(text.encode("utf-8")) <= budget


def _fence_after(blocks: list[str]) -> str | None:
    opener: str | None = None
    for block in blocks:
        for line in block.split("\n"):
            if not line.startswith("```"):
                continue
            if opener is None:
                opener = line
            elif line.strip() == "```":
                opener = None
    return opener


def _split_paragraph(
    para: str, budget: int, pieces: list[str], fence: str | None
) -> bool:
    lines = para.split("\n")
    buf: list[str] = [fence] if fence else []
    local_fence = fence

    def emit_lines() -> None:
        nonlocal buf, local_fence
        if not buf:
            return
        text = "\n".join(buf)
        reopen = local_fence
        if local_fence is not None and not text.rstrip().endswith("```"):
            text = f"{text}\n```"
        pieces.append(text)
        buf = []
        local_fence = None
        if reopen is not None:
            buf = [reopen]
            local_fence = reopen

    for line in lines:
        trial = "\n".join(buf + [line])
        close = "\n```" if _line_opens(buf + [line]) else ""
        if len((trial + close).encode("utf-8")) <= budget:
            buf.append(line)
            local_fence = _fence_after(buf)
            continue
        emit_lines()
        trial = "\n".join(buf + [line])
        close = "\n```" if _line_opens(buf + [line]) else ""
        if len((trial + close).encode("utf-8")) <= budget:
            buf.append(line)
            local_fence = _fence_after(buf)
            continue
        if not _hard_cut_line(line, budget, pieces, local_fence):
            return False
        local_fence = _fence_after([pieces[-1]]) if pieces else local_fence
        buf = [local_fence] if local_fence else []
    emit_lines()
    return True


def _line_opens(lines: list[str]) -> bool:
    return _fence_after(lines) is not None


def _hard_cut_line(
    line: str, budget: int, pieces: list[str], fence: str | None
) -> bool:
    prefix = f"{fence}\n" if fence else ""
    suffix = "\n```" if fence or line.startswith("```") else ""
    room = budget - len(prefix.encode("utf-8")) - len(suffix.encode("utf-8"))
    if room < 1:
        return False
    raw = line.encode("utf-8")
    first = True
    while raw:
        head, raw = _utf8_cut(raw, room)
        if not head:
            return False
        text = prefix + head.decode("utf-8")
        if fence is not None or (first and line.startswith("```")):
            text = f"{text}\n```"
        if len(text.encode("utf-8")) > budget:
            return False
        pieces.append(text)
        first = False
        if fence is None and line.startswith("```"):
            fence = line.split("\n", 1)[0]
            prefix = f"{fence}\n"
            suffix = "\n```"
            room = budget - len(prefix.encode("utf-8")) - len(suffix.encode("utf-8"))
            if room < 1:
                return False
    return True
