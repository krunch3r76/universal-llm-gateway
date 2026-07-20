"""Model load/unload event signals and factories for the gateway.

Defines MODEL_* signal constants and @event_factory helpers consumed by
load_flow, loader, unloader, and resource-tracker crash paths. Signal string
values are the event-bus contract — do not rename without event-contracts update.
"""

# ruff: noqa: N802 - Factory function names match event signal names

from typing import Any

from universal_event_bus import Event, event_factory

# ========== Model Lifecycle Event Signals ==========

MODEL_LOADING_STARTED = "model.loading.started"
"""
Emitted when a model loading operation begins.

Payload:
    model_id: str - Unique identifier for the model being loaded
"""

MODEL_LOADING_PROGRESS = "model.loading.progress"
"""
Emitted during active model load as a heartbeat (phase + pct).

Payload:
    model_id: str - Unique identifier for the model being loaded
    phase: str - Stable load-phase label (non-empty)
    pct: float - Completion percentage in [0, 100]
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
    worker_snapshot: dict | None - Optional best-effort dump of supervised
        worker processes, llama-cpp/vLLM child processes, and live hardware
        VRAM/RAM at failure time. Forwarded to Stargate via the WebSocket
        MODEL_LOAD_FAILED message and surfaced on the master-side
        model.load.failed event for forensics.

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
def ModelLoadingProgress(model_id: str, phase: str, pct: int | float) -> Event:
    """Create MODEL_LOADING_PROGRESS heartbeat event."""
    if not phase or not str(phase).strip():
        raise ValueError("phase must be a non-empty string")
    pct_value = float(pct)
    if not 0.0 <= pct_value <= 100.0:
        raise ValueError(f"pct must be in [0, 100], got {pct}")
    return Event(
        signal=MODEL_LOADING_PROGRESS,
        payload={"model_id": model_id, "phase": str(phase), "pct": pct_value},
    )


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
    model_id: str,
    error_message: str,
    failure_reason: str = "unknown",
    worker_snapshot: dict[str, Any] | None = None,
) -> Event:
    """
    Create MODEL_LOAD_FAILED event.

    Args:
        model_id: Model that failed to load
        error_message: Description of the failure
        failure_reason: Categorized reason code (default: "unknown")
        worker_snapshot: Optional best-effort dump of supervised worker
            processes, llama-cpp/vLLM child processes, and live hardware
            VRAM/RAM at failure time. Forwarded to Stargate via the
            WebSocket MODEL_LOAD_FAILED message.

    Returns:
        Event with ModelLoadFailed signal
    """
    payload: dict[str, Any] = {
        "model_id": model_id,
        "error_message": error_message,
        "failure_reason": failure_reason,
    }
    if worker_snapshot is not None:
        payload["worker_snapshot"] = worker_snapshot
    return Event(
        signal=MODEL_LOAD_FAILED,
        payload=payload,
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
