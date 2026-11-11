# Routing System Architecture — Post-P0 Targeted Refresh (Step 9)

<!-- GENERATED:START generated=2026-11-11 method=targeted-refresh inventory_sha=be33081258a3 source_commit=2b6bc981f316 refreshed_by=agent-bus:23032 coverage=57/57 -->
_Generated from module docstrings at HEAD; claims reflect what the source **declares**, not verified runtime behavior._

## Scope of this pass

**Targeted refresh** (Kaywan 2026-11-10) of `docs/architecture/routing.md` against `services/universal-stargate/systems/routing` at source commit `0d45bbef52c8`. This pass maintains a **full module inventory (57/57)** and prose for admission/eviction work landed after the prior `generated=2026-07-20` stamp. **Key Functions**, **Internal import statements**, and **Synthesis** below cover the full production routing tree at HEAD (all production modules under this tree).

**Package path:** `services/universal-stargate/systems/routing` (proxy admission paths that delegate here are covered in `stargate.md` / `systems/proxy/`).
**Module count (HEAD):** 57 production `.py` modules (excludes `test_*.py` under this tree).

---

## Material changes since 2026-07-20 (admission / eviction / RAG)

Commits on the admission and eviction path after the July stamp include `953f0fa06` (verdict-classed VRAM admission), `71b7cb362` (RAM reclaim admission and eviction abort semantics), `d43f2df5a` (full-evict VRAM reclaim when catalog understates hardware usage), and `4c3e03866` (RAG protected models evict-last).

**`selection/decision/admission_verdict.py`** — Centralizes verdict-classed VRAM and RAM admission: `AdmissionVerdict` (`admit`, `insufficient_transient`, `insufficient_margin`, `insufficient_structural`), structured `AdmissionEvaluation` / `RamAdmissionEvaluation` / `VramReclaimEvaluation` payloads for traces, capped headroom via `compute_capped_margin_mb`, non-evictable reserves (`compute_non_evictable_vram_reserve_mb`, `compute_non_evictable_ram_reserve_mb`), and `evaluate_vram_admission`, `evaluate_vram_reclaim`, and `evaluate_ram_reclaim` (hardware ceiling on full idle eviction when catalog freeable MB is low).

**`selection/decision/feasibility_reclaim.py`** — `can_fit_after_eviction_including_busy` calls the admission helpers so transient vs structural cannot-fit is distinguished when loaded **and** busy models must be evicted to admit a placement; loading-model reservations are subtracted before reclaim math (mirrors `resource_checks` / `eviction_planning`).

**`selection/decision/eviction_victim_select.py`** — Victim selection uses `evaluate_vram_reclaim` / `evaluate_ram_reclaim` for hardware-corrected freeable estimates; integrates RAG evict-last ordering via `rag_protected_models` (protected models remain reclaimable but sort last).

**`rag_protected_models.py`** — Defines the per-node protected set from `pipelines/rag/models.yaml` and `pipelines/rag_contextualize/models.yaml` (embedding + contextualize routing keys). Policy is **evict-last**, not never-evict; busy/in-flight models are still never victims (`eviction_hysteresis.py` imports `is_rag_protected_model` for ordering/filter metadata).

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

## Module Inventory (57/57)

| Module | Description |
|---|---|
| `__init__.py` | Routing system package root: feasibility tiers, gateway scoring, eviction orchestration; re-exports decision-engine surface. |
| `aggregate_model_availability.py` | Emits aggregate model-availability union deltas for Stargate routing telemetry. |
| `capacity/__init__.py` | Per-model admission control API for Master Stargate concurrency slots. |
| `capacity/pool.py` | Shim preserving `systems.routing.capacity.pool` imports into `_pool`. |
| `capacity/_pool/__init__.py` | Capacity pool package public re-exports. |
| `capacity/_pool/_acquisition.py` | `CapacityPool` mixin: acquire/wait loop and pause gating. |
| `capacity/_pool/_bookkeeping.py` | Slot maps, availability queries, reconcile helpers. |
| `capacity/_pool/_cancellation.py` | Cancelled waiters and leaked-slot recovery. |
| `capacity/_pool/_diagnostics.py` | Read-only pool/admission diagnostics. |
| `capacity/_pool/_events_admission.py` | EventBus admission lifecycle signals (pause/resume/leak). |
| `capacity/_pool/_events_queue.py` | EventBus queue lifecycle telemetry. |
| `capacity/_pool/_pause.py` | Admission pause/resume for starvation relief. |
| `capacity/_pool/_pool_class.py` | Event-driven `CapacityPool` with self-releasing tokens. |
| `capacity/_pool/_release_dispatch.py` | Token release and FIFO waiter dispatch. |
| `capacity/_pool/_types.py` | `CapacityToken`, `QueueFullError`, shared pool types. |
| `eviction/__init__.py` | Typed federated eviction exports (`EvictionOutcome`, `execute_eviction_plan`, …). |
| `eviction/event_waiter.py` | `MODEL_UNLOADED` waiter; `UnloadResult.SHUTDOWN` distinct from confirmed unload. |
| `eviction/executor.py` | HTTP unload + EventBus confirmation; `UNCONFIRMED_NO_BUS` fail-closed. |
| `model_router.py` | Unified predicate–score router for selection, eviction, and federation. |
| `queue/__init__.py` | Request queue with verification and backpressure. |
| `queue/request_queue/__init__.py` | `RequestQueue` API export. |
| `queue/request_queue/maintenance.py` | Shutdown drain and in-flight cancel. |
| `queue/request_queue/runtime.py` | Enqueue/dequeue/process loop and metrics. |
| `queue/request_queue/types.py` | `QueuedRequest` and queue item types. |
| `queue/verification.py` | Post-selection resource verification against footprints. |
| `rag_protected_models.py` | RAG workload IDs with **evict-last** (not never-evict) policy. |
| `selection/__init__.py` | Gateway selection package; DecisionEngine entry. |
| `selection/catalog.py` | Federated catalog union for routing decisions. |
| `selection/collector.py` | Live `GatewayInstance` → `Gateway` snapshot bridge. |
| `selection/selector.py` | Legacy stub; logic moved to `decision/`. |
| `selection/stargate_collector.py` | `Stargate` snapshot collection and collision checks. |
| `selection/types.py` | `Placement`, `Gateway`, `ModelDetails` dataclasses. |
| `selection/decision/__init__.py` | Decision engine package exports. |
| `selection/decision/admission_verdict.py` | Verdict-classed VRAM/RAM admission and reclaim evaluation. |
| `selection/decision/busy_view.py` | Tracker-over-telemetry busy/idle matrix for eviction. |
| `selection/decision/config.py` | Affinity/routing policy loaded from `stargate_config.yaml`. |
| `selection/decision/engine/__init__.py` | Engine package-shadow exports. |
| `selection/decision/engine/capacity_constraints.py` | Sticky routing temporary-capacity classification. |
| `selection/decision/engine/core.py` | `DecisionEngine` orchestration (feasibility → score → select). |
| `selection/decision/engine/selection_rules.py` | Deterministic best-candidate selection rules. |
| `selection/decision/engine/trace_emission.py` | `ROUTING_DECISION` trace EventBus helpers. |
| `selection/decision/eviction_cooldown_policy.py` | REQUIRED vs OPPORTUNISTIC cooldown classes. |
| `selection/decision/eviction_hysteresis.py` | Cooldown/demand filters before victim select. |
| `selection/decision/eviction_planning.py` | `_compute_eviction_plan` VRAM/RAM headroom planning. |
| `selection/decision/eviction_victim_select.py` | Greedy victims + hardware freeable correction. |
| `selection/decision/feasibility.py` | T0/T1/T2 tier evaluation pipeline. |
| `selection/decision/feasibility_gates.py` | Early T0/T1 short-circuit gates (incl. cloud T1). |
| `selection/decision/feasibility_reclaim.py` | Transient vs structural cannot-fit after busy eviction. |
| `selection/decision/model_checks.py` | Catalog/resident model checks for gates. |
| `selection/decision/protocols.py` | Duck-typed tracker protocols. |
| `selection/decision/requirements.py` | VRAM/RAM requirements lookup cache. |
| `selection/decision/resource_checks.py` | Footprints, loading reservations, admission calls. |
| `selection/decision/scorer.py` | Weighted utility scoring components. |
| `selection/decision/stability.py` | `StickyPlacementTracker` hysteresis bindings. |
| `selection/decision/types.py` | `GatewayCandidate`, `DecisionTrace`, feasibility types. |
| `telemetry/__init__.py` | Routing telemetry package. |
| `telemetry/freshness_waiter.py` | Epoch-based `GATEWAY_RESOURCE_UPDATE` freshness waiter. |

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

