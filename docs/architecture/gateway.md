# Universal LLM Gateway — Architecture Overview

<!-- GENERATED:START generated=2026-11-10 method=targeted-refresh source_commit=c75e3fc82d555f4c9e8bf3e6e82ad7949fe5338a refreshed_by=packet:arch-docs-22999-legC packages=80/80 modules=405/405 -->

> **Provenance disclaimer.** Sections §1–§10 summarize high-traffic subsystems from **module docstrings** at HEAD. §11 lists every production package under `services/_universal-llm-gateway/` (excludes tests). Claims reflect declarations, not verified runtime behavior. **Coverage: 80/80 package directories, 405/405 modules** (each module appears in exactly one package row in §11; §1–§10 add module-level rows where prior style already did).

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

Package-shadow for `model_loaders.yaml`: `ConfigManager`, `deep_merge_dict`, `ConfigValidationError`, and merge helpers (`manager.py`, `merge.py`, `validation.py`, `io.py`, `crud.py`, `types.py`). See **§11** for the full `core/config_manager` package row (7 modules).

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

Remaining HTTP and WebSocket router packages (`routers/api/v1/*`, `routers/v1/chat/completions`, etc.) are inventoried in **§11** (package rows).

## 8. Jobs

**`jobs/job.py`**

Base `Job` with status tracking and W3C SSE log streaming (`log` / `complete` / `error` events).

**`jobs/measurement/execution.py`**

Thin orchestration for VRAM/RAM profiling across CPU/GPU modes.

**`jobs/measurement/common.py`**

Shared subprocess hardening, process-tree memory measurement, health polling.

**`jobs/measurement/gpu.py`**

GPU/hybrid measurement execution (layer-count binary search, llama-server spawn path).

`jobs/measurement` (17 modules) and `jobs/context_detection` (8 modules) are summarized in **§11**; the four modules above remain the narrative entry points.

## 9. Application Bootstrap

**`app/app_factory.py`**

FastAPI application factory.

**`src/main.py`**

Uvicorn entry: logging bootstrap, engine environment, socket config, then `app_factory`.

## 10. Resource tracking, cache, inference streaming, discovery

**`core/resources/tracker.py`**

`ResourceTracker`: VRAM/RAM per model, load/unload state machine, inference occupancy.

**`core/caching/cache_manager.py`**

Phase 2B response cache manager for repeat LLM requests.

**`core/workers/inference/streaming.py`**

Streaming inference with guaranteed cancellation and worker crash detection.

**`tools/discovery.py`** (`src/tools/discovery.py`)

Model discovery via `inference_djinn` inspectors; emits schema-compliant loader YAML.

## 11. Package inventory (80 packages, 405 modules)

Paths in the **Package** column are relative to `services/_universal-llm-gateway/`: entries without a `src/` prefix are top-level service dirs (`config/`, `scripts/`, service `tools/`); all other rows map to `src/<package>/`.

