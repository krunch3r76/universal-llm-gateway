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
        # Body text can start with "## Turn …" (operator speech). Only canonical
        # assembly headings (`## Turn {N} — {topic}`) count; other ## Turn lines
        # are content, not grammar failures (10469 seal: `## Turn 2 specifically`).
        match = TURN_HEADING_RE.match(line)
        if match is None:
            continue
        indices.append(int(match.group(1)))
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


def _path_is_under(path: Path, root: Path) -> bool:
    root_str = str(root) + os.sep
    return (str(path) + os.sep).startswith(root_str)


def _safe_transcript_id(conversation_uuid: str) -> str | None:
    text = conversation_uuid.strip()
    if not text or text in {".", ".."} or "/" in text or "\\" in text:
        return None
    return text


def _cursor_projects_root(projects_root: Path | None = None) -> Path:
    if projects_root is not None:
        return projects_root.expanduser().resolve()
    return (Path.home() / ".cursor" / "projects").resolve()


def _is_cursor_project_transcript(path: Path, projects_root: Path | None = None) -> bool:
    """True for ``<project>/agent-transcripts/<uuid>/<uuid>.jsonl`` under Cursor projects."""
    if path.suffix != ".jsonl":
        return False
    transcript_id = path.parent.name
    if path.name != f"{transcript_id}.jsonl":
        return False
    if path.parent.parent.name != "agent-transcripts":
        return False
    try:
        path.relative_to(_cursor_projects_root(projects_root))
    except ValueError:
        return False
    return True


def locate_cursor_transcript_jsonl(
    conversation_uuid: str,
    *,
    projects_root: Path | None = None,
) -> tuple[Path | None, str | None]:
    """Find one Cursor JSONL for an explicit transcript id.

    The configured transcripts root is checked first. A miss searches every
    ``~/.cursor/projects/*/agent-transcripts/<id>/<id>.jsonl`` so a satellite
    workspace tab is not refused just because the default root is the gateway
    project. Returns ``(path, None)`` on one hit. Returns ``(None, code)``
    when nothing matches (``checkpoint.window_unresolvable``) or the same id
    exists in more than one project (``checkpoint.window_ambiguous``). Never
    selects the newest file. Checkpoint resolve is the caller.
    """
    transcript_id = _safe_transcript_id(conversation_uuid)
    if transcript_id is None:
        return None, "checkpoint.window_unresolvable"
    from cortex_store.transcript_session_id import jsonl_path_for_uuid

    primary = jsonl_path_for_uuid(_transcripts_root(), transcript_id)
    if primary.is_file():
        return primary, None
    projects = _cursor_projects_root(projects_root)
    if not projects.is_dir():
        return None, "checkpoint.window_unresolvable"
    hits = sorted(
        path
        for path in projects.glob(f"*/agent-transcripts/{transcript_id}/{transcript_id}.jsonl")
        if path.is_file() and path.resolve() != primary.resolve()
    )
    if not hits:
        return None, "checkpoint.window_unresolvable"
    if len(hits) > 1:
        return None, "checkpoint.window_ambiguous"
    return hits[0].resolve(), None


class TranscriptPathError(ValueError):
    """Raised when ``transcript_jsonl_path`` fails the sandbox/existence gate."""


def resolve_jsonl_path(candidate: str) -> Path:
    """Resolve *candidate* to a Cursor transcript JSONL the seal may read.

    Relative paths stay under the configured transcripts root. An absolute
    path may also be ``<project>/agent-transcripts/<uuid>/<uuid>.jsonl``
    under ``~/.cursor/projects``, which is how a satellite workspace window
    reaches seal after checkpoint resolve. Other paths are rejected.
    """
    if not candidate:
        raise TranscriptPathError("transcript_jsonl_path is required")
    root = _transcripts_root()
    raw = Path(candidate).expanduser()
    resolved = (raw if raw.is_absolute() else root / raw).resolve()
    allowed = _path_is_under(resolved, root) or _is_cursor_project_transcript(resolved)
    if not allowed:
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
    "locate_cursor_transcript_jsonl",
    "resolve_jsonl_path",
    "session_id_timing_hint",
    "validate_transcript_turn_grammar",
]
