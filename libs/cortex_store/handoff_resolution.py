"""2-A v2 handoff resolution for session close and upsert."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status

from .handoff_derivation import (
    DERIVATION_DETACHED_STRING,
    DERIVATION_SECTION,
    DERIVATION_SECTION_AMBIGUOUS,
    DERIVATION_SECTION_UNRESOLVED,
    HANDOFF_PROMPT_MAX_CHARS,
    HANDOFF_PROVENANCE_JSON_MAX_BYTES,
    WRITE_PATH_SESSION_CLOSE,
)
from .handoff_marker import extract_handoff_marker_region
from .handoff_paths import normalize_handoff_source_path, sha256_bytes, sha256_text
from .handoff_provenance import build_handoff_provenance
from .handoff_surface import build_handoff_surface_preview
from .session_close_validation import build_validation_error


@dataclass(frozen=True)
class HandoffResolution:
    """Resolved handoff state for close / upsert / dry_run preview."""

    handoff_prompt: str | None
    provenance: dict[str, Any] | None
    handoff_valid: bool
    findings: list[dict[str, Any]]
    derived_handoff_prompt: str | None


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


def _handoff_finding(kind: str, subject: str, detail: str) -> dict[str, Any]:
    from .dispatch_ops._detectors._shared import _finding

    return _finding(kind, subject, detail)


def _raise_handoff_conflict(
    *,
    reason: str,
    field: str,
    received: object,
    detail: str,
) -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=build_validation_error(
            reason=reason,
            field=field,
            received=received,
            expected="value matching dry_run preview / source extraction",
            examples=[],
            hint="Re-run dry_run and pass expected_* fields from the preview.",
            detail=detail,
        ),
    )


def resolve_handoff_for_write(
    *,
    files_root: Path,
    write_path: str,
    written_at: str,
    handoff_source_path: str | None,
    handoff_source_section: str | None,
    handoff_prompt: str | None,
    expected_handoff_prompt: str | None = None,
    expected_derived_handoff_prompt_sha256: str | None = None,
    expected_source_file_sha256: str | None = None,
) -> HandoffResolution:
    """Resolve handoff prompt + provenance for close or upsert (2-A v2)."""
    findings: list[dict[str, Any]] = []
    prompt_in = handoff_prompt.strip() if handoff_prompt else None
    section_label = (
        handoff_source_section.strip()
        if handoff_source_section and handoff_source_section.strip()
        else None
    )

    if handoff_source_path:
        text, file_sha = read_handoff_source_file(files_root, handoff_source_path)
        if (
            expected_source_file_sha256 is not None
            and file_sha != expected_source_file_sha256
        ):
            _raise_handoff_conflict(
                reason="handoff_source_file_sha256.mismatch",
                field="expected_source_file_sha256",
                received=expected_source_file_sha256,
                detail=(
                    f"source file hash {file_sha!r} does not match "
                    f"expected {expected_source_file_sha256!r}."
                ),
            )
        extracted = extract_handoff_marker_region(text, section_label)
        if extracted.status == "ambiguous":
            prov = build_handoff_provenance(
                write_path=write_path,
                source_path=handoff_source_path,
                files_root=files_root,
                written_at=written_at,
                derivation=DERIVATION_SECTION_AMBIGUOUS,
                source_section=section_label,
                source_file_sha256=file_sha,
            )
            findings.append(
                _handoff_finding(
                    "handoff_section_ambiguous",
                    normalize_handoff_source_path(handoff_source_path)
                    or handoff_source_path,
                    (
                        f"found {extracted.pair_count} marker pairs for label "
                        f"{section_label!r} — handoff is invalid; no prompt stored."
                    ),
                )
            )
            validate_handoff_write_limits(handoff_prompt=None, provenance=prov)
            return HandoffResolution(
                handoff_prompt=None,
                provenance=prov,
                handoff_valid=False,
                findings=findings,
                derived_handoff_prompt=None,
            )
        if extracted.status != "ok" or extracted.body is None:
            prov = build_handoff_provenance(
                write_path=write_path,
                source_path=handoff_source_path,
                files_root=files_root,
                written_at=written_at,
                derivation=DERIVATION_SECTION_UNRESOLVED,
                source_section=section_label,
                source_file_sha256=file_sha,
            )
            findings.append(
                _handoff_finding(
                    "handoff_section_unresolved",
                    normalize_handoff_source_path(handoff_source_path)
                    or handoff_source_path,
                    (
                        "marker region missing, empty, or unbalanced — "
                        "handoff is invalid; caller prompt is not retained."
                    ),
                )
            )
            validate_handoff_write_limits(handoff_prompt=None, provenance=prov)
            return HandoffResolution(
                handoff_prompt=None,
                provenance=prov,
                handoff_valid=False,
                findings=findings,
                derived_handoff_prompt=None,
            )

        derived = extracted.body
        derived_hash = sha256_text(derived)
        if expected_handoff_prompt is not None and derived != expected_handoff_prompt:
            _raise_handoff_conflict(
                reason="handoff_prompt.mismatch",
                field="expected_handoff_prompt",
                received=expected_handoff_prompt,
                detail="extracted handoff region does not match expected_handoff_prompt.",
            )
        if (
            expected_derived_handoff_prompt_sha256 is not None
            and derived_hash != expected_derived_handoff_prompt_sha256
        ):
            _raise_handoff_conflict(
                reason="handoff_derived_sha256.mismatch",
                field="expected_derived_handoff_prompt_sha256",
                received=expected_derived_handoff_prompt_sha256,
                detail=(
                    f"derived prompt hash {derived_hash!r} does not match "
                    f"expected {expected_derived_handoff_prompt_sha256!r}."
                ),
            )
        prov = build_handoff_provenance(
            write_path=write_path,
            source_path=handoff_source_path,
            files_root=files_root,
            written_at=written_at,
            derivation=DERIVATION_SECTION,
            source_section=section_label,
            source_file_sha256=file_sha,
            derived_handoff_prompt_sha256=derived_hash,
            derived_at=written_at,
        )
        validate_handoff_write_limits(handoff_prompt=derived, provenance=prov)
        return HandoffResolution(
            handoff_prompt=derived,
            provenance=prov,
            handoff_valid=True,
            findings=findings,
            derived_handoff_prompt=derived,
        )

    if prompt_in:
        prov = build_handoff_provenance(
            write_path=write_path,
            source_path=None,
            files_root=files_root,
            written_at=written_at,
            derivation=DERIVATION_DETACHED_STRING,
        )
        validate_handoff_write_limits(handoff_prompt=prompt_in, provenance=prov)
        return HandoffResolution(
            handoff_prompt=prompt_in,
            provenance=prov,
            handoff_valid=True,
            findings=findings,
            derived_handoff_prompt=prompt_in,
        )

    return HandoffResolution(
        handoff_prompt=None,
        provenance=None,
        handoff_valid=True,
        findings=findings,
        derived_handoff_prompt=None,
    )


def handoff_dry_run_preview(
    *,
    files_root: Path,
    handoff_source_path: str | None,
    handoff_source_section: str | None,
    handoff_prompt: str | None,
    expected_handoff_prompt: str | None = None,
    expected_derived_handoff_prompt_sha256: str | None = None,
    expected_source_file_sha256: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Non-writing handoff preview for ``session_close`` dry_run (2-A v2).

    When ``session_id`` is supplied the anchor check is included: if
    ``handoff_prompt`` omits the closing-session transcript anchor the
    finding is appended and ``handoff_valid`` is set to ``False`` so
    ``dry_run=True`` returns ``would_fail`` instead of ``would_succeed``.
    """
    written_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    resolution = resolve_handoff_for_write(
        files_root=files_root,
        write_path=WRITE_PATH_SESSION_CLOSE,
        written_at=written_at,
        handoff_source_path=handoff_source_path,
        handoff_source_section=handoff_source_section,
        handoff_prompt=handoff_prompt,
        expected_handoff_prompt=expected_handoff_prompt,
        expected_derived_handoff_prompt_sha256=expected_derived_handoff_prompt_sha256,
        expected_source_file_sha256=expected_source_file_sha256,
    )
    findings = handoff_post_close_findings(
        resolution=resolution,
        handoff_source_path=handoff_source_path,
        files_root=files_root,
    )
    handoff_valid = resolution.handoff_valid
    if session_id:
        from .handoff_audit import check_handoff_transcript_anchor

        anchor_finding = check_handoff_transcript_anchor(
            session_id=session_id,
            handoff_prompt=handoff_prompt,
            handoff_source_path=handoff_source_path,
        )
        if anchor_finding is not None:
            findings = [*findings, anchor_finding]
            handoff_valid = False
    return {
        "derived_handoff_prompt": resolution.derived_handoff_prompt,
        "handoff_provenance_preview": resolution.provenance,
        "handoff_surface_preview": build_handoff_surface_preview(
            resolution.handoff_prompt, resolution.provenance
        ),
        "handoff_valid": handoff_valid,
        "findings": findings,
    }


def handoff_post_close_findings(
    *,
    resolution: HandoffResolution,
    handoff_source_path: str | None,
    files_root: Path,
) -> list[dict[str, Any]]:
    """Append 2-B warn-only findings for legacy detached_string audit only."""
    from .handoff_audit import check_handoff_prompt_in_source

    findings = list(resolution.findings)
    prov = resolution.provenance or {}
    if prov.get("derivation") != DERIVATION_DETACHED_STRING:
        return findings
    mismatch = check_handoff_prompt_in_source(
        handoff_prompt=resolution.handoff_prompt,
        source_path=handoff_source_path,
        files_root=files_root,
    )
    if mismatch is not None:
        findings.append(mismatch)
    return findings
