"""
Typed telemetry payloads for federation protocol.

Provides type-safe payload dataclasses with validation at wire boundary.
Factory functions enforce correct construction (parallel to @event_factory).

Wire flow:
  dict (JSON) → TelemetryPayload.from_dict() → typed processing →
  .to_dict() → MessageEnvelope

INVARIANT: ∀ telemetry: from_dict() validates ∨ raises ValueError
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, TypeVar

# Thread-local flag for payload construction authorization
_allow_construction = threading.local()

# Type variable for factory return type
F = TypeVar("F", bound=Callable[..., Any])

# ─── Signal Constants (SCREAMING_SNAKE for Python, dot.notation for wire) ────

TELEMETRY_RESOURCE_UPDATED = "telemetry.resource.updated"
TELEMETRY_MODEL_LOADED = "telemetry.model.loaded"
TELEMETRY_MODEL_UNLOADED = "telemetry.model.unloaded"
TELEMETRY_MODEL_BUSY = "telemetry.model.busy"
TELEMETRY_MODEL_IDLE = "telemetry.model.idle"
TELEMETRY_MODEL_LOADING_STARTED = "telemetry.model.loading.started"
TELEMETRY_MODEL_LOADING_FAILED = "telemetry.model.loading.failed"
TELEMETRY_HEARTBEAT = "telemetry.heartbeat"
TELEMETRY_GATEWAY_SNAPSHOT = "telemetry.gateway.snapshot"
TELEMETRY_REQUEST_INFERENCE_STARTED = "telemetry.request.inference.started"


def telemetry_factory[F: Callable[..., Any]](func: F) -> F:
    """
    Decorator for telemetry payload factory functions.

    Automatically manages thread-local construction flag to allow
    TelemetryPayload construction within the decorated function.

    Example:
        @telemetry_factory
        def ResourceUpdate(available_vram_mb: int, ...) -> ResourceUpdatePayload:
            return ResourceUpdatePayload(available_vram_mb=available_vram_mb, ...)

    Args:
        func: Factory function that returns a TelemetryPayload subclass

    Returns:
        Wrapped function with automatic flag management
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        _allow_construction.value = True
        try:
            payload = func(*args, **kwargs)
            # Verify factory returns a TelemetryPayload
            if not isinstance(payload, TelemetryPayload):
                msg = (
                    f"Factory function {func.__name__} must return TelemetryPayload, "
                    f"got {type(payload).__name__}"
                )
                raise TypeError(msg)
            return payload
        finally:
            _allow_construction.value = False

    return wrapper  # type: ignore


@dataclass(slots=True, kw_only=True)
class TelemetrySource:
    """Source identification for telemetry messages."""

    stargate_id: str
    gateway_id: str
    node_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = {
            "stargate_id": self.stargate_id,
            "gateway_id": self.gateway_id,
        }
        if self.node_id:
            result["node_id"] = self.node_id
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TelemetrySource:
        """Parse from wire format. Raises ValueError if invalid."""
        if "stargate_id" not in data:
            raise ValueError("TelemetrySource missing required field: stargate_id")
        if "gateway_id" not in data:
            raise ValueError("TelemetrySource missing required field: gateway_id")
        return cls(
            stargate_id=data["stargate_id"],
            gateway_id=data["gateway_id"],
            node_id=data.get("node_id", ""),
        )


@dataclass(slots=True, kw_only=True)
class TelemetryPayload:
    """
    Base class for typed telemetry payloads.

    MUST be created via factory functions decorated with @telemetry_factory.
    Direct construction raises RuntimeError.

    Subclasses define specific fields for each telemetry type.
    """

    source: TelemetrySource | None = None

    def __post_init__(self) -> None:
        """Enforce factory function usage."""
        if not getattr(_allow_construction, "value", False):
            msg = (
                f"{type(self).__name__} must be created via factory functions. "
                "Direct construction is forbidden. "
                "Use @telemetry_factory decorator on factory functions."
            )
            raise RuntimeError(msg)

    def to_dict(self) -> dict[str, Any]:
        """Convert to wire format (dict for MessageEnvelope.data)."""
        result: dict[str, Any] = {}
        if self.source is not None:
            result["source"] = self.source.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TelemetryPayload:
        """
        Parse from wire format with validation.

        Subclasses override to add type-specific parsing.
        Base implementation handles source field only.
        """
        raise NotImplementedError(
            f"{cls.__name__}.from_dict() must be implemented by subclass"
        )


