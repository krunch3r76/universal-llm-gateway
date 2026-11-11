# Universal LLM Gateway — Architecture Overview

<!-- GENERATED:START generated=2026-11-11 method=targeted-refresh inventory_sha=dbc616e212b5 source_commit=0d45bbef52c8 refreshed_by=agent-bus:23032 packages=80/80 modules=405/405 -->

> **Provenance disclaimer.** §1–§10 summarize high-traffic subsystems from **module docstrings** at HEAD. §11 lists every production package with module- and entrypoint-level rows grounded in tree-sitter extraction. Claims reflect declarations, not verified runtime behavior.

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

## 11. Per-package module inventory

**80 packages, 405 production modules** (excludes tests). Each `###` section lists modules and declared entrypoints (classes, module-level functions) from docstrings at HEAD.

### `.`

**`__init__.py`** — Universal LLM Gateway - Root Package

### `config`

**`config/__init__.py`** — (no module docstring)

### `scripts`

**`scripts/__init__.py`** — (no module docstring)

**`scripts/gateway_service_manager.py`** — (no module docstring)

| Class | Signature |
|---|---|
| `ProcessManager` | `class ProcessManager` |
| `.__init__` | `def __init__(self, logger: logging.Logger)` |
| `.find_gateway_processes` | `def find_gateway_processes( self, port: int \| None = None, unix_socket: str \| None = None ) -> list[psutil.Process]` |
| `.find_processes_on_port` | `def find_processes_on_port(self, port: int) -> list[psutil.Process]` |
| `.get_process_tree` | `def get_process_tree(self, process: psutil.Process) -> list[psutil.Process]` |
| `.terminate_process_tree` | `def terminate_process_tree( self, process: psutil.Process, timeout: int = 3, sigint_first: bool = True ) -> bool` |
| `SystemdManager` | `class SystemdManager` |
| `.__init__` | `def __init__(self, logger: logging.Logger)` |
| `.is_running_under_systemd` | `def is_running_under_systemd(self) -> bool` |
| `.is_service_active` | `def is_service_active(self, service_name: str) -> bool` |
| `.stop_service` | `def stop_service(self, service_name: str) -> bool` |
| `GatewayServiceManager` | `class GatewayServiceManager` |
| `.__init__` | `def __init__(self, environment: str = "default", fast_shutdown: bool = False)` |
| `._setup_logging` | `def _setup_logging(self) -> logging.Logger` |
| `._validate_configuration` | `def _validate_configuration(self) -> None` |
| `._setup_environment` | `def _setup_environment(self) -> None` |
| `._cleanup_unix_socket` | `def _cleanup_unix_socket(self, socket_path: str) -> None` |
| `._ensure_socket_directory` | `def _ensure_socket_directory(self, socket_path: str) -> None` |
| `._cleanup_worker_logs` | `def _cleanup_worker_logs(self) -> None` |
| `._stop_existing_services` | `def _stop_existing_services(self) -> None` |
| … | (13 methods total) |

| Function | Signature |
|---|---|
| `_validate_thread_count` | `def _validate_thread_count(value: str \| None, param_name: str) -> int \| None` |
| `main` | `def main()` |

**`scripts/generate_examples.py`** — (no module docstring)

| Class | Signature |
|---|---|
| `SchemaIntrospector` | `class SchemaIntrospector` |
| `.get_type_annotation` | `def get_type_annotation(self, field_info: FieldInfo) -> str` |
| `._get_base_type` | `def _get_base_type(self, annotation) -> str` |
| `.get_example_value` | `def get_example_value(self, field_info: FieldInfo, field_name: str) -> Any` |
| `SchemaExampleGenerator` | `class SchemaExampleGenerator` |
| `.__init__` | `def __init__(self)` |
| `.generate_example` | `def generate_example( self, format_type: str, redact_paths: bool = True, timestamp: bool = False ) -> str` |
| `._generate_header` | `def _generate_header(self, format_type: str, timestamp: bool) -> str` |
| `._generate_resource_management` | `def _generate_resource_management(self) -> str` |
| `._generate_models_section` | `def _generate_models_section( self, schema, format_type: str, redact_paths: bool ) -> str` |
| `._generate_section` | `def _generate_section( self, name: str, field_info: FieldInfo, indent: int, redact_paths: bool ) -> list[str]` |
| `._to_yaml_value` | `def _to_yaml_value(self, value: Any) -> str` |
| `._redact_path` | `def _redact_path(self, path: str) -> str` |
| … | (14 methods total) |

| Function | Signature |
|---|---|
| `validate_config_file` | `def validate_config_file(config_path: str)` |
| `main` | `def main()` |

**`scripts/manage_changelog.py`** — (no module docstring)

| Function | Signature |
|---|---|
| `get_file_date` | `def get_file_date(file_path)` |
| `rename_with_creation_date` | `def rename_with_creation_date()` |
| `categorize_files` | `def categorize_files()` |
| `list_files` | `def list_files()` |
| `main` | `def main()` |

**`scripts/model_manager.py`** — (no module docstring)

| Function | Signature |
|---|---|
| `format_error` | `def format_error(message: str) -> str` |
| `format_success` | `def format_success(message: str) -> str` |
| `format_info` | `def format_info(message: str) -> str` |
| `load_json_config` | `def load_json_config(file_path: str) -> dict[str, Any]` |
| `cmd_add` | `def cmd_add(args, config_manager: ConfigManager)` |
| `cmd_update` | `def cmd_update(args, config_manager: ConfigManager)` |
| `cmd_get` | `def cmd_get(args, config_manager: ConfigManager)` |
| `cmd_list` | `def cmd_list(args, config_manager: ConfigManager)` |
| `cmd_delete` | `def cmd_delete(args, config_manager: ConfigManager)` |
| `cmd_example` | `def cmd_example(args, config_manager: ConfigManager)` |
| `cmd_validate` | `def cmd_validate(args, config_manager: ConfigManager)` |
| `cmd_discover` | `def cmd_discover(args, config_manager: ConfigManager)` |
| `main` | `def main()` |

### `src`

**`src/__init__.py`** — Universal LLM Gateway - Core Package

**`src/main.py`** — Main FastAPI application entry point for Universal LLM Gateway inference service.

| Function | Signature |
|---|---|
| `_fix_docker_priority` | `def _fix_docker_priority()` |
| `_setup_logging_environment` | `def _setup_logging_environment()` |
| `_load_engine_environment` | `def _load_engine_environment()` |
| `get_app` | `def get_app()` |
| `_cleanup_socket` | `def _cleanup_socket(socket_path: str) -> None` |
| `_ensure_socket_directory` | `def _ensure_socket_directory(socket_path: str) -> None` |
| `_set_socket_permissions` | `def _set_socket_permissions(socket_path: str, permissions: int = 0o600) -> None` |

### `src/app`

**`src/app/__init__.py`** — App package for Universal LLM Gateway application lifecycle management.

**`src/app/app_factory.py`** — Application factory for creating FastAPI application instances.

| Function | Signature |
|---|---|
| `create_app` | `def create_app() -> FastAPI` |

**`src/app/websocket_lifecycle.py`** — WebSocket lifecycle management for Stargate control plane.

| Function | Signature |
|---|---|
| `initialize_websocket_forwarder` | `async def initialize_websocket_forwarder(app: FastAPI) -> None` |
| `shutdown_websocket_forwarder` | `async def shutdown_websocket_forwarder(app: FastAPI) -> None` |

### `src/app/lifecycle`

**`src/app/lifecycle/__init__.py`** — Public facade for the Universal LLM Gateway application lifecycle package.

**`src/app/lifecycle/component_bootstrap.py`** — Core gateway component construction for the lifecycle package.

| Function | Signature |
|---|---|
| `initialize_components` | `async def initialize_components( config_loader: ConfigLoader, ) -> tuple[ EventBus, ModelRegistry, ModelMetadataAdapter, WorkerController, GatewayConfig, ResourceMonitor, ]` |

**`src/app/lifecycle/edge_service_runtime.py`** — Peripheral async services lifecycle (WebSocket rate limiter, state channel metrics, WebSocket event forwarder).

| Function | Signature |
|---|---|
| `start_edge_services` | `async def start_edge_services(app) -> None` |
| `stop_edge_services` | `async def stop_edge_services(app) -> None` |

**`src/app/lifecycle/fastapi_lifespan.py`** — FastAPI lifespan context manager orchestration.

**`src/app/lifecycle/hot_reload_runtime.py`** — Hot reload lifecycle management.

| Function | Signature |
|---|---|
| `_emit_catalog_reloaded` | `async def _emit_catalog_reloaded(reload_event, *, event_bus) -> None` |
| `start_hot_reload_manager` | `async def start_hot_reload_manager( app, gateway_config, model_registry, event_bus ) -> None` |

**`src/app/lifecycle/logging_bootstrap.py`** — Lifecycle logging bootstrap and lifecycle logger access.

| Function | Signature |
|---|---|
| `setup_logging_from_config` | `def setup_logging_from_config(config_loader: ConfigLoader) -> None` |
| `initialize_lifecycle_loggers` | `def initialize_lifecycle_loggers() -> None` |
| `get_lifecycle_logger` | `def get_lifecycle_logger()` |
| `get_gateway_logger` | `def get_gateway_logger()` |
| `log_startup_exception` | `def log_startup_exception(error: Exception) -> None` |

**`src/app/lifecycle/model_validation_startup.py`** — Startup model/resource validation phase for the gateway lifecycle.

| Function | Signature |
|---|---|
| `validate_gateway_models` | `def validate_gateway_models(model_registry)` |

**`src/app/lifecycle/shutdown_sequence.py`** — Gateway shutdown sequence and drain signaling.

| Function | Signature |
|---|---|
| `graceful_shutdown` | `async def graceful_shutdown( app: FastAPI, timeout: float = 30.0, ) -> None` |
| `run_shutdown_sequence` | `async def run_shutdown_sequence(app: FastAPI) -> None` |

**`src/app/lifecycle/worker_runtime_startup.py`** — Worker-runtime startup, orphan cleanup, and crash handler installation.

| Function | Signature |
|---|---|
| `start_worker_runtime` | `async def start_worker_runtime(app, event_bus, worker_controller) -> None` |
| `cleanup_orphaned_workers` | `async def cleanup_orphaned_workers(event_bus, worker_controller) -> None` |
| `install_worker_event_handlers` | `def install_worker_event_handlers(app, event_bus, worker_controller) -> None` |

### `src/core`

**`src/core/__init__.py`** — Core functionality including configuration, model registry, and management

**`src/core/catalog_manager.py`** — Catalog Manager - Write operations for model catalogs.

| Class | Signature |
|---|---|
| `CatalogValidationError` | `class CatalogValidationError(Exception)` |
| `CatalogManager` | `class CatalogManager` |
| `.__init__` | `def __init__(self, catalog_loader: CatalogLoader \| None = None) -> None` |
| `._strip_measurement_data` | `def _strip_measurement_data(self, entry: dict[str, Any]) -> dict[str, Any]` |
| `._ensure_local_entry` | `def _ensure_local_entry(self, entry: dict[str, Any]) -> dict[str, Any]` |
| `._get_model_file_path` | `def _get_model_file_path( self, model_id: str, entry: dict[str, Any], *, base_dir: Path ) -> Path` |
| `._write_model_file_atomic` | `def _write_model_file_atomic(self, path: Path, entry: dict[str, Any]) -> None` |
| `._validate_entry` | `def _validate_entry(self, model_id: str, entry: dict[str, Any]) -> None` |
| `.upsert_model` | `def upsert_model( self, model_id: str, entry: dict[str, Any], *, allow_overwrite: bool = True, ) -> CatalogOperationResult` |
| `.upsert_local_only` | `def upsert_local_only( self, model_id: str, entry: dict[str, Any], ) -> CatalogOperationResult` |
| … | (9 methods total) |

| Function | Signature |
|---|---|
| `get_catalog_manager` | `def get_catalog_manager() -> CatalogManager` |

**`src/core/config_loader.py`** — Configuration loader for the Universal LLM Gateway

| Class | Signature |
|---|---|
| `ConfigLoader` | `class ConfigLoader` |
| `.__init__` | `def __init__(self, config_dir: str = "config")` |
| `.load_gateway_config` | `def load_gateway_config( self, config_file: str = "gateway_config.yaml" ) -> GatewayConfig` |
| `.load_model_loaders_config` | `def load_model_loaders_config( self, config_file: str = "model_loaders.yaml" ) -> dict[str, Any]` |
| `.load_logging_config` | `def load_logging_config(self, config_file: str = "logging.yaml") -> dict[str, Any]` |
| `._get_default_logging_config` | `def _get_default_logging_config(self) -> dict[str, Any]` |
| `.load_all_configs` | `def load_all_configs(self) -> tuple[GatewayConfig, dict[str, Any], dict[str, Any]]` |

| Function | Signature |
|---|---|
| `validate_thread_count` | `def validate_thread_count(value: str \| None, param_name: str) -> int \| None` |
| `get_config_loader` | `def get_config_loader() -> ConfigLoader` |

**`src/core/debug_logger.py`** — Dedicated debug logger for streaming diagnostics.

| Function | Signature |
|---|---|
| `log_stream_debug` | `def log_stream_debug(message: str) -> None` |
| `clear_debug_log` | `def clear_debug_log() -> None` |

**`src/core/errors.py`** — Centralized error patterns and constants for consistent error handling.

| Class | Signature |
|---|---|
| `ErrorType` | `class ErrorType` |
| `ErrorCode` | `class ErrorCode` |
| `GatewayError` | `class GatewayError(Exception)` |
| `.__init__` | `def __init__( self, message: str, error_type: str, error_code: str = None, details: dict[str, Any] \| None = None, internal_error: str \| None = None, stack_trace: str \| None = None, context: dict[str, Any] \| None = None, )` |
| `WorkerInitializationError` | `class WorkerInitializationError(GatewayError)` |
| `.__init__` | `def __init__( self, message: str, error_type: str = ErrorType.INITIALIZATION_ERROR, error_code: str = ErrorCode.WORKER_INITIALIZATION_FAILED, **kwargs, )` |
| `ModelLoadingError` | `class ModelLoadingError(GatewayError)` |
| `.__init__` | `def __init__( self, message: str, error_type: str = ErrorType.INITIALIZATION_ERROR, error_code: str = ErrorCode.MODEL_LOADING_FAILED, **kwargs, )` |
| `SyntaxErrorException` | `class SyntaxErrorException(GatewayError)` |
| `.__init__` | `def __init__( self, message: str, error_type: str = ErrorType.SYNTAX_ERROR, error_code: str = ErrorCode.SYNTAX_ERROR, **kwargs, )` |

| Function | Signature |
|---|---|
| `is_connection_error` | `def is_connection_error(error_message: str) -> bool` |
| `is_crash_error` | `def is_crash_error(error_message: str) -> bool` |
| `is_variant_sm_error` | `def is_variant_sm_error(error_message: str) -> bool` |
| `classify_error` | `def classify_error(error_message: str) -> tuple[str, str]` |
| `create_error_response` | `def create_error_response( error: Exception, status_code: int = 500, context: dict[str, Any] \| None = None, include_stack_trace: bool = True, include_internal_details: bool = True, ) -> HTTPException` |

**`src/core/file_locker.py`** — File locking utility for safe concurrent access to configuration files.

| Class | Signature |
|---|---|
| `FileLockError` | `class FileLockError(Exception)` |
| `FileLock` | `class FileLock` |
| `.__init__` | `def __init__( self, lock_file: str \| Path, timeout: float \| None = 30.0, blocking: bool = True, )` |
| `.__enter__` | `def __enter__(self)` |
| `.__exit__` | `def __exit__(self, exc_type, exc_val, exc_tb)` |
| `.acquire` | `def acquire(self)` |
| `.release` | `def release(self)` |

**`src/core/formats.py`** — Format Utilities - Model format helpers.

| Function | Signature |
|---|---|
| `is_named_profile_format` | `def is_named_profile_format(format_type: str \| None) -> bool` |
| `get_engine_for_format` | `def get_engine_for_format(format_type: str) -> str \| None` |

**`src/core/gateway_config.py`** — Gateway configuration models

| Class | Signature |
|---|---|
| `ServerConfig` | `class ServerConfig(BaseModel)` |
| `LoggingConfig` | `class LoggingConfig(BaseModel)` |
| `ProcessIsolationConfig` | `class ProcessIsolationConfig(BaseModel)` |
| `WorkerProcessesConfig` | `class WorkerProcessesConfig(BaseModel)` |
| `ModelRegistryValidationConfig` | `class ModelRegistryValidationConfig(BaseModel)` |
| `ModelRegistryConfig` | `class ModelRegistryConfig(BaseModel)` |
| `ModelsConfig` | `class ModelsConfig(BaseModel)` |
| `CachingConfig` | `class CachingConfig(BaseModel)` |
| `RateLimitingConfig` | `class RateLimitingConfig(BaseModel)` |
| `HealthCheckConfig` | `class HealthCheckConfig(BaseModel)` |
| `SecurityConfig` | `class SecurityConfig(BaseModel)` |
| `ManagementAPIConfig` | `class ManagementAPIConfig(BaseModel)` |
| `HotReloadConfig` | `class HotReloadConfig(BaseModel)` |
| `StreamingConfig` | `class StreamingConfig(BaseModel)` |
| `ResourceGuardConfig` | `class ResourceGuardConfig(BaseModel)` |
| `WarmupModeConfig` | `class WarmupModeConfig(BaseModel)` |
| `WarmupConfig` | `class WarmupConfig(BaseModel)` |
| `GGUFEngineConfig` | `class GGUFEngineConfig(BaseModel)` |
| `VLLMWarmupConfig` | `class VLLMWarmupConfig(BaseModel)` |
| `VLLMEngineConfig` | `class VLLMEngineConfig(BaseModel)` |
| `GatewayConfig` | `class GatewayConfig(BaseModel)` |

**`src/core/gpu_detection.py`** — GPU detection for platform-aware model filtering.

| Class | Signature |
|---|---|
| `GPUCapabilities` | `class GPUCapabilities` |
| `.__new__` | `def __new__(cls) -> "GPUCapabilities"` |

**`src/core/hot_reload.py`** — Hot reload functionality for YAML configuration files.

**`src/core/hot_reload_metrics.py`** — Metrics collection for hot reload functionality.

| Class | Signature |
|---|---|
| `HotReloadMetrics` | `class HotReloadMetrics` |
| `.__init__` | `def __init__(self)` |
| `.record_reload_attempt` | `def record_reload_attempt(self, model_key: str \| None, file_type: str)` |
| `.record_reload_success` | `def record_reload_success( self, model_key: str \| None, file_type: str, duration_seconds: float )` |
| `.record_reload_failure` | `def record_reload_failure( self, model_key: str \| None, file_type: str, duration_seconds: float )` |
| `.record_file_event` | `def record_file_event(self, event_type: str, file_extension: str)` |
| `.record_watch_error` | `def record_watch_error(self, error_type)` |
| `.update_active_files_count` | `def update_active_files_count(self, count: int)` |
| `.update_hot_reload_status` | `def update_hot_reload_status(self, enabled: bool)` |
| … | (10 methods total) |

**`src/core/queue_errors.py`** — Comprehensive error handling for queue management API

| Class | Signature |
|---|---|
| `QueueErrorCode` | `class QueueErrorCode(Enum)` |
| `QueueManagementError` | `class QueueManagementError(Exception)` |
| `.__init__` | `def __init__( self, error_code: QueueErrorCode, message: str, details: dict[str, Any] \| None = None, )` |
| `InsufficientResourcesError` | `class InsufficientResourcesError(QueueManagementError)` |
| `.__init__` | `def __init__( self, required_vram_mb: int, available_vram_mb: int, required_ram_mb: int = None, available_ram_mb: int = None, )` |
| `ModelNotFoundError` | `class ModelNotFoundError(QueueManagementError)` |
| `.__init__` | `def __init__(self, model_id: str)` |
| `ModelAlreadyLoadedError` | `class ModelAlreadyLoadedError(QueueManagementError)` |
| `.__init__` | `def __init__(self, model_id: str)` |
| `ModelNotLoadedError` | `class ModelNotLoadedError(QueueManagementError)` |
| `.__init__` | `def __init__(self, model_id: str)` |
| `ModelBusyError` | `class ModelBusyError(QueueManagementError)` |
| `.__init__` | `def __init__(self, model_id: str, operation: str = "operation")` |
| `LoadFailedError` | `class LoadFailedError(QueueManagementError)` |
| `.__init__` | `def __init__(self, model_id: str, reason: str)` |
| `UnloadFailedError` | `class UnloadFailedError(QueueManagementError)` |
| `.__init__` | `def __init__(self, model_id: str, reason: str)` |
| `InvalidPriorityError` | `class InvalidPriorityError(QueueManagementError)` |
| `.__init__` | `def __init__(self, priority: str)` |
| `TimeoutError` | `class TimeoutError(QueueManagementError)` |
| `.__init__` | `def __init__(self, operation: str, timeout_seconds: float)` |
| `ConcurrentOperationError` | `class ConcurrentOperationError(QueueManagementError)` |
| `.__init__` | `def __init__(self, model_id: str, operation: str)` |
| `SystemError` | `class SystemError(QueueManagementError)` |
| `.__init__` | `def __init__(self, component: str, reason: str)` |

| Function | Signature |
|---|---|
| `handle_queue_error` | `def handle_queue_error(error: QueueManagementError) -> HTTPException` |
| `handle_generic_error` | `def handle_generic_error(error: Exception, context: str = "Unknown") -> HTTPException` |
| `validate_priority` | `def validate_priority(priority: str) -> None` |
| `create_error_response` | `def create_error_response( error_code: QueueErrorCode, message: str, details: dict[str, Any] \| None = None ) -> dict[str, Any]` |

**`src/core/resource_monitor.py`** — Lightweight resource monitor for gateway state streaming.

| Class | Signature |
|---|---|
| `ResourceMonitor` | `class ResourceMonitor` |
| `.__init__` | `def __init__(self, event_bus: EventBus, gateway_name: str)` |
| `.get_current_state` | `async def get_current_state(self) -> dict[str, ResourceState]` |
| `.get_snapshot` | `async def get_snapshot(self) -> dict[str, Any]` |
| `.subscribe_to_updates` | `async def subscribe_to_updates(self, channel) -> None` |
| `.unsubscribe_from_updates` | `async def unsubscribe_from_updates(self, channel) -> None` |
| `._setup_event_handlers` | `def _setup_event_handlers(self) -> None` |
| `._on_model_loaded` | `async def _on_model_loaded(self, event: Event) -> None` |
| `._on_model_unloaded` | `async def _on_model_unloaded(self, event: Event) -> None` |
| … | (17 methods total) |

**`src/core/synthetic_models.py`** — Synthetic Model ID Generation and Resolution

| Class | Signature |
|---|---|
| `SyntheticModelResolver` | `class SyntheticModelResolver` |

### `src/core/batching`

**`src/core/batching/__init__.py`** — Request batching system for Universal LLM Gateway Phase 2B Enables processing multiple requests in single inference pass for improved GPU utilization.

**`src/core/batching/batch_manager.py`** — Batch Manager for Universal LLM Gateway Phase 2B Handles batching of multiple inference requests simultaneously.

| Class | Signature |
|---|---|
| `BatchManager` | `class BatchManager` |
| `.__init__` | `def __init__( self, max_batch_size: int = 8, max_wait_time: float = 2.0, enable_batching: bool = True, )` |
| `.process_request` | `async def process_request( self, model_id: str, messages: list[dict[str, str]], max_tokens: int = 512, temperature: float = 0.7, top_p: float = 0.9, stop: list[str] \| None = None, stream: bool = False, ) -> dict[str, Any]` |
| `._batch_processor` | `async def _batch_processor(self, model_id: str)` |
| `._process_batch` | `async def _process_batch(self, batch: Batch)` |
| `._are_requests_compatible` | `def _are_requests_compatible(self, requests: list[BatchRequest]) -> bool` |
| `._execute_batch_inference` | `async def _execute_batch_inference(self, batch: Batch) -> list[dict[str, Any]]` |
| `._process_single_request` | `async def _process_single_request(self, request: BatchRequest) -> dict[str, Any]` |
| `.get_stats` | `def get_stats(self) -> dict[str, Any]` |
| … | (9 methods total) |

**`src/core/batching/scheduler.py`** — Batch Scheduler for Universal LLM Gateway Phase 2B Handles scheduling and prioritization of batched requests.

| Class | Signature |
|---|---|
| `RequestPriority` | `class RequestPriority(Enum)` |
| `BatchScheduler` | `class BatchScheduler` |
| `.__init__` | `def __init__( self, max_queue_size: int = 1000, default_timeout: float = 30.0, enable_priority_scheduling: bool = True, )` |
| `.schedule_request` | `async def schedule_request( self, model_id: str, request_data: dict[str, Any], priority: RequestPriority = RequestPriority.NORMAL, estimated_tokens: int \| None = None, timeout: float \| None = None, ) -> bool` |
| `._handle_request_timeout` | `async def _handle_request_timeout(self, request: ScheduledRequest) -> None` |
| `.get_next_batch` | `async def get_next_batch( self, model_id: str, max_batch_size: int = 8, max_tokens_per_batch: int = 4096 ) -> list[dict[str, Any]]` |
| `._estimate_tokens` | `def _estimate_tokens(self, request_data: dict[str, Any]) -> int` |
| `.get_queue_status` | `def get_queue_status(self) -> dict[str, Any]` |
| `.get_stats` | `def get_stats(self) -> dict[str, Any]` |
| `.shutdown` | `async def shutdown(self)` |

### `src/core/caching`

**`src/core/caching/__init__.py`** — Intelligent caching system for Universal LLM Gateway Phase 2B Provides response caching for improved performance and reduced GPU utilization.

**`src/core/caching/cache_manager.py`** — Cache Manager for Universal LLM Gateway Phase 2B Handles intelligent caching of LLM responses for improved performance.

| Class | Signature |
|---|---|
| `CacheManager` | `class CacheManager` |
| `.__init__` | `def __init__( self, max_cache_size: int = 10000, max_memory_mb: int = 512, default_ttl: int = 3600, # 1 hour enable_semantic_caching: bool = True, similarity_threshold: float = 0.95, )` |
| `._generate_cache_key` | `def _generate_cache_key( self, model_id: str, messages: list[dict[str, str]], parameters: dict[str, Any] ) -> str` |
| `._generate_prompt_hash` | `def _generate_prompt_hash(self, messages: list[dict[str, str]]) -> str` |
| `.get` | `async def get( self, model_id: str, messages: list[dict[str, str]], parameters: dict[str, Any] ) -> dict[str, Any] \| None` |
| `.put` | `async def put( self, model_id: str, messages: list[dict[str, str]], parameters: dict[str, Any], response: dict[str, Any], )` |
| `._find_similar_cached_response` | `async def _find_similar_cached_response( self, model_id: str, messages: list[dict[str, str]], parameters: dict[str, Any] ) -> dict[str, Any] \| None` |
| `._calculate_text_similarity` | `def _calculate_text_similarity(self, text1: str, text2: str) -> float` |
| `._are_parameters_similar` | `def _are_parameters_similar( self, params1: dict[str, Any], params2: dict[str, Any] ) -> bool` |
| … | (20 methods total) |

