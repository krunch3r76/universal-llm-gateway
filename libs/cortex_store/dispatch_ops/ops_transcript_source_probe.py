"""Dispatch op: verify relocated JSONL source against the messages seal."""

from __future__ import annotations

import hashlib
from typing import Any

from continuity_tape.extract_jsonl import extract_turns_from_jsonl_bytes
from continuity_tape.messages import seal_messages_sha256

from ..events_tape import transcript_source_probed
from ..verbatim_succession import (
    load_sealed_envelope_from_path,
    transcript_messages_path,
    transcript_source_path,
)
from ._shared import _FILES_ROOT


def _op_transcript_source_probe(
    session_id: str | None = None,
    *,
    emit_messages: bool = False,
    **_: object,
) -> dict[str, Any]:
    """Report whether Cortex holds a source copy matching the seal digests."""
    if not session_id:
        return {
            "error": "session_id is required",
            "reason": "missing_arg",
            "code": "transcript_source_probe.missing_session",
        }

    transcript_rel = f"notes/system/transcripts/{session_id}.md"
    seal_rel = transcript_messages_path(transcript_rel)
    source_rel = transcript_source_path(transcript_rel)
    seal_path = _FILES_ROOT / seal_rel
    source_path = _FILES_ROOT / source_rel

    if not seal_path.is_file():
        return {
            "session_id": session_id,
            "source_present": False,
            "error": "seal_missing",
            "code": "transcript_source_probe.no_seal",
        }

    sealed = load_sealed_envelope_from_path(seal_path)
    expected_source = sealed.meta.source_sha256 or ""
    expected_messages = sealed.meta.messages_sha256 or ""

    if not source_path.is_file():
        transcript_source_probed(
            session_id=session_id,
            source_present=False,
            sha_match=False,
            turn_count=int(sealed.meta.turn_count or 0),
        )
        return {
            "session_id": session_id,
            "source_present": False,
            "source_sha256": None,
            "messages_sha256": expected_messages,
            "sha_match": False,
            "turn_count": int(sealed.meta.turn_count or 0),
            "emit_messages": emit_messages,
        }

    raw = source_path.read_bytes()
    source_sha256 = hashlib.sha256(raw).hexdigest()
    envelope = extract_turns_from_jsonl_bytes(raw, session_id=session_id)
    recomputed_messages = seal_messages_sha256(envelope.messages)
    sha_match = (
        source_sha256 == expected_source
        and recomputed_messages == expected_messages
    )
    turn_count = int(envelope.meta.turn_count or sealed.meta.turn_count or 0)
    transcript_source_probed(
        session_id=session_id,
        source_present=True,
        sha_match=sha_match,
        turn_count=turn_count,
    )
    report: dict[str, Any] = {
        "session_id": session_id,
        "source_present": True,
        "source_sha256": source_sha256,
        "expected_source_sha256": expected_source,
        "messages_sha256": recomputed_messages,
        "expected_messages_sha256": expected_messages,
        "sha_match": sha_match,
        "turn_count": turn_count,
        "bytes": len(raw),
        "emit_messages": emit_messages,
    }
    if emit_messages:
        report["messages"] = envelope.messages
    return report


__all__ = ["_op_transcript_source_probe"]
