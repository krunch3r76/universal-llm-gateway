"""Session close route handler."""

from __future__ import annotations

from ..db import json_encode  # re-export for tests patching session_close.json_encode
from ..dispatch_ops._shared import _FILES_ROOT  # re-export for tests / session_journals
from ..models import SessionCloseRequest, SessionCloseResponse
from .session_close_persist import persist_session_close, try_idempotent_session_close
from .session_close_validate import validate_session_close

__all__ = [
    "_FILES_ROOT",
    "close_session",
    "json_encode",
]


def close_session(body: SessionCloseRequest) -> SessionCloseResponse:
    """Atomic session close: assemble verbatim, write file, commit DB tx.

    The handler is the single atomic boundary:
      1. Validate ``session_id``, ``summary``, ``session_summary_md``.
      2. Resolve ``transcript_jsonl_path`` under ``CURSOR_AGENT_TRANSCRIPTS_ROOT``.
      3. Assemble the verbatim layer from the JSONL.
      4. Compose verbatim + ``session_summary_md`` into the final
         markdown.
      5. Re-validate the composed markdown (dual-layer doctrine — defense
         in depth; should always pass when assembly succeeded).
      6. Idempotency check on ``session_journals.session_id``.
      7. Write the file under ``notes/system/transcripts/{session_id}.md``.
      8. Atomic DB tx: entity + journal row + ``continues`` edge.
      9. Compute ``content_hash`` of the on-disk markdown and return.

    ``body.transcript_depth`` (default ``"verbatim"``) selects the
    archival layer:

      - ``verbatim``: steps 2–9 as documented (current behavior).
      - ``light``: file content is ``session_summary_md`` alone; no
        verbatim assembly. Transcript entity carries
        ``attributes.transcript_depth="light"``.
      - ``none``: no file, no transcript entity. Journal row written
        with ``file_path=NULL``; continues edge written per the universal
        continuity path. handoff_prompt / handoff_source_path at ``none``
        are rejected (422 ``handoff.requires_transcript_entity``).
        Response transcript_entity_id / transcript_path / content_hash
        are null.

    On any failure between steps 2 and 8 that occurs after the file is
    written, the file is unlinked before raising.
    On any failure after the transcript file is written, a best-effort
    ``Path.unlink`` is performed; an OSError on unlink is logged at WARNING
    and does not suppress the original exception.
    """
    ctx = validate_session_close(body)
    idempotent = try_idempotent_session_close(body, ctx)
    if idempotent is not None:
        return idempotent
    return persist_session_close(body, ctx)
