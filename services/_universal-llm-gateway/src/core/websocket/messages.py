"""WebSocket message types for Stargate control plane."""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MessageType(str, Enum):
    """WebSocket message types (actual string values)."""

    # Gateway → Stargate (telemetry)
    INIT = "gateway.init"
    MODEL_LOADING_STARTED = "telemetry.model.loading.started"
    MODEL_LOADED = "telemetry.model.loaded"
    MODEL_LOAD_FAILED = "telemetry.model.loading.failed"
    MODEL_UNLOADED = "telemetry.model.unloaded"
    MODEL_BUSY = "telemetry.model.busy"
    MODEL_IDLE = "telemetry.model.idle"
    RESOURCE_UPDATE = "telemetry.resource.updated"
    CATALOG_UPDATE = "gateway.catalog.updated"
    GATEWAY_SHUTDOWN = "gateway.shutdown"
    GATEWAY_DRAINING = "gateway.draining"
    PING = "gateway.ping"
    TELEMETRY_HEARTBEAT = "telemetry.heartbeat"

    # Compute capacity telemetry (for orchestration observability)
    COMPUTE_QUEUE_WAIT = "telemetry.compute.queue.wait"
    COMPUTE_QUEUE_ACQUIRED = "telemetry.compute.queue.acquired"
    REQUEST_INFERENCE_STARTED = "telemetry.request.inference.started"
    VRAM_PHANTOM_DETECTED = "telemetry.vram.phantom.detected"
    PHANTOM_MODEL_DETECTED = "telemetry.model.phantom.detected"
    PHANTOM_MODEL_CLEANED = "telemetry.model.phantom.cleaned"

    # Stargate → Gateway
    PONG = "gateway.pong"
    QUERY = "gateway.query"

    # Bidirectional
    ERROR = "gateway.error"
    RESPONSE = "gateway.response"


@dataclass
class WebSocketMessage:
    """Base WebSocket message."""

    type: MessageType
    timestamp: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type.value, "timestamp": self.timestamp, "data": self.data}


def create_init_message(
    version: str,
    gateway_name: str,
    models: list[str],
    loaded_models: list[str],
    catalog: dict[str, Any],
    resources: dict[str, Any],
) -> WebSocketMessage:
    """Create INIT message with all startup data."""
    return WebSocketMessage(
        type=MessageType.INIT,
        data={
            "version": version,
            "gateway_name": gateway_name,
            "models": models,
            "loaded_models": loaded_models,
            "catalog": catalog,
            "resources": resources,
        },
    )


def create_model_loading_started_message(model_id: str) -> WebSocketMessage:
    """Create MODEL_LOADING_STARTED event message."""
    return WebSocketMessage(
        type=MessageType.MODEL_LOADING_STARTED,
        data={"model_id": model_id},
    )


def create_model_loaded_message(
    model_id: str,
    vram_mb: int,
    ram_mb: int,
    context_length: int | None = None,
) -> WebSocketMessage:
    """Create MODEL_LOADED event message."""
    return WebSocketMessage(
        type=MessageType.MODEL_LOADED,
        data={
            "model_id": model_id,
            "vram_mb": vram_mb,
            "ram_mb": ram_mb,
            "context_length": context_length,
        },
    )


def create_model_load_failed_message(
    model_id: str, error_message: str
) -> WebSocketMessage:
    """Create MODEL_LOAD_FAILED event message."""
    return WebSocketMessage(
        type=MessageType.MODEL_LOAD_FAILED,
        data={"model_id": model_id, "error_message": error_message},
    )


def create_model_unloaded_message(model_id: str) -> WebSocketMessage:
    """Create MODEL_UNLOADED event message."""
    return WebSocketMessage(
        type=MessageType.MODEL_UNLOADED, data={"model_id": model_id}
    )


def create_model_busy_message(model_id: str) -> WebSocketMessage:
    """Create MODEL_BUSY event message."""
    return WebSocketMessage(type=MessageType.MODEL_BUSY, data={"model_id": model_id})


def create_model_idle_message(
    model_id: str, last_inference_time: float
) -> WebSocketMessage:
    """Create MODEL_IDLE event message with LRU timestamp.

    Args:
        model_id: Model identifier
        last_inference_time: Unix timestamp when inference completed
    """
    return WebSocketMessage(
        type=MessageType.MODEL_IDLE,
        data={"model_id": model_id, "last_inference_time": last_inference_time},
    )


def create_resource_update_message(
    available_vram_mb: int,
    available_ram_mb: int,
    total_vram_mb: int | None = None,
    total_ram_mb: int | None = None,
    model_vram: dict[str, int] | None = None,
) -> WebSocketMessage:
    """Create RESOURCE_UPDATE event message.

    Single-writer invariant: loaded_models is NOT included here.
    Lifecycle state comes exclusively from MODEL_LOADED / MODEL_UNLOADED events.
    """
    data: dict[str, Any] = {
        "available_vram_mb": available_vram_mb,
        "available_ram_mb": available_ram_mb,
    }
    if total_vram_mb is not None:
        data["total_vram_mb"] = total_vram_mb
    if total_ram_mb is not None:
        data["total_ram_mb"] = total_ram_mb
    if model_vram is not None:
        data["model_vram"] = model_vram
    return WebSocketMessage(type=MessageType.RESOURCE_UPDATE, data=data)


def create_ping_message() -> WebSocketMessage:
    """Create PING keep-alive message."""
    return WebSocketMessage(type=MessageType.PING)


