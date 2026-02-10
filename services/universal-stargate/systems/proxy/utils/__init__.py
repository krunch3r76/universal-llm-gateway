"""Utility modules for the proxy layer"""

# model_id_utils removed - use model_id library instead
from model_id import ModelId, validate_model_id

from .gateway_config import (
    _is_valid_url,
    _normalize_gateway_config,
    _normalize_gateway_configs,
)

# Export public APIs and utility functions used by package root
__all__ = [
    "ModelId",
    "validate_model_id",
    "_is_valid_url",
    "_normalize_gateway_config",
    "_normalize_gateway_configs",
]