### `src/core/catalog`

**`src/core/catalog/__init__.py`** — Catalog Module - Model catalog loading, validation, and conversion.

| Class | Signature |
|---|---|
| `CatalogLoader` | `class CatalogLoader(_CatalogLoaderBase)` |
| `.to_model_loaders_format` | `def to_model_loaders_format(self, model_id: str) -> dict[str, Any] \| None` |
| `.get_all_models_as_loaders_format` | `def get_all_models_as_loaders_format(self) -> dict[str, Any]` |

| Function | Signature |
|---|---|
| `get_catalog_loader` | `def get_catalog_loader() -> CatalogLoader` |

**`src/core/catalog/constants.py`** — Catalog Constants - Schema Definitions.

**`src/core/catalog/convert.py`** — Catalog Conversion - Schema-driven conversion to registry format.

| Function | Signature |
|---|---|
| `to_model_loaders_format` | `def to_model_loaders_format( model_id: str, get_model_fn: Callable[[str], dict[str, Any] \| None], ) -> dict[str, Any] \| None` |
| `get_all_models_as_loaders_format` | `def get_all_models_as_loaders_format( catalog: dict[str, Any], get_model_fn: Callable[[str], dict[str, Any] \| None], ) -> dict[str, Any]` |

**`src/core/catalog/loading.py`** — Catalog Loading - V3 Schema with Static + Local Catalog Merge.

| Class | Signature |
|---|---|
| `CatalogDiscovery` | `class CatalogDiscovery` |
| `.__init__` | `def __init__(self, catalog_dir: Path) -> None` |
| `.discover_model_files` | `def discover_model_files(self) -> dict[str, Path]` |
| `.load_model_from_file` | `def load_model_from_file(self, model_id: str, file_path: Path) -> dict[str, Any]` |
| `.load_catalog` | `def load_catalog(self) -> dict[str, Any]` |
| `CatalogLoader` | `class CatalogLoader` |
| `.__init__` | `def __init__(self, catalog_dir: Path \| None = None) -> None` |
| `._detect_catalog_dir` | `def _detect_catalog_dir(self) -> Path` |
| `.load` | `def load(self) -> dict[str, Any]` |
| `.reload` | `def reload(self) -> None` |
| `.get_model` | `def get_model(self, model_id: str) -> dict[str, Any] \| None` |
| `.list_models` | `def list_models(self) -> list[str]` |
| `.get_model_metadata` | `def get_model_metadata(self, model_id: str) -> dict[str, Any] \| None` |
| `.get_model_download` | `def get_model_download(self, model_id: str) -> dict[str, Any] \| None` |
| … | (9 methods total) |

**`src/core/catalog/local.py`** — Local Catalog Discovery - reads ~/.gateway/catalog/ (or GATEWAY_LOCAL_CATALOG_DIR).

| Function | Signature |
|---|---|
| `get_local_catalog_dir` | `def get_local_catalog_dir() -> Path \| None` |
| `_has_yaml_files` | `def _has_yaml_files(directory: Path) -> bool` |
| `_load_entry` | `def _load_entry(yaml_file: Path) -> dict[str, Any] \| None` |
| `_validate_entry` | `def _validate_entry(model_id: str, entry: dict[str, Any]) -> bool` |
| `discover_local_models` | `def discover_local_models() -> dict[str, Any]` |

**`src/core/catalog/validation.py`** — Catalog Validation - Schema-driven validation.

| Function | Signature |
|---|---|
| `validate_model` | `def validate_model( model_id: str, entry: dict[str, Any], ) -> list[ValidationIssue]` |
| `validate_model_by_id` | `def validate_model_by_id( model_id: str, get_model_fn: Callable[[str], dict[str, Any] \| None], ) -> list[ValidationIssue]` |
| `validate_catalog` | `def validate_catalog(catalog: dict[str, Any]) -> list[ValidationIssue]` |
| `log_validation_report` | `def log_validation_report(catalog: dict[str, Any]) -> int` |
| `get_validation_summary` | `def get_validation_summary(catalog: dict[str, Any]) -> dict[str, Any]` |

**`src/core/catalog/vram_reconciliation.py`** — Local catalog VRAM reconciliation for measured model loads.

| Function | Signature |
|---|---|
| `_resolve_target_profile` | `def _resolve_target_profile( entry: dict[str, Any], canonical_model_id: str, measured_vram_mb: int, ) -> tuple[str, str] \| None` |
| `reconcile_max_observed_vram` | `def reconcile_max_observed_vram( model_id: str, measured_vram_mb: int, measured_ram_mb: int, ) -> bool` |

### `src/core/catalog/schemas`

**`src/core/catalog/schemas/__init__.py`** — Schema Registry - Central registry for engine schemas.

| Class | Signature |
|---|---|
| `SchemaRegistry` | `class SchemaRegistry` |

**`src/core/catalog/schemas/cross_encoder.py`** — CrossEncoder Engine Schema — cross-encoder reranker models via sentence_transformers.

| Class | Signature |
|---|---|
| `CrossEncoderSchema` | `class CrossEncoderSchema(BaseEngineSchema)` |
| `.get_default_loader` | `def get_default_loader(self) -> dict[str, Any]` |
| `.get_device_loader_defaults` | `def get_device_loader_defaults(self, device: str) -> dict[str, Any]` |
| `.validate` | `def validate( self, model_id: str, entry: dict[str, Any], ) -> list[ValidationIssue]` |
| `.convert` | `def convert( self, model_id: str, entry: dict[str, Any], ) -> ConvertedModel \| None` |

**`src/core/catalog/schemas/ctranslate2.py`** — CTranslate2 Schema - Translation models via CTranslate2 engine.

| Class | Signature |
|---|---|
| `CTranslate2Schema` | `class CTranslate2Schema(BaseEngineSchema)` |
| `.get_default_loader` | `def get_default_loader(self) -> dict[str, Any]` |
| `.get_device_loader_defaults` | `def get_device_loader_defaults(self, device: str) -> dict[str, Any]` |
| `.validate` | `def validate(self, model_id: str, entry: dict[str, Any]) -> list[ValidationIssue]` |
| `.convert` | `def convert(self, model_id: str, entry: dict[str, Any]) -> ConvertedModel \| None` |

**`src/core/catalog/schemas/diffusers.py`** — Diffusers Engine Schema - Flux.2 models via diffusers.

| Class | Signature |
|---|---|
| `DiffusersSchema` | `class DiffusersSchema(BaseEngineSchema)` |
| `.get_default_loader` | `def get_default_loader(self) -> dict[str, Any]` |
| `.validate` | `def validate( self, model_id: str, entry: dict[str, Any], ) -> list[ValidationIssue]` |
| `.convert` | `def convert( self, model_id: str, entry: dict[str, Any], ) -> ConvertedModel \| None` |

**`src/core/catalog/schemas/exllamav3.py`** — ExLlamaV3 Engine Schema - EXL3 models via ExLlamaV3.

| Class | Signature |
|---|---|
| `ExllamaV3Schema` | `class ExllamaV3Schema(BaseEngineSchema)` |
| `.get_default_loader` | `def get_default_loader(self) -> dict[str, Any]` |
| `.validate` | `def validate( self, model_id: str, entry: dict[str, Any], ) -> list[ValidationIssue]` |
| `.convert` | `def convert( self, model_id: str, entry: dict[str, Any], ) -> ConvertedModel \| None` |

**`src/core/catalog/schemas/faster_whisper.py`** — FasterWhisper Engine Schema - Whisper models via faster-whisper.

| Class | Signature |
|---|---|
| `FasterWhisperSchema` | `class FasterWhisperSchema(BaseEngineSchema)` |
| `.get_default_loader` | `def get_default_loader(self) -> dict[str, Any]` |
| `.get_device_loader_defaults` | `def get_device_loader_defaults(self, device: str) -> dict[str, Any]` |
| `.validate` | `def validate( self, model_id: str, entry: dict[str, Any], ) -> list[ValidationIssue]` |
| `.convert` | `def convert( self, model_id: str, entry: dict[str, Any], ) -> ConvertedModel \| None` |

**`src/core/catalog/schemas/llama_cpp.py`** — LlamaCpp Engine Schema - GGUF models via NativeGGUFEngine (llama-server).

| Class | Signature |
|---|---|
| `LlamaCppSchema` | `class LlamaCppSchema(BaseEngineSchema)` |
| `.get_default_loader` | `def get_default_loader(self) -> dict[str, Any]` |
| `.get_device_loader_defaults` | `def get_device_loader_defaults(self, device: str) -> dict[str, Any]` |
| `._validate_parallel_slots` | `def _validate_parallel_slots( self, model_id: str, device_name: str, profile_key: str, profile: dict[str, Any], issues: list[ValidationIssue], ) -> None` |
| `.validate` | `def validate( self, model_id: str, entry: dict[str, Any], ) -> list[ValidationIssue]` |
| `.convert` | `def convert( self, model_id: str, entry: dict[str, Any], ) -> ConvertedModel \| None` |

**`src/core/catalog/schemas/schema.py`** — Base Engine Schema - Abstract interface for engine-specific catalog handling.

| Class | Signature |
|---|---|
| `BaseEngineSchema` | `class BaseEngineSchema(ABC)` |
| `.get_device_loader_defaults` | `def get_device_loader_defaults(self, device: str) -> dict[str, Any]` |
| `.supports_device` | `def supports_device(self, device: str) -> bool` |
| `._validate_common` | `def _validate_common( self, model_id: str, entry: dict[str, Any], ) -> list[ValidationIssue]` |
| `._validate_profiles` | `def _validate_profiles( self, model_id: str, device_name: str, device_config: dict[str, Any], ) -> list[ValidationIssue]` |
| `._build_info` | `def _build_info( self, model_id: str, metadata: dict[str, Any], download: dict[str, Any], ) -> dict[str, Any]` |

**`src/core/catalog/schemas/types.py`** — Schema Types - Data classes for catalog schema validation and conversion.

**`src/core/catalog/schemas/vllm.py`** — vLLM Engine Schema - HF/AWQ/GPTQ models via vLLM.

| Class | Signature |
|---|---|
| `VllmSchema` | `class VllmSchema(BaseEngineSchema)` |
| `.get_default_loader` | `def get_default_loader(self) -> dict[str, Any]` |
| `._get_valid_loader_params` | `def _get_valid_loader_params(self) -> set[str] \| None` |
| `.validate` | `def validate( self, model_id: str, entry: dict[str, Any], ) -> list[ValidationIssue]` |
| `.convert` | `def convert( self, model_id: str, entry: dict[str, Any], ) -> ConvertedModel \| None` |

### `src/core/catalog/split`

**`src/core/catalog/split/__init__.py`** — Catalog split utilities for domain-based file operations.

**`src/core/catalog/split/mapping.py`** — Schema-to-path mapping for catalog split.

| Function | Signature |
|---|---|
| `determine_model_path` | `def determine_model_path(model_id: str, model_entry: dict[str, Any]) -> str` |

**`src/core/catalog/split/writer.py`** — YAML file writer for catalog split with atomic writes.

| Class | Signature |
|---|---|
| `IndentDumper` | `class IndentDumper(yaml.SafeDumper)` |
| `.increase_indent` | `def increase_indent(self, flow=False, indentless=False)` |

| Function | Signature |
|---|---|
| `str_representer` | `def str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.Node` |
| `write_model_file` | `def write_model_file(output_path: Path, model_entry: dict[str, Any]) -> None` |

### `src/core/config_manager`

**`src/core/config_manager/__init__.py`** — Configuration manager for model_loaders.yaml with validation and atomic writes.

**`src/core/config_manager/crud.py`** — CRUD mixin for ConfigManager model entries.

| Class | Signature |
|---|---|
| `ConfigCRUDMixin` | `class ConfigCRUDMixin` |
| `.upsert_model` | `def upsert_model( self, model_key: str, model_config: dict[str, Any], *, allow_path_deduplication: bool = True, allow_key_overwrite: bool = True, ) -> ModelOperationResult` |
| `.get_model` | `def get_model(self, model_key: str) -> dict[str, Any] \| None` |
| `.delete_model` | `def delete_model(self, model_key: str) -> None` |
| `.list_models` | `def list_models(self) -> dict[str, dict[str, Any]]` |

**`src/core/config_manager/io.py`** — Load/write/reload I/O mixin for ConfigManager.

| Class | Signature |
|---|---|
| `ConfigIOMixin` | `class ConfigIOMixin` |
| `.load_config` | `def load_config(self) -> dict[str, Any]` |
| `.load_and_validate` | `def load_and_validate(self) -> dict[str, Any]` |
| `.write_config` | `def write_config(self, config: dict[str, Any], validate: bool = True) -> None` |
| `._normalize_path` | `def _normalize_path(self, path: str) -> str` |
| `._sort_profiles_numerically` | `def _sort_profiles_numerically(self, model_config: dict[str, Any]) -> None` |
| `.get_config_version` | `def get_config_version(self) -> float` |
| `.reload` | `def reload(self, app_state: Any = None) -> set[str]` |
| `.get_example` | `def get_example(self, format_type: str) -> dict[str, Any]` |
| … | (9 methods total) |

**`src/core/config_manager/manager.py`** — ConfigManager facade for model_loaders.yaml lifecycle.

| Class | Signature |
|---|---|
| `ConfigManager` | `class ConfigManager(ConfigIOMixin, ConfigValidationMixin, ConfigCRUDMixin)` |
| `.__init__` | `def __init__(self, config_path: str \| Path = "config/model_loaders.yaml")` |

**`src/core/config_manager/merge.py`** — Deep-merge helpers for model_loaders.yaml configuration updates.

| Function | Signature |
|---|---|
| `_is_type_transition_allowed` | `def _is_type_transition_allowed(existing: Any, new: Any) -> bool` |
| `deep_merge_dict` | `def deep_merge_dict(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]` |

**`src/core/config_manager/types.py`** — Config-manager result and error types.

| Class | Signature |
|---|---|
| `ConfigValidationError` | `class ConfigValidationError(Exception)` |
| `ValidationContext` | `class ValidationContext(Enum)` |

**`src/core/config_manager/validation.py`** — Validation and normalization mixin for ConfigManager.

| Class | Signature |
|---|---|
| `ConfigValidationMixin` | `class ConfigValidationMixin` |
| `.validate_config` | `def validate_config( self, config: dict[str, Any], context: ValidationContext = ValidationContext.NEW, exclude_model_keys: set[str] \| None = None, validate_only: set[str] \| None = None, ) -> None` |
| `._validate_model_config` | `def _validate_model_config( self, model_key: str, model_config: dict[str, Any] ) -> None` |
| `._normalize_model_config` | `def _normalize_model_config( self, model_config: dict[str, Any], format_type: str ) -> dict[str, Any]` |
| `._validate_unique_openai_ids` | `def _validate_unique_openai_ids( self, models: dict[str, Any], exclude_keys: set[str] \| None = None ) -> None` |
| `._validate_unique_paths` | `def _validate_unique_paths( self, models: dict[str, Any], exclude_keys: set[str] \| None = None ) -> None` |

### `src/core/events`

**`src/core/events/__init__.py`** — Event-driven architecture support for Universal LLM Gateway.

| Function | Signature |
|---|---|
| `get_event_bus` | `def get_event_bus() -> EventBus` |
| `set_event_bus` | `def set_event_bus(event_bus: EventBus) -> None` |

**`src/core/events/aggregation.py`** — Event Aggregation - Compute insights and metrics from event streams.

| Class | Signature |
|---|---|
| `EventAggregator` | `class EventAggregator` |
| `.__init__` | `def __init__(self, event_bus, track_resource_history: bool = True)` |
| `._subscribe_to_events` | `def _subscribe_to_events(self)` |
| `._handle_model_loading_started` | `def _handle_model_loading_started(self, event)` |
| `._handle_model_loaded` | `def _handle_model_loaded(self, event)` |
| `._handle_model_load_failed` | `def _handle_model_load_failed(self, event)` |
| `._handle_model_unloading_started` | `def _handle_model_unloading_started(self, event)` |
| `._handle_model_unloaded` | `def _handle_model_unloaded(self, event)` |
| `._handle_inference_started` | `def _handle_inference_started(self, event)` |
| … | (20 methods total) |

**`src/core/events/crash_handlers.py`** — Event handlers for worker crash events from process_ipc integration.

| Class | Signature |
|---|---|
| `CentralizedCrashEventHandler` | `class CentralizedCrashEventHandler` |
| `.__init__` | `def __init__(self, event_bus, enable_auto_recovery: bool = False)` |
| `.setup_subscriptions` | `def setup_subscriptions(self) -> None` |
| `._handle_crash_with_recovery` | `def _handle_crash_with_recovery(self, event: Event) -> None` |
| `._handle_socket_orphaned_with_stats` | `def _handle_socket_orphaned_with_stats(self, event: Event) -> None` |
| `._handle_health_failure_with_diagnostics` | `def _handle_health_failure_with_diagnostics(self, event: Event) -> None` |
| `.get_crash_statistics` | `def get_crash_statistics(self) -> dict[str, Any]` |
| `.reset_statistics` | `def reset_statistics(self) -> None` |

| Function | Signature |
|---|---|
| `handle_worker_crash` | `def handle_worker_crash(event: Event) -> None` |
| `handle_socket_orphaned` | `def handle_socket_orphaned(event: Event) -> None` |
| `handle_health_check_failed` | `def handle_health_check_failed(event: Event) -> None` |

**`src/core/events/filtering.py`** — Event Filtering - Predicate-based event filtering for selective subscriptions.

| Class | Signature |
|---|---|
| `EventFilter` | `class EventFilter` |
| `.__init__` | `def __init__(self, predicate: Callable[[Any], bool])` |
| `.matches` | `def matches(self, event: Any) -> bool` |
| `.and_predicate` | `def and_predicate(self, other_predicate: Callable[[Any], bool]) -> "EventFilter"` |
| `.and_filter` | `def and_filter(self, other_filter: "EventFilter") -> "EventFilter"` |
| `.and_attribute` | `def and_attribute(self, attribute: str, value: Any) -> "EventFilter"` |
| `.or_predicate` | `def or_predicate(self, other_predicate: Callable[[Any], bool]) -> "EventFilter"` |
| `.or_filter` | `def or_filter(self, other_filter: "EventFilter") -> "EventFilter"` |
| `.or_attribute` | `def or_attribute(self, attribute: str, value: Any) -> "EventFilter"` |
| … | (9 methods total) |
| `FilteredEventBus` | `class FilteredEventBus` |
| `.__init__` | `def __init__(self, event_bus)` |
| `.subscribe_filtered` | `def subscribe_filtered( self, signal: str, handler: Callable[[Any], None], event_filter: EventFilter )` |
| `.publish` | `async def publish(self, event: Any)` |

**`src/core/events/measurement.py`** — Measurement event signals and factories.

**`src/core/events/persistence.py`** — Event Persistence - SQLite-based event storage for historical analysis.

| Class | Signature |
|---|---|
| `EventStore` | `class EventStore` |
| `.__init__` | `def __init__( self, db_path: str = "/tmp/llm_gateway_events.db", max_events: int \| None = 100000, auto_cleanup_days: int \| None = 7, )` |
| `._init_database` | `def _init_database(self)` |
| `.store_event` | `def store_event(self, event: Any) -> bool` |
| `.query_events` | `def query_events( self, event_type: str \| None = None, since: float \| None = None, until: float \| None = None, limit: int \| None = 1000, offset: int = 0, ) -> list[dict[str, Any]]` |
| `.count_events` | `def count_events( self, event_type: str \| None = None, since: float \| None = None, until: float \| None = None, ) -> int` |
| `.get_event_types` | `def get_event_types(self) -> list[str]` |
| `.cleanup_old_events` | `def cleanup_old_events(self, max_age_days: int) -> int` |
| `._auto_cleanup` | `def _auto_cleanup(self)` |
| … | (12 methods total) |
| `EventStoreSubscriber` | `class EventStoreSubscriber` |
| `.__init__` | `def __init__( self, event_bus, event_store: EventStore, event_signals: list[str] \| None = None, )` |
| `._handle_event` | `def _handle_event(self, event: Any)` |

**`src/core/events/resource_monitoring.py`** — Resource Monitoring - Non-blocking resource tracking during inference.

| Class | Signature |
|---|---|
| `AsyncResourceMonitor` | `class AsyncResourceMonitor` |
| `.__init__` | `def __init__(self, event_store=None)` |
| `.start_monitoring` | `async def start_monitoring( self, model_id: str, request_id: str, worker_config: dict[str, Any], worker_pid: int \| None = None, monitoring_interval: float = 1.0, )` |
| `.stop_monitoring` | `async def stop_monitoring(self, model_id: str)` |
| `._monitor_resources` | `async def _monitor_resources( self, model_id: str, request_id: str, worker_config: dict[str, Any], worker_pid: int \| None, monitoring_interval: float, )` |
| `._background_storage` | `async def _background_storage(self)` |
| `._store_snapshot_async` | `async def _store_snapshot_async(self, snapshot: InferenceResourceSnapshot)` |
| `._get_worker_resources_fast` | `async def _get_worker_resources_fast( self, worker_pid: int \| None ) -> tuple[int, int]` |
| `._get_worker_resources_sync` | `def _get_worker_resources_sync(self, worker_pid: int \| None) -> tuple[int, int]` |
| … | (16 methods total) |

### `src/core/events/persistence_async`

**`src/core/events/persistence_async/__init__.py`** — Non-blocking SQLite event persistence with automatic cleanup and query support.

**`src/core/events/persistence_async/store.py`** — Non-blocking AsyncEventStore for SQLite-backed historical event persistence.

| Class | Signature |
|---|---|
| `AsyncEventStore` | `class AsyncEventStore` |
| `.__init__` | `def __init__( self, db_path: str = "/tmp/llm_gateway_events.db", max_events: int \| None = 100000, auto_cleanup_days: int \| None = 7, )` |
| `.store_event` | `async def store_event(self, event: Any) -> bool` |
| `.query_events` | `async def query_events( self, event_type: str \| None = None, since: float \| None = None, until: float \| None = None, limit: int \| None = 1000, offset: int = 0, ) -> list[dict[str, Any]]` |
| `.count_events` | `async def count_events( self, event_type: str \| None = None, since: float \| None = None, until: float \| None = None, ) -> int` |
| `.get_event_types` | `async def get_event_types(self) -> list[str]` |
| `.cleanup_old_events` | `async def cleanup_old_events(self, max_age_days: int) -> int` |
| `._auto_cleanup_async` | `async def _auto_cleanup_async(self)` |
| `._enforce_max_events_async` | `async def _enforce_max_events_async(self)` |
| … | (11 methods total) |

**`src/core/events/persistence_async/store_sync.py`** — Synchronous SQLite operations for AsyncEventStore thread-pool execution.

| Function | Signature |
|---|---|
| `init_database` | `def init_database(db_path: Path) -> None` |
| `store_event_sync` | `def store_event_sync( db_path: Path, timestamp: float, event_type: str, event_json: str ) -> None` |
| `query_events_sync` | `def query_events_sync( db_path: Path, event_type: str \| None, since: float \| None, until: float \| None, limit: int \| None, offset: int, ) -> list[dict[str, Any]]` |
| `count_events_sync` | `def count_events_sync( db_path: Path, event_type: str \| None, since: float \| None, until: float \| None, ) -> int` |
| `get_event_types_sync` | `def get_event_types_sync(db_path: Path) -> list[str]` |
| `cleanup_old_events_sync` | `def cleanup_old_events_sync(db_path: Path, max_age_days: int) -> int` |
| `get_event_count_sync` | `def get_event_count_sync(db_path: Path) -> int` |
| `delete_oldest_events_sync` | `def delete_oldest_events_sync(db_path: Path, to_delete: int) -> None` |
| `clear_all_events_sync` | `def clear_all_events_sync(db_path: Path) -> int` |
| `get_stats_sync` | `def get_stats_sync(db_path: Path) -> dict[str, Any]` |

**`src/core/events/persistence_async/subscriber.py`** — EventBus subscriber that persists matching signals into AsyncEventStore.

| Class | Signature |
|---|---|
| `AsyncEventStoreSubscriber` | `class AsyncEventStoreSubscriber` |
| `.__init__` | `def __init__( self, event_bus, event_store: AsyncEventStore, event_signals: list[str] \| None = None, )` |
| `._handle_event` | `async def _handle_event(self, event: Any)` |

### `src/core/events/types`

**`src/core/events/types/__init__.py`** — LLM-specific event signals and factories for Universal LLM Gateway.

**`src/core/events/types/compute_capacity.py`** — Compute-capacity queue telemetry event signals and factories.

**`src/core/events/types/configuration.py`** — Catalog reload and snapshot gap event signals and factories.

**`src/core/events/types/gateway_lifecycle.py`** — Gateway lifecycle and VRAM reconciler event signals and factories.

**`src/core/events/types/inference.py`** — Inference and stream lifecycle event signals and factories.

**`src/core/events/types/model_lifecycle.py`** — Model load/unload event signals and factories for the gateway.

**`src/core/events/types/system_resources.py`** — System and per-inference resource telemetry event signals and factories.

**`src/core/events/types/worker.py`** — Worker process health and crash event signals and factories.

### `src/core/hot_reload`

**`src/core/hot_reload/__init__.py`** — Hot reload functionality for YAML configuration files.

**`src/core/hot_reload/reload.py`** — Configuration reload logic for hot reload functionality.

| Class | Signature |
|---|---|
| `ConfigReloader` | `class ConfigReloader` |
| `.__init__` | `def __init__( self, model_registry, allowed_paths: list[str], max_file_size_mb: int, )` |
| `.execute_reload` | `async def execute_reload(self, file_path: str) -> ReloadEvent` |
| `._extract_model_key` | `def _extract_model_key(self, file_path: Path) -> str` |
| `._validate_path_allowed` | `def _validate_path_allowed(self, file_path: Path)` |
| `._validate_file_size` | `def _validate_file_size(self, file_path: Path)` |
| `._parse_config_file` | `def _parse_config_file(self, file_path: Path) -> dict[str, Any]` |
| `._validate_model_config` | `def _validate_model_config(self, config_data: dict[str, Any])` |
| `._update_model_in_memory` | `async def _update_model_in_memory(self, model_key: str, new_config: dict[str, Any])` |
| … | (11 methods total) |

**`src/core/hot_reload/types.py`** — Type definitions for hot reload functionality.

| Class | Signature |
|---|---|
| `ErrorType` | `class ErrorType(StrEnum)` |

**`src/core/hot_reload/watcher.py`** — Gateway-specific hot reload manager.

