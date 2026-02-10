"""
Resource requirement protocols and configuration.

Provides type-safe interface for resource lookups.
"""

from typing import Protocol, TypedDict

from model_id import ModelId


class ResourceRequirements(TypedDict, total=False):
    """
    Resource requirements for model inference.

    All fields optional to allow partial information.
    """

    vram_mb: int
    ram_mb: int
    is_gpu: bool


class ResourceProvider(Protocol):
    """
    Protocol for async resource requirement lookup.

    Implementations must support model_id lookup and return
    ResourceRequirements dict. Missing fields should be omitted
    rather than returning 0.
    """

    async def __call__(self, model_id: ModelId) -> ResourceRequirements:
        """
        Get resource requirements for model.

        Args:
            model_id: Model identifier to lookup

        Returns:
            ResourceRequirements with available information

        Raises:
            KeyError: Model not found in catalog
            ValueError: Invalid model_id format
        """
        ...


# Configuration
MAX_BATCH_SIZE = 50  # Maximum requests per batch (prevent resource exhaustion)
DEFAULT_VRAM_MB = 4096  # Conservative default when catalog lookup fails
DEFAULT_RAM_MB = 8192