def create_catalog_update_message(
    reason: str,
    models: list[str] | None = None,
    catalog: dict[str, Any] | None = None,
) -> WebSocketMessage:
    """Create CATALOG_UPDATE message with updated models list."""
    data: dict[str, Any] = {"reason": reason}
    if models is not None:
        data["models"] = models
    if catalog is not None:
        data["catalog"] = catalog
    return WebSocketMessage(type=MessageType.CATALOG_UPDATE, data=data)


def create_error_message(code: str, message: str) -> WebSocketMessage:
    """Create ERROR message."""
    return WebSocketMessage(
        type=MessageType.ERROR, data={"code": code, "message": message}
    )


def create_gateway_shutdown_message(
    gateway_id: str, reason: str, timestamp: float
) -> WebSocketMessage:
    """Create GATEWAY_SHUTDOWN message."""
    return WebSocketMessage(
        type=MessageType.GATEWAY_SHUTDOWN,
        timestamp=timestamp,
        data={"gateway_id": gateway_id, "reason": reason},
    )


def create_gateway_draining_message(
    gateway_id: str, reason: str, timeout: float, timestamp: float
) -> WebSocketMessage:
    """Create GATEWAY_DRAINING message."""
    return WebSocketMessage(
        type=MessageType.GATEWAY_DRAINING,
        timestamp=timestamp,
        data={"gateway_id": gateway_id, "reason": reason, "timeout": timeout},
    )


def create_telemetry_heartbeat_message(gateway_id: str) -> WebSocketMessage:
    """
    Create TELEMETRY_HEARTBEAT message.

    Proves telemetry pipeline is functioning without making capacity claims.
    Does NOT update resource state - only proves path is working.

    Invariant: ∀ heartbeat ⟹ telemetry_healthy ∧ ¬capacity_claim
    """
    return WebSocketMessage(
        type=MessageType.TELEMETRY_HEARTBEAT,
        data={"gateway_id": gateway_id},
    )


def create_compute_queue_wait_message(
    request_id: str,
    model_id: str,
    compute_type: str,
    queue_position: int,
    active_count: int,
    limit: int,
    timestamp_ms: int,
) -> WebSocketMessage:
    """
    Create COMPUTE_QUEUE_WAIT telemetry message.

    Signals that a request had to queue due to compute capacity limit.
    Indicates orchestration drift (Stargate's view was out of sync).

    Args:
        queue_position: Queue position at enqueue time (1 = first in line)
    """
    return WebSocketMessage(
        type=MessageType.COMPUTE_QUEUE_WAIT,
        data={
            "request_id": request_id,
            "model_id": model_id,
            "compute_type": compute_type,
            "queue_position": queue_position,
            "active_count": active_count,
            "limit": limit,
            "timestamp_ms": timestamp_ms,
        },
    )


def create_compute_queue_acquired_message(
    request_id: str,
    model_id: str,
    compute_type: str,
    wait_duration_ms: float,
    queue_position_at_enqueue: int,
    timestamp_ms: int,
) -> WebSocketMessage:
    """
    Create COMPUTE_QUEUE_ACQUIRED telemetry message.

    Signals that a request acquired a compute slot after waiting.

    Args:
        queue_position_at_enqueue: Position when enqueued (for correlation)
    """
    return WebSocketMessage(
        type=MessageType.COMPUTE_QUEUE_ACQUIRED,
        data={
            "request_id": request_id,
            "model_id": model_id,
            "compute_type": compute_type,
            "wait_duration_ms": wait_duration_ms,
            "queue_position_at_enqueue": queue_position_at_enqueue,
            "timestamp_ms": timestamp_ms,
        },
    )


def create_request_inference_started_message(
    request_id: str,
    model_id: str,
    gateway_url: str,
    correlation_id: str | None = None,
) -> WebSocketMessage:
    """Create REQUEST_INFERENCE_STARTED telemetry message."""
    return WebSocketMessage(
        type=MessageType.REQUEST_INFERENCE_STARTED,
        data={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_url": gateway_url,
            "correlation_id": correlation_id,
        },
    )


def create_vram_phantom_detected_message(
    hardware_used_mb: int,
    catalog_used_mb: int,
    discrepancy_mb: int,
    tracked_models: list[str],
) -> WebSocketMessage:
    """Create VRAM phantom discrepancy telemetry message."""
    return WebSocketMessage(
        type=MessageType.VRAM_PHANTOM_DETECTED,
        data={
            "hardware_used_mb": hardware_used_mb,
            "catalog_used_mb": catalog_used_mb,
            "discrepancy_mb": discrepancy_mb,
            "tracked_models": tracked_models,
        },
    )


def create_phantom_model_detected_message(
    model_id: str,
    process_status: str,
    tracker_status: str | None,
) -> WebSocketMessage:
    """Create phantom model detected telemetry message."""
    return WebSocketMessage(
        type=MessageType.PHANTOM_MODEL_DETECTED,
        data={
            "model_id": model_id,
            "process_status": process_status,
            "tracker_status": tracker_status,
        },
    )


def create_phantom_model_cleaned_message(
    model_id: str,
    success: bool,
    vram_freed_mb: int | None,
) -> WebSocketMessage:
    """Create phantom model cleaned telemetry message."""
    return WebSocketMessage(
        type=MessageType.PHANTOM_MODEL_CLEANED,
        data={
            "model_id": model_id,
            "success": success,
            "vram_freed_mb": vram_freed_mb,
        },
    )
