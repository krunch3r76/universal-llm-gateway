"""
LLM-specific event signals for Universal LLM Gateway.

These events use the UML Message structure from universal_event_bus.
All events are published using Event(signal, payload) with auto-injected
timestamp and id.

This module defines signal constants for type safety and documentation.
"""

from typing import Any

from universal_event_bus import Event, event_factory

# ========== Model Lifecycle Event Signals ==========

MODEL_LOADING_STARTED = "model.loading.started"
"""
Emitted when a model loading operation begins.

Payload:
    model_id: str - Unique identifier for the model being loaded
"""

MODEL_LOADED = "model.loaded"
"""
Emitted when a model is engine-ready: the worker process is alive AND the
inference engine reports is_loaded() == True. This is the authoritative
readiness signal - consumers (remote load endpoint, master routing,
resource tracker) may treat this as "ready for immediate inference and
token counting".

Payload:
    model_id: str - Unique identifier for the loaded model
    vram_usage_mb: int - Amount of VRAM consumed by the model in megabytes
    ram_usage_mb: int - Amount of RAM consumed by the model in megabytes
    process_pid: Optional[int] - Process ID of the worker process hosting the model
"""

MODEL_LOAD_FAILED = "model.load.failed"
"""
Emitted when a model loading operation fails.

Payload:
    model_id: str - Unique identifier for the model that failed to load
    error_message: str - Description of the error that occurred
    failure_reason: str - Categorized reason code (see below)

Failure Reason Codes:
    "insufficient_vram" - Not enough VRAM available
    "insufficient_ram" - Not enough RAM available
    "missing_file" - Model file not found
    "worker_crash" - Worker process crashed during loading
    "timeout" - Loading operation timed out
    "config_error" - Invalid model configuration
    "oom" - Out of memory error from engine
    "unknown" - Unclassified error
"""

MODEL_LOAD_BLOCKED = "model.load.blocked"
"""
Emitted when preflight resource check blocks model loading (Recommendation #7).

This enables circuit breaker behavior - Stargate receives immediate notification
instead of waiting for timeout.

Payload:
    model_id: str - Model that was blocked from loading
    reason: str - Human-readable reason (e.g., "VRAM: need 32084MB, have 28000MB")
    required_vram_mb: int - VRAM required for model
    available_vram_mb: int - VRAM currently available
    required_ram_mb: int - RAM required for model
    available_ram_mb: int - RAM currently available
    bypassed_margin: bool - Whether safety margin was bypassed
"""

MODEL_LOAD_CONTEXT_MISMATCH = "model.load.context.mismatch"
"""
Emitted when a profile loader contained a stale n_ctx that was overridden by the
profile key, OR when a pre-load validation detects requested context ≠ resolved n_ctx.

Payload:
    model_id: str - Synthetic model ID (e.g., 'qwen3-5-9b-q8-0-262144')
    requested_context: int - Context encoded in the synthetic model ID
    actual_context: int - n_ctx value found in the profile loader before override
    reason: str - 'stale_profile_loader' | 'profile_not_found'
"""

MODEL_UNLOADING_STARTED = "model.unloading.started"
"""
Emitted when a model unloading operation begins.

Payload:
    model_id: str - Unique identifier for the model being unloaded
"""

MODEL_UNLOADED = "model.unloaded"
"""
Emitted when a model has been successfully unloaded from memory.

Payload:
    model_id: str - Unique identifier for the unloaded model
"""


# ========== Inference Lifecycle Event Signals ==========

REQUEST_QUEUED = "request.queued"
"""
Emitted when a request is queued for processing (immediately before
semaphore acquisition).

Payload:
    model_id: str - Unique identifier for the model that will process the request
    request_id: str - Unique identifier for this request
    messages: List[Dict[str, str]] - The chat messages or prompt being processed
    parameters: Dict[str, Any] - Generation parameters (temperature, max_tokens, etc.)
    stream: bool - Whether this is a streaming request
"""

