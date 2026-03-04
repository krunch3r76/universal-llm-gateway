"""Model lifecycle event signals.

Covers model loaded/unloaded state transitions, loading start/failure,
per-request execution lifecycle (started/completed/failed), and the
wake-only capacity-freed signal.

All MODEL_EXECUTION_* events are per-request lifecycle events, not state signals.
Consumers aggregate them to derive busy/idle state (set-based for llama.cpp,
counter-based for vLLM).

Signals:
    model.loaded — model available on gateway
    model.unloaded — model removed from gateway
    model.loading.started — model load in progress
    model.loading.failed — model load failed
    model.execution.started — one request started execution
    model.execution.completed — one request completed (triggers slot release)
    model.execution.failed — one request failed (triggers slot release)
    model.capacity.freed — wake-only; capacity likely increased
"""

from universal_event_bus import Event, event_factory

# ========================================
# Model Lifecycle Event Signals
# ========================================

MODEL_LOADED = "model.loaded"
"""
Model loaded on gateway
Payload: {
    "url": str,
    "model_id": str
}
"""

MODEL_UNLOADED = "model.unloaded"
"""
Model unloaded from gateway
Payload: {
    "url": str,
    "model_id": str
}
"""

MODEL_LOADING_STARTED = "model.loading.started"
"""
Model loading started on gateway
Payload: {
    "url": str,
    "model_id": str
}
"""

MODEL_LOADING_FAILED = "model.loading.failed"
"""
Model loading failed on gateway
Payload: {
    "url": str,
    "model_id": str,
    "error": str
}
"""

MODEL_EXECUTION_STARTED = "model.execution.started"
"""
Model execution request started (per-request lifecycle event).

This is a **lifecycle event**, not a state signal. Each event represents
one execution request starting. Consumer aggregates these to derive state.

Current (llama.cpp): Set-based tracking (1 request at a time)
Future (vLLM): Counter-based tracking (N concurrent requests)

Workload-agnostic: Applies to LLM inference, ASR, image generation, etc.

Payload: {
    "url": str,       # Gateway URL
    "model_id": str   # Model that started execution
}
"""

MODEL_EXECUTION_COMPLETED = "model.execution.completed"
"""
Model execution request completed (per-request lifecycle event).

**Scheduling signal**: Wakes queue processors to check if model has capacity.
**Slot release**: GatewayTracker subscribes to auto-release reserved slots.

INVARIANT: request_id and gateway_id always present

Payload: {
    "url": str,         # Gateway URL
    "model_id": str,    # Model that completed execution
    "request_id": str,  # Request identifier (for slot tracking)
    "gateway_id": str,  # Gateway identifier (for slot tracking)
}
"""

MODEL_EXECUTION_FAILED = "model.execution.failed"
"""
Model execution request failed (per-request lifecycle event).

**Slot release**: GatewayTracker subscribes to auto-release reserved slots.

Payload: {
    "url": str,         # Gateway URL
    "model_id": str,    # Model that failed execution
    "request_id": str,  # Request identifier (for slot tracking)
    "gateway_id": str,  # Gateway identifier (for slot tracking)
    "error": str,       # Error message
}
"""

MODEL_CAPACITY_FREED = "model.capacity.freed"
"""
Wake-only signal: capacity likely increased on gateway/model.

NOT a slot-release signal. Emitted when:
- Gateway reports MODEL_IDLE (execution finished, model now idle)
- Gateway reports MODEL_UNLOADED (model removed, resources freed)

Consumers should re-check capacity but NOT release any tracked slots.

Payload: {
    "url": str,       # Gateway URL
    "model_id": str,  # Model with freed capacity
}
"""


# ========================================
# Factory Functions
# ========================================


@event_factory
def ModelLoaded(
    url: str,
    model_id: str,
    gateway_name: str | None = None,
    vram_mb: int | None = None,
    ram_mb: int | None = None,
) -> Event:
    """
    Create MODEL_LOADED event.

    Args:
        url: Gateway URL
        model_id: Model that was loaded
        gateway_name: Optional gateway name (for enriched events)
        vram_mb: Optional VRAM usage in MB
        ram_mb: Optional RAM usage in MB

    Returns:
        Event with ModelLoaded signal
    """
    payload: dict[str, object] = {"url": url, "model_id": model_id}
    if gateway_name is not None:
        payload["gateway_name"] = gateway_name
    if vram_mb is not None:
        payload["vram_mb"] = vram_mb
    if ram_mb is not None:
        payload["ram_mb"] = ram_mb
    return Event(signal=MODEL_LOADED, payload=payload)


