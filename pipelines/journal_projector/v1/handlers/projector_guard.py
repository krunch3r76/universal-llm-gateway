"""Model guard for journal-projector frontier dispatch (acceptance 6)."""

from __future__ import annotations

from cursor_capabilities.model_pools import is_other_models_pool
from model_id import ModelId


def reject_projector_wire_model(model: str) -> None:
    """Raise when *model* must not run on journal-projector step 2."""
    wire = (model or "").strip()
    if not wire:
        raise ValueError("journal-projector requires a model id")
    parsed = ModelId.parse(wire)
    provider = (parsed.provider or "").lower()
    if provider == "anthropic":
        raise ValueError(f"anthropic models are not journal-projector substrates: {wire!r}")
    if is_other_models_pool(wire):
        raise ValueError(f"cursor/claude-* other-models pool rejected for projector: {wire!r}")
    bare = wire.split("/", 1)[-1] if "/" in wire else wire
    if bare == "composer-2.5-fast" or wire.endswith("/composer-2.5-fast"):
        raise ValueError(f"synthetic cursor id not in catalog: {wire!r}")
