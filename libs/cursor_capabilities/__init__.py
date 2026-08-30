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
    effective_knobs,
    effort_knob_name,
    is_cursor_model_denied,
    suggest_effort_knobs,
    supported_knobs,
    to_model_card_dict,
)

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ('git_integration_worker', 'stargate')

__all__ = [
    "CURSOR_DENIED_MODELS",
    "CURSOR_MODEL_CAPABILITIES",
    "DESCRIPTOR_VERSION",
    "KnobSpec",
    "ModelCapability",
    "canonical_cursor_bare_id",
    "catalog_divergences",
    "default_variant",
    "effective_knobs",
    "effort_knob_name",
    "is_cursor_model_denied",
    "suggest_effort_knobs",
    "supported_knobs",
    "to_model_card_dict",
]