"Concrete queue: runtime enqueue/process plus maintenance shutdown drain." Composed from `RequestQueueRuntime` + `RequestQueueMaintenance` + `QueuedRequest` (imported from `runtime`, `maintenance`, `types` respectively). See **Module Inventory** for submodule one-liners.

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

## Key Functions (full routing tree)

_All production modules under `systems/routing` at HEAD; signatures from tree-sitter extraction._

#### `services/universal-stargate/systems/routing/aggregate_model_availability.py`

| Symbol | Signature summary |
|---|---|
| `AggregateModelAvailabilityEmitter.__init__` | `def __init__(self, event_bus: _EventBusPublish \| None) -> None` |
| `AggregateModelAvailabilityEmitter.reconcile` | `async def reconcile( self, gateway_manager: SingleGatewayManager \| None, federated_manager: FederatedGatewayManager …` |
| `AggregateModelAvailabilityEmitter.reconcile_from_proxy` | `async def reconcile_from_proxy(self, proxy: StargateProxy) -> None` |
| `AggregateModelAvailabilityEmitter.register_watch` | `def register_watch(self, model_ids: list[str]) -> dict[str, bool]` |
| `AggregateModelAvailabilityEmitter.snapshot` | `def snapshot(self, model_ids: list[str]) -> dict[str, bool]` |
| `_EventBusPublish.publish_nowait` | `async def publish_nowait(self, event: Any) -> None` |

#### `services/universal-stargate/systems/routing/capacity/_pool/_acquisition.py`

| Symbol | Signature summary |
|---|---|
| `_AcquisitionMixin._try_immediate` | `def _try_immediate( self, request_id: str, model_id: str, allowed_gateway_ids: frozenset[str], ) -> str \| None` |
| `_AcquisitionMixin._wait_for_slot` | `async def _wait_for_slot( self, request_id: str, model_id: str, allowed_gateway_ids: frozenset[str], *, wait_deadline…` |
| `_AcquisitionMixin.acquire_token` | `async def acquire_token( self, request_id: str, model_id: str, allowed_gateway_ids: frozenset[str], timeout_s: float …` |

#### `services/universal-stargate/systems/routing/capacity/_pool/_bookkeeping.py`

| Symbol | Signature summary |
|---|---|
| `_BookkeepingMixin.available` | `def available(self, gateway_id: str, model_id: str) -> int` |
| `_BookkeepingMixin.get_available_gateways` | `def get_available_gateways(self, model_id: str) -> list[tuple[str, int]]` |
| `_BookkeepingMixin.get_slot_info` | `def get_slot_info(self, gateway_id: str, model_id: str) -> tuple[int, int, int]` |
| `_BookkeepingMixin.reconcile_gateway_state` | `async def reconcile_gateway_state( self, gateway_id: str, idle_model_ids: set[str], ) -> None` |
| `_BookkeepingMixin.remove_gateway` | `def remove_gateway(self, gateway_id: str) -> None` |
| `_BookkeepingMixin.remove_model` | `def remove_model(self, gateway_id: str, model_id: str) -> None` |
| `_BookkeepingMixin.reset_cold_slot` | `def reset_cold_slot( self, gateway_id: str, model_id: str, placeholder_capacity: int ) -> None` |
| `_BookkeepingMixin.set_capacity` | `def set_capacity( self, gateway_id: str, model_id: str, max_concurrent: int, ) -> None` |
| `_BookkeepingMixin.slot_quiet_elapsed` | `def slot_quiet_elapsed( self, gateway_id: str, model_id: str, *, quiet_s: float \| None = None ) -> bool` |

#### `services/universal-stargate/systems/routing/capacity/_pool/_cancellation.py`

| Symbol | Signature summary |
|---|---|
| `_CancellationMixin._recover_leaked_slot` | `def _recover_leaked_slot( self, request_id: str, gateway_id: str, model_id: str ) -> None` |
| `_CancellationMixin._remove_waiter` | `def _remove_waiter( self, request_id: str, *, reason: str, wake_with_exception: bool, ) -> bool` |
| `_CancellationMixin.cancel_request` | `def cancel_request(self, request_id: str, reason: str = "explicit_cancel") -> bool` |

#### `services/universal-stargate/systems/routing/capacity/_pool/_diagnostics.py`

| Symbol | Signature summary |
|---|---|
| `_DiagnosticsMixin.get_admission_state` | `def get_admission_state(self, model_id: str) -> dict[str, object]` |
| `_DiagnosticsMixin.get_model_slots` | `def get_model_slots(self, model_id: str) -> list[dict[str, object]]` |
| `_DiagnosticsMixin.get_snapshot` | `def get_snapshot(self) -> dict[str, Any]` |

#### `services/universal-stargate/systems/routing/capacity/_pool/_events_admission.py`

| Symbol | Signature summary |
|---|---|
| `_EventsAdmissionMixin._emit_admission_paused` | `def _emit_admission_paused( self, model_id: str, duration_s: float, reason: str ) -> None` |
| `_EventsAdmissionMixin._emit_admission_resumed` | `def _emit_admission_resumed(self, model_id: str, reason: str) -> None` |
| `_EventsAdmissionMixin._emit_slot_leak_recovered` | `def _emit_slot_leak_recovered( self, request_id: str, gateway_id: str, model_id: str ) -> None` |

#### `services/universal-stargate/systems/routing/capacity/_pool/_events_queue.py`

