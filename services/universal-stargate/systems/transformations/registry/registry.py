"""Transformation function registry."""

from collections.abc import Callable
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

# Type alias for transformation functions
TransformFunc = Callable[[list[dict[str, str]], dict[str, Any]], str]


class TransformationRegistry:
    """
    Registry for transformation functions.

    Maps transformation types to their handler functions, enabling clean
    dispatch without conditional logic in the request pipeline.
    """

    def __init__(self) -> None:
        self._registry: dict[str, TransformFunc] = {}

    def register(self, transformation_type: str, func: TransformFunc) -> None:
        """
        Register a transformation function.

        Args:
            transformation_type: Unique identifier for this transformation
            func: Transformation function with signature (messages, settings) -> str
        """
        if transformation_type in self._registry:
            logger.warning(f"Overwriting transformation type: {transformation_type}")
        self._registry[transformation_type] = func
        logger.debug(
            f"Registered transformation: {transformation_type} -> {func.__name__}"
        )

    def get(self, transformation_type: str) -> TransformFunc | None:
        """
        Get a transformation function by type.

        Args:
            transformation_type: Transformation type identifier

        Returns:
            Transformation function or None if not found
        """
        return self._registry.get(transformation_type)

    def has(self, transformation_type: str) -> bool:
        """Check if a transformation type is registered."""
        return transformation_type in self._registry

    def list_types(self) -> list[str]:
        """List all registered transformation types."""
        return list(self._registry.keys())


def create_default_registry() -> TransformationRegistry:
    """
    Create a new TransformationRegistry with built-in transformations.

    Returns:
        TransformationRegistry with built-in transformations registered
    """
    from ..implementations.cursorcore import format_cursorcore_prompt

    registry = TransformationRegistry()
    registry.register("cursorcore", format_cursorcore_prompt)
    logger.debug("Created default registry with built-in transformations")
    return registry
