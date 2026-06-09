"""Resolve optional ``source_ref`` for ``session_close`` stamping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from implement_admission.source_ref import SourceRefError, parse_source_ref

from .source_ref_provenance import build_source_ref_provenance


@dataclass(frozen=True, slots=True)
class SourceRefCloseResolution:
    """Outcome of canonicalizing a close-time ``source_ref``."""

    external_ref: str
    stamped_ref: str
    provenance: dict[str, Any]
    unparseable: bool


def resolve_source_ref_for_close(
    raw: str | None,
    *,
    derivation: str | None,
    captured_at: str,
) -> SourceRefCloseResolution | None:
    """Canonicalize *raw* via ``parse_source_ref``; never raise on bad grammar."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        parsed = parse_source_ref(text)
    except SourceRefError:
        provenance = build_source_ref_provenance(
            external_ref=text,
            canonical_ref=None,
            source_kind=None,
            parent_ref=None,
            selector=None,
            derivation=derivation,
            captured_at=captured_at,
            unparseable=True,
        )
        return SourceRefCloseResolution(
            external_ref=text,
            stamped_ref=text,
            provenance=provenance,
            unparseable=True,
        )
    provenance = build_source_ref_provenance(
        external_ref=parsed.external_ref,
        canonical_ref=parsed.canonical_ref,
        source_kind=parsed.source_kind,
        parent_ref=parsed.parent_ref,
        selector=parsed.selector,
        derivation=derivation,
        captured_at=captured_at,
    )
    return SourceRefCloseResolution(
        external_ref=parsed.external_ref,
        stamped_ref=parsed.canonical_ref,
        provenance=provenance,
        unparseable=False,
    )


def source_ref_depth_advisory(
    *,
    transcript_depth: str,
    has_source_ref: bool,
) -> dict[str, str] | None:
    """Advisory when ``source_ref`` is present at ``depth=none`` (no hard gate)."""
    if not has_source_ref or transcript_depth != "none":
        return None
    return {
        "kind": "source_ref_depth_advisory",
        "message": (
            "source_ref present; recommend transcript_depth >= light for "
            "transcript attribute stamping"
        ),
    }