| Symbol | Signature summary |
|---|---|
| `_EventsQueueMixin._emit_queue_admitted` | `def _emit_queue_admitted( self, request_id: str, model_id: str, gateway_id: str, wait_ms: float, ) -> None` |
| `_EventsQueueMixin._emit_queue_cancelled` | `def _emit_queue_cancelled( self, request_id: str, model_id: str, wait_ms: float, reason: str, ) -> None` |
| `_EventsQueueMixin._emit_queue_entered` | `def _emit_queue_entered( self, request_id: str, model_id: str, queue_position: int, allowed_gateways: int, ) -> None` |
| `_EventsQueueMixin._emit_queue_full` | `def _emit_queue_full( self, request_id: str, model_id: str, current_depth: int ) -> None` |
| `_EventsQueueMixin._emit_queue_waiting` | `def _emit_queue_waiting( self, request_id: str, model_id: str, wait_ms: float, ) -> None` |

#### `services/universal-stargate/systems/routing/capacity/_pool/_pause.py`

| Symbol | Signature summary |
|---|---|
| `_PauseMixin._clear_pause` | `def _clear_pause(self, model_id: str, *, reason: str) -> None` |
| `_PauseMixin._resume_after` | `async def _resume_after(self, model_id: str, duration_s: float) -> None` |
| `_PauseMixin.is_paused` | `def is_paused(self, model_id: str) -> bool` |
| `_PauseMixin.pause_admission` | `def pause_admission( self, model_id: str, duration_s: float, reason: str = "starvation_relief", ) -> float` |
| `_PauseMixin.resume_admission` | `def resume_admission( self, model_id: str, reason: str = "explicit", ) -> bool` |

#### `services/universal-stargate/systems/routing/capacity/_pool/_pool_class.py`

| Symbol | Signature summary |
|---|---|
| `CapacityPool.__init__` | `def __init__( self, event_bus: _EventBusLike \| None = None, max_queue_depth: int = 0, ) -> None` |
| `CapacityPool._ensure_subscribed` | `def _ensure_subscribed(self) -> None` |

#### `services/universal-stargate/systems/routing/capacity/_pool/_release_dispatch.py`

| Symbol | Signature summary |
|---|---|
| `_ReleaseDispatchMixin._dispatch` | `async def _dispatch(self, model_id: str) -> None` |
| `_ReleaseDispatchMixin._release` | `async def _release(self, token: CapacityToken) -> None` |

#### `services/universal-stargate/systems/routing/capacity/_pool/_types.py`

| Symbol | Signature summary |
|---|---|
| `CapacityAcquireTimeoutError.__init__` | `def __init__(self, model_id: str, timeout_s: float) -> None` |
| `QueueFullError.__init__` | `def __init__(self, model_id: str, current_depth: int, max_depth: int) -> None` |
| `_EventBusLike.publish_nowait` | `async def publish_nowait(self, event: Any) -> None` |
| `_EventBusLike.subscribe_async` | `def subscribe_async(self, signal: str, callback: Any) -> None` |
| `_WaiterCancelledError.__init__` | `def __init__(self, reason: str) -> None` |

#### `services/universal-stargate/systems/routing/eviction/event_waiter.py`

| Symbol | Signature summary |
|---|---|
| `EvictionWaiter.__init__` | `def __init__(self, event_bus: EventBus)` |
| `EvictionWaiter._on_model_unloaded` | `async def _on_model_unloaded(self, event) -> None` |
| `EvictionWaiter.register_wait` | `def register_wait(self, gateway_name: str, model_id: ModelId) -> None` |
| `EvictionWaiter.start` | `async def start(self) -> None` |
| `EvictionWaiter.stop` | `def stop(self) -> None` |
| `EvictionWaiter.wait_for_registered` | `async def wait_for_registered( self, gateway_name: str, model_id: ModelId, timeout: float = 10.0, ) -> UnloadResult` |

#### `services/universal-stargate/systems/routing/eviction/executor.py`

| Symbol | Signature summary |
|---|---|
| `EvictionInflightRegistry.__init__` | `def __init__(self) -> None` |
| `EvictionInflightRegistry.get_inflight` | `def get_inflight( self, gateway_name: str, model_key: str ) -> asyncio.Task[EvictionOutcome] \| None` |
| `EvictionInflightRegistry.register` | `def register( self, gateway_name: str, model_key: str, task: asyncio.Task[EvictionOutcome], ) -> None` |
| `EvictionInflightRegistry.release` | `def release( self, gateway_name: str, model_key: str, task: asyncio.Task[EvictionOutcome], ) -> None` |
| `_evict_single_model` | `async def _evict_single_model( *, forwarder: FederatedRequestForwarder, federated_gateway: FederatedGateway, model_id…` |
| `_model_routing_key` | `def _model_routing_key(model_id: ModelId) -> str` |
| `execute_eviction_plan` | `async def execute_eviction_plan( forwarder: FederatedRequestForwarder, federated_gateway: FederatedGateway, eviction_…` |
| `get_eviction_plan_for_gateway` | `def get_eviction_plan_for_gateway( trace: DecisionTrace, gateway_name: str, ) -> EvictionPlanSummary \| None` |

#### `services/universal-stargate/systems/routing/model_router.py`

| Symbol | Signature summary |
|---|---|
| `ModelRouter.__init__` | `def __init__( self, gateway_manager: Any, gateway_configs: dict \| None = None, config: dict \| None = None, event_bu…` |
| `ModelRouter._collect_candidate_gateways` | `def _collect_candidate_gateways(self) -> list[Gateway]` |
| `ModelRouter._execute_local_eviction` | `async def _execute_local_eviction( self, selected: Gateway, trace: DecisionTrace ) -> bool` |
| `ModelRouter._extract_model_id` | `def _extract_model_id(self, request: dict \| Any) -> str \| None` |
| `ModelRouter._log_routing_candidates` | `def _log_routing_candidates(self, gateways: list[Gateway]) -> None` |
| `ModelRouter._refresh_stale_candidates` | `async def _refresh_stale_candidates(self, gateways: list[Gateway]) -> list[Gateway]` |
| `ModelRouter._select_and_record_stats` | `def _select_and_record_stats( self, gateways: list[Gateway], placement: Placement, request_id: str \| None, ) -> tupl…` |
| `ModelRouter._validate_and_parse_model` | `def _validate_and_parse_model(self, model_id: str) -> tuple[ModelId, str] \| None` |
| `ModelRouter.configure_federation` | `def configure_federation( self, federated_manager: Any, local_stargate_id: str, ) -> None` |
| `ModelRouter.route_request` | `async def route_request(self, request: dict \| Any) -> Gateway \| None` |
| `ModelRouter.set_forwarder` | `def set_forwarder(self, forwarder: FederatedRequestForwarder) -> None` |
| `ModelRouter.set_load_waiter` | `def set_load_waiter(self, load_waiter) -> None` |

#### `services/universal-stargate/systems/routing/queue/request_queue/maintenance.py`

| Symbol | Signature summary |
|---|---|
| `RequestQueueMaintenance.shutdown` | `async def shutdown(self) -> None` |

