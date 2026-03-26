# Routing and Capacity

Gateway selection and request routing using feasibility-tier classification
with utility scoring. Master-side admission control for concurrency.

**Source**: `services/universal-stargate/systems/routing/`, `services/universal-stargate/systems/proxy/`

## Architecture

All gateways are accessed via federation. No local Gateway concept at Master.

```
ModelRouter.route_request()
  → build_placement()              RAM/VRAM requirements
  → collect_stargates()            All remote Stargates → Gateway snapshots
  → TelemetryFreshnessWaiter       Wait for fresh data if stale
  → DecisionEngine.select()        Feasibility + scoring + selection
  → CapacityPool.acquire_token()   Capacity gating (FIFO per-model)
  → Forward to Remote Stargate
```

<!-- GENERATED:START -->

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
Gateway Telemetry → CapacityPool.set_capacity()
                         ↓
DecisionEngine.select() → CapacityPool.acquire_token(request_id, model, [gateways])
                         ↓
              Available? → Reserve slot → Forward
              At capacity? → FIFO queue → Wait
                         ↓
              CapacityToken.__aexit__() → CapacityPool._release()
                         ↓
              CapacityPool._release() → CapacityPool._dispatch()
                         ↓
                 Wake FIFO head → Check capacity → Resolve
```

### Components

| Component | File | Role |
|---|---|---|
| `CapacityPool` | `capacity/pool.py` | Per-(gateway, model) slot tracking, FIFO admission, self-releasing tokens |

### Invariants

```
∀ slot: in_flight[slot] ≥ 0
∀ slot: in_flight[slot] ≤ capacity[slot]
∀ request_id ∈ reservations: ∃! slot
∀ acquired slot: released via CapacityToken.__aexit__ (structural, not event-driven)
```

<!-- GENERATED:END -->

<!-- AUTHORED -->

### Capacity Architecture (CapacityPool)

`CapacityPool` replaces the former three-component architecture
(`CapacityLedger` + `AdmissionQueue` + `CapacityReleaseConsumer`) with a
single unified component. The consolidation eliminates cross-component
coordination bugs — particularly the release path where the consumer had to
bridge ledger updates and queue dispatch atomically.

**INVARIANT**: `∀ forwarded request: exactly one acquire() → release() pair.`
Bypass of this pool is a silent correctness violation — the system appears
healthy while running over capacity. The only observable symptom is degraded
latency under load and eventual gateway saturation.

The pool uses self-releasing `CapacityToken` (async context manager): acquire
via `async with pool.acquire(...)`, release is automatic on `__aexit__`. Every
error path, timeout path, and cancellation path releases the token without
explicit coordination event emission — release is structural, not event-driven.

**Canary signals**:
- `capacity.slot.leak.recovered` — emitted via `@event_factory`
  (`CapacitySlotLeakRecovered`) when the cancellation race in `_wait_for_slot`
  is hit: `_dispatch` resolved a waiter's future (incrementing in_flight)
  but the waiter's task was cancelled before a `CapacityToken` was created.
  The slot is recovered by `_recover_leaked_slot`; this signal makes it
  observable. Non-zero rate under load is expected; sustained high rate
  warrants timeout tuning investigation.
- `routing.capacity.divergence` — emitted when telemetry `busy_models` state
  disagrees with `CapacityPool` slot state on a selected gateway/model.

<!-- /AUTHORED -->

## Request Execution Path

<!-- AUTHORED -->

The request execution path spans `systems/proxy/` — from HTTP entry to gateway
forwarding. The routing layer (above) selects the gateway; the execution path
prepares, transforms, forwards, and retries the request.

```
process_chat_completion()          chat.py — dispatch hub
  → RequestPreparer.prepare()      preparer.py — context construction
    → mode transforms              mode_transforms.py — normal/master/bypass
  → execute_with_retry()           retry.py — capacity + upstream retry
    → CapacityPool.acquire_token()
    → execute_request()            request_executor — gateway forwarding
