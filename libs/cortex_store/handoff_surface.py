"""Handoff surface projection (agent-bus 1188, surface-but-flag).

Read side: surfaces ``handoff_prompt`` on entity reads with an explicit trust
block so ``source_file:null`` / legacy detached rows are visible but flagged —
not silently treated as file-backed. Write side: ``build_handoff_surface_preview``
reuses the same builder so ``session_close`` can warn the writer at close time
when a supplied handoff will read back as unverified.
"""

from __future__ import annotations

from typing import Any

from .handoff_derivation import (
    DERIVATION_AUTO_PERSISTED,
    DERIVATION_DETACHED_STRING,
    DERIVATION_SECTION,
    DERIVATION_SECTION_AMBIGUOUS,
    DERIVATION_SECTION_UNRESOLVED,
)
from .models import ActionHint

_FLAG_UNVERIFIED = "unverified"
_FLAG_INVALID = "invalid"


def effective_handoff_derivation(provenance: dict[str, Any] | None) -> str:
    """Resolve derivation for read projection; pre-2-A rows → detached_string."""
    if not provenance:
        return DERIVATION_DETACHED_STRING
    raw = provenance.get("derivation")
    if raw in (
        DERIVATION_SECTION,
        DERIVATION_SECTION_UNRESOLVED,
        DERIVATION_SECTION_AMBIGUOUS,
        DERIVATION_DETACHED_STRING,
        DERIVATION_AUTO_PERSISTED,
    ):
        return str(raw)
    return DERIVATION_DETACHED_STRING


def _flag_reason(
    *,
    derivation: str,
    source_file: str | None,
    provenance_missing: bool,
) -> str:
    if provenance_missing:
        return (
            "Legacy handoff without handoff_provenance on the transcript "
            "attribute — treated as detached_string (surface-but-flag)."
        )
    if derivation == DERIVATION_SECTION_AMBIGUOUS:
        return (
            "Marker region was ambiguous in the source file at close time; "
            "handoff_prompt was not stored — do not treat as authoritative."
        )
    if derivation == DERIVATION_SECTION_UNRESOLVED:
        return (
            "Marker region could not be resolved in the source file at close "
            "time; handoff_prompt was not stored — do not treat as authoritative."
        )
    if source_file is None:
        return (
            "handoff_provenance.source_file is null — prompt was not derived "
            "from a lead-authored .md file (upserted or bled-through string)."
        )
    if derivation == DERIVATION_DETACHED_STRING:
        return (
            "Handoff was stored as detached_string without a file-backed "
            "marker extraction at close time."
        )
    if derivation == DERIVATION_AUTO_PERSISTED:
        return (
            "Handoff was auto-persisted from the inline close string — "
            "file-backed for reload/tamper-detection, not independently "
            "lead-authored marker extraction."
        )
    return "Handoff is not file-backed and marker-verified."


def build_handoff_surface(attributes: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build read-only ``handoff_surface`` when a handoff prompt is present."""
    if not attributes:
        return None
    prompt = attributes.get("handoff_prompt")
    if prompt is None or not str(prompt).strip():
        return None

    prov = attributes.get("handoff_provenance")
    provenance_missing = not isinstance(prov, dict)
    provenance = prov if isinstance(prov, dict) else {}
    derivation = effective_handoff_derivation(
        provenance if not provenance_missing else None
    )
    source_file = provenance.get("source_file")

    verified = (
        not provenance_missing
        and source_file is not None
        and derivation == DERIVATION_SECTION
    )
    invalid = derivation in (
        DERIVATION_SECTION_UNRESOLVED,
        DERIVATION_SECTION_AMBIGUOUS,
    )

    surface: dict[str, Any] = {
        "surfaced": True,
        "verified": verified,
        "derivation": derivation,
    }
    if source_file is not None:
        surface["source_file"] = source_file
    if provenance.get("source_file_sha256"):
        surface["source_file_sha256"] = provenance["source_file_sha256"]

    verification = attributes.get("handoff_verification")
    if isinstance(verification, dict):
        surface["handoff_verification"] = verification

    if not verified:
        surface["flag"] = _FLAG_INVALID if invalid else _FLAG_UNVERIFIED
        surface["reason"] = _flag_reason(
            derivation=derivation,
            source_file=source_file,
            provenance_missing=provenance_missing,
        )
    return surface


def build_handoff_surface_preview(
    handoff_prompt: str | None,
    provenance: dict[str, Any] | None,
    handoff_verification: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Write-time mirror of the read-side ``handoff_surface``.

    Given the ``(handoff_prompt, handoff_provenance)`` a close is about to
    persist, return the same trust block a later ``entity_get`` reader will
    compute via ``build_handoff_surface`` — but only when it is *not* verified.
    This lets ``session_close`` tell the writer at close time that an inline
    ``handoff_prompt`` with no ``handoff_source_path`` will read back as
    ``unverified``. Returns ``None`` on the verified path and when no handoff
    prompt is present, so the verified path carries no advisory.

    The unverified classification is unchanged — this reuses the read-side
    builder rather than re-deriving the flag.
    """
    attrs: dict[str, Any] = {
        "handoff_prompt": handoff_prompt,
        "handoff_provenance": provenance,
    }
    if handoff_verification is not None:
        attrs["handoff_verification"] = handoff_verification
    surface = build_handoff_surface(attrs)
    if surface is None or surface.get("verified"):
        return None
    verification = surface.get("handoff_verification")
    if isinstance(verification, dict):
        passed = int(verification.get("passed", 0))
        total = int(verification.get("total", 0))
        if total > 0 and passed >= total:
            return None
    return surface


def handoff_surface_action_hints(
    entity_id: str,
    surface: dict[str, Any],
) -> list[ActionHint]:
    """Action hints for unverified / invalid handoff surfaces."""
    if surface.get("verified"):
        return []
    flag = surface.get("flag", _FLAG_UNVERIFIED)
    derivation = surface.get("derivation", DERIVATION_DETACHED_STRING)
    reason = surface.get("reason", "")
    return [
        ActionHint(
            category="handoff_unverified",
            entity_id=entity_id,
            message=(
                f"Handoff on {entity_id} is {flag} (derivation={derivation}). {reason}"
            ),
            action=(
                "Treat handoff_prompt as non-authoritative until operator "
                "confirms; prefer re-loading from handoff_source_path when set."
            ),
        )
    ]


def apply_handoff_read_projection(
    row: dict[str, object],
    *,
    existing_hints: list[ActionHint] | None = None,
) -> tuple[dict[str, object], list[ActionHint] | None]:
    """Enrich entity read row: ``attributes.handoff_surface`` + action hints."""
    attrs = row.get("attributes")
    if not isinstance(attrs, dict):
        return row, existing_hints

    surface = build_handoff_surface(attrs)
    if surface is None:
        return row, existing_hints

    out = dict(row)
    merged_attrs = dict(attrs)
    merged_attrs["handoff_surface"] = surface
    out["attributes"] = merged_attrs

    entity_id = str(row.get("id", ""))
    hints = list(existing_hints or [])
    hints.extend(handoff_surface_action_hints(entity_id, surface))
    return out, hints or None