#### `services/universal-stargate/systems/routing/queue/request_queue/runtime.py`

| Symbol | Signature summary |
|---|---|
| `RequestQueueRuntime.__init__` | `def __init__( self, max_size: int = 100, max_concurrent_processing: int = 100, # High by default; worker has final sa…` |
| `RequestQueueRuntime._safe_requeue` | `async def _safe_requeue(self, request: QueuedRequest) -> None` |
| `RequestQueueRuntime.complete_request` | `async def complete_request(self, request_id: str, success: bool = True) -> None` |
| `RequestQueueRuntime.dequeue` | `async def dequeue(self) -> QueuedRequest \| None` |
| `RequestQueueRuntime.enqueue` | `async def enqueue( self, request: dict[str, Any], timeout: float \| None = None ) -> asyncio.Future` |
| `RequestQueueRuntime.get_queue_stats` | `def get_queue_stats(self) -> dict[str, Any]` |
| `RequestQueueRuntime.process_queue` | `async def process_queue(self, router, max_concurrent: int \| None = None) -> None` |

#### `services/universal-stargate/systems/routing/queue/verification.py`

| Symbol | Signature summary |
|---|---|
| `ResourceVerifier.__init__` | `def __init__( self, router, logger, event_bus=None, )` |
| `ResourceVerifier._fetch_configuration` | `async def _fetch_configuration(self, model_id: str)` |
| `ResourceVerifier._on_model_loaded` | `async def _on_model_loaded(self, event) -> None` |
| `ResourceVerifier._on_model_unloaded` | `async def _on_model_unloaded(self, event) -> None` |
| `ResourceVerifier._on_resource_update` | `async def _on_resource_update(self, event) -> None` |
| `ResourceVerifier._setup_event_subscriptions` | `def _setup_event_subscriptions(self)` |

#### `services/universal-stargate/systems/routing/rag_protected_models.py`

| Symbol | Signature summary |
|---|---|
| `_contextualize_models_path` | `def _contextualize_models_path() -> Path` |
| `_rag_models_path` | `def _rag_models_path() -> Path` |
| `_read_models_yaml` | `def _read_models_yaml(path: Path) -> dict[str, Any]` |
| `_repo_root` | `def _repo_root() -> Path` |
| `clear_rag_protected_cache` | `def clear_rag_protected_cache() -> None` |
| `is_rag_protected_model` | `def is_rag_protected_model(model_id: ModelId) -> bool` |

#### `services/universal-stargate/systems/routing/selection/catalog.py`

| Symbol | Signature summary |
|---|---|
| `_federated_gateway_catalog_model_ids` | `def _federated_gateway_catalog_model_ids(gateway: object) -> frozenset` |
| `_get_federated_activated_models` | `def _get_federated_activated_models( federated_manager: FederatedGatewayManager, ) -> list[set[str]]` |
| `_get_local_activated_models` | `def _get_local_activated_models( gateway_manager: SingleGatewayManager \| None, ) -> set[str] \| None` |
| `_local_gateway_catalog_id_strings` | `def _local_gateway_catalog_id_strings(client: object) -> frozenset[str]` |
| `collect_stargate_model_sets` | `def collect_stargate_model_sets( gateway_manager: SingleGatewayManager \| None, federated_manager: FederatedGatewayMa…` |
| `get_activated_models_for_display` | `def get_activated_models_for_display( gateway_manager: SingleGatewayManager \| None, federated_manager: FederatedGate…` |
| `get_all_available_models` | `def get_all_available_models( gateway_manager: SingleGatewayManager \| None, federated_manager: FederatedGatewayManag…` |
| `get_local_model_ids` | `def get_local_model_ids( gateway_manager: SingleGatewayManager \| None, ) -> set[str]` |
| `get_model_context_metadata` | `def get_model_context_metadata( gateway_manager: SingleGatewayManager \| None, federated_manager: FederatedGatewayMan…` |
| `get_model_dispatch_metadata` | `def get_model_dispatch_metadata( gateway_manager: SingleGatewayManager \| None, federated_manager: FederatedGatewayMa…` |
| `get_model_source_map` | `def get_model_source_map( local_stargate_id: str, gateway_manager: SingleGatewayManager \| None, federated_manager: F…` |
| `get_model_status_map` | `def get_model_status_map( local_stargate_id: str, gateway_manager: SingleGatewayManager \| None, federated_manager: F…` |
| `is_model_in_any_catalog` | `def is_model_in_any_catalog( model_id_str: str, gateway_manager: SingleGatewayManager \| None, federated_manager: Fed…` |

#### `services/universal-stargate/systems/routing/selection/collector.py`

| Symbol | Signature summary |
|---|---|
| `_validate_model_requirements` | `def _validate_model_requirements( vram_usage: int \| None, ram_usage: int \| None, model_id_str: str, ) -> tuple[int,…` |
| `build_placement` | `async def build_placement( model_id: ModelId, gateway_manager: Any, # SingleGatewayManager original_model_id: str \| …` |
| `collect_gateways` | `async def collect_gateways( gateway_instances: list[Any], # list[GatewayInstance] get_status: callable = None, timeou…` |
| `is_gateway_dispatchable` | `def is_gateway_dispatchable(gateway_ref: object) -> bool` |

#### `services/universal-stargate/systems/routing/selection/decision/admission_verdict.py`

| Symbol | Signature summary |
|---|---|
| `_attainable_ceiling_mb` | `def _attainable_ceiling_mb(vram_total_mb: int, vram_free_mb: int) -> int` |
| `_clamp` | `def _clamp(value: int, floor_mb: int, cap_mb: int) -> int` |
| `_nonreclaimable_overhead_mb` | `def _nonreclaimable_overhead_mb(vram_total_mb: int) -> int` |
| `compute_capped_margin_mb` | `def compute_capped_margin_mb( footprint_est_mb: int, *, vram_margin_pct: int, vram_headroom_floor_mb: int, vram_margi…` |
| `compute_non_evictable_ram_reserve_mb` | `def compute_non_evictable_ram_reserve_mb( ram_total_mb: int, resource_margins: dict[str, float \| int] \| None = None…` |
| `compute_non_evictable_vram_reserve_mb` | `def compute_non_evictable_vram_reserve_mb(vram_total_mb: int) -> int` |
| `evaluate_ram_reclaim` | `def evaluate_ram_reclaim( *, needed_mb: int, catalog_freeable_mb: int, ram_free_mb: int, ram_total_mb: int, loading_r…` |
| `evaluate_vram_admission` | `def evaluate_vram_admission( *, footprint_est_mb: int, vram_free_mb: int, vram_total_mb: int, reserved_mb: int, resou…` |
| `evaluate_vram_reclaim` | `def evaluate_vram_reclaim( *, needed_mb: int, catalog_freeable_mb: int, vram_free_mb: int, vram_total_mb: int, effect…` |

#### `services/universal-stargate/systems/routing/selection/decision/busy_view.py`

