"""Relocate JSONL source bytes beside the messages seal at session-close persist."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from durable_io.atomic import durable_write_bytes
from fastapi import HTTPException, status

from ..events_tape import (
    transcript_source_relocate_refused,
    transcript_source_relocated,
)
from ..raw_transcript_store import (
    WriteRawTranscriptResult,
    restore_raw_transcript,
    write_raw_transcript,
)
from ..session_close_validation import build_validation_error

_SESSION_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True, slots=True)
class SourceRelocateResult:
    """Outcome of a source relocation write (for persist rollback)."""

    prior_snapshot: bytes | None
    written_sha256: str
    raw_rel_path: str | None = None
    manifest_rel_path: str | None = None
    raw_write_result: WriteRawTranscriptResult | None = None


def _date_str_from_session_id(session_id: str) -> str:
    match = _SESSION_DATE_RE.search(session_id)
    if match:
        return match.group(1)
    return datetime.now(UTC).strftime("%Y-%m-%d")


def relocate_transcript_source(
    *,
    source_abs_path: Path,
    raw_bytes: bytes,
    expected_sha256: str,
    session_id: str,
    transcript_id: str | None,
    files_root: Path,
    house: str | None = None,
) -> SourceRelocateResult:
    """Write JSONL bytes to the seal path and canonical raw transcript store."""
    actual = hashlib.sha256(raw_bytes).hexdigest()
    if actual != expected_sha256:
        transcript_source_relocate_refused(
            session_id=session_id,
            transcript_id=transcript_id or "",
            expected_sha256=expected_sha256,
            actual_sha256=actual,
        )
        conflict = build_validation_error(
            reason="transcript.source.relocate.refused",
            field="transcript_jsonl_path",
            received=actual,
            expected=expected_sha256,
            examples=[],
            hint="JSONL bytes must match meta.source_sha256 from validation.",
            detail=(
                f"session {session_id!r} source relocation refused: "
                f"digest {actual} != expected {expected_sha256}."
            ),
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=conflict)

    prior_snapshot: bytes | None = None
    if source_abs_path.is_file():
        prior_snapshot = source_abs_path.read_bytes()

    written_sha256 = durable_write_bytes(
        source_abs_path,
        raw_bytes,
        retain_store_root=files_root,
    )
    native_id = (transcript_id or session_id).strip()
    date_str = _date_str_from_session_id(session_id)
    aliases = [a for a in (f"transcript:{session_id}", session_id, transcript_id) if a]
    raw_write_result = write_raw_transcript(
        files_root=files_root,
        house=house,
        source="cursor",
        kind="raw",
        date_str=date_str,
        native_id=native_id,
        native_ext="jsonl",
        raw_bytes=raw_bytes,
        exporter="cortex_store.session_close",
        exporter_version="1.0",
        aliases=aliases,
        expected_sha256=expected_sha256,
    )
    transcript_source_relocated(
        session_id=session_id,
        transcript_id=transcript_id or "",
        bytes=len(raw_bytes),
        source_sha256=written_sha256,
    )
    return SourceRelocateResult(
        prior_snapshot=prior_snapshot,
        written_sha256=written_sha256,
        raw_rel_path=raw_write_result.raw_rel_path,
        manifest_rel_path=raw_write_result.manifest_rel_path,
        raw_write_result=raw_write_result,
    )


def restore_transcript_source(
    source_abs_path: Path,
    prior_snapshot: bytes | None,
    *,
    files_root: Path,
    raw_write_result: WriteRawTranscriptResult | None = None,
) -> None:
    """Rollback helper: restore prior bytes or unlink when no prior file existed."""
    if raw_write_result is not None:
        restore_raw_transcript(raw_write_result, files_root=files_root)
    if prior_snapshot is not None:
        durable_write_bytes(
            source_abs_path,
            prior_snapshot,
            retain_store_root=files_root,
        )
    elif source_abs_path.is_file():
        source_abs_path.unlink(missing_ok=True)


__all__ = ["SourceRelocateResult", "relocate_transcript_source", "restore_transcript_source"]
