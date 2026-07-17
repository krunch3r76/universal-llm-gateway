"""WebSocket message types for Gateway control plane (Stargate side)."""

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class MessageType(StrEnum):
    """WebSocket message types (dot-notation per EVENTS.md)."""

    # Gateway → Stargate
    INIT = "gateway.init"
    MODEL_LOADING_STARTED = "telemetry.model.loading.started"
    MODEL_LOADING_PROGRESS = "telemetry.model.loading.progress"
    MODEL_LOADED = "telemetry.model.loaded"
    MODEL_LOAD_FAILED = "telemetry.model.loading.failed"
    MODEL_UNLOADED = "telemetry.model.unloaded"
    MODEL_BUSY = "telemetry.model.busy"
    MODEL_IDLE = "telemetry.model.idle"
    REQUEST_INFERENCE_STARTED = "telemetry.request.inference.started"
    RESOURCE_UPDATE = "telemetry.resource.updated"
    CATALOG_UPDATE = "gateway.catalog.updated"
    GATEWAY_SHUTDOWN = "gateway.shutdown"
    GATEWAY_DRAINING = "gateway.draining"
    PING = "gateway.ping"
    TELEMETRY_HEARTBEAT = "telemetry.heartbeat"

    # Compute capacity telemetry (orchestration observability)
    COMPUTE_QUEUE_WAIT = "telemetry.compute.queue.wait"
    COMPUTE_QUEUE_ACQUIRED = "telemetry.compute.queue.acquired"

    # Stargate → Gateway
    PONG = "gateway.pong"
    QUERY = "gateway.query"

    # Bidirectional
    ERROR = "gateway.error"
    RESPONSE = "gateway.response"


@dataclass
class InitData:
    """Parsed INIT message data."""

    version: str
    gateway_name: str
    models: list[str]
    loaded_models: list[str]
    catalog: dict[str, Any]
    resources: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InitData":
        return cls(
            version=data.get("version", "unknown"),
            gateway_name=data.get("gateway_name", "unknown"),
            models=data.get("models", []),
            loaded_models=data.get("loaded_models", []),
            catalog=data.get("catalog", {}),
            resources=data.get("resources", {}),
        )


@dataclass
class ResourcesData:
    """Complete resource status - unified for WebSocket and HTTP paths."""

    # Resource metrics
    total_ram_mb: int = 0
    available_ram_mb: int = 0
    total_vram_mb: int = 0
    available_vram_mb: int = 0

    # Model state (populated from HTTP or inferred from WebSocket)
    loaded_models: frozenset[str] = field(default_factory=frozenset)
    busy_models: frozenset[str] = field(default_factory=frozenset)
    model_details: dict[str, Any] = field(default_factory=dict)

    # Event-cached timestamps (WebSocket-only)
    model_last_inference: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResourcesData":
        """Create from dict (HTTP response or WebSocket message)."""
        raw_loaded = data.get("loaded_models", [])
        raw_busy = data.get("busy_models", [])
        loaded_models = (
            frozenset(str(item) for item in raw_loaded)
            if isinstance(raw_loaded, list)
            else frozenset()
        )
        busy_models = (
            frozenset(str(item) for item in raw_busy)
            if isinstance(raw_busy, list)
            else frozenset()
        )
        raw_model_last_inference = data.get("model_last_inference", {})
        model_last_inference = (
            {
                str(model_id): float(ts)
                for model_id, ts in raw_model_last_inference.items()
                if isinstance(model_id, str) and isinstance(ts, int | float)
            }
            if isinstance(raw_model_last_inference, dict)
            else {}
        )
        model_details = data.get("model_details", {})
        if not isinstance(model_details, dict):
            model_details = {}
        return cls(
            total_ram_mb=data.get("total_ram_mb", 0),
            available_ram_mb=data.get("available_ram_mb", 0),
            total_vram_mb=data.get("total_vram_mb", 0),
            available_vram_mb=data.get("available_vram_mb", 0),
            loaded_models=loaded_models,
            busy_models=busy_models,
            model_details=model_details,
            model_last_inference=model_last_inference,
        )


def create_pong_message() -> dict[str, Any]:
    """Create PONG response message."""
    return {"type": MessageType.PONG.value, "timestamp": time.time()}


def create_query_message(
    query_type: str,
    params: dict[str, Any],
    request_id: str,
) -> dict[str, Any]:
    """Create QUERY message."""
    return {
        "type": MessageType.QUERY.value,
        "timestamp": time.time(),
        "query": query_type,
        "params": params,
        "request_id": request_id,
    }