| Symbol | Signature summary |
|---|---|
| `_is_model_actually_busy` | `def _is_model_actually_busy( gateway: Gateway, model_id: ModelId, routing_key_tracker: RoutingKeyTracker \| None, gw_…` |
| `actually_busy_models` | `def actually_busy_models( gateway: Gateway, routing_key_tracker: RoutingKeyTracker \| None = None, gw_keys_in_flight:…` |
| `idle_models` | `def idle_models( gateway: Gateway, routing_key_tracker: RoutingKeyTracker \| None = None, gw_keys_in_flight: set[str]…` |

#### `services/universal-stargate/systems/routing/selection/decision/config.py`

| Symbol | Signature summary |
|---|---|
| `load_routing_policy` | `def load_routing_policy(config: dict[str, Any]) -> RoutingPolicy` |

#### `services/universal-stargate/systems/routing/selection/decision/engine/capacity_constraints.py`

| Symbol | Signature summary |
|---|---|
| `is_capacity_constrained` | `def is_capacity_constrained(candidate: GatewayCandidate) -> bool` |

#### `services/universal-stargate/systems/routing/selection/decision/engine/core.py`

| Symbol | Signature summary |
|---|---|
| `DecisionEngine.__init__` | `def __init__( self, policy: RoutingPolicy, event_bus: Any = None, emit_traces: bool \| None = None, routing_key_track…` |
| `DecisionEngine._apply_selection_rule` | `def _apply_selection_rule( self, candidates: list[GatewayCandidate], hard_affinity: Any, ) -> tuple[GatewayCandidate …` |
| `DecisionEngine._emit_decision_trace` | `def _emit_decision_trace(self, trace: DecisionTrace) -> None` |
| `DecisionEngine.select` | `def select( self, gateways: list[Gateway], placement: Placement, request_id: str \| None = None, sticky: bool \| None…` |
| `DecisionEngine.select_excluding` | `def select_excluding( self, gateways: list[Gateway], placement: Placement, *, excluded_gateway_names: frozenset[str],…` |
| `create_decision_engine` | `def create_decision_engine( policy: RoutingPolicy, event_bus: Any = None, routing_key_tracker: RoutingKeyTracker \| N…` |

#### `services/universal-stargate/systems/routing/selection/decision/engine/selection_rules.py`

| Symbol | Signature summary |
|---|---|
| `apply_selection_rule` | `def apply_selection_rule( *, candidates: list[GatewayCandidate], hard_affinity, eviction_margin: float, ) -> tuple[Ga…` |

#### `services/universal-stargate/systems/routing/selection/decision/engine/trace_emission.py`

| Symbol | Signature summary |
|---|---|
| `emit_decision_trace` | `def emit_decision_trace( *, event_bus: Any, trace: DecisionTrace, include_candidate_details: bool, ) -> None` |

#### `services/universal-stargate/systems/routing/selection/decision/eviction_cooldown_policy.py`

| Symbol | Signature summary |
|---|---|
| `clear_cooldown_override_tracker` | `def clear_cooldown_override_tracker() -> None` |
| `cooldown_override_eligible` | `def cooldown_override_eligible( remaining_s: float, *, hard_floor_s: float = COOLDOWN_HARD_FLOOR_S, ) -> bool` |
| `oscillation_blocks_override` | `def oscillation_blocks_override( key: CooldownOverrideKey, *, window_s: float = COOLDOWN_OVERRIDE_OSCILLATION_WINDOW_…` |
| `oscillation_hold_remaining_s` | `def oscillation_hold_remaining_s( key: CooldownOverrideKey, *, window_s: float = COOLDOWN_OVERRIDE_OSCILLATION_WINDOW…` |
| `record_cooldown_override` | `def record_cooldown_override( key: CooldownOverrideKey, *, now: float \| None = None, ) -> None` |
| `remaining_cooldown_s` | `def remaining_cooldown_s( gateway: Gateway, model_id: ModelId, eviction_cooldown_s: float, *, now: float \| None = No…` |

#### `services/universal-stargate/systems/routing/selection/decision/eviction_hysteresis.py`

| Symbol | Signature summary |
|---|---|
| `filter_evictable_with_hysteresis` | `def filter_evictable_with_hysteresis( gateway: Gateway, evictable: list[ModelId], *, eviction_cooldown_s: float, has_…` |

#### `services/universal-stargate/systems/routing/selection/decision/eviction_planning.py`

| Symbol | Signature summary |
|---|---|
| `_compute_eviction_plan` | `def _compute_eviction_plan( gateway: "Gateway", placement: "Placement", requirements_lookup: Callable[[ModelId], tupl…` |

#### `services/universal-stargate/systems/routing/selection/decision/eviction_victim_select.py`

| Symbol | Signature summary |
|---|---|
| `select_eviction_victims` | `def select_eviction_victims( gateway: Gateway, placement: Placement, *, hyst: HysteresisResult, gw_vram_mb: int, gw_r…` |

#### `services/universal-stargate/systems/routing/selection/decision/feasibility.py`

| Symbol | Signature summary |
|---|---|
| `_mark_capacity_failures_structural` | `def _mark_capacity_failures_structural( failures: list[ConstraintFailure], *, binding_axis: str, reclaimable: dict[st…` |
| `evaluate_feasibility` | `def evaluate_feasibility( gateway: Gateway, placement: Placement, policy: RoutingPolicy, requirements_lookup: Callabl…` |

#### `services/universal-stargate/systems/routing/selection/decision/feasibility_gates.py`

| Symbol | Signature summary |
|---|---|
| `early_feasibility_gates` | `def early_feasibility_gates( gateway: Gateway, placement: Placement, *, sticky: bool, is_gateway_available_fn: Callab…` |

#### `services/universal-stargate/systems/routing/selection/decision/feasibility_reclaim.py`

| Symbol | Signature summary |
|---|---|
| `can_fit_after_eviction_including_busy` | `def can_fit_after_eviction_including_busy( gateway: Gateway, placement: Placement, requirements_lookup: Callable[[Mod…` |

#### `services/universal-stargate/systems/routing/selection/decision/model_checks.py`

| Symbol | Signature summary |
|---|---|
| `_is_model_available` | `def _is_model_available(gateway: "Gateway", placement: "Placement") -> bool` |
| `_is_model_loaded` | `def _is_model_loaded(gateway: "Gateway", placement: "Placement") -> bool` |

#### `services/universal-stargate/systems/routing/selection/decision/protocols.py`

| Symbol | Signature summary |
|---|---|
| `RoutingKeyTracker.get_routing_keys_in_flight` | `def get_routing_keys_in_flight(self, gateway_id: str) -> set[str]` |
| `RoutingKeyTracker.get_routing_keys_in_flight_globally` | `def get_routing_keys_in_flight_globally(self) -> set[str]` |

#### `services/universal-stargate/systems/routing/selection/decision/requirements.py`

| Symbol | Signature summary |
|---|---|
| `create_requirements_lookup` | `def create_requirements_lookup( gateway_model_details: dict[ModelId, dict[str, Any]], ) -> Callable[[ModelId], tuple[…` |

#### `services/universal-stargate/systems/routing/selection/decision/resource_checks.py`