| Package | Modules | Purpose (from __init__ or lead module docstring) |
|---|---:|---|
| `(root)` | 1 | Universal LLM Gateway - Root Package |
| `config` | 1 | (no module docstring) |
| `scripts` | 5 | (no module docstring) |
| `src` | 2 | Universal LLM Gateway - Core Package |
| `(service)/tools` | 1 | Universal LLM Gateway - Monitoring Tools This package contains tools for monitoring and debugging the middleware system. |
| `app` | 3 | App package for Universal LLM Gateway application lifecycle management. |
| `core` | 14 | Core functionality including configuration, model registry, and management |
| `jobs` | 3 | Jobs package for background task execution. Provides: - Job: Base job class with status tracking and log streaming - MeasurementJo… |
| `middleware` | 3 | Security middleware for the Universal LLM Gateway. |
| `routers` | 4 | Centralized dependency injection functions for the Universal LLM Gateway API. |
| `schemas` | 12 | Pydantic schemas for API request/response validation |
| `tools` | 1 | Model discovery and metadata extraction using inference_djinn inspectors. Automatically generates complete, schema-compliant model configurations from model fil |
| `utils` | 5 | Utility modules for logging, monitoring, and validation |
| `app/lifecycle` | 9 | Public facade for the Universal LLM Gateway application lifecycle package. This package encapsulates all startup, runtime, and shu… |
| `core/batching` | 3 | Request batching system for Universal LLM Gateway Phase 2B Enables processing multiple requests in single inference pass for impro… |
| `core/caching` | 2 | Intelligent caching system for Universal LLM Gateway Phase 2B Provides response caching for improved performance and reduced GPU u… |
| `core/catalog` | 7 | Catalog Module - Model catalog loading, validation, and conversion. V2 Schema-Per-Engine Architecture: - SchemaRegistry: Central r… |
| `core/config_manager` | 7 | Configuration manager for model_loaders.yaml with validation and atomic writes. Package-shadow of config_manager.py. Re-exports Co… |
| `core/events` | 7 | Event-driven architecture support for Universal LLM Gateway. This module provides: - EventBus: Central event distribution system (… |
| `core/hot_reload` | 4 | Hot reload functionality for YAML configuration files. Uses universal_hot_reload.HotReloadWatcher (shared library) for pure async … |
| `core/loaders` | 1 | Loaders package for Universal LLM Gateway. All local inference is handled by the djinn domain. The gateway no longer handles exter… |
| `core/metrics` | 1 | Metrics collection for the Universal LLM Gateway. |
| `core/model_registry` | 2 | Model registry package for managing model metadata and validation |
| `core/resources` | 13 | Resource tracking package. Provides comprehensive resource monitoring for model VRAM/RAM usage, lifecycle states, and inference tr… |
| `core/websocket` | 6 | WebSocket module for Stargate control plane. |
| `core/workers` | 14 | Workers module for Universal LLM Gateway. This module contains all worker-related functionality including process management, work… |
| `jobs/context_detection` | 8 | Context detection and activation logic for measurement jobs — package-shadow. Re-exports public helpers so existing `from ..contex… |
| `jobs/measurement` | 17 | Measurement job package. |
| `routers/model_metadata_adapter` | 7 | Model metadata adapter package for API-friendly registry formatting. Package-shadow of model_metadata_adapter.py. Re-exports Model… |
| `routers/v1` | 2 | Embeddings endpoint - /v1/embeddings OpenAI-compatible text embedding generation. |
| `routers/ws` | 3 | WebSocket routers for real-time state streaming. |
| `schemas/resource_management` | 7 | Queue management API response schemas for Universal LLM Gateway. Package-shadow of the former monolithic resource_management.py. R… |
| `core/catalog/schemas` | 10 | Schema Registry - Central registry for engine schemas. Usage: from core.catalog.schemas import SchemaRegistry schema = SchemaRegis… |
| `core/catalog/split` | 3 | Catalog split utilities for domain-based file operations. Provides path mapping and atomic file writing for individual model files… |
| `core/events/persistence_async` | 4 | Non-blocking SQLite event persistence with automatic cleanup and query support. |
| `core/events/types` | 8 | LLM-specific event signals and factories for Universal LLM Gateway. Package-shadow of the former monolithic types.py. Re-exports e… |
| `core/metrics/state_channel_metrics` | 5 | Metrics collection for state channel monitoring — package-shadow of state_channel_metrics.py. Re-exports StateChannelMetricsCollec… |
| `core/model_registry/extractors` | 2 | Format-specific metadata extractors - API proxy only (other formats use djinn inspectors) |
| `core/model_registry/registry` | 10 | Model registry package — re-exports ModelRegistry and normalize_model_id. |
| `core/model_registry/validation` | 6 | Model validation logic for files, profiles, and catalog metadata correctness. |
| `core/resources/vram_reconciler` | 10 | Periodic VRAM reconciliation for phantom-model and ghost-model detection. Phantom model: worker process running but not tracked (o… |
| `core/websocket/event_forwarder` | 3 | Forward EventBus events to WebSocket clients — package-shadow of event_forwarder.py. Re-exports WebSocketEventForwarder so existin… |
| `core/workers/cancellation` | 2 | Stream cancellation operations. Provides event-driven cancellation emission with fallback state mutation. |
| `core/workers/chat_completion` | 3 | Chat completion module for WorkerController. |
| `core/workers/controller` | 10 | Worker controller package — re-exports WorkerController. |
| `core/workers/entrypoint` | 2 | Worker entrypoint configuration. Defines how workers are invoked (module vs script path). |
| `core/workers/inference` | 4 | Inference operations for worker processes. |
| `core/workers/model_operations` | 5 | Model operations module for WorkerController. |
| `core/workers/monitoring` | 2 | Process monitoring utilities for worker processes. |
| `core/workers/process` | 2 | Process lifecycle and communication management. This package contains components for managing worker process lifecycle and IPC com… |
| `core/workers/worker` | 9 | Worker module - process lifecycle and RPC handling. Provides the Worker class for model inference workers. |
| `routers/api/v1` | 1 | Metrics API endpoints. |
| `routers/v1/audio` | 5 | Audio routers for Whisper transcription (streaming and file-based). |
| `routers/v1/images` | 5 | Images API endpoints. |
| `routers/v1/models` | 3 | Models router for /v1/models endpoints |
| `core/workers/model_operations/load_flow` | 10 | Model loading flow operations: worker lifecycle, verification, and finalization. |
| `core/workers/model_operations/loader` | 7 | Model loading operations for WorkerController with single-flight coalescing. |
| `core/workers/process/communication` | 8 | Process communication modules for worker management. Modules are split by responsibility: - manager: High-level orchestration (sen… |
| `core/workers/process/crash_callback` | 6 | Process crash callback handling - event-driven cleanup. Orchestrates crash detection response with isolated error handling. |
| `core/workers/process/lifecycle` | 5 | Process lifecycle management for worker startup, shutdown, and cleanup. |
| `core/workers/worker/rpc` | 10 | Worker RPC handlers (non-streaming). |
| `core/workers/worker/stream` | 4 | Worker stream handlers. Directory-based naming for specificity: worker/stream/* is the worker-streaming domain (avoid generic stre… |
| `routers/api/v1/catalog` | 1 | Catalog API endpoints. |
| `routers/api/v1/config` | 2 | Configuration management API endpoints. |
| `routers/api/v1/jobs` | 2 | Jobs API endpoints. |
| `routers/api/v1/management` | 1 | Management API endpoints for streaming cancellation. Provides endpoints for cancelling active streaming requests and monitoring active streams. |
| `routers/api/v1/model_info` | 5 | Model info router for /api/v1/model_info endpoints |
| `routers/api/v1/models` | 2 | Model management endpoints for /api/v1/models |
| `routers/api/v1/status` | 2 | GET /api/v1/status/detailed - Comprehensive gateway status for proxy orchestration |
| `routers/api/v1/tokens` | 1 | POST /api/v1/tokens/count - Token counting endpoint |
| `routers/v1/audio/stream` | 7 | WebSocket endpoint package for real-time audio streaming transcription. Package-shadow of stream.py. Re-exports `router` so `from … |
| `routers/v1/chat/completions` | 9 | Chat completions endpoint. |
| `routers/v1/models/_model_id_` | 3 | Model-specific endpoints for /v1/models/{model_id} |
| `core/workers/process/communication/orchestration` | 4 | Model loading orchestration: flow execution and failure error handlers. |
| `routers/api/v1/catalog/get` | 5 | Catalog API endpoints package-shadow of get.py. Re-exports the shared router so existing imports such as `from src.routers.api.v1.… |
| `routers/api/v1/model_info/_model_id_` | 2 | Model-specific info endpoints for /api/v1/model_info/{model_id} |
| `routers/api/v1/models/_model_id_` | 4 | Model-specific management endpoints for /api/v1/models/{model_id} |
| `routers/api/v1/models/management` | 4 | Model Configuration Management API Router. Provides HTTP endpoints for programmatic model catalog management. Secured by gateway c… |
| `routers/api/v1/status/resource_history` | 8 | Resource History API - Query resource usage history for models. Package-shadow of resource_history.py. Re-exports `router` with en… |
| `routers/v1/chat/completions/stream` | 4 | Streaming response package for chat completions. Public exports: - generate_streaming_response: Main streaming response generator … |

<!-- GENERATED:END -->

---

## Coverage Notes

### unsupported_claims

None added in this refresh. Statements above stay within declared docstrings for the listed paths.

### missing_coverage

None for tree accounting: **§11** lists all 80 package directories covering 405 production modules. Narrative depth in §1–§10 remains selective (module-level tables only where this doc already used that style).

### human_markers

Not re-scanned across the full tree in this pass; prior CDP inventory reported none in the excerpt set.

### loaded_skills

None (targeted doc refresh from source docstrings).
