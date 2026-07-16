"""CRUD integration for canonical entity source URI normalization."""

from __future__ import annotations

from fastapi import HTTPException, status

from .entity_source_uri import (
    EntitySourceUriConflictError,
    normalize_create_source_uri,
    normalize_update_source_uri,
)
from .models import EntityCreate


def _conflict_http(exc: EntitySourceUriConflictError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "error": "source_uri_conflict",
            "message": str(exc),
        },
    )


def _compute_content_hash(source_uri: str) -> str | None:
    from .dispatch_ops._shared import _compute_content_hash as compute

    return compute(source_uri)


def normalize_create_write(
    body: EntityCreate,
    *,
    source_uri_supplied: bool,
    content_hash_supplied: bool,
) -> EntityCreate:
    """Normalize a validated create body and compute its derived hash."""
    try:
        normalized = normalize_create_source_uri(
            attributes=dict(body.attributes) if body.attributes is not None else None,
            source_uri=body.source_uri,
            source_uri_supplied=source_uri_supplied,
        )
    except EntitySourceUriConflictError as exc:
        raise _conflict_http(exc) from exc

    patch: dict[str, object] = {
        "attributes": normalized.attributes,
        "source_uri": normalized.source_uri,
    }
    if not content_hash_supplied and normalized.source_uri:
        content_hash = _compute_content_hash(normalized.source_uri)
        if content_hash:
            patch["content_hash"] = content_hash
    return body.model_copy(update=patch)


def normalize_update_write(
    *,
    prior: dict[str, object],
    updates: dict[str, object],
) -> dict[str, object]:
    """Normalize update fields and compute a hash after URI promotion."""
    prior_attributes = prior.get("attributes")
    try:
        normalized_updates, promoted = normalize_update_source_uri(
            prior_attributes=(
                prior_attributes if isinstance(prior_attributes, dict) else None
            ),
            prior_source_uri=(
                str(prior["source_uri"])
                if prior.get("source_uri") is not None
                else None
            ),
            updates=updates,
        )
    except EntitySourceUriConflictError as exc:
        raise _conflict_http(exc) from exc

    if promoted or (
        "source_uri" in normalized_updates and "content_hash" not in normalized_updates
    ):
        source_uri = normalized_updates.get("source_uri")
        if isinstance(source_uri, str) and source_uri.strip():
            content_hash = _compute_content_hash(source_uri)
            if content_hash:
                normalized_updates["content_hash"] = content_hash
    return normalized_updates
