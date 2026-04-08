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

from .model_id import ModelId, get_compute_type, parse_model_id
from .validation import validate_model_id, validate_model_id_strict

__all__ = [
    "ModelId",
    "parse_model_id",
    "get_compute_type",
    "validate_model_id",
    "validate_model_id_strict",
]