REQUEST_INFERENCE_STARTED = "request.inference.started"
"""
Emitted when gateway runtime execution begins for a specific request.

Payload:
    request_id: str - Unique identifier for this request
    model_id: str - Unique identifier for the model handling the request
    gateway_url: str - Gateway URL/identity where runtime execution starts
    correlation_id: Optional[str] - Cross-service trace correlation identifier
"""

INFERENCE_STARTED = "inference.started"
"""
Emitted when a model transitions to BUSY state (inference begins).

**Contract**: Model-scoped lifecycle event (not request-scoped).

Payload:
    model_id: str - Unique identifier for the model performing inference

**Invariant**: ∀ inference_start, ∃! emission via resource_tracker.set_model_busy()

Note: For request-level tracking, use REQUEST_QUEUED event instead.
"""

INFERENCE_COMPLETED = "inference.completed"
"""
Emitted when a model transitions from BUSY to LOADED state (inference ends).

**Contract**: Model-scoped lifecycle event (not request-scoped).

Payload:
    model_id: str - Unique identifier for the model that performed inference
    last_inference_time: float - Unix timestamp when inference completed
        (for LRU eviction)

**Invariant**: ∀ inference_end, ∃! emission via resource_tracker.set_model_idle()

Note: For request-level completion tracking with duration/tokens, use other events.
"""

INFERENCE_FAILED = "inference.failed"
"""
Emitted when an inference request fails.

Payload:
    model_id: str - Unique identifier for the model that attempted inference
    request_id: str - Unique identifier for this inference request
    error_message: str - Description of the error that occurred
"""

STREAM_CANCELLED = "stream.cancelled"
"""
Emitted when a streaming inference request is cancelled.

Payload:
    model_id: str - Unique identifier for the model performing inference
    stream_id: str - Stream ID of the cancelled stream
    reason: str - Reason for cancellation
        (e.g., "client_disconnect", "explicit_cancellation")
    worker_ready: bool - Whether the worker is immediately ready for new requests
"""

STREAM_CANCELLATION_COMPLETE = "stream.cancellation.complete"
"""
Emitted when stream cancellation cleanup is complete.

Payload:
    model_id: str - Unique identifier for the model
    stream_id: str - Stream ID of the cancelled stream
    cleanup_duration: float - Time taken for cleanup in seconds
"""


# ========== System Resource Event Signals ==========

SYSTEM_RESOURCES_UPDATED = "system.resources.updated"
"""
Emitted when system resource information is updated.

Payload:
    total_vram_mb: int - Total VRAM available on the system in megabytes
    available_vram_mb: int - Currently available VRAM in megabytes
    total_ram_mb: int - Total RAM available on the system in megabytes
    available_ram_mb: int - Currently available RAM in megabytes
"""

INFERENCE_RESOURCE_UPDATE = "inference.resource.update"
"""
Emitted when resource usage is updated during inference.

Payload:
    model_id: str - Model identifier
    request_id: str - Request identifier
    timestamp: float - Timestamp of the reading
    vram_used_mb: int - Current VRAM usage in megabytes
    ram_used_mb: int - Current RAM usage in megabytes
    vram_max_mb: int - Maximum VRAM usage observed
    ram_max_mb: int - Maximum RAM usage observed
    gpu_utilization: float - GPU utilization percentage
    inference_duration: float - Duration of inference so far
    worker_config: Dict[str, Any] - Worker configuration associated with this reading
"""


# ========== Worker Crash Detection Event Signals ==========

WORKER_CRASH_DETECTED = "worker.crash.detected"
"""
Emitted when a worker process crashes unexpectedly.

Payload:
    model_id: str - Model ID of the crashed worker
    error_message: str - Error message describing the crash
    socket_path: str - Path to the orphaned socket file
    process_pid: Optional[int] - PID of the crashed process
"""

SOCKET_ORPHANED = "socket.orphaned"
"""
Emitted when an orphaned socket file is detected and cleaned up.

Payload:
    model_id: str - Model ID associated with the orphaned socket
    socket_path: str - Path to the orphaned socket file
    cleanup_successful: bool - Whether cleanup was successful
    error: Optional[str] - Error message if cleanup failed
"""

