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


def get_local_model_ids(
    gateway_manager: SingleGatewayManager | None,
) -> set[str]:
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


def _local_gateway_catalog_id_strings(client: object) -> frozenset[str]:
    """All synthetic model IDs known to the gateway (not activation-filtered).

    Unions the INIT/CATALOG_UPDATE ``models`` cache with ``catalog.model_resources``
    keys. The latter is the full context grid; ``activated_contexts`` only affects
    display/routing filters, not catalog membership for pipeline availability.
    """
    combined: set[str] = set(client.get_models())
    raw_catalog = client.get_ws_catalog()
    model_resources = raw_catalog.get("model_resources")
    if isinstance(model_resources, dict):
        combined.update(str(k) for k in model_resources)
    return frozenset(combined)


def _federated_gateway_catalog_model_ids(gateway: object) -> frozenset:
    """Union telemetry ``available_models`` and ``model_resources`` keys."""
    from model_id import ModelId

    mids: set[ModelId] = set(getattr(gateway, "available_models", frozenset()))
    model_resources = getattr(gateway, "model_resources", None) or {}
    if isinstance(model_resources, dict):
        for k in model_resources:
            if isinstance(k, ModelId):
                mids.add(k)
            else:
                try:
                    mids.add(ModelId.parse(str(k)))
                except (TypeError, ValueError):
                    continue
    return frozenset(mids)


def is_model_in_any_catalog(
    model_id_str: str,
    gateway_manager: SingleGatewayManager | None,
    federated_manager: FederatedGatewayManager | None,
) -> bool:
    """True if *model_id_str* matches any gateway catalog entry (ModelId-aware).

    Uses ``ModelId.__eq__`` like request-time routing. Candidate IDs include the
    full ``model_resources`` grid (all contexts), not only activation-filtered lists.
    """
    from model_id import ModelId

    try:
        parsed = ModelId.parse(model_id_str)
    except Exception as e:
        logger.debug(
            "is_model_in_any_catalog: failed to parse %r: %s",
            model_id_str,
            e,
        )
        return False

    if gateway_manager:
        local_gateway = gateway_manager.get_gateway()
        if local_gateway and local_gateway.client.is_connected():
            client = local_gateway.client
            for mid_str in _local_gateway_catalog_id_strings(client):
                try:
                    if ModelId.parse(mid_str) == parsed:
                        return True
                except Exception:
                    continue

    if federated_manager:
        for federated_gateway in federated_manager.get_healthy_gateways():
            for catalog_id in _federated_gateway_catalog_model_ids(federated_gateway):
                if catalog_id == parsed:
                    return True

    return False


