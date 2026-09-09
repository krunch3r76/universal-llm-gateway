"""Verbatim-layer helpers for succession seal / fill / tape render (R3)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from continuity_tape.messages import (
    ContinuityMessagesEnvelope,
    seal_messages_sha256,
)
from continuity_tape.seal_reader import (
    load_sealed_envelope,
    load_sealed_envelope_from_path,
    sealed_turn_count,
)

STRUCTURAL_MARKER = "\n## Session Summary"
_SEAL_DIR = "notes/system/seals"
_TRANSCRIPT_DIR = "notes/system/transcripts"


def transcript_messages_path(file_path: str) -> str:
    """Map ``notes/system/transcripts/{sid}.md`` → seal JSON path."""
    name = Path(file_path).name
    if name.endswith(".md"):
        name = name[:-3] + ".messages.json"
    return f"{_SEAL_DIR}/{name}"


def split_verbatim_layer(
    full_md: str,
    *,
    verbatim_bytes: int | None = None,
) -> str:
    """Return the verbatim prefix of a composed transcript file."""
    if verbatim_bytes is not None and verbatim_bytes >= 0:
        raw = full_md.encode("utf-8")
        if verbatim_bytes <= len(raw):
            return raw[:verbatim_bytes].decode("utf-8")
    idx = full_md.find(STRUCTURAL_MARKER)
    if idx == -1:
        return full_md
    return full_md[:idx]


def verbatim_fingerprint(codec: str, payload: str | Sequence[Mapping[str, Any]]) -> tuple[str, int]:
    """Return ``(sha256:…, byte length)`` for a verbatim payload by codec."""
    if codec == "messages-v1":
        assert not isinstance(payload, str)
        raw = _seal_ndjson_bytes(payload)
        digest = hashlib.sha256(raw).hexdigest()
        return f"sha256:{digest}", len(raw)
    assert isinstance(payload, str)
    raw = payload.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return f"sha256:{digest}", len(raw)


def _seal_ndjson_bytes(messages: Sequence[Mapping[str, Any]]) -> bytes:
    from continuity_tape.messages import seal_messages_canonical_bytes

    return seal_messages_canonical_bytes(messages)


def prefix_holds(
    codec: str,
    sealed: str | Sequence[Mapping[str, Any]],
    new: str | Sequence[Mapping[str, Any]],
) -> bool:
    """PREFIX-EXTEND check per codec (R5)."""
    if codec == "md-v1":
        assert isinstance(sealed, str) and isinstance(new, str)
        return new.startswith(sealed)
    assert isinstance(sealed, list) and isinstance(new, list)
    if len(new) < len(sealed):
        return False
    if len(new) > len(sealed):
        return new[: len(sealed)] == sealed
    if new[: len(sealed) - 1] != sealed[: len(sealed) - 1]:
        return False
    if not sealed:
        return True
    last_sealed = sealed[-1]
    last_new = new[-1]
    if last_sealed.get("role") != last_new.get("role"):
        return False
    sealed_content = last_sealed.get("content")
    new_content = last_new.get("content")
    if sealed_content is None:
        return True
    if new_content is None:
        return False
    return str(new_content).startswith(str(sealed_content))


def stamp_verbatim_fields(
    conn: Any,
    *,
    session_id: str,
    codec: str,
    payload: str | Sequence[Mapping[str, Any]],
) -> None:
    """Persist verbatim fingerprint columns on ``session_journals``."""
    sha, nbytes = verbatim_fingerprint(codec, payload)
    conn.execute(
        "UPDATE session_journals SET verbatim_sha256 = ?, verbatim_bytes = ?, "
        "verbatim_codec = ? WHERE session_id = ?",
        (sha, nbytes, codec, session_id),
    )


def load_sealed_verbatim_for_session(
    session_id: str,
    *,
    files_root: Path,
) -> tuple[str, str] | None:
    """Load verbatim layer from a succession-sealed journal row, if present."""
    from .db import cortex_conn
    from .session_close_successor_hop import lookup_sealed_journal

    sealed = lookup_sealed_journal(session_id)
    if sealed is None or sealed.closed_by != "succession":
        return None
    conn = cortex_conn()
    try:
        row = conn.execute(
            "SELECT file_path, verbatim_bytes, verbatim_codec FROM session_journals "
            "WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row["file_path"]:
        return None
    rel_path = str(row["file_path"])
    codec = row["verbatim_codec"] or "md-v1"
    if codec == "messages-v1":
        seal_path = files_root / transcript_messages_path(rel_path)
        if seal_path.is_file():
            from continuity_tape.render_md import render_verbatim_md

            envelope = load_sealed_envelope_from_path(seal_path)
            verbatim, _ = render_verbatim_md(envelope, session_id)
            return verbatim, rel_path
    path = files_root / rel_path
    if not path.is_file():
        return None
    full = path.read_text(encoding="utf-8")
    verbatim = split_verbatim_layer(
        full, verbatim_bytes=journal_verbatim_bytes(row)
    )
    return verbatim, rel_path


def build_seal_envelope_meta(
    envelope: ContinuityMessagesEnvelope,
    *,
    session_id: str,
    tools: str = "marker",
    sealed_at: str,
    source_sha256: str | None = None,
) -> ContinuityMessagesEnvelope:
    """Stamp seal-time meta fields on an envelope before writing JSON."""
    sha = seal_messages_sha256(envelope.messages)
    meta = envelope.meta.model_copy(
        update={
            "surface": "cursor",
            "session_id": session_id,
            "tools": tools,
            "sealed_at": sealed_at,
            "messages_sha256": sha,
            "turn_count": envelope.meta.turn_count,
            "message_count": len(envelope.messages),
            "source_sha256": source_sha256 or getattr(
                envelope.meta, "source_sha256", None
            ),
        }
    )
    return envelope.model_copy(update={"meta": meta})


def journal_verbatim_bytes(row: dict[str, Any] | Any) -> int | None:
    """Read ``verbatim_bytes`` from a journal row mapping or sqlite Row."""
    if row is None:
        return None
    try:
        val = row["verbatim_bytes"]
    except (KeyError, TypeError, IndexError):
        return None
    if val is None:
        return None
    return int(val)


__all__ = [
    "STRUCTURAL_MARKER",
    "build_seal_envelope_meta",
    "journal_verbatim_bytes",
    "load_sealed_envelope",
    "load_sealed_envelope_from_path",
    "load_sealed_verbatim_for_session",
    "prefix_holds",
    "sealed_turn_count",
    "split_verbatim_layer",
    "stamp_verbatim_fields",
    "transcript_messages_path",
    "verbatim_fingerprint",
]
