"""Normalize entity source_uri at the shared CRUD write boundary.

Promotes ``attributes.source_uri`` into the canonical ``entities.source_uri``
column and strips the reserved nested key. Scheme-agnostic — values are opaque.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RESERVED_SOURCE_URI_KEY = "source_uri"


class EntitySourceUriConflictError(ValueError):
    """Top-level and nested source_uri disagree or explicit clear conflicts."""


@dataclass(frozen=True)
class NormalizedSourceUri:
    attributes: dict[str, Any] | None
    source_uri: str | None
    promoted: bool


def is_blank_source_uri(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def nested_source_uri(attributes: dict[str, Any] | None) -> str | None:
    if not attributes or RESERVED_SOURCE_URI_KEY not in attributes:
        return None
    raw = attributes[RESERVED_SOURCE_URI_KEY]
    if is_blank_source_uri(raw):
        return None
    return str(raw)


def strip_reserved_source_uri(
    attributes: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if attributes is None:
        return None
    if RESERVED_SOURCE_URI_KEY not in attributes:
        return attributes
    return {k: v for k, v in attributes.items() if k != RESERVED_SOURCE_URI_KEY}


def normalize_create_source_uri(
    *,
    attributes: dict[str, Any] | None,
    source_uri: str | None,
    source_uri_supplied: bool,
) -> NormalizedSourceUri:
    """Apply create-time promotion/strip rules after Pydantic validation."""
    nested = nested_source_uri(attributes)
    if nested is not None:
        if source_uri_supplied and not is_blank_source_uri(source_uri):
            if source_uri == nested:
                return NormalizedSourceUri(
                    strip_reserved_source_uri(attributes),
                    source_uri,
                    promoted=False,
                )
            raise EntitySourceUriConflictError("canonical and nested source_uri differ")
        if source_uri_supplied and is_blank_source_uri(source_uri):
            raise EntitySourceUriConflictError(
                "explicit null/empty canonical source_uri with non-empty nested"
            )
        return NormalizedSourceUri(
            strip_reserved_source_uri(attributes),
            nested,
            promoted=True,
        )
    final = source_uri if source_uri_supplied else None
    return NormalizedSourceUri(
        strip_reserved_source_uri(attributes),
        final,
        promoted=False,
    )


def normalize_update_source_uri(
    *,
    prior_attributes: dict[str, Any] | None,
    prior_source_uri: str | None,
    updates: dict[str, object],
) -> tuple[dict[str, object], bool]:
    """Normalize an update payload after shallow attribute merge.

    Returns the mutated ``updates`` dict and whether canonical source_uri was
    promoted from nested attributes.
    """
    attributes_touched = "attributes" in updates
    source_uri_touched = "source_uri" in updates
    if not attributes_touched and not source_uri_touched:
        return updates, False

    if attributes_touched:
        raw_attrs = updates["attributes"]
        if raw_attrs is None:
            effective_attrs: dict[str, Any] | None = None
        elif isinstance(raw_attrs, dict):
            base = dict(prior_attributes or {})
            base.update(raw_attrs)
            effective_attrs = base
        else:
            effective_attrs = (
                dict(prior_attributes) if prior_attributes is not None else None
            )
    else:
        effective_attrs = (
            dict(prior_attributes) if prior_attributes is not None else None
        )

    if attributes_touched and updates.get("attributes") is None:
        # Explicit attributes clear — never promote from discarded prior blob.
        stripped = None
        final_canonical = (
            updates["source_uri"] if source_uri_touched else prior_source_uri
        )
        out = dict(updates)
        out["attributes"] = stripped
        if source_uri_touched or final_canonical != prior_source_uri:
            out["source_uri"] = final_canonical
        return out, False

    nested = nested_source_uri(
        effective_attrs if isinstance(effective_attrs, dict) else None
    )
    effective_canonical = (
        updates["source_uri"] if source_uri_touched else prior_source_uri
    )

    promoted = False
    final_canonical: str | None
    if nested is not None:
        if source_uri_touched and not is_blank_source_uri(effective_canonical):
            if effective_canonical != nested:
                raise EntitySourceUriConflictError(
                    "canonical and nested source_uri differ"
                )
            final_canonical = effective_canonical
        elif source_uri_touched and is_blank_source_uri(effective_canonical):
            raise EntitySourceUriConflictError(
                "explicit null/empty canonical source_uri with non-empty nested"
            )
        elif is_blank_source_uri(effective_canonical):
            final_canonical = nested
            promoted = True
        elif effective_canonical == nested:
            final_canonical = effective_canonical
        else:
            raise EntitySourceUriConflictError("canonical and nested source_uri differ")
    else:
        final_canonical = (
            effective_canonical if source_uri_touched else prior_source_uri
        )

    stripped_attrs = strip_reserved_source_uri(
        effective_attrs if isinstance(effective_attrs, dict) else None
    )
    out = dict(updates)
    if attributes_touched:
        out["attributes"] = stripped_attrs
    elif stripped_attrs != prior_attributes:
        out["attributes"] = stripped_attrs
    if promoted or source_uri_touched or final_canonical != prior_source_uri:
        out["source_uri"] = final_canonical
    return out, promoted


def stranded_nested_source_uri(
    attributes: object,
    canonical_source_uri: object,
) -> bool:
    """True when nested source_uri exists but canonical column is blank."""
    if not isinstance(attributes, dict):
        return False
    nested = nested_source_uri(attributes)
    if nested is None:
        return False
    return is_blank_source_uri(canonical_source_uri)