| Class | Signature |
|---|---|
| `HotReloadManager` | `class HotReloadManager` |
| `.__init__` | `def __init__( self, config_manager, # Unused, kept for API compatibility model_registry, watch_directory: str \| Path = "config", debounce_ms: int = 500, recursive: bool = True, supported_formats: list[str] \| None = None, allowed_paths: list[str] \| None = None, max_file_size_mb: int = 10, log_level: str = "info", # Unused, kept for API compatibility )` |
| `.start` | `async def start(self) -> bool` |
| `.stop` | `async def stop(self)` |
| `._on_file_change` | `async def _on_file_change(self, file_path: str)` |
| `.add_reload_callback` | `def add_reload_callback(self, callback)` |
| `.queue_reload` | `async def queue_reload(self, file_path: str)` |
| `._reload_worker` | `async def _reload_worker(self, file_path: str)` |
| `._record_change` | `def _record_change(self, event: ReloadEvent)` |
| … | (12 methods total) |

### `src/core/loaders`

**`src/core/loaders/__init__.py`** — Loaders package for Universal LLM Gateway.

### `src/core/metrics`

**`src/core/metrics/__init__.py`** — Metrics collection for the Universal LLM Gateway.

### `src/core/metrics/state_channel_metrics`

**`src/core/metrics/state_channel_metrics/__init__.py`** — Metrics collection for state channel monitoring — package-shadow of state_channel_metrics.py.

**`src/core/metrics/state_channel_metrics/channel_metrics.py`** — Per-connection metric record and operation-tracking decorator for state channels.

| Function | Signature |
|---|---|
| `track_operation` | `def track_operation(operation_name: str)` |

**`src/core/metrics/state_channel_metrics/collector.py`** — State channel metrics collector public API and process lifecycle management.

| Class | Signature |
|---|---|
| `StateChannelMetricsCollector` | `class StateChannelMetricsCollector(MetricQueueProcessor, MetricEventHandlers)` |
| `.__init__` | `def __init__(self)` |
| `.on_connection` | `async def on_connection( self, client_id: str, auth_info: dict[str, Any] \| None = None ) -> ChannelMetrics` |
| `.on_disconnection` | `async def on_disconnection(self, client_id: str)` |
| `.on_message_received` | `async def on_message_received(self, client_id: str, message_size: int)` |
| `.on_message_sent` | `async def on_message_sent( self, client_id: str, message_type: str, message_size: int )` |
| `.on_subscription` | `async def on_subscription(self, client_id: str, pattern: str)` |
| `.on_error` | `async def on_error(self, client_id: str, error_type: str)` |
| `.on_resource_action` | `async def on_resource_action(self, client_id: str, action: str)` |
| … | (14 methods total) |

**`src/core/metrics/state_channel_metrics/event_handlers.py`** — Synchronous metric event handlers for state channel connection lifecycle events.

| Class | Signature |
|---|---|
| `MetricEventHandlers` | `class MetricEventHandlers` |
| `._validate_client` | `def _validate_client(self, client_id: str) -> bool` |
| `._record_timestamp` | `def _record_timestamp(self, timestamp_list: deque, event_type: str)` |
| `._update_channel_metric` | `def _update_channel_metric(self, client_id: str, field: str, value: Any)` |
| `._handle_on_subscription` | `def _handle_on_subscription(self, client_id: str, pattern: str)` |
| `._handle_on_error` | `def _handle_on_error(self, client_id: str, error_type: str)` |
| `._handle_on_resource_action` | `def _handle_on_resource_action(self, client_id: str, action: str)` |
| `._handle_get_active_connections` | `def _handle_get_active_connections(self) -> int` |
| `._handle_get_metrics_summary` | `def _handle_get_metrics_summary(self) -> dict[str, Any]` |
| … | (9 methods total) |

**`src/core/metrics/state_channel_metrics/queue_processor.py`** — Async queue processor mixin for non-blocking state channel metrics collection.

| Class | Signature |
|---|---|
| `MetricQueueProcessor` | `class MetricQueueProcessor` |
| `._ensure_processor` | `async def _ensure_processor(self)` |
| `._submit` | `async def _submit( self, handler: Callable, *args, expects_result: bool = False, **kwargs )` |
| `._process_queue` | `async def _process_queue(self)` |
| `._periodic_cleanup` | `async def _periodic_cleanup(self)` |
| `._drain_queue` | `async def _drain_queue(self)` |

### `src/core/model_registry`

**`src/core/model_registry/__init__.py`** — Model registry package for managing model metadata and validation

**`src/core/model_registry/metadata.py`** — Model metadata definitions and base extraction logic

| Class | Signature |
|---|---|
| `MetadataExtractor` | `class MetadataExtractor` |
| `.__init__` | `def __init__(self): # Import complete inspector functions from inference_djinn` |
| `.extract_metadata` | `def extract_metadata( self, model_id: str, model_data: dict[str, Any] ) -> ModelMetadata` |
| `._extract_metadata_from_djinn_summary` | `def _extract_metadata_from_djinn_summary( self, djinn_summary: dict[str, Any] ) -> tuple[int \| None, str \| None, bool]` |

### `src/core/model_registry/extractors`

**`src/core/model_registry/extractors/__init__.py`** — Format-specific metadata extractors - API proxy only (other formats use djinn inspectors)

**`src/core/model_registry/extractors/api_proxy.py`** — API proxy metadata extraction

| Class | Signature |
|---|---|
| `APIMetadataExtractor` | `class APIMetadataExtractor(BaseMetadataExtractor)` |
| `.extract` | `def extract( self, model_path: str, model_data: dict[str, Any] ) -> tuple[int \| None, str \| None, bool]` |

### `src/core/model_registry/registry`

**`src/core/model_registry/registry/__init__.py`** — Model registry package — re-exports ModelRegistry and normalize_model_id.

**`src/core/model_registry/registry/availability.py`** — Model file validation, availability reporting, and path availability tracking.

| Class | Signature |
|---|---|
| `AvailabilityMixin` | `class AvailabilityMixin` |
| `.validate_model_files` | `def validate_model_files(self, fast_mode: bool = True) -> ModelValidationReport` |
| `.check_model_availability` | `def check_model_availability(self, fast_mode: bool = True) -> ModelValidationReport` |
| `.apply_availability_report` | `def apply_availability_report(self, report: ModelValidationReport) -> None` |
| `.is_model_path_available` | `def is_model_path_available(self, model_id: str) -> bool` |

**`src/core/model_registry/registry/catalog.py`** — Catalog key lookup and raw model configuration path resolution from YAML.

| Class | Signature |
|---|---|
| `CatalogMixin` | `class CatalogMixin` |
| `.find_config_key_for_openai_id` | `def find_config_key_for_openai_id( self, model_id: str, _seen: set[str] \| None = None ) -> str \| None` |
| `.get_model_config` | `def get_model_config(self, model_id: str) -> dict[str, Any] \| None` |
| `.get_model_path` | `def get_model_path(self, model_id: str) -> str \| None` |

**`src/core/model_registry/registry/core.py`** — Main model registry class composing catalog, loader, and availability mixins.

| Class | Signature |
|---|---|
| `ModelRegistry` | `class ModelRegistry( IdentityMixin, CatalogMixin, InfoMixin, ListingsMixin, ResourcesMixin, LoaderMixin, AvailabilityMixin, LoadedModelsMixin, )` |
| `.__init__` | `def __init__(self, model_loaders_config: dict[str, Any])` |
| `.__repr__` | `def __repr__(self) -> str` |

**`src/core/model_registry/registry/identifiers.py`** — Model ID normalization, suffix validation, and synthetic ID resolution helpers.

| Class | Signature |
|---|---|
| `IdentityMixin` | `class IdentityMixin` |
| `._resolve_synthetic_id_info` | `def _resolve_synthetic_id_info( self, model_id: str ) -> tuple[str, int, bool, bool] \| None` |
| `.resolve_model_id` | `def resolve_model_id( self, model_id: str ) -> tuple[str, dict[str, Any] \| None, str \| None]` |

| Function | Signature |
|---|---|
| `normalize_model_id` | `def normalize_model_id(model_id: str) -> str` |
| `_validate_model_id_suffixes` | `def _validate_model_id_suffixes(model_id: str) -> str \| None` |

**`src/core/model_registry/registry/info.py`** — ModelInfo construction, enablement checks, and catalog count statistics.

| Class | Signature |
|---|---|
| `InfoMixin` | `class InfoMixin` |
| `.get_model_info` | `def get_model_info(self, model_id: str) -> ModelInfo \| None` |
| `.get_model_metadata` | `def get_model_metadata(self, model_id: str) -> ModelMetadata \| None` |
| `.is_model_enabled` | `def is_model_enabled(self, model_id: str) -> bool` |
| `.get_model_count` | `def get_model_count(self) -> dict[str, int]` |

**`src/core/model_registry/registry/listings.py`** — Synthetic model listing and enumeration with hardware-aware filtering.

| Class | Signature |
|---|---|
| `ListingsMixin` | `class ListingsMixin` |
| `.get_available_synthetic_model_ids` | `def get_available_synthetic_model_ids( self, enabled_only: bool = True, available_only: bool = True ) -> list[str]` |
| `.list_models` | `def list_models(self, enabled_only: bool = False) -> list[ModelInfo]` |

**`src/core/model_registry/registry/loaded.py`** — Runtime tracking of loaded model instances keyed by canonical model ID.

| Class | Signature |
|---|---|
| `LoadedModelsMixin` | `class LoadedModelsMixin` |
| `.register_loaded_model` | `def register_loaded_model(self, model_id: str, model_instance: Any) -> None` |
| `.unregister_loaded_model` | `def unregister_loaded_model(self, model_id: str) -> None` |
| `.is_model_loaded` | `def is_model_loaded(self, model_id: str) -> bool` |
| `.get_loaded_model` | `def get_loaded_model(self, model_id: str) -> Any \| None` |

**`src/core/model_registry/registry/loaders.py`** — Profile loader selection and merged loader configuration for model workers.

| Class | Signature |
|---|---|
| `LoaderMixin` | `class LoaderMixin` |
| `.get_model_loader_config` | `def get_model_loader_config( self, model_id: str, requested_ctx: int \| None = None ) -> dict[str, Any] \| None` |

**`src/core/model_registry/registry/resources.py`** — VRAM and RAM resource requirements plus token limit queries from profiles.

| Class | Signature |
|---|---|
| `ResourcesMixin` | `class ResourcesMixin` |
| `.get_model_resources` | `def get_model_resources(self, model_id: str) -> dict[str, int] \| None` |
| `.get_model_max_tokens` | `def get_model_max_tokens( self, model_id: str, requested_ctx: int \| None = None ) -> int \| None` |
| `.get_model_limits` | `def get_model_limits( self, model_id: str, requested_ctx: int \| None = None ) -> dict[str, int] \| None` |

### `src/core/model_registry/validation`

**`src/core/model_registry/validation/__init__.py`** — Model validation logic for files, profiles, and catalog metadata correctness.

**`src/core/model_registry/validation/file_validation.py`** — Model file existence and readability validation for enabled catalog entries.

| Function | Signature |
|---|---|
| `validate_model_files` | `def validate_model_files( models: dict[str, Any], fast_mode: bool = True ) -> ModelValidationReport` |
| `validate_single_model` | `def validate_single_model( model_id: str, metadata: Any, fast_mode: bool = True ) -> ModelValidationResult` |

**`src/core/model_registry/validation/metadata_validation.py`** — V2 catalog metadata validation for quant values, devices, and activated contexts.

| Function | Signature |
|---|---|
| `validate_catalog_metadata` | `def validate_catalog_metadata(catalog: dict[str, Any]) -> list[MetadataIssue]` |
| `validate_quant_consistency` | `def validate_quant_consistency( model_id: str, model_name: str, quant: Any ) -> list[MetadataIssue]` |
| `log_metadata_validation_results` | `def log_metadata_validation_results(issues: list[MetadataIssue]) -> None` |

**`src/core/model_registry/validation/profile_validation.py`** — Profile resource section validation for model loader YAML configurations.

| Function | Signature |
|---|---|
| `validate_profile_resources` | `def validate_profile_resources(models_config: dict[str, Any]) -> list[ProfileIssue]` |
| `log_profile_validation_results` | `def log_profile_validation_results(issues: list[ProfileIssue]) -> None` |

**`src/core/model_registry/validation/types.py`** — Validation issue dataclasses for model file, profile, and metadata checks.

**`src/core/model_registry/validation/validator.py`** — ModelValidator facade delegating to file, profile, and metadata validation modules.

| Class | Signature |
|---|---|
| `ModelValidator` | `class ModelValidator` |

### `src/core/resources`

**`src/core/resources/__init__.py`** — Resource tracking package.

| Function | Signature |
|---|---|
| `__getattr__` | `def __getattr__(name: str)` |

**`src/core/resources/cleanup.py`** — Cleanup operations for resource tracking.

| Function | Signature |
|---|---|
| `cleanup_stale_models` | `def cleanup_stale_models(tracker: "ResourceTracker") -> list[str]` |

**`src/core/resources/events.py`** — Event emission helpers for resource tracking.

| Function | Signature |
|---|---|
| `_resolve_gateway_identity` | `def _resolve_gateway_identity() -> str` |
| `emit_inference_started` | `async def emit_inference_started( event_bus: Any, model_id: str, request_id: str = "" ) -> None` |
| `emit_inference_completed` | `async def emit_inference_completed( event_bus: Any, model_id: str, last_inference_time: float ) -> None` |

**`src/core/resources/hardware.py`** — Hardware resource queries via pynvml and psutil.

| Function | Signature |
|---|---|
| `get_vram_info` | `def get_vram_info() -> dict[str, int]` |
| `get_ram_info` | `def get_ram_info() -> dict[str, int]` |
| `_collect_process_tree_pids` | `def _collect_process_tree_pids(pid: int) -> set[int]` |
| `get_process_gpu_memory` | `def get_process_gpu_memory(pid: int) -> int \| None` |
| `get_process_ram_usage` | `def get_process_ram_usage(pid: int) -> int \| None` |
| `pid_exists` | `def pid_exists(pid: int) -> bool` |

**`src/core/resources/queries.py`** — Config-based resource queries and estimation.

| Function | Signature |
|---|---|
| `get_model_resources_from_config` | `def get_model_resources_from_config(model_id: str) -> tuple[int \| None, int \| None]` |
| `detect_quantization` | `def detect_quantization(model_id: str) -> str \| None` |
| `estimate_context_length` | `def estimate_context_length(model_id: str) -> int \| None` |
| `get_model_requirements` | `def get_model_requirements(model_id: str) -> dict[str, Any]` |

**`src/core/resources/tracker.py`** — Core resource tracker for model VRAM/RAM and inference state.

| Class | Signature |
|---|---|
| `ResourceTracker` | `class ResourceTracker( _ResourceTrackerMixinModelStatus, _ResourceTrackerMixinUnloadAndInference, )` |
| `.__init__` | `def __init__(self, event_bus: Any \| None = None)` |
| `.set_event_bus` | `def set_event_bus(self, event_bus: Any) -> None` |
| `._initialize_system_resources` | `def _initialize_system_resources(self) -> None` |
| `._emit_state_change` | `def _emit_state_change( self, model_id: str \| ModelId, from_status: ModelStatus, to_status: ModelStatus, error_message: str \| None, ) -> None` |
| `._on_sm_transition` | `def _on_sm_transition( self, key: str, model_id: str, from_state: WorkerState, to_state: WorkerState, reason: str, metadata: dict \| None, ) -> None` |
| `.register_model` | `def register_model(self, model_id: str \| ModelId) -> None` |
| `.unregister_model` | `def unregister_model(self, model_id: str \| ModelId) -> None` |
| `.is_process_in_use` | `def is_process_in_use(self, model_id: str \| ModelId) -> bool` |
| … | (25 methods total) |

**`src/core/resources/tracker_keys.py`** — Tracking-key helpers shared by ResourceTracker split modules.

| Function | Signature |
|---|---|
| `_tracking_key` | `def _tracking_key(model_id: str \| ModelId) -> str` |
| `_process_key` | `def _process_key(model_id: str \| ModelId) -> str` |

**`src/core/resources/tracker_mixin_model_status.py`** — ResourceTracker mixin — model load paths, busy/idle, and error surfaces.

| Class | Signature |
|---|---|
| `_ResourceTrackerMixinModelStatus` | `class _ResourceTrackerMixinModelStatus: # ------------------------------------------------------------------------- # Model Status Management # -------------------------------------------------------------------------` |
| `.set_model_loading` | `def set_model_loading(self, model_id: str \| ModelId) -> bool` |
| `.set_model_loaded` | `def set_model_loaded( self, model_id: str \| ModelId, process_pid: int \| None = None ) -> None` |
| `.set_model_busy` | `async def set_model_busy( self, model_id: str \| ModelId, request_id: str = "" ) -> None` |
| `.set_model_idle` | `async def set_model_idle(self, model_id: str \| ModelId) -> None` |
| `.set_model_inference_state` | `def set_model_inference_state( self, model_id: str \| ModelId, inference_state: str ) -> None` |
| `.set_model_error` | `def set_model_error(self, model_id: str \| ModelId, error_message: str) -> None` |
| `.get_model_error` | `def get_model_error(self, model_id: str \| ModelId) -> str \| None` |

**`src/core/resources/tracker_mixin_unload_inference.py`** — ResourceTracker mixin — unload, force-idle, and inference context manager.

| Class | Signature |
|---|---|
| `_ResourceTrackerMixinUnloadAndInference` | `class _ResourceTrackerMixinUnloadAndInference` |
| `.set_model_unloading` | `def set_model_unloading(self, model_id: str \| ModelId) -> None` |
| `.set_model_not_loaded` | `def set_model_not_loaded(self, model_id: str \| ModelId, reason: str) -> None` |
| `.force_model_idle` | `async def force_model_idle(self, model_id: str \| ModelId, reason: str) -> bool` |

**`src/core/resources/tracker_queries.py`** — Runtime query functions for ResourceTracker.

| Function | Signature |
|---|---|
| `refresh_hardware_vram_probe` | `def refresh_hardware_vram_probe(tracker: ResourceTracker) -> dict[str, int]` |
| `_tracking_key` | `def _tracking_key(model_id: str \| ModelId) -> str` |
| `get_model_info` | `def get_model_info( tracker: ResourceTracker, model_id: str \| ModelId ) -> ModelResourceInfo \| None` |
| `get_all_models_info` | `def get_all_models_info(tracker: ResourceTracker) -> dict[str, ModelResourceInfo]` |
| `get_loaded_models` | `def get_loaded_models(tracker: ResourceTracker) -> list[str]` |
| `get_busy_models` | `def get_busy_models(tracker: ResourceTracker) -> list[str]` |
| `get_operations_in_progress` | `def get_operations_in_progress(tracker: ResourceTracker) -> dict[str, list[str]]` |
| `get_state_machine_status` | `def get_state_machine_status( tracker: ResourceTracker, model_id: str \| ModelId ) -> dict[str, Any] \| None` |
| `get_system_resources` | `async def get_system_resources(tracker: ResourceTracker) -> SystemResourceInfo` |
| `get_model_requirements_wrapper` | `def get_model_requirements_wrapper(model_id: str \| ModelId) -> dict[str, Any]` |
| `get_model_resources_from_config_wrapper` | `def get_model_resources_from_config_wrapper( model_id: str \| ModelId, ) -> tuple[int \| None, int \| None]` |

**`src/core/resources/transitions.py`** — State transition helpers for model lifecycle management.

| Function | Signature |
|---|---|
| `handle_error_state_recovery` | `def handle_error_state_recovery( state_machines: dict[str, WorkerStateMachine], models: dict[str, ModelResourceInfo], key: str, model_id: str, ) -> None` |
| `transition_to_loading` | `def transition_to_loading( state_machines: dict[str, WorkerStateMachine], key: str, model_id: str, ) -> bool` |
| `transition_to_idle` | `def transition_to_idle( state_machines: dict[str, WorkerStateMachine], key: str, model_id: str, ) -> None` |
| `_handle_failed_idle_transition` | `def _handle_failed_idle_transition( sm: WorkerStateMachine, key: str, model_id: str ) -> None` |
| `update_model_idle_status_async` | `async def update_model_idle_status_async( models: dict[str, ModelResourceInfo], key: str, model_id: str, event_bus: "EventBus", ) -> None` |

**`src/core/resources/types.py`** — Resource tracking data types.

| Class | Signature |
|---|---|
| `ModelStatus` | `class ModelStatus(Enum)` |

| Function | Signature |
|---|---|
| `_status_for_worker_state` | `def _status_for_worker_state(state: WorkerState) -> ModelStatus` |

**`src/core/resources/variant_registry.py`** — Registry mapping physical worker processes to their model ID variants.

| Class | Signature |
|---|---|
| `VariantRegistry` | `class VariantRegistry` |
| `.__init__` | `def __init__(self) -> None` |
| `.register` | `def register(self, process_key: str, tracking_key: str) -> None` |
| `.unregister` | `def unregister(self, tracking_key: str) -> None` |
| `.get_variants` | `def get_variants(self, process_key: str) -> frozenset[str]` |
| `.get_process_key` | `def get_process_key(self, tracking_key: str) -> str \| None` |
| `.has_variant` | `def has_variant(self, tracking_key: str) -> bool` |
| `.is_process_in_use` | `def is_process_in_use( self, process_key: str, state_machines: dict, busy_states: frozenset, ) -> bool` |
| `.describe_busy_variants` | `def describe_busy_variants( self, process_key: str, state_machines: dict, busy_states: frozenset, ) -> list[str]` |

### `src/core/resources/vram_reconciler`

**`src/core/resources/vram_reconciler/__init__.py`** — Periodic VRAM reconciliation for phantom-model and ghost-model detection.

**`src/core/resources/vram_reconciler/constants.py`** — Timing and threshold constants for periodic VRAM reconciliation sweeps.

**`src/core/resources/vram_reconciler/ghost_sweep.py`** — Ghost model sweep for tracked models whose inference engine subprocess died.

| Class | Signature |
|---|---|
| `GhostSweepMixin` | `class GhostSweepMixin` |
| `._sweep_ghost_models` | `async def _sweep_ghost_models( self, tracked_models: set[str], running_processes: dict[str, int], ) -> None` |
| `._cleanup_ghost_model` | `async def _cleanup_ghost_model(self, model_id: str, pid: int \| None) -> None` |
| `._clear_ghost_from_tracker` | `def _clear_ghost_from_tracker(self, model_id: str) -> None` |
| `._emit_model_unloaded` | `async def _emit_model_unloaded(self, model_id: str) -> None` |
| `._emit_ghost_cleaned` | `async def _emit_ghost_cleaned( self, model_id: str, success: bool, vram_freed_mb: int \| None ) -> None` |

**`src/core/resources/vram_reconciler/gpu_process.py`** — GPU process scanning and last-resort worker process termination.

| Class | Signature |
|---|---|
| `GpuProcessMixin` | `class GpuProcessMixin` |
| `._kill_worker_process` | `async def _kill_worker_process(self, model_id: str, pid: int) -> bool` |
| `._get_known_pids` | `def _get_known_pids(self) -> set[int]` |
| `._scan_gpu_processes` | `async def _scan_gpu_processes(self) -> list[dict[str, int]]` |
| `._scan_gpu_processes_sync` | `def _scan_gpu_processes_sync(self, known_pids: set[int]) -> list[dict[str, int]]` |

**`src/core/resources/vram_reconciler/loop.py`** — Periodic reconciliation loop lifecycle and single-pass orchestration.

| Class | Signature |
|---|---|
| `ReconcileLoopMixin` | `class ReconcileLoopMixin` |
| `.start` | `async def start(self) -> None` |
| `.stop` | `async def stop(self) -> None` |
| `._reconcile_loop` | `async def _reconcile_loop(self) -> None` |
| `._reconcile_once` | `async def _reconcile_once(self) -> None` |

**`src/core/resources/vram_reconciler/phantom_cleanup.py`** — Phantom model detection events and force-unload cleanup for orphan workers.

| Class | Signature |
|---|---|
| `PhantomCleanupMixin` | `class PhantomCleanupMixin` |
| `._emit_phantom_detected` | `async def _emit_phantom_detected( self, model_id: str, tracker_status: str \| None ) -> None` |
| `._force_cleanup` | `async def _force_cleanup(self, model_id: str, pid: int \| None) -> None` |
| `._emit_phantom_cleaned` | `async def _emit_phantom_cleaned( self, model_id: str, success: bool, vram_freed_mb: int \| None ) -> None` |

**`src/core/resources/vram_reconciler/protocols.py`** — Structural typing protocols for VramReconciler dependency injection.

| Class | Signature |
|---|---|
| `ResourceTrackerProto` | `class ResourceTrackerProto(Protocol)` |
| `.get_loaded_models` | `def get_loaded_models(self) -> list[str]` |
| `.get_system_resources` | `async def get_system_resources(self) -> dict[str, Any]` |
| `.set_model_not_loaded` | `def set_model_not_loaded(self, model_id: str, reason: str) -> None` |
| `UnloadResultProto` | `class UnloadResultProto(Protocol)` |
| `WorkerControllerProto` | `class WorkerControllerProto(Protocol)` |
| `.get_running_worker_processes` | `def get_running_worker_processes(self) -> dict[str, int]` |
| `.get_engine_pid` | `def get_engine_pid(self, model_id: str) -> int \| None` |
| `.check_engine_health` | `async def check_engine_health(self, model_id: str) -> bool` |
| `.unload_model` | `async def unload_model( self, model_id: str, force: bool = False ) -> UnloadResultProto` |
| `EventBusProto` | `class EventBusProto(Protocol)` |
| `.publish_nowait` | `async def publish_nowait(self, event: object) -> None` |

**`src/core/resources/vram_reconciler/reconciler.py`** — VramReconciler class composing periodic VRAM reconciliation concern mixins.

| Class | Signature |
|---|---|
| `VramReconciler` | `class VramReconciler( ReconcileLoopMixin, GhostSweepMixin, PhantomCleanupMixin, VramDiscrepancyMixin, GpuProcessMixin, TrackerStateMixin, )` |
| `.__init__` | `def __init__( self, resource_tracker: ResourceTrackerProto, worker_controller: WorkerControllerProto, event_bus: EventBusProto \| None = None, ) -> None` |

**`src/core/resources/vram_reconciler/tracker_state.py`** — Shared resource-tracker and engine subprocess state queries for reconciliation.

| Class | Signature |
|---|---|
| `TrackerStateMixin` | `class TrackerStateMixin` |
| `._get_tracker_status` | `def _get_tracker_status(self, model_id: str) -> str \| None` |
| `._get_transitioning_models` | `def _get_transitioning_models(self) -> set[str]` |
| `._get_tracked_vram` | `def _get_tracked_vram(self, model_id: str) -> int` |

**`src/core/resources/vram_reconciler/vram_discrepancy.py`** — Bidirectional VRAM accounting discrepancy detection and event emission.

| Class | Signature |
|---|---|
| `VramDiscrepancyMixin` | `class VramDiscrepancyMixin` |
| `._check_vram_discrepancy` | `async def _check_vram_discrepancy(self, tracked_models: set[str]) -> None` |
| `._emit_vram_orphan` | `async def _emit_vram_orphan( self, hardware_used: int, tracked_used: int, discrepancy: int, tracked_models: list[str], ) -> None` |
| `._emit_vram_staleness` | `async def _emit_vram_staleness( self, hardware_used: int, tracked_used: int, discrepancy: int, tracked_models: list[str], ) -> None` |