HEALTH_CHECK_FAILED = "health.check.failed"
"""
Emitted when a health check fails for a worker process.

Payload:
    model_id: str - Model ID of the worker
    error_message: str - Error message describing the failure
    socket_path: str - Path to the socket file
"""


# ========== Gateway Lifecycle Event Signals ==========

GATEWAY_SHUTDOWN = "gateway.shutdown"
"""
Emitted when the gateway is shutting down immediately.

Subscribers should NOT expect in-flight requests to complete.
Stargate uses this to retry/reroute requests to other gateways.

Payload:
    gateway_id: str - Identifier of the gateway shutting down
    reason: str - Reason for shutdown (e.g., "signal", "requested")
    timestamp: float - Unix timestamp of shutdown initiation
"""

GATEWAY_DRAINING = "gateway.draining"
"""
Emitted when the gateway begins graceful shutdown (draining mode).

Subscribers MAY expect in-flight requests to complete within the drain timeout.
New requests should be routed to other gateways.

Payload:
    gateway_id: str - Identifier of the gateway draining
    reason: str - Reason for shutdown
    timeout: float - Seconds until forced shutdown
    timestamp: float - Unix timestamp of drain initiation
"""

VRAM_ORPHAN_DETECTED = "gateway.vram.orphan.detected"
"""
Emitted when hardware VRAM exceeds tracked model VRAM by > threshold.
Indicates unmanaged GPU processes outside the model lifecycle.

Payload:
    hardware_used_mb: int - VRAM used per pynvml
    catalog_used_mb: int - VRAM tracked by resource tracker (measured when available)
    discrepancy_mb: int - positive delta (hardware - catalog)
    tracked_models: list[str] - currently tracked model IDs
"""

VRAM_STALENESS_DETECTED = "gateway.vram.staleness.detected"
"""
Emitted when tracked model VRAM exceeds hardware VRAM by > threshold.
Indicates catalog values are stale — tracked models not using claimed VRAM.

Payload:
    hardware_used_mb: int - VRAM used per pynvml
    catalog_used_mb: int - VRAM tracked by resource tracker (measured when available)
    discrepancy_mb: int - negative delta (hardware - catalog)
    tracked_models: list[str] - currently tracked model IDs
"""

PHANTOM_MODEL_DETECTED = "gateway.model.phantom.detected"
"""
Emitted when a running worker process is not tracked as LOADED/BUSY.

Payload:
    model_id: str - Model ID of phantom process
    process_status: str - Runtime process status (e.g. "running")
    tracker_status: str | None - Current ResourceTracker status if present
"""

PHANTOM_MODEL_CLEANED = "gateway.model.phantom.cleaned"
"""
Emitted after phantom cleanup attempt.

Payload:
    model_id: str - Model ID of phantom process
    success: bool - Whether cleanup succeeded
    vram_freed_mb: int | None - Estimated VRAM reclaimed by cleanup
"""

GHOST_MODEL_CLEANED = "gateway.model.ghost.cleaned"
"""
Emitted after ghost model cleanup (tracked as loaded but engine dead).

Payload:
    model_id: str - Model ID of ghost process
    success: bool - Whether cleanup succeeded
    vram_freed_mb: int | None - Estimated VRAM reclaimed by cleanup
"""


# ========== Compute Capacity Telemetry Event Signals ==========

COMPUTE_CAPACITY_QUEUE_WAIT = "compute.capacity.queue.wait"
"""
Emitted when a request must queue because compute capacity is at limit.

Signals orchestration drift - Stargate's view was out of sync with Gateway.

Payload:
    request_id: str - Request being queued
    model_id: str - Model the request is for
    compute_type: str - "cpu", "hybrid", or "gpu"
    queue_position: int - Queue position at enqueue time (1 = first in line)
    active_count: int - Current active requests
    limit: int - Capacity limit
    timestamp_ms: int - Milliseconds since epoch
"""

