"""Shared skill/rule INDEX envelope helpers (source_uri + body digest)."""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import HTTPException

LAYER_ENTITY_TYPES: dict[str, tuple[str, ...]] = {
    "skills": ("agent_skill",),
    "rules": ("rule",),
    "all": ("agent_skill", "rule"),
}


def entity_types_for_layer(layer: str) -> tuple[str, ...]:
    """Map a discovery layer to the fixed entity-type allowlist (422 on unknown)."""
    types = LAYER_ENTITY_TYPES.get(layer)
    if types is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown layer {layer!r}; expected one of "
                f"{sorted(LAYER_ENTITY_TYPES)}."
            ),
        )
    return types


def slug_from_row(row: dict[str, Any]) -> str:
    """Resolve manifest slug from an entity row."""
    name = str(row.get("name") or "").strip()
    if name:
        return name
    entity_id = str(row.get("id") or "")
    if ":" in entity_id:
        return entity_id.split(":", 1)[1]
    return entity_id


def content_digest(data: bytes) -> str:
    """SHA-256 digest prefix shared by route body resolution and ingest projection."""
    return f"sha256:{hashlib.sha256(data).hexdigest()[:16]}"


def body_digest(source_uri: str | None, slug: str) -> str | None:
    """Content digest of the resolved skill/rule body for the INDEX envelope."""
    from .boot._skill_trigger import _resolve_skill_file

    path = _resolve_skill_file(source_uri, slug)
    if path is None:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return content_digest(data)


def index_envelope_fields(row: dict[str, Any]) -> dict[str, str | None]:
    """Return ``source_uri`` and ``digest`` for a skill/rule manifest/boot row."""
    slug = slug_from_row(row)
    source_uri = row.get("source_uri")
    return {
        "source_uri": source_uri,
        "digest": body_digest(source_uri, slug),
    }