### `src/core/websocket`

**`src/core/websocket/__init__.py`** — WebSocket module for Stargate control plane.

**`src/core/websocket/connection_manager.py`** — WebSocket connection manager for Stargate clients.

| Class | Signature |
|---|---|
| `StargateConnectionManager` | `class StargateConnectionManager` |
| `.__init__` | `def __init__(self, ping_interval: float = 30.0)` |
| `.initialize_heartbeat` | `def initialize_heartbeat( self, gateway_id: str, interval_seconds: float = 30.0 ) -> None` |
| `.initialize_resource_telemetry` | `def initialize_resource_telemetry( self, resource_tracker: "ResourceTracker", interval_seconds: float = 5.0 ) -> None` |
| `.connect` | `async def connect(self, websocket: WebSocket) -> None` |
| `.disconnect` | `async def disconnect(self, websocket: WebSocket) -> None` |
| `.send_message` | `async def send_message( self, websocket: WebSocket, message: WebSocketMessage ) -> bool` |
| `.broadcast` | `async def broadcast(self, message: WebSocketMessage) -> int` |
| `.start_ping_loop` | `async def start_ping_loop(self, websocket: WebSocket) -> None` |
| … | (12 methods total) |

| Function | Signature |
|---|---|
| `get_connection_manager` | `def get_connection_manager() -> StargateConnectionManager` |

**`src/core/websocket/heartbeat.py`** — Periodic telemetry heartbeat publisher for WebSocket connections.

| Class | Signature |
|---|---|
| `TelemetryHeartbeatPublisher` | `class TelemetryHeartbeatPublisher` |
| `.__init__` | `def __init__( self, gateway_id: str, interval_seconds: float = 30.0, )` |
| `.register_connection` | `def register_connection(self, connection: Any) -> None` |
| `.unregister_connection` | `def unregister_connection(self, connection: Any) -> None` |
| `.start` | `async def start(self) -> None` |
| `.stop` | `async def stop(self) -> None` |
| `._heartbeat_loop` | `async def _heartbeat_loop(self) -> None` |

**`src/core/websocket/init_cache.py`** — Cached INIT data provider to avoid sync I/O on WebSocket connect.

| Class | Signature |
|---|---|
| `InitDataCache` | `class InitDataCache` |
| `.__init__` | `def __init__( self, model_registry: ModelRegistry, worker_controller: Any, event_bus: EventBus \| None = None, *, gateway_config: GatewayConfig, )` |
| `.refresh` | `async def refresh(self) -> None` |
| `._emit_resource_gap` | `async def _emit_resource_gap( self, all_models_count: int, resource_models_count: int, gap_cause: str, sample_missing: list[str], ) -> None` |
| `._handle_catalog_reload` | `async def _handle_catalog_reload(self, event: Event) -> None` |
| `.cleanup` | `def cleanup(self) -> None` |
| `.get_init_data` | `async def get_init_data(self) -> dict[str, Any]` |
| `.get_resources` | `def get_resources(self) -> dict[str, Any]` |
| `._get_all_model_ids` | `def _get_all_model_ids(self) -> list[str]` |
| … | (12 methods total) |

**`src/core/websocket/messages.py`** — WebSocket message types for Stargate control plane.

| Class | Signature |
|---|---|
| `MessageType` | `class MessageType(StrEnum)` |

| Function | Signature |
|---|---|
| `create_init_message` | `def create_init_message( version: str, gateway_name: str, models: list[str], loaded_models: list[str], catalog: dict[str, Any], resources: dict[str, Any], ) -> WebSocketMessage` |
| `create_model_loading_started_message` | `def create_model_loading_started_message(model_id: str) -> WebSocketMessage` |
| `create_model_loading_progress_message` | `def create_model_loading_progress_message( model_id: str, phase: str, pct: int \| float ) -> WebSocketMessage` |
| `create_model_loaded_message` | `def create_model_loaded_message( model_id: str, vram_mb: int, ram_mb: int, context_length: int \| None = None, ) -> WebSocketMessage` |
| `create_model_load_failed_message` | `def create_model_load_failed_message( model_id: str, error_message: str, worker_snapshot: dict[str, Any] \| None = None, ) -> WebSocketMessage` |
| `create_model_unloaded_message` | `def create_model_unloaded_message(model_id: str) -> WebSocketMessage` |
| `create_model_busy_message` | `def create_model_busy_message(model_id: str) -> WebSocketMessage` |
| `create_model_idle_message` | `def create_model_idle_message( model_id: str, last_inference_time: float ) -> WebSocketMessage` |
| `create_resource_update_message` | `def create_resource_update_message( available_vram_mb: int, available_ram_mb: int, total_vram_mb: int \| None = None, total_ram_mb: int \| None = None, model_vram: dict[str, int] \| None = None, ) -> WebSocketMessage` |
| `create_ping_message` | `def create_ping_message() -> WebSocketMessage` |
| `create_catalog_update_message` | `def create_catalog_update_message( reason: str, models: list[str] \| None = None, catalog: dict[str, Any] \| None = None, ) -> WebSocketMessage` |
| `create_error_message` | `def create_error_message(code: str, message: str) -> WebSocketMessage` |
| `create_gateway_shutdown_message` | `def create_gateway_shutdown_message( gateway_id: str, reason: str, timestamp: float ) -> WebSocketMessage` |
| `create_gateway_draining_message` | `def create_gateway_draining_message( gateway_id: str, reason: str, timeout: float, timestamp: float ) -> WebSocketMessage` |
| `create_telemetry_heartbeat_message` | `def create_telemetry_heartbeat_message(gateway_id: str) -> WebSocketMessage` |
| `create_compute_queue_wait_message` | `def create_compute_queue_wait_message( request_id: str, model_id: str, compute_type: str, queue_position: int, active_count: int, limit: int, timestamp_ms: int, ) -> WebSocketMessage` |
| `create_compute_queue_acquired_message` | `def create_compute_queue_acquired_message( request_id: str, model_id: str, compute_type: str, wait_duration_ms: float, queue_position_at_enqueue: int, timestamp_ms: int, ) -> WebSocketMessage` |
| `create_request_inference_started_message` | `def create_request_inference_started_message( request_id: str, model_id: str, gateway_url: str, correlation_id: str \| None = None, ) -> WebSocketMessage` |
| `create_vram_orphan_detected_message` | `def create_vram_orphan_detected_message( hardware_used_mb: int, catalog_used_mb: int, discrepancy_mb: int, tracked_models: list[str], ) -> WebSocketMessage` |
| `create_vram_staleness_detected_message` | `def create_vram_staleness_detected_message( hardware_used_mb: int, catalog_used_mb: int, discrepancy_mb: int, tracked_models: list[str], ) -> WebSocketMessage` |
| `create_phantom_model_detected_message` | `def create_phantom_model_detected_message( model_id: str, process_status: str, tracker_status: str \| None, ) -> WebSocketMessage` |
| `create_phantom_model_cleaned_message` | `def create_phantom_model_cleaned_message( model_id: str, success: bool, vram_freed_mb: int \| None, ) -> WebSocketMessage` |

**`src/core/websocket/resource_telemetry.py`** — Periodic resource telemetry publisher for WebSocket connections.

| Class | Signature |
|---|---|
| `ResourceTelemetryPublisher` | `class ResourceTelemetryPublisher` |
| `.__init__` | `def __init__( self, resource_tracker: "ResourceTracker", interval_seconds: float = 5.0, )` |
| `.start` | `async def start(self) -> None` |
| `.stop` | `async def stop(self) -> None` |
| `._publish_loop` | `async def _publish_loop(self) -> None` |

### `src/core/websocket/event_forwarder`

**`src/core/websocket/event_forwarder/__init__.py`** — Forward EventBus events to WebSocket clients — package-shadow of event_forwarder.py.

**`src/core/websocket/event_forwarder/forwarder.py`** — WebSocketEventForwarder — subscribes to EventBus and broadcasts to Stargate clients.

| Class | Signature |
|---|---|
| `WebSocketEventForwarder` | `class WebSocketEventForwarder` |
| `.__init__` | `def __init__( self, event_bus: EventBus, connection_manager: StargateConnectionManager, init_cache: InitDataCache \| None = None, )` |
| `.start` | `def start(self) -> None` |
| `.stop` | `def stop(self) -> None` |
| `._handle_event` | `async def _handle_event(self, event: Event) -> None` |
| `._process_and_broadcast` | `async def _process_and_broadcast(self, event: Event) -> None` |
| `._broadcast_with_logging` | `async def _broadcast_with_logging( self, message: WebSocketMessage, signal: str ) -> None` |
| `._event_to_message` | `async def _event_to_message(self, event: Event) -> WebSocketMessage \| None` |

**`src/core/websocket/event_forwarder/message_builders.py`** — WebSocket message builders mapping EventBus payloads to wire-format messages.

| Function | Signature |
|---|---|
| `build_sync_message` | `def build_sync_message(signal: str, payload: dict[str, Any]) -> WebSocketMessage \| None` |
| `build_async_message` | `async def build_async_message( signal: str, payload: dict[str, Any], init_cache: InitDataCache \| None, ) -> WebSocketMessage \| None` |
| `build_request_inference_started_message` | `def build_request_inference_started_message( payload: dict[str, Any], ) -> WebSocketMessage \| None` |
| `build_system_resources_update_message` | `def build_system_resources_update_message( payload: dict[str, Any], ) -> WebSocketMessage` |
| `build_catalog_update_message` | `async def build_catalog_update_message( payload: dict[str, Any], init_cache: InitDataCache \| None, ) -> WebSocketMessage` |
| `build_vram_orphan_detected_message` | `def build_vram_orphan_detected_message(payload: dict[str, Any]) -> WebSocketMessage` |
| `build_vram_staleness_detected_message` | `def build_vram_staleness_detected_message(payload: dict[str, Any]) -> WebSocketMessage` |
| `build_phantom_model_detected_message` | `def build_phantom_model_detected_message(payload: dict[str, Any]) -> WebSocketMessage` |
| `build_phantom_model_cleaned_message` | `def build_phantom_model_cleaned_message(payload: dict[str, Any]) -> WebSocketMessage` |

### `src/core/workers`

**`src/core/workers/__init__.py`** — Workers module for Universal LLM Gateway.

| Function | Signature |
|---|---|
| `get_worker_controller` | `def get_worker_controller() -> WorkerController \| None` |
| `set_worker_controller` | `def set_worker_controller(controller: WorkerController) -> None` |

**`src/core/workers/engine_factory.py`** — Engine factory for creating and loading model engines.

| Class | Signature |
|---|---|
| `EngineInitializationError` | `class EngineInitializationError(Exception)` |
| `.__init__` | `def __init__(self, engine_type: str, error: Exception, context: dict[str, Any])` |
| `EngineTimeoutError` | `class EngineTimeoutError(EngineInitializationError)` |
| `.__init__` | `def __init__(self, engine_type: str, operation: str, timeout: float)` |
| `EngineFactory` | `class EngineFactory` |

**`src/core/workers/exceptions.py`** — Worker-specific exceptions.

| Class | Signature |
|---|---|
| `WorkerError` | `class WorkerError(Exception)` |
| `WorkerStartupError` | `class WorkerStartupError(WorkerError)` |
| `WorkerInitializationError` | `class WorkerInitializationError(WorkerError)` |
| `ModelLoadingError` | `class ModelLoadingError(WorkerError)` |
| `WorkerCommunicationError` | `class WorkerCommunicationError(WorkerError)` |
| `WorkerTimeoutError` | `class WorkerTimeoutError(WorkerError)` |
| `WorkerHealthError` | `class WorkerHealthError(WorkerError)` |
| `WorkerShutdownError` | `class WorkerShutdownError(WorkerError)` |
| `ModelNotFoundError` | `class ModelNotFoundError(WorkerError)` |
| `UnsupportedModelFormatError` | `class UnsupportedModelFormatError(WorkerError)` |
| `GPUMemoryError` | `class GPUMemoryError(WorkerError)` |

**`src/core/workers/manager.py`** — Simple process manager using process_ipc directly.

| Class | Signature |
|---|---|
| `StreamErrorCode` | `class StreamErrorCode` |

**`src/core/workers/orphan_detector.py`** — Orphaned socket detection service.

| Class | Signature |
|---|---|
| `OrphanedSocketDetector` | `class OrphanedSocketDetector` |
| `.__init__` | `def __init__(self, worker_controller)` |
| `.find_orphaned_sockets` | `async def find_orphaned_sockets( self, socket_dir: Path ) -> list[tuple[str, Path, str]]` |
| `._is_socket_orphaned` | `async def _is_socket_orphaned(self, model_id: str, socket_file: Path) -> str \| None` |
| `._is_socket_accepting_connections` | `def _is_socket_accepting_connections(self, socket_file: Path) -> bool` |
| `.get_socket_stats` | `def get_socket_stats(self, socket_dir: Path) -> dict` |

**`src/core/workers/process_crash_bridge.py`** — Bridge handler for translating process_ipc crash events to gateway crash events.

| Class | Signature |
|---|---|
| `ProcessCrashBridge` | `class ProcessCrashBridge` |
| `.__init__` | `def __init__(self, event_bus: EventBus)` |
| `._handle_process_crash_detected` | `async def _handle_process_crash_detected(self, event: Event)` |
| `.get_bridge_count` | `def get_bridge_count(self) -> int` |

**`src/core/workers/resource_tracker_crash_handler.py`** — Event-driven resource tracker crash handler.

| Class | Signature |
|---|---|
| `ResourceTrackerCrashHandler` | `class ResourceTrackerCrashHandler` |
| `.__init__` | `def __init__(self, event_bus: EventBus, worker_controller=None)` |
| `._handle_worker_crash` | `async def _handle_worker_crash(self, event: Event)` |
| `.get_crash_count` | `def get_crash_count(self) -> int` |

| Function | Signature |
|---|---|
| `_classify_crash_error` | `def _classify_crash_error( error_message: str, exit_code: int \| None, ) -> tuple[str, str]` |

**`src/core/workers/socket_cleanup_handler.py`** — Event-driven socket cleanup handler.

| Class | Signature |
|---|---|
| `SocketCleanupHandler` | `class SocketCleanupHandler` |
| `.__init__` | `def __init__(self, event_bus: EventBus, socket_dir: Path, worker_controller)` |
| `._handle_worker_crash` | `async def _handle_worker_crash(self, event: Event)` |

**`src/core/workers/socket_utils.py`** — Socket utilities for safe file operations.

| Function | Signature |
|---|---|
| `safe_delete_socket` | `def safe_delete_socket(socket_path: Path) -> bool` |
| `is_socket_in_use` | `def is_socket_in_use(socket_path: Path) -> bool` |

**`src/core/workers/state_machine.py`** — Worker state machine with transition guards and audit logging.

| Class | Signature |
|---|---|
| `WorkerState` | `class WorkerState(Enum)` |
| `WorkerStateMachine` | `class WorkerStateMachine` |
| `.__init__` | `def __init__( self, worker_id: str, initial_state: WorkerState = WorkerState.UNINITIALIZED, on_transition: TransitionCallback \| None = None, )` |
| `._notify` | `def _notify( self, from_state: WorkerState, to_state: WorkerState, reason: str, metadata: dict \| None, ) -> None` |
| `.transition` | `def transition( self, to_state: WorkerState, reason: str, guard: Callable[[], bool] \| None = None, metadata: dict \| None = None, ) -> bool` |
| `._record_transition` | `def _record_transition( self, *, from_state: WorkerState, to_state: WorkerState, reason: str, metadata: dict \| None = None, forced: bool = False, ) -> None` |
| `.force_idle` | `def force_idle(self, reason: str) -> bool` |
| `.set_error` | `def set_error(self, error_message: str) -> bool` |
| `.clear_error` | `def clear_error(self, reason: str = "Error cleared") -> bool` |
| `.force_unloaded` | `def force_unloaded(self, reason: str) -> None` |
| … | (11 methods total) |

**`src/core/workers/stream_cancellation_handler.py`** — Event-driven stream cancellation handler.

| Class | Signature |
|---|---|
| `StreamCancellationHandler` | `class StreamCancellationHandler` |
| `.__init__` | `def __init__(self, event_bus: EventBus)` |
| `._handle_stream_cancellation` | `async def _handle_stream_cancellation(self, event: Event)` |
| `.get_cancellation_count` | `def get_cancellation_count(self) -> int` |

**`src/core/workers/utils.py`** — Worker utilities and helper functions.

| Function | Signature |
|---|---|
| `validate_worker_dependencies` | `def validate_worker_dependencies(python_executable: str) -> tuple[bool, list[str]]` |
| `capture_subprocess_output` | `def capture_subprocess_output( command: list[str], env: dict[str, str], cwd: str \| None = None, timeout: float = 30.0, ) -> tuple[int, str, str]` |
| `diagnose_worker_startup_failure` | `def diagnose_worker_startup_failure( command: list[str], env: dict[str, str], cwd: str \| None = None ) -> dict[str, Any]` |
| `check_process_health` | `def check_process_health(pid: int) -> bool` |
| `find_worker_processes` | `def find_worker_processes(model_id: str) -> list` |
| `cleanup_socket_file` | `def cleanup_socket_file(socket_path: str) -> bool` |
| `get_python_executable` | `def get_python_executable( gateway_config: Any, explicit_python: str \| None = None ) -> str` |
| `create_worker_environment` | `def create_worker_environment(model_id: str, python_executable: str) -> dict[str, str]` |
| `format_worker_command` | `def format_worker_command( python_executable: str, entrypoint: "WorkerEntrypoint", model_id: str, socket_path: str, log_file: str, log_level: str = "DEBUG", idle_timeout: float \| None = None, ) -> list` |
| `get_universal_protocol_socket_path` | `def get_universal_protocol_socket_path(model_id: str) -> str` |
| `is_model_active` | `def is_model_active(status) -> bool` |

**`src/core/workers/worker_logging.py`** — Worker logging setup - minimal module without auto-initialization triggers.

| Function | Signature |
|---|---|
| `setup_worker_logging` | `def setup_worker_logging(level: str = "INFO", log_file: str \| None = None) -> None` |

**`src/core/workers/worker_types.py`** — Worker-specific types and data structures.

| Class | Signature |
|---|---|
| `WorkerStatus` | `class WorkerStatus(Enum)` |
| `ModelFormat` | `class ModelFormat(Enum)` |

### `src/core/workers/cancellation`

**`src/core/workers/cancellation/__init__.py`** — Stream cancellation operations.

**`src/core/workers/cancellation/emission.py`** — Event emission helpers for stream cancellation.

| Function | Signature |
|---|---|
| `_get_resource_tracker` | `def _get_resource_tracker()` |
| `_get_event_classes` | `def _get_event_classes()` |
| `build_stream_cancelled_event` | `def build_stream_cancelled_event( model_id: str, stream_id: str \| None, reason: str, source: str = "worker_controller", )` |
| `emit_stream_cancelled_nowait` | `async def emit_stream_cancelled_nowait(event, event_bus) -> None` |
| `fallback_force_idle_on_event_failure` | `async def fallback_force_idle_on_event_failure(model_id: str, reason: str) -> None` |
| `emit_stream_cancelled_or_force_idle` | `async def emit_stream_cancelled_or_force_idle( model_id: str, stream_id: str \| None, reason: str, *, event_bus, source: str = "worker_controller", ) -> None` |

### `src/core/workers/chat_completion`

**`src/core/workers/chat_completion/__init__.py`** — Chat completion module for WorkerController.

**`src/core/workers/chat_completion/non_streaming.py`** — Non-streaming chat completion operations.

| Class | Signature |
|---|---|
| `NonStreamingChatCompletion` | `class NonStreamingChatCompletion` |
| `.__init__` | `def __init__(self, controller: "WorkerController")` |
| `.inference` | `async def inference( self, model_id: str, messages: list \| str, parameters: dict, correlation_id: str \| None = None, ) -> dict` |
| `._log_inference_error` | `def _log_inference_error(self, model_id: str, e: Exception)` |
| `.generate_chat_completion` | `async def generate_chat_completion( self, model_id: str, messages: list \| str, correlation_id: str \| None = None, **kwargs, ) -> dict` |
| `._get_worker_config` | `def _get_worker_config(self, model_id: str) -> dict` |
| `._format_response` | `def _format_response( self, response: dict, model_id: str, inference_ms: float \| None = None, ) -> dict` |
| `._capture_peak_usage` | `async def _capture_peak_usage( self, model_id: str, request_id: str, worker_config: dict )` |
| `._handle_transport_error` | `def _handle_transport_error(self, error_message: str, model_id: str, context: str)` |
| … | (10 methods total) |

| Function | Signature |
|---|---|
| `_get_resource_tracker` | `def _get_resource_tracker()` |

**`src/core/workers/chat_completion/streaming.py`** — Streaming chat completion operations.

| Class | Signature |
|---|---|
| `StreamingChatCompletion` | `class StreamingChatCompletion` |
| `.__init__` | `def __init__(self, controller: "WorkerController")` |
| `.inference_stream` | `async def inference_stream( self, model_id: str, messages: list[dict[str, str]] \| str, parameters: dict[str, Any], correlation_id: str \| None = None, ) -> AsyncIterator[dict[str, Any]]` |
| `.generate_chat_completion_stream` | `async def generate_chat_completion_stream( self, model_id: str, messages: list[dict[str, str]], correlation_id: str \| None = None, **kwargs, ) -> AsyncIterator[dict[str, Any]]` |
| `._stream_with_tracking` | `async def _stream_with_tracking( self, model_id: str, messages: list, kwargs: dict, correlation_id: str \| None, resource_tracker, idle_marked_in_stream: list[bool], ) -> AsyncIterator[dict[str, Any]]` |
| `._get_finish_reason` | `def _get_finish_reason(self, chunk: dict) -> str \| None` |
| `._finalize_stream` | `async def _finalize_stream(self, model_id: str) -> bool` |
| `._handle_cancellation` | `async def _handle_cancellation(self, model_id: str, model_marked_busy: bool)` |
| `._handle_stream_error` | `async def _handle_stream_error( self, model_id: str, error: Exception, model_marked_busy: bool )` |
| … | (10 methods total) |

| Function | Signature |
|---|---|
| `_get_resource_tracker` | `def _get_resource_tracker()` |

### `src/core/workers/controller`

**`src/core/workers/controller/__init__.py`** — Worker controller package — re-exports WorkerController.

**`src/core/workers/controller/_runtime.py`** — Package-private runtime helpers and loggers for the worker controller.

| Function | Signature |
|---|---|
| `_get_resource_tracker` | `def _get_resource_tracker()` |
| `_emit_embedding_debug` | `async def _emit_embedding_debug( step: str, model_id: str, correlation_id: str \| None, **extra: Any, ) -> None` |

**`src/core/workers/controller/cancel_rpc.py`** — Cancellation and RPC call mixin for WorkerController.

| Class | Signature |
|---|---|
| `CancelRpcMixin` | `class CancelRpcMixin` |
| `.cancel_streaming_inference` | `async def cancel_streaming_inference( self, model_id: str, stream_id: str, reason: str = "explicit_cancellation" ) -> bool` |
| `.cancel_current_stream` | `async def cancel_current_stream(self, model_id: str) -> bool` |
| `.cancel_work` | `async def cancel_work( self, model_id: str, stream_id: str \| None = None, reason: str = "explicit_cancellation", ) -> bool` |
| `._cancel_work_rpc` | `async def _cancel_work_rpc( self, model_id: str, stream_id: str \| None, reason: str ) -> bool` |
| `._handle_rpc_cancel_failure` | `async def _handle_rpc_cancel_failure(self, model_id: str, reason: str) -> None` |
| `._call_rpc` | `async def _call_rpc( self, model_id: str, method: str, params: dict[str, Any], timeout: float = 300.0 ) -> dict[str, Any]` |
| `.call_rpc` | `async def call_rpc( self, model_id: str, method: str, params: dict[str, Any], timeout: float = 300.0 ) -> Any` |

**`src/core/workers/controller/controller.py`** — Worker controller delegating model loading and inference.

| Class | Signature |
|---|---|
| `WorkerController` | `class WorkerController( LifecycleMixin, ModelFacadeMixin, InferenceFacadeMixin, CancelRpcMixin, MultimodalMixin, ProcessStatusMixin, MetricsMixin, )` |
| `.__init__` | `def __init__( self, model_registry: ModelRegistry, gateway_config: Any, event_bus=None )` |
| `._init_resource_monitoring` | `def _init_resource_monitoring(self, event_bus) -> None` |
| `._init_paths` | `def _init_paths(self, cfg) -> None` |
| `._init_managers` | `def _init_managers(self, cfg, event_bus) -> None` |
| `._create_health_config` | `def _create_health_config(self, cfg, event_bus) -> ProcessHealthConfig` |
| `._cleanup_socket_file` | `async def _cleanup_socket_file(self, model_id: str) -> None` |
| `._create_transport_config` | `def _create_transport_config(self, socket_path: str)` |

**`src/core/workers/controller/inference_facade.py`** — Chat completion and token-count inference facade mixin.

| Class | Signature |
|---|---|
| `InferenceFacadeMixin` | `class InferenceFacadeMixin` |
| `.inference` | `async def inference( self, model_id: str, messages: list, parameters: dict, correlation_id: str \| None = None, )` |
| `.generate_chat_completion` | `async def generate_chat_completion( self, model_id: str, messages: list, correlation_id: str \| None = None, **kwargs )` |
| `.count_tokens` | `async def count_tokens( self, model_id: str, message_or_prompt: list \| str, use_cpu: bool, context_length: int \| None = None, tools: list[dict[str, Any]] \| None = None, ) -> dict` |
| `.inference_stream` | `async def inference_stream( self, model_id: str, messages: list, parameters: dict, correlation_id: str \| None = None, ) -> AsyncIterator[dict]` |
| `.generate_chat_completion_stream` | `async def generate_chat_completion_stream( self, model_id: str, messages: list, correlation_id: str \| None = None, **kwargs ) -> AsyncIterator[dict]` |

**`src/core/workers/controller/lifecycle.py`** — Worker controller lifecycle and idle-notification mixin.

| Class | Signature |
|---|---|
| `LifecycleMixin` | `class LifecycleMixin` |
| `.start` | `async def start(self)` |
| `._on_inference_completed` | `async def _on_inference_completed(self, event) -> None` |
| `.stop` | `async def stop(self)` |
| `.shutdown` | `async def shutdown(self)` |
| `.is_idle` | `def is_idle(self) -> bool` |
| `.register_idle_callback` | `def register_idle_callback(self, callback: Callable[[], Awaitable[None]]) -> None` |
| `._notify_idle_if_needed` | `async def _notify_idle_if_needed(self) -> None` |

**`src/core/workers/controller/metrics.py`** — Active-model tracking, metrics, and orphan cleanup mixin.