COMPUTE_CAPACITY_QUEUE_ACQUIRED = "compute.capacity.queue.acquired"
"""
Emitted when a request acquires a compute slot after waiting in queue.

Payload:
    request_id: str - Request that acquired slot
    model_id: str - Model the request is for
    compute_type: str - "cpu", "hybrid", or "gpu"
    wait_duration_ms: float - Time spent waiting in queue
    queue_position_at_enqueue: int - Position when enqueued (for correlation)
    timestamp_ms: int - Milliseconds since epoch
"""


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


# ========== Factory Functions ==========
# ruff: noqa: N802 - Factory function names match event signal names


# Model Lifecycle Event Factories
@event_factory
def ModelLoadingStarted(model_id: str) -> Event:
    """
    Create MODEL_LOADING_STARTED event.

    Args:
        model_id: Unique identifier for the model being loaded

    Returns:
        Event with ModelLoadingStarted signal
    """
    return Event(signal=MODEL_LOADING_STARTED, payload={"model_id": model_id})


@event_factory
def ModelLoaded(
    model_id: str,
    vram_usage_mb: int,
    ram_usage_mb: int,
    process_pid: int | None = None,
) -> Event:
    """
    Create MODEL_LOADED event.

    Args:
        model_id: Unique identifier for the loaded model
        vram_usage_mb: Amount of VRAM consumed in megabytes
        ram_usage_mb: Amount of RAM consumed in megabytes
        process_pid: Optional process ID of worker hosting the model

    Returns:
        Event with ModelLoaded signal
    """
    return Event(
        signal=MODEL_LOADED,
        payload={
            "model_id": model_id,
            "vram_usage_mb": vram_usage_mb,
            "ram_usage_mb": ram_usage_mb,
            "process_pid": process_pid,
        },
    )


@event_factory
def ModelLoadFailed(
    model_id: str, error_message: str, failure_reason: str = "unknown"
) -> Event:
    """
    Create MODEL_LOAD_FAILED event.

    Args:
        model_id: Model that failed to load
        error_message: Description of the failure
        failure_reason: Categorized reason code (default: "unknown")

    Returns:
        Event with ModelLoadFailed signal
    """
    return Event(
        signal=MODEL_LOAD_FAILED,
        payload={
            "model_id": model_id,
            "error_message": error_message,
            "failure_reason": failure_reason,
        },
    )


@event_factory
def ModelLoadBlocked(
    model_id: str,
    reason: str,
    required_vram_mb: int,
    available_vram_mb: int,
    required_ram_mb: int,
    available_ram_mb: int,
    bypassed_margin: bool = False,
) -> Event:
    """
    Create MODEL_LOAD_BLOCKED event (Recommendation #7: Observability).

    Emitted when preflight resource check blocks loading. Enables circuit breaker
    behavior - Stargate receives immediate notification instead of timeout.

    Args:
        model_id: Model blocked from loading
        reason: Human-readable reason
        required_vram_mb: VRAM required
        available_vram_mb: VRAM available
        required_ram_mb: RAM required
        available_ram_mb: RAM available
        bypassed_margin: Whether safety margin was bypassed

    Returns:
        Event with ModelLoadBlocked signal
    """
    return Event(
        signal=MODEL_LOAD_BLOCKED,
        payload={
            "model_id": model_id,
            "reason": reason,
            "required_vram_mb": required_vram_mb,
            "available_vram_mb": available_vram_mb,
            "required_ram_mb": required_ram_mb,
            "available_ram_mb": available_ram_mb,
            "bypassed_margin": bypassed_margin,
        },
    )


@event_factory
def ModelLoadContextMismatch(
    model_id: str,
    requested_context: int,
    actual_context: int,
    reason: str = "stale_profile_loader",
) -> Event:
    """
    Create MODEL_LOAD_CONTEXT_MISMATCH event.

    Args:
        model_id: Synthetic model ID encoding the requested context
        requested_context: Context encoded in the model ID suffix
        actual_context: n_ctx value found in the profile loader
        reason: Classification of why the mismatch occurred

    Returns:
        Event with MODEL_LOAD_CONTEXT_MISMATCH signal
    """
    return Event(
        signal=MODEL_LOAD_CONTEXT_MISMATCH,
        payload={
            "model_id": model_id,
            "requested_context": requested_context,
            "actual_context": actual_context,
            "reason": reason,
        },
    )


