"""
Model availability and loading checks for feasibility evaluation.

Helpers for catalog membership and resident-model detection used by early
feasibility gates before resource / eviction planning runs.
"""

from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from ..types import Gateway, Placement

logger = get_logger(__name__)


def _is_model_available(gateway: "Gateway", placement: "Placement") -> bool:
    """Check if model is available in gateway catalog."""
    # Model already loaded = available
    if _is_model_loaded(gateway, placement):
        return True

    # Check catalog using ModelId equality (no parse needed - already ModelId)
    matches = []
    for available_id in gateway.available_models:
        if available_id == placement.model_id:  # ModelId.__eq__ handles normalization
            matches.append(available_id)

    found = len(matches) > 0
    logger.info(
        f"Catalog check for {placement.model_id}: "
        f"found={found}, matches={matches[:3]}, "
        f"catalog_size={len(gateway.available_models)}"
    )
    return found


def _is_model_loaded(gateway: "Gateway", placement: "Placement") -> bool:
    """Check if model is already loaded on gateway."""
    # If the model is transitioning (loading/unloading), treat as NOT loaded.
    #
    # Rationale: `loading_models` is used as the control-plane "in transition" set.
    # During eviction, a model may still appear in `loaded_models` while an unload is
    # in progress. Treating it as loaded creates TOCTOU races where we route T1 to a
    # model that is being unloaded, producing upstream 400 model_not_loaded.
    if placement.original_model_id:
        # Parse original to ModelId for comparison
        from model_id import ModelId

        original_parsed = ModelId.parse(placement.original_model_id)
        if original_parsed in gateway.loading_models:
            return False
        if original_parsed in gateway.loaded_models:  # ModelId in frozenset[ModelId]
            return True

    if placement.model_id in gateway.loading_models:
        return False
    return placement.model_id in gateway.loaded_models  # ModelId in frozenset[ModelId]