```

### Dispatch Hub (`chat.py`)

`process_chat_completion` is the sole entry point for `/v1/chat/completions`.
It performs pipeline detection (virtual model IDs route to `PipelineExecutor`),
request preparation, lifecycle event emission (`RequestSnapshotReceived`,
`RequestCompleted`/`RequestFailed`), and delegates execution to the retry loop.
No retry logic, no transformation logic — those are separated into their own
modules.

### Context Construction (`preparer.py`)

`RequestPreparer` builds a fully-populated `RequestContext` from a raw HTTP
request. It handles model ID validation (suffix stripping), parameter
extraction, `response_format` inference, and dispatches to mode-specific
transforms. Three modes: normal (full pipeline), master (federation forwarding),
bypass (minimal transforms for debugging).

### Mode Transforms (`mode_transforms.py`)

Standalone functions for each preparation mode, extracted from the former
monolithic `RequestPreparer`. Normal mode applies the full transformation
pipeline (model config, profiles, system prompts, content filters). Master
mode applies client-facing policy but defers model-specific transforms to
the edge. Legacy `prompt` field handling (conversion to messages format) is
here.

### Retry Loop (`retry.py`)

`execute_with_retry` implements unified capacity-retry and upstream-retry with
independent time budgets, exponential backoff, and gateway exclusion on upstream
failures.

**Terminal event invariant**: every exit path emits exactly one terminal event
set.  The events are PascalCase `@event_factory` signals:
- Success: `RequestCompleted` (`request.completed`) + `RequestSnapshotCompleted` (`request.snapshot.completed`)
- Failure: `RequestFailed` (`request.failed`) + `RequestSnapshotFailed` (`request.snapshot.failed`)

Capacity slot release is structural via `CapacityToken.__aexit__` — it does
NOT depend on these events.  The events are observational (metrics, tracing,
pipeline evaluation).

**Error classification** (concrete helpers in `retry.py`):
- `_is_capacity_error`: 503/504 with retryable envelope field or retryable
  `ErrorCode` → retry within capacity budget (`queue_timeout`)
- `_is_retryable_upstream_error`: 502 from federated gateway with retryable
  envelope → retry with gateway exclusion (`upstream_retry_timeout`),
  failed gateway extracted via `_extract_failed_gateway_id`
- Permanent resource failures: `can_fit_with_eviction` NOT in the failed
  constraint set → non-retryable, immediate 503

The permanent-vs-transient classification is critical: a gateway where
`can_fit_with_eviction` IS in the failed constraint set is **transient**
(retryable), not permanent. Misclassifying it as permanent discards viable
routing paths and causes non-retryable failures that mask retryable capacity
conditions.

<!-- /AUTHORED -->

<!-- GENERATED:START -->

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

<!-- GENERATED:END -->

## Key Files

### Routing (`systems/routing/`)

| File | Purpose |
|---|---|
| `model_router.py` | Entry point, `route_request()` orchestration |
| `telemetry/freshness_waiter.py` | Epoch-based telemetry wait |
| `selection/stargate_collector.py` | Federated stargate collection |
| `selection/collector.py` | Placement requirements builder |
| `selection/decision/engine/core.py` | `DecisionEngine.select()` (stateless) |
| `selection/decision/eviction_planning.py` | Eviction plan: idle model selection, VRAM source priority |
| `selection/decision/stability.py` | `StickyPlacementTracker` |
| `selection/decision/feasibility.py` | T0/T1/T2 classification |
| `selection/decision/scorer.py` | Utility scoring |
| `capacity/pool.py` | `CapacityPool` — concurrency tracking, FIFO admission, self-releasing tokens |
| `queue/request_queue/runtime.py` | Request queue runtime (enqueue, dequeue, processing) |
| `queue/request_queue/types.py` | Request queue type definitions |
| `queue/verification.py` | Resource verification against gateway state events |
| `selection/selector.py` | Legacy (empty — pending removal) |

### Proxy — Request Execution (`systems/proxy/`)

| File | Purpose |
|---|---|
| `stargate/requests/chat.py` | Dispatch hub — `/v1/chat/completions` entry point |
| `stargate/requests/retry.py` | Unified retry loop — capacity + upstream retry, gateway exclusion |
| `core/nonstreaming/preparer.py` | `RequestPreparer` — context construction from raw HTTP |
| `core/nonstreaming/mode_transforms.py` | Mode-specific transforms (normal, master, bypass) |

## Events

### Emitted by Retry Loop (`retry.py`)

| Event Factory | Signal | Trigger |
|---|---|---|
| `RequestCompleted` | `request.completed` | Non-streaming request completed successfully |
| `RequestFailed` | `request.failed` | Request failed (all retries exhausted or non-retryable) |
| `RequestSnapshotCompleted` | `request.snapshot.completed` | Response body captured (non-streaming) |
| `RequestSnapshotFailed` | `request.snapshot.failed` | Error details captured on failure |

### Emitted by Routing (`model_router.py`, `decision/engine/`)

| Event Factory | Signal | Trigger |
|---|---|---|
| `RoutingDecision` | `scheduler.routing.decided` | Successful gateway selection |
| `RoutingDecisionFailed` | `scheduler.routing.failed` | No feasible gateway |
| `CapacitySlotLeakRecovered` | `capacity.slot.leak.recovered` | Cancellation race slot recovered in CapacityPool |

### Consumed

| Event | Handler | Purpose |
|---|---|---|
| `GATEWAY_RESOURCE_UPDATE` | `TelemetryFreshnessWaiter` | Increment epoch, wake waiters |
| `GATEWAY_RESOURCE_UPDATE` | `CapacityPool._ensure_subscribed` | Wake FIFO queue for re-evaluation |
| `MODEL_UNLOADED` | `EvictionWaiter` | Wake eviction wait handles |

## Anti-Patterns

| Pattern | Fix |
|---|---|
| Bypassing `CapacityPool.acquire_token()` | Every forwarded request MUST acquire a capacity slot |
| Assuming release is event-driven | Release is structural via `CapacityToken.__aexit__`; no external event triggers it |
| Misclassifying `can_fit_with_eviction` as permanent failure | Transient — retryable via capacity retry path |
| Evicting target model variants | Filter by `routing_key` before eviction |
| Creating `DecisionEngine` per-request | Engine is stateless; state in `StickyPlacementTracker` |
| `asyncio.Event.clear()` for signaling | Use epoch-based `asyncio.Condition` |
| Direct local gateway access | All gateways via federation |