@event_factory
def ModelUnloadingStarted(model_id: str) -> Event:
    """
    Create MODEL_UNLOADING_STARTED event.

    Args:
        model_id: Model being unloaded

    Returns:
        Event with ModelUnloadingStarted signal
    """
    return Event(signal=MODEL_UNLOADING_STARTED, payload={"model_id": model_id})


@event_factory
def ModelUnloaded(model_id: str) -> Event:
    """
    Create MODEL_UNLOADED event.

    Args:
        model_id: Model that was unloaded

    Returns:
        Event with ModelUnloaded signal
    """
    return Event(signal=MODEL_UNLOADED, payload={"model_id": model_id})


# Inference Lifecycle Event Factories
@event_factory
def RequestQueued(
    model_id: str,
    request_id: str,
    messages: list[dict[str, str]],
    parameters: dict[str, Any],
    stream: bool,
) -> Event:
    """
    Create REQUEST_QUEUED event.

    Args:
        model_id: Model that will process the request
        request_id: Unique identifier for this request
        messages: Chat messages or prompt
        parameters: Generation parameters (temperature, max_tokens, etc.)
        stream: Whether this is a streaming request

    Returns:
        Event with RequestQueued signal
    """
    return Event(
        signal=REQUEST_QUEUED,
        payload={
            "model_id": model_id,
            "request_id": request_id,
            "messages": messages,
            "parameters": parameters,
            "stream": stream,
        },
    )


@event_factory
def RequestInferenceStarted(
    request_id: str,
    model_id: str,
    gateway_url: str,
    correlation_id: str | None = None,
) -> Event:
    """
    Create REQUEST_INFERENCE_STARTED event.

    Request-scoped runtime boundary event emitted at execution handoff.

    Args:
        request_id: Unique identifier for this request
        model_id: Model handling the request
        gateway_url: Gateway URL/identity for runtime start
        correlation_id: Optional cross-service trace correlation ID

    Returns:
        Event with RequestInferenceStarted signal
    """
    return Event(
        signal=REQUEST_INFERENCE_STARTED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_url": gateway_url,
            "correlation_id": correlation_id,
        },
    )


@event_factory
def InferenceStarted(model_id: str) -> Event:
    """
    Create INFERENCE_STARTED event.

    Model-scoped lifecycle event (not request-scoped).

    Args:
        model_id: Model performing inference

    Returns:
        Event with InferenceStarted signal
    """
    return Event(signal=INFERENCE_STARTED, payload={"model_id": model_id})


@event_factory
def InferenceCompleted(model_id: str, last_inference_time: float) -> Event:
    """
    Create INFERENCE_COMPLETED event.

    Model-scoped lifecycle event (not request-scoped).

    Args:
        model_id: Model that performed inference
        last_inference_time: Unix timestamp when inference completed (for LRU)

    Returns:
        Event with InferenceCompleted signal
    """
    return Event(
        signal=INFERENCE_COMPLETED,
        payload={"model_id": model_id, "last_inference_time": last_inference_time},
    )


@event_factory
def InferenceFailed(model_id: str, request_id: str, error_message: str) -> Event:
    """
    Create INFERENCE_FAILED event.

    Args:
        model_id: Model that attempted inference
        request_id: Request that failed
        error_message: Description of the failure

    Returns:
        Event with InferenceFailed signal
    """
    return Event(
        signal=INFERENCE_FAILED,
        payload={
            "model_id": model_id,
            "request_id": request_id,
            "error_message": error_message,
        },
    )


@event_factory
def StreamCancelled(
    model_id: str, stream_id: str, reason: str, worker_ready: bool
) -> Event:
    """
    Create STREAM_CANCELLED event.

    Args:
        model_id: Model performing inference
        stream_id: Stream ID of cancelled stream
        reason: Reason for cancellation (e.g., "client_disconnect")
        worker_ready: Whether worker is immediately ready for new requests

    Returns:
        Event with StreamCancelled signal
    """
    return Event(
        signal=STREAM_CANCELLED,
        payload={
            "model_id": model_id,
            "stream_id": stream_id,
            "reason": reason,
            "worker_ready": worker_ready,
        },
    )


