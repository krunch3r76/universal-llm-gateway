"""
Event signals for Universal Stargate event-driven architecture.

All events now use the UML Message structure from universal_event_bus.
Events are created via factory functions and published as Event objects.

The EventBus automatically injects:
- timestamp: ISO 8601 string with milliseconds and Z suffix
- id: Global counter for event ordering

Usage:
    from src.scheduling.events import GatewayStateChanged
    event = GatewayStateChanged(
        url="http://localhost:9998",
        connectivity="reachable",
        health="healthy",
        previous_connectivity=None,
        previous_health=None,
        transition_type="initial",
    )
    await event_bus.publish_async_nowait(event)
"""

from typing import Any

from model_id import ModelId
from universal_event_bus import Event, event_factory

# ========================================
# Routing Metrics Event Signals (UDP-emitted metrics)
# ========================================

REQUEST_ROUTED = "request.routed"
"""
Request successfully routed to gateway
Emitted when a request is routed to a specific gateway for processing.
This metric is useful for tracking routing decisions and load distribution.

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_url": str,
    "gateway_name": str,
    "timestamp": float,
    "routing_time_ms": float,  # Time taken to route request
    "queue_position": Optional[int],  # Position in queue if queued
    "immediate_route": bool  # True if routed immediately, False if queued
}
"""

MODEL_LOAD_INITIATED = "model.load.initiated"
"""
Model loading initiated on gateway
Emitted when a model load operation is triggered.

Payload: {
    "model_id": str,
    "gateway_url": str,
    "gateway_name": str,
    "timestamp": float,
    "already_loaded": bool  # True if model was already loaded
}
"""

MODEL_LOAD_COMPLETED = "model.load.completed"
"""
Model loading completed on gateway
Emitted when a model finishes loading (successfully or failed).

Payload: {
    "model_id": str,
    "gateway_url": str,
    "gateway_name": str,
    "timestamp": float,
    "success": bool,
    "load_time_ms": float,  # Time taken to load model
    "error": Optional[str]  # Error message if failed
}
"""

TOKEN_COUNT_COMPLETED = "request.token.counted"
"""
Token counting operation completed
Emitted after token counting finishes for request preparation.

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_url": str,
    "timestamp": float,
    "success": bool,
    "count_time_ms": float,
    "input_tokens": Optional[int],
    "context_limit": Optional[int],
    "allocated_max_tokens": Optional[int],
    "error": Optional[str]
}
"""

TOKEN_COUNTING_FAILED = "token.counting.failed"
"""
Federated token counting failed — gateway unreachable or returned error.

Distinct from request.token.counted: signals an infrastructure-level failure
(e.g. gateway :9998 down behind a relay) rather than a counting result.

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_id": str,
    "error": str
}
"""

# ========================================
# Routing Decision Event Signals
# ========================================

ROUTING_DECISION = "scheduler.routing.decided"
"""
Routing decision made by DecisionEngine.
Emitted for every routing decision with full observability.

Payload: {
    "model_id": str,                    # Model being routed
    "original_model_id": str | None,    # Original request model ID
    "selected_gateway": str | None,     # Selected gateway name
    "selection_reason": str,            # Why this gateway was selected
    "selection_tier": str | None,       # T0/T1/T2 tier name
    "candidate_count": int,             # Total candidates evaluated
    "feasible_count": int,              # Number of feasible candidates
    "evaluation_time_ms": float,        # Time spent in decision engine
    "request_id": str | None,           # Proxy request ID for tracing
    "timestamp": float,                 # Unix timestamp

    # Optional detailed trace (expensive, enable for debugging)
    "candidates": list[dict] | None,    # Full candidate details
}
"""

ROUTING_DECISION_FAILED = "scheduler.routing.failed"
"""
Routing decision failed - no gateway available.
Emitted when no feasible gateway can be found.

Payload: {
    "model_id": str,
    "original_model_id": str | None,
    "candidate_count": int,
    "evaluation_time_ms": float,
    "request_id": str | None,
    "timestamp": float,
    "reason": str
}
"""

# ========================================
# Gateway Event Signals
# ========================================

GATEWAY_STATE_CHANGED = "gateway.state.changed"
"""
Unified gateway state changed event (Phase 2)
Consolidates connectivity and health changes into single comprehensive event.

Payload: {
    "url": str,
    "connectivity": str,  # "reachable" | "unreachable"
    "health": str,  # "healthy" | "unhealthy" | "unknown"
    "previous_connectivity": Optional[str],
    "previous_health": Optional[str],
    "transition_type": str,  # "connectivity_only" | "health_only" | "both" | "initial"
    "check_duration_ms": int
}
"""

GATEWAY_RETRY_ATTEMPTED = "gateway.retry.attempted"
"""
Gateway request retry attempted (structured telemetry)
Emitted when a gateway request fails and retry is attempted.

Payload: {
    "gateway_url": str,
    "method": str,  # HTTP method
    "path": str,
    "attempt": int,  # Current attempt number (1-indexed)
    "max_retries": int,
    "error_type": str,  # Exception class name
    "error_message": str,
    "backoff_delay_ms": int  # Milliseconds until next retry
}
"""

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

GATEWAY_RESOURCE_UPDATE = "gateway.resource.updated"
"""
Gateway resource information updated
Payload: {
    "url": str,
    "total_vram_mb": int,
    "available_vram_mb": int,
    "total_ram_mb": int,
    "available_ram_mb": int,
    "loaded_models": list[str],  # Set converted to list for JSON
    "busy_models": list[str]     # Set converted to list for JSON
}
"""

FEDERATION_GATEWAY_CATALOG_CHANGED = "federation.catalog.changed"
"""
Federated gateway catalog changed
Emitted when a federated gateway's model catalog changes.
Payload: {
    "gateway_id": str,  # Unique identifier (e.g., "edge-localhost-gateway")
    "old_model_count": int,
    "new_model_count": int,
    "event_type": str | None,  # Optional: 'added', 'removed', 'changed'
    "models": list[str] | None,  # Optional: affected model IDs
}
"""

# ─────────────────────────────────────────────────────────────────────────────
# Federation Events (signal naming: past-tense verbs per workspace policy)
# ─────────────────────────────────────────────────────────────────────────────

# Connection lifecycle
FEDERATION_CONNECTION_ESTABLISHED = "federation.connection.established"
FEDERATION_CONNECTION_LOST = "federation.connection.lost"
FEDERATION_CONNECTION_AUTHENTICATED = "federation.connection.authenticated"

# Telemetry flow
FEDERATION_TELEMETRY_RECEIVED = "federation.telemetry.received"
FEDERATION_TELEMETRY_MARKED_STALE = "federation.telemetry.marked.stale"
FEDERATION_TELEMETRY_APPLIED = "federation.telemetry.applied"

# Routing decisions
FEDERATION_ROUTING_DELEGATED = "federation.routing.delegated"
FEDERATION_ROUTING_ROUTED_LOCAL = "federation.routing.routed.local"
FEDERATION_ROUTING_REJECTED = "federation.routing.rejected"

# Model load orchestration
FEDERATION_LOAD_REQUESTED = "federation.load.requested"
FEDERATION_LOAD_CONFIRMED = "federation.load.confirmed"
FEDERATION_LOAD_FAILED = "federation.load.failed"

