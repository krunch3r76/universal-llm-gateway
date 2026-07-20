"""Signal string constants for `model_lifecycle` scheduling events (model availability, load/unload, execution lifecycle, capacity, and worker eviction signals). Re-exported via the `model_lifecycle` package facade for use by `factories.py` and event subscribers."""

# ruff: noqa: N802

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

MODEL_LOADING_PROGRESS = "model.loading.progress"
"""
Load progress heartbeat while model is actively loading.

Node loader MUST emit at most every 15 seconds while loading.
Required fields: phase (non-empty str), pct (0–100).

Payload: {
    "url": str,
    "model_id": str,
    "phase": str,
    "pct": int | float,
    "gateway_name": str | None,
}
"""

MODEL_LOAD_FAILED = "model.load.failed"
"""
Model loading failed on gateway
Payload: {
    "url": str,
    "model_id": str,
    "error": str,
    "gateway_name": str | None,
    "gateway_state_snapshot": dict | None,
        # Master-side cached view of the gateway at failure time:
        # loaded_models, busy_models, loading_models, model_details (VRAM/RAM
        # per model), and aggregate resource availability. Built from
        # GatewayState — the Stargate WebSocket client's cached projection.
    "worker_snapshot": dict | None,
        # Edge-side dump captured by the gateway at failure time:
        # failed_worker (pid, status, child llama-cpp/vLLM processes with
        # rss_mb), peer_workers, and live hardware VRAM/RAM totals.
        # Forwarded over the WebSocket MODEL_LOAD_FAILED message.
}

Both snapshots are best-effort forensics for batch-pipeline / oncall debugging
and may be absent when capture fails. Subscribers MUST tolerate either being
None — coordination correctness does not depend on them.
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