@event_factory
def StreamCancellationComplete(
    model_id: str, stream_id: str, cleanup_duration: float
) -> Event:
    """
    Create STREAM_CANCELLATION_COMPLETE event.

    Args:
        model_id: Model identifier
        stream_id: Stream ID of the cancelled stream
        cleanup_duration: Time taken for cleanup in seconds

    Returns:
        Event with StreamCancellationComplete signal
    """
    return Event(
        signal=STREAM_CANCELLATION_COMPLETE,
        payload={
            "model_id": model_id,
            "stream_id": stream_id,
            "cleanup_duration": cleanup_duration,
        },
    )


# System Resource Event Factories
@event_factory
def SystemResourcesUpdated(
    total_vram_mb: int,
    available_vram_mb: int,
    total_ram_mb: int,
    available_ram_mb: int,
    model_vram: dict[str, int] | None = None,
) -> Event:
    """
    Create SYSTEM_RESOURCES_UPDATED event.

    Single-writer invariant: lifecycle state (loaded_models) is exclusively
    from MODEL_LOADED / MODEL_UNLOADED discrete events. RESOURCE_UPDATE
    carries only resource metrics and per-model VRAM measurements.

    Args:
        total_vram_mb: Total VRAM available in megabytes
        available_vram_mb: Currently available VRAM in megabytes
        total_ram_mb: Total RAM available in megabytes
        available_ram_mb: Currently available RAM in megabytes
        model_vram: Per-model actual VRAM consumption in MB (model_id → vram_mb).
            Populated from resource tracker measurements; used by Stargate eviction
            planner to replace stale load-time snapshots with current real values.

    Returns:
        Event with SystemResourcesUpdated signal
    """
    payload: dict[str, Any] = {
        "total_vram_mb": total_vram_mb,
        "available_vram_mb": available_vram_mb,
        "total_ram_mb": total_ram_mb,
        "available_ram_mb": available_ram_mb,
    }
    if model_vram is not None:
        payload["model_vram"] = model_vram
    return Event(
        signal=SYSTEM_RESOURCES_UPDATED,
        payload=payload,
    )


@event_factory
def InferenceResourceUpdate(
    model_id: str,
    request_id: str,
    timestamp: float,
    worker_config: dict[str, Any],
    vram_used_mb: int | None = None,
    ram_used_mb: int | None = None,
    vram_max_mb: int | None = None,
    ram_max_mb: int | None = None,
    gpu_utilization: float | None = None,
    inference_duration: float | None = None,
    peak_ram_gb: float | None = None,
    peak_vram_gb: float | None = None,
) -> Event:
    """
    Create INFERENCE_RESOURCE_UPDATE event.

    Args:
        model_id: Model identifier
        request_id: Request identifier
        timestamp: Timestamp of the reading
        worker_config: Worker configuration for this reading
        vram_used_mb: Current VRAM usage in megabytes (optional)
        ram_used_mb: Current RAM usage in megabytes (optional)
        vram_max_mb: Maximum VRAM usage observed (optional)
        ram_max_mb: Maximum RAM usage observed (optional)
        gpu_utilization: GPU utilization percentage (optional)
        inference_duration: Duration of inference so far (optional)
        peak_ram_gb: Peak RAM in GB (legacy, optional)
        peak_vram_gb: Peak VRAM in GB (legacy, optional)

    Returns:
        Event with InferenceResourceUpdate signal
    """
    payload = {
        "model_id": model_id,
        "request_id": request_id,
        "timestamp": timestamp,
        "worker_config": worker_config,
        "vram_used_mb": vram_used_mb,
        "ram_used_mb": ram_used_mb,
        "vram_max_mb": vram_max_mb,
        "ram_max_mb": ram_max_mb,
        "gpu_utilization": gpu_utilization,
        "inference_duration": inference_duration,
        "peak_ram_gb": peak_ram_gb,
        "peak_vram_gb": peak_vram_gb,
    }
    return Event(
        signal=INFERENCE_RESOURCE_UPDATE,
        payload={k: v for k, v in payload.items() if v is not None},
    )


