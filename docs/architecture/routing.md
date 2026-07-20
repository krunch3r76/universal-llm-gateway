# Routing System Architecture — Post-P0 Targeted Refresh (Step 9)

<!-- GENERATED:START source_corpus=doc-excerpts-consolidated.md source_sha256=871f957be6cfd5b4fb147364f91af9606b84ca4f022edb2311c0a1e2a20c968e generated=2026-07-20 session=cursor-2026-07-20-032652-83d -->
_Generated from docstrings, signatures, and imports; claims reflect what the source **declares**, not verified runtime behavior. CDP Sonnet draft verifies doc↔declaration consistency, not docstring↔behavior truth._

## Scope of this pass

This is a **targeted refresh**, not a full re-projection of the subsystem. The staged corpus for step 9 covers **18 modules** (routing decision core + eviction chain + package root + catalog + stargate collector + queue re-export + capacity shim) — the subset needed to re-project the five Post-P0 bind items below. It does **not** re-stage the remaining ~38 modules the prior provisional draft (`source/routing.md`, `inventory_sha=a71d07ced6db`, 2026-07-19) covered, including `capacity/_pool/*` internals, `selection/decision/engine/*`, `selection/decision/scorer.py`, `config.py`, `stability.py`, `requirements.py`, `resource_checks.py`, `eviction_cooldown_policy.py`, `model_checks.py`, `selection/collector.py`, `telemetry/freshness_waiter.py`, `queue/verification.py`, `queue/request_queue/{runtime,maintenance,types}.py`, and `aggregate_model_availability.py`. Those sections are **not reproduced here** — see `missing_coverage` below rather than treating their absence as a retraction of the prior draft's claims about them (which were separately spot-checked clean in arch-doc review 5427, 0 Critical / 2 Warning / 4 Suggestion).

**Package path:** `services/universal-stargate/systems/routing`
**Staged module count (this pass):** 18 of 56 scanned (per `doc-scan-summary.txt`, 2026-07-20 rescan: 56/56 modularize-green)

---

## Post-P0 bind: what changed vs. the prior draft

| # | Bind | Disposition this pass |
|---|---|---|
| 1 | `execute_eviction_plan` returns typed `EvictionOutcome` with `.ok`; no EventBus → `UNCONFIRMED_NO_BUS` (fail-closed) | **Confirmed**, `eviction/executor.py` |
| 2 | `UnloadResult.SHUTDOWN` distinct from confirmed unload | **Confirmed**, `eviction/event_waiter.py` |
| 3 | Vestigial `eviction/planner.py` (`unload_models` / `get_idle_models`) deleted | **Confirmed absent** — not in staged corpus; `eviction/__init__.py` no longer imports from it |
| 4 | `selection/decision/busy_view.py` owns tracker-over-telemetry busy/idle matrix | **Confirmed**, `selection/decision/busy_view.py` |
| 5 | Feasibility ≠ CapacityPool admission (concurrency ⇏ T0) | **Confirmed** — `__init__.py` invariant text corrected from the prior draft's stale wording (this is also the fix for arch-doc-review finding **W1**, see below) |

---

## Module Inventory (staged this pass)

