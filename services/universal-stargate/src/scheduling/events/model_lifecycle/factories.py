"""Factory functions for `model_lifecycle` scheduling events. Builds `Event` objects via `event_factory` from this package's signal constants (model availability, load/unload, execution start/complete/fail, capacity freed, worker eviction) for callers importing from `src.scheduling.events.model_lifecycle`."""

# ruff: noqa: N802

from universal_event_bus import Event, event_factory

from .signal_constants import (
    MODEL_AVAILABLE,
    MODEL_CAPACITY_FREED,
    MODEL_EXECUTION_COMPLETED,
    MODEL_EXECUTION_FAILED,
    MODEL_EXECUTION_STARTED,
    MODEL_LOAD_FAILED,
    MODEL_LOADED,
    MODEL_LOADING_PROGRESS,
    MODEL_LOADING_STARTED,
    MODEL_LOADING_STUCK,
    MODEL_UNAVAILABLE,
    MODEL_UNLOADED,
    WORKER_EVICTED,
)


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

    Coordination signal: batch pipelines (e.g. RAG contextualization) subscribe
    to anticipate the cold-load window and pause new submissions before
    Stargate's queue saturates. Stargate retains sole authority over the
    actual load decision — subscribers MUST NOT gate correctness on this
    signal (per the stargate-model-lifecycle invariant).

    Args:
        url: Gateway URL
        model_id: Model starting to load

    Returns:
        Event with ModelLoadingStarted signal
    """
    return Event(
        signal=MODEL_LOADING_STARTED,
        payload={"url": url, "model_id": model_id},
        role="coordination",
    )


@event_factory
def ModelLoadingProgress(
    url: str,
    model_id: str,
    phase: str,
    pct: int | float,
    gateway_name: str | None = None,
) -> Event:
    """Create MODEL_LOADING_PROGRESS heartbeat event.

    Args:
        url: Gateway URL
        model_id: Model being loaded
        phase: Stable load-phase label (non-empty)
        pct: Completion percentage in [0, 100]
        gateway_name: Optional gateway name for correlation

    Returns:
        Event with MODEL_LOADING_PROGRESS signal.
    """
    if not phase or not str(phase).strip():
        raise ValueError("phase must be a non-empty string")
    pct_value = float(pct)
    if not 0.0 <= pct_value <= 100.0:
        raise ValueError(f"pct must be in [0, 100], got {pct}")

    payload = {
        "url": url,
        "model_id": model_id,
        "phase": str(phase),
        "pct": pct_value,
        "gateway_name": gateway_name,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    return Event(
        signal=MODEL_LOADING_PROGRESS,
        payload=payload,
        role="coordination",
    )


@event_factory
def ModelLoadingFailed(
    url: str,
    model_id: str,
    error: str,
    gateway_name: str | None = None,
    gateway_state_snapshot: dict | None = None,
    worker_snapshot: dict | None = None,
) -> Event:
    """
    Create MODEL_LOAD_FAILED event.

    Args:
        url: Gateway URL
        model_id: Model that failed to load
        error: Error message
        gateway_name: Optional gateway name (for enriched events)
        gateway_state_snapshot: Optional master-side cached view of the
            gateway at failure time (loaded/busy/loading models, per-model
            VRAM/RAM, aggregate resource availability). Built from
            GatewayState. Forensics-only, may be None.
        worker_snapshot: Optional edge-side worker/process/resource dump
            forwarded over the WebSocket MODEL_LOAD_FAILED message.
            Forensics-only, may be None.

    Returns:
        Event with MODEL_LOAD_FAILED signal.
    """
    payload = {
        "url": url,
        "model_id": model_id,
        "error": error,
        "gateway_name": gateway_name,
        "gateway_state_snapshot": gateway_state_snapshot,
        "worker_snapshot": worker_snapshot,
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
