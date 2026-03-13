# Routing and Capacity

Gateway selection and request routing using feasibility-tier classification
with utility scoring. Master-side admission control for concurrency.

**Source**: `services/universal-stargate/systems/routing/`

## Architecture

All gateways are accessed via federation. No local Gateway concept at Master.

```
ModelRouter.route_request()
  → build_placement()              RAM/VRAM requirements
  → collect_stargates()            All remote Stargates → Gateway snapshots
  → TelemetryFreshnessWaiter       Wait for fresh data if stale
  → DecisionEngine.select()        Feasibility + scoring + selection
  → AdmissionQueue.acquire()       Capacity gating (FIFO per-model)
  → Forward to Remote Stargate
```

## Feasibility Tiers

| Tier | Meaning | Action |
|---|---|---|
| T0 | Infeasible (unhealthy, at capacity, missing model) | Skip |
| T1 | Warm (model loaded, capacity available) | Preferred |
| T2 | Cold (needs model load, possible eviction) | Fallback |

Selection: `∃ T1 candidate ⟹ selected ∈ T1 ∨ (T2.score ≥ T1.score + margin)`

## Decision Engine

`DecisionEngine` is **stateless** — routing state lives in external trackers.

1. **Feasibility evaluation** — classify each gateway as T0/T1/T2
2. **Utility scoring** — weighted: resource fit, staleness penalty, stability bonus
3. **Selection rule** — pick highest-scoring feasible gateway
4. **Sticky guard** — prevent concurrent race for sticky models (see below)

**Source**: `selection/decision/engine/` (package)

## Sticky Routing

Sticky models (configured in routing policy) must route to at most one gateway.

**Race scenario** (without guard):
1. Request 1: selects gateway A, awaits model load
2. Request 2: finds A at capacity (T0), selects B (T1) → **violation**

**Guard behavior**:
```
sticky ∧ selected ≠ current_best ∧ bound_at_capacity
  ⟹ selected = None
  ⟹ HTTPException(503, STICKY_CAPACITY)
  ⟹ retry loop in proxy layer
```

**Tracker**: `StickyPlacementTracker` (process lifetime, not per-request).

## Admission Control

Master tracks per-(gateway, model) concurrency and gates forwarding.

```
Gateway Telemetry → CapacityLedger.set_capacity()
                         ↓
DecisionEngine.select() → AdmissionQueue.acquire(request_id, model, [gateways])
                         ↓
              Available? → Reserve slot → Forward
              At capacity? → FIFO queue → Wait
                         ↓
      MODEL_EXECUTION_COMPLETED → CapacityReleaseConsumer
                         ↓
              CapacityLedger.release() → AdmissionQueue._dispatch()
                         ↓
                 Wake FIFO head → Check capacity → Resolve
```

### Components

| Component | File | Role |
|---|---|---|
| `CapacityLedger` | `capacity/ledger.py` | Per-(gateway, model) slot tracking |
| `AdmissionQueue` | `capacity/queue.py` | FIFO per-model admission, event-driven |
| `CapacityReleaseConsumer` | `capacity/consumer.py` | Subscribes to completion events |

### Invariants

```
∀ slot: in_flight[slot] ≥ 0
∀ slot: in_flight[slot] ≤ capacity[slot]
∀ request_id ∈ reservations: ∃! slot
∀ acquired_slot: emit_execution_completed() called exactly once
```

## Eviction Planning

When a T2 (cold) gateway is selected and a model must be loaded, `_compute_eviction_plan`
calculates which idle models to unload to free sufficient VRAM/RAM.

### VRAM Source Priority

The planner uses **measured VRAM first**, catalog estimate as fallback:

```
∀ evictable model m:
  vram(m) = measured_model_vram[m]   if RESOURCE_UPDATE has been received
           | model_details[m].vram    otherwise (catalog estimate)
```

`Gateway.model_measured_vram` (`dict[ModelId, int]`) is populated by
`collector.py` from `GatewayClientState._measured_model_vram`, which is set by
`ResourceUpdateHandler` when `RESOURCE_UPDATE.model_vram` arrives (nvidia-smi readings).

If no `RESOURCE_UPDATE` has been received yet (e.g., model just loaded), the planner
falls back to the catalog value. This is correct: the catalog represents the theoretical
requirement; measured is a refinement once the GPU reports actual allocation.

### Eviction Strategy

**Minimum-necessary eviction**: Candidates are sorted by effective VRAM (then RAM)
descending; the planner accumulates into `models_to_evict` and breaks as soon as
`effective_free + freed >= required` for both VRAM and RAM. Thus only the fewest
models needed to cover the deficit are evicted (e.g. one large GPU model instead
of all idle models). Hardware correction (AMD/HIP upper bound) still applies only
when evicting all loaded models.

**Source**: `selection/decision/eviction_planning.py:_compute_eviction_plan`

## Telemetry Freshness

`TelemetryFreshnessWaiter` uses epoch-based `asyncio.Condition` (not `Event.clear()`).
Subscribes lazily on first `wait_for_telemetry_update()` call.
Each `GATEWAY_RESOURCE_UPDATE` increments epoch and wakes all waiters.

## Key Files

| File | Purpose |
|---|---|
| `model_router.py` | Entry point, `route_request()` orchestration |
| `telemetry/freshness_waiter.py` | Epoch-based telemetry wait |
| `selection/stargate_collector.py` | Federated stargate collection |
| `selection/collector.py` | Placement requirements builder |
| `selection/decision/engine/` | `DecisionEngine.select()` (stateless) |
| `selection/decision/eviction_planning.py` | Eviction plan: idle model selection, VRAM source priority |
| `selection/decision/stability.py` | `StickyPlacementTracker` |
| `selection/decision/feasibility.py` | T0/T1/T2 classification |
| `selection/decision/scorer.py` | Utility scoring |
| `capacity/ledger.py` | Per-(gateway, model) concurrency tracking |
| `capacity/queue.py` | FIFO admission queue |
| `capacity/consumer.py` | Completion event → slot release |

## Events

### Emitted

| Event | Source | Trigger |
|---|---|---|
| `ROUTING_DECISION` | `DecisionEngine` | Successful gateway selection |
| `ROUTING_DECISION_FAILED` | `DecisionEngine` | No feasible gateway |

### Consumed

| Event | Handler | Purpose |
|---|---|---|
| `GATEWAY_RESOURCE_UPDATE` | `TelemetryFreshnessWaiter` | Increment epoch, wake waiters |
| `MODEL_EXECUTION_COMPLETED` | `CapacityReleaseConsumer` | Release slot, wake admission queue |
| `MODEL_LOADED` / `MODEL_UNLOADED` | `ResourceVerifier` | Invalidate config cache |

## Anti-Patterns

| Pattern | Fix |
|---|---|
| Polling for gateway availability | Subscribe to `MODEL_EXECUTION_COMPLETED` events |
| Evicting target model variants | Filter by `routing_key` before eviction |
| Creating `DecisionEngine` per-request | Engine is stateless; state in `StickyPlacementTracker` |
| `asyncio.Event.clear()` for signaling | Use epoch-based `asyncio.Condition` |
| Direct local gateway access | All gateways via federation |
