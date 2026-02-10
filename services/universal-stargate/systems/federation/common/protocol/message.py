"""
Federation message types and factories.

Uses typed TelemetryPayload from universal_protocol.
Wraps payloads in MessageEnvelope for wire format.
"""

from enum import Enum
from typing import Any

from universal_protocol.messages import (
    GatewaySnapshot,
    MessageEnvelope,
    ModelBusy,
    ModelIdle,
    ModelLoaded,
    ModelLoadFailed,
    ModelLoadingStarted,
    ModelUnloaded,
    ResourceUpdate,
    TelemetryHeartbeat,
    TelemetrySource,
)


class FederationMessageType(str, Enum):
    """Federation message types (dot-notation per EVENTS.md)."""

    # Lifecycle
    FEDERATION_INIT = "federation.init"
    FEDERATION_AUTH = "federation.auth"
    FEDERATION_AUTH_RESULT = "federation.auth.result"
    FEDERATION_PING = "federation.ping"
    FEDERATION_PONG = "federation.pong"

    # Control (Master → Remote)
    REQUEST_CANCEL = "request.cancel"

    # Telemetry (match telemetry registry)
    RESOURCE_UPDATE = "telemetry.resource.updated"
    MODEL_LOADED = "telemetry.model.loaded"
    MODEL_UNLOADED = "telemetry.model.unloaded"
    MODEL_BUSY = "telemetry.model.busy"
    MODEL_IDLE = "telemetry.model.idle"
    MODEL_LOADING_STARTED = "telemetry.model.loading.started"
    MODEL_LOAD_FAILED = "telemetry.model.loading.failed"
    TELEMETRY_HEARTBEAT = "telemetry.heartbeat"
    GATEWAY_SNAPSHOT = "telemetry.gateway.snapshot"


# Factory functions return MessageEnvelope


def create_resource_update(
    available_vram_mb: int,
    available_ram_mb: int,
    total_vram_mb: int | None = None,
    total_ram_mb: int | None = None,
    loaded_models: list[str] | None = None,
    busy_models: list[str] | None = None,
    source: dict[str, Any] | None = None,
) -> MessageEnvelope:
    """
    Create resource update message with typed payload.

    NOTE: available_models and model_resources are NOT accepted here.
    These static fields are only sent in initial telemetry, not in
    resource update messages.
    """
    # Convert dict source to TelemetrySource if provided
    typed_source = TelemetrySource.from_dict(source) if source else None

    payload = ResourceUpdate(
        available_vram_mb=available_vram_mb,
        available_ram_mb=available_ram_mb,
        total_vram_mb=total_vram_mb,
        total_ram_mb=total_ram_mb,
        loaded_models=loaded_models,
        busy_models=busy_models,
        source=typed_source,
    )

    return MessageEnvelope(
        type=FederationMessageType.RESOURCE_UPDATE.value,
        data=payload.to_dict(),
    )


def create_model_loaded(
    model_id: str,
    source: dict[str, Any] | None = None,
    **extra_data: Any,
) -> MessageEnvelope:
    """Create MODEL_LOADED message with typed payload."""
    typed_source = TelemetrySource.from_dict(source) if source else None
    payload = ModelLoaded(
        model_id=model_id,
        source=typed_source,
        extra_data=extra_data,
    )
    return MessageEnvelope(
        type=FederationMessageType.MODEL_LOADED.value,
        data=payload.to_dict(),
    )


def create_model_unloaded(
    model_id: str,
    source: dict[str, Any] | None = None,
) -> MessageEnvelope:
    """Create MODEL_UNLOADED message with typed payload."""
    typed_source = TelemetrySource.from_dict(source) if source else None
    payload = ModelUnloaded(model_id=model_id, source=typed_source)
    return MessageEnvelope(
        type=FederationMessageType.MODEL_UNLOADED.value,
        data=payload.to_dict(),
    )


def create_model_busy(
    model_id: str,
    source: dict[str, Any] | None = None,
) -> MessageEnvelope:
    """Create MODEL_BUSY message with typed payload."""
    typed_source = TelemetrySource.from_dict(source) if source else None
    payload = ModelBusy(model_id=model_id, source=typed_source)
    return MessageEnvelope(
        type=FederationMessageType.MODEL_BUSY.value,
        data=payload.to_dict(),
    )


def create_model_idle(
    model_id: str,
    source: dict[str, Any] | None = None,
) -> MessageEnvelope:
    """Create MODEL_IDLE message with typed payload."""
    typed_source = TelemetrySource.from_dict(source) if source else None
    payload = ModelIdle(model_id=model_id, source=typed_source)
    return MessageEnvelope(
        type=FederationMessageType.MODEL_IDLE.value,
        data=payload.to_dict(),
    )


def create_model_loading_started(
    model_id: str,
    source: dict[str, Any] | None = None,
) -> MessageEnvelope:
    """Create MODEL_LOADING_STARTED message with typed payload."""
    typed_source = TelemetrySource.from_dict(source) if source else None
    payload = ModelLoadingStarted(model_id=model_id, source=typed_source)
    return MessageEnvelope(
        type=FederationMessageType.MODEL_LOADING_STARTED.value,
        data=payload.to_dict(),
    )


def create_model_load_failed(
    model_id: str,
    error: str | None = None,
    source: dict[str, Any] | None = None,
) -> MessageEnvelope:
    """Create MODEL_LOAD_FAILED message with typed payload."""
    typed_source = TelemetrySource.from_dict(source) if source else None
    payload = ModelLoadFailed(model_id=model_id, error=error, source=typed_source)
    return MessageEnvelope(
        type=FederationMessageType.MODEL_LOAD_FAILED.value,
        data=payload.to_dict(),
    )