| Module | Description |
|---|---|
| `__init__.py` | Package root; re-exports `ModelRouter`, `DecisionEngine`, `evaluate_feasibility`, `calculate_utility`, `FeasibilityTier`, `ScoreComponents`. Declares corrected system-level invariants. |
| `model_router.py` | Unified model router (Predicate-Score Pipeline); central orchestration for gateway selection, eviction, and federated routing. |
| `capacity/pool.py` | Shim re-exporting `CapacityPool`, `CapacityToken`, `QueueFullError` from the `_pool` package; preserves the historical `systems.routing.capacity.pool` import path. Internals (`_pool/*`) not staged this pass. |
| `eviction/__init__.py` | Exports typed eviction helpers for federated unload paths: `EvictionWaiter`, `UnloadResult`, `EvictionInflightRegistry`, `EvictionOutcome`, `EvictionStatus`, `execute_eviction_plan`, `get_eviction_plan_for_gateway`. **No `unload_models` export.** |
| `eviction/event_waiter.py` | Unified event-driven eviction waiter for local and federated gateways; subscribes to `MODEL_UNLOADED` EventBus events. |
| `eviction/executor.py` | Eviction execution for federated gateways (HTTP unload + event confirmation); fail-closed when EventBus is absent. |
| `queue/request_queue/__init__.py` | Exposes the `RequestQueue` API (composition of runtime + maintenance). Submodule internals not staged this pass. |
| `selection/catalog.py` | Aggregates model availability from the local gateway and federation peers into a unified per-request catalog view. |
| `selection/stargate_collector.py` | Collects `Stargate` snapshots from federated gateways; converts to `Gateway` snapshots for `DecisionEngine`. |
| `selection/decision/types.py` | Immutable decision types: `FeasibilityTier`, `ConstraintFailure`, `ScoreComponents`, `EvictionPlanSummary`, `GatewayCandidate`, `DecisionTrace`. |
| `selection/decision/admission_verdict.py` | Verdict-classed VRAM admission (admit / transient / margin / structural-insufficient) using capped headroom and attainable ceiling. |
| `selection/decision/busy_view.py` | Tracker-over-telemetry busy/idle classification for eviction planning. |
| `selection/decision/feasibility.py` | Classifies gateways into T0/T1/T2 tiers; concurrency is explicitly out of tier scope. |
| `selection/decision/feasibility_gates.py` | Early T0/T1 short-circuit gates (circuit-breaker, health, catalog miss, already-loaded, loading-in-progress). |
| `selection/decision/feasibility_reclaim.py` | Distinguishes transient (reclaimable) vs. structural cannot-fit eviction failure. |
| `selection/decision/eviction_hysteresis.py` | Cooldown + demand-aware protection filters applied before victim selection. |
| `selection/decision/eviction_planning.py` | Computes an eviction plan to free VRAM/RAM headroom for a new model load. |
| `selection/decision/eviction_victim_select.py` | Greedy minimum idle-model eviction selection with hardware-freeable correction. |

