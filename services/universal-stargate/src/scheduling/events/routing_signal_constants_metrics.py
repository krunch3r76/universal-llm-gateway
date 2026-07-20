"""Signal string constants for Stargate routing metrics: model-load initiated/completed and per-request gateway-trace/routed signals, plus token-count precondition/completion/failure signals. Consumed by `routing_factories_metrics_load.py` and `routing_factories_metrics_tokens.py`'s event factories."""

# ruff: noqa: N802

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

REQUEST_GATEWAY_TRACE = "request.gateway.trace"
"""
Gateway selection invariant trace for a request.

Payload: {
    "request_id": str,
    "model_id": str,
    "phase": str,
    "selected_gateway": str | None,
    "capacity_gateway": str | None,
    "sticky_gateway": str | None,
    "final_gateway": str | None,
    "forwarded_gateway": str | None,
    "remote_id": str | None,
    "gateway_url": str | None,
    "invariant_status": str,
    "reason": str | None,
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
    "already_loaded": bool,
    "request_id": Optional[str]  # Present when triggered by request
}
"""

MODEL_LOAD_COMPLETED = "model.load.completed"
"""
Model loading completed on gateway
Emitted when a model load operation finishes (success or failure).

Payload: {
    "model_id": str,
    "gateway_url": str,
    "gateway_name": str,
    "timestamp": float,
    "success": bool,
    "load_time_ms": float,
    "error": Optional[str],
    "request_id": Optional[str]  # Present when triggered by request
}
"""

TOKEN_COUNT_COMPLETED = "token.count.completed"
"""
Token counting completed
Emitted when a token counting operation completes (success or failure).

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

TOKEN_COUNT_PRECONDITION = "token.count.precondition"
"""
Token-counting legality/precondition trace.

Payload: {
    "request_id": str,
    "model_id": str,
    "target_gateway": str,
    "selected_gateway": str | None,
    "gateway_url": str | None,
    "remote_id": str | None,
    "sticky": bool,
    "loaded_on_gateway": bool,
    "known_to_gateway": bool,
    "skip_requested": bool,
    "legal_reason": str,
    "content_type": str | None,
    "tools_count": int,
}
"""

TOKEN_COUNTING_FAILED = "token.counting.failed"
"""
Federated token counting failed due to infrastructure issue.
Emitted when the gateway/edge container is unreachable.

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_id": str,
    "error": str
}
"""

GATEWAY_VRAM_ORPHAN_DETECTED = "gateway.vram.orphan.detected"
"""
Forwarded from Gateway when hardware VRAM exceeds tracked VRAM (unmanaged GPU use).

Payload: {
    "gateway_id": str,
    "hardware_used_mb": int,
    "catalog_used_mb": int,
    "discrepancy_mb": int,
    "tracked_models": list[str],
}
"""

GATEWAY_VRAM_STALENESS_DETECTED = "gateway.vram.staleness.detected"
"""
Forwarded from Gateway when tracked VRAM exceeds hardware (stale catalog profiles).

Payload: {
    "gateway_id": str,
    "hardware_used_mb": int,
    "catalog_used_mb": int,
    "discrepancy_mb": int,
    "tracked_models": list[str],
}
"""

GATEWAY_PHANTOM_MODEL_DETECTED = "gateway.model.phantom.detected"
"""
Forwarded from Gateway when a running worker is not tracked as LOADED/BUSY.

Payload: {
    "gateway_id": str,
    "model_id": str,
    "process_status": str,
    "tracker_status": str | None,
}
"""

GATEWAY_PHANTOM_MODEL_CLEANED = "gateway.model.phantom.cleaned"
"""
Forwarded from Gateway after phantom cleanup attempt.

Payload: {
    "gateway_id": str,
    "model_id": str,
    "success": bool,
    "vram_freed_mb": int | None,
}
"""
