"""
Catalog integration for Stargate pool.

Aggregates model availability from local gateway and federation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from gateways import SingleGatewayManager
    from systems.federation import FederatedGatewayManager

logger = get_logger(__name__)


def _get_local_model_ids(gateway_manager: SingleGatewayManager | None) -> set[str]:
    """
    Get model IDs from local gateway.

    Single source of truth for local model access.

    Args:
        gateway_manager: Local gateway manager (None for router-only Master)

    Returns:
        Set of model ID strings from local gateway (empty if None or not connected)
    """
    if gateway_manager is None:
        return set()

    local_gateway = gateway_manager.get_gateway()
    if local_gateway and local_gateway.client.is_connected():
        return local_gateway.client.get_models()
    return set()


def collect_stargate_model_sets(
    gateway_manager: SingleGatewayManager | None,
    federated_manager: FederatedGatewayManager | None,
) -> list[set[str]]:
    """
    Collect model sets from all Stargates (local + remote).

    Returns string-based sets for interface stability with PipelineRegistry.

    Args:
        gateway_manager: Local gateway manager (None for router-only Master)
        federated_manager: Federation manager (or None if not federated)

    Returns:
        List of model ID sets, one per reachable Stargate

    Invariant:
        ∀ model_set: model_set ⊆ stargate.available_models
    """
    model_sets: list[set[str]] = []

    # Local Stargate's models (router-only Master returns empty set)
    local_models = _get_local_model_ids(gateway_manager)
    if local_models:
        model_sets.append(local_models)

    # Remote Stargates' models (from telemetry)
    if federated_manager:
        for federated_gateway in federated_manager.get_healthy_gateways():
            # FederatedGateway.available_models is frozenset[ModelId]
            remote_set = {
                str(model_id) for model_id in federated_gateway.available_models
            }
            model_sets.append(remote_set)

    total_unique = len(set().union(*model_sets)) if model_sets else 0
    logger.debug(
        "collect_stargate_model_sets: %d sources, %d unique models",
        len(model_sets),
        total_unique,
    )

    return model_sets


def get_all_available_models(
    gateway_manager: SingleGatewayManager | None,
    federated_manager: FederatedGatewayManager | None,
) -> set[str]:
    """
    Get union of all available models from Stargate pool.

    Returns ALL models that CAN be loaded (full catalog for routing).

    Args:
        gateway_manager: Local gateway manager (None for router-only Master)
        federated_manager: Federation manager (or None)

    Returns:
        Union of all available model IDs as strings
    """
    model_sets = collect_stargate_model_sets(gateway_manager, federated_manager)
    if not model_sets:
        return set()
    return set().union(*model_sets)


def _get_local_activated_models(
    gateway_manager: SingleGatewayManager,
) -> set[str] | None:
    """
    Get activated models from local gateway using WebSocket cache.

    Args:
        gateway_manager: Local gateway manager

    Returns:
        Set of activated model IDs, or None if gateway not connected
    """
    local_gateway = gateway_manager.get_gateway()
    if not local_gateway or not local_gateway.client.is_connected():
        return None

    # Use sync WebSocket cache methods (not async HTTP methods)
    local_models = local_gateway.client.get_models()
    catalog = local_gateway.client.get_ws_catalog()
    activated_contexts = catalog.get("activated_contexts", {})

    if not activated_contexts:
        # No activation rules: all models are activated
        return local_models

    # Apply activation filtering
    from gateways.filtering import ActivationInfo, filter_by_activation

    activated_contexts_info: dict[str, ActivationInfo] = {}
    for model_id, contexts_data in activated_contexts.items():
        activated_contexts_info[model_id] = ActivationInfo(
            cpu=contexts_data.get("cpu"),
            gpu=contexts_data.get("gpu"),
        )

    resources = local_gateway.client.get_ws_resources()
    gateway_resources = {
        local_gateway.config.name: {
            "total_ram_mb": resources.total_ram_mb,
            "total_vram_mb": resources.total_vram_mb,
        }
    }

    return filter_by_activation(
        local_models,
        activated_contexts_info,
        {},  # model_profile_resources not available from WS cache
        gateway_resources,
    )


def _get_federated_activated_models(
    federated_manager: FederatedGatewayManager,
) -> list[set[str]]:
    """
    Get activated models from federated gateways.

    Args:
        federated_manager: Federation manager

    Returns:
        List of activated model sets, one per healthy gateway
    """
    activated_sets: list[set[str]] = []

    for federated_gateway in federated_manager.get_healthy_gateways():
        available_count = len(federated_gateway.available_models)
        activated_models = federated_gateway.activated_models
        activated_is_none = activated_models is None

        # Check if activation data was explicitly provided (not default)
        # None = not provided (fallback to available_models)
        # frozenset() = explicitly empty (show no models)
        if activated_models is not None:
            remote_set = {str(model_id) for model_id in activated_models}
            activated_count = len(remote_set)
        else:
            # Fallback: no activation data provided, use all available
            remote_set = {
                str(model_id) for model_id in federated_gateway.available_models
            }
            activated_count = available_count

        # Debug logging for activation filtering verification
        logger.debug(
            f"📋 Federated activation: gateway={federated_gateway.gateway_id}, "
            f"available={available_count}, activated={activated_count}, "
            f"activated_is_none={activated_is_none}"
        )

        activated_sets.append(remote_set)

    return activated_sets


def get_activated_models_for_display(
    gateway_manager: SingleGatewayManager | None,
    federated_manager: FederatedGatewayManager | None,
) -> set[str]:
    """
    Get union of activated models for public /v1/models endpoint.

    Returns only models marked as activated (filtered by activated_contexts).
    Falls back to all available models if no activation data present.

    Args:
        gateway_manager: Local gateway manager (None for router-only Master)
        federated_manager: Federation manager (or None)

    Returns:
        Union of activated model IDs as strings

    INVARIANT: ∀ model ∈ result: model ∈ activated_models ∨ ¬∃ activation_rules
    """
    activated_sets: list[set[str]] = []

    # Local gateway's activated models
    if gateway_manager:
        local_activated = _get_local_activated_models(gateway_manager)
        if local_activated:
            activated_sets.append(local_activated)

    # Federated gateways' activated models
    if federated_manager:
        federated_sets = _get_federated_activated_models(federated_manager)
        activated_sets.extend(federated_sets)

    if not activated_sets:
        return set()

    return set().union(*activated_sets)


def get_model_source_map(
    local_stargate_id: str,
    gateway_manager: SingleGatewayManager | None,
    federated_manager: FederatedGatewayManager | None,
) -> dict[str, list[str]]:
    """
    Get mapping of model_id → [stargate_ids] where available.

    Useful for debugging which Stargates have which models.

    Args:
        local_stargate_id: This Stargate's ID
        gateway_manager: Local gateway manager (None for router-only Master)
        federated_manager: Federation manager

    Returns:
        Dict mapping model_id strings to list of stargate_ids
    """
    source_map: dict[str, list[str]] = {}

    # Local models (router-only Master returns empty set)
    for model_id in _get_local_model_ids(gateway_manager):
        source_map.setdefault(model_id, []).append(local_stargate_id)

    # Federated models
    if federated_manager:
        for federated_gateway in federated_manager.get_healthy_gateways():
            for model_id in federated_gateway.available_models:
                source_map.setdefault(str(model_id), []).append(
                    federated_gateway.remote_stargate_id
                )

    return source_map
