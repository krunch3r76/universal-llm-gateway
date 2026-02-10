"""
Sticky routing resolution.

Resolves whether a model should use sticky routing (single gateway affinity)
based on Stargate configuration overrides and defaults.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from model_id import ModelId
from universal_logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


def resolve_model_sticky(model_id: ModelId, config: Any) -> bool:
    """
    Resolve sticky routing policy for a model.

    Invariant: Stargate config is the source of truth (default True).

    Model ID matching handles normalization:
    - Config keys may be in original format
      (e.g., "Qwen2.5-Coder-14B-Instruct-Q8_0-2048")
    - Request model IDs are normalized
      (e.g., "qwen2-5-coder-14b-instruct-q8-0-2048")
    - Try both exact match and normalized match for compatibility

    Args:
        model_id: Model identifier object
        config: Stargate configuration object (or None)

    Returns:
        True if model should use sticky routing, False otherwise
    """
    # Convert to string only for dict key lookup
    model_id_str = str(model_id)

    if not config:
        return True

    model_routing = config.get_model_routing_config()
    overrides = model_routing.get("sticky_overrides", {}) or {}

    # Try exact match first
    if model_id_str in overrides:
        return bool(overrides[model_id_str])

    # Try normalized match (pass ModelId object)
    normalized_result = _try_normalized_match(model_id, overrides)
    if normalized_result is not None:
        return normalized_result

    return bool(model_routing.get("default_sticky", True))


def _normalize_for_comparison(s: str) -> str:
    """Normalize string for comparison: lowercase, dots/underscores -> hyphens."""
    return s.lower().replace(".", "-").replace("_", "-")


def _try_normalized_match(model_id: ModelId, overrides: dict[str, bool]) -> bool | None:
    """
    Try to match model_id against overrides using normalized comparison.

    Returns:
        Sticky value if match found, None otherwise
    """
    # Remove try/except ModelId.parse - caller now passes ModelId
    normalized_routing_key = _normalize_for_comparison(model_id.routing_key)

    # Normalize all config keys and check for match
    for config_key, sticky_value in overrides.items():
        try:
            parsed_key = ModelId.parse(config_key)
            config_routing_key = _normalize_for_comparison(parsed_key.routing_key)

            # Match on normalized routing_key (handles format differences)
            if normalized_routing_key == config_routing_key:
                logger.info(
                    f"✅ Sticky override matched: {model_id} "
                    f"(routing_key={model_id.routing_key}) -> {config_key} "
                    f"(routing_key={parsed_key.routing_key}) = {sticky_value}"
                )
                return bool(sticky_value)
        except ValueError:
            # Config key might not be a valid model ID format, skip
            continue

    return None