# Worker Crash Detection Event Factories
@event_factory
def WorkerCrashDetected(
    model_id: str,
    error_message: str,
    socket_path: str,
    process_pid: int | None = None,
) -> Event:
    """
    Create WORKER_CRASH_DETECTED event.

    Args:
        model_id: Model ID of crashed worker
        error_message: Error message describing the crash
        socket_path: Path to the orphaned socket file
        process_pid: Optional PID of crashed process

    Returns:
        Event with WorkerCrashDetected signal
    """
    return Event(
        signal=WORKER_CRASH_DETECTED,
        payload={
            "model_id": model_id,
            "error_message": error_message,
            "socket_path": socket_path,
            "process_pid": process_pid,
        },
    )


@event_factory
def SocketOrphaned(
    model_id: str,
    socket_path: str,
    cleanup_successful: bool,
    error: str | None = None,
) -> Event:
    """
    Create SOCKET_ORPHANED event.

    Args:
        model_id: Model ID associated with the orphaned socket
        socket_path: Path to the orphaned socket file
        cleanup_successful: Whether cleanup was successful
        error: Optional error message if cleanup failed

    Returns:
        Event with SocketOrphaned signal
    """
    return Event(
        signal=SOCKET_ORPHANED,
        payload={
            "model_id": model_id,
            "socket_path": socket_path,
            "cleanup_successful": cleanup_successful,
            "error": error,
        },
    )


@event_factory
def HealthCheckFailed(
    model_id: str,
    error_message: str,
    socket_path: str,
) -> Event:
    """
    Create HEALTH_CHECK_FAILED event.

    Args:
        model_id: Model ID of the worker
        error_message: Error message describing the failure
        socket_path: Path to the socket file

    Returns:
        Event with HealthCheckFailed signal
    """
    return Event(
        signal=HEALTH_CHECK_FAILED,
        payload={
            "model_id": model_id,
            "error_message": error_message,
            "socket_path": socket_path,
        },
    )


# Gateway Lifecycle Event Factories
@event_factory
def GatewayShutdown(
    gateway_id: str,
    reason: str,
    timestamp: float,
) -> Event:
    """
    Create GATEWAY_SHUTDOWN event.

    Subscribers should NOT expect in-flight requests to complete.

    Args:
        gateway_id: Identifier of gateway shutting down
        reason: Reason for shutdown (e.g., "signal", "requested")
        timestamp: Unix timestamp of shutdown initiation

    Returns:
        Event with GatewayShutdown signal
    """
    return Event(
        signal=GATEWAY_SHUTDOWN,
        payload={
            "gateway_id": gateway_id,
            "reason": reason,
            "timestamp": timestamp,
        },
    )


@event_factory
def GatewayDraining(
    gateway_id: str,
    reason: str,
    timeout: float,
    timestamp: float,
) -> Event:
    """
    Create GATEWAY_DRAINING event.

    Subscribers MAY expect in-flight requests to complete within timeout.

    Args:
        gateway_id: Identifier of gateway draining
        reason: Reason for shutdown
        timeout: Seconds until forced shutdown
        timestamp: Unix timestamp of drain initiation

    Returns:
        Event with GatewayDraining signal
    """
    return Event(
        signal=GATEWAY_DRAINING,
        payload={
            "gateway_id": gateway_id,
            "reason": reason,
            "timeout": timeout,
            "timestamp": timestamp,
        },
    )


@event_factory
def VramOrphanDetected(
    hardware_used_mb: int,
    catalog_used_mb: int,
    discrepancy_mb: int,
    tracked_models: list[str],
) -> Event:
    """Emitted when hardware VRAM exceeds catalog — unmanaged GPU processes suspected."""
    return Event(
        signal=VRAM_ORPHAN_DETECTED,
        payload={
            "hardware_used_mb": hardware_used_mb,
            "catalog_used_mb": catalog_used_mb,
            "discrepancy_mb": discrepancy_mb,
            "tracked_models": tracked_models,
        },
    )


