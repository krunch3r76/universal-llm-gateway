"""Session-journal detectors: continuity-edge integrity."""

from __future__ import annotations

import re
from typing import Any

from ...db import query
from .._shared import _FILES_ROOT
from ._shared import _finding

_CONTINUES_CLAIM_RE = re.compile(r"\*\*Continues:\*\*\s*\S+", re.IGNORECASE)


def _session_claims_continuation(
    *,
    handoff_prompt: str | None,
    file_path: str | None,
) -> bool:
    """True when the close explicitly signals a continuation arc."""
    if handoff_prompt and handoff_prompt.strip():
        return True
    if not file_path:
        return False
    transcript = _FILES_ROOT / file_path
    if not transcript.is_file():
        return False
    try:
        text = transcript.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(_CONTINUES_CLAIM_RE.search(text))


def detect_prior_session_id_omitted(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Session closed without prior_session_id despite an earlier same-agent session.

    Suppressed when the close did not claim continuation (no ``**Continues:**``
    in the structural layer and no ``handoff_prompt``) — omission is expected
    for fresh sessions without ``cortex_brief``.
    """
    sql = """
        SELECT sj.session_id, sj.agent, sj.handoff_prompt, sj.file_path
        FROM session_journals sj
        WHERE sj.prior_session_id IS NULL
          AND EXISTS (
              SELECT 1
              FROM session_journals earlier
              WHERE earlier.agent = sj.agent
                AND earlier.id < sj.id
          )
    """
    params: tuple[Any, ...] = ()
    if subject:
        transcript_id = subject.removeprefix("transcript:")
        sql += " AND sj.session_id = ?"
        params = (transcript_id,)
    rows = query(conn, sql, params)
    findings: list[dict[str, Any]] = []
    for row in rows:
        if not _session_claims_continuation(
            handoff_prompt=row.get("handoff_prompt"),
            file_path=row.get("file_path"),
        ):
            continue
        session_id = row["session_id"]
        findings.append(
            _finding(
                "prior_session_id_omitted",
                f"transcript:{session_id}",
                (
                    f"Session {session_id} ({row['agent']}) claimed continuation "
                    "but omitted prior_session_id even though an earlier "
                    "same-agent session exists."
                ),
            )
        )
    return findings


__all__ = ["detect_prior_session_id_omitted"]
