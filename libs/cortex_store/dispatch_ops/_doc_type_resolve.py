"""Doc-type registry resolution — base keys, variant suffix overlay merge."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

ALLOWED_VARIANT_OVERRIDE_FIELDS: frozenset[str] = frozenset(
    {"pedagogy_digest", "metadata", "builder"}
)


@dataclass(frozen=True, slots=True)
class DocTypeRecord:
    builder: Callable[[], str]
    schema: Any
    validator: Callable[..., Any]
    pedagogy_digest: str
    template_version: str
    variants: dict[str, dict[str, Any]] | None = None
    preflight: Callable[..., Any] | None = None
    side_effect_binding: str | None = None
    metadata: dict[str, Any] | None = None
    required_sections: list[str] | None = None
    skill_slugs: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class ResolvedDocType:
    record: DocTypeRecord
    base_key: str
    variant: str | None
    requested: str


def _apply_variant_overlay(
    base: DocTypeRecord,
    overlay: dict[str, Any],
) -> DocTypeRecord:
    updates: dict[str, Any] = {}
    for key, value in overlay.items():
        if key not in ALLOWED_VARIANT_OVERRIDE_FIELDS:
            continue
        updates[key] = value
    if not updates:
        return base
    return replace(base, **updates)


def resolve_doc_type(
    doc_type: str,
    registry: dict[str, DocTypeRecord],
) -> ResolvedDocType | None:
    """Resolve doc_type with optional ``:variant`` suffix overlay merge."""
    normalized = (doc_type or "").strip()
    if not normalized:
        return None

    if ":" in normalized:
        base_key, variant = normalized.rsplit(":", 1)
        base_record = registry.get(base_key)
        if base_record is not None and base_record.variants:
            overlay = base_record.variants.get(variant)
            if overlay is not None:
                merged = _apply_variant_overlay(base_record, overlay)
                return ResolvedDocType(
                    record=merged,
                    base_key=base_key,
                    variant=variant,
                    requested=normalized,
                )

    record = registry.get(normalized)
    if record is None:
        return None
    return ResolvedDocType(
        record=record,
        base_key=normalized,
        variant=None,
        requested=normalized,
    )


__all__ = [
    "ALLOWED_VARIANT_OVERRIDE_FIELDS",
    "DocTypeRecord",
    "ResolvedDocType",
    "resolve_doc_type",
]
