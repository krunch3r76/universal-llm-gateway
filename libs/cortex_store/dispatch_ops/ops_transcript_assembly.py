"""Transcript assembly dispatch op — thin wrapper over `transcript_assembly`.

The pure assembly pipeline (JSONL → verbatim markdown, composition with the
agent-supplied structural layer, content-hash) lives in
`libs/cortex_store/transcript_assembly.py` so `_op_session_close` can reuse
it without going through the dispatch indirection.  This op stays as a
debug / probe surface: agents can render a verbatim layer without committing
to a close.
"""

from __future__ import annotations

import logging
from typing import Any

from ..transcript_assembly import (
    TranscriptPathError,
    assemble_verbatim_md,
    resolve_jsonl_path,
)

logger = logging.getLogger("cortex-api.dispatch_ops.transcript_assembly")


def _op_assemble_transcript(
    jsonl_path: str | None = None,
    session_id: str | None = None,
    agent: str | None = None,
    assistant_label: str | None = None,
    **_: object,
) -> dict[str, Any]:
    """Assemble a verbatim transcript layer from a Cursor JSONL.

    Args:
      jsonl_path: path under ``CURSOR_AGENT_TRANSCRIPTS_ROOT`` (absolute or
        relative to the root).  Sandbox-enforced — paths outside the root
        return ``{"error", "reason": "path_outside_root"}``.
      session_id: ``{agent}-YYYY-MM-DD-HHMM`` — appears in the H1 line.
      agent: cosmetic; echoed in the response.
      assistant_label: heading label for assistant blocks; default
        ``"Assistant"``.

    Returns:
      ``{"transcript_md", "turn_count", "byte_count", "agent"}`` on success,
      ``{"error", "reason"}`` otherwise.  ``transcript_md`` here is the
      verbatim layer ONLY — the dispatch caller (debug / probe) is expected
      to inspect it, NOT to pass it back as a `session_close` argument
      (that path is dead — see Phase 2 of session-close-server-side-transcript).
    """
    if not session_id:
        return {"error": "session_id is required", "reason": "missing_arg"}
    if not jsonl_path:
        return {"error": "jsonl_path is required", "reason": "missing_arg"}

    try:
        path = resolve_jsonl_path(jsonl_path)
    except TranscriptPathError as exc:
        reason = (
            "path_outside_root"
            if "outside" in str(exc)
            else "jsonl_missing"
            if "not found" in str(exc) or "not a regular file" in str(exc)
            else "missing_arg"
        )
        return {"error": str(exc), "reason": reason}

    try:
        verbatim_md, turn_count = assemble_verbatim_md(
            jsonl_path=path,
            session_id=session_id,
            assistant_label=assistant_label,
        )
    except ValueError as exc:
        return {"error": str(exc), "reason": "jsonl_parse_error"}

    return {
        "transcript_md": verbatim_md,
        "turn_count": turn_count,
        "byte_count": len(verbatim_md.encode("utf-8")),
        "agent": agent,
    }


__all__ = ["_op_assemble_transcript"]
