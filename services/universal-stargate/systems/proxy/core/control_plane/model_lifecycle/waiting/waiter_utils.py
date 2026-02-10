"""
Utility functions for ModelLoadWaiter.

Extracted to keep waiter.py under 400 SLOC limit.
"""

from model_id import ModelId
from universal_logging import get_logger

logger = get_logger(__name__)


def normalize_model_id_for_events(model_id: str) -> str:
    """
    Normalize model_id for consistent matching (strips -hybrid only).

    Event keys use routing keys (strips -hybrid) to prevent cross-variant
    wakeup bugs where different context lengths wake each other up.

    Args:
        model_id: Original model ID (e.g., "model-q8-0-hybrid-2048")

    Returns:
        Event key for matching (e.g., "model-q8-0-2048")
    """
    parsed = ModelId.parse(model_id)
    return parsed.routing_key
