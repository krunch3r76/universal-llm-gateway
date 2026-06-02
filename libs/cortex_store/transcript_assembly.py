"""Server-side transcript assembly — JSONL → verbatim markdown + composition.

Pure functions used by `_op_session_close` (atomic close path) and
`_op_assemble_transcript` (debug/probe path). The two callers share one
implementation so the format on disk is identical regardless of entry point.

Layering:
  * Verbatim layer (`_assemble_verbatim_md`) — derived deterministically from
    a Cursor agent-transcripts JSONL.  H1 + per-turn `## Turn N` + `### User`
    / `### {assistant_label}` blocks.  Tool-call args are collapsed to
    `[tool call: NAME]` markers per `session-transcript-fidelity.mdc`.
  * Structural layer (`session_summary_md`) — agent-composed `## Session
    Summary` block carrying decisions, files modified, continuation state,
    debrief.  Server appends it verbatim, never derives it.
  * Composition (`compose_full_transcript`) — concatenate verbatim + a single
    blank line + structural.  Returns the on-disk markdown string.

Security:
  * `resolve_jsonl_path` validates that the supplied path resolves inside
    `CURSOR_AGENT_TRANSCRIPTS_ROOT` (defaults to the Cursor IDE's
    workspace-specific transcript directory).  Rejects `..` traversal and
    symlinks that escape the root.  cortex-api runs as a host process with
    user `io`'s full filesystem read access — without this gate the path
    argument would be an arbitrary-file-read primitive.

Content hash:
  * `compute_text_content_hash` returns `sha256:<hex>` of the composed
    transcript markdown.  Reported back to the agent in the session_close
    201 response; the agent quotes it instead of re-reading the file (per
    `provenance-discipline.mdc` rule 2 — response-payload evidence).
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from universal_logging import get_logger

logger = get_logger("cortex-api.transcript_assembly")

_DEFAULT_ASSISTANT_LABEL = "Assistant"
_TURN_TOPIC_MAX = 60


def _default_transcripts_root() -> Path:
    """Default Cursor agent-transcripts root for this workspace.

    The path is workspace-specific (`mnt-torus-projects-universal-llm-gateway`
    is the Cursor IDE's slug for the current project root).  Operators can
    override via ``CURSOR_AGENT_TRANSCRIPTS_ROOT`` when running cortex-api
    against a different workspace.
    """
    return (
        Path.home()
        / ".cursor"
        / "projects"
        / "mnt-torus-projects-universal-llm-gateway"
        / "agent-transcripts"
    )


def _transcripts_root() -> Path:
    """Resolve the configured agent-transcripts root (env override or default)."""
    override = os.environ.get("CURSOR_AGENT_TRANSCRIPTS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return _default_transcripts_root().resolve()


class TranscriptPathError(ValueError):
    """Raised when ``transcript_jsonl_path`` fails the sandbox/existence gate."""


def resolve_jsonl_path(candidate: str) -> Path:
    """Resolve *candidate* to a real file under ``CURSOR_AGENT_TRANSCRIPTS_ROOT``.

    Rules:
      * candidate may be absolute or relative; relative resolves against the
        configured root.
      * The fully resolved realpath MUST be under the root (`..` traversal
        and escaping symlinks are rejected).
      * The resolved target MUST be an existing regular file.

    Raises:
      TranscriptPathError: with a structured message naming the gate that
        failed (`missing`, `not_file`, `escapes_root`).  Callers map this to
        a 4xx response without leaking the resolved path beyond what the
        agent already supplied.
    """
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


def _extract_user_text(content: list[dict[str, Any]]) -> str:
    """Pull the human-authored text out of a Cursor user message.

    Cursor wraps each user turn into a single ``content`` array that may
    contain (a) the typed message as a ``text`` block and (b) one or more
    ``tool_result`` blocks carrying responses from the prior assistant tool
    calls.  Only the ``text`` blocks are user voice; tool results belong to
    the prior assistant turn and are dropped to keep the verbatim layer
    clean.
    """
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = block.get("text", "")
            if isinstance(text, str) and text.strip():
                parts.append(text)
    return "\n\n".join(parts).strip()


def _extract_assistant_blocks(content: list[dict[str, Any]]) -> str:
    """Pull text + tool-call summaries out of an assistant message.

    Tool input JSON is intentionally NOT inlined verbatim — the doctrine
    says ``trim tool JSON only``.  We emit ``[tool call: NAME]`` markers so
    the structural shape of the assistant's reasoning is preserved without
    flooding the transcript with potentially-large tool arguments.
    """
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text", "")
            if isinstance(text, str) and text.strip():
                parts.append(text)
        elif btype == "tool_use":
            name = block.get("name", "<unknown>")
            parts.append(f"[tool call: {name}]")
    return "\n\n".join(parts).strip()


def _topic_hint(user_text: str) -> str:
    """Best-effort one-line topic for ``## Turn N — <topic>``."""
    flat = " ".join(user_text.split())
    if len(flat) <= _TURN_TOPIC_MAX:
        return flat or "(no user text)"
    return flat[:_TURN_TOPIC_MAX].rstrip() + "…"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"jsonl_path {path} line {line_no}: invalid JSON ({exc})"
                ) from exc
    return records


