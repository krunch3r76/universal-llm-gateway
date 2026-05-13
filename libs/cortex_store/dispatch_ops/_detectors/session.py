"""Session-journal detectors: continuity-edge integrity."""

from __future__ import annotations

from typing import Any

from ...db import query
from ._shared import _finding


def detect_prior_session_id_omitted(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Session closed without prior_session_id despite an earlier same-agent session."""
    sql = """
        SELECT sj.session_id, sj.agent
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
    return [
        _finding(
            "prior_session_id_omitted",
            f"transcript:{r['session_id']}",
            f"Session {r['session_id']} ({r['agent']}) omitted prior_session_id even though an earlier same-agent session exists.",
        )
        for r in rows
    ]


__all__ = ["detect_prior_session_id_omitted"]