| Class | Signature |
|---|---|
| `MetricsMixin` | `class MetricsMixin` |
| `.get_active_model_id` | `def get_active_model_id(self) -> str \| None` |
| `.get_active_models` | `def get_active_models(self) -> list[str]` |
| `.is_model_loaded` | `async def is_model_loaded(self, model_id: str) -> bool` |
| `.get_status` | `async def get_status(self) -> dict[str, Any]` |
| `.get_worker_metrics` | `def get_worker_metrics(self, model_id: str) -> dict \| None` |
| `.get_all_worker_metrics` | `def get_all_worker_metrics(self) -> dict[str, dict]` |
| `.get_loaded_workers` | `def get_loaded_workers(self) -> list[str]` |
| `.get_running_worker_processes` | `def get_running_worker_processes(self) -> dict[str, int]` |
| … | (9 methods total) |

**`src/core/workers/controller/model_facade.py`** — Model load/unload facade mixin for WorkerController.

| Class | Signature |
|---|---|
| `ModelFacadeMixin` | `class ModelFacadeMixin` |
| `.load_model` | `async def load_model(self, model_id: str) -> bool` |
| `.ensure_model_loaded` | `async def ensure_model_loaded( self, model_id: str, correlation_id: str \| None = None ) -> bool` |
| `.unload_model` | `async def unload_model(self, model_id: str, force: bool = False) -> UnloadResult` |
| `.unload_current_model` | `async def unload_current_model(self) -> UnloadResult` |

**`src/core/workers/controller/multimodal.py`** — Multimodal inference (transcription, image, embeddings, rerank) mixin.

| Class | Signature |
|---|---|
| `MultimodalMixin` | `class MultimodalMixin` |
| `.transcribe_file` | `async def transcribe_file( self, model_id: str, audio_file_path: str, language: str \| None = None, prompt: str \| None = None, temperature: float = 0.0, word_timestamps: bool = True, timeout: float \| None = None, ) -> dict[str, Any]` |
| `.generate_image` | `async def generate_image( self, model_id: str, prompt: str, width: int = 1024, height: int = 1024, num_inference_steps: int = 20, # FLUX.2 default guidance_scale: float = 4.0, # FLUX.2 default seed: int \| None = None, response_format: str = "b64_json", negative_prompt: str \| None = None, caption_upsample_temperature: float \| None = None, timeout: float \| None = None, ) -> dict[str, Any]` |
| `.generate_embeddings` | `async def generate_embeddings( self, model_id: str, input_texts: list[str], correlation_id: str \| None = None, ) -> dict[str, Any]` |
| `.rerank` | `async def rerank( self, model_id: str, query: str, passages: list[str], correlation_id: str \| None = None, ) -> dict[str, Any]` |
| `._recycle_after_rerank_timeout` | `async def _recycle_after_rerank_timeout( self, model_id: str, correlation_id: str \| None = None ) -> None` |
| `._recycle_after_embedding_timeout` | `async def _recycle_after_embedding_timeout( self, model_id: str, correlation_id: str \| None = None ) -> None` |

**`src/core/workers/controller/process_status.py`** — Process health, status, and resource query mixin.

| Class | Signature |
|---|---|
| `ProcessStatusMixin` | `class ProcessStatusMixin` |
| `.get_all_process_info` | `def get_all_process_info(self) -> dict[str, Any]` |
| `.get_workers_status` | `async def get_workers_status(self) -> dict[str, Any]` |
| `.is_process_alive` | `async def is_process_alive(self, model_id: str) -> bool` |
| `.check_engine_health` | `async def check_engine_health(self, model_id: str) -> bool` |
| `.get_process_status` | `async def get_process_status(self, model_id: str)` |
| `.get_worker_info` | `def get_worker_info(self, model_id: str)` |
| `.get_engine_pid` | `def get_engine_pid(self, model_id: str) -> int \| None` |
| `.get_socket_path` | `def get_socket_path(self, model_id: str) -> str` |
| … | (12 methods total) |

### `src/core/workers/entrypoint`

**`src/core/workers/entrypoint/__init__.py`** — Worker entrypoint configuration.

**`src/core/workers/entrypoint/types.py`** — Worker entrypoint types.

### `src/core/workers/inference`

**`src/core/workers/inference/__init__.py`** — Inference operations for worker processes.

**`src/core/workers/inference/cancellation.py`** — Cancellation logic for streaming inference.

| Class | Signature |
|---|---|
| `InferenceCancellationManager` | `class InferenceCancellationManager` |
| `.__init__` | `def __init__(self, process_state)` |
| `.cancel_streaming_inference` | `async def cancel_streaming_inference( self, model_id: str, stream_id: str, reason: str = "explicit_cancellation" ) -> bool` |
| `.cancel_current_stream` | `async def cancel_current_stream(self, model_id: str) -> bool` |
| `.cancel_work` | `async def cancel_work( self, model_id: str, stream_id: str \| None = None, reason: str = "explicit_cancellation", ) -> bool` |

**`src/core/workers/inference/regular.py`** — Regular (non-streaming) inference operations.

| Class | Signature |
|---|---|
| `RegularInferenceManager` | `class RegularInferenceManager` |
| `.__init__` | `def __init__(self, process_state: ProcessState, gateway_config: Any)` |
| `.handle_regular_inference` | `async def handle_regular_inference( self, model_id: str, messages: list[dict[str, str]] \| str, parameters: dict[str, Any], correlation_id: str \| None = None, ) -> dict[str, Any]` |

**`src/core/workers/inference/streaming.py`** — Streaming inference operations with guaranteed cancellation.

| Class | Signature |
|---|---|
| `StreamingInferenceManager` | `class StreamingInferenceManager` |
| `.__init__` | `def __init__( self, process_state: ProcessState, gateway_config: Any, event_bus: Any \| None = None, )` |
| `.inference_stream` | `async def inference_stream( self, model_id: str, messages: list[dict[str, str]] \| str, parameters: dict[str, Any], correlation_id: str \| None = None, ) -> AsyncIterator[dict[str, Any]]` |

| Function | Signature |
|---|---|
| `parse_stream_message` | `def parse_stream_message(response_data: dict[str, Any]) -> tuple[str, Any]` |

### `src/core/workers/model_operations`

**`src/core/workers/model_operations/__init__.py`** — Model operations module for WorkerController.

**`src/core/workers/model_operations/failure_snapshot.py`** — Build a worker/process/resource snapshot at the moment of a load failure.

| Function | Signature |
|---|---|
| `build_worker_snapshot` | `def build_worker_snapshot( controller: WorkerController, failed_model_id: str, ) -> dict[str, Any] \| None` |
| `_capture_failed_worker` | `def _capture_failed_worker( controller: WorkerController, model_id: str ) -> dict[str, Any]` |
| `_capture_peer_workers` | `def _capture_peer_workers( controller: WorkerController, failed_model_id: str ) -> list[dict[str, Any]]` |
| `_capture_resources` | `def _capture_resources() -> dict[str, Any]` |
| `_safe_get_all_process_info` | `def _safe_get_all_process_info( controller: WorkerController, ) -> dict[str, Any] \| None` |
| `_describe_process_tree` | `def _describe_process_tree(pid: int) -> list[dict[str, Any]]` |
| `_describe_process` | `def _describe_process(proc: Any) -> dict[str, Any]` |

**`src/core/workers/model_operations/load_progress.py`** — Load progress heartbeat during active model load.

| Class | Signature |
|---|---|
| `LoadProgressHeartbeat` | `class LoadProgressHeartbeat` |
| `.__init__` | `def __init__(self, controller: WorkerController, model_id: str) -> None` |
| `.start` | `async def start(self) -> None` |
| `.stop` | `async def stop(self) -> None` |
| `.emit_phase` | `async def emit_phase(self, phase: str, pct: int \| None = None) -> None` |
| `._heartbeat_loop` | `async def _heartbeat_loop(self) -> None` |

**`src/core/workers/model_operations/preflight.py`** — Resource preflight checks for model loading.

| Function | Signature |
|---|---|
| `_get_resource_tracker` | `def _get_resource_tracker()` |
| `evaluate_resources` | `async def evaluate_resources( model_id: str, gateway_config: "GatewayConfig" ) -> tuple[bool, str \| None, bool]` |
| `check_resources_and_block` | `async def check_resources_and_block( controller: "WorkerController", model_id: str ) -> tuple[bool, dict \| None]` |

**`src/core/workers/model_operations/unloader.py`** — Model unloading operations for WorkerController.

| Class | Signature |
|---|---|
| `ModelUnloader` | `class ModelUnloader` |
| `.__init__` | `def __init__(self, controller: "WorkerController")` |
| `.unload_current_model` | `async def unload_current_model(self) -> UnloadResult` |
| `.unload_model` | `async def unload_model(self, model_id: str, force: bool = False) -> UnloadResult` |
| `._perform_unload` | `async def _perform_unload( self, model_id: str, supervisor, fast_unload: bool ) -> bool` |
| `._get_tracked_worker_pid` | `def _get_tracked_worker_pid(self, supervisor) -> int \| None` |
| `._is_pid_gone` | `def _is_pid_gone(self, pid: int \| None) -> bool` |
| `._fast_unload` | `async def _fast_unload(self, model_id: str, supervisor) -> bool` |
| `._standard_unload` | `async def _standard_unload(self, model_id: str, supervisor) -> bool` |
| … | (12 methods total) |

| Function | Signature |
|---|---|
| `_publish_socket_cleanup` | `async def _publish_socket_cleanup(model_id: str) -> None` |
| `_get_resource_tracker` | `def _get_resource_tracker()` |
| `_get_event_classes` | `def _get_event_classes()` |
| `_publish_event` | `async def _publish_event(event_bus, event) -> bool` |

### `src/core/workers/model_operations/load_flow`

**`src/core/workers/model_operations/load_flow/__init__.py`** — Model loading flow operations: worker lifecycle, verification, and finalization.

**`src/core/workers/model_operations/load_flow/cleanup.py`** — Failed worker cleanup with guaranteed process termination after load failures.

| Function | Signature |
|---|---|
| `is_model_cleanup_in_progress` | `def is_model_cleanup_in_progress(model_id: str) -> bool` |
| `cleanup_failed_worker` | `async def cleanup_failed_worker( controller: "WorkerController", model_id: str, reason: str )` |
| `_cleanup_failed_worker_inner` | `async def _cleanup_failed_worker_inner( controller: "WorkerController", model_id: str, reason: str )` |

**`src/core/workers/model_operations/load_flow/deps.py`** — Shared lazy imports, loggers, and debug helpers for model load flow modules.

| Function | Signature |
|---|---|
| `get_resource_tracker` | `def get_resource_tracker()` |
| `get_event_classes` | `def get_event_classes()` |
| `publish_event` | `async def publish_event(event_bus, event) -> bool` |
| `emit_load_flow_debug` | `async def emit_load_flow_debug(step: str, model_id: str, **extra: Any) -> None` |
| `is_cleanup_in_progress` | `def is_cleanup_in_progress(model_id: str) -> bool` |
| `mark_cleanup_started` | `def mark_cleanup_started(model_id: str) -> None` |
| `mark_cleanup_finished` | `def mark_cleanup_finished(model_id: str) -> None` |

**`src/core/workers/model_operations/load_flow/events.py`** — Model loading lifecycle event emission for started, progress, and failed states.

| Function | Signature |
|---|---|
| `emit_loading_event` | `async def emit_loading_event( controller: "WorkerController", model_id: str, status: str, error: str \| None = None, ) -> None` |
| `emit_loading_progress` | `async def emit_loading_progress( controller: "WorkerController", model_id: str, phase: str, pct: int \| float, ) -> None` |

**`src/core/workers/model_operations/load_flow/failure_classify.py`** — Failure text normalization and reason-code classification for model load errors.

| Function | Signature |
|---|---|
| `is_oom_error` | `def is_oom_error(error_str: str) -> bool` |
| `is_resource_error` | `def is_resource_error(error_str: str) -> bool` |
| `classify_load_failure` | `def classify_load_failure(error_message: str) -> tuple[str, str]` |
| `resolve_load_failure` | `def resolve_load_failure(model_id: str, fallback: str) -> tuple[str, str]` |

**`src/core/workers/model_operations/load_flow/failure_handle.py`** — Exception handling during model load with classified errors and cleanup.

| Function | Signature |
|---|---|
| `handle_load_exception` | `async def handle_load_exception( controller: "WorkerController", model_id: str, e: Exception )` |

**`src/core/workers/model_operations/load_flow/finalize.py`** — Successful load finalization with resource measurement and catalog VRAM reconcile.

| Function | Signature |
|---|---|
| `finalize_load` | `async def finalize_load( controller: "WorkerController", model_id: str, vram_before: float \| None, context_length: int \| None = None, )` |
| `reconcile_catalog_vram` | `def reconcile_catalog_vram( model_id: str, actual_vram: int, actual_ram: int, ) -> bool` |

**`src/core/workers/model_operations/load_flow/state_measure.py`** — State-machine reset and pre-load VRAM measurement helpers for model loading.

| Function | Signature |
|---|---|
| `reset_state_machine` | `def reset_state_machine(model_id: str)` |
| `measure_vram_before` | `async def measure_vram_before(model_id: str) -> float \| None` |

**`src/core/workers/model_operations/load_flow/worker_config.py`** — Model configuration dispatch and post-load responsiveness checks for workers.

| Function | Signature |
|---|---|
| `send_model_config` | `async def send_model_config( controller: "WorkerController", model_id: str ) -> dict[str, Any] \| None` |
| `verify_model_responsive` | `async def verify_model_responsive( controller: "WorkerController", model_id: str ) -> bool` |

**`src/core/workers/model_operations/load_flow/worker_start.py`** — Worker process startup and orphan reconciliation during model load.

| Function | Signature |
|---|---|
| `start_worker` | `async def start_worker(controller: "WorkerController", model_id: str) -> bool` |
| `start_worker_if_needed` | `async def start_worker_if_needed(controller: "WorkerController", model_id: str) -> bool` |

### `src/core/workers/model_operations/loader`

**`src/core/workers/model_operations/loader/__init__.py`** — Model loading operations for WorkerController with single-flight coalescing.

**`src/core/workers/model_operations/loader/constants.py`** — Shared constants and debug helpers for model loading gate operations.

| Function | Signature |
|---|---|
| `get_resource_tracker` | `def get_resource_tracker()` |
| `emit_load_gate_debug` | `async def emit_load_gate_debug( step: str, model_id: str, correlation_id: str \| None = None, **extra: Any ) -> None` |

**`src/core/workers/model_operations/loader/context_validation.py`** — Context availability validation for synthetic model IDs before load attempts.

| Function | Signature |
|---|---|
| `validate_context_availability` | `async def validate_context_availability( controller: "WorkerController", model_id: str ) -> tuple[bool, str]` |

**`src/core/workers/model_operations/loader/core.py`** — ModelLoader class coordinating single-flight model load operations.

| Class | Signature |
|---|---|
| `ModelLoader` | `class ModelLoader` |
| `.__init__` | `def __init__(self, controller: "WorkerController")` |
| `.ensure_model_loaded` | `async def ensure_model_loaded( self, model_id: str, correlation_id: str \| None = None ) -> bool` |
| `.load_model` | `async def load_model(self, model_id: str) -> bool` |

**`src/core/workers/model_operations/loader/dependencies.py`** — Worker dependency validation before model load operations proceed.

| Function | Signature |
|---|---|
| `validate_dependencies` | `async def validate_dependencies(controller: "WorkerController", model_id: str) -> bool` |

**`src/core/workers/model_operations/loader/engine_ready.py`** — Bounded engine readiness polling after worker start during model load.

| Function | Signature |
|---|---|
| `wait_for_engine_ready` | `async def wait_for_engine_ready(loader: "ModelLoader", model_id: str) -> bool` |

**`src/core/workers/model_operations/loader/load_execution.py`** — Inner load execution steps after preflight and loading.started events.

| Function | Signature |
|---|---|
| `load_model_inner` | `async def load_model_inner(loader: "ModelLoader", model_id: str) -> bool` |
| `run_load_after_started` | `async def run_load_after_started( loader: "ModelLoader", model_id: str, heartbeat: load_progress.LoadProgressHeartbeat, ) -> bool` |

### `src/core/workers/monitoring`

**`src/core/workers/monitoring/__init__.py`** — Process monitoring utilities for worker processes.

**`src/core/workers/monitoring/process_monitor.py`** — Process monitor utilities for worker processes.

| Class | Signature |
|---|---|
| `ProcessMonitor` | `class ProcessMonitor` |
| `.__init__` | `def __init__(self, process_state: ProcessState)` |
| `.get_resource_usage` | `async def get_resource_usage(self, model_id: str)` |
| `.get_peak_usage` | `def get_peak_usage(self, model_id: str)` |
| `.reset_peak_usage` | `def reset_peak_usage(self, model_id: str)` |

### `src/core/workers/process`

**`src/core/workers/process/__init__.py`** — Process lifecycle and communication management.

**`src/core/workers/process/state.py`** — Shared state container for worker process management.

| Function | Signature |
|---|---|
| `_process_key` | `def _process_key(model_id: str \| ModelId) -> str` |

### `src/core/workers/process/communication`

**`src/core/workers/process/communication/__init__.py`** — Process communication modules for worker management.

**`src/core/workers/process/communication/cleanup.py`** — Worker cleanup and resource management - event-driven.

| Function | Signature |
|---|---|
| `cleanup_failed_worker` | `async def cleanup_failed_worker( model_id: str, supervisor: ProcessSupervisor \| None, gateway_config: Any, socket_path: str, error_message: str, ) -> None` |
| `_reset_model_state_after_cleanup` | `def _reset_model_state_after_cleanup( model_id: str, cleanup_succeeded: bool, ) -> None` |
| `determine_error_type_and_code` | `def determine_error_type_and_code(error_msg: str) -> tuple[str, str]` |
| `cleanup_syntax_error_worker` | `async def cleanup_syntax_error_worker( model_id: str, supervisor: ProcessSupervisor \| None, gateway_config: Any, socket_path: str, ) -> None` |

**`src/core/workers/process/communication/config_builder.py`** — Model configuration builder for worker initialization.

| Function | Signature |
|---|---|
| `build_model_config_for_worker` | `def build_model_config_for_worker( model_id: str, model_registry: Any, gateway_config: Any, ) -> dict[str, Any]` |
| `get_worker_timeout` | `def get_worker_timeout(gateway_config: Any) -> float` |
| `_is_vllm_engine` | `def _is_vllm_engine(model_registry: Any, model_id: str) -> bool` |
| `_inject_vllm_warmup_config` | `def _inject_vllm_warmup_config( loader_config: dict[str, Any], gateway_config: Any ) -> None` |
| `get_force_stop_timeout` | `def get_force_stop_timeout(gateway_config: Any) -> float` |

**`src/core/workers/process/communication/error_handling.py`** — Error handling for worker communication.

| Function | Signature |
|---|---|
| `log_process_error` | `def log_process_error( error: ProcessError, model_id: str, command_type: str, ) -> None` |
| `create_worker_error_from_process_error` | `def create_worker_error_from_process_error( error: ProcessError, model_id: str, command: dict, operation: str, ) -> WorkerInitializationError` |
| `create_timeout_error` | `def create_timeout_error( model_id: str, command: dict, timeout: float, operation: str, ) -> WorkerInitializationError` |
| `log_connection_error` | `def log_connection_error( error: Exception, model_id: str, context: str = "operation", ) -> None` |

**`src/core/workers/process/communication/event_handlers.py`** — Event handlers for worker cleanup events.

| Function | Signature |
|---|---|
| `handle_socket_cleanup` | `async def handle_socket_cleanup(event: Event) -> None` |
| `handle_supervisor_termination` | `async def handle_supervisor_termination(event: Event) -> None` |
| `handle_resource_state_update` | `async def handle_resource_state_update(event: Event) -> None` |
| `handle_worker_cleanup` | `async def handle_worker_cleanup(event: Event) -> None` |
| `register_cleanup_event_handlers` | `def register_cleanup_event_handlers() -> None` |

**`src/core/workers/process/communication/health_checks.py`** — Worker health validation and preflight checks.

| Function | Signature |
|---|---|
| `run_preflight_checks` | `async def run_preflight_checks( supervisor: ProcessSupervisor, socket_path: str, model_id: str, ) -> None` |
| `validate_load_response` | `def validate_load_response( result: Any, model_id: str, ) -> tuple[bool, int \| None, int \| None, str \| None]` |

**`src/core/workers/process/communication/manager.py`** — ProcessCommunicationManager for worker orchestration.

| Class | Signature |
|---|---|
| `ProcessCommunicationManager` | `class ProcessCommunicationManager` |
| `.__init__` | `def __init__( self, state: Any, ipc_socket_dir: Path, gateway_config: Any, model_registry: Any, )` |
| `.create_transport_config` | `def create_transport_config(self, socket_file_path: str) -> UnixSocketConfig` |
| `._check_rpc_health` | `async def _check_rpc_health( self, socket_path: str, model_id: str, timeout: float = 5.0 ) -> bool` |
| `.send_model_config` | `async def send_model_config( self, model_id: str, supervisor: ProcessSupervisor, correlation_id: str \| None = None, ) -> dict[str, Any] \| None` |
| `.handle_transport_error` | `def handle_transport_error( self, error_message: str, model_id: str, context: str = "operation" ) -> None` |
| `.get_socket_path` | `def get_socket_path(self, model_id: str) -> str` |
| `.cleanup_socket_file` | `async def cleanup_socket_file(self, model_id: str) -> None` |

**`src/core/workers/process/communication/rpc_client.py`** — RPC client for worker communication.

| Function | Signature |
|---|---|
| `check_rpc_health` | `async def check_rpc_health( socket_path: str, model_id: str, timeout: float = 5.0, ) -> bool` |
| `send_init_config_command` | `async def send_init_config_command( supervisor: ProcessSupervisor, config: dict[str, Any], model_id: str, correlation_id: str \| None = None, timeout: float = 30.0, ) -> dict[str, Any]` |
| `send_load_model_command` | `async def send_load_model_command( supervisor: ProcessSupervisor, model_id: str, correlation_id: str \| None = None, timeout: float = 300.0, ) -> dict[str, Any]` |

### `src/core/workers/process/communication/orchestration`

**`src/core/workers/process/communication/orchestration/__init__.py`** — Model loading orchestration: flow execution and failure error handlers.

**`src/core/workers/process/communication/orchestration/debug.py`** — Debug event emission for gateway model load handshake tracing.

| Function | Signature |
|---|---|
| `emit_gateway_load_handshake_debug` | `async def emit_gateway_load_handshake_debug( step: str, model_id: str, correlation_id: str \| None, **extra: Any, ) -> None` |

**`src/core/workers/process/communication/orchestration/error_handlers.py`** — Error handlers for model load failures during worker communication orchestration.

| Function | Signature |
|---|---|
| `handle_load_failure` | `async def handle_load_failure( model_id: str, supervisor: ProcessSupervisor, gateway_config: Any, socket_path: str, failed_workers: set[str], error_msg: str, config_to_send: dict[str, Any], ) -> None` |
| `handle_syntax_error_exception` | `async def handle_syntax_error_exception( model_id: str, supervisor: ProcessSupervisor, gateway_config: Any, socket_path: str, failed_workers: set[str], error: SyntaxError, ) -> None` |
| `handle_general_exception` | `async def handle_general_exception( model_id: str, supervisor: ProcessSupervisor, gateway_config: Any, socket_path: str, failed_workers: set[str], error: Exception, ) -> None` |

**`src/core/workers/process/communication/orchestration/flow.py`** — Model loading flow orchestration: config build, RPC, and response validation.

| Function | Signature |
|---|---|
| `execute_model_loading_flow` | `async def execute_model_loading_flow( model_id: str, supervisor: ProcessSupervisor, model_registry: Any, gateway_config: Any, correlation_id: str \| None = None, ) -> tuple[bool, int \| None, int \| None, str \| None]` |

### `src/core/workers/process/crash_callback`

**`src/core/workers/process/crash_callback/__init__.py`** — Process crash callback handling - event-driven cleanup.

**`src/core/workers/process/crash_callback/events.py`** — Socket cleanup event publication for crashed processes.

| Function | Signature |
|---|---|
| `publish_socket_cleanup_event` | `def publish_socket_cleanup_event(process_id: str) -> None` |

**`src/core/workers/process/crash_callback/health.py`** — Health monitoring lifecycle management for crashed processes.

| Function | Signature |
|---|---|
| `stop_health_monitoring` | `async def stop_health_monitoring(process_id: str, state: ProcessState) -> None` |

**`src/core/workers/process/crash_callback/logging.py`** — Crash error logging with error isolation.

| Function | Signature |
|---|---|
| `log_crash_error` | `def log_crash_error(process_id: str, exit_code: int, error_message: str) -> None` |

**`src/core/workers/process/crash_callback/orchestrator.py`** — Crash callback orchestration - delegates to responsibility-focused modules.

| Function | Signature |
|---|---|
| `handle_process_crash_callback` | `async def handle_process_crash_callback( process_id: str, exit_code: int, error_message: str, state: ProcessState, ) -> None` |

**`src/core/workers/process/crash_callback/state.py`** — Crash state management - worker failure marking and supervisor cleanup.

| Function | Signature |
|---|---|
| `mark_worker_failed` | `def mark_worker_failed(process_id: str, state: ProcessState) -> None` |
| `cleanup_supervisor_references` | `def cleanup_supervisor_references(process_id: str, state: ProcessState) -> None` |

### `src/core/workers/process/lifecycle`

**`src/core/workers/process/lifecycle/__init__.py`** — Process lifecycle management for worker startup, shutdown, and cleanup.

**`src/core/workers/process/lifecycle/cleanup.py`** — Process cleanup helpers for stale, orphaned, and untracked worker processes.

| Function | Signature |
|---|---|
| `kill_pid_tree` | `async def kill_pid_tree(pid: int, model_id: str) -> bool` |
| `_schedule_socket_cleanup` | `def _schedule_socket_cleanup(model_id: str, socket_path: str) -> None` |
| `fallback_process_cleanup` | `async def fallback_process_cleanup(manager: Any, model_id: str) -> bool` |
| `cleanup_stale_process` | `async def cleanup_stale_process(state: ProcessState, model_id: str) -> None` |

**`src/core/workers/process/lifecycle/liveness.py`** — Process liveness verification for tracked worker supervisors.

| Function | Signature |
|---|---|
| `verify_process_alive` | `async def verify_process_alive( model_id: str, get_all_process_info_func: Callable[[], dict[str, Any]], ) -> bool` |
| `is_process_alive` | `async def is_process_alive(state: ProcessState, model_id: str) -> bool` |

**`src/core/workers/process/lifecycle/manager.py`** — ProcessLifecycleManager coordinating worker startup, shutdown, and cleanup.

