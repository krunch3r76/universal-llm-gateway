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
    model.load.failed — model load failed
    model.loading.stuck — model load exceeded stuck TTL
    model.execution.started — one request started execution
    model.execution.completed — one request completed (triggers slot release)
    model.execution.failed — one request failed (triggers slot release)
    model.capacity.freed — wake-only; capacity likely increased
    worker.evicted — model evicted from gateway to free VRAM
"""

# ruff: noqa: N802

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

MODEL_LOAD_FAILED = "model.load.failed"
"""
Model loading failed on gateway
Payload: {
    "url": str,
    "model_id": str,
    "error": str
}
"""

MODEL_LOADING_STUCK = "model.loading.stuck"
"""
A model was stuck in loading state beyond the TTL threshold.
The loading reservation has been auto-cleared to unblock VRAM.

Payload: {
    "url": str,
    "model_id": str,
    "elapsed_s": float,
    "ttl_s": float
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

WORKER_EVICTED = "worker.evicted"
"""
Emitted when Stargate evicts a model from a gateway to free VRAM for another model.

Coordination signal: downstream services (RAG, pipelines) use this to avoid
stampeding cold workers with concurrent requests after eviction.

Payload: {
    "model_id": str,           # Model that was evicted
    "trigger_model_id": str,   # Model that needs the freed VRAM
    "vram_freed_mb": int,      # Estimated VRAM freed by this eviction
    "gateway_name": str        # Gateway where eviction occurred
}
"""

MODEL_AVAILABLE = "model.available"
"""
Aggregate routing: at least one Stargate-visible path can serve model_id.

Payload: {
    "model_id": str,
}
"""

MODEL_UNAVAILABLE = "model.unavailable"
"""
Aggregate routing: no remaining path can serve model_id.

Payload: {
    "model_id": str,
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
        Event with MODEL_LOADED signal.
    """
    payload = {
        "url": url,
        "model_id": model_id,
        "gateway_name": gateway_name,
        "vram_mb": vram_mb,
        "ram_mb": ram_mb,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    return Event(signal=MODEL_LOADED, payload=payload, role="coordination")


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
        Event with MODEL_UNLOADED signal.
    """
    payload = {
        "url": url,
        "model_id": model_id,
        "gateway_name": gateway_name,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    return Event(signal=MODEL_UNLOADED, payload=payload, role="coordination")


@event_factory
def ModelAvailable(model_id: str) -> Event:
    """Publish aggregate routing availability for a model ID at Stargate scope.

    Indicates that the union of local and federated catalogs now contains at
    least one path that can serve inference for this model_id. This is not
    equivalent to model.loaded on a specific gateway.

    Args:
        model_id: OpenAI-style model identifier as routed by Stargate.

    Returns:
        Coordination event with signal model.available and payload model_id.
    """
    return Event(
        signal=MODEL_AVAILABLE,
        payload={"model_id": model_id},
        role="coordination",
        scope="global",
    )


@event_factory
def ModelUnavailable(model_id: str) -> Event:
    """Publish aggregate routing loss for a model ID at Stargate scope.

    Emitted when the last Stargate-visible path that could serve this model_id
    disappears (local disconnect, federation loss, or catalog shrink).

    Args:
        model_id: OpenAI-style model identifier as routed by Stargate.

    Returns:
        Coordination event with signal model.unavailable and payload model_id.
    """
    return Event(
        signal=MODEL_UNAVAILABLE,
        payload={"model_id": model_id},
        role="coordination",
        scope="global",
    )


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
    Create MODEL_LOAD_FAILED event.

    Args:
        url: Gateway URL
        model_id: Model that failed to load
        error: Error message
        gateway_name: Optional gateway name (for enriched events)

    Returns:
        Event with MODEL_LOAD_FAILED signal.
    """
    payload = {
        "url": url,
        "model_id": model_id,
        "error": error,
        "gateway_name": gateway_name,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    return Event(
        signal=MODEL_LOAD_FAILED,
        payload=payload,
        role="coordination",
    )


@event_factory
def ModelLoadingStuck(
    url: str,
    model_id: str,
    elapsed_s: float,
    ttl_s: float,
) -> Event:
    """Signal that model load exceeded stuck TTL; reservation cleared.

    Args:
        url: Gateway URL.
        model_id: Model that was stuck in loading.
        elapsed_s: Time in seconds the model has been stuck.
        ttl_s: Threshold time in seconds after which the model is considered stuck.

    Returns:
        Event with MODEL_LOADING_STUCK signal.
    """
    return Event(
        signal=MODEL_LOADING_STUCK,
        payload={
            "url": url,
            "model_id": model_id,
            "elapsed_s": elapsed_s,
            "ttl_s": ttl_s,
        },
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
        role="coordination",
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
        role="coordination",
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
        role="coordination",
    )


@event_factory
def WorkerEvicted(
    model_id: str,
    trigger_model_id: str,
    vram_freed_mb: int,
    gateway_name: str,
) -> Event:
    """Create worker.evicted event.

    Coordination signal emitted per model when Stargate evicts it from a gateway
    to free VRAM for trigger_model_id. Downstream services should anticipate a
    cold-load window before the evicted model can serve again.

    Args:
        model_id: Model that was evicted.
        trigger_model_id: Model that needs the freed VRAM.
        vram_freed_mb: Estimated VRAM freed by this eviction.
        gateway_name: Gateway where eviction occurred.
    """
    return Event(
        signal=WORKER_EVICTED,
        payload={
            "model_id": model_id,
            "trigger_model_id": trigger_model_id,
            "vram_freed_mb": vram_freed_mb,
            "gateway_name": gateway_name,
        },
        role="coordination",
        scope="global",
    )
