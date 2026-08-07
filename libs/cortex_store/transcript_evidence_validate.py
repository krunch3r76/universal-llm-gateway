"""Write-time validation for ``transcript:`` entries in assertion evidence_uris."""

from __future__ import annotations

from typing import Any

from .db import cortex_conn
from .transcript_turn_resolve import (
    TranscriptResolveError,
    resolve_transcript_turn,
    transcript_resolve_error_to_detail,
)

_TRANSCRIPT_URI_PREFIX = "transcript:"


def _is_transcript_evidence_uri(uri: str) -> bool:
    return uri.strip().startswith(_TRANSCRIPT_URI_PREFIX)


def validate_transcript_evidence_uris(
    evidence_uris: list[str] | None,
    *,
    conn: object | None = None,
) -> None:
    """Fail-closed when any ``transcript:`` cite cannot resolve at write time.

    Distinguishes **too early** (``transcript_absent`` — entity not minted yet)
    from **wrong cite** (``turn_out_of_range``, ``invalid_turn_number``,
    ``transcript_missing_turn_fragment``) and **depth failure**
    (``transcript_below_verbatim``). Each code carries an actionable message.
    """
    if not evidence_uris:
        return
    transcript_uris = [u for u in evidence_uris if _is_transcript_evidence_uri(u)]
    if not transcript_uris:
        return
    own_conn = conn is None
    if own_conn:
        conn = cortex_conn()
    try:
        for uri in transcript_uris:
            resolve_transcript_turn(uri, conn=conn)
    except TranscriptResolveError:
        raise
    finally:
        if own_conn and conn is not None:
            conn.close()  # type: ignore[union-attr]


def http_detail_from_transcript_error(exc: TranscriptResolveError) -> dict[str, Any]:
    """422 payload for assertion mutation routes."""
    return transcript_resolve_error_to_detail(exc)


__all__ = [
    "http_detail_from_transcript_error",
    "validate_transcript_evidence_uris",
]