# Orchestrator decisions
FEDERATION_ORCHESTRATOR_DECIDED = "federation.orchestrator.decided"
FEDERATION_ORCHESTRATOR_EVICTED = "federation.orchestrator.evicted"

# Cloud proxy availability (Stargate-side observation of the proxy)
CLOUD_PROXY_AVAILABLE = "cloud.proxy.available"
CLOUD_PROXY_UNAVAILABLE = "cloud.proxy.unavailable"
CLOUD_PROXY_CATALOG_UPDATED = "cloud.proxy.catalog.updated"
CLOUD_PROXY_CATALOG_FETCH_FAILED = "cloud.proxy.catalog.fetch.failed"

RESOURCE_RESERVED = "resource.reserved"
"""
Resources reserved for model loading
Emitted when resource manager reserves VRAM/RAM for a model load operation.

Payload: {
    "gateway_name": str,
    "model_id": str,
    "reservation_id": str,
    "vram_mb": int,
    "ram_mb": int,
    "timeout_seconds": float
}
"""

RESOURCE_RELEASED = "resource.released"
"""
Resources released from reservation
Emitted when resource reservation is released (completed, expired, or cancelled).

Payload: {
    "gateway_name": str,
    "model_id": str,
    "reservation_id": str,
    "vram_mb": int,
    "ram_mb": int,
    "reason": str  # "completed" | "expired" | "cancelled"
}
"""

# ========================================
# Request Event Signals
# ========================================

REQUEST_QUEUED = "request.queued"
"""
Request added to queue
Payload: {
    "request_id": str,
    "model_id": str,
    "priority": int
}
"""

REQUEST_PROCESSING = "request.processing"
"""
Request started processing
Payload: {
    "request_id": str,
    "gateway_url": str,
    "model_id": str
}
"""

REQUEST_COMPLETED = "request.completed"
"""
Request completed successfully
Payload: {
    "request_id": str,
    "gateway_url": str,
    "model_id": str,
    "duration": float
}
"""

REQUEST_FAILED = "request.failed"
"""
Request failed
Payload: {
    "request_id": str,
    "gateway_url": Optional[str],
    "model_id": str,
    "error": str
}
"""

REQUEST_TIMEOUT = "request.timed.out"
"""
Request timed out
Payload: {
    "request_id": str,
    "gateway_url": Optional[str],
    "model_id": str,
    "timeout_seconds": float
}
"""

REQUEST_CAPACITY_TIMEOUT = "request.capacity.timeout"
"""
Capacity timeout — all retries exhausted waiting for model capacity.
Emitted before request.failed for immediate filtering.
Payload: {
    "request_id": str,
    "model_id": str,
    "timeout_seconds": float,
    "retry_count": int,
    "elapsed_s": float,
    "pipeline_step_id": Optional[str]
}
"""

ROUTING_RESOURCE_DATA_MISSING = "routing.resource.data.missing"
"""
Model is in gateway catalog (available_models) but missing from model_details.

Emitted when routing fails with missing_gateway_resource_data constraint.
Distinguishes startup resource-gap from genuine MODEL_NOT_FOUND.

Diagnostic query:
    jq 'select(.signal == "routing.resource.data.missing")'

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_ids": list[str]  # gateways that have model in catalog but no resource data
}
"""

ROUTING_MODEL_INFEASIBLE = "routing.model.infeasible"
"""
Model exists in gateway catalogs but every candidate gateway is infeasible.

Emitted when routing returns NO_FEASIBLE_GATEWAY (503, retryable).
Carries per-gateway constraint details for diagnosis.

Diagnostic query:
    jq 'select(.signal == "routing.model.infeasible")'

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_constraints": list[dict]  # per-gateway constraint failures
    "excluded_gateway_ids": list[str]  # gateways excluded by retry logic
}
"""

ROUTING_CAPACITY_DIVERGENCE = "routing.capacity.divergence"
"""
Telemetry busy_models disagrees with master-local CapacityPool.

Emitted when telemetry marks a model as busy while CapacityPool reports
available slots on the selected gateway/model.

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_id": str,
    "busy_models_state": str,         # "busy" | "idle"
    "capacity_pool_available": int,
    "capacity_pool_in_flight": int,
    "capacity_pool_max": int,
}
"""

ROUTING_CAPACITY_PRESEEDED = "routing.capacity.preseeded"
"""
CapacityPool pre-seeded for a cold-load model from catalog model_details.

Emitted when a request triggers a cold load and CapacityPool is seeded with
expected capacity BEFORE the model finishes loading.  This closes the
cold-load bypass that previously let unlimited requests flood the gateway.

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_id": str,
    "expected_capacity": int,
}
"""

FEDERATION_SNAPSHOT_SENT = "federation.snapshot.sent"
"""
Edge Stargate sent GATEWAY_SNAPSHOT to Master.

Payload documents all_models vs available_models gap — the difference
between what /v1/models shows and what Master can actually route.

Diagnostic query:
    jq 'select(.signal == "federation.snapshot.sent" and .payload.gap_count > 0)'

Payload: {
    "gateway_id": str,
    "all_models_count": int,     # from ws_client.get_models()
    "available_models_count": int, # models WITH resource data (routable)
    "gap_count": int,            # all_models_count - available_models_count
}
"""

REQUEST_REMOVED = "request.removed"
"""
Request removed from queue (e.g., client disconnect)
Payload: {
    "request_id": str,
    "reason": str,
    "model_id": str,
    "age_seconds": float
}
"""

# ========================================
# Master Queue Event Signals
# ========================================

QUEUE_MASTER_ENTERED = "queue.master.entered"
"""
Request entered master capacity queue.
Emitted when a request starts waiting for system-wide capacity.

Payload: {
    "request_id": str,
    "model_id": str,
    "compute_type": str,
    "endpoint_category": str,
    "queue_position": int,
}
"""

QUEUE_MASTER_WOKEN = "queue.master.woken"
"""
Request woken from master capacity queue.
Emitted when capacity becomes available and a waiter is released.

Payload: {
    "request_id": str,
    "model_id": str,
    "compute_type": str,
    "endpoint_category": str,
    "wait_time_ms": float,
    "gateway_id": str | None,  # Gateway with capacity
}
"""

QUEUE_MASTER_TIMEOUT = "queue.master.timed.out"
"""
Request timed out in master capacity queue.
Emitted when safety net timeout is exceeded.

Payload: {
    "request_id": str,
    "model_id": str,
    "compute_type": str,
    "endpoint_category": str,
    "timeout_seconds": float,
}
"""

QUEUE_MASTER_TOCTOU = "queue.master.toctou"
"""
TOCTOU race detected after master queue wake.
Emitted when request fails capacity check after being woken.

Payload: {
    "request_id": str,
    "model_id": str,
    "compute_type": str,
    "endpoint_category": str,
    "retry_count": int,
}
"""

# ========================================
# Pipeline Registry Events
# ========================================

PIPELINE_REGISTRY_UNAVAILABLE = "pipeline.registry.unavailable"
"""
Pipeline permanently skipped — required models missing after deferred retry.

Emitted once per unavailable pipeline after each registry load or reload.
∀ id: model deps unresolvable against current gateway catalogs + registered pipelines.

Payload: {
    "pipeline_id": str,    # Pipeline that could not be loaded
    "missing_models": list[str],  # Model IDs that were not found
}
"""

# ========================================
# Pipeline Step Events: Embedding
# ========================================

