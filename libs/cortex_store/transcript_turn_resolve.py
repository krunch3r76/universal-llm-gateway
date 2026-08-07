"""Resolve ``transcript:<session_id>#turn-N`` to User+Assistant turn body."""

from __future__ import annotations

import json
import re
from typing import Any

from .db import cortex_conn, json_decode, query
from .rag_resolver import _source_uri_to_absolute_path
from .transcript_assembly import TURN_HEADING_RE

_TURN_FRAGMENT_RE = re.compile(r"^transcript:(?P<sid>.+)#turn-(?P<num>.+)$")


class TranscriptResolveError(Exception):
    """Structured resolve / evidence validation failure."""

    def __init__(self, code: str, message: str, status_code: int = 404) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def parse_transcript_turn_uri(uri: str) -> tuple[str, int] | None:
    """Return (session_id, turn_number) when *uri* carries a ``#turn-N`` fragment."""
    match = _TURN_FRAGMENT_RE.match(uri.strip())
    if not match:
        return None
    sid = match.group("sid")
    num_raw = match.group("num")
    try:
        turn_num = int(num_raw)
    except ValueError:
        raise TranscriptResolveError(
            code="invalid_turn_number",
            message=(
                f"turn_number must be int, got {num_raw!r} in transcript URI {uri!r}"
            ),
            status_code=422,
        ) from None
    if turn_num < 1:
        raise TranscriptResolveError(
            code="invalid_turn_number",
            message=f"turn_number must be >= 1, got {turn_num} in {uri!r}",
            status_code=422,
        )
    return sid, turn_num


def _load_entity(conn: object, session_id: str) -> dict[str, Any] | None:
    entity_id = f"transcript:{session_id}"
    rows = query(conn, "SELECT * FROM entities WHERE id = ?", (entity_id,))  # type: ignore[arg-type]
    if not rows:
        return None
    row = dict(rows[0])
    attrs = row.get("attributes")
    if isinstance(attrs, str):
        row["attributes"] = json_decode(attrs)
    return row


def _transcript_depth(entity: dict[str, Any]) -> str | None:
    attrs = entity.get("attributes")
    if not isinstance(attrs, dict):
        return None
    depth = attrs.get("transcript_depth")
    return str(depth) if depth is not None else None


def _extract_turn_body(transcript_md: str, turn_num: int) -> str | None:
    """Return User+Assistant body for *turn_num*, or None when heading absent."""
    lines = transcript_md.splitlines()
    start_idx: int | None = None
    for idx, line in enumerate(lines):
        match = TURN_HEADING_RE.match(line)
        if match and int(match.group(1)) == turn_num:
            start_idx = idx + 1
            break
    if start_idx is None:
        return None
    end_idx = len(lines)
    for idx in range(start_idx, len(lines)):
        if TURN_HEADING_RE.match(lines[idx]):
            end_idx = idx
            break
    body = "\n".join(lines[start_idx:end_idx]).strip()
    return body or None


def resolve_transcript_turn(
    uri: str,
    *,
    conn: object | None = None,
) -> dict[str, Any]:
    """Resolve a ``transcript:<sid>#turn-N`` URI to turn body text.

    Raises ``TranscriptResolveError`` with distinct codes:
    - ``transcript_absent`` — entity not minted / depth none / no file (too early)
    - ``transcript_below_verbatim`` — closed below turn grain
    - ``turn_out_of_range`` — verbatim file lacks turn N
    - ``invalid_turn_number`` — non-int or <1 N
    - ``transcript_missing_turn_fragment`` — bare ``transcript:<sid>`` (no fragment)
    """
    parsed = parse_transcript_turn_uri(uri)
    if parsed is None:
        raise TranscriptResolveError(
            code="transcript_missing_turn_fragment",
            message=(
                f"Transcript cite {uri!r} must include #turn-N fragment for "
                "turn-grain resolution (e.g. transcript:<sid>#turn-1)."
            ),
            status_code=422,
        )
    session_id, turn_num = parsed
    own_conn = conn is None
    if own_conn:
        conn = cortex_conn()
    try:
        entity = _load_entity(conn, session_id)  # type: ignore[arg-type]
        if entity is None:
            raise TranscriptResolveError(
                code="transcript_absent",
                message=(
                    f"Transcript entity transcript:{session_id} does not exist — "
                    "session may still be open or was closed with depth=none. "
                    "Patch evidence_uris after session_close commits the transcript "
                    "entity, not during the live session."
                ),
                status_code=404,
            )
        depth = _transcript_depth(entity)
        if depth == "none" or not entity.get("source_uri"):
            raise TranscriptResolveError(
                code="transcript_absent",
                message=(
                    f"Transcript {session_id} has no fetchable archive "
                    f"(transcript_depth={depth!r}, source_uri absent). "
                    "Wait for verbatim session_close before citing turn grain."
                ),
                status_code=404,
            )
        if depth != "verbatim":
            raise TranscriptResolveError(
                code="transcript_below_verbatim",
                message=(
                    f"Transcript {session_id} closed at depth={depth!r}; "
                    "turn-grain body was never archived (structural-only / "
                    "turn_count=0). Cite "
                    f"transcript:{session_id}#turn-{turn_num} is not L1-fetchable. "
                    "Re-close cannot upgrade depth; re-state under a future verbatim "
                    "close or use another L1 class."
                ),
                status_code=404,
            )
        source_uri = str(entity["source_uri"])
        abs_path = _source_uri_to_absolute_path(source_uri)
        try:
            transcript_md = open(abs_path, encoding="utf-8").read()
        except OSError as exc:
            raise TranscriptResolveError(
                code="transcript_absent",
                message=(
                    f"Transcript file for {session_id} missing at {abs_path}: {exc}"
                ),
                status_code=404,
            ) from exc
        body = _extract_turn_body(transcript_md, turn_num)
        if body is None:
            raise TranscriptResolveError(
                code="turn_out_of_range",
                message=(
                    f"Turn {turn_num} not found in verbatim transcript "
                    f"transcript:{session_id} — heading "
                    f"'## Turn {turn_num} — …' absent."
                ),
                status_code=404,
            )
        return {
            "resolved": "transcript_turn",
            "uri": uri,
            "session_id": session_id,
            "turn_number": turn_num,
            "transcript_depth": depth,
            "body": body,
        }
    finally:
        if own_conn and conn is not None:
            conn.close()  # type: ignore[union-attr]


def resolve_transcript_turn_op(uri: str) -> dict[str, Any]:
    """Dispatch ``resolve`` op wrapper — maps errors to JSON payloads."""
    try:
        return resolve_transcript_turn(uri)
    except TranscriptResolveError as exc:
        return {
            "error": exc.code,
            "code": exc.code,
            "message": exc.message,
            "status_code": exc.status_code,
        }


def transcript_resolve_error_to_detail(exc: TranscriptResolveError) -> dict[str, Any]:
    return {
        "error": "transcript_evidence_rejected",
        "code": exc.code,
        "message": exc.message,
    }


__all__ = [
    "TranscriptResolveError",
    "parse_transcript_turn_uri",
    "resolve_transcript_turn",
    "resolve_transcript_turn_op",
    "transcript_resolve_error_to_detail",
]