| Symbol | Signature summary |
|---|---|
| `_calculate_required_resources` | `def _calculate_required_resources( *, vram_mb: int, ram_mb: int, resource_margins: dict[str, float] \| None = None, )…` |
| `_check_resources` | `def _check_resources( gateway: "Gateway", placement: "Placement", requirements_lookup: Callable[[ModelId], tuple[int,…` |
| `_compute_loading_reservation` | `def _compute_loading_reservation( gateway: "Gateway", target_model: ModelId, requirements_lookup: Callable[[ModelId],…` |
| `resolve_gateway_requirements` | `def resolve_gateway_requirements( gateway: "Gateway", placement: "Placement", ) -> tuple[int, int] \| ConstraintFailure` |

#### `services/universal-stargate/systems/routing/selection/decision/scorer.py`

| Symbol | Signature summary |
|---|---|
| `_calculate_contention_score` | `def _calculate_contention_score(gateway: Gateway) -> float` |
| `_calculate_slack_score` | `def _calculate_slack_score( gateway: Gateway, placement: Placement, eviction_plan: EvictionPlanSummary \| None, gw_vr…` |
| `_calculate_staleness_score` | `def _calculate_staleness_score( gateway: Gateway, policy: RoutingPolicy, ) -> float` |
| `_is_model_loaded` | `def _is_model_loaded(gateway: Gateway, placement: Placement) -> bool` |
| `_is_model_loading` | `def _is_model_loading(gateway: Gateway, placement: Placement) -> bool` |
| `calculate_utility` | `def calculate_utility( gateway: Gateway, placement: Placement, policy: RoutingPolicy, tier: FeasibilityTier, eviction…` |

#### `services/universal-stargate/systems/routing/selection/decision/types.py`

| Symbol | Signature summary |
|---|---|
| `is_eviction_plan_actionable` | `def is_eviction_plan_actionable(plan: object) -> bool` |

#### `services/universal-stargate/systems/routing/selection/stargate_collector.py`

| Symbol | Signature summary |
|---|---|
| `_collect_federated_stargates` | `def _collect_federated_stargates( federated_manager: Any \| None, snapshot_time: float, ) -> list[Stargate]` |
| `_map_model_resources_to_details` | `def _map_model_resources_to_details( model_resources: dict[Any, dict[str, Any]], ) -> dict[Any, dict[str, int]]` |
| `_model_measured_vram_from_federated` | `def _model_measured_vram_from_federated(fg: Any) -> dict[Any, int]` |
| `collect_stargates` | `def collect_stargates( _local_stargate_id: str, federated_manager: Any \| None, ) -> list[Stargate]` |
| `federated_gateways_to_routing_candidates` | `def federated_gateways_to_routing_candidates( federated_gateways: list, ) -> list[Gateway]` |
| `stargate_to_gateway` | `def stargate_to_gateway(stargate: Stargate) -> Gateway` |
| `validate_stargate_pool` | `def validate_stargate_pool(stargates: list[Stargate]) -> None` |

#### `services/universal-stargate/systems/routing/telemetry/freshness_waiter.py`

| Symbol | Signature summary |
|---|---|
| `TelemetryFreshnessWaiter.__init__` | `def __init__(self, event_bus: Any = None) -> None` |
| `TelemetryFreshnessWaiter._notify_update` | `async def _notify_update(self) -> None` |
| `TelemetryFreshnessWaiter._subscribe_to_events` | `def _subscribe_to_events(self, loop: asyncio.AbstractEventLoop) -> None` |
| `TelemetryFreshnessWaiter.wait_for_telemetry_update` | `async def wait_for_telemetry_update(self, timeout_s: float = 0.5) -> bool` |

## Imports and Dependencies (full routing tree)

### External packages referenced
``, `asyncio`, `collections`, `contextlib`, `dataclasses`, `enum`, `functools`, `logging`, `os`, `pathlib`, `random`, `re`, `src`, `systems`, `time`, `typing`, `uuid`, `yaml`

### Internal import statements (sample per module)