PIPELINE_STEP_EMBEDDING_STARTED = "pipeline.step.embedding.started"
"""
Pipeline embedding step started.
Emitted when a pipeline step begins fetching embeddings.

Payload: {
    "execution_id": str,
    "step_id": str,
    "model_id": str,
    "input_count": int,
}
"""

PIPELINE_STEP_EMBEDDING_COMPLETED = "pipeline.step.embedding.completed"
"""
Pipeline embedding step completed.
Emitted when embeddings are successfully retrieved.

Payload: {
    "execution_id": str,
    "step_id": str,
    "model_id": str,
    "input_count": int,
    "duration_ms": float,
    "embedding_dim": int,
}
"""

PIPELINE_STEP_EMBEDDING_FAILED = "pipeline.step.embedding.failed"
"""
Pipeline embedding step failed.
Emitted when embedding request fails.

Payload: {
    "execution_id": str,
    "step_id": str,
    "model_id": str,
    "input_count": int,
    "duration_ms": float,
    "error": str,
    "status_code": int | None,
}
"""

# ========================================
# Pipeline Step Events: Domain Verification
# ========================================

PIPELINE_STEP_DOMAIN_VERIFICATION_STARTED = "pipeline.step.domain.verification.started"
"""
Pipeline domain verification step started.
Emitted when domain-specific verification begins for a domain.

Payload: {
    "execution_id": str,
    "step_id": str,
    "domain": str,
    "model_id": str,
    "statement_count": int,
}
"""

PIPELINE_STEP_DOMAIN_VERIFICATION_COMPLETED = (
    "pipeline.step.domain.verification.completed"
)
"""
Pipeline domain verification step completed.
Emitted when domain-specific verification completes for a domain.

Payload: {
    "execution_id": str,
    "step_id": str,
    "domain": str,
    "model_id": str,
    "statement_count": int,
    "passed_count": int,
    "failed_count": int,
    "duration_ms": float,
}
"""

# ========================================
# System Event Signals
# ========================================

SYSTEM_STARTED = "system.started"
"""
System session started.

Payload: {
    "pid": int,              # OS process ID — cross-check against lsof/ps
    "role": str,             # "master" | "edge" | "relay"
    "started_at": float,     # Unix epoch (time.time()) at startup
    "version": str | None,   # Package version string, if available
}
"""

SYSTEM_SHUTDOWN = "system.shutdown"
"""
System shutting down
Payload: {} (empty)
"""


# ========================================
# Factory Functions (Type-Safe Event Creation)
# ========================================


# Routing Events
@event_factory
def RequestRouted(
    request_id: str,
    model_id: str,
    gateway_url: str,
    gateway_name: str,
    timestamp: float,
    routing_time_ms: float,
    queue_position: int | None = None,
    immediate_route: bool = True,
) -> Event:
    """
    Create REQUEST_ROUTED event.

    INVARIANT: request_id always present (proxy request ID for tracking)

    Args:
        request_id: Proxy request ID for tracking and tracing
        model_id: Model being routed
        gateway_url: Selected gateway URL
        gateway_name: Selected gateway name
        timestamp: Unix timestamp
        routing_time_ms: Time taken to route request
        queue_position: Position in queue if queued
        immediate_route: True if routed immediately, False if queued

    Returns:
        Event with RequestRouted signal
    """
    return Event(
        signal=REQUEST_ROUTED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_url": gateway_url,
            "gateway_name": gateway_name,
            "timestamp": timestamp,
            "routing_time_ms": routing_time_ms,
            "queue_position": queue_position,
            "immediate_route": immediate_route,
        },
    )


@event_factory
def ModelLoadInitiated(
    model_id: str,
    gateway_url: str,
    gateway_name: str,
    timestamp: float,
    already_loaded: bool = False,
    *,
    request_id: str | None = None,
) -> Event:
    """
    Create MODEL_LOAD_INITIATED event.

    INVARIANT: request_id present when triggered by request

    Args:
        model_id: Model being loaded
        gateway_url: Target gateway URL
        gateway_name: Target gateway name
        timestamp: Unix timestamp
        already_loaded: True if model was already loaded
        request_id: Proxy request ID (when triggered by request)

    Returns:
        Event with ModelLoadInitiated signal
    """
    payload = {
        "model_id": model_id,
        "gateway_url": gateway_url,
        "gateway_name": gateway_name,
        "timestamp": timestamp,
        "already_loaded": already_loaded,
    }
    if request_id:
        payload["request_id"] = request_id
    return Event(signal=MODEL_LOAD_INITIATED, payload=payload)


@event_factory
def ModelLoadCompleted(
    model_id: str,
    gateway_url: str,
    gateway_name: str,
    timestamp: float,
    success: bool,
    load_time_ms: float,
    error: str | None = None,
    *,
    request_id: str | None = None,
) -> Event:
    """
    Create MODEL_LOAD_COMPLETED event.

    INVARIANT: request_id present when triggered by request

    Args:
        model_id: Model that finished loading
        gateway_url: Gateway URL
        gateway_name: Gateway name
        timestamp: Unix timestamp
        success: True if load succeeded
        load_time_ms: Time taken to load model
        error: Error message if failed
        request_id: Proxy request ID (when triggered by request)

    Returns:
        Event with ModelLoadCompleted signal
    """
    payload = {
        "model_id": model_id,
        "gateway_url": gateway_url,
        "gateway_name": gateway_name,
        "timestamp": timestamp,
        "success": success,
        "load_time_ms": load_time_ms,
        "error": error,
    }
    if request_id:
        payload["request_id"] = request_id
    return Event(signal=MODEL_LOAD_COMPLETED, payload=payload)


@event_factory
def TokenCountCompleted(
    request_id: str,
    model_id: str,
    gateway_url: str,
    timestamp: float,
    success: bool,
    count_time_ms: float,
    input_tokens: int | None = None,
    context_limit: int | None = None,
    allocated_max_tokens: int | None = None,
    error: str | None = None,
) -> Event:
    """
    Create TOKEN_COUNT_COMPLETED event.

    INVARIANT: request_id always present (proxy request ID for tracking)

    Args:
        request_id: Proxy request ID for tracking and tracing
        model_id: Model for token counting
        gateway_url: Gateway URL
        timestamp: Unix timestamp
        success: True if count succeeded
        count_time_ms: Time taken for token counting
        input_tokens: Number of input tokens
        context_limit: Model context limit
        allocated_max_tokens: Allocated max_tokens value
        error: Error message if failed

    Returns:
        Event with TokenCountCompleted signal
    """
    return Event(
        signal=TOKEN_COUNT_COMPLETED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_url": gateway_url,
            "timestamp": timestamp,
            "success": success,
            "count_time_ms": count_time_ms,
            "input_tokens": input_tokens,
            "context_limit": context_limit,
            "allocated_max_tokens": allocated_max_tokens,
            "error": error,
        },
    )


@event_factory
def TokenCountingFailed(
    request_id: str,
    model_id: str,
    gateway_id: str,
    error: str,
) -> Event:
    """
    Create TOKEN_COUNTING_FAILED event.

    Emitted when federated token counting fails due to an infrastructure
    issue (gateway unreachable, edge container down, etc.).

    Args:
        request_id: Proxy request ID
        model_id: Model being requested
        gateway_id: Gateway that failed token counting
        error: Error description

    Returns:
        Event with TokenCountingFailed signal
    """
    return Event(
        signal=TOKEN_COUNTING_FAILED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "error": error,
        },
    )