def create_telemetry_heartbeat(
    gateway_id: str,
    source: dict[str, Any] | None = None,
) -> MessageEnvelope:
    """Create TELEMETRY_HEARTBEAT message with typed payload."""
    typed_source = TelemetrySource.from_dict(source) if source else None
    payload = TelemetryHeartbeat(gateway_id=gateway_id, source=typed_source)
    return MessageEnvelope(
        type=FederationMessageType.TELEMETRY_HEARTBEAT.value,
        data=payload.to_dict(),
    )


def create_gateway_snapshot(
    available_vram_mb: int,
    available_ram_mb: int,
    total_vram_mb: int,
    total_ram_mb: int,
    available_models: list[str],
    model_resources: dict[str, dict[str, int]],
    activated_models: list[str] | None = None,
    activated_contexts: dict[str, dict[str, list[int]]] | None = None,
    loaded_models: list[str] | None = None,
    busy_models: list[str] | None = None,
    source: dict[str, Any] | None = None,
) -> MessageEnvelope:
    """
    Create GATEWAY_SNAPSHOT message with typed payload.

    Initial catalog snapshot sent when Edge Stargate first wires Gateway.
    Contains complete available_models list and model_resources for routing.

    Args:
        available_models: Full model list (all contexts) for routing capability
        activated_models: Filtered subset for public /v1/models endpoint
        activated_contexts: Activation rules (base_model -> {gpu: [...], cpu: [...]})
    """
    typed_source = TelemetrySource.from_dict(source) if source else None
    payload = GatewaySnapshot(
        available_vram_mb=available_vram_mb,
        available_ram_mb=available_ram_mb,
        total_vram_mb=total_vram_mb,
        total_ram_mb=total_ram_mb,
        available_models=available_models,
        model_resources=model_resources,
        activated_models=activated_models,
        activated_contexts=activated_contexts,
        loaded_models=loaded_models,
        busy_models=busy_models,
        source=typed_source,
    )
    return MessageEnvelope(
        type=FederationMessageType.GATEWAY_SNAPSHOT.value,
        data=payload.to_dict(),
    )


def create_request_cancel(
    request_id: str,
    model_id: str | None = None,
) -> MessageEnvelope:
    """
    Create REQUEST_CANCEL message.

    Sent from Master to Remote to cancel an in-flight request.

    Args:
        request_id: The request ID of the request to cancel
        model_id: Optional model ID for queue-specific cancellation
    """
    data: dict[str, Any] = {"request_id": request_id}
    if model_id:
        data["model_id"] = model_id
    return MessageEnvelope(
        type=FederationMessageType.REQUEST_CANCEL.value,
        data=data,
    )


def create_federation_ping() -> MessageEnvelope:
    """Create FEDERATION_PING message."""
    return MessageEnvelope(
        type=FederationMessageType.FEDERATION_PING.value,
        data={},
    )


def create_federation_pong() -> MessageEnvelope:
    """Create FEDERATION_PONG message."""
    return MessageEnvelope(
        type=FederationMessageType.FEDERATION_PONG.value,
        data={},
    )


def create_federation_init(
    stargate_id: str,
    role: str,
    protocol_version: str = "1.0",
) -> MessageEnvelope:
    """Create FEDERATION_INIT message."""
    return MessageEnvelope(
        type=FederationMessageType.FEDERATION_INIT.value,
        data={
            "stargate_id": stargate_id,
            "role": role,
            "protocol_version": protocol_version,
        },
    )


def create_federation_auth(
    stargate_id: str,
    api_key: str,
    protocol_version: str = "1.0",
) -> MessageEnvelope:
    """Create FEDERATION_AUTH message."""
    return MessageEnvelope(
        type=FederationMessageType.FEDERATION_AUTH.value,
        data={
            "stargate_id": stargate_id,
            "api_key": api_key,
            "protocol_version": protocol_version,
        },
    )


def create_federation_auth_result(
    success: bool,
    message: str | None = None,
    stargate_id: str | None = None,
    protocol_version: str | None = None,
) -> MessageEnvelope:
    """Create FEDERATION_AUTH_RESULT message."""
    data: dict[str, Any] = {"success": success}
    if message is not None:
        data["message"] = message
    if stargate_id is not None:
        data["stargate_id"] = stargate_id
    if protocol_version is not None:
        data["protocol_version"] = protocol_version
    return MessageEnvelope(
        type=FederationMessageType.FEDERATION_AUTH_RESULT.value,
        data=data,
    )


# Type checking helpers


def is_telemetry_type(msg_type: str) -> bool:
    """Check if message type is telemetry (passthrough to master)."""
    return msg_type in {
        FederationMessageType.RESOURCE_UPDATE.value,
        FederationMessageType.MODEL_LOADED.value,
        FederationMessageType.MODEL_UNLOADED.value,
        FederationMessageType.MODEL_BUSY.value,
        FederationMessageType.MODEL_IDLE.value,
        FederationMessageType.MODEL_LOADING_STARTED.value,
        FederationMessageType.MODEL_LOAD_FAILED.value,
        FederationMessageType.TELEMETRY_HEARTBEAT.value,
        FederationMessageType.GATEWAY_SNAPSHOT.value,
    }


def parse_federation_message(d: dict[str, Any]) -> MessageEnvelope:
    """
    Parse and validate federation message from wire format.

    Uses MessageEnvelope.from_dict() for validation.
    """
    return MessageEnvelope.from_dict(d)
