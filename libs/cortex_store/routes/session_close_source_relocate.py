"""Relocate JSONL source bytes beside the messages seal at session-close persist."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from durable_io.atomic import durable_write_bytes
from fastapi import HTTPException, status

from ..events_tape import (
    transcript_source_relocate_refused,
    transcript_source_relocated,
)
from ..session_close_validation import build_validation_error


@dataclass(frozen=True, slots=True)
class SourceRelocateResult:
    """Outcome of a source relocation write (for persist rollback)."""

    prior_snapshot: bytes | None
    written_sha256: str


def relocate_transcript_source(
    *,
    source_abs_path: Path,
    raw_bytes: bytes,
    expected_sha256: str,
    session_id: str,
    transcript_id: str | None,
    files_root: Path,
) -> SourceRelocateResult:
    """Write JSONL bytes to the seal-adjacent source path or refuse on digest mismatch."""
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
    transcript_source_relocated(
        session_id=session_id,
        transcript_id=transcript_id or "",
        bytes=len(raw_bytes),
        source_sha256=written_sha256,
    )
    return SourceRelocateResult(
        prior_snapshot=prior_snapshot,
        written_sha256=written_sha256,
    )


def restore_transcript_source(
    source_abs_path: Path,
    prior_snapshot: bytes | None,
    *,
    files_root: Path,
) -> None:
    """Rollback helper: restore prior bytes or unlink when no prior file existed."""
    if prior_snapshot is not None:
        durable_write_bytes(
            source_abs_path,
            prior_snapshot,
            retain_store_root=files_root,
        )
    elif source_abs_path.is_file():
        source_abs_path.unlink(missing_ok=True)


__all__ = ["SourceRelocateResult", "relocate_transcript_source", "restore_transcript_source"]
