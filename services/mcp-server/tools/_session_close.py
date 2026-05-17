"""Session close helper — DEPRECATED.

This module previously returned step-by-step protocol instructions without
performing the close itself. That path was the proximate cause of the
`cursor-2026-05-01-2059` hallucination (agent-bus thread 824): the agent
emitted a complete six-step session-close narrative while the actual writes
either never occurred or rolled back, and the reminder dispatch had no
mechanism to detect or surface that.

The atomic path lives at `cortex(tool="session_close", ...)` (handler:
`libs/cortex_store/routes/session_journals.py:close_session`). It validates
transcript structure, writes the file, and atomically creates the
transcript entity + journal row + `continues` edge in a single DB
transaction. It returns 201 on success or an explicit error — there is no
in-between state.

`build_session_close()` is retained as a stub that returns a hard error so
any remaining caller fails loudly rather than silently following the unsafe
path. The function will be deleted in a follow-up change once we confirm no
caller still depends on it.
"""

from __future__ import annotations

from typing import Any


def build_session_close(
    agent: str,  # noqa: ARG001 — kept for API stability of the deprecation stub
    session_id: str = "",  # noqa: ARG001
) -> dict[str, Any]:
    """Return a hard-deprecation error.

    The reminder-only dispatch path was the proximate cause of the
    `cursor-2026-05-01-2059` hallucination (agent-bus thread 824). Use
    the atomic ``cortex(tool="session_close", ...)`` path instead.
    """
    return {
        "error": "deprecated",
        "use": (
            'cortex(tool="session_close", arguments=\'{"session_id": ..., '
            '"agent": ..., '
            '"transcript_jsonl_path": ... (cursor) OR "transcript_md": ... (web), '
            '"session_summary_md": ..., "summary": ...}\')'
        ),
        "reason": (
            "The reminder-only dispatch path returned step instructions "
            "without performing the close, leading to hallucinated "
            "session-close success. The atomic cortex session_close path "
            "reads the Cursor agent-transcripts JSONL server-side, "
            "assembles the verbatim layer, validates structure, writes the "
            "file, and creates the transcript entity + journal row + "
            "continues edge in a single DB transaction. See agent-bus "
            "thread 824 and session-close.mdc."
        ),
        "incident": "cursor-2026-05-01-2059",
        "thread": "824",
    }
