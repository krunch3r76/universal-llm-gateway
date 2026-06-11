"""I/O and size limits for handoff write resolution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status

from .handoff_derivation import (
    HANDOFF_PROMPT_MAX_CHARS,
    HANDOFF_PROVENANCE_JSON_MAX_BYTES,
)
from .handoff_paths import normalize_handoff_source_path, sha256_bytes
from .session_close_validation import build_validation_error


def read_handoff_source_file(
    files_root: Path,
    source_path: str,
) -> tuple[str, str]:
    """Read sandboxed source bytes once; return ``(text, source_file_sha256)``."""
    rel = normalize_handoff_source_path(source_path)
    if rel is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=build_validation_error(
                reason="handoff_source_path.invalid",
                field="handoff_source_path",
                received=source_path,
                expected="non-empty cortex-relative path",
                examples=["notes/system/sessions/cursor-2026-06-03-handoff.md"],
                hint="Pass a path under the cortex files root.",
                detail="handoff_source_path is empty after normalization.",
            ),
        )
    try:
        abs_path = (files_root / rel).resolve()
        abs_path.relative_to(files_root.resolve())
        raw = abs_path.read_bytes()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=build_validation_error(
                reason="handoff_source_path.sandbox_escape",
                field="handoff_source_path",
                received=source_path,
                expected="path resolved under CORTEX_FILES_ROOT",
                examples=["notes/system/sessions/handoff.md"],
                hint="Do not use .. or absolute paths outside the sandbox.",
                detail=str(exc),
            ),
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=build_validation_error(
                reason="handoff_source_path.unreadable",
                field="handoff_source_path",
                received=source_path,
                expected="readable UTF-8 file under cortex files root",
                examples=["notes/system/sessions/handoff.md"],
                hint="Write the handoff file before close, or fix the path.",
                detail=f"Could not read handoff source file: {exc}",
            ),
        ) from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=build_validation_error(
                reason="handoff_source_path.not_utf8",
                field="handoff_source_path",
                received=source_path,
                expected="UTF-8 text",
                examples=[],
                hint="Handoff source files must be UTF-8 markdown.",
                detail=str(exc),
            ),
        ) from exc
    return text, sha256_bytes(raw)


def validate_handoff_write_limits(
    *,
    handoff_prompt: str | None,
    provenance: dict[str, Any] | None,
) -> None:
    """Binding #7 — reject oversize prompt / provenance before DB or attribute write."""
    if handoff_prompt is not None and len(handoff_prompt) > HANDOFF_PROMPT_MAX_CHARS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=build_validation_error(
                reason="handoff_prompt.too_long",
                field="handoff_prompt",
                received=len(handoff_prompt),
                expected=f"length <= {HANDOFF_PROMPT_MAX_CHARS}",
                examples=[],
                hint="Shorten the handoff region or split content across the source file.",
                detail=(
                    f"handoff_prompt length {len(handoff_prompt)} exceeds "
                    f"{HANDOFF_PROMPT_MAX_CHARS}."
                ),
            ),
        )
    if provenance is not None:
        encoded = json.dumps(provenance, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > HANDOFF_PROVENANCE_JSON_MAX_BYTES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=build_validation_error(
                    reason="handoff_provenance.too_long",
                    field="handoff_provenance",
                    received=len(encoded.encode("utf-8")),
                    expected=f"JSON size <= {HANDOFF_PROVENANCE_JSON_MAX_BYTES} bytes",
                    examples=[],
                    hint="Reduce provenance payload size.",
                    detail="handoff_provenance JSON exceeds max encoded size.",
                ),
            )