| Class | Signature |
|---|---|
| `ProcessLifecycleManager` | `class ProcessLifecycleManager` |
| `.__init__` | `def __init__( self, state: ProcessState, worker_logs_dir: Path, ipc_socket_dir: Path, gateway_config: Any, python_executable: str, worker_entrypoint: WorkerEntrypoint, health_config: ProcessHealthConfig, resource_config: ProcessIPCResourceConfig \| None, startup_timeout: float, shutdown_timeout: float, )` |
| `.start_worker` | `async def start_worker( self, model_id: str, transport_config_factory: Callable, verify_process_alive_func: Callable, capture_diagnostics_func: Callable, ) -> bool` |
| `.shutdown` | `async def shutdown(self) -> bool` |
| `.handle_process_crash_callback` | `async def handle_process_crash_callback( self, process_id: str, exit_code: int, error_message: str, )` |
| `.fallback_process_cleanup` | `async def fallback_process_cleanup(self, model_id: str) -> bool` |
| `.verify_process_alive` | `async def verify_process_alive( self, model_id: str, get_all_process_info_func: Callable[[], dict[str, Any]], ) -> bool` |
| `.cleanup_stale_process` | `async def cleanup_stale_process(self, model_id: str) -> None` |
| `.is_process_alive` | `async def is_process_alive(self, model_id: str) -> bool` |

**`src/core/workers/process/lifecycle/start.py`** — Worker process startup: command assembly, supervisor spawn, and state tracking.

| Function | Signature |
|---|---|
| `start_worker` | `async def start_worker( manager: Any, model_id: str, transport_config_factory: Callable[[str], UnixSocketConfig], verify_process_alive_func: Callable[[str], Any], capture_diagnostics_func: Callable[[str, list, dict], Any], ) -> bool` |

### `src/core/workers/worker`

**`src/core/workers/worker/__init__.py`** — Worker module - process lifecycle and RPC handling.

| Class | Signature |
|---|---|
| `Worker` | `class Worker( WorkerBase, LoadHandlers, InferenceHandlers, LifecycleHandlers, StreamHandlers, MetadataHandlers, RPCHelpers, LegacyRPCHelpers, StreamingHandlers, FluxImageHandlers, EmbeddingHandlers, RerankHandlers, EngineLifecycle, )` |

**`src/core/workers/worker/__main__.py`** — Worker process main entry point.

| Function | Signature |
|---|---|
| `main` | `async def main()` |

**`src/core/workers/worker/deadline.py`** — Timeout enforcement for worker inference.

**`src/core/workers/worker/engine_lifecycle.py`** — Engine lifecycle management for worker.

| Class | Signature |
|---|---|
| `EngineLifecycle` | `class EngineLifecycle` |
| `._load_model_engine` | `async def _load_model_engine(self) -> None` |
| `.process_command` | `async def process_command(self, command: dict[str, Any]) -> dict[str, Any]` |

| Function | Signature |
|---|---|
| `_validate_gpu_requirement` | `def _validate_gpu_requirement( engine_type: str, loader_config: dict[str, Any], model_id: str ) -> tuple[bool, str]` |

**`src/core/workers/worker/events.py`** — Worker event emission to local Event Service.

| Function | Signature |
|---|---|
| `_get_publisher` | `async def _get_publisher() -> Any` |
| `_make_event` | `def _make_event( signal: str, payload: dict[str, Any], *, scope: str = "node", role: str = "observation", ) -> dict[str, Any]` |
| `emit_worker_started` | `async def emit_worker_started(*, worker_id: str, model_id: str, pid: int) -> None` |
| `emit_worker_stopped` | `async def emit_worker_stopped( *, worker_id: str, model_id: str, exit_code: int \| None ) -> None` |
| `emit_model_loading` | `async def emit_model_loading(*, worker_id: str, model_id: str) -> None` |
| `emit_model_loaded` | `async def emit_model_loaded( *, worker_id: str, model_id: str, duration_s: float ) -> None` |
| `emit_model_failed` | `async def emit_model_failed(*, worker_id: str, model_id: str, error: str) -> None` |
| `emit_inference_dequeued` | `async def emit_inference_dequeued( *, worker_id: str, model_id: str, request_id: str, queue_wait_ms: float, ) -> None` |
| `emit_inference_started` | `async def emit_inference_started( *, worker_id: str, model_id: str, request_id: str ) -> None` |
| `emit_inference_completed` | `async def emit_inference_completed( *, worker_id: str, model_id: str, request_id: str, duration_s: float, tokens: int \| None = None, ) -> None` |
| `shutdown_publisher` | `async def shutdown_publisher() -> None` |

**`src/core/workers/worker/helpers.py`** — Shared helper functions for RPC handlers.

| Class | Signature |
|---|---|
| `RPCHelpers` | `class RPCHelpers` |
| `._build_non_streaming_engine_request` | `def _build_non_streaming_engine_request(self, params: dict) -> dict` |
| `._map_exception_to_engine_error` | `def _map_exception_to_engine_error(self, e: Exception) -> EngineError` |

**`src/core/workers/worker/process.py`** — Worker process lifecycle management for Universal Protocol RPC workers.

| Class | Signature |
|---|---|
| `Worker` | `class Worker(WorkerProcess)` |
| `.__init__` | `def __init__( self, worker_id: str, socket_path: str, model_id: str, idle_timeout: float \| None = None, ): # Initialize base class` |
| `.initialize` | `async def initialize(self, socket_path: str) -> None` |
| `._start_asgi_server` | `async def _start_asgi_server(self) -> None` |
| `._run_asgi_server` | `async def _run_asgi_server(self) -> None` |
| `._configure_rpc_handlers` | `async def _configure_rpc_handlers(self) -> None` |
| `.run` | `async def run(self) -> None` |
| `.shutdown` | `async def shutdown(self) -> None` |
| `._cleanup_worker` | `async def _cleanup_worker(self) -> None` |

**`src/core/workers/worker/rpc_helpers.py`** — Helper methods for RPC handlers.

| Class | Signature |
|---|---|
| `RPCHelpers` | `class RPCHelpers` |
| `._resolve_model_path` | `def _resolve_model_path(self, original_path: str) -> str` |
| `._handle_count_tokens` | `async def _handle_count_tokens(self, command: dict[str, Any]) -> dict[str, Any]` |
| `._count_tokens_with_tokenizer` | `async def _count_tokens_with_tokenizer( self, message_or_prompt: list[dict[str, str]] \| str, use_cpu: bool, context_length: int \| None = None, tools: list[dict[str, Any]] \| None = None, ) -> int` |

**`src/core/workers/worker/stream_lifecycle.py`** — Stream lifecycle management - worker-side wrapper.

| Function | Signature |
|---|---|
| `register_stream_entry` | `def register_stream_entry( entry_id: str, kind: EntryKind, context: StreamContext \| None = None, queue: UnboundedStreamQueue \| None = None, task: asyncio.Task \| None = None, ) -> asyncio.Event` |
| `cleanup_stream_entry` | `async def cleanup_stream_entry(entry_id: str, reason: str = "cleanup") -> bool` |
| `cleanup_all_streams` | `async def cleanup_all_streams(reason: str = "unload") -> int` |
| `notify_model_unload` | `async def notify_model_unload(entry_id: str, model_name: str) -> bool` |

### `src/core/workers/worker/rpc`

**`src/core/workers/worker/rpc/__init__.py`** — Worker RPC handlers (non-streaming).

**`src/core/workers/worker/rpc/embedding.py`** — RPC handlers for embedding generation.

| Class | Signature |
|---|---|
| `EmbeddingHandlers` | `class EmbeddingHandlers` |
| `.handle_generate_embeddings` | `async def handle_generate_embeddings( self, params: dict[str, Any] ) -> dict[str, Any]` |

**`src/core/workers/worker/rpc/flux_image.py`** — RPC handlers for Flux.2 image generation.

| Class | Signature |
|---|---|
| `FluxImageHandlers` | `class FluxImageHandlers` |
| `.handle_generate_image` | `async def handle_generate_image(self, params: dict[str, Any]) -> dict[str, Any]` |

**`src/core/workers/worker/rpc/helpers.py`** — Shared helper functions for RPC handlers.

| Class | Signature |
|---|---|
| `RPCHelpers` | `class RPCHelpers` |
| `._build_non_streaming_engine_request` | `def _build_non_streaming_engine_request(self, params: dict) -> dict` |
| `._map_exception_to_engine_error` | `def _map_exception_to_engine_error(self, e: Exception) -> EngineError` |

**`src/core/workers/worker/rpc/inference.py`** — Inference execution RPC handlers.

| Class | Signature |
|---|---|
| `InferenceHandlers` | `class InferenceHandlers` |
| `.handle_run_inference` | `async def handle_run_inference(self, params: dict) -> dict` |

| Function | Signature |
|---|---|
| `_emit_worker_inference_debug` | `async def _emit_worker_inference_debug( *, step: str, worker_id: str, model_id: str, request_id: str, **extra: object, ) -> None` |

**`src/core/workers/worker/rpc/lifecycle.py`** — Worker lifecycle and health RPC handlers.

| Class | Signature |
|---|---|
| `LifecycleHandlers` | `class LifecycleHandlers` |
| `.handle_init_config` | `async def handle_init_config(self, params: dict) -> dict` |
| `.handle_health` | `async def handle_health(self, params: dict[str, Any]) -> dict` |
| `.handle_ping` | `async def handle_ping(self, params: dict[str, Any]) -> dict` |
| `.handle_debug_stats` | `async def handle_debug_stats(self, params: dict[str, Any]) -> dict` |

**`src/core/workers/worker/rpc/load.py`** — Model lifecycle RPC handlers (load/unload).

| Class | Signature |
|---|---|
| `LoadHandlers` | `class LoadHandlers` |
| `._get_load_lock` | `def _get_load_lock(self) -> asyncio.Lock` |
| `._init_inference_gate` | `def _init_inference_gate(self, request_id: str) -> None` |
| `._validate_load_request_params` | `def _validate_load_request_params( self, rpc_name: str \| None, rpc_path: str \| None, request_id: str, ) -> None` |
| `._extract_context_size_from_catalog` | `def _extract_context_size_from_catalog( self, request_id: str, ) -> int \| None` |
| `.handle_load_model` | `async def handle_load_model(self, params: dict) -> dict` |
| `._validate_unload_model_request` | `def _validate_unload_model_request(self, params: dict) -> None` |
| `._notify_streams_of_unload` | `async def _notify_streams_of_unload(self) -> None` |
| `._release_engine_resources` | `async def _release_engine_resources(self) -> None` |
| … | (10 methods total) |

**`src/core/workers/worker/rpc/metadata.py`** — Model metadata and utility RPC handlers.

| Class | Signature |
|---|---|
| `MetadataHandlers` | `class MetadataHandlers` |
| `.handle_list_models` | `async def handle_list_models(self, params: dict) -> dict` |
| `.handle_get_model_info` | `async def handle_get_model_info(self, params: dict) -> dict` |
| `.handle_count_tokens` | `async def handle_count_tokens(self, params: dict) -> dict` |

**`src/core/workers/worker/rpc/rerank.py`** — RPC handlers for cross-encoder reranking.

| Class | Signature |
|---|---|
| `RerankHandlers` | `class RerankHandlers` |
| `.handle_rerank` | `async def handle_rerank(self, params: dict[str, Any]) -> dict[str, Any]` |

**`src/core/workers/worker/rpc/streams.py`** — Stream management RPC handlers.

| Class | Signature |
|---|---|
| `StreamHandlers` | `class StreamHandlers` |
| `._require_stream_id` | `def _require_stream_id(self, params: dict) -> str` |
| `._cancel_stream` | `def _cancel_stream(self, entry: "StreamEntry", stream_id: str) -> None` |
| `._force_cancel_task_if_needed` | `def _force_cancel_task_if_needed( self, entry: "StreamEntry", stream_id: str ) -> bool` |
| `._await_stream_cleanup` | `async def _await_stream_cleanup( self, entry: "StreamEntry", timeout: float, stream_id: str ) -> bool` |
| `._finalize_stream_cleanup` | `async def _finalize_stream_cleanup(self, stream_id: str, reason: str) -> None` |
| `._schedule_deferred_cleanup` | `def _schedule_deferred_cleanup( self, entry: "StreamEntry", stream_id: str, reason: str ) -> None` |
| `.handle_cancel_inference` | `async def handle_cancel_inference(self, params: dict) -> dict` |

### `src/core/workers/worker/stream`

**`src/core/workers/worker/stream/__init__.py`** — Worker stream handlers.

| Class | Signature |
|---|---|
| `StreamingHandlers` | `class StreamingHandlers( StreamInferenceStartHandlers, StreamInferenceRunHandlers, WhisperStreamingHandlers, )` |

**`src/core/workers/worker/stream/audio_rpc.py`** — Audio RPC handlers for Whisper streaming + transcription (worker-side).

| Class | Signature |
|---|---|
| `WhisperStreamingHandlers` | `class WhisperStreamingHandlers` |
| `.handle_create_stream_session` | `async def handle_create_stream_session(self, params: dict[str, Any]) -> str` |
| `.handle_process_audio_chunk` | `async def handle_process_audio_chunk(self, params: dict[str, Any]) -> list` |
| `.handle_close_stream_session` | `async def handle_close_stream_session( self, params: dict[str, Any] ) -> dict[str, Any]` |
| `.handle_transcribe_file` | `async def handle_transcribe_file(self, params: dict[str, Any]) -> dict[str, Any]` |

**`src/core/workers/worker/stream/inference_run.py`** — Streaming inference loop (worker-side).

| Class | Signature |
|---|---|
| `StreamInferenceRunHandlers` | `class StreamInferenceRunHandlers` |
| `.stream_inference` | `async def stream_inference( self, stream_id: str, context: Any, queue: Any, cancellation_event: asyncio.Event, prompt: str \| None, messages: list[dict[str, str]] \| None, parameters: dict[str, Any], request_id: str = "unknown", ) -> None` |
| `._run_streaming_inference` | `async def _run_streaming_inference( self, stream_id: str, queue: Any, cancellation_event: asyncio.Event, prompt: str \| None, messages: list[dict[str, str]] \| None, parameters: dict[str, Any], request_id: str, reset_idle: Callable[[], None] = lambda: None, ) -> None` |

**`src/core/workers/worker/stream/inference_start.py`** — Streaming inference start/registration handlers (worker-side).

| Class | Signature |
|---|---|
| `StreamInferenceStartHandlers` | `class StreamInferenceStartHandlers` |
| `._validate_stream_params` | `def _validate_stream_params( self, params: dict[str, Any] ) -> tuple[str \| None, list[dict[str, Any]] \| None, int, float \| None]` |
| `.handle_start_inference` | `async def handle_start_inference(self, params: dict[str, Any]) -> dict[str, Any]` |

### `src/jobs`

**`src/jobs/__init__.py`** — Jobs package for background task execution.

**`src/jobs/job.py`** — Base Job class with status tracking and SSE log streaming.

| Class | Signature |
|---|---|
| `JobEventType` | `class JobEventType(StrEnum)` |
| `JobStatus` | `class JobStatus(StrEnum)` |

**`src/jobs/store.py`** — Job storage singleton for managing background jobs.

| Class | Signature |
|---|---|
| `JobStore` | `class JobStore` |
| `.__init__` | `def __init__(self) -> None` |
| `.add` | `async def add(self, job: Job) -> None` |
| `.get` | `async def get(self, job_id: str) -> Job \| None` |
| `.remove` | `async def remove(self, job_id: str) -> bool` |
| `.list_all` | `async def list_all(self) -> list[Job]` |
| `.cleanup_completed` | `async def cleanup_completed(self, max_age_seconds: int = 3600) -> int` |

| Function | Signature |
|---|---|
| `get_job_store` | `def get_job_store() -> JobStore` |

### `src/jobs/context_detection`

**`src/jobs/context_detection/__init__.py`** — Context detection and activation logic for measurement jobs — package-shadow.

**`src/jobs/context_detection/catalog_lock.py`** — Exclusive file lock for concurrent-safe local catalog writes during measurement.

**`src/jobs/context_detection/catalog_writes.py`** — Local catalog profile and activated-context writes after measurement completes.

| Function | Signature |
|---|---|
| `update_local_catalog_profile` | `def update_local_catalog_profile( model_id: str, context: int, vram_mb: int \| None, ram_mb: int \| None, n_gpu_layers: int \| None, device: str \| None = None, ) -> bool` |
| `update_local_catalog_contexts` | `def update_local_catalog_contexts( model_id: str, gpu_contexts: list[int], cpu_contexts: list[int] ) -> bool` |
| `schedule_remote_sync_nowait` | `def schedule_remote_sync_nowait(model_id: str, update_type: str) -> None` |

**`src/jobs/context_detection/constants.py`** — Standard context sizes and remote catalog sync configuration for measurement jobs.

**`src/jobs/context_detection/context_lists.py`** — Context list derivation and activation selection for measurement job profiles.

| Function | Signature |
|---|---|
| `get_step_down_contexts` | `def get_step_down_contexts(training_ctx: int) -> list[int]` |
| `get_embedding_contexts` | `def get_embedding_contexts(training_ctx: int) -> list[int]` |
| `determine_activated_contexts` | `def determine_activated_contexts( results: dict[str, dict[str, Any]], mode: str ) -> tuple[list[int], list[int], str]` |

**`src/jobs/context_detection/gguf_metadata.py`** — GGUF metadata extraction for training context length discovery during measurement.

| Function | Signature |
|---|---|
| `extract_training_context_from_gguf` | `def extract_training_context_from_gguf(file_path: Path) -> int \| None` |

**`src/jobs/context_detection/path_resolution.py`** — Model ID to filesystem path resolution for catalog-backed measurement jobs.

| Function | Signature |
|---|---|
| `resolve_model_path` | `def resolve_model_path(model_id: str) -> Path \| None` |
| `_find_model_by_pattern` | `def _find_model_by_pattern(model_id: str) -> Path \| None` |

**`src/jobs/context_detection/training_context.py`** — Training context length lookup combining catalog metadata and GGUF fallback.

| Function | Signature |
|---|---|
| `get_training_context` | `async def get_training_context(model_id: str) -> int \| None` |

### `src/jobs/measurement`

**`src/jobs/measurement/__init__.py`** — Measurement job package.

**`src/jobs/measurement/common.py`** — Common utilities for measurement execution.

| Class | Signature |
|---|---|
| `SubprocessTracker` | `class SubprocessTracker` |
| `.__init__` | `def __init__(self) -> None` |
| `.add` | `def add(self, proc: asyncio.subprocess.Process) -> None` |
| `.discard` | `def discard(self, proc: asyncio.subprocess.Process) -> None` |
| `.kill_all` | `async def kill_all(self) -> None` |

| Function | Signature |
|---|---|
| `env_int` | `def env_int(name: str) -> int \| None` |
| `env_float` | `def env_float(name: str) -> float \| None` |
| `setup_measurement_subprocess` | `def setup_measurement_subprocess( *, as_limit_bytes: int \| None, nice_value: int, oom_score_adj: int \| None, ) -> None` |
| `_setup_death_signal` | `def _setup_death_signal() -> None` |
| `_get_descendant_pids` | `def _get_descendant_pids(pid: int) -> list[int]` |
| `measure_process_vram_mb` | `def measure_process_vram_mb(pid: int, device_index: int = 0) -> int \| None` |
| `get_total_vram_bytes` | `def get_total_vram_bytes(device_index: int = 0) -> int \| None` |
| `measure_process_tree_rss_mb` | `def measure_process_tree_rss_mb(pid: int) -> int` |
| `_capture_process_error` | `async def _capture_process_error( proc: asyncio.subprocess.Process, *, process_name: str, ) -> str` |
| `poll_health` | `async def poll_health( socket_path: str, proc: asyncio.subprocess.Process, timeout_sec: int, *, poll_interval: float, process_name: str, ) -> str \| None` |
| `kill_measurement_process` | `async def kill_measurement_process( proc: asyncio.subprocess.Process, *, sigterm_timeout: float, ) -> None` |

**`src/jobs/measurement/context_selection.py`** — Context list selection and training-context auto-detection for measurement jobs.

| Function | Signature |
|---|---|
| `get_cpu_contexts` | `def get_cpu_contexts(request: MeasureJobRequest) -> list[int]` |
| `detect_contexts_from_metadata` | `async def detect_contexts_from_metadata( request: MeasureJobRequest, emit_log: Callable[[str], None], ) -> None` |
| `resolve_embedding_task_default` | `def resolve_embedding_task_default(model_id: str, loader_config: dict) -> str` |

**`src/jobs/measurement/cpu.py`** — CPU measurement execution.

| Function | Signature |
|---|---|
| `measure_cpu_context` | `async def measure_cpu_context( model_path: Path, context: int, n_batch: int, gpu_index: int, mmproj_path: str \| None, tracker: SubprocessTracker, ) -> dict[str, Any]` |

**`src/jobs/measurement/embedding_measurement.py`** — GGUF embedding step-down VRAM measurement for llama-cpp loader models.

| Function | Signature |
|---|---|
| `measure_gguf_embedding_profiles` | `async def measure_gguf_embedding_profiles( request: MeasureJobRequest, model_path: Path, tracker: SubprocessTracker, entry: dict[str, Any] \| None, emit_log: Callable[[str], None], ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]` |

**`src/jobs/measurement/embedding_profile_loader.py`** — Resolve per-profile loader config for embedding measurement.

| Function | Signature |
|---|---|
| `merged_profile_loader` | `def merged_profile_loader( entry: dict[str, Any], *, ctx: int, mode: str, ) -> dict[str, Any]` |

**`src/jobs/measurement/execution.py`** — Measurement execution logic for VRAM/RAM profiling.

| Function | Signature |
|---|---|
| `measure_gpu_with_stepdown` | `async def measure_gpu_with_stepdown( model_path: Path, contexts: list[int], n_batch: int, gpu_index: int, mmproj_path: str \| None, enable_hybrid: bool, emit_log: Callable[[str], None], tracker: SubprocessTracker, safety_margin: int \| None = None, validate_timing: bool = True, ) -> dict[str, dict[str, Any]]` |
| `measure_cpu_contexts` | `async def measure_cpu_contexts( model_path: Path, contexts: list[int], n_batch: int, gpu_index: int, mmproj_path: str \| None, emit_log: Callable[[str], None], tracker: SubprocessTracker, validate_timing: bool = True, ) -> dict[str, dict[str, Any]]` |
| `measure_auto_mode` | `async def measure_auto_mode( model_path: Path, contexts: list[int], n_batch: int, gpu_index: int, mmproj_path: str \| None, enable_hybrid: bool, emit_log: Callable[[str], None], tracker: SubprocessTracker, safety_margin: int \| None = None, validate_timing: bool = True, ) -> dict[str, dict[str, Any]]` |
| `log_profile_result` | `def log_profile_result( ctx: int, profile: dict[str, Any], emit_log: Callable[[str], None] ) -> None` |
| `_log_hybrid_result` | `def _log_hybrid_result( ctx: int, profile: dict[str, Any], n_layers: int, total_layers: Any, emit_log: Callable[[str], None], ) -> None` |
| `try_hybrid_measurement` | `async def try_hybrid_measurement( model_path: Path, context: int, n_batch: int, gpu_index: int, mmproj_path: str \| None, min_layers_hint: int \| None, emit_log: Callable[[str], None], tracker: "SubprocessTracker", safety_margin: int \| None = None, ) -> dict[str, Any] \| None` |
| `apply_resource_caps` | `def apply_resource_caps( results: dict[str, dict[str, Any]], vram_cap_mb: int \| None, ram_cap_mb: int \| None, emit_log: Callable[[str], None], ) -> None` |

**`src/jobs/measurement/gguf_reader.py`** — Minimal GGUF binary header reader for measurement.

| Function | Signature |
|---|---|
| `_skip_value` | `def _skip_value(f: BinaryIO, value_type: int) -> None` |
| `_read_scalar` | `def _read_scalar(f: BinaryIO, value_type: int) -> int` |
| `extract_block_count` | `def extract_block_count(model_path: Path) -> int \| None` |

**`src/jobs/measurement/gpu.py`** — GPU and hybrid measurement execution.

| Function | Signature |
|---|---|
| `_select_visible_gpu_device` | `def _select_visible_gpu_device(gpu_index: int) -> str` |
| `_compute_adaptive_timeout` | `def _compute_adaptive_timeout( model_path: Path, n_layers: int, base_timeout: int ) -> int` |
| `_start_memory_monitor` | `async def _start_memory_monitor( model_path: Path, proc: asyncio.subprocess.Process ) -> asyncio.Task[None] \| None` |
| `run_layer_test` | `async def run_layer_test( model_path: Path, n_layers: int, context: int, n_batch: int, gpu_index: int, mmproj_path: str \| None, tracker: SubprocessTracker, timeout_sec: int = 300, embedding: bool = False, pooling: str \| None = None, ubatch_size: int \| None = None, ) -> dict[str, Any]` |
| `measure_gpu_context` | `async def measure_gpu_context( model_path: Path, context: int, n_batch: int, gpu_index: int, mmproj_path: str \| None, tracker: SubprocessTracker, total_layers: int \| None = None, ) -> dict[str, Any]` |
| `measure_hybrid_context` | `async def measure_hybrid_context( model_path: Path, context: int, n_batch: int, gpu_index: int, mmproj_path: str \| None, min_layers_hint: int \| None, tracker: SubprocessTracker, emit_log: Callable[[str], None], safety_margin: int \| None = None, ) -> dict[str, Any]` |

**`src/jobs/measurement/helpers.py`** — Helper functions for measurement job operations.

| Function | Signature |
|---|---|
| `check_measurement_resources` | `async def check_measurement_resources( model_id: str, gateway_config: "GatewayConfig", ) -> tuple[bool, str \| None]` |
| `update_catalog_with_results` | `async def update_catalog_with_results( model_id: str, mode: str, results: dict[str, dict[str, Any]], emit_log: Callable[[str], None], use_static: bool = False, loader_updates: dict[str, Any] \| None = None, ) -> None` |
| `_write_per_file_catalog_results` | `async def _write_per_file_catalog_results( model_id: str, mode: str, results: dict[str, dict[str, Any]], emit_log: Callable[[str], None], loader_updates: dict[str, Any] \| None, ) -> None` |

**`src/jobs/measurement/job.py`** — Measurement job for VRAM/RAM profiling.

**`src/jobs/measurement/memory_info.py`** — System memory information and headroom computation for measurement probes.

| Class | Signature |
|---|---|
| `_VirtualMemoryLike` | `class _VirtualMemoryLike(Protocol)` |
| `_SwapMemoryLike` | `class _SwapMemoryLike(Protocol)` |
| `_PsutilModuleLike` | `class _PsutilModuleLike(Protocol)` |
| `.virtual_memory` | `def virtual_memory(self) -> _VirtualMemoryLike` |
| `.swap_memory` | `def swap_memory(self) -> _SwapMemoryLike` |

| Function | Signature |
|---|---|
| `maybe_psutil` | `def maybe_psutil() -> _PsutilModuleLike \| None` |
| `compute_ram_headroom_bytes` | `def compute_ram_headroom_bytes(psutil_mod: _PsutilModuleLike \| None) -> int \| None` |
| `get_system_memory_info` | `def get_system_memory_info() -> dict[str, Any]` |

**`src/jobs/measurement/request.py`** — Request parameters and catalog lookup helpers for measurement jobs.

| Function | Signature |
|---|---|
| `lookup_catalog_entry` | `def lookup_catalog_entry(model_id: str) -> dict[str, Any] \| None` |