@event_factory
def RoutingDecision(
    model_id: str,
    selection_reason: str,
    candidate_count: int,
    feasible_count: int,
    evaluation_time_ms: float,
    timestamp: float,
    original_model_id: str | None = None,
    selected_gateway: str | None = None,
    selection_tier: str | None = None,
    request_id: str | None = None,
    candidates: list[dict] | None = None,
) -> Event:
    """
    Create ROUTING_DECISION event.

    Args:
        model_id: Model being routed
        selection_reason: Why this gateway was selected
        candidate_count: Total candidates evaluated
        feasible_count: Number of feasible candidates
        evaluation_time_ms: Time spent in decision engine
        timestamp: Unix timestamp
        original_model_id: Original request model ID
        selected_gateway: Selected gateway name
        selection_tier: T0/T1/T2 tier name
        request_id: Proxy request ID for tracing
        candidates: Full candidate details (optional, expensive)

    Returns:
        Event with RoutingDecision signal
    """
    return Event(
        signal=ROUTING_DECISION,
        payload={
            "model_id": model_id,
            "original_model_id": original_model_id,
            "selected_gateway": selected_gateway,
            "selection_reason": selection_reason,
            "selection_tier": selection_tier,
            "candidate_count": candidate_count,
            "feasible_count": feasible_count,
            "evaluation_time_ms": evaluation_time_ms,
            "request_id": request_id,
            "timestamp": timestamp,
            "candidates": candidates,
        },
    )


@event_factory
def RoutingDecisionFailed(
    model_id: str,
    candidate_count: int,
    evaluation_time_ms: float,
    timestamp: float,
    reason: str,
    original_model_id: str | None = None,
    request_id: str | None = None,
) -> Event:
    """
    Create ROUTING_DECISION_FAILED event.

    Args:
        model_id: Model that failed to route
        candidate_count: Total candidates evaluated
        evaluation_time_ms: Time spent in decision engine
        timestamp: Unix timestamp
        reason: Failure reason
        original_model_id: Original request model ID
        request_id: Proxy request ID for tracing

    Returns:
        Event with RoutingDecisionFailed signal
    """
    return Event(
        signal=ROUTING_DECISION_FAILED,
        payload={
            "model_id": model_id,
            "original_model_id": original_model_id,
            "candidate_count": candidate_count,
            "evaluation_time_ms": evaluation_time_ms,
            "request_id": request_id,
            "timestamp": timestamp,
            "reason": reason,
        },
    )


# Gateway Events
@event_factory
def GatewayStateChanged(
    url: str,
    connectivity: str,
    health: str,
    transition_type: str,
    previous_connectivity: str | None = None,
    previous_health: str | None = None,
    check_duration_ms: int = 0,
) -> Event:
    """
    Create GATEWAY_STATE_CHANGED event.

    Args:
        url: Gateway URL
        connectivity: "reachable" | "unreachable"
        health: "healthy" | "unhealthy" | "unknown"
        transition_type: "connectivity_only" | "health_only" | "both" | "initial"
        previous_connectivity: Previous connectivity state
        previous_health: Previous health state
        check_duration_ms: Duration of health check

    Returns:
        Event with GatewayStateChanged signal
    """
    return Event(
        signal=GATEWAY_STATE_CHANGED,
        payload={
            "url": url,
            "connectivity": connectivity,
            "health": health,
            "previous_connectivity": previous_connectivity,
            "previous_health": previous_health,
            "transition_type": transition_type,
            "check_duration_ms": check_duration_ms,
        },
    )