- `services/universal-stargate/systems/routing/__init__.py` → `from .model_router import ModelRouter`
- `services/universal-stargate/systems/routing/__init__.py` → `from .selection.decision.engine import DecisionEngine`
- `services/universal-stargate/systems/routing/__init__.py` → `from .selection.decision.feasibility import evaluate_feasibility`
- `services/universal-stargate/systems/routing/__init__.py` → `from .selection.decision.scorer import calculate_utility`
- `services/universal-stargate/systems/routing/__init__.py` → `from .selection.decision.types import FeasibilityTier, ScoreComponents`
- `services/universal-stargate/systems/routing/aggregate_model_availability.py` → `from typing import TYPE_CHECKING, Any, Protocol`
- `services/universal-stargate/systems/routing/aggregate_model_availability.py` → `from universal_logging import get_logger`
- `services/universal-stargate/systems/routing/aggregate_model_availability.py` → `from systems.routing.selection.catalog import get_all_available_models`
- `services/universal-stargate/systems/routing/capacity/__init__.py` → `from .pool import CapacityPool, CapacityToken`
- `services/universal-stargate/systems/routing/capacity/_pool/__init__.py` → `from ._pool_class import CapacityPool`
- `services/universal-stargate/systems/routing/capacity/_pool/__init__.py` → `from ._types import CapacityAcquireTimeoutError, CapacityToken, QueueFullError`
- `services/universal-stargate/systems/routing/capacity/_pool/_acquisition.py` → `import asyncio`
- `services/universal-stargate/systems/routing/capacity/_pool/_acquisition.py` → `import random`
- `services/universal-stargate/systems/routing/capacity/_pool/_acquisition.py` → `import time`
- `services/universal-stargate/systems/routing/capacity/_pool/_acquisition.py` → `from collections import deque`
- `services/universal-stargate/systems/routing/capacity/_pool/_acquisition.py` → `from collections.abc import AsyncIterator`
- `services/universal-stargate/systems/routing/capacity/_pool/_acquisition.py` → `from contextlib import asynccontextmanager`
- `services/universal-stargate/systems/routing/capacity/_pool/_acquisition.py` → `from ._types import (
    _WAITING_EVENT_INTERVAL_S,
    CapacityAcquireTimeoutError,
    CapacityToken,
    QueueFullError,
    _Slot,
    _Waiter,
    _WaiterCancelledError,
)`
- `services/universal-stargate/systems/routing/capacity/_pool/_bookkeeping.py` → `from ._types import _Slot`
- `services/universal-stargate/systems/routing/capacity/_pool/_cancellation.py` → `from ._types import _Slot, _WaiterCancelledError`
- `services/universal-stargate/systems/routing/capacity/_pool/_diagnostics.py` → `from typing import Any`
- `services/universal-stargate/systems/routing/capacity/_pool/_pool_class.py` → `from ._acquisition import _AcquisitionMixin`
- `services/universal-stargate/systems/routing/capacity/_pool/_pool_class.py` → `from ._bookkeeping import _BookkeepingMixin`
- `services/universal-stargate/systems/routing/capacity/_pool/_pool_class.py` → `from ._cancellation import _CancellationMixin`
- `services/universal-stargate/systems/routing/capacity/_pool/_pool_class.py` → `from ._diagnostics import _DiagnosticsMixin`
- `services/universal-stargate/systems/routing/capacity/_pool/_pool_class.py` → `from ._events_admission import _EventsAdmissionMixin`
- `services/universal-stargate/systems/routing/capacity/_pool/_pool_class.py` → `from ._events_queue import _EventsQueueMixin`
- `services/universal-stargate/systems/routing/capacity/_pool/_pool_class.py` → `from ._pause import _PauseMixin`
- `services/universal-stargate/systems/routing/capacity/_pool/_pool_class.py` → `from ._release_dispatch import _ReleaseDispatchMixin`
- `services/universal-stargate/systems/routing/capacity/_pool/_pool_class.py` → `from ._types import _EventBusLike`
- `services/universal-stargate/systems/routing/capacity/_pool/_release_dispatch.py` → `from ._types import CapacityToken, _Slot, _Waiter`
- `services/universal-stargate/systems/routing/capacity/_pool/_types.py` → `from dataclasses import dataclass, field`
- `services/universal-stargate/systems/routing/capacity/pool.py` → `from ._pool import (
    CapacityAcquireTimeoutError,
    CapacityPool,
    CapacityToken,
    QueueFullError,
)`
- `services/universal-stargate/systems/routing/eviction/__init__.py` → `from .event_waiter import EvictionWaiter, UnloadResult`
- `services/universal-stargate/systems/routing/eviction/__init__.py` → `from .executor import (
    EvictionInflightRegistry,
    EvictionOutcome,
    EvictionStatus,
    execute_eviction_plan,
    get_eviction_plan_for_gateway,
)`
- `services/universal-stargate/systems/routing/eviction/event_waiter.py` → `from enum import Enum`
- `services/universal-stargate/systems/routing/eviction/event_waiter.py` → `from typing import TYPE_CHECKING`
- `services/universal-stargate/systems/routing/eviction/event_waiter.py` → `from model_id import ModelId`
- `services/universal-stargate/systems/routing/eviction/executor.py` → `import uuid`
- `services/universal-stargate/systems/routing/eviction/executor.py` → `from dataclasses import dataclass`
- `services/universal-stargate/systems/routing/model_router.py` → `from typing import TYPE_CHECKING, Any`
- `services/universal-stargate/systems/routing/model_router.py` → `from model_id import ModelId, validate_model_id`
- `services/universal-stargate/systems/routing/model_router.py` → `from .selection.collector import build_placement`
- `services/universal-stargate/systems/routing/model_router.py` → `from .selection.decision import DecisionEngine, FeasibilityTier, load_routing_policy`
- `services/universal-stargate/systems/routing/queue/__init__.py` → `from .request_queue import QueuedRequest, RequestQueue`
- `services/universal-stargate/systems/routing/queue/__init__.py` → `from .verification import ResourceVerifier, VerificationResult`
- `services/universal-stargate/systems/routing/queue/request_queue/__init__.py` → `from .maintenance import RequestQueueMaintenance`
- `services/universal-stargate/systems/routing/queue/request_queue/__init__.py` → `from .runtime import RequestQueueRuntime`
- `services/universal-stargate/systems/routing/queue/request_queue/__init__.py` → `from .types import QueuedRequest`
- `services/universal-stargate/systems/routing/queue/verification.py` → `from src.scheduling.events import (
    GATEWAY_RESOURCE_UPDATE,
    MODEL_LOADED,
    MODEL_UNLOADED,
)`
- `services/universal-stargate/systems/routing/rag_protected_models.py` → `import functools`
- `services/universal-stargate/systems/routing/rag_protected_models.py` → `import logging`
- `services/universal-stargate/systems/routing/rag_protected_models.py` → `import os`
- `services/universal-stargate/systems/routing/rag_protected_models.py` → `from pathlib import Path`
- `services/universal-stargate/systems/routing/rag_protected_models.py` → `import yaml`
- `services/universal-stargate/systems/routing/selection/__init__.py` → `from .catalog import (
    collect_stargate_model_sets,
    get_activated_models_for_display,
    get_all_available_models,
    get_model_source_map,
    is_model_in_any_catalog,
)`
- `services/universal-stargate/systems/routing/selection/__init__.py` → `from .collector import build_placement, collect_gateways`
- `services/universal-stargate/systems/routing/selection/__init__.py` → `from .types import Gateway, Placement, Predicate, Scorer, SelectionResult`
- `services/universal-stargate/systems/routing/selection/decision/__init__.py` → `from .config import (
    AffinityRule,
    AffinityStrength,
    RoutingPolicy,
    ScoringWeights,
    load_routing_policy,
)`
- `services/universal-stargate/systems/routing/selection/decision/__init__.py` → `from .engine import DecisionEngine, create_decision_engine`
- `services/universal-stargate/systems/routing/selection/decision/__init__.py` → `from .feasibility import evaluate_feasibility`
- `services/universal-stargate/systems/routing/selection/decision/__init__.py` → `from .scorer import calculate_utility`
- `services/universal-stargate/systems/routing/selection/decision/__init__.py` → `from .stability import StickyPlacementTracker`
- `services/universal-stargate/systems/routing/selection/decision/__init__.py` → `from .types import (
    ConstraintFailure,
    DecisionTrace,
    EvictionPlanSummary,
    FeasibilityTier,
    GatewayCandidate,
    ScoreComponents,
)`
- `services/universal-stargate/systems/routing/selection/decision/admission_verdict.py` → `from enum import StrEnum`
- `services/universal-stargate/systems/routing/selection/decision/config.py` → `import re`
- `services/universal-stargate/systems/routing/selection/decision/engine/__init__.py` → `from .core import DecisionEngine, create_decision_engine`
- `services/universal-stargate/systems/routing/selection/decision/engine/capacity_constraints.py` → `from ..types import GatewayCandidate`
- `services/universal-stargate/systems/routing/selection/decision/engine/core.py` → `from collections.abc import Callable`
- `services/universal-stargate/systems/routing/selection/decision/engine/core.py` → `from ..config import RoutingPolicy`
- `services/universal-stargate/systems/routing/selection/decision/engine/core.py` → `from ..feasibility import evaluate_feasibility`
- `services/universal-stargate/systems/routing/selection/decision/engine/core.py` → `from ..requirements import create_requirements_lookup`
- `services/universal-stargate/systems/routing/selection/decision/engine/core.py` → `from ..scorer import calculate_utility`
- `services/universal-stargate/systems/routing/selection/decision/engine/core.py` → `from ..stability import StickyPlacementTracker`
- `services/universal-stargate/systems/routing/selection/decision/engine/core.py` → `from ..types import DecisionTrace, FeasibilityTier, GatewayCandidate`
- `services/universal-stargate/systems/routing/selection/decision/engine/core.py` → `from .capacity_constraints import is_capacity_constrained`
- `services/universal-stargate/systems/routing/selection/decision/engine/core.py` → `from .selection_rules import apply_selection_rule`
- `services/universal-stargate/systems/routing/selection/decision/engine/core.py` → `from .trace_emission import emit_decision_trace`
- `services/universal-stargate/systems/routing/selection/decision/engine/selection_rules.py` → `from ..types import FeasibilityTier, GatewayCandidate`
- `services/universal-stargate/systems/routing/selection/decision/engine/trace_emission.py` → `from ..types import DecisionTrace`
- `services/universal-stargate/systems/routing/selection/decision/eviction_hysteresis.py` → `from systems.routing.rag_protected_models import is_rag_protected_model`
- `services/universal-stargate/systems/routing/selection/decision/eviction_hysteresis.py` → `from .eviction_cooldown_policy import (
    COOLDOWN_HARD_FLOOR_S,
    EvictionRequestClass,
    cooldown_override_eligible,
    remaining_cooldown_s,
)`
- `services/universal-stargate/systems/routing/selection/decision/eviction_planning.py` → `from .busy_view import actually_busy_models, idle_models`
- `services/universal-stargate/systems/routing/selection/decision/eviction_planning.py` → `from .eviction_cooldown_policy import EvictionRequestClass`
- `services/universal-stargate/systems/routing/selection/decision/eviction_planning.py` → `from .eviction_hysteresis import filter_evictable_with_hysteresis`
- `services/universal-stargate/systems/routing/selection/decision/eviction_planning.py` → `from .eviction_victim_select import (
    compute_non_evictable_vram_reserve_mb,
    select_eviction_victims,
)`
- `services/universal-stargate/systems/routing/selection/decision/eviction_planning.py` → `from .resource_checks import _compute_loading_reservation, resolve_gateway_requirements`
- `services/universal-stargate/systems/routing/selection/decision/eviction_planning.py` → `from .types import ConstraintFailure, EvictionPlanAbort, EvictionPlanSummary`
- `services/universal-stargate/systems/routing/selection/decision/eviction_victim_select.py` → `from .admission_verdict import (
    compute_non_evictable_vram_reserve_mb,
    evaluate_ram_reclaim,
    evaluate_vram_admission,
    evaluate_vram_reclaim,
)`
- `services/universal-stargate/systems/routing/selection/decision/eviction_victim_select.py` → `from .eviction_hysteresis import HysteresisResult`
- `services/universal-stargate/systems/routing/selection/decision/eviction_victim_select.py` → `from .types import EvictionPlanAbort, EvictionPlanSummary`
- `services/universal-stargate/systems/routing/selection/decision/feasibility.py` → `from .admission_verdict import AdmissionVerdict`
- `services/universal-stargate/systems/routing/selection/decision/feasibility.py` → `from .eviction_cooldown_policy import (
    DEFAULT_EVICTION_COOLDOWN_S,
    EvictionRequestClass,
)`
- `services/universal-stargate/systems/routing/selection/decision/feasibility.py` → `from .eviction_planning import _compute_eviction_plan`
- `services/universal-stargate/systems/routing/selection/decision/feasibility.py` → `from .feasibility_gates import early_feasibility_gates`
- `services/universal-stargate/systems/routing/selection/decision/feasibility.py` → `from .feasibility_reclaim import _can_fit_after_eviction_including_busy`
- `services/universal-stargate/systems/routing/selection/decision/feasibility.py` → `from .resource_checks import (
    _calculate_required_resources,
    _check_resources,
    resolve_gateway_requirements,
)`
- `services/universal-stargate/systems/routing/selection/decision/feasibility.py` → `from .types import (
    ConstraintFailure,
    EvictionPlanAbort,
    EvictionPlanSummary,
    FeasibilityTier,
)`
- `services/universal-stargate/systems/routing/selection/decision/feasibility_gates.py` → `from .model_checks import _is_model_available, _is_model_loaded`
- `services/universal-stargate/systems/routing/selection/decision/feasibility_gates.py` → `from .types import ConstraintFailure, EvictionPlanSummary, FeasibilityTier`
- `services/universal-stargate/systems/routing/selection/decision/feasibility_reclaim.py` → `from .admission_verdict import (
    AdmissionVerdict,
    evaluate_ram_reclaim,
    evaluate_vram_admission,
    evaluate_vram_reclaim,
)`
- `services/universal-stargate/systems/routing/selection/decision/feasibility_reclaim.py` → `from .types import ConstraintFailure`
- `services/universal-stargate/systems/routing/selection/decision/protocols.py` → `from typing import Protocol`
- `services/universal-stargate/systems/routing/selection/decision/resource_checks.py` → `from .admission_verdict import AdmissionVerdict, evaluate_vram_admission`
- `services/universal-stargate/systems/routing/selection/decision/scorer.py` → `from .resource_checks import resolve_gateway_requirements`
- `services/universal-stargate/systems/routing/selection/decision/scorer.py` → `from .types import (
    ConstraintFailure,
    EvictionPlanSummary,
    FeasibilityTier,
    ScoreComponents,
)`
- `services/universal-stargate/systems/routing/selection/decision/types.py` → `from enum import IntEnum`
- `services/universal-stargate/systems/routing/selection/decision/types.py` → `from typing import TYPE_CHECKING, Any, Literal`
- `services/universal-stargate/systems/routing/selection/stargate_collector.py` → `from collections import Counter`
- `services/universal-stargate/systems/routing/selection/stargate_collector.py` → `from .collector import is_gateway_dispatchable`
- `services/universal-stargate/systems/routing/selection/stargate_collector.py` → `from .types import Stargate`
- `services/universal-stargate/systems/routing/selection/types.py` → `from typing import Any, TypedDict`
- `services/universal-stargate/systems/routing/telemetry/__init__.py` → `from .freshness_waiter import TelemetryFreshnessWaiter`