**`src/jobs/measurement/run_loop.py`** — Main measurement run loop with engine dispatch and subprocess lifecycle.

| Function | Signature |
|---|---|
| `_log_memory_diagnostics` | `def _log_memory_diagnostics(emit_log: Callable[[str], None]) -> None` |
| `_dispatch_measurement` | `async def _dispatch_measurement( request: MeasureJobRequest, model_path: Path, tracker: SubprocessTracker, emit_log: Callable[[str], None], ) -> tuple[dict[str, dict[str, Any]], dict[str, Any] \| None]` |
| `_measure_llama_cpp_text` | `async def _measure_llama_cpp_text( request: MeasureJobRequest, model_path: Path, tracker: SubprocessTracker, emit_log: Callable[[str], None], ) -> dict[str, dict[str, Any]]` |
| `run_measurement_job` | `async def run_measurement_job( request: MeasureJobRequest, gateway_config: "GatewayConfig", emit_log: Callable[[str], None], set_result: Callable[[dict[str, Any]], None], ) -> None` |

**`src/jobs/measurement/timing.py`** — Timing extraction and validation for CPU measurements.

| Class | Signature |
|---|---|
| `TimingTracker` | `class TimingTracker` |
| `.__init__` | `def __init__(self, tolerance_sec: float = 5.0) -> None` |
| `.check_and_record` | `def check_and_record( self, context: int, total_time: float ) -> list[TimingViolation]` |
| `.get_tracked_contexts` | `def get_tracked_contexts(self) -> dict[int, float]` |

| Function | Signature |
|---|---|
| `parse_timing_from_stderr` | `def parse_timing_from_stderr(stderr: str) -> TimingInfo` |
| `add_timing_to_profile` | `def add_timing_to_profile(profile: dict[str, Any]) -> TimingInfo \| None` |
| `create_timing_anomaly_error` | `def create_timing_anomaly_error( violations: list[TimingViolation], timing: TimingInfo, ) -> dict[str, Any]` |
| `validate_and_log_timing` | `def validate_and_log_timing( profile: dict[str, Any], ctx: int, timing_tracker: "TimingTracker \| None", emit_log: Any, mode_label: str = "", ) -> dict[str, Any]` |

**`src/jobs/measurement/vllm.py`** — vLLM measurement execution via vllm serve.

| Function | Signature |
|---|---|
| `_build_measurement_config` | `def _build_measurement_config( model_path: Path, max_model_len: int, quantization: str \| None, gpu_memory_utilization: float, loader_config: dict[str, Any] \| None, ) -> VLLMServerConfig` |
| `run_vllm_probe` | `async def run_vllm_probe( model_path: Path, max_model_len: int, quantization: str \| None, gpu_memory_utilization: float, tracker: SubprocessTracker, timeout_sec: int = _DEFAULT_TIMEOUT_SEC, loader_config: dict[str, Any] \| None = None, ) -> dict[str, Any]` |
| `_model_dir_size_bytes` | `def _model_dir_size_bytes(model_path: Path) -> int` |
| `_compute_initial_utilization` | `def _compute_initial_utilization( model_path: Path, total_vram_bytes: int, max_model_len: int = 0, ) -> float` |
| `find_min_gpu_utilization` | `async def find_min_gpu_utilization( model_path: Path, max_model_len: int, quantization: str \| None, tracker: SubprocessTracker, emit_log: Callable[[str], None], loader_config: dict[str, Any] \| None = None, device_index: int = 0, util_cap: float = 0.95, is_embedding: bool = False, ) -> tuple[float, dict[str, Any]]` |

**`src/jobs/measurement/vllm_measurement.py`** — vLLM-specific VRAM measurement probes for chat and embedding models.

| Function | Signature |
|---|---|
| `measure_vllm_profiles` | `async def measure_vllm_profiles( request: MeasureJobRequest, model_path: Path, tracker: SubprocessTracker, entry: dict[str, Any] \| None, emit_log: Callable[[str], None], ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]` |

### `src/middleware`

**`src/middleware/__init__.py`** — Security middleware for the Universal LLM Gateway.

**`src/middleware/auth.py`** — Enhanced authentication middleware for WebSocket connections.

| Class | Signature |
|---|---|
| `AuthLevel` | `class AuthLevel(Enum)` |
| `WebSocketAuthenticator` | `class WebSocketAuthenticator` |
| `.__init__` | `def __init__(self, config: AuthConfig \| None = None)` |
| `._load_default_config` | `def _load_default_config(self) -> AuthConfig` |
| `.authenticate` | `async def authenticate( self, api_key: str \| None = None, auth_header: str \| None = None, origin: str \| None = None, ) -> APIKeyInfo \| None` |
| `._check_origin` | `def _check_origin(self, origin: str \| None) -> bool` |
| `._validate_api_key` | `def _validate_api_key(self, key: str) -> APIKeyInfo \| None` |
| `.check_permission` | `def check_permission(self, key_info: APIKeyInfo, permission: str) -> bool` |
| `.get_rate_limit_tier` | `def get_rate_limit_tier(self, key_info: APIKeyInfo) -> str` |
| `.add_api_key` | `def add_api_key( self, key: str, permissions: set[str], rate_limit_tier: str = "standard" ) -> str` |
| … | (9 methods total) |

**`src/middleware/rate_limiter.py`** — Rate limiting middleware for WebSocket connections.

| Class | Signature |
|---|---|
| `WebSocketRateLimiter` | `class WebSocketRateLimiter` |
| `.__init__` | `def __init__( self, max_requests_per_minute: int = 60, max_burst: int = 20, )` |
| `.start` | `async def start(self)` |
| `.check_rate_limit` | `async def check_rate_limit(self, client_id: str, cost: float = 1.0) -> bool` |
| `._on_client_expired` | `def _on_client_expired(self, info: RateLimitInfo) -> None` |
| `.get_client_status` | `async def get_client_status(self, client_id: str) -> dict[str, float]` |
| `.shutdown` | `async def shutdown(self)` |

### `src/routers`

**`src/routers/dependencies.py`** — Centralized dependency injection functions for the Universal LLM Gateway API.

| Function | Signature |
|---|---|
| `get_model_registry` | `def get_model_registry(request: Request) -> ModelRegistry` |
| `get_model_metadata_adapter` | `def get_model_metadata_adapter(request: Request) -> ModelMetadataAdapter` |
| `get_worker_controller` | `def get_worker_controller(request: Request) -> WorkerController` |
| `get_gateway_config` | `def get_gateway_config(request: Request) -> GatewayConfig` |
| `get_event_bus` | `def get_event_bus(request: Request) -> EventBus` |
| `get_hot_reload_manager` | `def get_hot_reload_manager(request: Request) -> HotReloadManager` |

**`src/routers/health.py`** — Health check endpoint - /health

**`src/routers/metrics.py`** — System metrics endpoint - /metrics

**`src/routers/middleware.py`** — Middleware for CORS, error handling, and request logging

| Function | Signature |
|---|---|
| `setup_cors_middleware` | `def setup_cors_middleware(app: FastAPI, config: GatewayConfig) -> None` |
| `setup_security_middleware` | `def setup_security_middleware(app: FastAPI, config: GatewayConfig) -> None` |
| `logging_middleware` | `async def logging_middleware(request: Request, call_next: Callable) -> Response` |
| `rate_limiting_middleware` | `async def rate_limiting_middleware(request: Request, call_next: Callable) -> Response` |
| `setup_request_middleware` | `def setup_request_middleware(app: FastAPI, config: GatewayConfig) -> None` |
| `setup_all_middleware` | `def setup_all_middleware(app: FastAPI, config: GatewayConfig) -> None` |

### `src/routers/api/v1`

**`src/routers/api/v1/metrics.py`** — Metrics API endpoints.

| Function | Signature |
|---|---|
| `_is_local_subnet` | `def _is_local_subnet(client_ip: str) -> bool` |
| `verify_admin_access` | `async def verify_admin_access(request: Request, api_key: str \| None = None) -> bool` |

### `src/routers/api/v1/catalog`

**`src/routers/api/v1/catalog/__init__.py`** — Catalog API endpoints.

### `src/routers/api/v1/catalog/get`

**`src/routers/api/v1/catalog/get/__init__.py`** — Catalog API endpoints package-shadow of get.py.

**`src/routers/api/v1/catalog/get/deps.py`** — Shared router, logging, and optional local-catalog imports for catalog GET API.

**`src/routers/api/v1/catalog/get/mutations.py`** — Catalog mutation HTTP routes for reload, profile updates, and context activation.

**`src/routers/api/v1/catalog/get/queries.py`** — Catalog read-only HTTP routes for merged model catalog inspection and listing.

**`src/routers/api/v1/catalog/get/schemas.py`** — Pydantic request and response models for catalog API read and write endpoints.

| Class | Signature |
|---|---|
| `CatalogResponse` | `class CatalogResponse(BaseModel)` |
| `ModelEntryResponse` | `class ModelEntryResponse(BaseModel)` |
| `ProfileUpdate` | `class ProfileUpdate(BaseModel)` |
| `ActivatedContextsUpdate` | `class ActivatedContextsUpdate(BaseModel)` |
| `UpdateResponse` | `class UpdateResponse(BaseModel)` |
| `ModelSummary` | `class ModelSummary(BaseModel)` |
| `ModelSummaryListResponse` | `class ModelSummaryListResponse(BaseModel)` |

### `src/routers/api/v1/config`

**`src/routers/api/v1/config/__init__.py`** — Configuration management API endpoints.

**`src/routers/api/v1/config/reload.py`** — Hot reload API endpoints for configuration management.

| Class | Signature |
|---|---|
| `ReloadRequest` | `class ReloadRequest(BaseModel)` |
| `ReloadResponse` | `class ReloadResponse(BaseModel)` |
| `ReloadStatusResponse` | `class ReloadStatusResponse(BaseModel)` |

### `src/routers/api/v1/jobs`

**`src/routers/api/v1/jobs/__init__.py`** — Jobs API endpoints.

**`src/routers/api/v1/jobs/router.py`** — Jobs API Router - Background task execution with SSE streaming.

| Class | Signature |
|---|---|
| `MeasureJobRequestModel` | `class MeasureJobRequestModel(BaseModel)` |
| `JobResponse` | `class JobResponse(BaseModel)` |
| `JobListResponse` | `class JobListResponse(BaseModel)` |

### `src/routers/api/v1/management`

**`src/routers/api/v1/management/cancellation.py`** — Management API endpoints for streaming cancellation.

| Class | Signature |
|---|---|
| `CancelRequest` | `class CancelRequest(BaseModel)` |
| `CancelResponse` | `class CancelResponse(BaseModel)` |

### `src/routers/api/v1/model_info`

**`src/routers/api/v1/model_info/__init__.py`** — Model info router for /api/v1/model_info endpoints

**`src/routers/api/v1/model_info/aliases.py`** — GET /api/v1/model_info/aliases - Model aliases endpoint

**`src/routers/api/v1/model_info/configurations.py`** — GET /api/v1/model_info/configurations - Model configurations endpoint

**`src/routers/api/v1/model_info/stats.py`** — GET /api/v1/model_info/stats - Model statistics endpoint

**`src/routers/api/v1/model_info/validate.py`** — GET /api/v1/model_info/validate - Model validation endpoint

### `src/routers/api/v1/model_info/_model_id_`

**`src/routers/api/v1/model_info/_model_id_/__init__.py`** — Model-specific info endpoints for /api/v1/model_info/{model_id}

**`src/routers/api/v1/model_info/_model_id_/get.py`** — GET /api/v1/model_info/{model_id} - Get specific model info endpoint

### `src/routers/api/v1/models`

**`src/routers/api/v1/models/__init__.py`** — Model management endpoints for /api/v1/models

**`src/routers/api/v1/models/requirements.py`** — GET /api/v1/models/{model_id}/requirements - Model resource requirements

### `src/routers/api/v1/models/_model_id_`

**`src/routers/api/v1/models/_model_id_/__init__.py`** — Model-specific management endpoints for /api/v1/models/{model_id}

**`src/routers/api/v1/models/_model_id_/cleanup.py`** — POST /api/v1/models/{model_id}/cleanup - Force cleanup orphaned process

**`src/routers/api/v1/models/_model_id_/delete.py`** — DELETE /api/v1/models/{model_id} - Unload model endpoint

**`src/routers/api/v1/models/_model_id_/load.py`** — POST /api/v1/models/{model_id}/load — validation + async dispatch.

### `src/routers/api/v1/models/management`

**`src/routers/api/v1/models/management/__init__.py`** — Model Configuration Management API Router.

**`src/routers/api/v1/models/management/deps.py`** — Shared FastAPI dependencies and router for model catalog management API.

| Function | Signature |
|---|---|
| `get_catalog_manager_dep` | `def get_catalog_manager_dep() -> CatalogManager` |
| `get_gateway_config` | `def get_gateway_config(request: Request) -> GatewayConfig` |
| `check_management_api_enabled` | `def check_management_api_enabled( gateway_config: GatewayConfig = Depends(get_gateway_config), )` |
| `check_auth_token` | `def check_auth_token( gateway_config: GatewayConfig = Depends(get_gateway_config), x_management_token: str = Header(None), )` |

**`src/routers/api/v1/models/management/mutations.py`** — Catalog mutation endpoints for the model management API.

**`src/routers/api/v1/models/management/queries.py`** — Catalog query and reload endpoints for the model management API.

### `src/routers/api/v1/status`

**`src/routers/api/v1/status/detailed.py`** — GET /api/v1/status/detailed - Comprehensive gateway status for proxy orchestration

**`src/routers/api/v1/status/resources.py`** — GET /api/v1/status/resources - Resource status endpoint

### `src/routers/api/v1/status/resource_history`

**`src/routers/api/v1/status/resource_history/__init__.py`** — Resource History API - Query resource usage history for models.

**`src/routers/api/v1/status/resource_history/deps.py`** — Shared FastAPI router for resource history and monitoring status endpoints.

**`src/routers/api/v1/status/resource_history/guards.py`** — Guard helpers that verify process IPC resource monitoring is enabled.

| Function | Signature |
|---|---|
| `require_resource_monitor` | `def require_resource_monitor(worker_controller) -> None` |

**`src/routers/api/v1/status/resource_history/history.py`** — Resource history route returning current and peak usage snapshots for a model.

**`src/routers/api/v1/status/resource_history/monitoring.py`** — Resource monitoring availability route reporting process IPC monitor status.

**`src/routers/api/v1/status/resource_history/snapshots.py`** — Snapshot builders for model resource history and statistics API responses.

| Function | Signature |
|---|---|
| `fetch_model_usage` | `async def fetch_model_usage( worker_controller, model_id: str, logger: Any ) -> tuple[Any, Any]` |
| `_bytes_to_mb` | `def _bytes_to_mb(value: int \| float \| None) -> int` |
| `build_history_snapshots` | `def build_history_snapshots(current_usage, peak_usage) -> list[dict[str, Any]]` |
| `build_stats_snapshots` | `def build_stats_snapshots(current_usage, peak_usage) -> list[dict[str, Any]]` |

**`src/routers/api/v1/status/resource_history/stats.py`** — Resource statistics route summarizing worker and system usage for a model.

**`src/routers/api/v1/status/resource_history/stats_aggregation.py`** — Aggregate resource statistics from normalized snapshot records.

| Function | Signature |
|---|---|
| `_metric_values` | `def _metric_values(snapshots: list[dict[str, Any]], key: str) -> list[Any]` |
| `_usage_summary` | `def _usage_summary( values: list[Any], max_config_key: str, snapshots: list[dict[str, Any]] ) -> dict[str, Any]` |
| `build_resource_stats_response` | `def build_resource_stats_response( model_id: str, snapshots: list[dict[str, Any]] ) -> dict[str, Any]` |

### `src/routers/api/v1/tokens`

**`src/routers/api/v1/tokens/count.py`** — POST /api/v1/tokens/count - Token counting endpoint

### `src/routers/model_metadata_adapter`

**`src/routers/model_metadata_adapter/__init__.py`** — Model metadata adapter package for API-friendly registry formatting.

**`src/routers/model_metadata_adapter/adapter.py`** — ModelMetadataAdapter facade wiring registry data to API-friendly responses.

| Class | Signature |
|---|---|
| `ModelMetadataAdapter` | `class ModelMetadataAdapter` |
| `.__init__` | `def __init__( self, model_registry: ModelRegistry, gateway_config: Any \| None = None )` |
| `._load_configuration` | `def _load_configuration(self)` |
| `._has_working_chat_template` | `def _has_working_chat_template(self, model_path: str, model_format: str) -> bool` |
| `.get_chat_template_from_config` | `def get_chat_template_from_config( self, model_id: str, model_config: dict[str, Any] ) -> dict[str, Any]` |
| `.get_model_type_from_config` | `def get_model_type_from_config( self, model_id: str, model_config: dict[str, Any] ) -> str` |
| `.get_inference_engine_info` | `def get_inference_engine_info(self, model_id: str) -> dict[str, Any]` |
| `.get_comprehensive_model_metadata` | `def get_comprehensive_model_metadata(self, model_id: str) -> dict[str, Any] \| None` |
| `.get_all_models_metadata` | `def get_all_models_metadata(self) -> dict[str, dict[str, Any]]` |

**`src/routers/model_metadata_adapter/chat_template.py`** — Chat template resolution from YAML configuration and djinn inspector probes.

| Function | Signature |
|---|---|
| `has_working_chat_template` | `def has_working_chat_template( registry: ModelRegistry, model_path: str, model_format: str ) -> bool` |
| `get_chat_template_from_config` | `def get_chat_template_from_config( model_id: str, model_config: dict[str, Any] ) -> dict[str, Any]` |

**`src/routers/model_metadata_adapter/comprehensive_metadata.py`** — Build comprehensive per-model metadata payloads for model_info API endpoints.

| Function | Signature |
|---|---|
| `get_comprehensive_model_metadata` | `def get_comprehensive_model_metadata( registry: ModelRegistry, model_id: str, inference_engine_specs: dict[str, Any], get_model_type_from_config, ) -> dict[str, Any] \| None` |

**`src/routers/model_metadata_adapter/inference_engine.py`** — Inference engine metadata derived from gateway config and registry model info.

| Function | Signature |
|---|---|
| `get_inference_engine_info` | `def get_inference_engine_info( registry: ModelRegistry, model_id: str, inference_engine_specs: dict[str, Any], ) -> dict[str, Any]` |

**`src/routers/model_metadata_adapter/model_info_extract.py`** — Pure mapping from model_loaders.yaml configuration to API response fields.

| Function | Signature |
|---|---|
| `extract_comprehensive_model_info` | `def extract_comprehensive_model_info( model_id: str, model_config: dict[str, Any] ) -> dict[str, Any]` |

**`src/routers/model_metadata_adapter/text_normalization.py`** — String helpers for normalizing model metadata fields in API responses.

| Function | Signature |
|---|---|
| `safe_lower` | `def safe_lower(value: str \| None) -> str` |

### `src/routers/v1`

**`src/routers/v1/embeddings.py`** — Embeddings endpoint - /v1/embeddings

| Function | Signature |
|---|---|
| `_emit_embedding_debug` | `async def _emit_embedding_debug( step: str, correlation_id: str, model_id: str, **extra: object, ) -> None` |

**`src/routers/v1/rerank.py`** — Rerank endpoint — /v1/rerank

| Function | Signature |
|---|---|
| `_emit_rerank_debug` | `async def _emit_rerank_debug( step: str, correlation_id: str, model_id: str, **extra: object, ) -> None` |

### `src/routers/v1/audio`

**`src/routers/v1/audio/__init__.py`** — Audio routers for Whisper transcription (streaming and file-based).

**`src/routers/v1/audio/error_mapper.py`** — Error mapping for audio streaming endpoints.

| Function | Signature |
|---|---|
| `map_worker_error` | `def map_worker_error(error: Exception) -> tuple[str, str, bool]` |

**`src/routers/v1/audio/params.py`** — FastAPI query parameter definitions for audio streaming endpoint.

| Class | Signature |
|---|---|
| `VADMethod` | `class VADMethod(StrEnum)` |

| Function | Signature |
|---|---|
| `get_model_param` | `def get_model_param()` |
| `get_session_timeout_param` | `def get_session_timeout_param()` |
| `get_inactivity_timeout_param` | `def get_inactivity_timeout_param()` |
| `get_language_param` | `def get_language_param()` |
| `get_beam_size_param` | `def get_beam_size_param()` |
| `get_temperature_param` | `def get_temperature_param()` |
| `get_condition_on_previous_text_param` | `def get_condition_on_previous_text_param()` |
| `get_vad_method_param` | `def get_vad_method_param()` |
| `get_min_window_duration_param` | `def get_min_window_duration_param()` |
| `get_max_window_duration_param` | `def get_max_window_duration_param()` |
| `get_silero_threshold_param` | `def get_silero_threshold_param()` |
| `get_silero_min_silence_ms_param` | `def get_silero_min_silence_ms_param()` |
| `get_energy_threshold_param` | `def get_energy_threshold_param()` |
| `get_speech_pad_ms_param` | `def get_speech_pad_ms_param()` |
| `get_silence_retention_ratio_param` | `def get_silence_retention_ratio_param()` |
| `get_speech_preservation_ms_param` | `def get_speech_preservation_ms_param()` |
| `get_overlap_duration_ms_param` | `def get_overlap_duration_ms_param()` |
| `get_use_whisper_vad_param` | `def get_use_whisper_vad_param()` |
| `get_overlap_correction_enabled_param` | `def get_overlap_correction_enabled_param()` |
| `get_overlap_hold_word_count_param` | `def get_overlap_hold_word_count_param()` |
| `get_overlap_max_time_gap_ms_param` | `def get_overlap_max_time_gap_ms_param()` |
| `get_overlap_min_prefix_ratio_param` | `def get_overlap_min_prefix_ratio_param()` |
| `get_second_pass_enabled_param` | `def get_second_pass_enabled_param()` |
| `get_second_pass_min_silence_duration_ms_param` | `def get_second_pass_min_silence_duration_ms_param()` |
| `get_second_pass_scan_step_ms_param` | `def get_second_pass_scan_step_ms_param()` |
| `get_second_pass_max_search_depth_ms_param` | `def get_second_pass_max_search_depth_ms_param()` |
| `get_second_pass_leave_behind_ms_param` | `def get_second_pass_leave_behind_ms_param()` |
| `get_second_pass_vad_method_param` | `def get_second_pass_vad_method_param()` |
| `get_min_word_probability_param` | `def get_min_word_probability_param()` |
| `get_adaptive_preservation_enabled_param` | `def get_adaptive_preservation_enabled_param()` |
| `get_max_window_leave_behind_ms_param` | `def get_max_window_leave_behind_ms_param()` |

**`src/routers/v1/audio/session_utils.py`** — Session management utilities for audio streaming.

| Function | Signature |
|---|---|
| `parse_temperature_json` | `def parse_temperature_json(temperature_json: str) -> list[float]` |
| `clamp_timeout` | `def clamp_timeout(value: int \| None, default: int, max_allowed: int, name: str) -> int` |
| `build_timeout_info` | `def build_timeout_info( session_timeout: int, inactivity_timeout: int ) -> tuple[str, dict[str, Any], bool]` |
| `build_session_config` | `def build_session_config( # noqa: PLR0913 language: str \| None, beam_size: int \| None, temperature: str \| None, condition_on_previous_text: bool \| None, vad_method: str \| None, min_window_duration: float \| None, max_window_duration: float \| None, silero_threshold: float \| None, silero_min_silence_ms: int \| None, energy_threshold: float \| None, speech_pad_ms: int \| None, silence_retention_ratio: float \| None, speech_preservation_ms: int \| None, overlap_duration_ms: int \| None, use_whisper_vad: bool \| None, overlap_correction_enabled: bool \| None = None, overlap_hold_word_count: int \| None = None, overlap_max_time_gap_ms: int \| None = None, overlap_min_prefix_ratio: float \| None = None, second_pass_enabled: bool \| None = None, second_pass_min_silence_duration_ms: int \| None = None, second_pass_scan_step_ms: int \| None = None, second_pass_max_search_depth_ms: int \| None = None, second_pass_leave_behind_ms: int \| None = None, second_pass_vad_method: str \| None = None, min_word_probability: float \| None = None, adaptive_preservation_enabled: bool \| None = None, max_window_leave_behind_ms: int \| None = None, ) -> dict[str, Any]` |
| `cleanup_session` | `async def cleanup_session( worker_controller, model: str, session_id: str, request_id: str ) -> list[dict]` |

**`src/routers/v1/audio/transcriptions.py`** — OpenAI-compatible file transcription endpoint for Whisper ASR.

| Function | Signature |
|---|---|
| `_validate_audio_sync` | `def _validate_audio_sync(temp_path: str) -> tuple[float, int, int]` |
| `_validate_audio_file` | `async def _validate_audio_file(temp_path: str, request_id: str) -> float` |
| `_format_response` | `def _format_response(result: dict[str, Any], response_format: str)` |
| `_format_timestamp` | `def _format_timestamp(seconds: float, use_comma: bool = True) -> str` |
| `_generate_srt` | `def _generate_srt(segments: list) -> str` |
| `_generate_vtt` | `def _generate_vtt(segments: list) -> str` |

### `src/routers/v1/audio/stream`

**`src/routers/v1/audio/stream/__init__.py`** — WebSocket endpoint package for real-time audio streaming transcription.

**`src/routers/v1/audio/stream/chunk_validation.py`** — PCM audio chunk validation for live transcription WebSocket streams.

| Function | Signature |
|---|---|
| `validate_audio_chunk` | `def validate_audio_chunk( audio_bytes: bytes, consecutive_errors: int ) -> ChunkValidationResult` |
| `_invalid_chunk` | `def _invalid_chunk( consecutive_errors: int, code: str, message: str ) -> ChunkValidationResult` |

**`src/routers/v1/audio/stream/deps.py`** — Shared FastAPI router and logger for live audio WebSocket transcription.

**`src/routers/v1/audio/stream/live_transcribe.py`** — WebSocket route handler for live audio streaming transcription.

**`src/routers/v1/audio/stream/session_bootstrap.py`** — Session bootstrap for live audio WebSocket transcription connections.

| Function | Signature |
|---|---|
| `bootstrap_stream_session` | `async def bootstrap_stream_session( *, websocket: WebSocket, worker_controller, request_id: str, model: str, session_config: dict[str, Any], ready_message: dict[str, Any], ) -> str \| None` |

**`src/routers/v1/audio/stream/streaming_loop.py`** — Main receive/process loop for live audio WebSocket transcription sessions.

| Function | Signature |
|---|---|
| `run_streaming_loop` | `async def run_streaming_loop( *, websocket: WebSocket, worker_controller, request_id: str, model: str, session_id: str, loop: asyncio.AbstractEventLoop, session_start: float, effective_session_timeout: int, effective_inactivity_timeout: int, ) -> None` |
| `_receive_audio_bytes` | `async def _receive_audio_bytes( websocket: WebSocket, effective_inactivity_timeout: int ) -> bytes \| None` |

