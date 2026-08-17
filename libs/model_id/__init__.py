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
    AGENT_SUBSTRATE_PROVIDERS,
    KNOWN_CLOUD_PROVIDERS,
    SubstrateCapabilityUnimplementedError,
    WireModelResolution,
    WireModelResolutionError,
    require_cloud_api_backend,
    require_cloud_provider,
    resolve_wire_model_id,
)

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = (
    'cloud_proxy',
    'gateway',
    'git_integration_worker',
    'mcp',
    'rag',
    'stargate',
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
    "AGENT_SUBSTRATE_PROVIDERS",
    "KNOWN_CLOUD_PROVIDERS",
    "SubstrateCapabilityUnimplementedError",
    "WireModelResolution",
    "WireModelResolutionError",
    "require_cloud_api_backend",
    "require_cloud_provider",
    "resolve_wire_model_id",
]