def collect_stargate_model_sets(
    gateway_manager: SingleGatewayManager | None,
    federated_manager: FederatedGatewayManager | None,
) -> list[set[str]]:
    """
    Collect model sets from all Stargates (local + remote).

    Returns string-based sets (e.g. for ``get_all_available_models`` / ``source=all``).

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
    local_models = get_local_model_ids(gateway_manager)
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

    total_unique = len(set().union(*model_sets))
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
    gateway_manager: SingleGatewayManager | None,
) -> set[str] | None:
    """
    Get activated models from local gateway using WebSocket cache.

    Args:
        gateway_manager: Local gateway manager

    Returns:
        Set of activated model IDs, or None if gateway not connected
    """
    if gateway_manager is None:
        return None

    local_gateway = gateway_manager.get_gateway()
    if not local_gateway or not local_gateway.client.is_connected():
        return None

    # Use sync WebSocket cache methods (not async HTTP methods)
    local_models = local_gateway.client.get_models()
    catalog = local_gateway.client.get_ws_catalog()
    activated_contexts = catalog.get("activated_contexts", {})

    if not activated_contexts:
        # No activation rules: all models are activated
        logger.debug(
            "📋 Local activation: no rules, all %d models activated", len(local_models)
        )
        return local_models

    # Apply activation filtering
    from gateways.filtering import ActivationInfo, filter_by_activation

    activated_contexts_info: dict[str, ActivationInfo] = {
        model_id: ActivationInfo(**contexts_data)
        for model_id, contexts_data in activated_contexts.items()
    }

    resources = local_gateway.client.get_ws_resources()
    gateway_resources = {
        local_gateway.config.name: {
            "total_ram_mb": resources.total_ram_mb,
            "total_vram_mb": resources.total_vram_mb,
        }
    }

    activated_models = filter_by_activation(
        local_models,
        activated_contexts_info,
        {},  # model_profile_resources not available from WS cache
        gateway_resources,
    )

    if not activated_models and local_models:
        logger.warning(
            "⚠️ Local activation filter: %d available models but filtered to empty set",
            len(local_models),
        )
    elif activated_models:
        logger.debug(
            "📋 Local activation: %d available models, %d activated after filtering",
            len(local_models),
            len(activated_models),
        )

    return activated_models


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

        if activated_is_none:
            logger.debug(
                "📋 Federated activation: gateway=%s, "
                "available=%d, activated=fallback (no activation data)",
                federated_gateway.gateway_id,
                available_count,
            )
        elif activated_count == 0 and available_count > 0:
            logger.warning(
                "⚠️ Strict activation filter: gateway=%s has %d available "
                "models but activated_models is explicitly empty — hiding all",
                federated_gateway.gateway_id,
                available_count,
            )
        else:
            logger.debug(
                "📋 Federated activation: gateway=%s, available=%d, activated=%d",
                federated_gateway.gateway_id,
                available_count,
                activated_count,
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


def get_model_context_metadata(
    gateway_manager: SingleGatewayManager | None,
    federated_manager: FederatedGatewayManager | None,
) -> dict[str, dict[str, int]]:
    """Collect context-length metadata for all models from local + federated gateways.

    Returns a dict keyed by model_id string, with values containing
    ``context_length`` and ``effective_context_per_slot`` where available.
    Provides parity with cloud model metadata from ``/api/select``.

    Data sources (already populated by telemetry — no extra I/O):
    - Local: WebSocket catalog ``model_resources``
    - Federated: ``FederatedGateway.model_resources``
    """
    metadata: dict[str, dict[str, int]] = {}

    # Local gateway: context metadata from WebSocket catalog cache
    if gateway_manager:
        local_gw = gateway_manager.get_gateway()
        if local_gw and local_gw.client.is_connected():
            catalog = local_gw.client.get_ws_catalog()
            for model_id, res in catalog.get("model_resources", {}).items():
                mid_str = str(model_id)
                ctx_raw = res.get("context_length")
                if ctx_raw:
                    entry: dict[str, int] = {"context_length": int(ctx_raw)}
                    eff_raw = res.get("effective_context_per_slot")
                    if eff_raw:
                        entry["effective_context_per_slot"] = int(eff_raw)
                    metadata.setdefault(mid_str, entry)

    # Federated gateways: context metadata from telemetry snapshots
    if federated_manager:
        for fed_gw in federated_manager.get_healthy_gateways():
            for mid, res in fed_gw.model_resources.items():
                mid_str = str(mid)
                ctx_raw = res.get("context_length")
                if not ctx_raw:
                    continue

                entry: dict[str, int] = {"context_length": int(ctx_raw)}
                eff_raw = res.get("effective_context_per_slot")
                if eff_raw:
                    entry["effective_context_per_slot"] = int(eff_raw)

                existing = metadata.get(mid_str)
                if existing is None:
                    metadata[mid_str] = entry
                    continue

                if entry["context_length"] > existing.get("context_length", 0):
                    metadata[mid_str] = entry
                elif entry["context_length"] == existing.get("context_length", 0):
                    # For equal context length, keep the better per-slot context.
                    if entry.get("effective_context_per_slot", 0) > existing.get(
                        "effective_context_per_slot", 0
                    ):
                        metadata[mid_str]["effective_context_per_slot"] = entry[
                            "effective_context_per_slot"
                        ]

    return metadata


def get_model_dispatch_metadata(
    gateway_manager: SingleGatewayManager | None,
    federated_manager: FederatedGatewayManager | None,
) -> dict[str, dict]:
    """Collect per-model dispatch facets for the /v1/models projection.

    Returns a dict keyed by model_id string -> the ``dispatch`` wire facet
    (mirror of libs ``CapabilityDispatch``), read from the per-model
    ``model_resources`` carrier. Federated only this build: cloud dispatch rides
    ``FederatedGateway.model_resources[mid]["dispatch"]`` (seeded by the
    cloud-proxy catalog poller). Local rows carry no dispatch facet today
    (tracked local-parity follow-up); the reader is source-shaped so populating
    local ``model_resources["dispatch"]`` later needs no projection change.

    ``gateway_manager`` is accepted for call-site parity with
    ``get_model_context_metadata`` and the future local source.
    """
    _ = gateway_manager  # local dispatch not wired this build (source-agnostic Q4)
    metadata: dict[str, dict] = {}

    if federated_manager:
        for fed_gw in federated_manager.get_healthy_gateways():
            for mid, res in fed_gw.model_resources.items():
                dispatch = res.get("dispatch")
                if isinstance(dispatch, dict):
                    metadata.setdefault(str(mid), dispatch)

    return metadata


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
    for model_id in get_local_model_ids(gateway_manager):
        source_map.setdefault(model_id, []).append(local_stargate_id)

    # Federated models
    if federated_manager:
        for federated_gateway in federated_manager.get_healthy_gateways():
            for model_id in federated_gateway.available_models:
                source_map.setdefault(str(model_id), []).append(
                    federated_gateway.remote_stargate_id
                )

    return source_map


def get_model_status_map(
    local_stargate_id: str,
    gateway_manager: SingleGatewayManager | None,
    federated_manager: FederatedGatewayManager | None,
) -> dict[str, dict[str, list[str]]]:
    """Per-model load/busy/loading status across all gateways.

    Returns:
        Dict mapping model_id str → {
            "loaded_on": [stargate_ids],
            "busy_on": [stargate_ids],
            "loading_on": [stargate_ids],
        }
    """
    status: dict[str, dict[str, list[str]]] = {}

    def _ensure(mid: str) -> dict[str, list[str]]:
        return status.setdefault(
            mid, {"loaded_on": [], "busy_on": [], "loading_on": []}
        )

    # Local gateway
    if gateway_manager:
        local_gw = gateway_manager.get_gateway()
        if local_gw and local_gw.client.is_connected():
            for mid in local_gw.client.get_loaded_models():
                _ensure(mid)["loaded_on"].append(local_stargate_id)
            for mid in local_gw.client._ws_client.get_busy_models():
                _ensure(mid)["busy_on"].append(local_stargate_id)
            for mid in local_gw.client._ws_client.get_loading_models():
                _ensure(mid)["loading_on"].append(local_stargate_id)

    # Federated gateways
    if federated_manager:
        for fed_gw in federated_manager.get_healthy_gateways():
            node = fed_gw.remote_stargate_id
            for mid in fed_gw.loaded_models:
                _ensure(str(mid))["loaded_on"].append(node)
            for mid in fed_gw.busy_models:
                _ensure(str(mid))["busy_on"].append(node)
            for mid in fed_gw.loading_models:
                _ensure(str(mid))["loading_on"].append(node)

    return status
