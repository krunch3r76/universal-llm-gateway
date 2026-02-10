"""Model metadata cache and middleware decision helpers."""

import asyncio
from typing import Any

from universal_logging import get_logger

from .config import ModelMetadata

logger = get_logger(__name__)


class ModelCache:
    """Model metadata cache with TTL."""

    def __init__(self, ttl: float = 300.0):
        self._cache: dict[str, ModelMetadata] = {}
        self._timestamp: float = 0
        self._ttl: float = ttl

    def get(self, model_id: str) -> ModelMetadata | None:
        """Get model metadata from cache."""
        current_time = asyncio.get_event_loop().time()
        if current_time - self._timestamp > self._ttl:
            logger.debug("Model cache expired, returning stale data")

        return self._cache.get(model_id)

    def refresh(self, models: dict[str, ModelMetadata]) -> None:
        """Update cache with new models."""
        self._cache = models
        self._timestamp = asyncio.get_event_loop().time()

    def clear(self) -> None:
        """Clear the cache."""
        self._cache = {}
        self._timestamp = 0

    @property
    def size(self) -> int:
        """Get number of cached models."""
        return len(self._cache)


def should_apply_middleware_for_metadata(metadata: dict[str, Any]) -> bool:
    """Determine if middleware should be applied based on metadata.

    Args:
        metadata: Model metadata dictionary

    Returns:
        True if middleware should be applied, False otherwise
    """
    if not metadata:
        return False

    middleware_config = metadata.get("middleware_config", {})
    preserve_personality = middleware_config.get("preserve_personality", False)
    input_schema = metadata.get("input_schema", "prompt")
    model_type = metadata.get("model_type", "default")

    should_apply = (
        preserve_personality and input_schema != "messages" and model_type != "default"
    )

    return should_apply