@event_factory
def VramStalenessDetected(
    hardware_used_mb: int,
    catalog_used_mb: int,
    discrepancy_mb: int,
    tracked_models: list[str],
) -> Event:
    """Emitted when catalog VRAM exceeds hardware — catalog profiles stale."""
    return Event(
        signal=VRAM_STALENESS_DETECTED,
        payload={
            "hardware_used_mb": hardware_used_mb,
            "catalog_used_mb": catalog_used_mb,
            "discrepancy_mb": discrepancy_mb,
            "tracked_models": tracked_models,
        },
    )


@event_factory
def PhantomModelDetected(
    model_id: str,
    process_status: str,
    tracker_status: str | None = None,
) -> Event:
    """Create PHANTOM_MODEL_DETECTED event."""
    return Event(
        signal=PHANTOM_MODEL_DETECTED,
        payload={
            "model_id": model_id,
            "process_status": process_status,
            "tracker_status": tracker_status,
        },
    )


@event_factory
def PhantomModelCleaned(
    model_id: str,
    success: bool,
    vram_freed_mb: int | None = None,
) -> Event:
    """Create PHANTOM_MODEL_CLEANED event."""
    return Event(
        signal=PHANTOM_MODEL_CLEANED,
        payload={
            "model_id": model_id,
            "success": success,
            "vram_freed_mb": vram_freed_mb,
        },
    )


@event_factory
def GhostModelCleaned(
    model_id: str,
    success: bool,
    vram_freed_mb: int | None = None,
) -> Event:
    """Create GHOST_MODEL_CLEANED event (tracked model whose engine was dead)."""
    return Event(
        signal=GHOST_MODEL_CLEANED,
        payload={
            "model_id": model_id,
            "success": success,
            "vram_freed_mb": vram_freed_mb,
        },
    )


# Compute Capacity Telemetry Event Factories
@event_factory
def ComputeCapacityQueueWait(
    request_id: str,
    model_id: str,
    compute_type: str,
    queue_position: int,
    active_count: int,
    limit: int,
    timestamp_ms: int,
) -> Event:
    """
    Create COMPUTE_CAPACITY_QUEUE_WAIT event.

    Emitted when request enters queue due to capacity limit.

    Args:
        request_id: Request being queued
        model_id: Model the request is for
        compute_type: "cpu", "hybrid", or "gpu"
        queue_position: Queue position at enqueue time (1 = first in line)
        active_count: Current active requests
        limit: Capacity limit
        timestamp_ms: Milliseconds since epoch

    Returns:
        Event with ComputeCapacityQueueWait signal
    """
    return Event(
        signal=COMPUTE_CAPACITY_QUEUE_WAIT,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "compute_type": compute_type,
            "queue_position": queue_position,
            "active_count": active_count,
            "limit": limit,
            "timestamp_ms": timestamp_ms,
        },
    )


@event_factory
def ComputeCapacityQueueAcquired(
    request_id: str,
    model_id: str,
    compute_type: str,
    wait_duration_ms: float,
    queue_position_at_enqueue: int,
    timestamp_ms: int,
) -> Event:
    """
    Create COMPUTE_CAPACITY_QUEUE_ACQUIRED event.

    Emitted when request acquires slot after waiting.

    Args:
        request_id: Request that acquired slot
        model_id: Model the request is for
        compute_type: "cpu", "hybrid", or "gpu"
        wait_duration_ms: Time spent waiting in queue
        queue_position_at_enqueue: Position when enqueued (for correlation)
        timestamp_ms: Milliseconds since epoch

    Returns:
        Event with ComputeCapacityQueueAcquired signal
    """
    return Event(
        signal=COMPUTE_CAPACITY_QUEUE_ACQUIRED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "compute_type": compute_type,
            "wait_duration_ms": wait_duration_ms,
            "queue_position_at_enqueue": queue_position_at_enqueue,
            "timestamp_ms": timestamp_ms,
        },
    )


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
