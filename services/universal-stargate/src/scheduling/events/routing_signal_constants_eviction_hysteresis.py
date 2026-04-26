"""Stargate scheduling routing events — split module (routing_signal_constants_eviction_hysteresis.py)."""

# ruff: noqa: N802

# ========================================
# Eviction Hysteresis Event Signals
# ========================================

EVICTION_COOLDOWN_BLOCKED = "scheduler.eviction.cooldown.blocked"
"""
All evictable candidates were protected (cooldown and/or demand).
Escape hatch activated: least-harmful candidate evicted.

Payload: {
    "request_id": str | None,
    "model_id": str,
    "gateway_id": str,
    "evicted_model_id": str,
    "escape_reason": str,      # "cooldown" | "demand"
    "cooldown_remaining_s": float | None,
    "candidates_in_cooldown": int,
    "candidates_demand_protected": int,
    "timestamp": float
}
"""

EVICTION_COOLDOWN_APPLIED = "scheduler.eviction.cooldown.applied"
"""
Eviction planner filtered candidates by cooldown — informational.
Emitted when ≥1 model was protected by cooldown during eviction planning.

Payload: {
    "model_id": str,
    "gateway_id": str,
    "protected_count": int,
    "cooldown_s": float,
    "timestamp": float
}
"""

EVICTION_DEMAND_APPLIED = "scheduler.eviction.demand.applied"
"""
Eviction planner filtered candidates by demand protection — informational.
Emitted when ≥1 model was protected by routing queue demand.

Payload: {
    "model_id": str,
    "gateway_id": str,
    "protected_count": int,
    "waiter_counts": dict[str, int],
    "timestamp": float
}
"""

ROUTING_EVICTION_EXECUTE_FAILED = "routing.eviction.execute.failed"
"""
Eviction execution failed at T2 finalize after admission.

Emitted from finalize_selection_and_load when execute_master_eviction returns
False — the eviction plan computed during selection no longer matches gateway
state by the time the request was admitted, so the gateway rejected (or could
not satisfy) the planned unloads. Carries enough context for forensics: which
models were planned, the per-candidate constraint breakdown from the selection
trace, and the selected gateway / tier at decision time.

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_id": str,
    "selection_tier": str,
    "selection_reason": str,
    "models_to_evict": list[str],
    "freed_vram_mb": int,
    "freed_ram_mb": int,
    "estimated_cost": float,
    "cooldown_protected_count": int,
    "demand_protected_count": int,
    "candidate_breakdown": list[dict],   # [{gateway_id, feasibility_tier,
                                          #   constraints_failed: list[str]}]
    "timestamp": float
}
"""
