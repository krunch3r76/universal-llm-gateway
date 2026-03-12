# Event Contracts

**Purpose**: Define the structure, relationships, and guarantees for all Stargate events.

## Event Schema

All events follow this structure:

```json
{
  "type": "stargate_event",
  "signal": "string",
  "payload": {},
  "timestamp": "ISO-8601",
  "id": "integer (monotonic)",
  "source": "universal_stargate"
}
```

## Correlation Fields

### Request Scoping

| Field | Type | Presence | Description |
|-------|------|----------|-------------|
| `request_id` | string (UUID) | Required for request events | Identifies single request |
| `correlation_id` | string (UUID) | Optional | Links federated request chain |

**INVARIANT**: ∀ request-scoped events: `request_id` ∈ payload

### Propagation

```
Client Request
  └─> Master (generates request_id, correlation_id)
      └─> Relay (preserves correlation_id, new request_id)
          └─> Edge (preserves correlation_id, new request_id)
```

### Gateway request_id propagation (X-Internal-Request-ID)

For request-scoped telemetry emitted by Gateways (e.g., `request.queued`,
`request.inference.started`), Gateways MUST prefer the upstream-provided
`X-Internal-Request-ID` header when present.

**Rationale**: Ensures a single `request_id` matches end-to-end across
Stargate → Gateway and across federation hops (Master ↔ Edge). Required
for pipeline map iteration correlation (Master-side `request_id_to_idx`).

**Fallback**: If `X-Internal-Request-ID` is absent (direct calls to Gateway),
Gateway generates a UUID `request_id`.

## Event Lifecycle Contracts

### Request Lifecycle

**INVARIANT**: `request.started` ⟹ (`request.completed` ∨ `request.failed` ∨ `request.timed.out` ∨ `request.client.disconnected`)

```
request.routed
  └─> request.queued (if queued)
      └─> request.processing
          └─> request.inference.started
          └─> request.completed | request.failed | request.timed.out
              └─> request.capacity.timeout (precedes request.failed when cause is capacity)
```

### Capacity & Slot Lifecycle

**Purpose**: Tracks physical slot allocation and release on gateways. Distinct from
the Request Lifecycle - a slot is leased *to* a request, but this lifecycle
governs hardware capacity, not user intent.

**INVARIANT**: ∀ acquired slot: exactly one of `model.execution.completed` ∨
`model.execution.failed` MUST be emitted to release the lease.
**INVARIANT**: `model.execution.completed` and `model.execution.failed` MUST
contain `request_id` and `gateway_id` for slot tracking.

```
[Slot Acquired implicitly via API/Queue]
      |
      v
[Execution / Inference]
      |
      └─> model.execution.completed | model.execution.failed
            └─> model.capacity.freed (wake-only signal; no slot release)
```

| Signal | Status | Payload | Description |
|---|---|---|---|
| `model.execution.started` | **Inactive** | `url`, `model_id` | Defined but not currently emitted. Reserved for future counter-based busy tracking. |
| `model.execution.completed` | **Active** | `url`, `model_id`, `request_id`, `gateway_id` | Terminal success. Triggers slot release. |
| `model.execution.failed` | **Active** | `url`, `model_id`, `request_id`, `gateway_id` | Terminal failure. Triggers slot release. |
| `model.capacity.freed` | **Active** | `url`, `model_id` | Wake-only signal (e.g., model unloaded). No slot release. |

**Consumers**:
- `CapacityWaiter`: Wakes queue processors on `completed` / `failed` / `freed`.
- `GatewayTracker`: Releases slot reservations on `completed` / `failed`.

### Non-sticky Overflow Lifecycle

**INVARIANT**: `routing.overflow.triggered` ⟹
(`model.load.overflow.started` ∨ `model.capacity.overflow.assigned`) ∨ `routing.overflow.failed`.

| Signal | Required Payload | Description |
|---|---|---|
| `routing.overflow.triggered` | `request_id`, `model_id`, `from_gateway`, `to_gateway`, `reason` | Spillover branch selected |
| `model.load.overflow.started` | `request_id`, `model_id`, `gateway_id`, `reason` | Overflow gateway cold-load initiated |
| `model.capacity.overflow.assigned` | `request_id`, `model_id`, `from_gateway`, `to_gateway`, `depth_before` | Admission moved to overflow gateway |
| `routing.overflow.failed` | `request_id`, `model_id`, `tried_gateways`, `reason` | No feasible spillover path |

### Model Lifecycle

**INVARIANT**: `model.load.initiated` ⟹ (`model.loaded` ∨ `model.loading.failed`)

```
model.load.initiated
  └─> model.loading.started
      └─> model.loaded | model.loading.failed
```

### Federation Monitoring Events

| Signal | Required Payload | Optional Payload |
|--------|-----------------|------------------|
| `federation.catalog.vram.drift` | `gateway_id`, `model_id`, `measured_mb`, `catalog_mb`, `drift_pct` | — |

**`federation.catalog.vram.drift`**: Emitted when `RESOURCE_UPDATE.model_vram` reveals that a loaded model's actual GPU VRAM (measured via nvidia-smi) diverges from the catalog estimate by >5%.

- Emitted per model per `RESOURCE_UPDATE` that carries `model_vram` and shows drift.
- `drift_pct` = `|measured_mb - catalog_mb| / catalog_mb * 100`.
- Threshold: 5% (hardcoded). Sustained drift indicates the catalog profile needs updating.
- Does **not** trigger automatic catalog correction; this is an observability signal only.

### Federation Lifecycle

**INVARIANT**: `federation.routing.delegated` ⟹ response_from_remote ∨ timeout

```
federation.routing.delegated
  └─> federation.load.requested (if model not loaded)
      └─> federation.load.confirmed | federation.load.failed
  └─> [response from remote]
```

### Federated Prompt Transformation Contract

**INVARIANT**: ∀ federated request with `input_schema == "prompt"` and
`transformation_engine` present:
`federated.request.prompt.transformation.applied` ∨
`federated.request.prompt.transformation.failed`

**INVARIANT**: ∀ federated request with `input_schema != "prompt"` OR
`¬transformation_engine`:
`federated.request.prompt.transformation.skipped`

| Field | Values |
|---|---|
| `reason` (skipped) | `"no_engine"` \| `"schema_not_prompt"` |
| `prompt_chars` (applied) | Character count of resulting `prompt` string (not token count) |
| `error` (failed) | Exception message from transformation engine |

### Capacity Timeout Contract (`CAPACITY_TIMEOUT`)

**INVARIANT**: `request.capacity.timeout` ⟹ `request.failed`

**INVARIANT**: if `request.failed.error` represents `CAPACITY_TIMEOUT`, then `request.capacity.timeout` is emitted first with the same `request_id`.

```
request.processing
  └─> [capacity retry loop]
      └─> request.capacity.timeout
          └─> request.failed
```

`request.capacity.timeout` is the canonical structured signal for capacity starvation (retry budget exhausted). Consumers should filter by signal instead of parsing `request.failed.error`.

### RAG Watcher Lifecycle

**INVARIANT**: `rag.watch.started` ⟹ `rag.watch.initial.complete` (same `path`)

**INVARIANT**: if watch path is invalid, emit `rag.watch.directory.missing` and do not emit `rag.watch.started` for that path.

**INVARIANT**: `rag.watch.reindex.complete` and `rag.watch.reconcile.complete` only occur after `rag.watch.started`.

```
rag.started
  └─> rag.pending.reconciled?               (* emitted once if pending files found at startup)
  └─> rag.orphan.purged                     (* always emitted; files=0 when nothing to purge)
  └─> rag.watch.directory.missing | rag.watch.started
      └─> rag.watch.initial.complete
      └─> rag.watch.reindex.complete*        (* zero or more)
          └─> rag.file.skipped               (* if unchanged or duplicate PDF)
          └─> rag.extraction.batch.started   (* if extraction enabled and content changed)
              └─> rag.extraction.completed | rag.extraction.failed  (* N per chunk)
              └─> rag.extraction.permanently.skipped  (* ≤ M; when chunk crosses max_attempts)
              └─> rag.extraction.batch.completed
          └─> rag.extraction.batch.skipped    (* if all chunks permanently failed)
          └─> rag.file.indexed | rag.file.deleted | rag.file.indexing.failed
      └─> rag.watch.reconcile.complete*      (* zero or more)
  └─> rag.property.index.rebuilt             (* after rebuild from metadata)
  └─> rag.shutdown
      └─> rag.watch.stopped
```

