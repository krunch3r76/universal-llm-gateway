"""Cortex model-entity normalization for provider and routing model ids."""

from __future__ import annotations

import re

from .model_id import ModelId

_MODEL_ENTITY_ALIASES: dict[str, str] = {
    # Keep this table conservative: only exact upstream aliases belong here.
    # Do not collapse same-family variants without evidence.
}
_UNSAFE_ENTITY_CHARS = re.compile(r"[^a-z0-9._-]+")


def canonical_model_slug(model_id: str | ModelId) -> str:
    """Return the Cortex ``model:`` slug for a provider/wire model id."""
    parsed = ModelId.parse(model_id)
    identity = parsed.api_model_id
    if identity.startswith("models/"):
        identity = identity.removeprefix("models/")
    if "/" in identity:
        identity = identity.rsplit("/", 1)[-1]
    slug = identity.strip().lower()
    slug = _MODEL_ENTITY_ALIASES.get(slug, slug)
    slug = _UNSAFE_ENTITY_CHARS.sub("-", slug).strip("-")
    if not slug:
        raise ValueError(f"Could not derive model entity slug from {model_id!r}")
    return slug


def canonical_model_entity_id(model_id: str | ModelId) -> str:
    """Return the canonical Cortex model entity id for a model id."""
    return f"model:{canonical_model_slug(model_id)}"
