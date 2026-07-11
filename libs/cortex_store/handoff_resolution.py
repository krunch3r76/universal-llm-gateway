"""2-A v2 handoff resolution for session close and upsert."""

from __future__ import annotations

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
    WRITE_PATH_SESSION_CLOSE,
)
from .handoff_inline_persist import build_inline_handoff_provenance
from .handoff_marker import extract_handoff_marker_region
from .handoff_paths import normalize_handoff_source_path, sha256_text
from .handoff_provenance import build_handoff_provenance
from .handoff_resolution_io import (
    read_handoff_source_file,
    validate_handoff_write_limits,
)
from .handoff_surface import build_handoff_surface_preview
from .handoff_verification import build_handoff_verification
from .session_close_validation import build_validation_error


@dataclass(frozen=True)
class HandoffResolution:
    """Resolved handoff state for close / upsert / dry_run preview."""

    handoff_prompt: str | None
    provenance: dict[str, Any] | None
    handoff_valid: bool
    findings: list[dict[str, Any]]
    derived_handoff_prompt: str | None
    handoff_verification: dict[str, Any] | None = None


def _handoff_finding(kind: str, subject: str, detail: str) -> dict[str, Any]:
    from .dispatch_ops._detectors._shared import _finding

    return _finding(kind, subject, detail)


_FINDING_TO_REASON: dict[str, str] = {
    "handoff_missing_transcript_anchor": "handoff.missing_transcript_anchor",
    "handoff_section_unresolved": "handoff.section_unresolved",
    "handoff_section_ambiguous": "handoff.section_ambiguous",
}


def handoff_failure_reason(findings: list[dict[str, Any]]) -> str | None:
    """Map the first matching handoff finding kind to a session-close reason."""
    for kind, reason in _FINDING_TO_REASON.items():
        if any(f.get("kind") == kind for f in findings):
            return reason
    if findings:
        return "handoff.invalid"
    return None


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


def _attach_handoff_verification(
    resolution: HandoffResolution,
    *,
    session_id: str | None,
    handoff_source_path: str | None,
    files_root: Path,
) -> HandoffResolution:
    if not session_id or not resolution.handoff_prompt:
        return resolution
    verification = build_handoff_verification(
        session_id=session_id,
        handoff_prompt=resolution.handoff_prompt,
        handoff_source_path=handoff_source_path,
        files_root=files_root,
    )
    return HandoffResolution(
        handoff_prompt=resolution.handoff_prompt,
        provenance=resolution.provenance,
        handoff_valid=resolution.handoff_valid,
        findings=resolution.findings,
        derived_handoff_prompt=resolution.derived_handoff_prompt,
        handoff_verification=verification,
    )


def resolve_handoff_for_write(
    *,
    files_root: Path,
    write_path: str,
    written_at: str,
    handoff_source_path: str | None,
    handoff_source_section: str | None,
    handoff_prompt: str | None,
    session_id: str | None = None,
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
            return _attach_handoff_verification(
                HandoffResolution(
                    handoff_prompt=None,
                    provenance=prov,
                    handoff_valid=False,
                    findings=findings,
                    derived_handoff_prompt=None,
                ),
                session_id=session_id,
                handoff_source_path=handoff_source_path,
                files_root=files_root,
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
            return _attach_handoff_verification(
                HandoffResolution(
                    handoff_prompt=None,
                    provenance=prov,
                    handoff_valid=False,
                    findings=findings,
                    derived_handoff_prompt=None,
                ),
                session_id=session_id,
                handoff_source_path=handoff_source_path,
                files_root=files_root,
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
        return _attach_handoff_verification(
            HandoffResolution(
                handoff_prompt=derived,
                provenance=prov,
                handoff_valid=True,
                findings=findings,
                derived_handoff_prompt=derived,
            ),
            session_id=session_id,
            handoff_source_path=handoff_source_path,
            files_root=files_root,
        )

    if prompt_in:
        prov = build_inline_handoff_provenance(
            files_root=files_root,
            session_id=session_id,
            prompt=prompt_in,
            write_path=write_path,
            written_at=written_at,
        )
        validate_handoff_write_limits(handoff_prompt=prompt_in, provenance=prov)
        return _attach_handoff_verification(
            HandoffResolution(
                handoff_prompt=prompt_in,
                provenance=prov,
                handoff_valid=True,
                findings=findings,
                derived_handoff_prompt=prompt_in,
            ),
            session_id=session_id,
            handoff_source_path=handoff_source_path,
            files_root=files_root,
        )

    return _attach_handoff_verification(
        HandoffResolution(
            handoff_prompt=None,
            provenance=None,
            handoff_valid=True,
            findings=findings,
            derived_handoff_prompt=None,
        ),
        session_id=session_id,
        handoff_source_path=handoff_source_path,
        files_root=files_root,
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
        session_id=session_id,
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
            handoff_prompt=resolution.handoff_prompt,
            handoff_source_path=handoff_source_path,
        )
        if anchor_finding is not None:
            findings = [*findings, anchor_finding]
            handoff_valid = False
    preview: dict[str, Any] = {
        "derived_handoff_prompt": resolution.derived_handoff_prompt,
        "handoff_provenance_preview": resolution.provenance,
        "handoff_surface_preview": build_handoff_surface_preview(
            resolution.handoff_prompt,
            resolution.provenance,
            resolution.handoff_verification,
        ),
        "handoff_valid": handoff_valid,
        "findings": findings,
    }
    if not handoff_valid:
        preview["reason"] = handoff_failure_reason(findings)
    return preview


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