**INVARIANT**: ∀ file indexing attempt: exactly one of `rag.file.indexed`, `rag.file.deleted`,
`rag.file.skipped`, or `rag.file.indexing.failed` is emitted — these are mutually exclusive.

**INVARIANT**: `rag.file.indexing.failed` ⟹ `rag.file.skipped` is NOT emitted for same `file`.

**INVARIANT**: For `.html`/`.htm` files: `rag.html.normalization.started` ⟹
`rag.html.normalization.completed` ∨ `rag.html.normalization.failed` (same `file`).
Failure emits `failed` then indexing raises; success emits `completed` then indexing continues.

### RAG Pending Reconciliation

**INVARIANT**: `rag.pending.reconciled` is emitted at most once per startup, and only when
`get_pending_files()` returns a non-empty list (i.e., at least one file was mid-index at last shutdown).

**INVARIANT**: `rag.pending.reconciled` is emitted before `rag.watch.started` — reconciliation
completes before the watcher sweep begins, ensuring stores are consistent on first scan.

| Signal | Required Payload | Description |
|--------|-----------------|-------------|
| `rag.pending.reconciled` | `reconciled`, `cleared`, `failed_transient`, `failed_permanent` | Startup reconciliation of files interrupted mid-index |

Payload semantics:
- `reconciled`: files successfully re-indexed (both stores now consistent)
- `cleared`: files removed from `pending` because the file no longer exists on disk
- `failed_transient`: files that hit a timeout/connection error — watcher will retry on next sweep
- `failed_permanent`: files that hit an unexpected error — requires manual intervention

### RAG Orphan Purge

**INVARIANT**: `rag.orphan.purged` is emitted exactly once per startup, after pending reconciliation
and before `rag.watch.started`. `files=0` when no orphans were found.

**INVARIANT**: Only sources under configured watch directory prefixes are examined — externally
indexed sources are left untouched.

| Signal | Required Payload | Description |
|--------|-----------------|-------------|
| `rag.orphan.purged` | `files`, `chunks` | Chunks removed for source files deleted while service was down |

Payload semantics:
- `files`: number of distinct source paths whose chunks were deleted
- `chunks`: total chunks removed across all purged sources

### RAG Extraction Batch Lifecycle

**INVARIANT**: `rag.extraction.batch.started` ⟹ `rag.extraction.batch.completed` (same `file`)

| Signal | Required Payload | Description |
|--------|-----------------|-------------|
| `rag.extraction.batch.started` | `file`, `chunk_count` | Batch extraction initiated for a file |
| `rag.extraction.batch.completed` | `file`, `chunk_count`, `successful`, `written`, `duration_seconds` | Batch extraction finished (successful ≤ chunk_count; written = 0 on partial failure). Optional payload: `extraction_model`. |
| `rag.extraction.model.mismatch` | `file`, `expected_model`, `chunk_count` | Re-extraction triggered because existing chunks have different or missing extraction_model. |
| `rag.extraction.batch.skipped` | `file`, `chunk_count`, `skipped_count`, `max_attempts` | All chunks permanently failed — no pipeline call made |
| `rag.extraction.failed` | `chunk_id`, `error` | Per-chunk extraction failure (expected iteration result missing or invalid after batch parsing) |
| `rag.extraction.permanently.skipped` | `chunk_id`, `source`, `attempt_count` | Chunk crossed `max_extraction_attempts`; permanently abandoned. Persisted as `permanent=1` in `failed_extractions`. Emitted exactly once per chunk. |

Between these, per-chunk signals fire: N × `rag.extraction.completed` + M × `rag.extraction.failed`
where N + M ≤ `chunk_count`. `file` is the correlation key — matches `rag.watch.reindex.complete.file`.

`successful` = number of chunks for which batch parsing produced a valid extraction result after positional binding to the requested chunk list.
`written` = number of chunks whose extraction metadata was committed (0 when any chunk failed,
due to the all-or-nothing write invariant; equals `successful` when all chunks succeed).

**INVARIANT**: `rag.extraction.permanently.skipped` is emitted at most once per `chunk_id` — on the attempt that causes `attempt_count >= max_extraction_attempts`.

### RAG scope resolution

