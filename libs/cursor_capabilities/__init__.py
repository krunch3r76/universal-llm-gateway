"""Cursor model capability descriptor shared by Stargate and git_integration_worker."""

from .cursor_capabilities import (
    CURSOR_MODEL_CAPABILITIES,
    DESCRIPTOR_VERSION,
    KnobSpec,
    ModelCapability,
    default_variant,
    supported_knobs,
    to_model_card_dict,
)

__all__ = [
    "CURSOR_MODEL_CAPABILITIES",
    "DESCRIPTOR_VERSION",
    "KnobSpec",
    "ModelCapability",
    "default_variant",
    "supported_knobs",
    "to_model_card_dict",
]
