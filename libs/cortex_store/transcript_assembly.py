"""Server-side transcript assembly — path resolution, composition, content hash.

Pure functions used by `_op_session_close` (atomic close path) and
`_op_assemble_transcript` (debug/probe path). Verbatim extraction and
rendering live in ``libs/continuity_tape/{extract_jsonl,render_md}.py``.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from universal_logging import get_logger

from cortex_store.transcript_session_id import (
    derive_prior_session_id_from_jsonl_path,
    derive_session_id_from_jsonl_start,
    session_id_timing_hint,
)

logger = get_logger("cortex-api.transcript_assembly")

_TURN_TOPIC_MAX = 60

TURN_HEADING_RE = re.compile(r"^## Turn (\d+) — .+$")


@dataclass(frozen=True)
class TranscriptGrammarError:
    """Turn-heading grammar failure on a verbatim transcript body."""

    reason: str
    detail: str
    line_no: int | None = None


def format_turn_heading(idx: int, topic: str) -> str:
    """Emit a canonical ``## Turn {idx} — {topic}`` heading."""
    return f"## Turn {idx} — {topic}"


def count_canonical_turn_headings(verbatim_md: str) -> int:
    """Count lines matching assembly turn-heading grammar."""
    return sum(
        1 for line in verbatim_md.splitlines() if TURN_HEADING_RE.match(line)
    )


def validate_transcript_turn_grammar(verbatim_md: str) -> TranscriptGrammarError | None:
    """Validate verbatim turn headings match assembly grammar and sequence."""
    indices: list[int] = []
    for line_no, line in enumerate(verbatim_md.splitlines(), start=1):
        if not line.startswith("## Turn"):
            continue
        if not TURN_HEADING_RE.match(line):
            return TranscriptGrammarError(
                reason="transcript.grammar_invalid",
                detail=(
                    f"line {line_no}: turn heading must match "
                    f"'## Turn {{N}} — {{topic}}' (assembly grammar); got {line!r}"
                ),
                line_no=line_no,
            )
        indices.append(int(TURN_HEADING_RE.match(line).group(1)))  # type: ignore[union-attr]
    if not indices:
        return None
    expected = list(range(1, len(indices) + 1))
    if sorted(indices) != expected:
        return TranscriptGrammarError(
            reason="transcript.grammar_invalid",
            detail=(
                f"turn indices must be sequential 1..{len(indices)} without "
                f"gaps or duplicates; found {indices}"
            ),
        )
    if len(set(indices)) != len(indices):
        return TranscriptGrammarError(
            reason="transcript.grammar_invalid",
            detail=f"duplicate turn indices in verbatim layer: {indices}",
        )
    return None


def _default_transcripts_root() -> Path:
    return (
        Path.home()
        / ".cursor"
        / "projects"
        / "mnt-torus-projects-universal-llm-gateway"
        / "agent-transcripts"
    )


def _transcripts_root() -> Path:
    override = os.environ.get("CURSOR_AGENT_TRANSCRIPTS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return _default_transcripts_root().resolve()


class TranscriptPathError(ValueError):
    """Raised when ``transcript_jsonl_path`` fails the sandbox/existence gate."""


def resolve_jsonl_path(candidate: str) -> Path:
    """Resolve *candidate* to a real file under ``CURSOR_AGENT_TRANSCRIPTS_ROOT``."""
    if not candidate:
        raise TranscriptPathError("transcript_jsonl_path is required")
    root = _transcripts_root()
    raw = Path(candidate).expanduser()
    resolved = (raw if raw.is_absolute() else root / raw).resolve()
    root_str = str(root) + os.sep
    if not (str(resolved) + os.sep).startswith(root_str):
        raise TranscriptPathError(
            f"transcript_jsonl_path {candidate!r} resolves to {resolved} — "
            f"outside CURSOR_AGENT_TRANSCRIPTS_ROOT ({root})"
        )
    if not resolved.exists():
        raise TranscriptPathError(
            f"transcript_jsonl_path {candidate!r} not found at {resolved}"
        )
    if not resolved.is_file():
        raise TranscriptPathError(
            f"transcript_jsonl_path {candidate!r} is not a regular file"
        )
    return resolved


def compose_full_transcript(verbatim_md: str, session_summary_md: str) -> str:
    """Concatenate the verbatim and structural layers."""
    base = verbatim_md if verbatim_md.endswith("\n") else verbatim_md + "\n"
    return base + session_summary_md.rstrip("\n") + "\n"


def compute_text_content_hash(text: str) -> str:
    """Return ``sha256:<hex>`` of the UTF-8 bytes of *text*."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


__all__ = [
    "TranscriptGrammarError",
    "TranscriptPathError",
    "TURN_HEADING_RE",
    "compose_full_transcript",
    "compute_text_content_hash",
    "count_canonical_turn_headings",
    "derive_prior_session_id_from_jsonl_path",
    "derive_session_id_from_jsonl_start",
    "format_turn_heading",
    "resolve_jsonl_path",
    "session_id_timing_hint",
    "validate_transcript_turn_grammar",
]
