# Pipeline System

<!-- AUTHORED:START -->
First-run seed for `doc-generate` (thread 4750). Inventory basename maps
`systems/pipeline/` → `docs/architecture/pipeline.md`. Related authored
surface (not replaced by this file): `docs/architecture/design/async-pipeline-dispatch.md`.
<!-- AUTHORED:END -->

<!-- GENERATED:START inventory_sha=61793257e8f1 generated=2026-07-09 -->
_Generated from docstrings, signatures, and imports; claims reflect what the source **declares**, not verified runtime behavior. doc-generate verifies doc<->docstring consistency, not docstring<->behavior truth._

## Scope

The `systems/pipeline` subsystem provides multi-model workflow orchestration. It is fully domain-agnostic: core infrastructure (schemas, handlers, DAG, prompts) lives under `core/`, and all domain handlers are loaded from a user handlers directory or via entry points. The subsystem exposes `PipelineExecutor` and `PipelineRegistry` as its primary public surface.

Typical usage (from module docstring):
```python
from systems.pipeline import PipelineExecutor, PipelineRegistry

registry = PipelineRegistry(search_paths=["config"])
registry.load()

executor = PipelineExecutor(registry, request_executor, proxy)
response = await executor.execute(context)
```

---

## Module Inventory

| Module path | Purpose (from docstring) |
|---|---|
| `__init__.py` | Pipeline module — multi-model workflow orchestration. Re-exports public surface. |
| `availability.py` | Pipeline model availability checks. |
| `executor.py` | Pipeline executor - exports from core. |
| `hot_reload.py` | Pipeline configuration hot-reload via `HotReloadWatcher`. |
| `loader.py` | Load pipeline configuration from YAML files; recursive sub-pipeline loading. |
| `pipeline_failure_debug.py` | Write step failure tracebacks and response details to a debug file. |
| `plugins.py` | Plugin API for external domain handlers via entry points or direct registration. |
| `response_builder.py` | Response builder for pipeline results. |
| `schemas.py` | Pipeline schemas - exports from core. |
| `user_handlers.py` | User handlers directory loading with variant-scoped routing. |
| `user_prompts.py` | User prompts directory loading. |
| `verification_report.py` | Verification report builder for `consensus_verify_chain_v4` pipeline steps. |
| `execution_summary_inputs.py` | Input formatting for pipeline execution summaries. |
| **core/** | |
| `core/__init__.py` | Domain-agnostic pipeline core. Re-exports all core public symbols. |
| `core/conditions.py` | Safe condition evaluation for pipeline steps (sandboxed, whitelist builtins). |
| `core/constants.py` | Shared sentinel constants for RAG pipeline components. |
| `core/dag.py` | DAG builder: dependency graph, sub-pipeline expansion, cycle detection, topological sort. |
| `core/domain_router.py` | Domain routing: resolves `(pipeline_type, variant, step_type)` to handler. |
| `core/estimation.py` | Shared token-budget estimation helpers. |
| `core/fragments.py` | Pipeline fragment loading and composition (DRY step sequences). |
| `core/migration.py` | Schema migration for pipeline YAML files (v4→v5→v6). |
| `core/pipeline_config.py` | Pipeline-level schema definitions (`PipelineSpec`, `PipelineOptions`, etc.). |
| `core/prompts.py` | Generic prompt template rendering via regex substitution. |
| `core/schemas.py` | Compatibility exports for pipeline core schemas. |
| `core/step_config/` | `StepConfig` package: schema, parsing validators, model resolution, map config builder. |
| `core/step_types.py` | Shared step-related data types. |
| `core/validation.py` | Parse-time validation for pipeline configuration. |
| **core/events/** | |
| `core/events/__init__.py` | Pipeline execution events. Two event systems coexist: old bus events and new observability dataclass events. |
| `core/events/assess_loop.py` | Assess loop lifecycle events. |
| `core/events/base.py` | Base pipeline event contract (`PipelineEvent`). |
| `core/events/checkpoint.py` | Checkpoint operation events. |
| `core/events/compaction.py` | Thread-persistence compaction event factories. |
| `core/events/delivery.py` | Async-dispatch result-delivery lifecycle events. |
| `core/events/delivery_audit.py` | B3 delivery-audit registry lifecycle event factories. |
| `core/events/dispatch/` | Package re-exports for async-dispatch tracker and frontier-dispatch event factories (27 symbols). |
| `core/events/guidance_locality.py` | Token-locality guidance-delivery lifecycle event factories. |
| `core/events/inference.py` | Model invocation events (`ModelInvocation`). |
| `core/events/lifecycle.py` | Pipeline and step lifecycle events + data capture events. |
| `core/events/map.py` | Map step progress events. |
| `core/events/pipeline.py` | Pipeline lifecycle events. |
| `core/events/recorder.py` | Event recorder: persists pipeline events to per-execution JSONL files. |
| `core/events/refusal.py` | Frontier refusal anomaly events. |
| `core/events/step/` | Package re-exports for step, fallback, consensus, and RAG bus event factories (29 symbols). |
| `core/events/verification.py` | Verification decision events. |
| **core/execution/** | |
| `core/execution/__init__.py` | Pipeline execution components. Re-exports DAGExecutor, errors, map/reduce, proxy client, resolver, retry, timeout. |
| `core/execution/async_tracker/` | In-process record store for async-dispatched pipelines. Modularized from single module. |
| `core/execution/async_tracker_delivery/` | Agent-bus result delivery for async-dispatched pipeline executions (legacy + bus-mode paths). |
| `core/execution/checkpoint/` | Checkpoint persistence for pipeline step outputs (async, atomic writes). |
| `core/execution/chunked/` | Chunked model execution infrastructure with configurable chunking and model distribution. |
| `core/execution/cloud_resolver.py` | Cloud-aware model resolution via cloud subsystem `/api/select`. |
| `core/execution/concurrency.py` | Pipeline-level concurrency gate surface (`maybe_concurrency_gate`). |
| `core/execution/concurrency_backend.py` | Concurrency backend abstraction for per-key serialisation (`InProcessConcurrencyBackend`). |
| `core/execution/critical_path.py` | Critical path calculation for pipeline DAGs (static analysis, O(V+E)). |
| `core/execution/dag_executor/` | DAG executor package. Modularized: executor shell, lifecycle, scheduling, completions, step runner, map step. |
| `core/execution/disconnect_monitor.py` | Client disconnection monitoring for pipeline execution. |
| `core/execution/dispatch_journal.py` | Persistent SQLite journal for terminal async-dispatch tracker records. |
| `core/execution/errors/` | Pipeline error hierarchy with structured serialization. |
| `core/execution/executor.py` | Backward-compatible `DAGExecutor` import path. |
| `core/execution/fallback_eligibility.py` | Classify whether a failed model invocation should trigger ranked fallback. |
| `core/execution/map_reduce/` | Map/Reduce execution for dynamic parallelism (`MapExecutor`, `MapOutputCollection`). |
| `core/execution/model_tracker.py` | Model usage tracking for DAG execution (prevents concurrent same-model use). |
| `core/execution/outcome.py` | Terminal pipeline execution outcome carrier. |
| `core/execution/protocols.py` | Protocols for execution domain type safety. |
| `core/execution/proxy_client/` | Stargate proxy client package (`ProxyClient`, `ProxyClientConfig`, `ProxyClientError`). |
| `core/execution/request_inference_boundary.py` | Request-scoped inference boundary subscriptions for pipeline execution. |
| `core/execution/requirements_resolver.py` | Resolve `model_requirements` to concrete model IDs via `POST /v1/models/select`. |
| `core/execution/resolved_candidates.py` | Per-execution cache for ranked model candidates. |
| `core/execution/resolver.py` | Namespace resolution with pluggable handlers (`NamespaceResolver`, `traverse_path`). |
| `core/execution/retry.py` | Retry policies with backoff strategies. |
| `core/execution/step_wrapper.py` | Step execution wrappers integrating retry, timeout, and checkpoint. |
| `core/execution/timeout.py` | Timeout wrapper for async execution. |
| **core/executor/** | |
| `core/executor/__init__.py` | Package-shadow split of former `core/executor.py`. Exports `PipelineExecutor`, `_normalize_pipeline_exception`, `PreparedPipelineExecution`. |
| `core/executor/exception_mapping.py` | Map pipeline exceptions to `(code, message, data)` tuples. |
| `core/executor/execution_loop.py` | DAG execution loop with lifecycle event emission. |
| `core/executor/input_extraction.py` | Request-input extraction helpers for the pipeline executor. |
| `core/executor/outcome_assembly.py` | Assemble a `PipelineExecutionOutcome` from a completed DAG run. |
| `core/executor/output_resolution.py` | Terminal output resolution from `context.outputs`. |
| `core/executor/pipeline_executor.py` | `PipelineExecutor` facade — sync + async entrypoints + instance helpers. |
| `core/executor/preparation.py` | Pipeline preparation — resolve spec, build DAG, emit start events. |
| `core/executor/prepared.py` | Shared prepared-state, protocols, and named loggers for the executor package. |
| **core/handlers/** | |
| `core/handlers/__init__.py` | Step handlers package. Provides plugin-based step type execution. |
| `core/handlers/archive_assistant_turn.py` | `archive_assistant_turn_v1` — persist the assistant turn. |
| `core/handlers/archive_user_turn.py` | `archive_user_turn_v1` — persist the user turn (artifact + assertion). |
| `core/handlers/assemble_thread.py` | `assemble_thread_v1` — resolve chat anchor and build message prefix. |
| `core/handlers/assess_loop/` | Engine-mediated iterative loop handler package (`assess_loop_v1`). |
| `core/handlers/assess_loop_config.py` | Configuration and loop utilities for `assess_loop_v1` handler. |
| `core/handlers/builtin/` | Base handler with common utilities for model invocation. |
| `core/handlers/data_sink.py` | Built-in `data_sink_v1`: persist pipeline outputs to RAG metadata (SQLite). |
| `core/handlers/data_source/` | Built-in `data_source_v1`: load pipeline inputs from SQLite, RAG, or models. |
| `core/handlers/frontier_dispatch/` | Built-in `frontier_dispatch_v1` step handler — native-endpoint frontier dispatch. |
| `core/handlers/generate/` | Generic generate handler package. |
| `core/handlers/handler_contract.py` | Abstract handler base and `StepHandler` protocol. |
| `core/handlers/model_fallback.py` | Model fallback resolution for generate steps. |
| `core/handlers/parallel.py` | Parallel model call utilities for pipeline handlers. |
| `core/handlers/parse_json.py` | Built-in `parse_json_v1` step: deterministic JSON-string parse. |
| `core/handlers/pipeline_call.py` | `pipeline_call_v1` step handler — calls another pipeline as a service. |
| `core/handlers/pipeline_context.py` | Pipeline execution context passed through step handlers. |
| `core/handlers/protocol.py` | Step handler protocol and execution context. |
| `core/handlers/rag_search.py` | Built-in `rag_search_v1`: semantic search against the RAG service. |
| `core/handlers/registry.py` | Step handler registry integrated with domain routing. |
| `core/handlers/select_output.py` | Select-output handler: picks first non-skipped result from candidate steps. |
| `core/handlers/step_output.py` | Step output types for pipeline handler execution. |
| `core/handlers/summarize_thread_v1.py` | `summarize_thread_v1` — collapse older turns into a cortex consolidation summary. |
| `core/handlers/thread_persistence/` | Thread anchor, window, and turn-artifact helpers for cortex-chat-openai compactor. |
| `core/handlers/token_resolution.py` | Token budget resolution for pipeline handlers. |
| **execution_summary/** | |
| `execution_summary/__init__.py` | Pipeline execution summary writer. Supports JSON, YAML, Markdown, per-step directory. |
| `execution_summary/factory.py` | Module-level factory for `ExecutionSummaryWriter`. |
| `execution_summary/markdown/` | Internal markdown-rendering layer. |
| `execution_summary/retention.py` | Filesystem retention for pipeline execution summaries. |
| `execution_summary/summary_dict.py` | Structured summary-dict builders for pipeline execution summaries. |
| `execution_summary/writer.py` | Pipeline execution summary writer — public façade. |
| **registry/** | |
| `registry/__init__.py` | Pipeline registry - loads and manages pipeline configurations. |
| `registry/access.py` | Pipeline access logic for the registry subsystem. |
| `registry/core.py` | Core `PipelineRegistry` orchestration. |
| `registry/loader.py` | Pipeline loading logic for the registry subsystem. |
| `registry/validator.py` | Pipeline validation logic for the registry subsystem. |

---

## Key Classes

### `PipelineRegistry` (`registry/core.py`)

Registry for pipeline configurations. Loads pipelines, models, and prompts from YAML configuration. Validates all configurations at load time (fail-fast). Pipeline `p` is loaded if and only if each required model passes `is_model_available`; passing `is_model_available=None` disables filtering (all pipelines load).

- `load()` — Load all configurations from search paths. Each search path is an isolated model namespace. Later paths override earlier for the same pipeline ID.
- `reload_pipelines()` — Reload pipelines with current model availability. Atomically swaps dict references so concurrent readers see either the complete old or complete new state.
- `get_pipeline(pipeline_id)` — Get pipeline by ID.
- `get_model_config(model_ref, *, domain, search_path)` — Get model configuration with search-path-scoped resolution.
- `get_prompt(prompt_ref)` — Get structured prompt configuration by reference.
- `is_pipeline(model_id)` — Check if model_id refers to a pipeline.

### `PipelineExecutor` (`core/executor/pipeline_executor.py`)

Execute pipeline workflows using DAG-based scheduling. Steps execute as soon as their dependencies are satisfied. Independent steps automatically run in parallel. Only `DAGExecutor` writes to `context.outputs`.

- `execute(context)` — Execute a pipeline using DAG-based scheduling (sync HTTP path). Returns an OpenAI-compatible chat completion `Response`.
- `execute_async(context, *, execution_id, started_at, tracker)` — Run the prepared DAG in the background and record terminal state.
- `prepare_execution(context, *, execution_id)` — Resolve spec, build DAG, emit start events.
- `generate_execution_id()` — Mint a fresh execution_id prior to DAG preparation or run.

Invariants:
- `generate_execution_id()` is called exactly once per dispatch; the minted id is threaded through `prepare_execution` so sync + async paths share identity with the DAG.
- Sync `execute()` MUST return a `Response` carrying the `X-Pipeline-Execution-Id` header (enforced by `ResponseBuilder`).
- Terminal-passthrough streaming: when the terminal step's `StepOutput` carries a non-None `stream`, `execute()` returns a header-only placeholder Response and the proxy lifecycle replaces it with a `StreamingResponse`; `ResponseBuilder` is intentionally not invoked on this branch.

### `DAGExecutor` (`core/execution/dag_executor/executor/dag_executor.py`)

Execute pipeline DAG with automatic parallelization. Steps execute as soon as their dependencies are satisfied. Independent steps run in parallel using asyncio.

Invariants:
- Only this executor writes to `context.outputs`.
- ∀ step: dependencies complete before step starts.
- Parallel steps share no mutable state.
- First failure cancels remaining (fail-fast).
- `SKIPPED` counts as "dependency satisfied"; skip propagation is not automatic.

### `DomainRouter` (`core/domain_router.py`)

Route step execution to variant-scoped or shared domain handlers.

Resolution order:
1. `(domain, variant, step_type)` — variant-specific
2. `(domain, "", step_type)` — shared domain handler
3. `step_type` — generic handler
4. `KeyError` — fail-fast

All domain handlers are external; no built-in domains remain.

### `DAGBuilder` (`core/dag.py`)

Build execution DAG from pipeline steps. Validates no dangling dependency references, no cycles, all inputs are valid step references. Supports sub-pipeline expansion (`type: sub_pipeline` → namespaced flat steps). No implicit dependencies.

### `HandlerRegistry` (`core/handlers/registry.py`)

Registry of step handler classes (not instances). Registers handler classes and instantiates per-execution. Delegates to `DomainRouter` for domain-aware resolution.

### `PipelineExecutionTracker` (`core/execution/async_tracker/tracker.py`)

In-process async-dispatch record store for pipelines dispatched via `POST /api/v1/pipelines/dispatch`. The tracker is the sole writer of dispatch-lifecycle signals.

Invariants:
- ∀ `register_execution` success: emit `pipeline.dispatch.async` once.
- ∀ terminal transition: emit `pipeline.dispatch.completed` exactly once (idempotent guard). Exception: `op="to_thread"` records may be demoted `completed`→`failed` after a failed on-behalf POST, emitting a second `pipeline.dispatch.completed` with `status="failed"`; consumers key off the latest `status`.
- ∀ admission refusal: emit `pipeline.dispatch.rejected` before raising.
- TTL pruning uses `completed_at_monotonic` — running records are never evicted by age alone.
- Records are node-local and non-durable across Stargate restart; an optional journal hook persists terminal records to a SQLite cold path (`dispatch_journal.py`) so `GET /api/v1/pipelines/executions/{id}` survives restarts.

### `MapExecutor` (`core/execution/map_reduce/map_executor/executor/map_executor.py`)

Executes map steps with fan-out parallelism. Resolves `map_over` to list/dict, creates per-iteration `MapState`, executes handler with iteration context, returns `MapOutputCollection`. Supports partial success when `timeout_seconds` or `min_success_threshold` is configured.

### `ProxyClient` (`core/execution/proxy_client/client.py`)

HTTP client for pipeline → Stargate internal communication. Submits requests through Stargate's full pipeline (transformations, profiles, token management, request queue, routing). Transport selection (UDS vs TCP) is fully delegated to `transport_utils.make_async_client()`.

### `NamespaceResolver` (`core/execution/resolver.py`)

Centralized namespace resolution. Invariant: ∀ binding, `resolve(binding)` returns root object for `traverse_path()`.

Built-in namespaces: `sourceNs`, `optionsNs`, `{step_name}`, `mapNs` (registered only during map step execution via `with_map_context()`). Reserved namespaces `{sourceNs, optionsNs, loopNs, mapNs}` cannot be overridden; custom namespaces registered via `register_namespace()`.

### `EventRecorder` (`core/events/recorder.py`)

Write pipeline events to a per-execution JSONL file. Thread-safe via a lock on the sequence counter and file writes. Auto-populates identity fields (`pipeline_id`, `execution_id`), timestamps (monotonic + wall clock), and sequence numbers before writing.

Invariant: ∀ event ∈ events.jsonl: `event.sequence` is monotonically increasing.

### `CheckpointManager` (`core/execution/checkpoint/manager.py`)

Manages checkpoint storage and event emission. Key generation: `{pipeline_id}:{step_name}:{execution_id}`. Optional input fingerprint for cross-execution reuse.

### `BaseHandler` (`core/handlers/builtin/base.py`)

Base class with common handler utilities. Orchestrates helpers from sibling modules (model_resolution, prompt_rendering, token_management, generation_params). Subclasses inherit the full method API.

### `AbstractStepHandler` (`core/handlers/handler_contract.py`)

Abstract base class for pipeline step handlers. Defines the complete contract: `step_type` class attribute, async `execute()` returning `StepOutput`, optional `validate()` and `get_required_placeholders()`.

Invariants:
- ∀ `execute()`: returns `StepOutput` ∧ ¬writes to `context.outputs`.
- Handlers are stateless (instantiated fresh per-execution).
- All I/O operations must be async.

### `StepConfig` (`core/step_config/config.py`)

Step configuration for pipeline execution (Pydantic model). Invariant: ∀ binding ∈ `handler_inputs.values()`, binding resolved before `execute()`.

### `PipelineSpec` (`core/pipeline_config.py`)

Generic pipeline specification. The `type` field determines which domain handles execution.

### `FragmentLoader` (`core/fragments.py`)

Load and manage pipeline fragments. Supports type-preserving substitution and V6 binding support with `as_prefix`.

### `ConditionEvaluator` (`core/conditions.py`)

Safe condition evaluator for pipeline steps. Sandboxed evaluation context with empty `__builtins__`, whitelist of safe functions, and `StepOutputProxy` for safe attribute access.

Invariant: ∀ condition: `eval_sandboxed(condition)` ∧ ¬arbitrary_code_execution.

### `InProcessConcurrencyBackend` (`core/execution/concurrency_backend.py`)

In-process per-key FIFO serialisation backed by `FifoCapacityGate`. Each gate is constructed at `limit=1`. Gate is evicted from the store after release when no holder and no queued waiters remain.

### `ExecutionSummaryWriter` (`execution_summary/writer.py`)

Writes pipeline execution summaries to disk. Supports JSON, YAML, single-file markdown, and per-step execution directory formats. All writes end with a retention sweep for that pipeline.

### `PipelineHotReload` (`hot_reload.py`)

Manages hot-reload for pipeline configurations. Watches all search paths from `PipelineRegistry` and triggers `reload_pipelines()` when YAML files change.

---

## Key Functions

### Execution

- `execute_step_with_wrappers(step, handler_fn, checkpoint_manager)` (`core/execution/step_wrapper.py`) — Execute step with retry, timeout, and checkpoint wrappers. Composition order (outer to inner): step timeout → retry with backoff → handler timeout → handler execution.
- `execute_with_retry(fn, policy, step_name)` (`core/execution/retry.py`) — Execute async function with retry policy.
- `execute_with_step_timeout(fn, timeout_seconds, step_name)` (`core/execution/timeout.py`) — Execute async function with total step timeout.
- `execute_with_handler_timeout(fn, timeout_seconds, step_name, attempt)` (`core/execution/timeout.py`) — Execute async function with handler-level timeout.
- `execute_with_disconnect_monitoring(dag_executor, http_request, ...)` (`core/execution/disconnect_monitor.py`) — Execute DAG with client disconnection monitoring.
- `run_prepared_execution(executor, prepared, *, monitor_disconnect)` (`core/executor/execution_loop.py`) — Acquire per-chat concurrency gate (if declared) and run the DAG.
- `run_prepared_execution_inner(executor, prepared, *, monitor_disconnect)` (`core/executor/execution_loop.py`) — Execute the prepared DAG and return structured outcome.
- `do_prepare_execution(executor, context, *, execution_id)` (`core/executor/preparation.py`) — Resolve pipeline spec, build DAG context/nodes, extract input text.
- `assemble_outcome(prepared, duration)` (`core/executor/outcome_assembly.py`) — Build the outcome carrier from the just-finished DAG.

### DAG

- `_expand_all_sub_pipelines(steps)` (`core/dag.py`) — Replace `sub_pipeline` steps with namespaced flat steps.
- `calculate_critical_path(nodes, step_durations)` (`core/execution/critical_path.py`) — Calculate critical path step IDs using CPM. O(V+E).
- `calculate_step_depths(nodes)` (`core/execution/critical_path.py`) — Calculate depth of each step in the DAG. O(V+E).

### Model Resolution

- `async_resolve_model_requirements(requirements_dict, estimated_source_tokens)` (`core/execution/requirements_resolver.py`) — Resolve a `model_requirements` dict to a ranked list of model IDs (non-blocking). Degrades gracefully: returns an empty list on timeout (45s default), HTTP, or network error rather than raising.
- `get_ranked_candidates(*, context, step_name, requirements, estimated_source_tokens)` (`core/execution/resolved_candidates.py`) — Resolve and cache ranked model IDs for one step within a pipeline execution.
- `resolve_cloud_ref_async(model_ref, *, cloud_select_fn)` (`core/execution/cloud_resolver.py`) — Resolve a `cloud:` prefixed model_ref to a concrete model ID.
- `try_step_model_fallback(step, primary_err, *, primary_model_id, ...)` (`core/execution/dag_executor/step_model_fallback.py`) — Try fallback models after the primary model's retry chain fails.
- `classify_fallback_error(exc, *, suppression_reason)` (`core/execution/fallback_eligibility.py`) — Return whether another model could plausibly recover from this failure.
- `get_fallback_suppression_reason(*, primary_resolution, model_requirements)` (`core/execution/fallback_eligibility.py`) — Return why fallback must be suppressed for the resolved primary model.

### Namespace Resolution

- `traverse_path(root, field_path, ...)` (`core/execution/resolver.py`) — Navigate dot-separated path with wildcard and dynamic key support.
- `resolve_model_alias(model_key, context, *, domain)` (`core/execution/resolver.py`) — Resolve model alias to full model ID via pipeline registry.

### Async Tracker

- `register_execution(tracker, *, execution_id, pipeline, started_at, ...)` (`core/execution/async_tracker/lifecycle.py`) — Admit a new execution and emit `pipeline.dispatch.async`.
- `complete_execution(tracker, execution_id, *, content, model, ...)` (`core/execution/async_tracker/lifecycle.py`) — Record success terminal state (idempotent).
- `fail_execution(tracker, execution_id, *, code, message, data)` (`core/execution/async_tracker/lifecycle.py`) — Record failure terminal state (idempotent).
- `deliver_result(record, *, event_bus, auth_token, url)` (`core/execution/async_tracker_delivery/deliver.py`) — Route delivery based on `op`: legacy envelope-post or bus-mode on-behalf post.

### Conditions

- `evaluate_condition(condition, outputs, options)` (`core/conditions.py`) — Convenience function to evaluate a condition in sandboxed context.
- `extract_condition_deps(condition)` (`core/conditions.py`) — Extract referenced step names from a condition expression via AST parsing.

### Availability

- `get_pipeline_required_models(pipeline, *, resolve_model_ref)` (`availability.py`) — Extract model IDs required by pipeline steps.
- `are_models_available(required_models, *, is_available)` (`availability.py`) — Return True iff every required model ID passes `is_available`.

### Plugins

- `register_domain_handler(domain, step_type, handler_class)` (`plugins.py`) — Register an external domain handler.
- `register_domain(domain, handlers)` (`plugins.py`) — Register multiple handlers for a domain at once (mapping of step_type → handler_class).
- `discover_plugins()` (`plugins.py`) — Discover and load plugins from entry points.
- `load_user_handlers(config_base_dir)` (`user_handlers.py`) — Load handlers from `{domain}/handlers/` and `{domain}/{variant}/handlers/`.
- `register_handler(handler_class)` (`core/handlers/registry.py`) — Decorator to register a generic step handler class.

### Exception Mapping

- `_normalize_pipeline_exception(exc)` (`core/executor/exception_mapping.py`) — Map known pipeline exceptions to `(code, message, data)` tuples.

### Output Resolution

- `get_final_result(pipeline, context, output_aliases)` (`core/executor/output_resolution.py`) — Get final result from pipeline output step.
- `extract_output_hints(pipeline, context, output_aliases)` (`core/executor/output_resolution.py`) — Extract structured anomaly hints from the terminal output step's JSON.
- `extract_backtranslation_data(steps, context)` (`core/executor/output_resolution.py`) — Extract backtranslation data if present.

### Frontier Dispatch

- `run_admission_gate(handler, step, context)` (`core/handlers/frontier_dispatch/admission_gate.py`) — Execute the ordered admission sequence and resolve the dispatch tool set.
- `build_frontier_request(handler, step, context, admission)` (`core/handlers/frontier_dispatch/gen_params.py`) — Assemble generation parameters and the `FrontierRequest` for the loop.
- `run_dispatch_loop(handler, step, context, admission, req)` (`core/handlers/frontier_dispatch/native_loop.py`) — Run the bounded native tool loop and emit Started + terminal events.
- `emit_post_loop_observability(*, context, publish, agent, boot_level, model, result)` (`core/handlers/frontier_dispatch/observability.py`) — Fire the Phase-1 hoisted observability signals; return anomaly hints.

### Thread Persistence

- `resolve_or_create_anchor(thread_kind, thread_key)` (`core/handlers/thread_persistence/anchor.py`) — Return `(entity_id, current_turn_index)`.
- `build_referential_window(anchor_id, *, k)` (`core/handlers/thread_persistence/window.py`) — Return the assembled message prefix for the referential window.
- `write_turn_artifact(*, chat_id, turn_index, payload)` (`core/handlers/thread_persistence/artifact.py`) — Write per-turn JSON artifacts to the workspace `.runtime/` tree.
- `cx_async(tool, arguments)` (`core/handlers/thread_persistence/events.py`) — Relay asynchronously to cortex-api via UDS, normalising error shape.

---

## Imports and Dependencies

### Internal dependencies (intra-subsystem)

- `core/executor/` imports from `core/dag`, `core/events/`, `core/execution/`, `core/handlers/`, `core/schemas`, `core/fragments`, `core/prompts`, `registry/`
- `core/execution/dag_executor/` imports from `core/dag`, `core/events/step`, `core/events/lifecycle`, `core/execution/proxy_client`, `core/execution/model_tracker`, `core/execution/fallback_eligibility`, `core/execution/map_reduce`, `core/step_config/model_resolution`
- `core/execution/async_tracker/` imports from `core/events/dispatch`
- `core/execution/async_tracker_delivery/` imports from `core/execution/async_tracker/records`
- `core/handlers/builtin/` imports from `core/prompts`, `core/handlers/protocol`, `core/execution/resolver`
- `core/handlers/frontier_dispatch/` imports from `core/events/dispatch`, `core/execution/errors`, `core/handlers/builtin`, `core/handlers/registry`
- `core/handlers/generate/` imports from `core/execution/fallback_eligibility`, `core/step_config`, `core/handlers/builtin`
- `core/handlers/thread_persistence/` imports from `core/events/compaction`
- `registry/` imports from `availability`, `core/schemas`, `core/dag`, `core/handlers`, `loader`
- `execution_summary/` imports from `core/execution/map_reduce`, `core/handlers/protocol`, `verification_report`, `execution_summary_inputs`

### External dependencies

- `universal_logging` — `get_logger`
- `universal_event_bus` — `Event`, `event_factory`
- `universal_hot_reload` — `HotReloadWatcher`, `read_text_preserving_timestamps`
- `universal_concurrency` — `FifoCapacityGate`
- `transport_utils` — `make_async_client`, `DEFAULT_STARGATE_URL`, `DEFAULT_AGENT_BUS_URL`, `DEFAULT_CORTEX_URL`, `resolve_rag_base_url`
- `model_id` — `ModelId`, `canonical_model_entity_id`, `validate_model_id`
- `model_capabilities` — `mcp_client_tool_loop`, `mcp_remote_connector`, `server_side_tools`
- `agent_seat` — `normalize_agent_slug`, `native_loop`, `profiles`, `registry`
- `llm_adapters` — `FrontierRequest`, `effective_provider_for_model`, `capability_dispatch`
- `frontier_observability` — `TerminationShadowDetector`, `detect_output_short`
- `pipeline_assess_registry` — `PROGRAMMATIC_ASSESS_HANDLERS`
- `implement_admission` — `scheme_resolve`, `share_uri_emit`
- `pydantic` — `BaseModel`, `ConfigDict`, `Field`, `field_validator`, `model_validator`
- `fastapi` — `Response`
- `httpx` — async HTTP client
- `yaml` — YAML parsing
- `sqlite3` — dispatch journal persistence
- `sse` — SSE parsing for streaming proxy client
- `src.core.gateway_tracker` — `gateway_tracker` (model coordination; host-Stargate internal import — boundary inversion, not a shared lib)
- `src.scheduling.events` — model gate events (lazy imports in observability, kept lazy to break an import cycle; host-Stargate internal import — boundary inversion, not a shared lib)

---

## Open Human Synthesis Markers

<!-- HUMAN: describe the full lifecycle of a synchronous pipeline execution from HTTP request ingress through DAGExecutor completion and response assembly -->
<!-- HUMAN: describe the two event system coexistence strategy (old bus @event_factory vs new dataclass JSONL) and the migration plan -->
<!-- HUMAN: describe the cortex-chat-openai compaction loop phases (A through D) and how assemble_thread_v1, archive_user_turn_v1, archive_assistant_turn_v1, and summarize_thread_v1 compose -->
<!-- HUMAN: describe the frontier_dispatch_v1 admission gate ordering invariant and why reject_unknown_runtime_options must run first -->

<!-- GENERATED:END -->
