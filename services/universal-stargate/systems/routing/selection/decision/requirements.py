"""
Model requirements lookup for resource reservation.

Provides in-memory VRAM/RAM lookup for loading models from cached data.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from model_id import ModelId
from universal_logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


def create_requirements_lookup(
    gateway_model_details: dict[ModelId, dict[str, Any]],
) -> Callable[[ModelId], tuple[int, int]]:
    """
    Create a pure in-memory model requirements lookup function.

    Lookup order:
    1. gateway_model_details (WebSocket cache: vram_usage, ram_usage)
    2. Return (0, 0) if not found (caller enforces fail-fast)

    Args:
        gateway_model_details: Gateway's model_details dict (from WebSocket events)

    Returns:
        Function: ModelId -> (vram_mb, ram_mb)

    Invariant: ∀ ModelId, returns (int >= 0, int >= 0)

    Note: Returns (0, 0) for unknown models. Callers MUST validate
          and fail-fast when (0, 0) is unacceptable for loading models.
    """

    def lookup(model_id: ModelId) -> tuple[int, int]:
        # Try gateway model_details (WebSocket cache from MODEL_LOADED events)
        if model_id in gateway_model_details:
            details = gateway_model_details[model_id]
            vram = details.get("vram_usage", 0) or 0
            ram = details.get("ram_usage", 0) or 0

            # Validate that we got actual requirements (not zeros)
            if vram > 0 or ram > 0:
                return (vram, ram)

            # Found entry but has zeros - log warning but don't fail
            # (this can happen for CPU-only models with vram=0)
            logger.debug(
                f"Model {model_id} in model_details but has zero requirements "
                f"(vram={vram}, ram={ram})"
            )
            return (vram, ram)

        # Not in cache - this is unexpected for loading models
        # Return (0, 0) and let caller decide if this is acceptable
        # (caller will fail-fast in _compute_loading_reservation if needed)
        logger.warning(
            f"Requirements for {model_id} not in gateway model_details cache"
        )
        return (0, 0)

    return lookup
