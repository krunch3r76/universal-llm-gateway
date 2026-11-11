# Universal LLM Gateway — Architecture Overview

<!-- GENERATED:START generated=2026-11-10 method=targeted-refresh source_commit=c75e3fc82d555f4c9e8bf3e6e82ad7949fe5338a refreshed_by=agent-bus:22999 coverage=40/405 -->

> **Provenance disclaimer.** Sections below are derived from **module docstrings and signatures** read at HEAD in `services/_universal-llm-gateway/`. They describe what the code **declares**, not verified runtime behavior. Coverage is **40 of 405** production `.py` modules (excludes tests); remaining modules are out of scope for this targeted refresh.

## 1. Workers & Controller

**`core/workers/controller/controller.py`**

Declares `WorkerController` as a **mixin composition** (`LifecycleMixin`, `ModelFacadeMixin`, `InferenceFacadeMixin`, `CancelRpcMixin`, `MultimodalMixin`, `ProcessStatusMixin`, `MetricsMixin`) delegating model load/unload and chat handlers.

- Operates on **canonical model IDs only** — not `:N` instance suffixes.
- Enforces **one worker per `model_id`**.
- Constructor wires resource monitoring (`_init_resource_monitoring`), filesystem/socket paths (`_init_paths`), subprocess managers (`_init_managers`), `ModelLoader` / `ModelUnloader`, and streaming/non-streaming chat completion handlers.

**`core/workers/controller/__init__.py`**

Package-shadow re-export of `WorkerController`.

## 2. Model Registry

**`core/model_registry/registry/core.py`**

Declares `ModelRegistry` composing **catalog**, **loader**, and **availability** mixins for metadata and validation.

**Mixin modules (package-shadow split):**

| Module | Declared role |
|---|---|
| `registry/catalog.py` | Catalog key lookup and YAML path resolution. |
| `registry/loaders.py` | Profile loader selection and merged worker config. |
| `registry/availability.py` | Model file validation and availability reporting. |
| `registry/loaded.py` | Loaded instance tracking by canonical model ID. |
| `registry/listings.py` | Synthetic model listing with hardware filters. |
| `registry/resources.py` | VRAM/RAM requirements and token limits from profiles. |
| `registry/info.py` | `ModelInfo` construction and enablement checks. |
| `registry/identifiers.py` | ID normalization, suffix validation, synthetic IDs. |

**`core/model_registry/registry/__init__.py`**

Re-exports `ModelRegistry` and `normalize_model_id` (implemented in `identifiers.py`).

## 3. Events & Types

**`core/events/types/__init__.py`**

Package-shadow replacement for monolithic `types.py`; re-exports event signals and `@event_factory` helpers for stable import paths.

**`core/events/types/model_lifecycle.py`**

Declares `MODEL_*` lifecycle signals and factory helpers consumed by load flow, loader/unloader, and resource-tracker crash handling. Signal string values are an event-bus contract (see `docs/event-contracts.md`).

## 4. VRAM Reconciler

**`core/resources/vram_reconciler/reconciler.py`**

Declares `VramReconciler` composing periodic reconciliation mixins (`ReconcileLoopMixin`, `GhostSweepMixin`, `PhantomCleanupMixin`, `VramDiscrepancyMixin`, `GpuProcessMixin`, `TrackerStateMixin`).

Declared behavior on interval `RECONCILE_INTERVAL_S`:

1. **Phantom sweep** — running GPU process not in tracker → force-unload orphan.
2. **Ghost sweep** — tracked model whose engine is dead → unload and emit `MODEL_UNLOADED`.
3. **VRAM discrepancy** — `|hardware − catalog| > threshold` → alert.

## 5. Config Manager

**`core/config_manager/__init__.py`**

Package-shadow for `model_loaders.yaml`: `ConfigManager`, `deep_merge_dict`, `ConfigValidationError`, and merge helpers (`manager.py`, `merge.py`, `validation.py`, `io.py`, `crud.py`, `types.py` — not expanded in this pass).

## 6. Load Flow

**`core/workers/model_operations/load_flow/__init__.py`**

Model loading flow: worker lifecycle, verification, finalization.

| Submodule | Declared role |
|---|---|
| `worker_start.py` | Worker process startup and orphan reconciliation. |
| `worker_config.py` | Config dispatch and post-load responsiveness checks. |
| `state_measure.py` | State-machine reset and pre-load VRAM measurement. |
| `events.py` | Load lifecycle EventBus emissions (started/progress/failed). |
| `finalize.py` | Successful load finalization and catalog VRAM reconcile. |
| `failure_classify.py` | Load failure reason-code classification. |
| `failure_handle.py` | Exception handling with cleanup. |
| `cleanup.py` | Guaranteed process termination after failed loads. |
| `deps.py` | Shared lazy imports and debug helpers. |

## 7. Routers

**`routers/api/v1/models/management/__init__.py`**

Model Configuration Management API (token-gated catalog CRUD).

**`routers/api/v1/catalog/get/mutations.py`**

Catalog mutation routes: reload, profile updates, context activation; emits `CATALOG_RELOADED` after writes.

**`routers/v1/audio/params.py`**

FastAPI query parameter definitions for audio streaming.

**`routers/ws/state.py`**

WebSocket endpoint for real-time gateway state streaming.

Additional router trees (`routers/api/v1/catalog/*`, chat, batching, health) exist at HEAD but are not inventoried in this refresh.

## 8. Jobs

**`jobs/job.py`**

Base `Job` with status tracking and W3C SSE log streaming (`log` / `complete` / `error` events).

**`jobs/measurement/execution.py`**

Thin orchestration for VRAM/RAM profiling across CPU/GPU modes.

**`jobs/measurement/common.py`**

Shared subprocess hardening, process-tree memory measurement, health polling.

**`jobs/measurement/gpu.py`**

GPU/hybrid measurement execution (layer-count binary search, llama-server spawn path).

The broader `jobs/measurement/*` and `jobs/context_detection/*` packages profile models for catalog VRAM rows — module-level detail omitted here.

## 9. Application Bootstrap

**`app/app_factory.py`**

FastAPI application factory.

**`main.py`**

Uvicorn entry: logging bootstrap, engine environment, socket config, then `app_factory`.

## 10. Resource tracking, cache, inference streaming, discovery

**`core/resources/tracker.py`**

`ResourceTracker`: VRAM/RAM per model, load/unload state machine, inference occupancy.

**`core/caching/cache_manager.py`**

Phase 2B response cache manager for repeat LLM requests.

**`core/workers/inference/streaming.py`**

Streaming inference with guaranteed cancellation and worker crash detection.

**`tools/discovery.py`**

Model discovery via `inference_djinn` inspectors; emits schema-compliant loader YAML.

<!-- GENERATED:END -->

---

## Coverage Notes

### unsupported_claims

None added in this refresh. Statements above stay within declared docstrings for the listed paths.

### missing_coverage

- Remaining **~365** production modules under `services/_universal-llm-gateway/` (batching, hot_reload, full catalog schema tree, chat_completion handlers, worker state machine, event persistence, gateway_config, etc.) — no one-line inventory in this pass.
- `core/config_manager/*` implementation modules beyond the package `__init__.py` summary.
- Full HTTP router surface beyond management, catalog mutations, audio params, and websocket state.
- `jobs/context_detection/*` and non-measurement job helpers beyond `jobs/job.py`.

### human_markers

Not re-scanned across the full tree in this pass; prior CDP inventory reported none in the excerpt set.

### loaded_skills

None (targeted doc refresh from source docstrings).