The RAG `/search` request body may send `scope` as a string (single scope name) or an array of strings (multiple scopes; resolved to the union of each scope's `source_prefixes`). Scope resolution runs before search; event payload `scope` is the client-provided value (string or array of strings).

| Signal | Payload | Description |
|--------|---------|-------------|
| `rag.scope.resolved` | `scope` (str \| list[str]), `prefix_count` | Scope(s) resolved to merged source_prefixes |
| `rag.scope.rejected` | `scope` (str \| list[str]), `reason`, `available` | Scope validation failed (e.g. unknown scope name or empty list) |

### HTML Normalization Lifecycle (RAG)

| Signal | Required Payload | Description |
|---|---|---|
| `rag.html.normalization.started` | `file` | HTML/HTM normalization started before chunking |
| `rag.html.normalization.completed` | `file`, `output_chars` | HTML normalized to markdown successfully |
| `rag.html.normalization.failed` | `file`, `error` | HTML normalization failed; file indexing aborted |

**INVARIANT**: `rag.html.normalization.started` ⟹ (`rag.html.normalization.completed` ∨ `rag.html.normalization.failed`) for each HTML/HTM file indexing attempt.

### Doc Generate Extraction Lifecycle

**INVARIANT**: `doc.generate.extract.success` ⟹ preceding
(`doc.generate.architecture.found` ∨ `doc.generate.architecture.notfound`)
for the same `execution_id` and `step_id`.

**INVARIANT**: `doc.generate.extract.failed` and `doc.generate.extract.success`
are terminal alternatives for one extract step attempt.

**INVARIANT**: `doc.generate.python.empty` is informational and may co-occur
with `doc.generate.extract.success`.

```
doc.generate.extract.failed
  (invalid_subsystem_path_type | empty_subsystem_path | path_outside_repo_root | path_not_directory)

doc.generate.architecture.found | doc.generate.architecture.notfound
  └─> doc.generate.python.empty? (if zero .py files)
      └─> doc.generate.extract.success
```

### Cancel Groups

Cancel groups enable iteration-level cancellation of federated requests.
A cancel group is a set of requests that share a lifecycle boundary
(e.g., all LLM calls within one map iteration).

**Wire contract**:
- `X-Internal-Request-ID`: unique per physical HTTP call (capacity, snapshots)
- `X-Pipeline-Cancel-Group`: shared across calls in one logical unit (cancel group)

**Signal**: No dedicated event signal. Cancellation of individual members
emits the existing per-request cancellation signals. Group identity is
logged at DEBUG level in `MasterRequestTracker`.

**Invariants**:
- ∀ request: belongs to at most one cancel group
- ∀ cancel_group(g): cancels ∀ r ∈ g that are still ACTIVE
- ∀ completed request: removed from its cancel group (no stale references)

### Pipeline Lifecycle Contract

**INVARIANT**: `pipeline.started` ⟹ (`pipeline.completed` ∨ `pipeline.failed` ∨ `pipeline.cancelled`)

**INVARIANT**: `pipeline.step.started` ⟹ (`pipeline.step.completed` ∨ `pipeline.step.failed` ∨ `pipeline.step.skipped`)

```
pipeline.started
  └─> pipeline.step.condition.evaluated? (if step has condition)
      └─> pipeline.step.started | pipeline.step.skipped
          └─> pipeline.step.completed | pipeline.step.failed
  └─> pipeline.completed | pipeline.failed | pipeline.cancelled
```

### Pipeline Estimate Contract

**INVARIANT**: `pipeline.estimate.requested` ⟹ (`pipeline.estimate.completed` ∨ `pipeline.estimate.failed`)

| Signal | Required payload |
|---|---|
| `pipeline.estimate.requested` | `pipeline_id`, `item_count`, `total_chars` |
| `pipeline.estimate.completed` | `pipeline_id`, `item_count`, `batch_count`, `total_source_tokens`, `budget_tokens` |
| `pipeline.estimate.failed` | `pipeline_id`, `error`, `retryable` |

### Pipeline Step Condition Contract

**INVARIANT**: ∀ conditional step S: `pipeline.step.condition.evaluated(step_name=S)` precedes either `pipeline.step.started(step_name=S)` (if `result=true`) or `pipeline.step.skipped(step_name=S)` (if `result=false`)

```
pipeline.step.condition.evaluated (result=true)
  └─> pipeline.step.started
      └─> pipeline.step.completed | pipeline.step.failed

pipeline.step.condition.evaluated (result=false)
  └─> pipeline.step.skipped
```

### Pipeline Step Model Fallback Contract

**INVARIANT**: `pipeline.step.model.fallback` is emitted only for failures
classified as eligible for alternate-model retry.

**INVARIANT**: deterministic local failures emit
`pipeline.step.model.fallback.suppressed` and MUST re-raise the original
exception without attempting alternate-model resolution.

**INVARIANT**: handler-level `ModelFallbackResolved` remains
`ProxyClientError`-only; non-proxy deterministic failures are handled by
executor-level suppression semantics above.

| Signal | Required Payload | Description |
|---|---|---|
| `pipeline.step.model.fallback` | `pipeline_id`, `execution_id`, `step_name`, `primary_model`, `fallback_model`, `primary_error_type`, `fallback_attempt`, `total_fallbacks`, `succeeded` | Executor-level fallback attempt outcome for eligible failures only |
| `pipeline.step.model.fallback.suppressed` | `pipeline_id`, `execution_id`, `step_name`, `primary_error_type`, `suppression_reason` | Explicit suppression boundary for deterministic local errors |

### RAG Retrieval Lifecycle

**INVARIANT**: `pipeline.rag.query.analysis.completed` is emitted once per retrieval step execution, before retrieval gate evaluation.

**INVARIANT**: `pipeline.rag.query.analysis.completed` ⟹ (`pipeline.rag.query.rewrite.completed` ∨ `pipeline.rag.query.rewrite.skipped`)

**INVARIANT**: `pipeline.rag.query.rewrite.skipped.reason` ∈ {`rewrite_disabled`, `needs_retrieval_false`, `step_condition_false`}

**INVARIANT**: `pipeline.rag.retrieval.params.resolved` ⟹ (`pipeline.rag.retrieval.completed` ∨ `pipeline.rag.retrieval.failed`)

**INVARIANT**: `pipeline.rag.retrieval.skipped` is emitted *before* params resolution when the
rewrite model flags an out-of-scope query and no user-supplied `rag_source_prefixes` override
is present. When skipped fires, neither params.resolved nor completed/failed are emitted.

**INVARIANT**: `pipeline.rag.scope.rejected` is emitted *before* params resolution when scope
validation fails (invalid override, invalid predicted scope, low confidence, or scope catalog
unavailable). When scope.rejected
fires, neither params.resolved nor completed/failed are emitted. Retrieval returns 0 chunks.

**INVARIANT**: `pipeline.rag.retrieval.completed` and `pipeline.rag.retrieval.failed` are terminal
alternatives — exactly one is emitted per retrieval step execution that passes params resolution.

**Scope validation**: Scope authority derives from the RAG service scope registry (`GET /scopes`),
not a static pipeline-local list. Invalid or low-confidence scopes result in fail-closed behavior
(0 chunks returned), never implicit broadening.

```
pipeline.rag.query.analysis.completed
  └─> pipeline.rag.query.rewrite.completed | pipeline.rag.query.rewrite.skipped
pipeline.rag.scope.rejected?                              (* retrieval requested, but scope policy rejection — fail-closed, 0 chunks)
pipeline.rag.retrieval.skipped?                           (* semantic no-retrieval gate, no user prefix override)
pipeline.rag.retrieval.params.resolved
  └─> [parallel queries to RAG /search]
      └─> pipeline.rag.retrieval.bibliography.filtered?  (* after merge, before completed; when junk filter drops chunks)
      └─> pipeline.rag.retrieval.source.diversity.limited? (* after bibliography filter; when per-source cap drops chunks)
      └─> pipeline.rag.neighbor.expansion.completed?     (* after junk filter, before metadata boost; when expansion enabled)
      └─> pipeline.rag.retrieval.completed | pipeline.rag.retrieval.failed
```

| Signal | Required Payload | Description |
|--------|------------------|-------------|
| `pipeline.rag.query.analysis.completed` | `pipeline_id`, `execution_id`, `step_name`, `needs_retrieval`, `scope`, `scope_confidence`, `out_of_scope_reason` | Scope-analysis decision consumed by retrieval |
| `pipeline.rag.query.rewrite.completed` | `pipeline_id`, `execution_id`, `step_name`, `rewrite_count`, `hyde_present` | Rewrite generation completed and available to retrieval |
| `pipeline.rag.query.rewrite.skipped` | `pipeline_id`, `execution_id`, `step_name`, `reason` | Rewrite generation bypassed (`rewrite_disabled`, `needs_retrieval_false`, `step_condition_false`) |
| `pipeline.rag.scope.rejected` | `pipeline_id`, `execution_id`, `step_name`, `reason`, `scope`, `details` | Scope validation rejected — fail-closed, 0 chunks returned |
| `pipeline.rag.retrieval.skipped` | `pipeline_id`, `execution_id`, `step_name`, `reason`, `out_of_scope_reason` | Retrieval skipped by semantic no-retrieval gate (query/corpus mismatch with no user prefix override) |
| `pipeline.rag.retrieval.bibliography.filtered` | `pipeline_id`, `execution_id`, `step_name`, `chunks_dropped` | Emitted when post-RRF junk/bibliography filter removes one or more chunks |
| `pipeline.rag.retrieval.source.diversity.limited` | `pipeline_id`, `execution_id`, `step_name`, `per_source_limit`, `chunks_dropped`, `chunks_before`, `chunks_after` | Emitted when source-diversity cap removes chunks from dominant source documents |
| `pipeline.rag.retrieval.params.resolved` | `pipeline_id`, `execution_id`, `step_name`, `consumer_model`, `consumer_tier`, `profile_class`, `max_chunks`, `top_k_per_query`, `rrf_k`, `scope`, `retrieval_mode`, `uses_explicit_prefixes` | Pre-retrieval: effective parameters after three-tier merge; `scope` may be string or array of strings (multiscope) |
| `pipeline.rag.neighbor.expansion.completed` | `pipeline_id`, `execution_id`, `step_name`, `enabled`, `neighbors_added`, `neighbors_fetched`, `sources_expanded`, `expansion_n`, `max_chunks`, `expansion_seconds` | Neighbor chunk expansion result — emitted when expansion is enabled, even if zero neighbors were added |
| `pipeline.rag.retrieval.completed` | `pipeline_id`, `execution_id`, `step_name`, `predicted_scope`, `scope_confidence`, `fallback_triggered`, `chunks_per_query`, `zero_result_queries`, `rrf_score_min`, `rrf_score_max`, `rrf_score_mean`, `chunks_after_merge`, `total_retrieval_seconds`, `neighbor_expansion_added` | Post-retrieval: scope prediction + quality metrics (`neighbor_expansion_added` is optional and defaults to 0 when expansion is disabled) |
| `pipeline.rag.retrieval.failed` | `pipeline_id`, `execution_id`, `step_name`, `error`, `total_retrieval_seconds` | All queries failed — no chunks to merge |

Payload semantics:
- `reason` (scope.rejected): one of `invalid_scope_override`, `invalid_predicted_scope`, `scope_confidence_below_threshold`, `scope_catalog_unavailable`
- `scope` (scope.rejected): the scope label(s) that were rejected (str or list of str)
- `details` (scope.rejected): human-readable explanation (e.g., unknown scope names, confidence values)
- `reason` (query.rewrite.skipped): `rewrite_disabled`, `needs_retrieval_false`, or `step_condition_false`
- `consumer_tier`: Caller-declared consumer capacity class (`"frontier"`, `"local"`, `"small_local"`, or None if not specified)
- `scope` (params.resolved): Resolved retrieval scope: single label (str) or list of labels (array of str) for multiscope retrieval
- `predicted_scope`: Raw scope label from the rewrite model (before alias resolution)
- `scope_confidence`: Model confidence in [0.0, 1.0]; values below threshold cause scope rejection (0 chunks)
- `fallback_triggered`: True when scope was normalized via alias resolution (no broad fallback exists — invalid/low-confidence scopes are rejected before retrieval via `pipeline.rag.scope.rejected`)
- `chunks_per_query`: Per-query result counts; `[10, 0, 8]` means query 1 returned 10, query 2 returned 0
- `zero_result_queries`: Count of queries with 0 results — high values indicate query quality or scope issues
- `rrf_score_{min,max,mean}`: Distribution of RRF scores in the merged set
- `per_source_limit` / `chunks_dropped` / `chunks_before` / `chunks_after`: Source-diversity cap impact on final candidate pool (emitted only when drops occur)
- `total_retrieval_seconds`: Wall-clock time from first query dispatch to merge completion/failure
- `neighbor_expansion_added`: Number of chunks appended during contiguous neighbor expansion (0 when expansion disabled or no eligible neighbors)

**Debugging queries**:

```bash
# Consumer tier resolution and parameter impact
jq -c 'select(.signal == "pipeline.rag.retrieval.params.resolved") |
  {tier: .payload.consumer_tier, model: .payload.consumer_model,
   class: .payload.profile_class, top_k: .payload.top_k_per_query,
   max_chunks: .payload.max_chunks}' /tmp/pipeline-events/current.jsonl

# Scope rejection events (fail-closed — invalid, low-confidence, or catalog unavailable)
jq -c 'select(.signal == "pipeline.rag.scope.rejected") |
  {reason: .payload.reason, scope: .payload.scope,
   details: .payload.details}' /tmp/pipeline-events/current.jsonl

# Scope prediction accuracy (alias normalization only — no broad fallback)
jq -c 'select(.signal == "pipeline.rag.retrieval.completed") |
  {scope: .payload.predicted_scope, confidence: .payload.scope_confidence,
   alias_normalized: .payload.fallback_triggered}' /tmp/pipeline-events/current.jsonl

# Neighbor expansion activity
jq -c 'select(.signal == "pipeline.rag.neighbor.expansion.completed") |
  {added: .payload.neighbors_added, fetched: .payload.neighbors_fetched,
   sources: .payload.sources_expanded, seconds: .payload.expansion_seconds}' \
  /tmp/pipeline-events/current.jsonl

# Source-diversity cap impact
jq -c 'select(.signal == "pipeline.rag.retrieval.source.diversity.limited") |
  {limit: .payload.per_source_limit, dropped: .payload.chunks_dropped,
   before: .payload.chunks_before, after: .payload.chunks_after}' \
  /tmp/pipeline-events/current.jsonl

# Low-quality retrievals (low RRF max or any zero-result query)
jq -c 'select(.signal == "pipeline.rag.retrieval.completed" and
  (.payload.rrf_score_max < 0.02 or .payload.zero_result_queries > 0)) |
  {scope: .payload.predicted_scope, max_rrf: .payload.rrf_score_max,
   zero_queries: .payload.zero_result_queries, chunks: .payload.chunks_after_merge}' \
  /tmp/pipeline-events/current.jsonl

# Retrieval latency distribution
jq -c 'select(.signal == "pipeline.rag.retrieval.completed") |
  {step: .payload.step_name, seconds: .payload.total_retrieval_seconds,
   chunks: .payload.chunks_after_merge}' /tmp/pipeline-events/current.jsonl

# Retrieval failures
jq -c 'select(.signal == "pipeline.rag.retrieval.failed")' /tmp/pipeline-events/current.jsonl

# Out-of-scope skips (query unanswerable from active corpus)
jq -c 'select(.signal == "pipeline.rag.retrieval.skipped") |
  {reason: .payload.reason, oos: .payload.out_of_scope_reason}' /tmp/pipeline-events/current.jsonl
```

### RAG LLM Reranking

Emitted once per `rerank_assemble` step execution, whether reranking is enabled or skipped.

**INVARIANT**: `pipeline.rag.rerank.completed` is emitted exactly once per `rerank_assemble` step execution, before `pipeline.step.completed`.

| Signal | Required Payload | Description |
|--------|------------------|-------------|
| `pipeline.rag.rerank.completed` | `pipeline_id`, `execution_id`, `step_name`, `rerank_enabled`, `model_id`, `chunks_input`, `chunks_output`, `windows_evaluated`, `max_rank_movement_observed`, `total_rerank_seconds` | Post-reranking: LLM reranking metrics or skip confirmation |

Payload semantics:
- `rerank_enabled`: True if LLM reranking was performed; False if skipped (disabled or too few chunks)
- `model_id`: Model used for reranking LLM calls (None when skipped)
- `chunks_input`: Number of candidate chunks considered for reranking
- `chunks_output`: Final chunk count after reranking (includes passthrough tail)
- `windows_evaluated`: Number of sliding windows processed by LLM (0 when skipped)
- `max_rank_movement_observed`: Largest rank position change in this execution (0 when skipped)
- `total_rerank_seconds`: Wall-clock time for the reranking phase

**Debugging queries**:

```bash
# Reranking activity
jq -c 'select(.signal == "pipeline.rag.rerank.completed") |
  {enabled: .payload.rerank_enabled, windows: .payload.windows_evaluated,
   max_move: .payload.max_rank_movement_observed, seconds: .payload.total_rerank_seconds}' \
  /tmp/pipeline-events/current.jsonl

# Reranking latency when enabled
jq -c 'select(.signal == "pipeline.rag.rerank.completed" and .payload.rerank_enabled) |
  {model: .payload.model_id, seconds: .payload.total_rerank_seconds,
   chunks: .payload.chunks_input, windows: .payload.windows_evaluated}' \
  /tmp/pipeline-events/current.jsonl
```

### Corpus Hint Filtering

**Signal**: `pipeline.rag.hints.filtered`

Emitted by the `filter_corpus_hints` step after filtering corpus hints by
chunk-weighted co-occurrence with query-derived terms from suggest_terms.

| Field | Type | Description |
|-------|------|-------------|
| `pipeline_id` | string | Pipeline ID |
| `execution_id` | string | Execution ID |
| `step_name` | string | Step name (`filter_corpus_hints`) |
| `query_terms` | string[] | Terms from suggest_terms used for lookup |
| `original_hint_count` | int | Total hints before filtering |
| `filtered_hint_count` | int | Hints after filtering (post-cap if applied) |
| `filtered_hints` | string[] | The surviving hint terms (sorted by overlap count desc) |
| `fallback` | bool | True if no co-occurrence found, all hints kept |
| `scoring_mode` | string | `"chunk_weighted"` (default) — co-occurrence scoring strategy |
| `min_threshold` | int | Minimum co-occurrence count required to keep a hint |
| `capped` | bool | True if max_hints cap was applied after filtering |
| `cap_limit` | int | Configured max_hints value (0 = no cap) |

**Invariant**: `filtered_hint_count ≤ original_hint_count`
**Invariant**: `capped=true` ⟹ `filtered_hint_count ≤ cap_limit`
**Note**: `fallback=true` ⟹ `filtered_hint_count = original_hint_count` (all hints kept as conservative default)

**Fallback chain**: chunk-weighted (N=`min_threshold`) → doc-level (N=1) → all hints (fallback=true)

### Unified Model Selection (`POST /v1/models/select`)

The unified selection endpoint runs a three-tier cascade (intelligence
profiles → cloud proxy tags → empty) server-side and returns the result
synchronously.

### Model Selection Reputation Signals

| Signal | Required Payload | Optional Payload |
|---|---|---|
| `model.selection.health.observation` | `task`, `model_id`, `outcome`, `latency_ms` | `quality_score`, `tokens_per_second` |
| `model.selection.score.updated` | `task`, `model_id`, `final_score`, `components` | — |
| `model.selection.rank.computed` | `task`, `selection_path`, `candidates` | `avoid_models` |
| `model.selection.switch.suppressed` | `task`, `sticky_key`, `current_model_id`, `contender_model_id`, `delta`, `reason` | — |
| `model.selection.switch.allowed` | `task`, `sticky_key`, `previous_model_id`, `new_model_id`, `delta` | — |
| `model.selection.filtered` | `model_id`, `reason` | — |

When a candidate is excluded by requirement checks (e.g. `min_context`, `min_completion_tokens`), the intelligence profile store logs at DEBUG with signal name `model.selection.filtered` and payload `model_id`, `reason` (`"min_context"` or `"min_completion_tokens"`). Optional emission from the selection layer (Stargate) may be added later for request-scoped correlation.

**INVARIANT**: `model.selection.score.updated` is emitted once per candidate model in a reputation-enabled request.
**INVARIANT**: `model.selection.rank.computed` includes candidates sorted descending by `final_score`.
**INVARIANT**: `model.selection.switch.suppressed` ⊕ `model.selection.switch.allowed` — exactly one is emitted when anti-thrash evaluates a candidate switch.

`avoid_models`: `list[str] | null` — model IDs excluded from this selection (set when `avoid_models_from` binding is active).

### Consultation / grounding

When the consult script's grounding guard auto-excludes a model (path hallucination), that outcome is recorded in the run artifact only; no event-bus signal is emitted. The logical signal name for this behavior is **consult.grounding.auto_excluded**. Payload (in artifact): `task`, `model_id`, `hallucination_ratio`, `invalid_paths`, `ts`. Captured in the consult run artifact as `grounding_exclusions.json` and in metadata as `grounding_exclusions_applied`.

Consult may POST to `POST /v1/models/observe` for each excluded outcome. Stargate then calls `reputation_store.observe()` and emits **model.selection.health.observation** (existing contract); no change to that signal's payload or semantics.

### Agent report-model (reducing reputation of bad models)

Agents (including consult's grounding guard) can reduce a model's reputation so selection prefers others:

- **POST /api/v1/report-model** — Request body: `task`, `model_id`, `reason`, optional `details`. Stargate maps this to `reputation_store.observe()` with `outcome=reason`, `quality_score=0`, `latency_ms=0`, and emits **model.selection.health.observation**. Use for path hallucination, wrong format, refusal, or other quality failures. Administrative API (same auth as other /api/v1 endpoints).
- **POST /v1/models/observe** — Full observation payload (task, model_id, outcome, latency_ms, quality_score?, tokens_per_second?) for callers that already have structured metrics; part of the standardized /v1 surface.

Both endpoints feed the same reputation store; negative reports lower the model's quality component and thus its rank in reputation-aware selection.

### Model Selection Decisions

Model selection for pipeline steps follows this precedence:

1. `pipeline_options.model_ref_overrides` (runtime CLI override, highest priority)
2. `model_requirements` (declarative, profile-store resolved)
3. `model_ref` in chain YAML (static fallback, lowest priority)

Selection outcomes are observable through existing signals:
- `pipeline.step.started` payload includes `model_id` - the resolved model after
  all precedence rules and overrides are applied.
- `pipeline.step.failed` captures selection failures (e.g., requirements resolved
  to zero candidates and no `model_ref` fallback). The `error` payload field
  distinguishes selection issues from generation failures.

No dedicated model-selection signal is needed - the step lifecycle signals
provide full observability.

### Assess Loop Lifecycle

**INVARIANT**: `AssessLoopStarted` ⟹ `AssessLoopCompleted`
(exactly one completed per started, even on `ProxyClientError` — handler emits in `finally` before re-raising)

**INVARIANT**: `AssessLoopStarted` ⟹ 0..N `AssessLoopIterationCompleted`
(zero iterations if the first assess call fails JSON parse before any action dispatches)

```
StepStarted (executor)
  └─ AssessLoopStarted (handler)
      └─ ModelInvocation [assess_0]
      └─ ModelInvocation [action_revise_0]
      └─ AssessLoopIterationCompleted [iteration=0, action=revise, is_terminal=false]
      └─ ModelInvocation [assess_1]
      └─ AssessLoopIterationCompleted [iteration=1, action=accept, is_terminal=true]
      └─ AssessLoopCompleted [iterations_used=2, exit_reason=terminal_action]
  └─ StepCompleted (executor)
```

`exit_reason` values: `terminal_action` | `max_consecutive` | `budget_exhausted` | `json_parse_failure` | `unknown_action` | `model_error`

These events are handler-emitted observability events written to the per-execution JSONL
(same as `ModelInvocation`), not system-level coordination signals on the global event bus.

### CombinePassages Coverage Contract

`CombinePassagesCompleted` is emitted by `CombinePassagesHandler` once per `combine` step execution, after synthesis is complete and citation coverage is measured.

**INVARIANT**: `CombinePassagesCompleted` is emitted exactly once per `pipeline.step.completed` for any step of type `consensus_combine_passages_v7`.

| Field | Type | Description |
|-------|------|-------------|
| `fact_count` | int | Total verified facts sent to combine |
| `chunk_count` | int | Synthesis chunks (1 = single call, N = chunked path) |
| `cited_count` | int | Unique fact indices cited at least once in the output |
| `uncited_indices` | list[int] | Fact indices with no citation in the output |
| `coverage_pct` | float | `cited_count / fact_count * 100`, rounded to 1 decimal |

**Query**:
```bash
# Coverage for every combine step in last run
jq -c 'select(.event_type == "combine_passages_completed") | {step: .step_name, facts: .fact_count, chunks: .chunk_count, coverage: .coverage_pct, uncited: .uncited_indices}' \
  /tmp/logs/universal-stargate/pipeline_summaries/**/*.jsonl
```

## Signal Reference

### System Events

| Signal | Payload | Correlation |
|--------|---------|-------------|
| `system.started` | `{}` | None |
| `system.shutdown` | `{}` | None |

### Request Events

| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `request.routed` | `request_id`, `model_id`, `routing_reason` | `correlation_id`, `target_gateway` |
| `request.queued` | `request_id` | `correlation_id`, `queue_position` |
| `request.processing` | `request_id` | `correlation_id` |
| `request.inference.started` | `request_id`, `model_id`, `gateway_url` | `correlation_id` |
| `request.completed` | `request_id` | `correlation_id`, `tokens`, `duration_ms` |
| `request.failed` | `request_id`, `error` | `correlation_id` |
| `request.timed.out` | `request_id` | `correlation_id`, `timeout_ms` |
| `request.profile.resolved` | `request_id`, `model_id`, `profile_name` | `correlation_id` |
| `request.capacity.timeout` | `request_id`, `model_id`, `timeout_seconds`, `retry_count`, `elapsed_s` | `pipeline_step_id` |
| `request.client.disconnected` | `request_id`, `model_id`, `hop` | `correlation_id` |
| `routing.resource.data.missing` | `request_id`, `model_id`, `gateway_ids` | - |
| `routing.model.infeasible` | `request_id`, `model_id`, `gateway_constraints`, `excluded_gateway_ids` | - |
| `routing.eviction.blocked.busy` | `request_id`, `model_id`, `gateway_id`, `loaded_count`, `busy_count`, `vram_free` | - |
| `routing.eviction.insufficient.permanent` | `request_id`, `model_id`, `gateway_id`, `reason`, `failed_constraints` | - |
| `routing.upstream.all.excluded` | `request_id`, `model_id`, `excluded_gateway_ids` | - |
| `routing.capacity.divergence` | `request_id`, `model_id`, `gateway_id`, `busy_models_state`, `capacity_pool_available`, `capacity_pool_in_flight`, `capacity_pool_max` | - |
| `routing.overflow.triggered` | `request_id`, `model_id`, `from_gateway`, `to_gateway`, `reason` | - |
| `routing.overflow.failed` | `request_id`, `model_id`, `tried_gateways`, `reason` | - |
| `model.load.overflow.started` | `request_id`, `model_id`, `gateway_id`, `reason` | - |
| `model.capacity.overflow.assigned` | `request_id`, `model_id`, `from_gateway`, `to_gateway`, `depth_before` | - |
| `federated.request.prompt.transformation.applied` | `request_id`, `model_id`, `gateway_id`, `prompt_chars` | — |
| `federated.request.prompt.transformation.failed`  | `request_id`, `model_id`, `gateway_id`, `error` | — |
| `federated.request.prompt.transformation.skipped` | `request_id`, `model_id`, `gateway_id`, `reason` | — |

### request.profile.resolved

Emitted after request preparation when a profile is in effect for the request.
This covers both auto-assignment by model basename and explicit profile override.

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | Request that resolved a profile |
| `model_id` | string | Selected model for this request |
| `profile_name` | string | Resolved profile name applied to request policy |

### request.processing vs request.inference.started

`request.processing` marks the admission/dispatch boundary (request accepted for
processing flow). `request.inference.started` marks the downstream-confirmed
runtime begin boundary (model execution has actually started on the Gateway).

For queue-wait attribution and map iteration inference timing, prefer
`request.inference.started` when available. `request.processing` remains the
compatibility fallback.

`request.inference.started` is emitted for both streaming and non-streaming
requests, after model load gating succeeds and immediately before inference
iteration begins. In federated topologies, the Edge Stargate forwards this
signal to the Master via the federation telemetry channel.

**Propagation chain** (federated topology):
```
Gateway (emit_inference_started)
  → Edge Stargate event bus (request.inference.started)
  → EdgeTelemetrySender.forward_gateway_telemetry (parse_telemetry validates)
  → [for remote edges] Relay RemoteTelemetrySender.forward_edge_telemetry
  → Master /ws/federation/master → MasterTelemetryReceiver.handle_message
  → Master event bus (request.inference.started)
```

**`source` field invariant**: `request.inference.started` telemetry payloads have
`source: null` (no `TelemetrySource`). This differs from model-lifecycle telemetry
which always carries a source. Relay forwarding code must guard against null source
before rewriting `stargate_id`.

### routing.capacity.divergence

Emitted when telemetry-derived `busy_models` disagrees with master-local
CapacityPool. Indicates stale telemetry (e.g., `MODEL_IDLE` lost due to
WebSocket drop). Informational only; CapacityPool remains authoritative for
admission.

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | Request that triggered detection |
| `model_id` | string | Model with divergent state |
| `gateway_id` | string | Gateway with divergent state |
| `busy_models_state` | string | `"busy"` or `"idle"` (telemetry claim) |
| `capacity_pool_available` | int | Available slots per CapacityPool |
| `capacity_pool_in_flight` | int | Current in-flight requests |
| `capacity_pool_max` | int | Max concurrent capacity |

### routing.eviction.blocked.busy

Emitted when routing cannot form an eviction plan *right now* because loaded
models are busy with in-flight work. This is a transient capacity state and
should be treated as retryable/queueable.

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | Request that hit transient eviction block |
| `model_id` | string | Model requested |
| `gateway_id` | string | Gateway where eviction was blocked |
| `loaded_count` | int | Number of loaded models on gateway |
| `busy_count` | int | Number of loaded models marked busy |
| `vram_free` | int | Current free VRAM on gateway (MB) |

### routing.eviction.insufficient.permanent

Emitted immediately before non-retryable RESOURCE_UNAVAILABLE when routing
determines that resources are insufficient even with eviction.

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | Request that failed permanently |
| `model_id` | string | Model requested |
| `gateway_id` | string | Gateway evaluated as permanently insufficient |
| `reason` | string | Human-readable primary failure reason |
| `failed_constraints` | list[string] | Constraint names that failed |

### routing.upstream.all.excluded

Emitted when upstream retry logic has excluded all gateways that can serve the
model for this live request. This is a fail-fast boundary: the request must not
retry the same failed gateway again. When all excluded gateways failed with
HTTP 429 (rate limit), the client receives 429; otherwise 503.

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | Request that exhausted upstream alternatives |
| `model_id` | string | Model requested |
| `excluded_gateway_ids` | list[string] | Gateways excluded due to upstream errors |

### routing.overflow.triggered

Emitted when non-sticky overflow spillover excludes the primary saturated gateway,
finds a feasible alternate gateway, and triggers spillover routing to that target.

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | Request that triggered spillover |
| `model_id` | string | Model requested |
| `from_gateway` | string | Original selected gateway before overflow |
| `to_gateway` | string | Alternate gateway selected in overflow pass |
| `reason` | string | Spillover reason (`primary_capacity_saturated`) |

### routing.overflow.failed

Emitted when the non-sticky overflow path is attempted but cannot complete due
to no alternate feasible gateway or overflow load failure.

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | Request that attempted spillover |
| `model_id` | string | Model requested |
| `tried_gateways` | list[string] | Alternate gateways evaluated in spillover path |
| `reason` | string | Failure reason (`no_alternate_gateway`, `overflow_load_failed`, etc.) |

### model.load.overflow.started

Emitted when overflow spillover selects an alternate gateway that requires a
cold-load, immediately before remote load orchestration begins.

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | Request that triggered overflow loading |
| `model_id` | string | Model being loaded on overflow gateway |
| `gateway_id` | string | Overflow gateway selected for loading |
| `reason` | string | Initiation reason (`overflow_spillover`) |

### model.capacity.overflow.assigned

Emitted at admission boundary when overflow spillover causes effective
assignment to move from the original saturated gateway to an alternate gateway.

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | Request assigned via overflow path |
| `model_id` | string | Model assigned |
| `from_gateway` | string | Primary gateway selected before spillover |
| `to_gateway` | string | Gateway selected by admission after spillover |
| `depth_before` | int | Queue depth on the primary gateway before spillover |

### Cloud Proxy Provider Adapter Contract

| Signal | Required payload |
|---|---|
| `cloud.proxy.request.forwarded` | `provider`, `model`, `streaming`, `adapter_type` |
| `cloud.proxy.request.failed` | `provider`, `model`, `status_code`, `error`, `adapter_type` |
| `cloud.proxy.request.translation.failed` | `provider`, `model`, `error`, `direction`, `adapter_type` |
| `cloud.proxy.mcp.configured` | `provider`, `mcp_server_url` |**INVARIANT**: `cloud.proxy.request.translation.failed` is emitted for adapter
conversion failures (`request`, `response`, `stream_chunk`) and is distinct from
provider HTTP failures.

### Model Events

| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `model.loaded` | `model_id` | `ram_mb`, `vram_mb` |
| `model.unloaded` | `model_id` | `reason` |
| `model.load.initiated` | `request_id`, `model_id` | `correlation_id` |
| `model.load.completed` | `request_id`, `model_id`, `duration_ms` | `correlation_id` |
| `model.load.context.mismatch` | `model_id`, `requested_context`, `actual_context`, `reason` | - |

**Note**: Execution-capacity signals (`model.execution.*` and
`model.capacity.freed`) are documented under **Capacity & Slot Lifecycle**.

### Federation Events

| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `federation.connection.established` | `remote_id`, `transport` | `latency_ms` |
| `federation.connection.authenticated` | `remote_id`, `method` | - |
| `federation.connection.lost` | `remote_id`, `reason` | - |
| `federation.snapshot.sent` | `gateway_id`, `all_models_count`, `available_models_count`, `gap_count` | - |
| `federation.telemetry.received` | `remote_id`, `model_count` | `resource_summary`, `telemetry_age_ms` |
| `federation.telemetry.applied` | `remote_id`, `changes` | - |
| `federation.telemetry.marked.stale` | `remote_id`, `age_seconds`, `threshold_seconds` | - |
| `federation.routing.delegated` | `request_id`, `target_remote`, `model_id` | `correlation_id`, `reason` |
| `federation.routing.routed.local` | `request_id`, `model_id`, `reason` | `correlation_id` |
| `federation.routing.rejected` | `request_id`, `model_id`, `reason` | `correlation_id` |
| `federation.load.requested` | `request_id`, `target_remote`, `model_id` | `correlation_id` |
| `federation.load.confirmed` | `request_id`, `remote_id`, `model_id`, `duration_ms` | `correlation_id` |
| `federation.load.failed` | `request_id`, `remote_id`, `model_id`, `error` | `correlation_id` |
| `federation.orchestrator.decided` | `request_id`, `decision_type`, `target`, `reason` | `correlation_id`, `alternatives_considered` |
| `federation.orchestrator.evicted` | `target_remote`, `model_id`, `reason` | - |

### Gateway Events

| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `gateway.state.changed` | `url`, `connectivity`, `health` | `previous_connectivity`, `previous_health` |
| `gateway.resource.updated` | `gateway_name`, `resources` | - |
| `gateway.snapshot.resource.gap` | `all_models_count`, `resource_models_count`, `gap_count`, `gap_cause` | `sample_missing` |

### RAG Events

| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `rag.started` | - | - |
| `rag.shutdown` | - | - |
| `rag.watch.directory.missing` | `path` | - |
| `rag.watch.started` | `path`, `extensions`, `recursive` | - |
| `rag.watch.initial.complete` | `path`, `files`, `reindexed`, `unchanged` | - |
| `rag.watch.reindex.complete` | `file`, `deleted`, `indexed`, `unchanged` | - |
| `rag.watch.reconcile.complete` | `path`, `recovered`, `unchanged` | - |
| `rag.watch.stopped` | `watchers` | - |
| `rag.extraction.batch.started` | `file`, `chunk_count` | - |
| `rag.extraction.batch.completed` | `file`, `chunk_count`, `successful`, `written`, `duration_seconds` | `extraction_model` (optional) |
| `rag.extraction.model.mismatch` | `file`, `expected_model`, `chunk_count` | re-extraction due to model mismatch |
| `rag.extraction.batch.skipped` | `file`, `chunk_count`, `skipped_count`, `max_attempts` | all chunks exceeded max_attempts; no pipeline call |
| `rag.extraction.completed` | `chunk_id`, `entities`, `topics` | - |
| `rag.extraction.failed` | `chunk_id`, `error` | - |
| `rag.property.index.rebuilt` | `collection`, `count` | - |
| `rag.pending.reconciled` | `reconciled`, `cleared`, `failed_transient`, `failed_permanent` | emitted once at startup if interrupted files found |
| `rag.orphan.purged` | `files`, `chunks` | emitted once at startup; chunks removed for source files deleted while service was down |
| `rag.article.registry.loaded` | `path`, `article_count` | article registry successfully loaded at startup |
| `rag.article.registry.failed` | `path`, `error` | article registry load failed at startup |
| `rag.article.registry.write.failed` | `path`, `filename`, `error` | writing entry to article registry failed during ingest |
| `rag.file.indexed` | `file`, `deleted`, `indexed`, `duration_seconds` | file fully indexed; `duration_seconds` = wall-clock time to index this file; optional: `batch_start_ts` (ISO-8601), `document_metadata` (dict — e.g. `article_title`, `article_authors`, `article_venue`, `published_date`, `article_doi` when file is in registry), `bibliography_chunks` (int — count of chunks tagged `is_bibliography` for this file) |
| `rag.file.deleted` | `file`, `deleted` | all chunks deleted, no replacement (file now empty) |
| `rag.file.skipped` | `file`, `reason` | file skipped; `reason` ∈ {`unchanged`, `duplicate_pdf`} |
| `rag.file.indexing.failed` | `file`, `error` | unhandled error aborted indexing for this file |
| `rag.html.normalization.started` | `file` | HTML ingest entered normalization pipeline (before chunking) |
| `rag.html.normalization.completed` | `file`, `output_chars` | HTML normalization succeeded; output_chars = total chunk text length |
| `rag.html.normalization.failed` | `file`, `error` | HTML normalization failed; file indexing aborted for this file |
| `rag.directory.index.started` | `path`, `total_files` | emitted once before concurrent directory index/reindex dispatch; total_files = count of files to process |
| `rag.directory.index.completed` | `path`, `total_files`, `indexed`, `deleted`, `unchanged`, `duplicates`, `errors` | emitted after all files in a directory index/reindex have been processed; absence after `rag.directory.index.started` indicates interrupted session |
| `rag.scope.resolved` | `scope`, `prefix_count` | scope(s) resolved to prefixes; `scope`: str or array of strings |
| `rag.scope.rejected` | `scope`, `reason`, `available` | scope validation failed |
| `rag.search.executed` | `query_len`, `top_k`, `results`, `scope` | search completed with ≥1 result; `scope`: str \| list[str] \| None |
| `rag.search.no_results` | `query_len`, `scope` | search completed with 0 results; `scope`: str \| list[str] \| None |
| `rag.corpus_hints.updated` | `path`, `scopes_updated`, `timestamp` | corpus_hints.yaml written after aggregation from property index |

### Doc Generate Events

| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `doc.generate.extract.success` | `execution_id`, `step_id`, `subsystem_path`, `file_count`, `class_count`, `function_count` | - |
| `doc.generate.extract.failed` | `execution_id`, `step_id`, `reason`, `error` | `subsystem_path` |
| `doc.generate.architecture.found` | `execution_id`, `step_id`, `architecture_doc_path` | - |
| `doc.generate.architecture.notfound` | `execution_id`, `step_id`, `architecture_doc_path` | - |
| `doc.generate.python.empty` | `execution_id`, `step_id`, `subsystem_path` | - |

### Pipeline Events

Pipeline events flow to two sinks:
- `/tmp/stargate-events/current.jsonl` — all signals
- `/tmp/pipeline-events/current.jsonl` — `pipeline.*` signals only (low-noise, for pipeline debugging)

| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `pipeline.started` | `pipeline_id`, `execution_id`, `domain`, `step_count`, `timeout_seconds` | - |
| `pipeline.completed` | `pipeline_id`, `execution_id`, `duration_seconds`, `step_count`, `output_step` | - |
| `pipeline.failed` | `pipeline_id`, `execution_id`, `duration_seconds`, `error`, `failed_step` | - |
| `pipeline.cancelled` | `pipeline_id`, `execution_id`, `duration_seconds`, `reason`, `completed_steps`, `pending_steps` | - |
| `pipeline.step.started` | `pipeline_id`, `execution_id`, `step_name`, `step_type`, `model_id`, `is_map_step` | - |
| `pipeline.step.completed` | `pipeline_id`, `execution_id`, `step_name`, `duration_seconds`, `output_length`, `prompt_tokens`, `completion_tokens`, `model_call_count` | `exit_code` (shell steps only) |
| `pipeline.step.failed` | `pipeline_id`, `execution_id`, `step_name`, `duration_seconds`, `error`, `prompt_tokens`, `completion_tokens`, `model_call_count` | - |
| `pipeline.step.skipped` | `pipeline_id`, `execution_id`, `step_name`, `reason` | - |
| `pipeline.step.condition.evaluated` | `pipeline_id`, `execution_id`, `step_name`, `condition`, `result`, `available_outputs` | - |
| `pipeline.step.model.fallback` | `pipeline_id`, `execution_id`, `step_name`, `primary_model`, `fallback_model`, `primary_error_type`, `fallback_attempt`, `total_fallbacks`, `succeeded` | emitted only for fallback-eligible failures |
| `pipeline.step.model.fallback.suppressed` | `pipeline_id`, `execution_id`, `step_name`, `primary_error_type`, `suppression_reason` | fallback intentionally not attempted due to deterministic local error |
| `pipeline.generation.params.filtered` | `step_name`, `model_id`, `removed_keys`, `allowed_keys` | - |
| `pipeline.estimate.requested` | `pipeline_id`, `item_count`, `total_chars` | - |
| `pipeline.estimate.completed` | `pipeline_id`, `item_count`, `batch_count`, `total_source_tokens`, `budget_tokens` | `estimated_validate_tokens` |
| `pipeline.estimate.failed` | `pipeline_id`, `error`, `retryable` | - |
| `pipeline.rag.query.analysis.completed` | `pipeline_id`, `execution_id`, `step_name`, `needs_retrieval`, `scope`, `scope_confidence`, `out_of_scope_reason` | - |
| `pipeline.rag.query.rewrite.completed` | `pipeline_id`, `execution_id`, `step_name`, `rewrite_count`, `hyde_present` | - |
| `pipeline.rag.query.rewrite.skipped` | `pipeline_id`, `execution_id`, `step_name`, `reason` | reasons: `rewrite_disabled`, `needs_retrieval_false`, `step_condition_false` |
| `pipeline.rag.scope.rejected` | `pipeline_id`, `execution_id`, `step_name`, `reason`, `scope`, `details` | fail-closed scope rejection (0 chunks); reasons: `invalid_scope_override`, `invalid_predicted_scope`, `scope_confidence_below_threshold`, `scope_catalog_unavailable` |
| `pipeline.rag.retrieval.skipped` | `pipeline_id`, `execution_id`, `step_name`, `reason`, `out_of_scope_reason` | - |
| `pipeline.rag.retrieval.params.resolved` | `pipeline_id`, `execution_id`, `step_name`, `consumer_model`, `consumer_tier`, `profile_class`, `max_chunks`, `top_k_per_query`, `rrf_k`, `scope`, `retrieval_mode`, `uses_explicit_prefixes` | `scope` may be string or array of strings (multiscope) |
| `pipeline.rag.retrieval.completed` | `pipeline_id`, `execution_id`, `step_name`, `predicted_scope`, `scope_confidence`, `fallback_triggered`, `chunks_per_query`, `zero_result_queries`, `rrf_score_min`, `rrf_score_max`, `rrf_score_mean`, `chunks_after_merge`, `total_retrieval_seconds`, `neighbor_expansion_added` | `neighbor_expansion_added` defaults to 0 when expansion disabled; `fallback_triggered` now reflects alias normalization only (no broad fallback) |
| `pipeline.rag.retrieval.failed` | `pipeline_id`, `execution_id`, `step_name`, `error`, `total_retrieval_seconds` | - |
| `pipeline.rag.retrieval.bibliography.filtered` | `pipeline_id`, `execution_id`, `step_name`, `chunks_dropped` | - |
| `pipeline.rag.retrieval.source.diversity.limited` | `pipeline_id`, `execution_id`, `step_name`, `per_source_limit`, `chunks_dropped`, `chunks_before`, `chunks_after` | - |
| `pipeline.rag.neighbor.expansion.completed` | `pipeline_id`, `execution_id`, `step_name`, `enabled`, `neighbors_added`, `neighbors_fetched`, `sources_expanded`, `expansion_n`, `max_chunks`, `expansion_seconds` | - |
| `pipeline.rag.rerank.completed` | `pipeline_id`, `execution_id`, `step_name`, `rerank_enabled`, `model_id`, `chunks_input`, `chunks_output`, `windows_evaluated`, `max_rank_movement_observed`, `total_rerank_seconds` | - |
| `pipeline.rag.hints.filtered` | `pipeline_id`, `execution_id`, `step_name`, `query_terms`, `original_hint_count`, `filtered_hint_count`, `filtered_hints`, `fallback`, `scoring_mode`, `min_threshold`, `capped`, `cap_limit` | - |

**Note on `pipeline.step.failed` partial progress**: `prompt_tokens`, `completion_tokens`,
and `model_call_count` are populated from all model calls completed before the failure,
including on timeout. A step that processes 41 claims before timing out reports those
token counts rather than zero.

### Pipeline Map Iteration Events

**Map iteration ordering (per request_id)**:

```
pipeline.map.iteration.started
  └─> pipeline.map.iteration.inference.started (primary: request.inference.started; fallback: request.processing)
      └─> pipeline.map.iteration.completed | pipeline.map.iteration.failed
```

**Emission timing**: `pipeline.map.iteration.completed` is emitted **immediately**
when each iteration's coroutine resolves (not in a post-step burst). This means
consumers see one event per iteration as it finishes, enabling real-time progress
tracking across long-running map steps. Failed/timeout/cancelled iterations emit
`pipeline.map.iteration.failed` in bulk after all tasks settle.

**INVARIANT**: If fallback timing is used,
`pipeline.map.iteration.inference.fallback` is emitted.
**INVARIANT**: If no boundary signal is seen,
`pipeline.map.iteration.inference.lost` is emitted.

| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `pipeline.map.started` | `pipeline_id`, `execution_id`, `step_name`, `total_iterations`, `timeout_seconds`, `threshold` | - |
| `pipeline.map.iteration.started` | `pipeline_id`, `execution_id`, `step_name`, `iteration_index`, `model_id`, `gateway_id` | `request_id` |
| `pipeline.map.iteration.inference.started` | `pipeline_id`, `execution_id`, `step_name`, `iteration_index`, `request_id`, `model_id`, `queue_wait_seconds` | - |
| `pipeline.map.iteration.inference.fallback` | `pipeline_id`, `execution_id`, `step_name`, `iteration_index`, `request_id`, `fallback_signal`, `reason` | - |
| `pipeline.map.iteration.inference.lost` | `pipeline_id`, `execution_id`, `step_name`, `iteration_index`, `request_id` | - |
| `pipeline.map.iteration.completed` | `pipeline_id`, `execution_id`, `step_name`, `iteration_index`, `duration_seconds` | - |
| `pipeline.map.iteration.failed` | `pipeline_id`, `execution_id`, `step_name`, `iteration_index`, `error`, `duration_seconds`, `failure_type` | - |
| `pipeline.map.completed` | `pipeline_id`, `execution_id`, `step_name`, `succeeded_count`, `failed_count`, `total_count`, `duration_seconds`, `met_threshold` | - |

**`pipeline.map.iteration.inference.started`**: Bridges Stargate request runtime-start
signals into pipeline observability using a primary-preferred stamp model:
emitted immediately when `request.inference.started` (primary) arrives, or
deferred to iteration completion from `request.processing` (fallback) timing when
the primary is absent. Exactly one emission per iteration that received at least
one boundary signal. `queue_wait_seconds` = time from iteration dispatch to
resolved inference start boundary. `request_id` correlates with request lifecycle
signals.

**`pipeline.map.iteration.inference.fallback`**: Emitted at iteration completion
only when primary runtime-start telemetry was absent and fallback timing had to be
used. Persistent occurrence indicates regression in `request.inference.started`
propagation.

**`pipeline.map.iteration.inference.lost`**: Emitted at iteration completion
when neither `request.inference.started` nor `request.processing` was observed for
the iteration request ID. Indicates a total observability gap.

**`pipeline.map.iteration.failed` `failure_type` values**: `"error"` | `"timeout"` | `"inference_timeout"` | `"cancelled"`.
`"inference_timeout"` indicates the iteration exceeded `inference_timeout_seconds` after
inference started (distinct from outer wall-clock `"timeout"`).

### Consult Call Lifecycle

Client-side events emitted by `scripts/consult` to `/tmp/consult-history/current.jsonl`.
Separate from the Stargate event bus — these track CLI consultation calls, not
server-side pipeline execution.

**INVARIANT**: `consult.call.started` ⟹ `consult.call.finished` (same `call_id`)

**INVARIANT**: `consult.call.finished` is terminal — exactly one per `call_id`,
covering success, failure, and timeout.

```
consult.call.started
  └─> [model selection, RAG retrieval, pipeline/direct execution]
      └─> consult.call.finished (success=true | success=false)
```

| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `consult.call.started` | `call_id`, `role`, `mode`, `question_preview`, `selected_models`, `pipeline_id`, `context_files`, `context_file_count`, `cloud_only` | `execution_id`, `artifact_dir` |
| `consult.call.finished` | `call_id`, `role`, `mode`, `question_preview`, `selected_models`, `used_models`, `selection_path`, `pipeline_id`, `context_files`, `context_file_count`, `output_file`, `cloud_only`, `success`, `error`, `duration_seconds` | `execution_id`, `status`, `artifact_dir`, `partial_output_available`, `chain_phase_count`, `failure_kind` |

**Correlation**: `call_id` (UUID) links started→finished pairs. `execution_id`
(from `X-Pipeline-Execution-Id` response header) correlates with
`pipeline.step.started`/`pipeline.step.completed` events in
`/tmp/pipeline-events/current.jsonl` for actual model resolution.

**`selected_models`**: For pipeline calls, `selected_models` in the started
event may be empty (server-side selection); the finished event resolves actual
models from pipeline step events via `execution_id`. For direct calls,
`selected_models` is populated from `/v1/models/select` before the call.

**`status`**: Machine-readable run outcome. Values: `success`, `pipeline_failed`,
`selection_failed`, `partial_output_available`, `stale_output_prevented`,
`command_failed`. Prefer over `success` (bool) for programmatic recovery.

**`artifact_dir`**: Absolute path to the per-run artifact directory
(`tmp/consult-runs/<ts>-<call_id>/`). Contains `metadata.json`, `stdout.md`,
`partial.json` (pipeline recorder step outputs), `chain_trace.json`
(per-phase timing for chained mode), and `stderr.log`.

**`partial_output_available`**: True when at least one pipeline recorder step
record or chain phase intermediate output was captured before termination.

**`chain_phase_count`**: Number of chain phases completed (chained mode only).
Agents can correlate with `chain_trace.json` in `artifact_dir`.

**`failure_kind`**: Populated on `selection_failed` runs. Values:
`config_missing`, `http_error`, `network_error`, `empty_result`.

## Ordering Guarantees

1. **Monotonic IDs**: Event `id` field is strictly increasing within a single Stargate instance
2. **Causal ordering**: Events from same request are ordered by `id`
3. **No cross-node ordering**: Events from different Stargates require `timestamp` comparison

## Completeness Checks

Use these jq queries to verify event completeness:

```bash
# Find requests without completion
jq -s '
  [.[] | select(.signal == "request.routed") | .payload.request_id] as $started |
  [.[] | select(.signal | test("request.(completed|failed|timed)")) | .payload.request_id] as $finished |
  $started - $finished
' events.jsonl

# Find model loads without completion
jq -s '
  [.[] | select(.signal == "model.load.initiated") | .payload.request_id] as $initiated |
  [.[] | select(.signal == "model.loaded") | .payload.model_id] as $loaded |
  $initiated - $loaded
' events.jsonl

# Find pipeline steps that started but never completed (stuck or timed out)
jq -s '
  [.[] | select(.signal == "pipeline.step.started") | .payload.step_name] as $started |
  [.[] | select(.signal | test("pipeline.step.(completed|failed|skipped)")) | .payload.step_name] as $finished |
  $started - $finished
' /tmp/pipeline-events/current.jsonl

# Timed-out steps with partial token counts
jq -c 'select(.signal == "pipeline.step.failed") | {
  step: .payload.step_name,
  error: .payload.error,
  duration: .payload.duration_seconds,
  tokens: (.payload.prompt_tokens + .payload.completion_tokens),
  calls: .payload.model_call_count
}' /tmp/pipeline-events/current.jsonl
```
