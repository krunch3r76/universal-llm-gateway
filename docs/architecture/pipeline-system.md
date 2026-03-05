# Pipeline System

DAG-based pipeline orchestration. Pipelines are defined in YAML, loaded
by the registry, and executed by the DAG executor with parallel step scheduling.

**Source**: `services/universal-stargate/systems/pipeline/`

## YAML → Execution Flow

1. **Loading** (`PipelineRegistry.load()`):
   - Walks `search_paths` (`pipelines/`, `pipelines.local/`, `~/.pipelines/`)
   - Per domain: loads `models.yaml`, `prompts.yaml`, pipeline `*.yaml` specs
   - Resolves `pipeline_ref` references (sub-pipeline fragments)
   - Filters pipelines by model availability (checks gateway catalogs)
   - Validates handlers, model_refs, prompt_refs, DAG structure

2. **Registration**:
   - Pipelines stored in `PipelineRegistry.pipelines`
   - Models in `_root_models` / `_domain_models`
   - Prompts in nested `self.prompts` (e.g., `consensus.v7.analyze_question`)

3. **Dispatch** (in `process_chat_completion`):
   - `registry.is_pipeline(model_id)` → true if model ID matches a pipeline ID
   - Creates `PipelineContext` → calls `PipelineExecutor.execute()`

4. **Execution** (`DAGExecutor`):
   - Expands fragments (`use: fragment_name`)
   - Builds DAG with `DAGBuilder`
   - Steps run when dependencies satisfied; independent steps run in parallel
   - Each step: `HandlerRegistry.create_handler()` → `handler.execute()`
   - Only DAGExecutor writes to `context.outputs`; handlers return `StepOutput`

## BaseHandler

All pipeline step handlers extend `BaseHandler`.

**Source**: `services/universal-stargate/systems/pipeline/core/handlers/builtin.py`

| Method | Purpose |
|---|---|
| `_render_prompt()` | Load prompt from registry, render via `PromptBuilder` |
| `_call_model()` | Call model via `ProxyClient`, return `ModelCallResult` |
| `_resolve_model_alias()` | Resolve alias via registry (domain → root → passthrough) |
| `_resolve_model_pool()` | Resolve `model_pool` domain field |
| `_resolve_input()` | Resolve handler input bindings |
| `_report_progress()` | Emit `StepProgress` for long steps |

### _call_model Flow

1. Resolve model alias (unless `model_id_is_resolved=True`)
2. Build messages (system + user prompts)
3. Build generation params via `_build_generation_params()`
4. `ProxyClient.chat_completion()` → HTTP to Stargate `/v1/chat/completions`
5. Handle truncation (`finish_reason == "length"` → `ResponseTruncatedError`)
6. Emit `ModelInvocation` to recorder
7. Return `ModelCallResult` (content, tokens, request body, prompts)

### ProxyClient

Internal HTTP client for pipeline → Stargate calls.
- TCP or Unix socket (env: `STARGATE_HOST/PORT` or `STARGATE_UNIX_SOCKET`)
- Headers: `X-Pipeline-Internal`, `X-Pipeline-Execution-Id`, `X-Pipeline-Step-Id`
- Each call gets unique `X-Internal-Request-ID` for capacity tracking

## Pipeline-as-Service

Any pipeline can be called as a virtual model ID via `_call_model()`.
Stargate detects the ID and routes to `PipelineExecutor` transparently.

**Current service pipelines**:
| ID | Purpose | Source |
|---|---|---|
| `rag-context` | Query rewrite → parallel RAG + RRF merge → context | `pipelines/rag/rag_context_v1/` |
| `rag-answer` | Calls `rag-context` → grounded answer via phi4 | `pipelines/answer_v1/` |
| `code-review` | Structured review → validation → deterministic merged findings | `pipelines/code_review/` |

### Pipeline Estimation Metadata

Pipelines can expose estimation policy through domain extras in YAML:

```yaml
estimation:
  budget_source_tokens: 12000
  chars_per_token: 3.5
  validate_amplification: 1.3
  fixed_overhead_tokens: 1300
  large_file_warning_tokens: 20000
```

`PipelineSpec` uses `extra="allow"`, so this metadata is loaded without schema changes
and consumed by `/v1/pipelines/estimate` for caller-agnostic batch planning.

## Step Configuration

`StepConfig` uses `extra="allow"`. Domain-specific YAML keys become
`model_extra`, accessed via `step.get_domain_field("key")`.

**Invariant**: ¬ add new first-class attributes to `StepConfig` for domain fields.
Adding one silently breaks all handlers reading the same key via `get_domain_field()`.

## Version Isolation

**Step types**: `consensus_{name}_v{V}` suffix per version (last-write-wins registry).
**Prompt refs**: `{domain}.{version}.*` namespace (validated at load time).
**Sub-pipeline fragments**: Must NOT have `version` or `schema_version` fields.

## Event Recording

### Per-Execution JSONL (recorder)

| Event | Trigger |
|---|---|
| `PipelineStarted` / `PipelineCompleted` / `PipelineFailed` | Pipeline lifecycle |
| `StepStarted` / `StepCompleted` / `StepFailed` / `StepSkipped` | Step lifecycle |
| `StepInputsCaptured` / `StepOutputCaptured` | Step I/O |
| `ModelInvocation` | Each `_call_model()` |

Written to `{output_dir}/events.jsonl` per execution.

### Event Bus (pipeline.* signals)

Pipeline events also published to EventBus as `pipeline.*` signals.
Persisted to `/tmp/pipeline-events/current.jsonl` (filtered sink).

**Invariant**: Handlers MUST NOT emit pipeline events. All events are framework-level.
Exception: `assess_loop_v1` iteration events written to recorder only (intra-step).

## Key Files

| File | Purpose |
|---|---|
| `core/executor.py` | `PipelineExecutor` — entry point |
| `core/execution/executor.py` | `DAGExecutor` — step scheduling |
| `core/execution/proxy_client.py` | `ProxyClient` — internal HTTP calls |
| `core/handlers/builtin.py` | `BaseHandler` — shared handler logic |
| `core/estimation.py` | Shared token estimation and batch packing helpers |
| `core/handlers/protocol.py` | `PipelineContext` protocol |
| `core/schemas.py` | `StepConfig`, `FragmentRef`, `SubPipelineSpec` |
| `core/registry.py` | `PipelineRegistry` — loading, resolution |
| `core/events/recorder.py` | `EventRecorder` — per-execution JSONL |

## Pipeline File Structure

Each pipeline version directory:

| File | Contents |
|---|---|
| `chain-v{X}.yaml` | Top-level step sequence, model refs |
| `prompts.yaml` | Prompt templates (system + user) |
| `models.yaml` | Model alias → full model ID mapping |
| `handlers/__init__.py` | Handler registration |
| `handlers/*.py` | Step handler implementations |
| Sub-pipeline YAML | `verify.yaml`, `veto.yaml`, `synthesize.yaml` |
