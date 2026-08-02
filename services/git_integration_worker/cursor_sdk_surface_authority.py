"""Authority class + absence semantics labeling for effects_manifest surfaces (AC-9e)."""

from __future__ import annotations

from implement_admission.closeout_models import (
    AbsenceSemantics,
    AuthorityClass,
    EffectsManifest,
    SurfaceSection,
)

_OBSERVED_SOURCES = frozenset({"stream", "artifacts", "mcp_events"})
_LEDGER_SOURCES = frozenset({"git", "ledger", "commit_ack"})
_SELF_SOURCES = frozenset({"conversation", "capture", "wrapper"})

_SURFACE_DEFAULT: tuple[AuthorityClass, AbsenceSemantics] = (
    "self_reported",
    "absence=unknown",
)


def authority_for_surface_source(
    surface: str,
    source: str,
    *,
    entry_count: int = 0,
) -> tuple[AuthorityClass, AbsenceSemantics]:
    """Map one surface section's capture source to authority + absence semantics."""
    src = (source or "").lower()
    if src in _LEDGER_SOURCES:
        return ("ledger_attested", "absence=zero")
    if src in _OBSERVED_SOURCES:
        return ("observed", "absence=zero")
    if surface == "subagents" and entry_count == 0 and src in {"capture", "stream"}:
        return ("observed", "absence=zero")
    if src in _SELF_SOURCES:
        return ("self_reported", "absence=unknown")
    if src in _OBSERVED_SOURCES | _LEDGER_SOURCES:
        return ("observed", "absence=zero")
    return _SURFACE_DEFAULT


def _merge_authority(
    left: tuple[AuthorityClass, AbsenceSemantics],
    right: tuple[AuthorityClass, AbsenceSemantics],
) -> tuple[AuthorityClass, AbsenceSemantics]:
    """Weakest authority wins; absence=unknown if either side is unknown."""
    rank = {"ledger_attested": 0, "observed": 1, "self_reported": 2}
    weaker = left if rank[left[0]] >= rank[right[0]] else right
    absence: AbsenceSemantics = (
        "absence=unknown"
        if left[1] == "absence=unknown" or right[1] == "absence=unknown"
        else "absence=zero"
    )
    return (weaker[0], absence)


def label_surface_section(section: SurfaceSection) -> SurfaceSection:
    """Apply authority labels to one surface section."""
    auth = authority_for_surface_source(
        section.surface,
        section.source,
        entry_count=len(section.entries),
    )
    if section.cross_check and "mixed_authority" in (section.cross_check or ""):
        auth = _merge_authority(auth, ("self_reported", "absence=unknown"))
    return section.model_copy(
        update={"authority_class": auth[0], "absence_semantics": auth[1]}
    )


def label_manifest_authority(manifest: EffectsManifest | None) -> EffectsManifest | None:
    """Label every emitted surface — flat undifferentiated merge is a defect (AC-9e)."""
    if manifest is None:
        return None
    labeled: dict[str, SurfaceSection] = {}
    for name, section in manifest.surfaces.items():
        labeled[name] = label_surface_section(section)
    return manifest.model_copy(update={"surfaces": labeled})


def mixed_source_cross_check(existing: SurfaceSection, incoming_source: str) -> str | None:
    """Flag when fold-in would mix authority classes on one surface."""
    existing_auth = authority_for_surface_source(
        existing.surface, existing.source, entry_count=len(existing.entries)
    )
    incoming_auth = authority_for_surface_source(
        existing.surface, incoming_source, entry_count=0
    )
    if existing_auth[0] != incoming_auth[0]:
        return "mixed_authority:partition_required"
    return existing.cross_check


__all__ = [
    "authority_for_surface_source",
    "label_manifest_authority",
    "label_surface_section",
    "mixed_source_cross_check",
]