*(Each row is grounded in `excerpts/<module>.md`; see per-module citations below. `queue/request_queue.py`, `selection/selector.py`, and the various `__init__.py`/`test_*.py` shims in the full 56-module scan are outside this pass's staged set.)*

---

## Key Classes

### `ModelRouter`
**Path:** `model_router.py` — cite: `excerpts/model_router.py.md`

Unified router for CPU and GPU models; central orchestration point for gateway selection based on gateway status, model placement requirements, routing policy, and resource capacity.

**Design invariants (from docstring):**
- Selection decisions always produce a trace for auditing/diagnosis.
- Routing respects both hard and soft affinity rules, honoring preferred placements where possible while falling back as policies allow.
- Eviction and model loading are orchestrated to minimize service disruption and to maintain capacity guarantees.
- Router is stateless on a per-request basis; gateway and model states are tracked externally.

**Notable methods (signatures as declared):**

| Method | Signature summary |
|---|---|
| `__init__` | `gateway_manager`, `gateway_configs=None` (unused, back-compat), `config=None`, `event_bus=None`, `federated_manager=None`, `local_stargate_id=None`, `compute_type_tracker=None` — raises `ValueError` if `federated_manager` set without `local_stargate_id`. |
| `set_load_waiter` | Inject load_waiter for event-driven eviction confirmation. |
| `configure_federation` | `(federated_manager, local_stargate_id) -> None` — does **not** reset router state (preserves `load_waiter`). |
| `set_forwarder` | Inject `FederatedRequestForwarder` for eviction execution (Master mode, post-federation-init). |
| `_collect_candidate_gateways` | `() -> list[Gateway]` — federated gateways as snapshots (post-unification: all gateways via federation). |
| `_refresh_stale_candidates` | `async (gateways) -> list[Gateway]` — waits for fresh telemetry if any gateway is stale. |
| `_select_and_record_stats` | `() -> (selected_gateway, trace)` — runs decision engine, records stats. |
| `_execute_local_eviction` | `async (selected, trace) -> bool` — delegates to the shared eviction executor. |
| `route_request` | `async () -> Gateway \| None` — `None` means "should queue"; caller forwards to `gateway.ref` (`FederatedGateway`). |

<!-- HUMAN: model_router.py's __init__ docstring documents event_bus / federated_manager / compute_type_tracker roles in detail (telemetry sync, decision tracing, Master-mode routing-key tracking); condensed above for length. Full text is in the staged excerpt if a longer treatment is wanted. -->

---

### Eviction confirmation types — `eviction/executor.py`, `eviction/event_waiter.py`
cite: `excerpts/eviction__executor.py.md`, `excerpts/eviction__event_waiter.py.md`

**`EvictionStatus`** (no docstring) and **`EvictionOutcome`** ("Typed result of `execute_eviction_plan`") together replace the previous boolean-success eviction contract.

**`EvictionInflightRegistry`** — "Executor-owned inflight eviction tasks keyed by (gateway, model routing key)."

**`execute_eviction_plan(forwarder, federated_gateway, eviction_plan, gateway_name, request_id, event_bus, inflight_registry=None)`** (async):

> INVARIANT: unload confirmation is required when EventBus + waiter are present. No EventBus means `UNCONFIRMED_NO_BUS` (fail closed — not success).

Declared flow per model: (1) register wait handle *before* HTTP, (2) send HTTP unload request, (3) wait for `MODEL_UNLOADED` from EventBus, (4) return `CONFIRMED` only when the event confirms resources freed. Returns `EvictionOutcome` with `.ok is True` **only** on `CONFIRMED` status.

**`UnloadResult`** ("Outcome of waiting for `MODEL_UNLOADED`") — `wait_for_registered` returns:
- `UNLOADED` if the event was received,
- **`SHUTDOWN`** if the waiter was stopped (`stop()` docstring: "wake pending waiters with SHUTDOWN (not UNLOADED)") — i.e. explicitly **not** treated as a confirmed unload,
- `TIMEOUT` on timeout (default 10s for force unload),
- `FAILED` if no wait was registered.

Net effect declared by the source: the eviction confirmation surface now has three non-`CONFIRMED`/non-`UNLOADED` outcomes (`UNCONFIRMED_NO_BUS`, `SHUTDOWN`, `TIMEOUT`/`FAILED`) that a caller must not conflate with success. This directly narrows arch-doc-review finding **W2** (2026-07-19, thread 5427) against the prior draft, which stated eviction wait "completes only when unload is confirmed" without the no-bus caveat — see Review carry-forward below.

---

### `RequestQueue`
**Path:** `queue/request_queue/__init__.py` — cite: `excerpts/queue__request_queue____init__.py.md`

"Concrete queue: runtime enqueue/process plus maintenance shutdown drain." Composed from `RequestQueueRuntime` + `RequestQueueMaintenance` + `QueuedRequest` (imported from `runtime`, `maintenance`, `types` respectively). **Method-level detail for `runtime.py`/`maintenance.py`/`types.py` is not staged this pass** — see `missing_coverage`.

---

### `selection/decision/types.py` classes
cite: `excerpts/selection__decision__types.py.md`

| Class | Declared role |
|---|---|
| `FeasibilityTier` | Gateway feasibility classification. `T0 < T1 < T2` in preference order (lower is worse). Concurrency slot exhaustion is explicitly **not** a feasibility tier. |
| `ConstraintFailure` | Single constraint that failed for a gateway. |
| `ScoreComponents` | Utility score breakdown, pre-weighting; `.total` computes the weighted sum. |
| `EvictionPlanSummary` | Summary of an eviction plan for a candidate. |
| `GatewayCandidate` | Gateway with feasibility evaluation and utility score, produced per-gateway by the decision engine. `.utility_score` is 0 if infeasible; `.is_feasible` is true with or without eviction. |
| `DecisionTrace` | Complete decision trace for observability (`to_log_dict`, `to_detailed_dict`, `to_event_payload(include_candidates=...)` for `ROUTING_DECISION` event emission). |

---

### `AdmissionVerdict` / `AdmissionEvaluation`
**Path:** `selection/decision/admission_verdict.py` — cite: `excerpts/selection__decision__admission_verdict.py.md`

"Verdict-classed VRAM admission for routing resource checks. Classifies admit vs. transient / margin / structural insufficient using capped headroom and attainable ceiling — shared by resource_checks and reclaim paths." Key function: `compute_capped_margin_mb(footprint_est_mb) -> clamp(pct×footprint, floor_mb, abs_cap_mb)`.

---

### `HysteresisResult`
**Path:** `selection/decision/eviction_hysteresis.py` — cite: `excerpts/selection__decision__eviction_hysteresis.py.md`

"Surviving evictable set plus metadata for `EvictionPlanSummary`." `filter_evictable_with_hysteresis` "Applies cooldown + demand filters and class-gated escape hatches. Returns `None` when hysteresis leaves no legal victim (plan abort)."

---

### `StargateCollisionError`
**Path:** `selection/stargate_collector.py` — cite: `excerpts/selection__stargate_collector.py.md`

Raised by `validate_stargate_pool` when duplicate `stargate_id` values are detected ("fail-fast").

---

## Dedicated section: `busy_view.py` — the tracker-over-telemetry busy/idle matrix (bind #4)

**Path:** `selection/decision/busy_view.py` — cite: `excerpts/selection__decision__busy_view.py.md`

> "Tracker-over-telemetry busy/idle classification for eviction planning."

| Function | Declared role |
|---|---|
| `actually_busy_models(gateway, routing_key_tracker, gw_keys_in_flight)` | Loaded models with verified in-flight requests. |
| `idle_models(gateway, routing_key_tracker, gw_keys_in_flight)` | Loaded models that are idle and not currently loading. |
| `_is_model_actually_busy(gateway, model_id, routing_key_tracker, gw_keys_in_flight)` | INVARIANT: `tracker_in_flight(model_id, gateway) ⟹ busy(model_id)`. |

Declared decision matrix (docstring, verbatim intent):

```
tracker has keys   -> busy (regardless of telemetry)
no tracker         -> telemetry alone decides
tracker, no keys   -> idle (telemetry "busy" treated as stale)
```

The docstring explains the ordering rationale: the routing-key tracker is the master's authoritative record of dispatched-but-not-completed requests; edge telemetry (`busy_models`) is a best-effort hint that "can be momentarily stale and MUST NOT override a positive tracker signal." `eviction_planning.py`'s `_compute_eviction_plan` imports `actually_busy_models` and `idle_models` from this module directly — this module, not `eviction/planner.py`, is the declared source of truth for busy/idle classification feeding eviction.

---

## Removed since prior draft: `eviction/planner.py` (bind #3)

The prior provisional draft (`source/routing.md`, 2026-07-19) listed `eviction/planner.py` with `unload_models(load_waiter, gateway_instance, models_to_evict) -> bool` and `get_idle_models(gw) -> list[str]`, and stated `eviction/__init__.py` re-exports `unload_models`.

**This pass's staged `eviction/__init__.py` excerpt shows no `planner` import and no `unload_models` export** — its only imports are `from event_waiter import EvictionWaiter, UnloadResult` and `from executor import EvictionInflightRegistry, EvictionOutcome, EvictionStatus, execute_eviction_plan, get_eviction_plan_for_gateway`. `eviction/planner.py` is also absent from the current staged corpus and from the current 56-module rescan in `doc-scan-summary.txt`. Per the staging manifest: "Do NOT trust prior routing.md claims that eviction/__init__ exports unload_models." Idle/busy classification is now owned by `selection/decision/busy_view.py` (see above), and eviction execution confirmation is owned by `eviction/executor.py` + `eviction/event_waiter.py`.

<!-- HUMAN: staged evidence confirms planner.py's absence from this corpus and from the current rescan; it does not itself constitute a git-log confirmation of deletion (that would be a workspaces:// / repo check, out of scope for this pass). Treat "deleted" as source-declared-absent, consistent with the Post-P0 bind list this draft was asked to reflect. -->

---

## System-Level Invariants (bind #5)

From `__init__.py` (cite: `excerpts/__init__.py.md`) — **corrected wording vs. the prior draft**:

```
∀ gateway: (model_loaded ∧ resources_fit) ⟹ tier = T1
∀ gateway: concurrency capacity is CapacityPool admission, independent of
           feasibility tier (loaded-but-busy stays T1; ¬ T0 for slot exhaustion)
∀ eviction: models_to_evict ⊆ idle_models
```

The prior draft's `__init__.py`-sourced invariant block read `∀ gateway: (model_loaded ∧ ¬has_capacity) ⟹ tier = T0`, which arch-doc review 5427 (finding **W1**) flagged as false under `feasibility.py`'s tier semantics: a loaded model is in-catalog and resident, so it is never demoted to T0 for lack of concurrency; concurrency is gated separately by `CapacityPool` admission. The now-staged `__init__.py` invariant text has been corrected at the source to state this explicitly ("concurrency capacity is CapacityPool admission, independent of feasibility tier"), consistent with W1's suggested fix ("Fix at source — correct the invariant in `__init__.py` docstring (regen flows to doc)"). `selection/decision/feasibility.py`'s own docstring is consistent with this: "Concurrency slots are CapacityPool admission — not encoded in these tiers," and its declared tier classification is T0 = unhealthy / not-in-catalog / cannot-fit; T1 = already loaded OR fits with free resources; T2 = fits after eviction of idle models.

From `eviction/executor.py` (cite: `excerpts/eviction__executor.py.md`) — see "Eviction confirmation types" above for the full `UNCONFIRMED_NO_BUS` invariant, which narrows the prior draft's `∀ unload: (HTTP ok ∧ MODEL_UNLOADED event) ∨ abort` line (that line did not distinguish a no-bus non-confirmation from an aborted plan; the current source states the no-bus case explicitly as a typed, fail-closed non-success outcome rather than folding it into "abort").

---

## Key Functions (staged this pass)

### Feasibility and admission

| Function | Module | Signature summary |
|---|---|---|
| `evaluate_feasibility` | `feasibility.py` | `(gateway, placement, policy, requirements_lookup, sticky, routing_key_tracker, is_gateway_available_fn, eviction_cooldown_s, has_demand, eviction_request_class) -> (FeasibilityTier, tuple[ConstraintFailure,...], EvictionPlanSummary \| None)`. Invariants: `tier==T0 ⟹ len(constraint_failures)>0`; `tier==T2 ⟹ eviction_plan is not None`; `¬sticky ⟹ T2_FEASIBLE_EVICT valid even when model loaded elsewhere`. |
| `early_feasibility_gates` | `feasibility_gates.py` | "Run checks 0–3.5. Return a terminal result, or `None` to continue." `sticky` accepted for call-site parity/logging only; gates do not branch on it. |
| `can_fit_after_eviction_including_busy` | `feasibility_reclaim.py` | Distinguishes transient (reclaimable after eviction) from structural (permanent) insufficient-capacity failure. |
| `evaluate_vram_admission` | `admission_verdict.py` | Classify admit/insufficient using capped headroom and attainable ceiling. |
| `compute_capped_margin_mb` | `admission_verdict.py` | `(footprint_est_mb) -> int` headroom margin. |

### Eviction

| Function | Module | Signature summary |
|---|---|---|
| `execute_eviction_plan` | `executor.py` | `async (forwarder, federated_gateway, eviction_plan, gateway_name, request_id, event_bus, inflight_registry=None) -> EvictionOutcome` (see Eviction confirmation types). |
| `get_eviction_plan_for_gateway` | `executor.py` | `(trace, gateway_name) -> EvictionPlanSummary \| None` — "eviction_plan is on `GatewayCandidate`, not `DecisionTrace`." |
| `_compute_eviction_plan` | `eviction_planning.py` | Computes eviction plan freeing enough VRAM/RAM "with full runtime headroom margins," sized so the post-eviction resource check passes without a wasted eviction cycle. |
| `filter_evictable_with_hysteresis` | `eviction_hysteresis.py` | See `HysteresisResult` above. |
| `select_eviction_victims` | `eviction_victim_select.py` | Greedy minimum idle-model set to free placement footprints; corrects catalog freeable estimates against hardware reserves when present. |
| `compute_non_evictable_vram_reserve_mb` | `eviction_victim_select.py` | Conservative VRAM reserve eviction must not assume freeable. |
| `actually_busy_models`, `idle_models` | `busy_view.py` | See dedicated section above. |

### Catalog and collection

| Function | Module | Signature summary |
|---|---|---|
| `get_local_model_ids` | `catalog.py` | `(gateway_manager) -> set[str]` — model IDs from local gateway; "single source of truth for local model access." |
| `is_model_in_any_catalog` | `catalog.py` | `(model_id_str, gateway_manager, federated_manager) -> bool`, `ModelId`-aware; checks the full `model_resources` grid, not only activation-filtered lists. |
| `collect_stargate_model_sets` | `catalog.py` | `(gateway_manager, federated_manager) -> list[set[str]]`, one set per reachable Stargate. Invariant: `∀ model_set: model_set ⊆ stargate.available_models`. |
| `get_all_available_models` | `catalog.py` | Union of all loadable models across the Stargate pool (full catalog for routing, not activation-filtered). |
| `get_activated_models_for_display` | `catalog.py` | Union of activated models for the public `/v1/models` endpoint; falls back to all-available if no activation data. Invariant: `∀ model ∈ result: model ∈ activated_models ∨ ¬∃ activation_rules`. |
| `get_model_context_metadata` | `catalog.py` | Per-model `context_length` / `effective_context_per_slot`, sourced from already-populated telemetry (no extra I/O). |
| `get_model_dispatch_metadata` | `catalog.py` | Per-model `dispatch` wire facet; federated-only this build (local rows carry no dispatch facet yet — "tracked local-parity follow-up"). |
| `get_model_source_map` | `catalog.py` | `model_id -> [stargate_ids]`. |
| `get_model_status_map` | `catalog.py` | `model_id -> {loaded_on, busy_on, loading_on}` per gateway. |
| `stargate_to_gateway` | `stargate_collector.py` | Converts a `Stargate` snapshot to a `Gateway` snapshot; `gateway.name = stargate.ref.gateway_id`. |
| `federated_gateways_to_routing_candidates` | `stargate_collector.py` | Router-only Master mode: `FederatedGateway` instances directly to `Gateway` snapshots (no intermediate `Stargate`). |
| `collect_stargates` | `stargate_collector.py` | All reachable Stargates from federation; raises `StargateCollisionError` on duplicate `stargate_id`. |
| `validate_stargate_pool` | `stargate_collector.py` | Fail-fast duplicate-`stargate_id` check. |

*(`build_placement`, `collect_gateways`, `is_gateway_dispatchable` are referenced by name in staged import lists — `model_router.py` imports `build_placement` from `selection.collector`; `stargate_collector.py` imports `is_gateway_dispatchable` from `collector` — but `selection/collector.py` itself is **not staged this pass**; their signatures/docstrings are carried forward from the prior draft **unverified** this round. See arch-doc-review 5427 finding **S3**, which already flagged this file as unstaged and its `build_placement` divergence-row as a probable parse artifact.)*

---

## Imports and Dependencies (staged modules only)

### External packages referenced
`asyncio`, `uuid`, `enum` (`Enum`, `StrEnum`), `dataclasses` (`dataclass`), `typing` (`TYPE_CHECKING`, `Any`), `__future__` (`annotations`).

### Internal packages referenced
`model_id` (`ModelId`, `validate_model_id`), `universal_logging` (`get_logger`).

### Cross-module dependencies within the staged subset

- `model_router.py` → `selection.collector.build_placement` (unstaged), `selection.decision.{DecisionEngine, FeasibilityTier, load_routing_policy}` (engine/config unstaged)
- `eviction/__init__.py` → `event_waiter.{EvictionWaiter, UnloadResult}`, `executor.{EvictionInflightRegistry, EvictionOutcome, EvictionStatus, execute_eviction_plan, get_eviction_plan_for_gateway}`
- `selection/decision/feasibility.py` → `admission_verdict`, `eviction_cooldown_policy` (unstaged), `eviction_planning`, `feasibility_gates`, `feasibility_reclaim`, `resource_checks` (unstaged), `types`
- `selection/decision/eviction_planning.py` → `busy_view.{actually_busy_models, idle_models}`, `eviction_cooldown_policy` (unstaged), `eviction_hysteresis`, `eviction_victim_select`, `resource_checks` (unstaged), `types`
- `selection/decision/eviction_hysteresis.py` → `eviction_cooldown_policy` (unstaged)
- `selection/decision/eviction_victim_select.py` → `eviction_hysteresis`, `types`
- `selection/decision/feasibility_reclaim.py` → `admission_verdict`, `resource_checks` (unstaged), `types`
- `selection/decision/feasibility_gates.py` → `model_checks` (unstaged), `types`
- `selection/stargate_collector.py` → `collector.is_gateway_dispatchable` (unstaged), `types.Stargate`
- `queue/request_queue/__init__.py` → `maintenance`, `runtime`, `types` (all unstaged this pass)

---

<!-- AUTHORED -->
## Synthesis: eviction confirmation path (grounded in staged modules only)

For a T2 (evict-then-load) placement, the declared eviction confirmation path is:

1. `selection/decision/eviction_planning.py` computes a plan via `_compute_eviction_plan`, drawing idle/busy classification from `busy_view.idle_models` / `busy_view.actually_busy_models` (tracker-authoritative, telemetry-secondary), then filtering through `eviction_hysteresis.filter_evictable_with_hysteresis` (cooldown + demand escape hatches) and `eviction_victim_select.select_eviction_victims` (greedy minimum idle-model set, hardware-reserve-corrected).
2. `eviction/executor.execute_eviction_plan` executes the plan per model: register a wait handle on `EvictionWaiter` *before* the HTTP unload request (race prevention per `event_waiter.py`'s docstring), send the HTTP request, then wait for the `MODEL_UNLOADED` EventBus event.
3. Confirmation is typed, not boolean: `EvictionOutcome.ok is True` only for `CONFIRMED`. Absence of an EventBus yields `UNCONFIRMED_NO_BUS` (fail-closed); a stopped waiter yields `SHUTDOWN`, distinct from `UNLOADED`.

This synthesis intentionally stops at the executor/planner boundary. The prior draft's broader end-to-end flow (covering `CapacityPool.acquire`/`acquire_token` admission, `ModelRouter.route_request` orchestration end-to-end, and federation topology/telemetry propagation) drew on `capacity/_pool/_acquisition.py`, `selection/decision/engine/core.py`, and other modules **not staged this pass** — repeating those claims here would not be re-verified against current source. See `unsupported_claims`.

<!-- GENERATED:END -->