**`src/routers/v1/audio/stream/websocket_errors.py`** — WebSocket error helpers for live audio transcription sessions.

| Function | Signature |
|---|---|
| `send_websocket_error` | `async def send_websocket_error( websocket: WebSocket, code: str, message: str, *, close: bool = False ) -> None` |

### `src/routers/v1/chat/completions`

**`src/routers/v1/chat/completions/__init__.py`** — Chat completions endpoint.

**`src/routers/v1/chat/completions/disconnect.py`** — Non-streaming client disconnect detection and cancellation.

| Function | Signature |
|---|---|
| `cancel_non_streaming_inference` | `async def cancel_non_streaming_inference( worker_controller, model_id: str, request_id: str, ) -> None` |
| `inference_with_disconnect_watch` | `async def inference_with_disconnect_watch( inference_coro: Coroutine, request: Request, worker_controller, model_id: str, request_id: str, ) -> dict` |

**`src/routers/v1/chat/completions/events.py`** — Request-scoped event emission helpers.

| Function | Signature |
|---|---|
| `emit_request_queued_nowait` | `async def emit_request_queued_nowait( event_bus: EventBus, model_id: str, request_id: str, messages, parameters: dict, stream: bool, ) -> None` |
| `emit_inference_failed_nowait` | `async def emit_inference_failed_nowait( event_bus: EventBus, model_id: str, request_id: str, error_message: str, ) -> None` |

**`src/routers/v1/chat/completions/model_resolution.py`** — Model ID resolution for chat completions.

| Function | Signature |
|---|---|
| `resolve_model_id` | `def resolve_model_id( model_override: str \| None, request_model: str \| None, model_registry: ModelRegistry \| None, ) -> ModelId` |

**`src/routers/v1/chat/completions/non_streaming.py`** — Non-streaming chat completion execution path.

| Function | Signature |
|---|---|
| `generate_non_streaming_response` | `async def generate_non_streaming_response( worker_controller: Any, model_id: str, messages: Any, request_id: str, correlation_id: str, start_time: float, event_bus: Any, generation_params: dict, timeout_hint: float \| None = None, request: Request \| None = None, )` |

**`src/routers/v1/chat/completions/openai_errors.py`** — OpenAI-compliant error response builders.

| Function | Signature |
|---|---|
| `validate_openai_error_type` | `def validate_openai_error_type(error_type: str) -> str` |
| `build_openai_error_payload` | `def build_openai_error_payload( message: str, error_type: str, error_code: str \| None = None, param: str \| None = None, ) -> dict` |
| `create_openai_error_response` | `def create_openai_error_response( status_code: int, message: str, error_type: str, error_code: str \| None = None, param: str \| None = None, request_id: str \| None = None, duration_ms: float \| None = None, suggestion: str \| None = None, ) -> JSONResponse` |
| `create_model_crash_error_response` | `def create_model_crash_error_response( model_id: str, error_message: str, request_id: str \| None = None, duration_ms: float \| None = None, ) -> JSONResponse` |

**`src/routers/v1/chat/completions/response_build.py`** — Pure passthrough generation params and OpenAI-shaped completion responses.

| Function | Signature |
|---|---|
| `build_generation_params` | `def build_generation_params(completion_request: ChatCompletionRequest) -> dict` |
| `build_completion_response` | `def build_completion_response( completion_result: dict, model_id: str, ) -> ChatCompletionResponse` |
| `resolve_gateway_url` | `def resolve_gateway_url(request: Request \| None) -> str` |

**`src/routers/v1/chat/completions/route.py`** — Chat completions endpoint - /v1/chat/completions

| Function | Signature |
|---|---|
| `_extract_internal_request_id` | `def _extract_internal_request_id(request: Request) -> str \| None` |

**`src/routers/v1/chat/completions/runtime_errors.py`** — Map worker/runtime failures to OpenAI-compatible HTTP responses.

| Function | Signature |
|---|---|
| `handle_runtime_error` | `def handle_runtime_error( e: RuntimeError, model_id: str, request_id: str, response_time_ms: float, )` |

### `src/routers/v1/chat/completions/stream`

**`src/routers/v1/chat/completions/stream/__init__.py`** — Streaming response package for chat completions.

**`src/routers/v1/chat/completions/stream/error_mapping.py`** — Error classification and response mapping for streaming errors.

| Class | Signature |
|---|---|
| `ErrorClassification` | `class ErrorClassification(StrEnum)` |

| Function | Signature |
|---|---|
| `classify_runtime_error` | `def classify_runtime_error(error_message: str) -> ErrorClassification` |
| `_format_request_context` | `def _format_request_context(request_id: str \| None) -> str` |
| `iter_timeout_error_events` | `def iter_timeout_error_events( error_str: str, request_id: str \| None = None, )` |
| `iter_runtime_error_events` | `def iter_runtime_error_events( error: RuntimeError, model_id: str, request_id: str \| None = None, )` |
| `iter_process_error_events` | `def iter_process_error_events( error: Exception, model_id: str, request_id: str \| None = None, )` |

**`src/routers/v1/chat/completions/stream/ndjson.py`** — NDJSON serialization for streaming responses.

| Function | Signature |
|---|---|
| `create_ndjson_event` | `def create_ndjson_event(signal: str, payload) -> dict` |
| `serialize_ndjson_event` | `def serialize_ndjson_event(event: dict) -> str` |
| `convert_worker_chunk_to_openai_format` | `def convert_worker_chunk_to_openai_format(chunk: dict) -> dict` |
| `iter_error_and_complete_events` | `def iter_error_and_complete_events(message: str, error_type: str, error_code: str)` |

**`src/routers/v1/chat/completions/stream/response.py`** — Streaming response generation for chat completions.

| Function | Signature |
|---|---|
| `generate_streaming_response` | `async def generate_streaming_response( worker_controller, model_id: str, messages, event_bus: EventBus, correlation_id: str \| None = None, timeout_hint: float \| None = None, request_id: str \| None = None, gateway_url: str = "unknown", **kwargs, )` |

### `src/routers/v1/images`

**`src/routers/v1/images/__init__.py`** — Images API endpoints.

**`src/routers/v1/images/generations.py`** — OpenAI-compatible image generation endpoint for Flux.2.

**`src/routers/v1/images/schemas.py`** — Image generation request/response schemas.

| Class | Signature |
|---|---|
| `ImageGenerationRequest` | `class ImageGenerationRequest(BaseModel)` |
| `ImageData` | `class ImageData(BaseModel)` |
| `ImageGenerationResponse` | `class ImageGenerationResponse(BaseModel)` |

**`src/routers/v1/images/validation.py`** — Image generation request validation.

| Function | Signature |
|---|---|
| `parse_and_validate_size` | `def parse_and_validate_size(size: str) -> tuple[int, int]` |

**`src/routers/v1/images/worker_ops.py`** — Worker operations for image generation.

| Function | Signature |
|---|---|
| `resolve_generation_params` | `def resolve_generation_params( request: ImageGenerationRequest, width: int, height: int ) -> dict` |
| `generate_image_via_worker` | `async def generate_image_via_worker( worker_controller, request: ImageGenerationRequest, width: int, height: int, ) -> ImageGenerationResponse` |

### `src/routers/v1/models`

**`src/routers/v1/models/__init__.py`** — Models router for /v1/models endpoints

**`src/routers/v1/models/extended.py`** — GET /v1/models/extended - Extended models endpoint

| Function | Signature |
|---|---|
| `load_openai_models_config` | `def load_openai_models_config() -> dict[str, Any]` |
| `get_extended_models` | `def get_extended_models() -> list[ExtendedModelInfo]` |

**`src/routers/v1/models/get.py`** — GET /v1/models - List models endpoint

| Class | Signature |
|---|---|
| `ListModelsQuery` | `class ListModelsQuery(BaseModel)` |

| Function | Signature |
|---|---|
| `build_comprehensive_model_info_for_synthetic` | `def build_comprehensive_model_info_for_synthetic( synthetic_model, config: dict[str, Any], model_registry: ModelRegistry = None ) -> ComprehensiveModelInfo` |
| `get_openai_models` | `def get_openai_models( config: dict, include_all_variants: bool = True ) -> list[OpenAIModelInfo]` |
| `get_comprehensive_models` | `def get_comprehensive_models( config: dict, include_all_variants: bool = True ) -> list[ComprehensiveModelInfo]` |

### `src/routers/v1/models/_model_id_`

**`src/routers/v1/models/_model_id_/__init__.py`** — Model-specific endpoints for /v1/models/{model_id}

**`src/routers/v1/models/_model_id_/extended.py`** — GET /v1/models/{model_id}/extended - Extended model info endpoint

| Function | Signature |
|---|---|
| `load_openai_models_config` | `def load_openai_models_config() -> dict[str, Any]` |
| `get_extended_models` | `def get_extended_models() -> list[ExtendedModelInfo]` |

**`src/routers/v1/models/_model_id_/get.py`** — GET /v1/models/{model_id} - Get specific model endpoint

| Function | Signature |
|---|---|
| `find_synthetic_model` | `def find_synthetic_model( all_synthetic_models: list[SyntheticModel], model_id: str ) -> SyntheticModel \| None` |

### `src/routers/ws`

**`src/routers/ws/__init__.py`** — WebSocket routers for real-time state streaming.

**`src/routers/ws/stargate.py`** — WebSocket endpoint for Stargate control plane.

| Function | Signature |
|---|---|
| `_handle_query` | `async def _handle_query( message: dict[str, Any], model_registry: ModelRegistry, worker_controller: Any, init_cache: InitDataCache, ) -> WebSocketMessage` |

**`src/routers/ws/state.py`** — WebSocket endpoint for real-time state streaming.

| Class | Signature |
|---|---|
| `StateChannelHandler` | `class StateChannelHandler` |
| `.__init__` | `def __init__( self, websocket: WebSocket, state_provider, auth_info: APIKeyInfo \| None = None, )` |
| `.send_state_update` | `async def send_state_update(self, update: StateUpdate)` |
| `.send_state_delta` | `async def send_state_delta(self, delta: StateDelta)` |
| `.send_full_state` | `async def send_full_state(self)` |
| `.handle_message` | `async def handle_message(self, data: dict, message_size: int)` |
| `._matches_subscriptions` | `def _matches_subscriptions(self, path: str) -> bool` |
| `._send_json` | `async def _send_json(self, data: dict)` |

| Function | Signature |
|---|---|
| `get_authenticator` | `def get_authenticator() -> WebSocketAuthenticator` |

### `src/schemas`

**`src/schemas/__init__.py`** — Pydantic schemas for API request/response validation

**`src/schemas/capabilities.py`** — Central ModelCapabilities schema for catalog metadata (v4).

| Class | Signature |
|---|---|
| `CapabilityModalities` | `class CapabilityModalities(BaseModel)` |
| `CapabilityInteraction` | `class CapabilityInteraction(BaseModel)` |
| `CapabilityReasoning` | `class CapabilityReasoning(BaseModel)` |
| `CapabilityLimits` | `class CapabilityLimits(BaseModel)` |
| `CapabilityProvenance` | `class CapabilityProvenance(BaseModel)` |
| `CapabilitySpecialization` | `class CapabilitySpecialization(BaseModel)` |
| `CapabilityDispatchMaxOutput` | `class CapabilityDispatchMaxOutput(BaseModel)` |
| `CapabilityDispatchReasoning` | `class CapabilityDispatchReasoning(BaseModel)` |
| `CapabilityDispatchKnobSpec` | `class CapabilityDispatchKnobSpec(BaseModel)` |
| `CapabilityDispatchSpecializations` | `class CapabilityDispatchSpecializations(BaseModel)` |
| `CapabilityDispatchFacet` | `class CapabilityDispatchFacet(BaseModel)` |
| `ModelCapabilities` | `class ModelCapabilities(BaseModel)` |

| Function | Signature |
|---|---|
| `capabilities_from_dict` | `def capabilities_from_dict(data: dict[str, Any]) -> ModelCapabilities` |
| `default_capabilities_dict` | `def default_capabilities_dict() -> dict[str, Any]` |

**`src/schemas/chat_completion.py`** — OpenAI Chat Completion API compatible schemas

| Class | Signature |
|---|---|
| `ChatMessage` | `class ChatMessage(BaseModel)` |
| `ChatCompletionRequest` | `class ChatCompletionRequest(BaseModel)` |
| `ChatCompletionChoice` | `class ChatCompletionChoice(BaseModel)` |
| `ChatCompletionUsage` | `class ChatCompletionUsage(BaseModel)` |
| `ChatCompletionResponse` | `class ChatCompletionResponse(BaseModel)` |
| `ChatCompletionStreamDelta` | `class ChatCompletionStreamDelta(BaseModel)` |
| `ChatCompletionStreamChoice` | `class ChatCompletionStreamChoice(BaseModel)` |
| `ChatCompletionStreamResponse` | `class ChatCompletionStreamResponse(BaseModel)` |

**`src/schemas/embedding.py`** — OpenAI Embeddings API compatible schemas.

| Class | Signature |
|---|---|
| `EmbeddingRequest` | `class EmbeddingRequest(BaseModel)` |
| `EmbeddingData` | `class EmbeddingData(BaseModel)` |
| `EmbeddingUsage` | `class EmbeddingUsage(BaseModel)` |
| `EmbeddingResponse` | `class EmbeddingResponse(BaseModel)` |

**`src/schemas/gateway_status.py`** — Schemas for rich gateway status API for proxy orchestration

| Class | Signature |
|---|---|
| `ModelResourceUsage` | `class ModelResourceUsage(BaseModel)` |
| `ModelStatusDetail` | `class ModelStatusDetail(BaseModel)` |
| `OperationsInProgress` | `class OperationsInProgress(BaseModel)` |
| `GatewayHealthInfo` | `class GatewayHealthInfo(BaseModel)` |
| `ResourceInfo` | `class ResourceInfo(BaseModel)` |
| `QueueInfo` | `class QueueInfo(BaseModel)` |
| `DetailedStatusResponse` | `class DetailedStatusResponse(BaseModel)` |
| `ResourceRequirements` | `class ResourceRequirements(BaseModel)` |
| `ModelCapabilities` | `class ModelCapabilities(BaseModel)` |
| `ModelRequirementsResponse` | `class ModelRequirementsResponse(BaseModel)` |

**`src/schemas/model_info.py`** — Model information and metadata schemas

| Class | Signature |
|---|---|
| `InferenceEngineInfo` | `class InferenceEngineInfo(BaseModel)` |
| `ChatTemplateInfo` | `class ChatTemplateInfo(BaseModel)` |
| `ModelInfo` | `class ModelInfo(BaseModel)` |
| `ModelConfigurationsResponse` | `class ModelConfigurationsResponse(BaseModel)` |
| `ModelValidationResult` | `class ModelValidationResult(BaseModel)` |
| `ModelValidationReport` | `class ModelValidationReport(BaseModel)` |

**`src/schemas/model_management.py`** — API request and response schemas for model catalog management.

| Class | Signature |
|---|---|
| `AddModelRequest` | `class AddModelRequest(BaseModel)` |
| `UpdateModelRequest` | `class UpdateModelRequest(BaseModel)` |
| `ReloadConfigRequest` | `class ReloadConfigRequest(BaseModel)` |
| `ModelManagementResponse` | `class ModelManagementResponse(BaseModel)` |
| `GetModelConfigResponse` | `class GetModelConfigResponse(BaseModel)` |
| `ReloadConfigResponse` | `class ReloadConfigResponse(BaseModel)` |
| `ModelListItem` | `class ModelListItem(BaseModel)` |
| `ListModelsResponse` | `class ListModelsResponse(BaseModel)` |

**`src/schemas/openai_models.py`** — OpenAI-compatible model schemas with minimum required fields

| Class | Signature |
|---|---|
| `OpenAIModelInfo` | `class OpenAIModelInfo(BaseModel)` |
| `OpenAIModelListResponse` | `class OpenAIModelListResponse(BaseModel)` |
| `ExtendedModelInfo` | `class ExtendedModelInfo(BaseModel)` |
| `ExtendedModelListResponse` | `class ExtendedModelListResponse(BaseModel)` |
| `ComprehensiveModelInfo` | `class ComprehensiveModelInfo(BaseModel)` |
| `ComprehensiveModelListResponse` | `class ComprehensiveModelListResponse(BaseModel)` |

**`src/schemas/rerank.py`** — Rerank API schemas — request/response models for cross-encoder reranking.

| Class | Signature |
|---|---|
| `RerankRequest` | `class RerankRequest(BaseModel)` |
| `RerankResponse` | `class RerankResponse(BaseModel)` |

**`src/schemas/responses.py`** — General API response schemas

| Class | Signature |
|---|---|
| `HealthResponse` | `class HealthResponse(BaseModel)` |
| `MetricsResponse` | `class MetricsResponse(BaseModel)` |
| `ErrorResponse` | `class ErrorResponse(BaseModel)` |
| `ValidationErrorResponse` | `class ValidationErrorResponse(BaseModel)` |
| `SuccessResponse` | `class SuccessResponse(BaseModel)` |

**`src/schemas/tokens.py`** — Token counting and management schemas

| Class | Signature |
|---|---|
| `ImageURL` | `class ImageURL(BaseModel)` |
| `ContentPartText` | `class ContentPartText(BaseModel)` |
| `ContentPartImage` | `class ContentPartImage(BaseModel)` |
| `Message` | `class Message(BaseModel)` |
| `TokenCountRequest` | `class TokenCountRequest(BaseModel)` |
| `TokenCountResponse` | `class TokenCountResponse(BaseModel)` |
| `TokenCountError` | `class TokenCountError(BaseModel)` |
| `TokenMetrics` | `class TokenMetrics(BaseModel)` |

**`src/schemas/yaml_config.py`** — YAML configuration schemas - Source of Truth for model_loaders.yaml

| Class | Signature |
|---|---|
| `OpenAPIFields` | `class OpenAPIFields(BaseModel)` |
| `ResourceConfig` | `class ResourceConfig(BaseModel)` |
| `GGUFModelInfo` | `class GGUFModelInfo(BaseModel)` |
| `GGUFLoaderConfig` | `class GGUFLoaderConfig(BaseModel)` |
| `GGUFProfileLoader` | `class GGUFProfileLoader(BaseModel)` |
| `GGUFProfile` | `class GGUFProfile(BaseModel)` |
| `GGUFModelConfig` | `class GGUFModelConfig(BaseModel)` |
| `HFModelInfo` | `class HFModelInfo(BaseModel)` |
| `HFLoaderConfig` | `class HFLoaderConfig(BaseModel)` |
| `HFProfileLoader` | `class HFProfileLoader(BaseModel)` |
| `HFProfile` | `class HFProfile(BaseModel)` |
| `HFModelConfig` | `class HFModelConfig(BaseModel)` |
| `ResourceManagement` | `class ResourceManagement(BaseModel)` |
| `ModelLoadersConfig` | `class ModelLoadersConfig(BaseModel)` |

### `src/schemas/resource_management`

**`src/schemas/resource_management/__init__.py`** — Queue management API response schemas for Universal LLM Gateway.

**`src/schemas/resource_management/error_codes.py`** — Standard string error codes for queue management API failure responses.

| Class | Signature |
|---|---|
| `ErrorCodes` | `class ErrorCodes` |

**`src/schemas/resource_management/literals.py`** — Literal type aliases for queue management API model status and priority levels.

**`src/schemas/resource_management/load_unload.py`** — Pydantic schemas for model load and unload request and response payloads.

| Class | Signature |
|---|---|
| `ModelLoadRequest` | `class ModelLoadRequest(BaseModel)` |
| `ModelLoadResponse` | `class ModelLoadResponse(BaseModel)` |
| `ModelLoadErrorResponse` | `class ModelLoadErrorResponse(BaseModel)` |
| `ModelUnloadRequest` | `class ModelUnloadRequest(BaseModel)` |
| `ModelUnloadResponse` | `class ModelUnloadResponse(BaseModel)` |

**`src/schemas/resource_management/model_status.py`** — Pydantic schemas for per-model and catalog-wide model status API responses.

| Class | Signature |
|---|---|
| `ModelStatusInfo` | `class ModelStatusInfo(BaseModel)` |
| `ModelStatusResponse` | `class ModelStatusResponse(BaseModel)` |
| `SingleModelStatusResponse` | `class SingleModelStatusResponse(BaseModel)` |

**`src/schemas/resource_management/requirements.py`** — Pydantic schema for model hardware requirement and timing estimates.

| Class | Signature |
|---|---|
| `ModelRequirementsResponse` | `class ModelRequirementsResponse(BaseModel)` |

**`src/schemas/resource_management/resource_status.py`** — Pydantic schemas for aggregate resource status and per-model resource details.

| Class | Signature |
|---|---|
| `ModelResourceUsage` | `class ModelResourceUsage(BaseModel)` |
| `ModelDetails` | `class ModelDetails(BaseModel)` |
| `ResourceStatusResponse` | `class ResourceStatusResponse(BaseModel)` |

### `src/tools`

**`src/tools/discovery.py`** — Model discovery and metadata extraction using inference_djinn inspectors.

| Class | Signature |
|---|---|
| `DiscoveryError` | `class DiscoveryError(Exception)` |
| `ModelDiscovery` | `class ModelDiscovery` |
| `.__init__` | `def __init__(self)` |
| `._check_djinn_availability` | `def _check_djinn_availability(self) -> bool` |
| `.discover_gguf` | `def discover_gguf(self, model_path: str \| Path) -> dict[str, Any]` |
| `.discover_hf` | `def discover_hf( self, model_path: str \| Path, format_hint: Literal["hf", "gptq", "awq"] = "hf" ) -> dict[str, Any]` |
| `._map_gguf_to_config` | `def _map_gguf_to_config( self, djinn_info: dict[str, Any], model_path: Path ) -> dict[str, Any]` |
| `._map_hf_to_config` | `def _map_hf_to_config( self, djinn_info: dict[str, Any], model_path: Path, format_hint: str ) -> dict[str, Any]` |
| `._infer_quant_from_filename` | `def _infer_quant_from_filename(self, filename: str) -> int \| None` |
| `.discover_auto` | `def discover_auto(self, model_path: str \| Path) -> dict[str, Any]` |

| Function | Signature |
|---|---|
| `discover_model` | `def discover_model(model_path: str \| Path) -> dict[str, Any]` |

### `src/utils`

**`src/utils/__init__.py`** — Utility modules for logging, monitoring, and validation

**`src/utils/examples.py`** — Schema-driven example generation for model configurations.

| Class | Signature |
|---|---|
| `SchemaIntrospector` | `class SchemaIntrospector` |
| `.get_type_annotation` | `def get_type_annotation(self, field_info: FieldInfo) -> str` |
| `._get_base_type` | `def _get_base_type(self, annotation) -> str` |
| `.get_example_value` | `def get_example_value(self, field_info: FieldInfo, field_name: str) -> Any` |
| `.get_field_annotation` | `def get_field_annotation(self, field_info: FieldInfo)` |
| `ExampleGenerator` | `class ExampleGenerator` |
| `.__init__` | `def __init__(self)` |
| `.get_example_dict` | `def get_example_dict(self, format_type: str) -> dict[str, Any]` |
| `._generate_config_dict` | `def _generate_config_dict(self, schema: type[BaseModel]) -> dict[str, Any]` |
| `._generate_profile_example` | `def _generate_profile_example(self) -> dict[str, Any]` |
| `._generate_cpu_profile_example` | `def _generate_cpu_profile_example(self) -> dict[str, Any]` |
| `.get_schema_info` | `def get_schema_info(self, format_type: str) -> dict[str, Any]` |
| `._extract_field_info` | `def _extract_field_info( self, schema: type[BaseModel], all_fields: list[str], required_fields: list[str], optional_fields: list[str], prefix: str = "", )` |

| Function | Signature |
|---|---|
| `get_example_for_format` | `def get_example_for_format(format_type: str) -> dict[str, Any]` |
| `get_schema_info` | `def get_schema_info(format_type: str) -> dict[str, Any]` |

**`src/utils/model_utils.py`** — Model utilities for the universal-llm-gateway.

| Function | Signature |
|---|---|
| `is_token_counting_available` | `def is_token_counting_available(model_metadata: dict) -> bool` |
| `detect_model_type` | `def detect_model_type( model_id: str, model_config: dict \| None = None, model_path: str \| None = None ) -> str` |
| `convert_metadata_to_model_info` | `def convert_metadata_to_model_info(model_metadata: dict) -> ModelInfo` |

**`src/utils/monitoring.py`** — System monitoring and metrics utilities

| Class | Signature |
|---|---|
| `SystemMonitor` | `class SystemMonitor` |
| `.__init__` | `def __init__(self)` |
| `.get_uptime_seconds` | `def get_uptime_seconds(self) -> float` |
| `.get_cpu_info` | `def get_cpu_info(self) -> dict[str, Any]` |
| `.get_memory_info` | `def get_memory_info(self) -> dict[str, Any]` |
| `.get_disk_info` | `def get_disk_info(self) -> dict[str, Any]` |
| `.get_gpu_info` | `def get_gpu_info(self) -> dict[str, Any] \| None` |
| `.get_system_info` | `def get_system_info(self) -> dict[str, Any]` |
| `.get_process_info` | `def get_process_info(self) -> dict[str, Any]` |
| … | (10 methods total) |

**`src/utils/validators.py`** — Input validation utilities for API requests

| Class | Signature |
|---|---|
| `ValidationError` | `class ValidationError(Exception)` |

| Function | Signature |
|---|---|
| `validate_model_id` | `def validate_model_id(model_id: str) -> str` |
| `validate_chat_messages` | `def validate_chat_messages(messages: list[dict[str, Any]]) -> list[ChatMessage]` |
| `validate_temperature` | `def validate_temperature(temperature: float \| None) -> float \| None` |
| `validate_max_tokens` | `def validate_max_tokens(max_tokens: int \| None) -> int \| None` |
| `validate_top_p` | `def validate_top_p(top_p: float \| None) -> float \| None` |
| `validate_stop_sequences` | `def validate_stop_sequences( stop: str \| list[str] \| None, ) -> list[str] \| None` |
| `validate_chat_completion_request` | `def validate_chat_completion_request( request_data: dict[str, Any], query_model: str \| None = None ) -> ChatCompletionRequest` |
| `sanitize_user_input` | `def sanitize_user_input(text: str, max_length: int = 10000) -> str` |
| `validate_api_key` | `def validate_api_key(api_key: str \| None) -> str \| None` |
| `validate_content_type` | `def validate_content_type(content_type: str) -> bool` |

### `tools`

**`tools/__init__.py`** — Universal LLM Gateway - Monitoring Tools

| Function | Signature |
|---|---|
| `__getattr__` | `def __getattr__(name: str) -> object` |

<!-- GENERATED:END -->
---

## Coverage Notes

### unsupported_claims

None added in this refresh. Statements above stay within declared docstrings for the listed paths.

### missing_coverage

None for tree accounting: §11 lists all 80 packages with per-module entrypoint signatures (405 modules).

### human_markers

Not re-scanned across the full tree in this pass; prior CDP inventory reported none in the excerpt set.

### loaded_skills

None (targeted doc refresh from source docstrings).
