"""
Shared Model ID parsing and normalization library.

Used by both Gateway and Stargate for consistent model ID handling.

Examples:
    >>> from model_id import ModelId
    >>> m = ModelId.parse("hermes-16384-hybrid")
    >>> m.base_id           # "hermes"
    >>> m.context_length    # 16384
    >>> m.is_hybrid         # True
    >>> m.routing_key       # "hermes-16384" (strips -hybrid for routing)
    >>> m.normalized        # "hermes-16384" (strips -hybrid, keeps -cpu)
"""

from .entity import canonical_model_entity_id, canonical_model_slug
from .model_id import (
    ModelId,
    get_compute_type,
    infer_cloud_provider_from_bare,
    parse_model_id,
)
from .validation import validate_model_id, validate_model_id_strict
from .wire_resolve import (
    KNOWN_CLOUD_PROVIDERS,
    WireModelResolution,
    WireModelResolutionError,
    require_cloud_provider,
    resolve_wire_model_id,
)

__all__ = [
    "ModelId",
    "parse_model_id",
    "get_compute_type",
    "infer_cloud_provider_from_bare",
    "canonical_model_slug",
    "canonical_model_entity_id",
    "validate_model_id",
    "validate_model_id_strict",
    "KNOWN_CLOUD_PROVIDERS",
    "WireModelResolution",
    "WireModelResolutionError",
    "require_cloud_provider",
    "resolve_wire_model_id",
]