@event_factory
def GatewayRetryAttempted(
    gateway_url: str,
    method: str,
    path: str,
    attempt: int,
    max_retries: int,
    error_type: str,
    error_message: str,
    backoff_delay_ms: int,
) -> Event:
    """
    Create GATEWAY_RETRY_ATTEMPTED event.

    Args:
        gateway_url: Gateway URL
        method: HTTP method
        path: Request path
        attempt: Current attempt number (1-indexed)
        max_retries: Maximum retry count
        error_type: Exception class name
        error_message: Error message
        backoff_delay_ms: Milliseconds until next retry

    Returns:
        Event with GatewayRetryAttempted signal
    """
    return Event(
        signal=GATEWAY_RETRY_ATTEMPTED,
        payload={
            "gateway_url": gateway_url,
            "method": method,
            "path": path,
            "attempt": attempt,
            "max_retries": max_retries,
            "error_type": error_type,
            "error_message": error_message,
            "backoff_delay_ms": backoff_delay_ms,
        },
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
        Event with ModelLoaded signal
    """
    payload: dict[str, Any] = {"url": url, "model_id": model_id}
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
    payload: dict[str, Any] = {"url": url, "model_id": model_id}
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
    payload: dict[str, Any] = {"url": url, "model_id": model_id, "error": error}
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


@event_factory
def GatewayResourceUpdate(
    url: str,
    total_vram_mb: int,
    available_vram_mb: int,
    total_ram_mb: int,
    available_ram_mb: int,
    loaded_models: list[str],
    busy_models: list[str],
) -> Event:
    """
    Create GATEWAY_RESOURCE_UPDATE event.

    Args:
        url: Gateway URL
        total_vram_mb: Total VRAM in MB
        available_vram_mb: Available VRAM in MB
        total_ram_mb: Total RAM in MB
        available_ram_mb: Available RAM in MB
        loaded_models: List of loaded model IDs
        busy_models: List of busy model IDs

    Returns:
        Event with GatewayResourceUpdate signal
    """
    return Event(
        signal=GATEWAY_RESOURCE_UPDATE,
        payload={
            "url": url,
            "total_vram_mb": total_vram_mb,
            "available_vram_mb": available_vram_mb,
            "total_ram_mb": total_ram_mb,
            "available_ram_mb": available_ram_mb,
            "loaded_models": loaded_models,
            "busy_models": busy_models,
        },
    )


@event_factory
def ResourceReserved(
    gateway_name: str,
    model_id: str,
    reservation_id: str,
    vram_mb: int,
    ram_mb: int,
    timeout_seconds: float,
) -> Event:
    """
    Create RESOURCE_RESERVED event.

    Args:
        gateway_name: Gateway name
        model_id: Model for reservation
        reservation_id: Unique reservation ID
        vram_mb: VRAM reserved in MB
        ram_mb: RAM reserved in MB
        timeout_seconds: Reservation timeout

    Returns:
        Event with ResourceReserved signal
    """
    return Event(
        signal=RESOURCE_RESERVED,
        payload={
            "gateway_name": gateway_name,
            "model_id": model_id,
            "reservation_id": reservation_id,
            "vram_mb": vram_mb,
            "ram_mb": ram_mb,
            "timeout_seconds": timeout_seconds,
        },
    )


@event_factory
def ResourceReleased(
    gateway_name: str,
    model_id: str,
    reservation_id: str,
    vram_mb: int,
    ram_mb: int,
    reason: str,
) -> Event:
    """
    Create RESOURCE_RELEASED event.

    Args:
        gateway_name: Gateway name
        model_id: Model for reservation
        reservation_id: Unique reservation ID
        vram_mb: VRAM released in MB
        ram_mb: RAM released in MB
        reason: "completed" | "expired" | "cancelled"

    Returns:
        Event with ResourceReleased signal
    """
    return Event(
        signal=RESOURCE_RELEASED,
        payload={
            "gateway_name": gateway_name,
            "model_id": model_id,
            "reservation_id": reservation_id,
            "vram_mb": vram_mb,
            "ram_mb": ram_mb,
            "reason": reason,
        },
    )


# Request Events
@event_factory
def RequestQueued(
    request_id: str,
    model_id: str,
    priority: int,
) -> Event:
    """
    Create REQUEST_QUEUED event.

    INVARIANT: request_id always present (proxy request ID for tracking)

    Args:
        request_id: Proxy request ID for tracking and tracing
        model_id: Model requested
        priority: Request priority

    Returns:
        Event with RequestQueued signal
    """
    return Event(
        signal=REQUEST_QUEUED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "priority": priority,
        },
    )


@event_factory
def RequestProcessing(
    request_id: str,
    gateway_url: str,
    model_id: str,
) -> Event:
    """
    Create REQUEST_PROCESSING event.

    INVARIANT: request_id always present (proxy request ID for tracking)

    Args:
        request_id: Proxy request ID for tracking and tracing
        gateway_url: Gateway processing request
        model_id: Model being used

    Returns:
        Event with RequestProcessing signal
    """
    return Event(
        signal=REQUEST_PROCESSING,
        payload={
            "request_id": request_id,
            "gateway_url": gateway_url,
            "model_id": model_id,
        },
    )


@event_factory
def RequestCompleted(
    request_id: str,
    gateway_url: str,
    model_id: str,
    duration: float,
) -> Event:
    """
    Create REQUEST_COMPLETED event.

    INVARIANT: request_id always present (proxy request ID for tracking)

    Args:
        request_id: Proxy request ID for tracking and tracing
        gateway_url: Gateway that processed request
        model_id: Model used
        duration: Request duration in seconds

    Returns:
        Event with RequestCompleted signal
    """
    return Event(
        signal=REQUEST_COMPLETED,
        payload={
            "request_id": request_id,
            "gateway_url": gateway_url,
            "model_id": model_id,
            "duration": duration,
        },
    )


@event_factory
def RequestFailed(
    request_id: str,
    model_id: str,
    error: str,
    gateway_url: str | None = None,
) -> Event:
    """
    Create REQUEST_FAILED event.

    INVARIANT: request_id always present (proxy request ID for tracking)

    Args:
        request_id: Proxy request ID for tracking and tracing
        model_id: Model requested
        error: Error message
        gateway_url: Gateway URL (optional)

    Returns:
        Event with RequestFailed signal
    """
    return Event(
        signal=REQUEST_FAILED,
        payload={
            "request_id": request_id,
            "gateway_url": gateway_url,
            "model_id": model_id,
            "error": error,
        },
    )


@event_factory
def RequestTimeout(
    request_id: str,
    model_id: str,
    timeout_seconds: float,
    gateway_url: str | None = None,
) -> Event:
    """
    Create REQUEST_TIMEOUT event.

    INVARIANT: request_id always present (proxy request ID for tracking)

    Args:
        request_id: Proxy request ID for tracking and tracing
        model_id: Model requested
        timeout_seconds: Timeout value
        gateway_url: Gateway URL (optional)

    Returns:
        Event with RequestTimeout signal
    """
    return Event(
        signal=REQUEST_TIMEOUT,
        payload={
            "request_id": request_id,
            "gateway_url": gateway_url,
            "model_id": model_id,
            "timeout_seconds": timeout_seconds,
        },
    )


@event_factory
def RequestCapacityTimeout(
    request_id: str,
    model_id: str,
    timeout_seconds: float,
    retry_count: int,
    elapsed_s: float,
    pipeline_step_id: str | None = None,
) -> Event:
    """
    Create REQUEST_CAPACITY_TIMEOUT event.

    Emitted when all capacity retries are exhausted for a model.
    Distinct from request.failed — enables direct jq filtering:
        jq 'select(.signal == "request.capacity.timeout")'

    Args:
        request_id: Proxy request ID
        model_id: Model that had no capacity
        timeout_seconds: Total retry budget (seconds)
        retry_count: Number of retries attempted
        elapsed_s: Actual wall time spent retrying
        pipeline_step_id: Pipeline step (if request originated from pipeline)

    Returns:
        Event with RequestCapacityTimeout signal
    """
    payload: dict[str, object] = {
        "request_id": request_id,
        "model_id": model_id,
        "timeout_seconds": timeout_seconds,
        "retry_count": retry_count,
        "elapsed_s": elapsed_s,
    }
    if pipeline_step_id:
        payload["pipeline_step_id"] = pipeline_step_id
    return Event(
        signal=REQUEST_CAPACITY_TIMEOUT,
        payload=payload,
    )


@event_factory
def RoutingResourceDataMissing(
    request_id: str,
    model_id: str,
    gateway_ids: list[str],
) -> Event:
    """
    Create ROUTING_RESOURCE_DATA_MISSING event.

    Emitted when model is in gateway available_models (catalog) but
    absent from model_details (no resource data). This causes T0_INFEASIBLE
    via missing_gateway_resource_data constraint — routing fails despite
    model appearing in /v1/models.

    Distinguishes startup resource gap from genuine MODEL_NOT_FOUND.

    Args:
        request_id: Request that failed routing
        model_id: Model that has catalog entry but no resource data
        gateway_ids: Gateways that have model in catalog but no resource data

    Returns:
        Event with RoutingResourceDataMissing signal
    """
    return Event(
        signal=ROUTING_RESOURCE_DATA_MISSING,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_ids": gateway_ids,
        },
    )


@event_factory
def RoutingModelInfeasible(
    request_id: str,
    model_id: str,
    gateway_constraints: list[dict[str, Any]],
    excluded_gateway_ids: list[str],
) -> Event:
    """
    Create ROUTING_MODEL_INFEASIBLE event.

    Model exists in at least one gateway catalog but every candidate is
    infeasible (capacity, circuit breaker, resource constraints, etc.).
    Accompanies NO_FEASIBLE_GATEWAY (503) error response.

    Args:
        request_id: Request that failed routing
        model_id: Model that exists but cannot be served
        gateway_constraints: Per-gateway constraint failures
        excluded_gateway_ids: Gateways excluded by retry logic

    Returns:
        Event with RoutingModelInfeasible signal
    """
    return Event(
        signal=ROUTING_MODEL_INFEASIBLE,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_constraints": gateway_constraints,
            "excluded_gateway_ids": excluded_gateway_ids,
        },
    )


@event_factory
def RoutingCapacityDivergence(
    request_id: str,
    model_id: str,
    gateway_id: str,
    busy_models_state: str,
    capacity_pool_available: int,
    capacity_pool_in_flight: int,
    capacity_pool_max: int,
) -> Event:
    """
    Create ROUTING_CAPACITY_DIVERGENCE event.

    Emitted when telemetry-derived busy_models and CapacityPool slot state disagree.
    Primary purpose is stale telemetry observability; routing correctness still
    relies on CapacityPool admission.

    Args:
        request_id: Request that triggered divergence detection
        model_id: Divergent model
        gateway_id: Gateway with divergent state
        busy_models_state: Telemetry busy/idle claim
        capacity_pool_available: Available slots from CapacityPool
        capacity_pool_in_flight: Current in-flight requests in CapacityPool
        capacity_pool_max: Max concurrent slots in CapacityPool

    Returns:
        Event with RoutingCapacityDivergence signal
    """
    return Event(
        signal=ROUTING_CAPACITY_DIVERGENCE,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "busy_models_state": busy_models_state,
            "capacity_pool_available": capacity_pool_available,
            "capacity_pool_in_flight": capacity_pool_in_flight,
            "capacity_pool_max": capacity_pool_max,
        },
    )


@event_factory
def RoutingCapacityPreseeded(
    request_id: str,
    model_id: str,
    gateway_id: str,
    expected_capacity: int,
) -> Event:
    """
    Create ROUTING_CAPACITY_PRESEEDED event.

    Emitted when a cold-load request pre-seeds CapacityPool with expected
    capacity from catalog model_details, closing the cold-load bypass.

    Args:
        request_id: Request that triggered the pre-seed
        model_id: Model being cold-loaded
        gateway_id: Target gateway
        expected_capacity: Slots pre-seeded from max_concurrent_requests

    Returns:
        Event with RoutingCapacityPreseeded signal
    """
    return Event(
        signal=ROUTING_CAPACITY_PRESEEDED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "expected_capacity": expected_capacity,
        },
    )


@event_factory
def FederationSnapshotSent(
    gateway_id: str,
    all_models_count: int,
    available_models_count: int,
) -> Event:
    """
    Create FEDERATION_SNAPSHOT_SENT event.

    Emitted by Edge Stargate when it broadcasts GATEWAY_SNAPSHOT to Master.
    Documents the gap between all models (visible in /v1/models) and
    routable models (those with resource data in model_details).

    A non-zero gap_count means some models will route as MODEL_NOT_FOUND
    despite appearing in /v1/models — see gateway.snapshot.resource.gap
    in the Edge Gateway events for root cause.

    Args:
        gateway_id: Gateway identifier
        all_models_count: Total models from ws_client.get_models()
        available_models_count: Models with resource data (routable by Master)

    Returns:
        Event with FederationSnapshotSent signal
    """
    return Event(
        signal=FEDERATION_SNAPSHOT_SENT,
        payload={
            "gateway_id": gateway_id,
            "all_models_count": all_models_count,
            "available_models_count": available_models_count,
            "gap_count": all_models_count - available_models_count,
        },
    )


@event_factory
def RequestRemoved(
    request_id: str, reason: str, model_id: str, age_seconds: float
) -> Event:
    """
    Create REQUEST_REMOVED event.

    Args:
        request_id: Request identifier
        reason: Removal reason
        model_id: Model requested
        age_seconds: How long request was queued

    Returns:
        Event with RequestRemoved signal
    """
    return Event(
        signal=REQUEST_REMOVED,
        payload={
            "request_id": request_id,
            "reason": reason,
            "model_id": model_id,
            "age_seconds": age_seconds,
        },
    )


# Master Queue Events
@event_factory
def QueueMasterEntered(
    request_id: str,
    model_id: str,
    queue_position: int,
    compute_type: str = "",
    endpoint_category: str = "",
) -> Event:
    """
    Create QUEUE_MASTER_ENTERED event.

    Args:
        request_id: Request identifier
        model_id: Model being requested
        queue_position: Position in queue (1-indexed)
        compute_type: Optional; "cpu", "hybrid", or "gpu" (legacy)
        endpoint_category: Optional; "generation" or "embedding" (legacy)

    Returns:
        Event with QueueMasterEntered signal
    """
    return Event(
        signal=QUEUE_MASTER_ENTERED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "queue_position": queue_position,
            "compute_type": compute_type,
            "endpoint_category": endpoint_category,
        },
    )


@event_factory
def QueueMasterWoken(
    request_id: str,
    model_id: str,
    wait_time_ms: float,
    gateway_id: str | None = None,
    compute_type: str = "",
    endpoint_category: str = "",
) -> Event:
    """
    Create QUEUE_MASTER_WOKEN event.

    Args:
        request_id: Request identifier
        model_id: Model being requested
        wait_time_ms: Time spent waiting in queue
        gateway_id: Gateway with available capacity
        compute_type: Optional (legacy)
        endpoint_category: Optional (legacy)

    Returns:
        Event with QueueMasterWoken signal
    """
    return Event(
        signal=QUEUE_MASTER_WOKEN,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "wait_time_ms": wait_time_ms,
            "gateway_id": gateway_id,
            "compute_type": compute_type,
            "endpoint_category": endpoint_category,
        },
    )


@event_factory
def QueueMasterTimedOut(
    request_id: str,
    model_id: str,
    timeout_seconds: float,
    compute_type: str = "",
    endpoint_category: str = "",
) -> Event:
    """
    Create QUEUE_MASTER_TIMEOUT event.

    Args:
        request_id: Request identifier
        model_id: Model being requested
        timeout_seconds: Timeout value that was exceeded
        compute_type: Optional (legacy)
        endpoint_category: Optional (legacy)

    Returns:
        Event with QueueMasterTimedOut signal
    """
    return Event(
        signal=QUEUE_MASTER_TIMEOUT,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "timeout_seconds": timeout_seconds,
            "compute_type": compute_type,
            "endpoint_category": endpoint_category,
        },
    )


@event_factory
def QueueMasterToctou(
    request_id: str,
    model_id: str,
    compute_type: str,
    endpoint_category: str,
    retry_count: int,
) -> Event:
    """
    Create QUEUE_MASTER_TOCTOU event.

    Args:
        request_id: Request identifier
        model_id: Model being requested
        compute_type: "cpu", "hybrid", or "gpu"
        endpoint_category: "generation" or "embedding"
        retry_count: Number of retries so far

    Returns:
        Event with QueueMasterToctou signal
    """
    return Event(
        signal=QUEUE_MASTER_TOCTOU,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "compute_type": compute_type,
            "endpoint_category": endpoint_category,
            "retry_count": retry_count,
        },
    )


# Pipeline Step Events: Embedding
@event_factory
def PipelineStepEmbeddingStarted(
    execution_id: str,
    step_id: str,
    model_id: str,
    input_count: int,
) -> Event:
    """
    Create PIPELINE_STEP_EMBEDDING_STARTED event.

    Args:
        execution_id: Pipeline execution ID
        step_id: Step identifier
        model_id: Embedding model being used
        input_count: Number of texts to embed

    Returns:
        Event with PipelineStepEmbeddingStarted signal
    """
    return Event(
        signal=PIPELINE_STEP_EMBEDDING_STARTED,
        payload={
            "execution_id": execution_id,
            "step_id": step_id,
            "model_id": model_id,
            "input_count": input_count,
        },
    )


@event_factory
def PipelineStepEmbeddingCompleted(
    execution_id: str,
    step_id: str,
    model_id: str,
    input_count: int,
    duration_ms: float,
    embedding_dim: int,
) -> Event:
    """
    Create PIPELINE_STEP_EMBEDDING_COMPLETED event.

    Args:
        execution_id: Pipeline execution ID
        step_id: Step identifier
        model_id: Embedding model used
        input_count: Number of texts embedded
        duration_ms: Time taken in milliseconds
        embedding_dim: Dimension of embeddings

    Returns:
        Event with PipelineStepEmbeddingCompleted signal
    """
    return Event(
        signal=PIPELINE_STEP_EMBEDDING_COMPLETED,
        payload={
            "execution_id": execution_id,
            "step_id": step_id,
            "model_id": model_id,
            "input_count": input_count,
            "duration_ms": duration_ms,
            "embedding_dim": embedding_dim,
        },
    )


@event_factory
def PipelineStepEmbeddingFailed(
    execution_id: str,
    step_id: str,
    model_id: str,
    input_count: int,
    duration_ms: float,
    error: str,
    status_code: int | None = None,
) -> Event:
    """
    Create PIPELINE_STEP_EMBEDDING_FAILED event.

    Args:
        execution_id: Pipeline execution ID
        step_id: Step identifier
        model_id: Embedding model attempted
        input_count: Number of texts attempted
        duration_ms: Time taken before failure
        error: Error message
        status_code: HTTP status code if applicable

    Returns:
        Event with PipelineStepEmbeddingFailed signal
    """
    return Event(
        signal=PIPELINE_STEP_EMBEDDING_FAILED,
        payload={
            "execution_id": execution_id,
            "step_id": step_id,
            "model_id": model_id,
            "input_count": input_count,
            "duration_ms": duration_ms,
            "error": error,
            "status_code": status_code,
        },
    )


# Pipeline Step Events: Domain Verification
@event_factory
def PipelineStepDomainVerificationStarted(  # noqa: N802
    execution_id: str,
    step_id: str,
    domain: str,
    model_id: str,
    statement_count: int,
) -> Event:
    """
    Create PIPELINE_STEP_DOMAIN_VERIFICATION_STARTED event.

    Args:
        execution_id: Pipeline execution ID
        step_id: Step identifier
        domain: Domain being verified (e.g., "mathematics")
        model_id: Domain authority model
        statement_count: Number of statements to verify

    Returns:
        Event with PipelineStepDomainVerificationStarted signal
    """
    return Event(
        signal=PIPELINE_STEP_DOMAIN_VERIFICATION_STARTED,
        payload={
            "execution_id": execution_id,
            "step_id": step_id,
            "domain": domain,
            "model_id": model_id,
            "statement_count": statement_count,
        },
    )


@event_factory
def PipelineStepDomainVerificationCompleted(  # noqa: N802
    execution_id: str,
    step_id: str,
    domain: str,
    model_id: str,
    statement_count: int,
    passed_count: int,
    failed_count: int,
    duration_ms: float,
) -> Event:
    """
    Create PIPELINE_STEP_DOMAIN_VERIFICATION_COMPLETED event.

    Args:
        execution_id: Pipeline execution ID
        step_id: Step identifier
        domain: Domain that was verified
        model_id: Domain authority model used
        statement_count: Number of statements verified
        passed_count: Statements that passed verification
        failed_count: Statements that failed verification
        duration_ms: Time taken in milliseconds

    Returns:
        Event with PipelineStepDomainVerificationCompleted signal
    """
    return Event(
        signal=PIPELINE_STEP_DOMAIN_VERIFICATION_COMPLETED,
        payload={
            "execution_id": execution_id,
            "step_id": step_id,
            "domain": domain,
            "model_id": model_id,
            "statement_count": statement_count,
            "passed_count": passed_count,
            "failed_count": failed_count,
            "duration_ms": duration_ms,
        },
    )


# System Events
@event_factory
def SystemStarted(
    pid: int,
    role: str,
    started_at: float,
    version: str | None = None,
) -> Event:
    """
    Create SYSTEM_STARTED event.

    ∀ stargate session: exactly one system.started emitted at startup.
    pid + started_at together identify the session uniquely in a non-truncated log.

    Args:
        pid: OS process ID (os.getpid())
        role: "master" | "edge" | "relay"
        started_at: Unix epoch at startup (time.time())
        version: Package version string

    Returns:
        Event with SystemStarted signal
    """
    return Event(
        signal=SYSTEM_STARTED,
        payload={
            "pid": pid,
            "role": role,
            "started_at": started_at,
            "version": version,
        },
    )


@event_factory
def SystemShutdown() -> Event:
    """
    Create SYSTEM_SHUTDOWN event.

    Returns:
        Event with SystemShutdown signal
    """
    return Event(signal=SYSTEM_SHUTDOWN, payload={})


@event_factory
def FederationGatewayCatalogChanged(
    gateway_id: str,
    old_model_count: int,
    new_model_count: int,
    event_type: str | None = None,
    models: list[str] | None = None,
) -> Event:
    """
    Create FEDERATION_GATEWAY_CATALOG_CHANGED event.

    Emitted when a federated gateway's model catalog changes.

    Args:
        gateway_id: Unique gateway identifier (e.g., "edge-localhost-gateway")
        old_model_count: Previous number of models
        new_model_count: New number of models
        event_type: Type of change ('added', 'removed', 'changed')
        models: List of affected model IDs

    Returns:
        Event with FEDERATION_GATEWAY_CATALOG_CHANGED signal

    Note:
        Gateway identification uses gateway_id, not URL. Master routes via
        Edge Stargate URL, never direct to Gateway.
    """
    payload = {
        "gateway_id": gateway_id,
        "old_model_count": old_model_count,
        "new_model_count": new_model_count,
    }
    if event_type is not None:
        payload["event_type"] = event_type
    if models is not None:
        payload["models"] = models
    return Event(
        signal=FEDERATION_GATEWAY_CATALOG_CHANGED,
        payload=payload,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Federation Event Factories (all require @event_factory decorator)
# ─────────────────────────────────────────────────────────────────────────────


@event_factory
def FederationConnectionEstablished(
    remote_id: str,
    transport: str,  # "websocket" | "http_polling"
    latency_ms: int | None = None,
) -> Event:
    """Remote Stargate connected to Master."""
    payload = {
        "remote_id": remote_id,
        "transport": transport,
    }
    if latency_ms is not None:
        payload["latency_ms"] = latency_ms
    return Event(signal=FEDERATION_CONNECTION_ESTABLISHED, payload=payload)


@event_factory
def FederationConnectionLost(
    remote_id: str,
    reason: str,
) -> Event:
    """Remote Stargate disconnected from Master."""
    return Event(
        signal=FEDERATION_CONNECTION_LOST,
        payload={"remote_id": remote_id, "reason": reason},
    )


@event_factory
def FederationConnectionAuthenticated(
    remote_id: str,
    method: str,
) -> Event:
    """Remote Stargate authenticated with Master."""
    return Event(
        signal=FEDERATION_CONNECTION_AUTHENTICATED,
        payload={"remote_id": remote_id, "method": method},
    )


@event_factory
def FederationTelemetryReceived(
    remote_id: str,
    model_count: int,
    resource_summary: dict[str, Any],
    telemetry_age_ms: int | None = None,
) -> Event:
    """Master received telemetry from Remote/Edge."""
    payload = {
        "remote_id": remote_id,
        "model_count": model_count,
        "resource_summary": resource_summary,
    }
    if telemetry_age_ms is not None:
        payload["telemetry_age_ms"] = telemetry_age_ms
    return Event(signal=FEDERATION_TELEMETRY_RECEIVED, payload=payload)


@event_factory
def FederationTelemetryMarkedStale(
    remote_id: str,
    age_seconds: float,
    threshold_seconds: float,
) -> Event:
    """Telemetry from Remote exceeded staleness threshold."""
    return Event(
        signal=FEDERATION_TELEMETRY_MARKED_STALE,
        payload={
            "remote_id": remote_id,
            "age_seconds": age_seconds,
            "threshold_seconds": threshold_seconds,
        },
    )


@event_factory
def FederationTelemetryApplied(
    remote_id: str,
    changes: list[str],
) -> Event:
    """Telemetry applied to Master state."""
    return Event(
        signal=FEDERATION_TELEMETRY_APPLIED,
        payload={"remote_id": remote_id, "changes": changes},
    )


@event_factory
def FederationRoutingDelegated(
    request_id: str,
    target_remote: str,
    model_id: str,
    reason: str | None = None,
) -> Event:
    """Master delegated request to Remote Stargate."""
    payload = {
        "request_id": request_id,
        "target_remote": target_remote,
        "model_id": model_id,
    }
    if reason:
        payload["reason"] = reason
    return Event(signal=FEDERATION_ROUTING_DELEGATED, payload=payload)


@event_factory
def FederationRoutingRoutedLocal(
    request_id: str,
    model_id: str,
    reason: str,
) -> Event:
    """Master routed request to local Gateway."""
    return Event(
        signal=FEDERATION_ROUTING_ROUTED_LOCAL,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "reason": reason,
        },
    )


@event_factory
def FederationRoutingRejected(
    request_id: str,
    model_id: str,
    reason: str,
) -> Event:
    """Master rejected request (no available target)."""
    return Event(
        signal=FEDERATION_ROUTING_REJECTED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "reason": reason,
        },
    )


@event_factory
def FederationLoadRequested(
    request_id: str,
    target_remote: str,
    model_id: str,
) -> Event:
    """Master requested Remote load a model."""
    return Event(
        signal=FEDERATION_LOAD_REQUESTED,
        payload={
            "request_id": request_id,
            "target_remote": target_remote,
            "model_id": model_id,
        },
    )


@event_factory
def FederationLoadConfirmed(
    request_id: str,
    remote_id: str,
    model_id: str,
    duration_ms: int,
) -> Event:
    """Remote confirmed model loaded."""
    return Event(
        signal=FEDERATION_LOAD_CONFIRMED,
        payload={
            "request_id": request_id,
            "remote_id": remote_id,
            "model_id": model_id,
            "duration_ms": duration_ms,
        },
    )


@event_factory
def FederationLoadFailed(
    request_id: str,
    remote_id: str,
    model_id: str,
    error: str,
) -> Event:
    """Remote failed to load model."""
    return Event(
        signal=FEDERATION_LOAD_FAILED,
        payload={
            "request_id": request_id,
            "remote_id": remote_id,
            "model_id": model_id,
            "error": error,
        },
    )


@event_factory
def FederationOrchestratorDecided(
    request_id: str,
    decision_type: str,  # "route" | "load" | "queue" | "reject"
    target: str | None,
    reason: str,
    alternatives_considered: list[str] | None = None,
) -> Event:
    """Orchestrator made a routing/load decision."""
    payload = {
        "request_id": request_id,
        "decision_type": decision_type,
        "target": target,
        "reason": reason,
    }
    if alternatives_considered:
        payload["alternatives_considered"] = alternatives_considered
    return Event(signal=FEDERATION_ORCHESTRATOR_DECIDED, payload=payload)


@event_factory
def FederationOrchestratorEvicted(
    target_remote: str,
    model_id: str,
    reason: str,
) -> Event:
    """Orchestrator evicted model from Remote."""
    return Event(
        signal=FEDERATION_ORCHESTRATOR_EVICTED,
        payload={
            "target_remote": target_remote,
            "model_id": model_id,
            "reason": reason,
        },
    )


@event_factory
def FederationGatewayResourceUpdateSignal(
    gateway_id: str,
    source: str = "http_polling",
) -> Event:
    """
    Create GATEWAY_RESOURCE_UPDATE wake-up signal for federation.

    Minimal payload for telemetry freshness notification.
    Used by HTTP polling master to wake up FreshnessWaiter.

    Args:
        gateway_id: Gateway identifier
        source: Source of update ("http_polling", "websocket", etc.)

    Returns:
        Event with GatewayResourceUpdate signal
    """
    return Event(
        signal=GATEWAY_RESOURCE_UPDATE,
        payload={
            "gateway_id": gateway_id,
            "source": source,
        },
    )


@event_factory
def FederationModelLoaded(gateway_id: str, model_id: ModelId | str) -> Event:
    """
    Create MODEL_LOADED event for federation (gateway_id instead of url).

    Args:
        gateway_id: Gateway identifier
        model_id: Model that was loaded

    Returns:
        Event with ModelLoaded signal
    """
    return Event(
        signal=MODEL_LOADED,
        payload={
            "gateway_id": gateway_id,
            "model_id": model_id,
        },
    )


@event_factory
def FederationModelUnloaded(gateway_id: str, model_id: ModelId | str) -> Event:
    """
    Create MODEL_UNLOADED event for federation (gateway_id instead of url).

    Args:
        gateway_id: Gateway identifier
        model_id: Model that was unloaded

    Returns:
        Event with ModelUnloaded signal
    """
    return Event(
        signal=MODEL_UNLOADED,
        payload={
            "gateway_id": gateway_id,
            "model_id": model_id,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Cloud Proxy Events (Stargate-side proxy observation at coordination boundary)
# ─────────────────────────────────────────────────────────────────────────────


@event_factory
def CloudProxyAvailable(proxy_url: str, model_count: int) -> Event:  # noqa: N802
    """Proxy became reachable and catalog was fetched."""
    return Event(
        signal=CLOUD_PROXY_AVAILABLE,
        payload={"proxy_url": proxy_url, "model_count": model_count},
    )


@event_factory
def CloudProxyUnavailable(proxy_url: str, reason: str) -> Event:  # noqa: N802
    """Proxy health probe failed — no cloud models will be registered."""
    return Event(
        signal=CLOUD_PROXY_UNAVAILABLE,
        payload={"proxy_url": proxy_url, "reason": reason},
    )


@event_factory
def CloudProxyCatalogUpdated(  # noqa: N802
    proxy_url: str, model_count: int, gateway_count: int
) -> Event:
    """Proxy catalog re-fetched and virtual gateways updated."""
    return Event(
        signal=CLOUD_PROXY_CATALOG_UPDATED,
        payload={
            "proxy_url": proxy_url,
            "model_count": model_count,
            "gateway_count": gateway_count,
        },
    )


@event_factory
def CloudProxyCatalogFetchFailed(proxy_url: str, error: str) -> Event:  # noqa: N802
    """Failed to fetch catalog from cloud proxy."""
    return Event(
        signal=CLOUD_PROXY_CATALOG_FETCH_FAILED,
        payload={"proxy_url": proxy_url, "error": error},
    )


@event_factory
def PipelineRegistryUnavailable(  # noqa: N802
    pipeline_id: str,
    missing_models: list[str],
) -> Event:
    """
    Pipeline permanently skipped after deferred retry — model deps unresolvable.

    Payload:
        pipeline_id: Pipeline ID that could not be loaded
        missing_models: Model IDs absent from all gateway catalogs and pipeline registry
    """
    return Event(
        signal=PIPELINE_REGISTRY_UNAVAILABLE,
        payload={
            "pipeline_id": pipeline_id,
            "missing_models": missing_models,
        },
    )