# ─── Resource Update ─────────────────────────────────────────────────────────


@dataclass(slots=True, kw_only=True)
class ResourceUpdatePayload(TelemetryPayload):
    """
    Payload for RESOURCE_UPDATE telemetry.

    Dynamic resource state updates (resources, loaded/busy models).
    Does NOT include catalog data - use GatewaySnapshot for initial catalog.
    """

    # Required
    available_vram_mb: int
    available_ram_mb: int

    # Optional
    total_vram_mb: int | None = None
    total_ram_mb: int | None = None
    loaded_models: list[str] | None = None
    busy_models: list[str] | None = None
    max_concurrent_per_worker: int | None = None
    model_vram: dict[str, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to wire format (dict for MessageEnvelope.data)."""
        result = TelemetryPayload.to_dict(self)
        result["available_vram_mb"] = self.available_vram_mb
        result["available_ram_mb"] = self.available_ram_mb
        if self.total_vram_mb is not None:
            result["total_vram_mb"] = self.total_vram_mb
        if self.total_ram_mb is not None:
            result["total_ram_mb"] = self.total_ram_mb
        if self.loaded_models is not None:
            result["loaded_models"] = self.loaded_models
        if self.busy_models is not None:
            result["busy_models"] = self.busy_models
        if self.max_concurrent_per_worker is not None:
            result["max_concurrent_per_worker"] = self.max_concurrent_per_worker
        if self.model_vram is not None:
            result["model_vram"] = self.model_vram
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResourceUpdatePayload:
        """Parse from wire format. Raises ValueError if invalid."""
        # Required fields
        if "available_vram_mb" not in data:
            raise ValueError("ResourceUpdatePayload missing: available_vram_mb")
        if "available_ram_mb" not in data:
            raise ValueError("ResourceUpdatePayload missing: available_ram_mb")

        # Parse source if present
        source = None
        if "source" in data and data["source"]:
            source = TelemetrySource.from_dict(data["source"])

        # Use factory to construct
        return ResourceUpdate(
            available_vram_mb=data["available_vram_mb"],
            available_ram_mb=data["available_ram_mb"],
            total_vram_mb=data.get("total_vram_mb"),
            total_ram_mb=data.get("total_ram_mb"),
            loaded_models=data.get("loaded_models"),
            busy_models=data.get("busy_models"),
            max_concurrent_per_worker=data.get("max_concurrent_per_worker"),
            model_vram=data.get("model_vram"),
            source=source,
        )


# ─── Model Lifecycle ─────────────────────────────────────────────────────────


@dataclass(slots=True, kw_only=True)
class ModelLoadedPayload(TelemetryPayload):
    """
    Payload for MODEL_LOADED telemetry.

    NOTE: extra_data is intentionally flexible (dict[str, Any]) to support
    future extensions without breaking changes. If stricter type safety
    is needed, define specific optional fields instead.
    """

    model_id: str
    extra_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result = TelemetryPayload.to_dict(self)
        result["model_id"] = self.model_id
        result.update(self.extra_data)
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelLoadedPayload:
        if "model_id" not in data:
            raise ValueError("ModelLoadedPayload missing: model_id")

        source = None
        if "source" in data and data["source"]:
            source = TelemetrySource.from_dict(data["source"])

        # Extract extra data (anything not model_id or source)
        extra = {k: v for k, v in data.items() if k not in ("model_id", "source")}

        return ModelLoaded(
            model_id=data["model_id"],
            source=source,
            extra_data=extra,
        )


@dataclass(slots=True, kw_only=True)
class ModelUnloadedPayload(TelemetryPayload):
    """Payload for MODEL_UNLOADED telemetry."""

    model_id: str

    def to_dict(self) -> dict[str, Any]:
        result = TelemetryPayload.to_dict(self)
        result["model_id"] = self.model_id
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelUnloadedPayload:
        if "model_id" not in data:
            raise ValueError("ModelUnloadedPayload missing: model_id")

        source = None
        if "source" in data and data["source"]:
            source = TelemetrySource.from_dict(data["source"])

        return ModelUnloaded(model_id=data["model_id"], source=source)


@dataclass(slots=True, kw_only=True)
class ModelBusyPayload(TelemetryPayload):
    """Payload for MODEL_BUSY telemetry."""

    model_id: str

    def to_dict(self) -> dict[str, Any]:
        result = TelemetryPayload.to_dict(self)
        result["model_id"] = self.model_id
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelBusyPayload:
        if "model_id" not in data:
            raise ValueError("ModelBusyPayload missing: model_id")

        source = None
        if "source" in data and data["source"]:
            source = TelemetrySource.from_dict(data["source"])

        return ModelBusy(model_id=data["model_id"], source=source)


@dataclass(slots=True, kw_only=True)
class ModelIdlePayload(TelemetryPayload):
    """Payload for MODEL_IDLE telemetry."""

    model_id: str

    def to_dict(self) -> dict[str, Any]:
        result = TelemetryPayload.to_dict(self)
        result["model_id"] = self.model_id
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelIdlePayload:
        if "model_id" not in data:
            raise ValueError("ModelIdlePayload missing: model_id")

        source = None
        if "source" in data and data["source"]:
            source = TelemetrySource.from_dict(data["source"])

        return ModelIdle(model_id=data["model_id"], source=source)


@dataclass(slots=True, kw_only=True)
class ModelLoadingStartedPayload(TelemetryPayload):
    """Payload for MODEL_LOADING_STARTED telemetry."""

    model_id: str

    def to_dict(self) -> dict[str, Any]:
        result = TelemetryPayload.to_dict(self)
        result["model_id"] = self.model_id
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelLoadingStartedPayload:
        if "model_id" not in data:
            raise ValueError("ModelLoadingStartedPayload missing: model_id")

        source = None
        if "source" in data and data["source"]:
            source = TelemetrySource.from_dict(data["source"])

        return ModelLoadingStarted(model_id=data["model_id"], source=source)


@dataclass(slots=True, kw_only=True)
class ModelLoadFailedPayload(TelemetryPayload):
    """Payload for MODEL_LOAD_FAILED telemetry.

    Snapshots are forensics-only diagnostic enrichment. Both are optional and
    forwarded as opaque dicts so the protocol does not need to track every
    field the gateway/edge stargate decides to capture.
    """

    model_id: str
    error: str | None = None
    worker_snapshot: dict[str, Any] | None = None
    gateway_state_snapshot: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result = TelemetryPayload.to_dict(self)
        result["model_id"] = self.model_id
        if self.error is not None:
            result["error"] = self.error
        if self.worker_snapshot is not None:
            result["worker_snapshot"] = self.worker_snapshot
        if self.gateway_state_snapshot is not None:
            result["gateway_state_snapshot"] = self.gateway_state_snapshot
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelLoadFailedPayload:
        if "model_id" not in data:
            raise ValueError("ModelLoadFailedPayload missing: model_id")

        source = None
        if "source" in data and data["source"]:
            source = TelemetrySource.from_dict(data["source"])

        return ModelLoadFailed(
            model_id=data["model_id"],
            error=data.get("error"),
            worker_snapshot=data.get("worker_snapshot"),
            gateway_state_snapshot=data.get("gateway_state_snapshot"),
            source=source,
        )


# ─── Heartbeat ───────────────────────────────────────────────────────────────


@dataclass(slots=True, kw_only=True)
class TelemetryHeartbeatPayload(TelemetryPayload):
    """Payload for TELEMETRY_HEARTBEAT."""

    gateway_id: str

    def to_dict(self) -> dict[str, Any]:
        result = TelemetryPayload.to_dict(self)
        result["gateway_id"] = self.gateway_id
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TelemetryHeartbeatPayload:
        if "gateway_id" not in data:
            raise ValueError("TelemetryHeartbeatPayload missing: gateway_id")

        source = None
        if "source" in data and data["source"]:
            source = TelemetrySource.from_dict(data["source"])

        return TelemetryHeartbeat(gateway_id=data["gateway_id"], source=source)


# ─── Gateway Snapshot ────────────────────────────────────────────────────────


@dataclass(slots=True, kw_only=True)
class GatewaySnapshotPayload(TelemetryPayload):
    """
    Payload for GATEWAY_SNAPSHOT telemetry.

    Sent once when Edge Stargate first wires Gateway telemetry.
    Contains complete catalog (available_models, model_resources) plus
    initial resource state.

    This is the authoritative source for catalog data - RESOURCE_UPDATE
    messages only contain dynamic resource changes.

    Activation filtering:
    - activated_models: Subset of available_models for public /v1/models display
    - activated_contexts: Rules to compute activated_models (base_model -> contexts)
    """

    # Required: Resource state
    available_vram_mb: int
    available_ram_mb: int
    total_vram_mb: int
    total_ram_mb: int

    # Required: Catalog data (full list for routing)
    available_models: list[str]
    model_resources: dict[str, dict[str, int | str]]

    # Optional: Activation filtering for /v1/models
    activated_models: list[str] | None = None
    activated_contexts: dict[str, dict[str, list[int]]] | None = None

    # Optional: Initial model state
    loaded_models: list[str] | None = None
    busy_models: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to wire format."""
        result = TelemetryPayload.to_dict(self)
        result["available_vram_mb"] = self.available_vram_mb
        result["available_ram_mb"] = self.available_ram_mb
        result["total_vram_mb"] = self.total_vram_mb
        result["total_ram_mb"] = self.total_ram_mb
        result["available_models"] = self.available_models
        result["model_resources"] = self.model_resources
        if self.activated_models is not None:
            result["activated_models"] = self.activated_models
        if self.activated_contexts is not None:
            result["activated_contexts"] = self.activated_contexts
        if self.loaded_models is not None:
            result["loaded_models"] = self.loaded_models
        if self.busy_models is not None:
            result["busy_models"] = self.busy_models
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GatewaySnapshotPayload:
        """Parse from wire format. Raises ValueError if invalid."""
        # Required fields
        required = [
            "available_vram_mb",
            "available_ram_mb",
            "total_vram_mb",
            "total_ram_mb",
            "available_models",
            "model_resources",
        ]
        for field_name in required:
            if field_name not in data:
                raise ValueError(f"GatewaySnapshotPayload missing: {field_name}")

        # Parse source if present
        source = None
        if "source" in data and data["source"]:
            source = TelemetrySource.from_dict(data["source"])

        # Use factory to construct
        return GatewaySnapshot(
            available_vram_mb=data["available_vram_mb"],
            available_ram_mb=data["available_ram_mb"],
            total_vram_mb=data["total_vram_mb"],
            total_ram_mb=data["total_ram_mb"],
            available_models=data["available_models"],
            model_resources=data["model_resources"],
            activated_models=data.get("activated_models"),
            activated_contexts=data.get("activated_contexts"),
            loaded_models=data.get("loaded_models"),
            busy_models=data.get("busy_models"),
            source=source,
        )


# ─── Factory Functions ───────────────────────────────────────────────────────


@telemetry_factory
def ResourceUpdate(  # noqa: N802
    available_vram_mb: int,
    available_ram_mb: int,
    total_vram_mb: int | None = None,
    total_ram_mb: int | None = None,
    loaded_models: list[str] | None = None,
    busy_models: list[str] | None = None,
    max_concurrent_per_worker: int | None = None,
    model_vram: dict[str, int] | None = None,
    source: TelemetrySource | None = None,
) -> ResourceUpdatePayload:
    """
    Create telemetry.resource.updated payload.

    Signal: TELEMETRY_RESOURCE_UPDATED

    NOTE: available_models and model_resources are NOT accepted here.
    These static fields are only sent in initial telemetry, not in resource updates.
    """
    return ResourceUpdatePayload(
        available_vram_mb=available_vram_mb,
        available_ram_mb=available_ram_mb,
        total_vram_mb=total_vram_mb,
        total_ram_mb=total_ram_mb,
        loaded_models=loaded_models,
        busy_models=busy_models,
        max_concurrent_per_worker=max_concurrent_per_worker,
        model_vram=model_vram,
        # available_models and model_resources intentionally omitted
        source=source,
    )


@telemetry_factory
def ModelLoaded(  # noqa: N802
    model_id: str,
    source: TelemetrySource | None = None,
    extra_data: dict[str, Any] | None = None,
) -> ModelLoadedPayload:
    """
    Create telemetry.model.loaded payload.

    Signal: TELEMETRY_MODEL_LOADED
    """
    return ModelLoadedPayload(
        model_id=model_id,
        source=source,
        extra_data=extra_data or {},
    )


@telemetry_factory
def ModelUnloaded(  # noqa: N802
    model_id: str,
    source: TelemetrySource | None = None,
) -> ModelUnloadedPayload:
    """
    Create telemetry.model.unloaded payload.

    Signal: TELEMETRY_MODEL_UNLOADED
    """
    return ModelUnloadedPayload(model_id=model_id, source=source)


@telemetry_factory
def ModelBusy(  # noqa: N802
    model_id: str,
    source: TelemetrySource | None = None,
) -> ModelBusyPayload:
    """
    Create telemetry.model.busy payload.

    Signal: TELEMETRY_MODEL_BUSY
    """
    return ModelBusyPayload(model_id=model_id, source=source)


@telemetry_factory
def ModelIdle(  # noqa: N802
    model_id: str,
    source: TelemetrySource | None = None,
) -> ModelIdlePayload:
    """
    Create telemetry.model.idle payload.

    Signal: TELEMETRY_MODEL_IDLE
    """
    return ModelIdlePayload(model_id=model_id, source=source)


@telemetry_factory
def ModelLoadingStarted(  # noqa: N802
    model_id: str,
    source: TelemetrySource | None = None,
) -> ModelLoadingStartedPayload:
    """
    Create telemetry.model.loading.started payload.

    Signal: TELEMETRY_MODEL_LOADING_STARTED
    """
    return ModelLoadingStartedPayload(model_id=model_id, source=source)


@telemetry_factory
def ModelLoadFailed(  # noqa: N802
    model_id: str,
    error: str | None = None,
    worker_snapshot: dict[str, Any] | None = None,
    gateway_state_snapshot: dict[str, Any] | None = None,
    source: TelemetrySource | None = None,
) -> ModelLoadFailedPayload:
    """
    Create telemetry.model.loading.failed payload.

    Signal: TELEMETRY_MODEL_LOADING_FAILED
    """
    return ModelLoadFailedPayload(
        model_id=model_id,
        error=error,
        worker_snapshot=worker_snapshot,
        gateway_state_snapshot=gateway_state_snapshot,
        source=source,
    )


@telemetry_factory
def TelemetryHeartbeat(  # noqa: N802
    gateway_id: str,
    source: TelemetrySource | None = None,
) -> TelemetryHeartbeatPayload:
    """
    Create telemetry.heartbeat payload.

    Signal: TELEMETRY_HEARTBEAT
    """
    return TelemetryHeartbeatPayload(gateway_id=gateway_id, source=source)


@telemetry_factory
def GatewaySnapshot(  # noqa: N802
    available_vram_mb: int,
    available_ram_mb: int,
    total_vram_mb: int,
    total_ram_mb: int,
    available_models: list[str],
    model_resources: dict[str, dict[str, int | str]],
    activated_models: list[str] | None = None,
    activated_contexts: dict[str, dict[str, list[int]]] | None = None,
    loaded_models: list[str] | None = None,
    busy_models: list[str] | None = None,
    source: TelemetrySource | None = None,
) -> GatewaySnapshotPayload:
    """
    Create telemetry.gateway.snapshot payload.

    Signal: TELEMETRY_GATEWAY_SNAPSHOT

    Initial catalog snapshot sent when Edge Stargate first wires Gateway.
    Contains complete available_models list and model_resources for routing.

    Args:
        available_models: Full model list (all contexts) for routing capability
        activated_models: Filtered subset for public /v1/models endpoint
        activated_contexts: Activation rules (base_model -> {gpu: [...], cpu: [...]})
    """
    return GatewaySnapshotPayload(
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
        source=source,
    )


# ─── Request Inference Started ───────────────────────────────────────────────


@dataclass(slots=True, kw_only=True)
class RequestInferenceStartedPayload(TelemetryPayload):
    """
    Payload for REQUEST_INFERENCE_STARTED telemetry.

    Emitted by Gateway when inference for a specific client request actually
    begins (model lock acquired, first token generation starts). Provides the
    request-scoped counterpart to model-scoped InferenceStarted, allowing
    Stargate to distinguish queue-wait time from true GPU execution time.
    """

    request_id: str
    model_id: str
    gateway_url: str
    correlation_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = TelemetryPayload.to_dict(self)
        result["request_id"] = self.request_id
        result["model_id"] = self.model_id
        result["gateway_url"] = self.gateway_url
        if self.correlation_id:
            result["correlation_id"] = self.correlation_id
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RequestInferenceStartedPayload:
        source = None
        if "source" in data and data["source"]:
            source = TelemetrySource.from_dict(data["source"])
        return RequestInferenceStarted(
            request_id=data["request_id"],
            model_id=data["model_id"],
            gateway_url=data.get("gateway_url", ""),
            correlation_id=data.get("correlation_id", ""),
            source=source,
        )


@telemetry_factory
def RequestInferenceStarted(  # noqa: N802
    request_id: str,
    model_id: str,
    gateway_url: str,
    correlation_id: str = "",
    source: TelemetrySource | None = None,
) -> RequestInferenceStartedPayload:
    """
    Create telemetry.request.inference.started payload.

    Signal: TELEMETRY_REQUEST_INFERENCE_STARTED

    Emitted by Gateway at the moment inference begins for a specific request.
    Forwarded via WebSocket telemetry to Edge Stargate → Master Stargate.
    """
    return RequestInferenceStartedPayload(
        request_id=request_id,
        model_id=model_id,
        gateway_url=gateway_url,
        correlation_id=correlation_id,
        source=source,
    )


# ─── Payload Registry ────────────────────────────────────────────────────────

TELEMETRY_PAYLOAD_TYPES: dict[str, type[TelemetryPayload]] = {
    TELEMETRY_RESOURCE_UPDATED: ResourceUpdatePayload,
    TELEMETRY_MODEL_LOADED: ModelLoadedPayload,
    TELEMETRY_MODEL_UNLOADED: ModelUnloadedPayload,
    TELEMETRY_MODEL_BUSY: ModelBusyPayload,
    TELEMETRY_MODEL_IDLE: ModelIdlePayload,
    TELEMETRY_MODEL_LOADING_STARTED: ModelLoadingStartedPayload,
    TELEMETRY_MODEL_LOADING_FAILED: ModelLoadFailedPayload,
    TELEMETRY_HEARTBEAT: TelemetryHeartbeatPayload,
    TELEMETRY_GATEWAY_SNAPSHOT: GatewaySnapshotPayload,
    TELEMETRY_REQUEST_INFERENCE_STARTED: RequestInferenceStartedPayload,
}


def parse_telemetry(msg_type: str, data: dict[str, Any]) -> TelemetryPayload:
    """
    Parse telemetry data into typed payload.

    Args:
        msg_type: Message type string (e.g., "telemetry.resource.updated")
        data: Raw telemetry data dict

    Returns:
        Typed TelemetryPayload subclass

    Raises:
        ValueError: If msg_type unknown or data invalid
    """
    payload_cls = TELEMETRY_PAYLOAD_TYPES.get(msg_type)
    if payload_cls is None:
        raise ValueError(f"Unknown telemetry type: {msg_type}")
    return payload_cls.from_dict(data)


def validate_telemetry_registry() -> None:
    """
    Validate all registered telemetry signals at import time.

    Called at module load to ensure all TELEMETRY_PAYLOAD_TYPES
    keys follow dot-notation spec.

    Raises:
        ValueError: If any registered signal is invalid
    """
    from universal_protocol.messages.validation import validate_telemetry_signal

    for signal in TELEMETRY_PAYLOAD_TYPES:
        validate_telemetry_signal(signal)


# Validate at import time
validate_telemetry_registry()
