"""Catalog reload and snapshot gap event signals and factories.

CATALOG_RELOADED notifies Stargate to refresh catalog; GATEWAY_SNAPSHOT_RESOURCE_GAP
diagnoses models visible in /v1/models but missing resource-tracker data.
"""

# ruff: noqa: N802 - Factory function names match event signal names

from typing import Any

from universal_event_bus import Event, event_factory

# ========== Configuration Event Signals ==========

CATALOG_RELOADED = "catalog.reloaded"
"""
Emitted when the model catalog is reloaded.

Stargate clients should re-fetch catalog data when receiving this event.

Payload:
    reason: str - Reason for reload (e.g., "hot_reload", "manual", "config_change")
"""

GATEWAY_SNAPSHOT_RESOURCE_GAP = "gateway.snapshot.resource.gap"
"""
Emitted when model_resources count < all_models count in the GATEWAY_SNAPSHOT.

Indicates models visible in /v1/models that are NOT routable by Master.
Used to distinguish startup race from resource-tracker gap.

Payload:
    all_models_count: int - Total models in catalog (from get_models())
    resource_models_count: int - Models with VRAM/RAM data (routable)
    gap_count: int - all_models_count - resource_models_count
    gap_cause: str - "init_cache_not_ready" | "resource_tracker_incomplete"
    sample_missing: list[str] - Up to 5 model IDs missing from resource data
"""


# Configuration Event Factories
@event_factory
def GatewaySnapshotResourceGap(
    all_models_count: int,
    resource_models_count: int,
    gap_cause: str,
    sample_missing: list[str] | None = None,
) -> Event:
    """
    Create GATEWAY_SNAPSHOT_RESOURCE_GAP event.

    Emitted when the GATEWAY_SNAPSHOT will advertise fewer routable models
    than the total catalog size. Enables diagnosis of MODEL_NOT_FOUND despite
    model appearing in /v1/models.

    Args:
        all_models_count: Total models in catalog
        resource_models_count: Models with VRAM/RAM resource data (routable)
        gap_cause: "init_cache_not_ready" or "resource_tracker_incomplete"
        gap_count: all_models_count - resource_models_count (in payload)
        sample_missing: Up to 5 model IDs missing resource data

    Returns:
        Event with GatewaySnapshotResourceGap signal
    """
    payload: dict[str, Any] = {
        "all_models_count": all_models_count,
        "resource_models_count": resource_models_count,
        "gap_count": all_models_count - resource_models_count,
        "gap_cause": gap_cause,
    }
    if sample_missing:
        payload["sample_missing"] = sample_missing[:5]
    return Event(signal=GATEWAY_SNAPSHOT_RESOURCE_GAP, payload=payload)


@event_factory
def CatalogReloaded(reason: str) -> Event:
    """
    Create CATALOG_RELOADED event.

    Stargate clients should re-fetch catalog data when receiving this event.

    Args:
        reason: Reason for reload (e.g., "hot_reload", "manual", "config_change")

    Returns:
        Event with CatalogReloaded signal
    """
    return Event(signal=CATALOG_RELOADED, payload={"reason": reason})