@event_factory
def ModelUnloaded(
    url: str,
    model_id: str,
    gateway_name: str | None = None,
) -> Event:
    """
    Create MODEL_UNLOADED event.

    Args:
        url: Gateway URL
        model_id: Model that was unloaded
        gateway_name: Optional gateway name (for enriched events)

    Returns:
        Event with ModelUnloaded signal
    """
    payload: dict[str, object] = {"url": url, "model_id": model_id}
    if gateway_name is not None:
        payload["gateway_name"] = gateway_name
    return Event(signal=MODEL_UNLOADED, payload=payload)


@event_factory
def ModelLoadingStarted(url: str, model_id: str) -> Event:
    """
    Create MODEL_LOADING_STARTED event.

    Args:
        url: Gateway URL
        model_id: Model starting to load

    Returns:
        Event with ModelLoadingStarted signal
    """
    return Event(
        signal=MODEL_LOADING_STARTED, payload={"url": url, "model_id": model_id}
    )


@event_factory
def ModelLoadingFailed(
    url: str,
    model_id: str,
    error: str,
    gateway_name: str | None = None,
) -> Event:
    """
    Create MODEL_LOADING_FAILED event.

    Args:
        url: Gateway URL
        model_id: Model that failed to load
        error: Error message
        gateway_name: Optional gateway name (for enriched events)

    Returns:
        Event with ModelLoadingFailed signal
    """
    payload: dict[str, object] = {"url": url, "model_id": model_id, "error": error}
    if gateway_name is not None:
        payload["gateway_name"] = gateway_name
    return Event(
        signal=MODEL_LOADING_FAILED,
        payload=payload,
    )


@event_factory
def ModelExecutionStarted(url: str, model_id: str) -> Event:
    """
    Create model.execution.started event.

    Lifecycle event: one execution request started on this model.
    Consumer aggregates to derive busy state.

    Workload-agnostic: Applies to LLM inference, ASR, image generation, etc.

    Args:
        url: Gateway URL
        model_id: Model that started execution

    Returns:
        Event with ModelExecutionStarted signal
    """
    return Event(
        signal=MODEL_EXECUTION_STARTED, payload={"url": url, "model_id": model_id}
    )


@event_factory
def ModelExecutionCompleted(
    url: str,
    model_id: str,
    request_id: str,
    gateway_id: str,
) -> Event:
    """
    Create model.execution.completed event (request-scoped slot release).

    INVARIANT: request_id and gateway_id are REQUIRED for slot tracking.

    Triggers:
    1. GatewayTracker auto-releases slot (via subscription)
    2. Queue processors wake to check capacity

    Args:
        url: Gateway URL
        model_id: Model that completed execution
        request_id: Request identifier (REQUIRED for slot tracking)
        gateway_id: Gateway identifier (REQUIRED for slot tracking)

    Returns:
        Event with ModelExecutionCompleted signal
    """
    return Event(
        signal=MODEL_EXECUTION_COMPLETED,
        payload={
            "url": url,
            "model_id": model_id,
            "request_id": request_id,
            "gateway_id": gateway_id,
        },
    )


@event_factory
def ModelExecutionFailed(
    url: str,
    model_id: str,
    request_id: str,
    gateway_id: str,
    error: str,
) -> Event:
    """
    Create model.execution.failed event (request-scoped slot release).

    Args:
        url: Gateway URL
        model_id: Model that failed execution
        request_id: Request identifier (REQUIRED for slot tracking)
        gateway_id: Gateway identifier (REQUIRED for slot tracking)
        error: Error message

    Returns:
        Event with ModelExecutionFailed signal
    """
    return Event(
        signal=MODEL_EXECUTION_FAILED,
        payload={
            "url": url,
            "model_id": model_id,
            "request_id": request_id,
            "gateway_id": gateway_id,
            "error": error,
        },
    )


@event_factory
def ModelCapacityFreed(url: str, model_id: str) -> Event:
    """
    Create model.capacity.freed event (wake-only, no slot release).

    Args:
        url: Gateway URL
        model_id: Model with freed capacity

    Returns:
        Event with ModelCapacityFreed signal
    """
    return Event(
        signal=MODEL_CAPACITY_FREED,
        payload={"url": url, "model_id": model_id},
    )