def _walk_turns(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Walk JSONL records into a list of {user, assistant} turn dicts.

    Cursor often emits a `user` record carrying only `tool_result` blocks
    after the assistant has made tool calls — those records are *not* a new
    user turn and must not open an empty ``### User`` block.  Such records
    are merged into the in-progress turn.
    """
    turns: list[dict[str, str]] = []
    current: dict[str, list[str]] | None = None
    for record in records:
        role = record.get("role")
        message = record.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            continue
        if role == "user":
            user_text = _extract_user_text(content)
            if not user_text:
                continue
            if current is not None:
                turns.append(
                    {
                        "user": "\n\n".join(current["user"]).strip(),
                        "assistant": "\n\n".join(current["assistant"]).strip(),
                    }
                )
            current = {"user": [user_text], "assistant": []}
        elif role == "assistant":
            if current is None:
                current = {"user": ["(no user message)"], "assistant": []}
            asst = _extract_assistant_blocks(content)
            if asst:
                current["assistant"].append(asst)
    if current is not None:
        turns.append(
            {
                "user": "\n\n".join(current["user"]).strip(),
                "assistant": "\n\n".join(current["assistant"]).strip(),
            }
        )
    return turns


def assemble_verbatim_md(
    *,
    jsonl_path: Path,
    session_id: str,
    assistant_label: str | None = None,
) -> tuple[str, int]:
    """Build the verbatim layer from a Cursor JSONL.

    Args:
      jsonl_path: real, validated path under the agent-transcripts root.
      session_id: full ``{agent}-YYYY-MM-DD-HHMM`` ID used in the H1 line.
      assistant_label: heading label for assistant blocks; defaults to
        ``"Assistant"``.

    Returns:
      (verbatim_md, turn_count).  verbatim_md ends with a single trailing
      newline so the structural layer slots in cleanly.
    """
    records = _read_jsonl(jsonl_path)
    turns = _walk_turns(records)
    label = assistant_label or _DEFAULT_ASSISTANT_LABEL
    lines: list[str] = [f"# Transcript: {session_id}", ""]
    for idx, turn in enumerate(turns, start=1):
        topic = _topic_hint(turn["user"])
        lines.append(f"## Turn {idx} — {topic}")
        lines.append("")
        lines.append("### User")
        lines.append("")
        lines.append(turn["user"] or "(empty)")
        lines.append("")
        lines.append(f"### {label}")
        lines.append("")
        lines.append(turn["assistant"] or "(no assistant output)")
        lines.append("")
    return "\n".join(lines), len(turns)


def compose_full_transcript(verbatim_md: str, session_summary_md: str) -> str:
    """Concatenate the verbatim and structural layers.

    Caller guarantees ``session_summary_md`` starts with a ``## Session
    Summary`` heading per the contract in `session-close.mdc`.  We do not
    rewrite the structural layer — the agent owns its content (continues
    pointer, decisions narrative, files-modified list).
    """
    base = verbatim_md if verbatim_md.endswith("\n") else verbatim_md + "\n"
    return base + session_summary_md.rstrip("\n") + "\n"


def derive_session_id_from_jsonl_start(*, jsonl_path: Path, agent: str) -> str:
    """Derive ``{agent}-YYYY-MM-DD-HHMM`` from when the JSONL file was created.

    Cursor agent-transcripts files are created at session start; their birth
    time (Linux ``st_birthtime``, else ``st_mtime``) is the canonical proxy when
    the agent did not hold a ``cortex_boot`` ``session_id``.  Minute resolution
    matches web-claude and ``_parse_opened_at`` on close.
    """
    st = jsonl_path.stat()
    started = getattr(st, "st_birthtime", None)
    if started is None or started <= 0:
        started = st.st_mtime
    dt = datetime.fromtimestamp(started, tz=UTC)
    return f"{agent}-{dt.strftime('%Y-%m-%d-%H%M')}"


def session_id_timing_hint(
    *,
    session_id: str,
    jsonl_path: Path,
    agent: str,
) -> str | None:
    """Advisory when ``session_id`` looks like close-time, not JSONL start."""
    from_jsonl = derive_session_id_from_jsonl_start(jsonl_path=jsonl_path, agent=agent)
    if session_id == from_jsonl:
        return None
    return (
        f"session_id {session_id!r} differs from JSONL session-start "
        f"{from_jsonl!r}; use the boot-held session_id or the JSONL-start ID "
        "(web-claude convention), not UTC wall clock at close."
    )


def compute_text_content_hash(text: str) -> str:
    """Return ``sha256:<hex>`` of the UTF-8 bytes of *text*.

    Used as the response-payload evidence the agent quotes back to the
    user.  Length is fixed (`sha256:` + 64 hex chars = 71 chars), so it
    cannot blow up the response size.
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


__all__ = [
    "TranscriptPathError",
    "assemble_verbatim_md",
    "compose_full_transcript",
    "compute_text_content_hash",
    "derive_session_id_from_jsonl_start",
    "resolve_jsonl_path",
    "session_id_timing_hint",
]
