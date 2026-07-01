"""Cursor model capability descriptor shared by Stargate and git_integration_worker."""

from .cursor_capabilities import (
    CURSOR_DENIED_MODELS,
    CURSOR_MODEL_CAPABILITIES,
    DESCRIPTOR_VERSION,
    KnobSpec,
    ModelCapability,
    canonical_cursor_bare_id,
    catalog_divergences,
    default_variant,
    is_cursor_model_denied,
    supported_knobs,
    to_model_card_dict,
)

__all__ = [
    "CURSOR_DENIED_MODELS",
    "CURSOR_MODEL_CAPABILITIES",
    "DESCRIPTOR_VERSION",
    "KnobSpec",
    "ModelCapability",
    "canonical_cursor_bare_id",
    "catalog_divergences",
    "default_variant",
    "is_cursor_model_denied",
    "supported_knobs",
    "to_model_card_dict",
]