<!-- AUTHORED -->
## Synthesis: eviction confirmation path (full routing tree)

For a T2 (evict-then-load) placement, the declared eviction confirmation path is:

1. `selection/decision/eviction_planning.py` computes a plan via `_compute_eviction_plan`, drawing idle/busy classification from `busy_view.idle_models` / `busy_view.actually_busy_models` (tracker-authoritative, telemetry-secondary), then filtering through `eviction_hysteresis.filter_evictable_with_hysteresis` (cooldown + demand escape hatches) and `eviction_victim_select.select_eviction_victims` (greedy minimum idle-model set, hardware-reserve-corrected).
2. `eviction/executor.execute_eviction_plan` executes the plan per model: register a wait handle on `EvictionWaiter` *before* the HTTP unload request (race prevention per `event_waiter.py`'s docstring), send the HTTP request, then wait for the `MODEL_UNLOADED` EventBus event.
3. Confirmation is typed, not boolean: `EvictionOutcome.ok is True` only for `CONFIRMED`. Absence of an EventBus yields `UNCONFIRMED_NO_BUS` (fail-closed); a stopped waiter yields `SHUTDOWN`, distinct from `UNLOADED`.

End-to-end flow beyond this synthesis also involves `capacity/_pool/_acquisition.py` admission, `selection/decision/engine/core.py` selection, and `ModelRouter.route_request` federation orchestration — summarized in **Module Inventory** (57/57) with module docstrings as authority.

<!-- GENERATED:END -->
