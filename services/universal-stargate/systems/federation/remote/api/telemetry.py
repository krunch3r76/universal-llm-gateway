"""
HTTP telemetry endpoint for Remote Stargates.

Allows Master to poll telemetry when WebSocket is disabled (Golem compatibility).

git Topology notes:
- Execution-capable Remote (no local_edge): telemetry is sourced from local Gateway
  via app.state.gateway_manager (gateway WS state).
- Relay topology (Remote with local_edge): telemetry is sourced from the local Edge
  WebSocket feed (LocalEdgeClient) and accumulated in this module.

INVARIANT: ∀ telemetry_request: authenticated via X-Federation-* headers
INVARIANT: Response includes all fields needed for routing decisions
INVARIANT: Edge-first delta computation (Remote computes deltas, not Master)
INVARIANT: Tracker always initialized before endpoint available
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from universal_logging import get_logger

from ...common.config.schema import FederationConfig, LocalEdgeConfig
from ...common.middleware.auth import require_federation_auth
from ..telemetry.logger import TelemetryLogger
from ..telemetry.state_tracker import TelemetryStateTracker

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/federation", tags=["federation"])

# Global instances (initialized by federation integration)
tracker: TelemetryStateTracker | None = None
telemetry_logger: TelemetryLogger | None = None

# Relay topology telemetry cache (Edge → Relay ingestion)
_edge_state: dict[str, Any] = {}

_EDGE_TO_TRACKER_KEY: Final[dict[str, str]] = {
    # universal_protocol payload fields → tracker state fields
    "available_vram_mb": "vram_free_mb",
    "available_ram_mb": "ram_free_mb",
    "loaded_models": "loaded_models",
    "busy_models": "busy_models",
    "available_models": "available_models",
}


def _require_gateway_manager(request: Request) -> Any:
    """
    Get gateway manager from app state (fail-fast if missing).

    Args:
        request: FastAPI request with app state

    Returns:
        Gateway manager instance

    Raises:
        HTTPException: 503 if gateway manager not initialized
    """
    if not hasattr(request.app.state, "gateway_manager"):
        logger.error("Gateway manager not available in app state")
        raise HTTPException(status_code=503, detail="Gateway manager not initialized")
    return request.app.state.gateway_manager


def _require_federation_config(request: Request) -> FederationConfig:
    """
    Get federation config from app state (fail-fast if missing).

    Args:
        request: FastAPI request with app state

    Returns:
        Federation configuration

    Raises:
        HTTPException: 503 if federation config not initialized
    """
    if not hasattr(request.app.state, "federation_config"):
        logger.error("Federation config not available in app state")
        raise HTTPException(status_code=503, detail="Federation config not initialized")
    return request.app.state.federation_config


def _require_local_edge_config(
    federation_config: FederationConfig,
) -> LocalEdgeConfig:
    """
    Get local edge config from federation config (fail-fast if missing).

    Args:
        federation_config: Federation configuration

    Returns:
        Local edge configuration

    Raises:
        HTTPException: 503 if local edge not configured
    """
    if not hasattr(federation_config, "local_edge") or not federation_config.local_edge:
        logger.error("Local edge config not available")
        raise HTTPException(status_code=503, detail="Local edge not configured")
    return federation_config.local_edge


def _apply_activation_filtering(
    ws_client: Any,
    model_ids: set[str],
    resources: Any,
) -> set[str]:
    """
    Apply activation filtering to model catalog.

    Filters models based on activated_gpu_contexts and activated_cpu_contexts
    from the gateway catalog. Matches the filtering logic used in WebSocket mode.

    Args:
        ws_client: WebSocket client with access to catalog
        model_ids: Raw model IDs from gateway
        resources: Resource status for capacity checks

    Returns:
        Filtered set of model IDs respecting activated contexts
    """
    from gateways.filtering import ActivationInfo, filter_by_activation

    # Get activated contexts from gateway catalog
    catalog = ws_client.get_catalog()
    raw_activated = catalog.get("activated_contexts", {})

    if not raw_activated:
        # No activation rules, return all models
        return model_ids

    # Convert to ActivationInfo objects
    activated_contexts: dict[str, ActivationInfo] = {}
    for model_id, contexts_data in raw_activated.items():
        activated_contexts[model_id] = ActivationInfo(
            cpu=contexts_data.get("cpu"),
            gpu=contexts_data.get("gpu"),
        )

    # Build gateway resources dict
    gateway_resources = {
        "local": {
            "total_ram_mb": resources.total_ram_mb,
            "total_vram_mb": resources.total_vram_mb,
        }
    }

    # Apply filtering (model_profile_resources empty - gateway doesn't send this yet)
    model_profile_resources: dict[str, dict[str, dict[int, dict[str, int]]]] = {}

    filtered = filter_by_activation(
        model_ids,
        activated_contexts,
        model_profile_resources,
        gateway_resources,
    )

    logger.debug(
        "🔍 HTTP telemetry activation filtering: "
        f"{len(model_ids)} → {len(filtered)} models"
    )

    return filtered


def _get_current_gateway_state(request: Request) -> dict[str, Any]:
    """
    Extract current gateway state snapshot from app state.

    Args:
        request: FastAPI request with app state

    Returns:
        Dict with telemetry fields for tracker

    Raises:
        HTTPException: 503 if gateway not available
    """
    gateway_manager = _require_gateway_manager(request)
    gateway = gateway_manager.get_gateway()
    if not gateway:
        logger.warning("Gateway not connected for telemetry request")
        raise HTTPException(status_code=503, detail="Gateway not connected")

    resources = gateway.client.get_ws_resources()
    loaded_models = gateway.client.get_loaded_models()
    busy_models = gateway.client.get_busy_models()
    raw_available_models = gateway.client.get_models()

    # Apply activation filtering (matches WebSocket mode behavior)
    available_models = _apply_activation_filtering(
        gateway.client.ws_client,
        raw_available_models,
        resources,
    )

    return {
        "loaded_models": [str(m) for m in loaded_models],
        "busy_models": [str(m) for m in busy_models],
        "active_requests": 0,  # SingleGatewayManager doesn't track per-gateway count
        "vram_free_mb": resources.available_vram_mb,
        "ram_free_mb": resources.available_ram_mb,
        "available_models": [str(m) for m in available_models],
    }


def initialize_telemetry(node_id: str, log_level: str | None = None) -> None:
    """
    Initialize global telemetry tracker and logger.

    Called by federation integration on startup.

    Args:
        node_id: Remote node identifier
        log_level: Telemetry log level (DEBUG/INFO/ERROR string or enum)
    """
    global tracker, telemetry_logger

    from ..telemetry.logger import TelemetryLogLevel

    # Convert string to enum if needed
    if log_level is None:
        level_enum = TelemetryLogLevel.INFO
    elif isinstance(log_level, str):
        level_enum = TelemetryLogLevel(log_level)
    else:
        level_enum = log_level

    tracker = TelemetryStateTracker(node_id=node_id)
    telemetry_logger = TelemetryLogger(
        node_id=node_id,
        log_level=level_enum,
    )


def ingest_edge_telemetry(msg_type: str, data: dict[str, Any]) -> None:
    """
    Ingest telemetry from local Edge (Relay topology) into the HTTP polling tracker.

    Called by RemoteIntegration when LocalEdgeClient receives telemetry.* messages.

    Args:
        msg_type: FederationMessageType string (e.g. "telemetry.resource.updated")
        data: Telemetry payload dict from universal_protocol (MessageEnvelope.data)
    """
    global _edge_state

    if tracker is None:
        logger.error(
            "Edge telemetry received before tracker initialization",
            extra={"msg_type": msg_type},
        )
        return

    # Update cached state from Edge payload.
    for edge_key, tracker_key in _EDGE_TO_TRACKER_KEY.items():
        if edge_key in data and data[edge_key] is not None:
            _edge_state[tracker_key] = data[edge_key]

    # Ensure required fields exist for routing consumers (even before first snapshot).
    _edge_state.setdefault("active_requests", 0)
    _edge_state.setdefault("loaded_models", [])
    _edge_state.setdefault("busy_models", [])
    _edge_state.setdefault("available_models", [])
    _edge_state.setdefault("vram_free_mb", 0)
    _edge_state.setdefault("ram_free_mb", 0)

    # Add critical events for model lifecycle signals when present.
    if msg_type == "telemetry.model.loaded" and "model_id" in data:
        tracker.add_critical_event("MODEL_LOADED", {"model_id": data["model_id"]})
        if data["model_id"] not in _edge_state["loaded_models"]:
            _edge_state["loaded_models"].append(data["model_id"])

    elif msg_type == "telemetry.model.unloaded" and "model_id" in data:
        tracker.add_critical_event("MODEL_UNLOADED", {"model_id": data["model_id"]})
        _edge_state["loaded_models"] = [
            m for m in _edge_state["loaded_models"] if m != data["model_id"]
        ]
        _edge_state["busy_models"] = [
            m for m in _edge_state["busy_models"] if m != data["model_id"]
        ]

    elif msg_type == "telemetry.model.busy" and "model_id" in data:
        tracker.add_critical_event("MODEL_BUSY", {"model_id": data["model_id"]})
        if data["model_id"] not in _edge_state["busy_models"]:
            _edge_state["busy_models"].append(data["model_id"])

    elif msg_type == "telemetry.model.idle" and "model_id" in data:
        tracker.add_critical_event("MODEL_IDLE", {"model_id": data["model_id"]})
        _edge_state["busy_models"] = [
            m for m in _edge_state["busy_models"] if m != data["model_id"]
        ]

    elif msg_type == "telemetry.model.loading.failed" and "model_id" in data:
        tracker.add_critical_event(
            "MODEL_LOAD_FAILED",
            {"model_id": data["model_id"], "error": data.get("error")},
        )

    # Finally, update tracker state (delta computation happens on polling).
    tracker.update(_edge_state)


def _build_snapshot_response(
    tracker: TelemetryStateTracker, gateway_id: str
) -> dict[str, Any]:
    """
    Build snapshot response (full=true).

    Args:
        tracker: Telemetry state tracker
        gateway_id: Gateway identifier (stable key for Master state)

    Returns:
        Snapshot response dict with gateway_id for correct state keying
    """
    snapshot = tracker.get_full_snapshot()

    if telemetry_logger:
        telemetry_logger.log_snapshot(snapshot)

    return {
        "type": "snapshot",
        "gateway_id": gateway_id,
        "state": snapshot,
        "timestamp": tracker.timestamp,
        "node_id": tracker.node_id,
    }


def _build_delta_response(
    tracker: TelemetryStateTracker, gateway_id: str
) -> dict[str, Any] | Response:
    """
    Build delta response or 204 No Content.

    Args:
        tracker: Telemetry state tracker
        gateway_id: Gateway identifier (stable key for Master state)

    Returns:
        Delta response dict with gateway_id, or 204 Response
    """
    delta = tracker.get_delta()

    # Return 204 No Content for empty deltas (network efficiency)
    # Empty delta = only sequence_number, no actual changes, no critical events
    if (
        len(delta) <= 1
        and "sequence_number" in delta
        and "critical_events" not in delta
    ):
        return Response(status_code=204)

    # Log delta (async, non-blocking)
    if telemetry_logger:
        telemetry_logger.log_delta(delta, delta.get("sequence_number", 0))

        # Log critical events separately
        for event in delta.get("critical_events", []):
            telemetry_logger.log_critical_event(event["event"], event)

    # Extract critical events and changes
    excluded_keys = ("sequence_number", "critical_events")
    changes = {k: v for k, v in delta.items() if k not in excluded_keys}
    critical_events = delta.get("critical_events", [])

    response = {
        "type": "delta",
        "gateway_id": gateway_id,
        "changes": changes,
        "sequence_number": delta.get("sequence_number", 0),
        "critical_events": critical_events,
        "timestamp": tracker.timestamp,
        "node_id": tracker.node_id,
    }

    logger.info(
        f"📬 Returning 200 with delta: gateway={gateway_id}, "
        f"seq={response['sequence_number']}, changes={len(changes)}, "
        f"change_fields={list(changes.keys())}"
    )

    return response


@router.get("/telemetry")
async def get_telemetry(
    request: Request,
    full: bool = False,
    _auth: None = Depends(require_federation_auth),
):
    """
    Get gateway telemetry delta or snapshot for HTTP polling.

    Used by Master when WebSocket is disabled (Golem compatibility).
    Implements edge-first delta computation with auto-clearing on delivery.

    Auth: Requires X-Federation-Source and X-Federation-Key headers.

    Query params:
        full: If True, return full snapshot (reconnect/sync)

    Returns:
        - 200 with delta JSON if state changed (includes gateway_id)
        - 204 No Content if no state changes (empty delta)
        - 200 with snapshot JSON if full=true (includes gateway_id)

    Raises:
        HTTPException: 503 if tracker not initialized or gateway not available
    """
    # Fail fast if tracker not initialized
    if not tracker:
        logger.error("Telemetry tracker not initialized")
        raise HTTPException(
            status_code=503, detail="Telemetry infrastructure not initialized"
        )

    # Get edge_id from local config (stable key for Master state)
    federation_config = _require_federation_config(request)
    local_edge = _require_local_edge_config(federation_config)
    # Use stargate_id as gateway_id for telemetry (Edge identifies the execution
    # endpoint).
    gateway_id = local_edge.stargate_id

    # Telemetry source depends on topology:
    # - Execution-capable Remote: pull from local Gateway manager.
    # - Relay topology: tracker is updated asynchronously via ingest_edge_telemetry().
    if hasattr(request.app.state, "gateway_manager"):
        current_state = _get_current_gateway_state(request)
        tracker.update(current_state)
    else:
        # Relay topology: fail-fast until we have at least one Edge telemetry update.
        if not _edge_state:
            logger.warning(
                "Telemetry requested before any Edge telemetry received",
                extra={"gateway_id": gateway_id},
            )
            raise HTTPException(status_code=503, detail="Edge telemetry not available")

    # Full snapshot requested (reconnect)
    if full:
        return _build_snapshot_response(tracker, gateway_id)

    # Compute and return delta (auto-clears on second poll at same sequence)
    return _build_delta_response(tracker, gateway_id)
