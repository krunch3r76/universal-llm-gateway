# Event Contracts

**Purpose**: Define the structure, relationships, and guarantees for all Stargate events.

## G2 anti-drift CI fast-follow (2026-06-05, thread 1310)

**Event-contracts audit**: not needed — no new `@event_factory` introduced.
The Lane B.3 tool-loop fidelity probe consumes existing signals
(`pipeline.frontier.dispatch.tool.called`, `pipeline.frontier.dispatch.tool.failed`,
`pipeline.frontier.dispatch.tool.requested`) defined in
`services/universal-stargate/systems/pipeline/core/events/dispatch/frontier_tools.py`
and emits a probe-report artifact (JSON), not new bus vocabulary.

## V1 (2026-05-19)

* Renamed: tool surface `grok_build` → `grokbuild`; module files `_grok_build_*` → `_grokbuild_*`; sidecar dir `/tmp/logs/grok-build` → `/tmp/logs/grokbuild`.
* Event signal family renamed in V1.1: `mcp.grokbuild.{family}.{event}` (4 parts; flattened from V1's 5-part `mcp.grok.build.{family}.{event}` to leave headroom under the 5-segment regex ceiling for future sub-domain events).
* `dispatch.failed` payload gains `reason_code`.
* `dispatch.called` / `dispatch.rejected` payload `op` field: now emits `'build'` instead of `'dispatch'` (rejected envelopes for retired callers still echo back the caller's value `'dispatch'` so audit consumers can see the actual input).
* Envelope metadata gains the V1 resolved param surface (tier, reasoning_effort, effort, check, no_subagents, disable_web_search, resume_strict, max_turns, best_of_n, timeout_seconds, resolved_session_id).

## Event Schema

All events follow this structure:

```json
{
  "type": "stargate_event",
  "signal": "string",
  "payload": {},
  "role": "observation | coordination | debug | realtime",
  "scope": "global | node",
  "timestamp": "ISO-8601",
  "id": "integer (monotonic)",
  "source": "universal_stargate"
}
```

## Signal Classification

Events carry `role` and `scope` fields (defaults: `observation`, `global`).
Validated by `@event_factory` at call time.

| Field | Values | Semantics |
|-------|--------|-----------|
| `role` | `coordination` | Consumed by state machines, admission control, queues. Suppressing breaks correctness. |
| `role` | `observation` | Debugging/monitoring only. Safe to suppress, deduplicate, or scope to originating node. |
| `role` | `debug` | Temporary diagnostic instrumentation. Pruned at session boundary (current session only). Excluded from business-metric operations. |
| `role` | `realtime` | High-frequency ephemeral events stored only in an in-memory ring buffer (not SQLite). Broadcast to WebSocket subscribers. Excluded from business-metric operations. |
| `scope` | `node` | Meaningful only where the action originates. Not re-emitted on master. |
| `scope` | `global` | Needs master-level visibility. Available in master event stream. |

### Debug Events

Signals with `role="debug"` are temporary diagnostic instrumentation added during
active debugging sessions. They are written directly to the event service via
`emit_debug_event()` (bypassing the Stargate event bus).

**Retention**: Pruned at every retention cycle and at startup — only events from
the current session (after the most recent `system.started`) survive. Debug events
never accumulate across sessions.

**Query visibility**: Excluded from business-metric operations (`recent-failures`,
`noise-profile`, `federation-health`, `capacity-snapshot`). Visible in
`pipeline-trace`, `request-trace`, `signal-events`, and `raw_sql` queries.

**Usage**: `from universal_event_bus.events.debug import emit_debug_event`

```python
await emit_debug_event(
    "pipeline.debug.validate",
    {"execution_id": ctx.execution_id, "available_outputs": list(ctx.outputs.keys())},
    source="pipeline.journal_extract.validate",
)
```

**Convention**: Debug signals use `*.debug.*` naming (e.g., `pipeline.debug.validate`).

### Realtime Events

Signals with `role="realtime"` are high-frequency ephemeral events that bypass
SQLite entirely. They are stored in an in-memory ring buffer (default 10,000
events, configurable via `REALTIME_BUFFER_SIZE` env var) and broadcast to
WebSocket subscribers in real-time.

**Storage**: In-memory `deque(maxlen=N)` only — never written to SQLite. Events
are lost on service restart. The ring buffer overwrites oldest events when full.

**WebSocket replay**: On new WebSocket subscription, the current ring buffer
contents are sent as a replay burst before switching to live push. This gives
clients a window of recent realtime activity without needing SQLite.

**Query visibility**: Not present in SQLite, so excluded from all named operations
except `realtime-snapshot` (which reads directly from the ring buffer). Also
excluded defensively from business-metric operations via `role NOT IN ('debug',
'realtime')` filters.

**Query**: Use `realtime-snapshot` operation to read the buffer:
```python
observability(operation="realtime-snapshot", params={"limit": 50})
```

**Use cases**: Heartbeat telemetry, high-frequency resource updates, live
streaming metrics — any signal where persistence is unnecessary and volume
would bloat the SQLite store.

### Coordination Events

Signals that MUST carry `role="coordination"`. Suppressing these breaks system correctness.

| Signal | Consumer | Coordination Role |
|--------|----------|-------------------|
| `model.execution.completed` | CapacityWaiter, GatewayTracker | Slot release, queue wake |
| `model.execution.failed` | CapacityWaiter, GatewayTracker | Slot release, queue wake |
| `model.capacity.freed` | CapacityWaiter | Queue wake (model unloaded) |
| `model.loading.started` | RAG ContextualizeModelCoordinator | Cold-load window opened; batch coordinators pause new submissions for this `model_id` |
| `model.loaded` | SequentialLoader, RAG ContextualizeModelCoordinator | Load completion; batch coordinators resume submissions |
| `model.unloaded` | SequentialLoader | Unload/eviction; informational for resident-state observers. Batch coordinators MUST NOT pause on bare unloads — without a paired `model.loading.started`, the next submission simply triggers Stargate to reload on demand. |
| `model.load.failed` | SequentialLoader, RAG ContextualizeModelCoordinator | Load failure; batch coordinators restore optimism so the next submission triggers a loud retry. Carries optional `gateway_state_snapshot` (master-side cached view) and `worker_snapshot` (edge-side process tree + live VRAM/RAM) for forensics — see "Forensics payloads" below. |
| `worker.evicted` | (eviction observers only) | Eviction with `trigger_model_id` provenance (paired with `model.unloaded`); informational. Same rule as `model.unloaded` — not a coordinator clear signal. |
| `model.available` | RAG, admission tooling | Aggregate catalog: at least one connected path can serve `model_id` (union of Stargate-visible catalogs). Not resident load state. |
| `model.unavailable` | RAG, admission tooling | Aggregate catalog: no path can serve `model_id`. Emitted only when the last serving path disappears. |

**Disambiguation**: `model.loaded` / `model.unloaded` describe **resident** lifecycle on a gateway URL. `model.available` / `model.unavailable` describe **aggregate routability** at the Stargate view (federation + local). Downstream services that gate work on “can this ID be routed?” MUST use `model.available` / `model.unavailable` (or the equivalent HTTP catalog), not `model.loaded` alone.

**Batch-coordinator surface (advisory, not correctness)**: `model.loading.started`, `model.loaded`, and `model.load.failed` form the published surface for batch pipelines (RAG indexing/contextualization, fine-tuning prep, bulk eval) to coordinate cold-load windows. Per the `stargate-model-lifecycle` invariant, subscribers MUST treat these as hints — every wait MUST cap on a timeout and fall through to the optimistic submit path. A subscriber that ignores the entire stream MUST still produce the correct result; only throughput differs. ¬gate first-request correctness on these signals.

**Bare unloads are NOT coordinator-clear signals.** A `model.unloaded` (or `worker.evicted`) without a paired `model.loading.started` describes a routine VRAM eviction; the next inference request will re-trigger load-on-demand. Coordinators that pause on bare unloads will stall indefinitely whenever the upstream `model.loading.started` / `model.loaded` stream is partial — exactly the failure mode `todo:stargate-model-load-event-emission-gap` tracks.

All signals not listed above default to `role="observation"` and are safe to suppress,
deduplicate, or scope to the originating node.

### Node-Scoped Signals

Signals with `scope="node"` exist only at the originating node (edge container or remote host).
They are not re-emitted on master to reduce event noise.

| Signal | Origin | Why node-scoped |
|--------|--------|-----------------|
| `gateway.resource.updated` | Edge gateway | VRAM/RAM state churn from telemetry processing |
| `federation.model.lifecycle` | Edge gateway | Busy/idle flapping per slot transition |

### Querying by Classification

```bash
# Find all coordination events
scripts/query-events --sql "SELECT signal, role, scope, payload FROM events WHERE role='coordination' ORDER BY seq DESC LIMIT 100"

# Find node-scoped events (only visible at originating node)
scripts/query-events --sql "SELECT signal, role, scope, payload FROM events WHERE scope='node' ORDER BY seq DESC LIMIT 100"
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

Migration note: `scripts/rag-status --watch` moved from JSONL polling to event-service WebSocket consumption. This is a transport-only consumer change; no new signals were added and no existing signal contracts were modified.

### Request Lifecycle

**INVARIANT**: `request.started` ⟹ (`request.completed` ∨ `request.failed` ∨ `request.timed.out` ∨ `request.client.disconnected`)

```
request.routed
  └─> request.queued (if queued)
      └─> request.processing
          └─> request.inference.started
          └─> request.completed | request.failed | request.timed.out | request.client.disconnected
```

### MCP Request Lifecycle

MCP server events use a two-tier retention model: forensic audit signals
(`role="coordination"`) survive session pruning; high-volume transport and
dispatch breadcrumbs default to `role="observation"` and are capped at the
2-session `system.started` boundary.

Every request-scoped MCP event includes sanitized caller context from the
authenticated ASGI scope when available:

#### MCP event role taxonomy (forensic vs session telemetry)

- **Forensic audit (`role="coordination"`)**: `mcp.request.started`, `mcp.request.completed`, `mcp.request.failed`, `mcp.request.unauthorized`, the auth-admission family (`mcp.auth.admitted`, `mcp.oauth.token.accepted/rejected`, `mcp.oauth.profile.rejected`), and `fs.timeout.suspected`. These survive the 7-day age cap; `run_session_retention()` in `libs/event_store/store.py` skips `role='coordination'` rows (the session cap does not apply).
- **Session telemetry (`role="observation"`, default)**: transport signals (`mcp.transport.*`), high-volume success breadcrumbs (`mcp.tool.dispatch.success`, `mcp.cortex.relay.*`, `mcp.tool.file.read`, etc.). Pruned at the 2-session `system.started` boundary.
- **Post-mortem query path**: to answer "did seat X call tool Y?", query coordination-tier `mcp.request.started` or `mcp.request.completed` filtered on `json_extract(payload,'$.tool_name')` — not inner dispatch breadcrumbs.
- **Connector-side blind spot**: `tool_search` executed pre-bound on the connector (Cursor manifest lookup, no `/mcp` round-trip) emits no server event. Server-side `tool_search` calls appear as `mcp.tool.search.called` (observation). Absence of events does not imply the tool never ran.
- **Promotion policy**: explicit `role="coordination"` at the `record()` call site only. No suffix-based auto-promotion manifest. Promote additional signals only when a demonstrated post-mortem query fails against the coordination request tier.

| Field | Type | Description |
|-------|------|-------------|
| `request_profile` | string | Active MCP profile (`default`, `cursor_safe`, etc.) |
| `caller_identity` | string | Non-secret principal label: OAuth `client_id`, `cursor`, or configured static-token identity |
| `oauth_client_id` | string | OAuth client ID when `auth_mode="oauth"` |
| `client_ip` | string | Socket peer IP |
| `mcp_method` | string | JSON-RPC method (`tools/call`, `tools/list`, etc.) |
| `tool_name` | string | MCP tool name for `tools/call` requests |
| `response_bytes` | integer | Bytes written on the HTTP response body |

| Signal | Role | Required Payload | Description |
|--------|------|------------------|-------------|
| `mcp.request.started` | coordination | `method`, `client_ip`, `auth_mode`, `mcp_method` | HTTP request accepted by MCP request middleware |
| `mcp.request.completed` | coordination | `method`, `client_ip`, `duration_s`, `auth_mode`, `response_bytes` | Request returned without raising server-side exception |
| `mcp.request.failed` | coordination | `method`, `client_ip`, `duration_s`, `error`, `exc_type`, `auth_mode`, `response_bytes` | Request raised before completion |
| `mcp.tool.file.read.timeout` | observation | `path`, `extension`, `elapsed_s`, `timeout_s` | Server-side PDF extraction exceeded the fs read budget |
| `fs.timeout.suspected` | coordination | `tool_name`, `duration_s`, `response_bytes`, `client_ip`, `auth_mode`, `mcp_method` | Derived signal for provider-side MCP cutoffs: `tool_name="fs"`, `duration_s >= 25`, and `0 < response_bytes <= 100` |

`recent-failures` includes signals ending in `.failed`, `.error`, `.timeout`,
and the derived `fs.timeout.suspected` signal.

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

**INVARIANT**: `model.load.initiated` ⟹ (`model.loaded` ∨ `model.load.failed`)

```
model.load.initiated
  └─> model.loading.started + worker.loading
      └─> model.loaded | model.load.failed
```

#### `model.load.failed` Forensics Payloads

`model.load.failed` carries two optional diagnostic payloads. Both are
best-effort and may be absent — coordination correctness does not depend
on them; they exist for oncall debugging.

**`gateway_state_snapshot`** (master-side, lagging view): captured by
Stargate from the WebSocket client's cached projection of the gateway at
the moment the failure was observed.

```
{
  gateway_name: str,
  gateway_url: str,
  captured_at: float,         # epoch seconds
  loaded_models: list[str],
  loading_models: list[str],
  busy_models: list[str],
  model_details: { model_id: { ...scalar VRAM/RAM fields... } },
  measured_model_vram_mb: { model_id: int },
  resources: { total_vram_mb, available_vram_mb, total_ram_mb, available_ram_mb },
}
```

**`worker_snapshot`** (edge-side, live view): captured on the gateway at
the actual failure point and forwarded over the WebSocket
`telemetry.model.loading.failed` message.

```
{
  failed_worker: {
    model_id: str,
    pid: int | None,
    supervisor_status: str | None,
    child_processes: [
      { pid: int, name: str|None, rss_mb: int|None, status: str|None,
        tree_vram_mb?: int }
    ],
  },
  peer_workers: [
    { model_id: str, pid: int|None, status: str|None,
      child_processes: [...] }
  ],
  resources: {
    total_vram_mb: int, available_vram_mb: int, hardware_used_vram_mb: int,
    total_ram_mb: int,  available_ram_mb: int,  hardware_used_ram_mb: int,
  },
}
```

The two snapshots are intentionally complementary: `gateway_state_snapshot`
reflects what Stargate *believed* about the gateway via telemetry;
`worker_snapshot` reflects what the gateway *actually had running* when the
load failed (peer llama-cpp/vLLM EngineCore subprocesses, real psutil RSS,
live nvidia-smi VRAM totals).

**Federation pass-through**: Both snapshots ride the federation
`telemetry.model.loading.failed` envelope from edge Stargate to master
Stargate. The master applier (`FederatedGatewayManager._apply_model_load_failed`)
extracts them from the parsed payload and passes them to its own
`ModelLoadingFailed` emission, so master-published `model.load.failed` events
carry the same forensics as the originating edge event. Three `model.load.failed`
events therefore appear in the host event store for a single failure: the
gateway-origin observation event (with `worker_snapshot` only, captured at the
process), the edge Stargate coordination event (with both snapshots, enriched
locally), and the master Stargate coordination event (with both snapshots,
enriched via federation pass-through).

### Worker Lifecycle Signals

Coordination signals for cold-worker visibility. Downstream services (RAG,
pipelines) use these to avoid stampeding cold workers with concurrent requests.

| Signal | Role | Scope | Required Payload | Description |
|--------|------|-------|------------------|-------------|
| `worker.evicted` | coordination | global | `model_id`, `trigger_model_id`, `vram_freed_mb`, `gateway_name` | Stargate evicted a model from a gateway to free VRAM for another model |
| `worker.loading` | coordination | global | `model_id`, `estimated_vram_mb`, `trigger` | Gateway began loading a model into a worker slot |
| `inference.dequeued` | coordination | node | `worker_id`, `model_id`, `request_id`, `queue_wait_ms` | Worker inference slot acquired from FifoCapacityGate; marks queue-wait boundary |

**`worker.evicted`**: Emitted by Stargate's eviction executor after `model.unloaded`
is confirmed for each evicted model. `trigger_model_id` identifies the model that
needed the freed VRAM. `vram_freed_mb` is the aggregate estimate from the eviction
plan.

**`worker.loading`**: Emitted by the gateway alongside `model.loading.started`.
`estimated_vram_mb` comes from the model catalog requirements. `trigger` is
`"on_demand"` (inference-path auto-load) or `"explicit"` (API load endpoint).

**`inference.dequeued`**: Emitted by the worker subprocess to the Event Service
immediately after `FifoCapacityGate.acquire()` returns. `queue_wait_ms` is the
time (ms) spent waiting for an inference slot — the exact boundary between
queue-wait and active inference latency. `scope: node` (worker-local; not
re-emitted on master). Source: `emit_inference_dequeued()` in
`services/_universal-llm-gateway/src/core/workers/worker/events.py`.

**Distinction from `worker.model.loading`**: The existing `worker.model.loading`
signal (emitted by the worker subprocess to Event Service) carries only
`worker_id` and `model_id`. `worker.loading` is a gateway EventBus signal with
VRAM estimates for downstream coordination.

### Federation Monitoring Events

| Signal | Required Payload | Optional Payload |
|--------|-----------------|------------------|
| `federation.catalog.vram.drift` | `gateway_id`, `model_id`, `measured_mb`, `catalog_mb`, `drift_pct` | — |

**`federation.catalog.vram.drift`**: Emitted when `RESOURCE_UPDATE.model_vram` reveals that a loaded model's actual GPU VRAM (measured via nvidia-smi) diverges from the catalog estimate by >5%.

**Single-writer invariant**: `RESOURCE_UPDATE` no longer carries `loaded_models` in its
wire payload. Model lifecycle state (`loaded_models`, `busy_models`, `loading_models`)
is exclusively maintained by discrete events (`MODEL_LOADED`, `MODEL_UNLOADED`,
`MODEL_BUSY`, `MODEL_IDLE`, `MODEL_LOADING_STARTED`). Edge telemetry forwarding
also strips `loaded_models`/`busy_models` from forwarded `RESOURCE_UPDATE` messages;
lifecycle state is forwarded via dedicated callbacks instead.

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

#### Cloud Upstream Request vs Gateway Error Semantics

For cloud-backed federated gateways (`backend_type == "cloud_api"`), upstream
provider 4xx responses represent client/request errors and are preserved as
client-visible 4xx responses. They MUST NOT be treated as gateway-health
failures for circuit-breaker purposes.

By contrast:

- cloud upstream 5xx responses are surfaced as 502 (provider outage)
- local/federated upstream HTTP failures are surfaced as 502 (gateway error)

`model.execution.failed` remains a request-terminal lifecycle signal and does
not by itself imply infrastructure failure. Consumers MUST NOT infer
gateway-health failure from that signal alone — the `error` field payload
distinguishes between `Upstream 4xx` (request error) and `Upstream 5xx`
(provider/gateway error).

### Federation Capacity Seeding

**INVARIANT**: ∀ `GATEWAY_SNAPSHOT` received: `_seed_capacity_pool_for_gateway`
seeds all models with `max_concurrent_requests` from `model_resources`.

**INVARIANT**: ∀ `MODEL_LOADED` without prior `GATEWAY_SNAPSHOT` for that
gateway: `_restore_model_capacity` applies `fallback_max_concurrent=1` and
emits `federation.capacity.fallback.applied`. This fallback is corrected when
the snapshot arrives and `_seed_capacity_pool_for_gateway` runs with
authoritative `model_resources`.

| Signal | Required Payload | Description |
|---|---|---|
| `federation.capacity.fallback.applied` | `gateway_id`, `model_id`, `fallback_max_concurrent`, `reason` | Capacity seeded from fallback default; snapshot not yet received for this gateway |

**Debugging queries**:

```bash
# Capacity fallback events (indicates GATEWAY_SNAPSHOT timing issue)
scripts/query-events --sql "SELECT signal, payload FROM events WHERE signal='federation.capacity.fallback.applied' ORDER BY seq DESC LIMIT 50"

# Verify fallback was later corrected by snapshot
scripts/query-events --sql "SELECT signal, payload FROM events WHERE signal='federation.capacity.fallback.applied' OR (signal='federation.telemetry.received' AND json_extract(payload, '$.msg_type')='telemetry.gateway.snapshot') ORDER BY seq DESC LIMIT 100"
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

### RAG Watcher Lifecycle

**INVARIANT**: `rag.watch.started` ⟹ `rag.watch.initial.started` ⟹ `rag.watch.initial.complete` (same `path`).

**INVARIANT**: `rag.watch.initial.started` ⟹ `rag.watch.initial.progress*` ⟹ `rag.watch.initial.complete` where `progress*` is zero or more events.

**INVARIANT**: for each `rag.watch.initial.progress`, `processed = reindexed + unchanged + errors` and `processed <= total_files`.

**INVARIANT**: if watch path is invalid, emit `rag.watch.directory.missing` and do not emit `rag.watch.started` for that path.

**INVARIANT**: `rag.watch.reindex.complete` and `rag.watch.reconcile.complete` only occur after `rag.watch.started`.

```
rag.started
  └─> rag.start.degraded?                  (* emitted when Stargate-backed activation is blocked after core boot *)
      └─> rag.dependency.retry.scheduled*  (* zero or more retries with bounded backoff *)
  └─> rag.dependencies.activated           (* always emitted after successful activation; may follow rag.start.degraded? *)
      └─> rag.pending.reconciled?          (* emitted once if pending files found at startup *)
      └─> rag.orphan.purged               (* always emitted; files=0 when nothing to purge)
      └─> rag.exclusion.purged            (* always emitted; files=0 when nothing to purge)
      └─> rag.watch.directory.missing | rag.watch.started
          └─> rag.watch.initial.started
              └─> rag.watch.initial.progress*   (* zero or more, monotonic processed count *)
              └─> rag.watch.initial.complete
          └─> rag.watch.reindex.complete*        (* zero or more)
              └─> rag.file.skipped               (* if unchanged or duplicate PDF)
              └─> rag.chunk.noise.tagged*        (* zero or more; per-chunk when heuristic tags noise)
              └─> rag.extraction.batch.started   (* if extraction enabled and content changed)
                  └─> rag.extraction.completed | rag.extraction.failed  (* N per chunk)
                  └─> rag.extraction.permanently.skipped  (* ≤ M; when chunk crosses max_attempts)
                  └─> rag.extraction.batch.completed | rag.extraction.batch.timed.out
              └─> rag.extraction.batch.skipped    (* if all chunks permanently failed)
              └─> rag.contextualization.started
                  └─> rag.chunk.contextualization.started*
                  └─> rag.chunk.contextualization.completed*
                  └─> rag.chunk.contextualization.failed*
                  └─> rag.contextualization.tail.abandoned (* straggler path)
                  └─> rag.contextualization.exception.recorded (* if degraded)
                  └─> rag.contextualization.completed
              └─> rag.embedding.chunk.fallback*   (* zero or more; chunk kept as zero vector on persistent embedding fault)
              └─> rag.file.indexed | rag.file.deleted | rag.file.indexing.failed
          └─> rag.watch.reconcile.complete*      (* zero or more)
  └─> rag.property.index.rebuilt             (* after rebuild from metadata)
  └─> rag.shutdown
      └─> rag.watch.stopped
```

Note: `rag.watch.initial.complete.files` remains a legacy success count that excludes errored files. `total_files` in `rag.watch.initial.started` and `rag.watch.initial.progress` counts all scheduled candidates.

**INVARIANT**: `rag.started` means local core boot completed; it does not imply Stargate-backed dependencies are active.

**INVARIANT**: ∀ startup where automatic indexing is enabled: `rag.dependencies.activated` is always emitted exactly once, whether activation succeeded immediately or after retries.

**INVARIANT**: `rag.start.degraded` ⟹ `rag.dependency.retry.scheduled*` ⟹ `rag.dependencies.activated` — the degraded path always terminates in activation, not in silence.

**INVARIANT**: `rag.watch.started` only occurs after `rag.dependencies.activated` when automatic indexing is enabled.

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
| `rag.orphan.purged` | `files`, `chunks` | Missing watched sources reconciled during startup; `chunks` counts only Chroma deletions |

Payload semantics:
- `files`: number of distinct watched source paths reconciled back to filesystem truth
- `chunks`: total Chroma chunks removed across all purged sources
- `sources` (optional): list of purged filenames (basename only); present when `files > 0`
- `files > 0` with `chunks = 0` is valid when cleanup removed metadata-only stale sources

### RAG Exclusion Purge

**INVARIANT**: `rag.exclusion.purged` is emitted exactly once per startup, after orphan purge
and before `rag.watch.started`. `files=0` when no excluded sources were found in the index.

**INVARIANT**: Only sources under configured watch directory prefixes whose filenames match
an `exclude` pattern (via `fnmatch`) are examined. Files not under any watch prefix are untouched.

| Signal | Required Payload | Description |
|--------|-----------------|-------------|
| `rag.exclusion.purged` | `files`, `chunks` | Indexed sources matching exclusion patterns purged during startup |

Payload semantics:
- `files`: number of distinct source paths purged because they now match an exclusion pattern
- `chunks`: total Chroma chunks removed across all purged sources
- `sources` (optional): list of purged filenames (basename only); present when `files > 0`
- Covers the case where a file was previously indexed but later added to the `exclude` list

### RAG Extraction Batch Lifecycle

**INVARIANT**: `rag.extraction.batch.started` ⟹ (`rag.extraction.batch.completed` ∨ `rag.extraction.batch.timed.out`) (same `file`)

| Signal | Required Payload | Description |
|--------|-----------------|-------------|
| `rag.extraction.batch.started` | `file`, `chunk_count` | Batch extraction initiated for a file |
| `rag.extraction.batch.completed` | `file`, `chunk_count`, `successful`, `written`, `duration_seconds` | Batch extraction finished (successful ≤ chunk_count; written = 0 on partial failure). Optional payload: `extraction_model`, `finish_reason` (present when pipeline stop reason ≠ "stop", e.g. `"length"` = max_tokens truncation). |
| `rag.extraction.model.mismatch` | `file`, `expected_model`, `chunk_count` | Re-extraction triggered because existing chunks have different or missing extraction_model. |
| `rag.extraction.batch.timed.out` | `file`, `chunk_count`, `timeout_seconds`, `duration_seconds` | Extraction batch exceeded dynamic timeout budget; all chunks recorded as transient failures |
| `rag.extraction.batch.skipped` | `file`, `chunk_count`, `skipped_count`, `max_attempts` | All chunks permanently failed — no pipeline call made |
| `rag.extraction.failed` | `chunk_id`, `error` | Per-chunk extraction failure (expected iteration result missing or invalid after batch parsing) |
| `rag.extraction.permanently.skipped` | `chunk_id`, `source`, `attempt_count` | Chunk crossed `max_extraction_attempts`; permanently abandoned. Persisted as `permanent=1` in `failed_extractions`. Emitted exactly once per chunk. |
| `rag.extraction.unavailable` | `pipeline`, `error` | Extraction pipeline not routable via Stargate at watcher start. Watcher is not started; RAG serves queries but does not index until restart. |
| `rag.extraction.structurally.unavailable` | `model_id`, `reason`, `detail` | Extraction model ID has no Stargate catalog entry; failures are marked permanent (no retry loop). |

Between these, per-chunk signals fire: N × `rag.extraction.completed` + M × `rag.extraction.failed`
where N + M ≤ `chunk_count`. `file` is the correlation key — matches `rag.watch.reindex.complete.file`.

`successful` = number of chunks for which batch parsing produced a valid extraction result after positional binding to the requested chunk list.
`written` = number of chunks whose extraction metadata was committed (0 when any chunk failed,
due to the all-or-nothing write invariant; equals `successful` when all chunks succeed).

**INVARIANT**: `rag.extraction.permanently.skipped` is emitted at most once per `chunk_id` — on the attempt that causes `attempt_count >= max_extraction_attempts`.

**Note**: When the extraction backend model is not loaded, RAG holds workers using existing `model.loaded` / `model.unloaded` events from the Event Service (no `rag.extraction.circuit.*` signals).

### RAG Extraction Admission Lifecycle

**Role**: `coordination`. Advisory signals emitted by RAG's
`ExtractionAdmissionGate` (see `services/rag/extraction_admission.py`) when it
self-regulates the extraction worker based on observed Stargate signals
(`pipeline.map.iteration.failed`, `pipeline.map.completed`,
`federation.gateway.{degraded,recovered}`, `model.{loading.started,loaded,load.failed}`).

**INVARIANT**: `rag.extraction.admission.closed` ⟹
(`rag.extraction.admission.opened` matching the same `pipeline_id`) eventually,
once all close-reasons clear. ¬ correctness gate; the per-chunk client timeout
remains the correctness backstop.

| Signal | Required Payload | Description |
|--------|------------------|-------------|
| `rag.extraction.admission.closed` | `pipeline_id`, `reason`, `active_reasons`, `signal` | Gate transitioned OPEN → CLOSED. `reason` ∈ {`iteration-timeout-burst`, `step-failure-ratio`, `gateway:<gateway_id>`, `model:<model_id>`}. `signal` is the upstream Stargate signal that drove the transition. |
| `rag.extraction.admission.opened` | `pipeline_id`, `cleared_reason`, `signal`, `closed_seconds` | Last active close-reason cleared; gate reopened. `closed_seconds` measures the wall-clock window between the matching `closed` and this `opened`. |
| `rag.extraction.admission.timeout` | `pipeline_id`, `waited_seconds`, `active_reasons` | The extraction worker's pre-dequeue wait timed out and the worker proceeded optimistically. Each occurrence is a tuning datum, not a failure. |

`pipeline_id` is the correlation key — `closed` and `opened` events for the
same `pipeline_id` (typically `rag-extract-knowledge`) bracket a single
admission window.

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

### Doc Generate Enforce Lifecycle

**INVARIANT**: `doc.generate.enforce.success` ⟹ no `doc.generate.authored.loss`
for the same `execution_id` and `step_id` (AUTHORED loss aborts the run).

**INVARIANT**: `doc.generate.authored.loss` is emitted only when an AUTHORED region
from `existing_doc` is absent from the reviewed doc; the step then raises and the
pipeline does not emit final output.

```
review (LLM) ─> enforce (deterministic)
  ├─> doc.generate.authored.loss? (blocking — run aborts)
  └─> doc.generate.enforce.success
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

### Pipeline Execution Contract

No pipeline-wide admission layer.

Backpressure for pipeline-driven inference lives at:
- per-step map fanout (for example `max_concurrency` in map steps)
- request-level `capacity.pool.*` events in `CapacityPool`

Pipeline execution starts immediately once the request is accepted:

```
pipeline.started
  └─> pipeline.step.started / pipeline.map.started / ...
  └─> capacity.pool.queued? / capacity.pool.admitted? (request-level contention only)
  └─> pipeline.completed | pipeline.failed | pipeline.cancelled
```

#### Execution Result Payload (`GET /api/v1/pipelines/executions/{id}`)

```json
{
  "execution_id": "string",
  "pipeline": "string",
  "status": "completed | failed | cancelled | running",
  "started_at": "ISO-8601",
  "completed_at": "ISO-8601 | null",
  "result": {
    "content": "string",
    "model": "string",
    "usage": {
      "prompt_tokens": 0,
      "completion_tokens": 0,
      "total_tokens": 0,
      "reasoning_tokens": 0
    },
    "duration_s": 0.0,
    "reasoning": "string | null",
    "hints": []
  },
  "error": null
}
```

**`result.hints`** is always present (possibly empty `[]`) so callers can safely
test `len(result["hints"]) > 0` without checking key presence. `None` is never
returned for `hints`; absence of hints is represented as `[]`.

Each hint object has a `type` discriminator. Currently defined types:

| `type` | Emitted when | Key fields |
|---|---|---|
| `output_short` | Dispatch completed with fewer output tokens than `SHORT_OUTPUT_TOKEN_THRESHOLD` (likely provider degradation) | `output_tokens`, `tool_calls_made`, `finish_reason`, `block_reason`, `provider`, `reason`, `suggestion` |
| `param_dropped` | A generation parameter was silently ignored by the adapter (unsupported model+param combination) | `param`, `value`, `reason` |

The bus-delivery envelope (`result_delivery` turns on agent-bus) also includes
`hints` when non-empty, as a peer to `usage` — degradation-aware subscribers
do not need a second poll round-trip to `/api/v1/pipelines/executions/{id}`.

### Pipeline Lifecycle Contract

**INVARIANT**: `pipeline.started` ⟹ (`pipeline.completed` ∨ `pipeline.failed` ∨ `pipeline.cancelled`)

**INVARIANT**: `pipeline.step.started` ⟹ (`pipeline.step.completed` ∨ `pipeline.step.failed` ∨ `pipeline.step.skipped`)

**INVARIANT**: `pipeline.execution.timed.out` and `pipeline.deadlock.detected` are
failure-boundary signals emitted immediately before the corresponding
`PipelineExecutionError` is raised.

**INVARIANT**: `pipeline.model.gate.claimed` ⟹
(`pipeline.model.gate.released` ∨ `pipeline.model.gate.failure.release`)
for the same `pipeline_id` + `execution_id` + `step_id` + `model_id`.
`model_id` is the resolved execution target model identity used for
claim/release correlation (not the internal local lock-tracker key).

**INVARIANT**: `pipeline.dag.execution.completed` is emitted exactly once when all
steps are terminal (`COMPLETED` ∪ `SKIPPED` ∪ `FAILED`).

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
      └─> pipeline.rag.neighbor.expansion.applied?       (* after junk filter, before metadata boost; when expansion enabled)
      └─> pipeline.rag.coverage.selection.applied?       (* after metadata boost scoring; when coverage selection is enabled)
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
| `pipeline.rag.retrieval.params.resolved` | `pipeline_id`, `execution_id`, `step_name`, `consumer_model`, `consumer_tier`, `profile_class`, `max_chunks`, `top_k_per_query`, `rrf_k`, `scope`, `retrieval_mode`, `uses_explicit_prefixes`, `pool_b_enabled` | Pre-retrieval: effective parameters after three-tier merge; `scope` may be string or array of strings (multiscope); `pool_b_enabled` indicates sparse facet/IDF pool (Pool B) active |
| `pipeline.rag.neighbor.expansion.applied` | `pipeline_id`, `execution_id`, `step_name`, `enabled`, `neighbors_added`, `neighbors_fetched`, `sources_expanded`, `expansion_n`, `max_chunks`, `expansion_seconds` | Neighbor chunk expansion result — emitted when expansion is enabled, even if zero neighbors were added |
| `pipeline.rag.coverage.selection.applied` | `pipeline_id`, `execution_id`, `step_name`, `enabled`, `applied`, `chunks_before`, `chunks_after` | Coverage-aware selection outcome after metadata boost scoring (only emitted when coverage selection is enabled) |
| `pipeline.rag.retrieval.completed` | `pipeline_id`, `execution_id`, `step_name`, `predicted_scope`, `scope_confidence`, `fallback_triggered`, `chunks_per_query`, `zero_result_queries`, `rrf_score_min`, `rrf_score_max`, `rrf_score_mean`, `chunks_after_merge`, `total_retrieval_seconds`, `neighbor_expansion_added`, `coverage_bias_applied`, `coverage_bias_query_class`, `coverage_bias_anchor_source`, `coverage_bias_boosted_chunks` | Post-retrieval: scope prediction + quality metrics; coverage-bias fields default when query-class bias is off (`coverage_bias_applied=false`, `coverage_bias_query_class=default`, `coverage_bias_anchor_source=null`, `coverage_bias_boosted_chunks=0`) |
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
- `coverage_bias_applied` / `coverage_bias_query_class` / `coverage_bias_anchor_source` / `coverage_bias_boosted_chunks`: Query-class coverage bias (enumeration/API-surface queries) — boosts distinct sections from the dominant source before diversity pruning

**Debugging queries**:

```bash
# Retrieve all RAG retrieval pipeline events for an execution
scripts/query-events --op pipeline-trace --execution-id ID
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
# Reranking metrics for one execution
scripts/query-events --op pipeline-trace --execution-id ID
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

### Generation Context Refinement

**Signal**: `pipeline.rag.generation.context.refined`

Emitted by `refine_generation_context` after scope-filtering register
vocabulary and enriching must_include with corpus-validated anchors.
Runs after `analyze_scope`, before `generate_rewrites`/`generate_hyde`.

| Field | Type | Description |
|-------|------|-------------|
| `pipeline_id` | string | Pipeline ID |
| `execution_id` | string | Execution ID |
| `step_name` | string | Step name (`refine_generation_context`) |
| `predicted_scopes` | string[] | Scopes from analyze_scope used for filtering |
| `original_must_include` | string[] | must_include tokens before enrichment |
| `enriched_must_include` | string[] | must_include tokens after adding scope anchors |
| `scope_anchors_added` | string[] | New anchor terms added by enrichment |
| `flat_hint_count` | int | Co-occurrence-filtered flat hints for predicted scopes |
| `register_scopes_included` | int | Scopes in the filtered register vocabulary |
| `register_scopes_total` | int | Total scopes in unfiltered register vocabulary |
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

### TUI Events

Source: `/tmp/tui-events/current.jsonl`

| Signal | Payload | Notes |
|--------|---------|-------|
| `tui.started` | `pid` | Emitted on mount; absence after known launch = crash |
| `tui.exited` | `reason` | Emitted on clean quit (q / ctrl+c); absence = crash or SIGKILL |

Crash evidence: `/tmp/logs/tui/tui.log` (append-mode, traceback on unhandled exception).

### Request Events

<!-- GENERATED:START region=capacity inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `capacity.admission.paused` | `model_id`, `duration_s`, `reason` | Admission for model_id suspended for duration_s seconds. |
| `capacity.admission.resumed` | `model_id`, `reason` | Admission pause cleared for model_id; queued waiters may be admitted. |
| `capacity.pool.admitted` | `request_id`, `model_id`, `gateway_id`, `wait_ms` | Queued request assigned a slot after waiting in FIFO queue. |
| `capacity.pool.cancelled` | `request_id`, `model_id`, `wait_ms`, `reason` | Queued request removed before admission due to explicit cancellation. |
| `capacity.pool.full` | `request_id`, `model_id`, `current_depth`, `max_depth` | Queue at max depth — request rejected immediately (overload protection). |
| `capacity.pool.queued` | `request_id`, `model_id`, `queue_position`, `allowed_gateways` | Request entered per-model FIFO admission queue in CapacityPool. |
| `capacity.pool.waiting` | `request_id`, `model_id`, `wait_ms`, `queue_position`, `queue_depth` | Request is still queued in CapacityPool; waiting remains non-terminal. |
| `capacity.slot.leak.recovered` | `request_id`, `gateway_id`, `model_id`, `snapshot` | Create CAPACITY_SLOT_LEAK_RECOVERED event. |
<!-- GENERATED:END region=capacity -->
<!-- GENERATED:START region=federated inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `federated.request.prompt.transformation.applied` | `request_id`, `model_id`, `gateway_id`, `prompt_chars` | — |
| `federated.request.prompt.transformation.failed` | `request_id`, `model_id`, `gateway_id`, `error` | — |
| `federated.request.prompt.transformation.skipped` | `request_id`, `model_id`, `gateway_id`, `reason` | — |
<!-- GENERATED:END region=federated -->
<!-- GENERATED:START region=model inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `model.available` | `model_id` | Publish aggregate routing availability for a model ID at Stargate scope. |
| `model.capacity.freed` | `url`, `model_id` | Create model.capacity.freed event (wake-only, no slot release). |
| `model.capacity.overflow.assigned` | `request_id`, `model_id`, `from_gateway`, `to_gateway`, `depth_before` | Admission moved to overflow gateway |
| `model.execution.completed` | `url`, `model_id`, `request_id`, `gateway_id` | Create model.execution.completed event (request-scoped slot release). |
| `model.execution.failed` | `url`, `model_id`, `request_id`, `gateway_id`, `error` | Create model.execution.failed event (request-scoped slot release). |
| `model.execution.started` | `url`, `model_id` | Create model.execution.started event. |
| `model.load.blocked` | `model_id`, `reason`, `required_vram_mb`, `available_vram_mb`, `required_ram_mb`, `available_ram_mb`, `bypassed_margin` | Create MODEL_LOAD_BLOCKED event (Recommendation #7: Observability). |
| `model.load.completed` | `model_id`, `gateway_url`, `gateway_name`, `timestamp`, `success`, `load_time_ms`, `error`, `request_id?` | `correlation_id` |
| `model.load.context.mismatch` | `model_id`, `requested_context`, `actual_context`, `reason` | Create MODEL_LOAD_CONTEXT_MISMATCH event. |
| `model.load.failed` | `model_id`, `error_message`, `failure_reason`, `worker_snapshot?` | Create MODEL_LOAD_FAILED event. |
| `model.load.failed` | `url`, `model_id`, `error`, `gateway_name`, `gateway_state_snapshot`, `worker_snapshot` | Create MODEL_LOAD_FAILED event. |
| `model.load.initiated` | `model_id`, `gateway_url`, `gateway_name`, `timestamp`, `already_loaded`, `request_id?` | `correlation_id` |
| `model.load.overflow.started` | `request_id`, `model_id`, `gateway_id`, `reason` | Overflow gateway cold-load initiated |
| `model.loaded` | `model_id`, `vram_usage_mb`, `ram_usage_mb`, `process_pid` | `ram_mb`, `vram_mb` |
| `model.loaded` | `gateway_id`, `model_id` | `ram_mb`, `vram_mb` |
| `model.loaded` | `url`, `model_id`, `gateway_name`, `vram_mb`, `ram_mb` | `ram_mb`, `vram_mb` |
| `model.loading.progress` | `model_id`, `phase`, `pct` | Create MODEL_LOADING_PROGRESS heartbeat event. |
| `model.loading.progress` | `url`, `model_id`, `phase`, `pct`, `gateway_name` | Create MODEL_LOADING_PROGRESS heartbeat event. |
| `model.loading.started` | `model_id` | `role=coordination`, `scope=global` — bridged from gateway WebSocket telemetry; opens cold-load window for batch coordinators |
| `model.loading.started` | `url`, `model_id` | `role=coordination`, `scope=global` — bridged from gateway WebSocket telemetry; opens cold-load window for batch coordinators |
| `model.loading.stuck` | `url`, `model_id`, `elapsed_s`, `ttl_s` | Signal that model load exceeded stuck TTL; reservation cleared. |
| `model.selection.health.observation` | `task`, `model_id`, `outcome`, `latency_ms`, `quality_score?`, `tokens_per_second?` | `quality_score`, `tokens_per_second` |
| `model.selection.rank.computed` | `task`, `candidates`, `selection_path` | `avoid_models` |
| `model.selection.score.updated` | `task`, `model_id`, `final_score`, `components` | — |
| `model.selection.switch.allowed` | `task`, `sticky_key`, `previous_model_id`, `new_model_id`, `delta` | — |
| `model.selection.switch.suppressed` | `task`, `sticky_key`, `current_model_id`, `contender_model_id`, `delta`, `reason` | — |
| `model.unavailable` | `model_id` | Publish aggregate routing loss for a model ID at Stargate scope. |
| `model.unloaded` | `model_id` | `reason` |
| `model.unloaded` | `gateway_id`, `model_id` | `reason` |
| `model.unloaded` | `url`, `model_id`, `gateway_name` | `reason` |
| `model.unloading.started` | `model_id` | Create MODEL_UNLOADING_STARTED event. |
<!-- GENERATED:END region=model -->
<!-- GENERATED:START region=request inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `request.alias.resolved` | `request_id`, `alias_id`, `backing_model_id` | Create REQUEST_ALIAS_RESOLVED event. |
| `request.client.disconnected` | `request_id`, `model_id`, `hop`, `gateway_url?`, `duration?` | `correlation_id`, `gateway_url`, `duration` |
| `request.completed` | `request_id`, `gateway_url`, `model_id`, `duration` | `correlation_id`, `tokens`, `duration_ms` |
| `request.deadline.exceeded` | `request_id`, `model_id`, `gateway_id`, `deadline_s`, `elapsed_ms` | Client-supplied X-Request-Timeout deadline exceeded mid-inference. Distinct from `request.timed.out` (queue TTL). role=observation. |
| `request.failed` | `request_id`, `gateway_url`, `model_id`, `error`, `error_code`, `error_source`, `error_data`, `caller_hint` | `correlation_id`, `error_code`, `error_source`, `error_data` (incl. `topology_snapshot` for `MODEL_NOT_FOUND`), `caller_hint` |
| `request.gateway.trace` | `request_id`, `model_id`, `phase`, `selected_gateway`, `capacity_gateway`, `sticky_gateway`, `final_gateway`, `forwarded_gateway`, `remote_id`, `gateway_url`, `invariant_status`, `reason` | `selected_gateway`, `capacity_gateway`, `sticky_gateway`, `final_gateway`, `forwarded_gateway`, `remote_id`, `gateway_url`, `reason` |
| `request.inference.started` | `request_id`, `model_id`, `gateway_url`, `correlation_id` | `correlation_id` |
| `request.inference.started` | `request_id`, `model_id`, `gateway_url`, `correlation_id` | `correlation_id` |
| `request.processing` | `request_id`, `gateway_url`, `model_id` | `correlation_id` |
| `request.profile.resolved` | `request_id`, `model_id`, `profile_name` | `correlation_id` |
| `request.queued` | `model_id`, `request_id`, `messages`, `parameters`, `stream` | `correlation_id`, `queue_position` |
| `request.queued` | `request_id`, `model_id`, `priority` | `correlation_id`, `queue_position` |
| `request.removed` | `request_id`, `reason`, `model_id`, `age_seconds` | Create REQUEST_REMOVED event. |
| `request.routed` | `request_id`, `model_id`, `gateway_url`, `gateway_name`, `timestamp`, `routing_time_ms`, `queue_position`, `immediate_route` | `correlation_id`, `target_gateway` |
| `request.snapshot.completed` | `request_id`, `model_id`, `gateway_id`, `content`, `usage`, `duration_s`, `phase` | Snapshot the completed response (non-streaming only). |
| `request.snapshot.failed` | `request_id`, `model_id`, `error`, `error_code`, `error_source`, `error_data`, `caller_hint`, `phase` | Snapshot a request failure. |
| `request.snapshot.received` | `request_id`, `model_id`, `messages`, `is_pipeline`, `phase` | Snapshot the raw incoming request before routing. |
| `request.snapshot.routed` | `request_id`, `model_id`, `gateway_id`, `profile_name`, `phase` | Snapshot the routing decision (model, gateway, profile). |
| `request.timed.out` | `request_id`, `gateway_url`, `model_id`, `timeout_seconds` | `correlation_id`, `timeout_ms` |
<!-- GENERATED:END region=request -->
<!-- GENERATED:START region=routing inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `routing.capacity.divergence` | `request_id`, `model_id`, `gateway_id`, `busy_models_state`, `capacity_pool_available`, `capacity_pool_in_flight`, `capacity_pool_max` | Create ROUTING_CAPACITY_DIVERGENCE event. |
| `routing.capacity.preseeded` | `request_id`, `model_id`, `gateway_id`, `placeholder_capacity`, `catalog_capacity` | Cold-load loading placeholder capacity applied before `model.loaded` |
| `routing.debug.gateway.dropout` | `model_id`, `stage`, `all_gateway_ids`, `surviving_gateway_ids`, `dropped_gateway_ids`, `detail` | Emitted when one or more gateways are dropped by the health filter. |
| `routing.debug.gateway.registered` | `gateway_id`, `remote_stargate_id`, `node_id`, `catalog_size`, `is_http_polling` | Emitted when a new gateway is registered (initial or reconnect). |
| `routing.debug.gateway.removed` | `remote_stargate_id`, `removed_gateway_ids`, `remaining_gateway_ids` | Emitted when gateways are removed on edge/remote disconnect. |
| `routing.drain.initiated` | `request_id`, `target_model_id`, `gateway_ids`, `drained_model_ids`, `duration_s`, `starved_for_ms` | Emit when starvation-triggered admission drain begins. |
| `routing.eviction.blocked.busy` | `request_id`, `model_id`, `gateway_id`, `loaded_count`, `busy_count`, `vram_free`, `candidate_breakdown` | Create ROUTING_EVICTION_BLOCKED_BUSY event. |
| `routing.eviction.execute.failed` | `request_id`, `model_id`, `gateway_id`, `selection_tier`, `selection_reason`, `models_to_evict`, `freed_vram_mb`, `freed_ram_mb`, `estimated_cost`, `cooldown_protected_count`, `demand_protected_count`, `candidate_breakdown`, `timestamp` | Emit when T2 finalize-time eviction execution failed. |
| `routing.eviction.insufficient.permanent` | `request_id`, `model_id`, `gateway_id`, `reason`, `failed_constraints` | Create ROUTING_EVICTION_INSUFFICIENT_PERMANENT event. |
| `routing.eviction.wait.cancelled` | `request_id`, `model_id`, `waited_ms` | Emit when eviction wait was cancelled (client disconnect / task cancel). |
| `routing.eviction.wait.resolved` | `request_id`, `model_id`, `gateway_id`, `waited_ms` | Emit when eviction wait completed and selection succeeded. |
| `routing.eviction.wait.started` | `request_id`, `model_id`, `timeout_s`, `queue_depth` | Emit when request enters eviction wait queue (transient eviction blocked). |
| `routing.eviction.wait.timeout` | `request_id`, `model_id`, `waited_ms`, `exit_reason`, `exit_constraint_summary` | Emit when the eviction wait exits without a resolved placement. |
| `routing.inference.oom.recovery.started` | `request_id`, `model_id`, `gateway_id`, `evicting_count`, `evicting_models` | Emit when OOM recovery begins (evicting idle models). |
| `routing.inference.oom.recovery.succeeded` | `request_id`, `model_id`, `gateway_id`, `evicted_count` | Emit when retry after OOM recovery succeeds. |
| `routing.model.grace.queued` | `request_id`, `model_id`, `timeout_s`, `unhealthy_gateway_ids` | Emit when a request enters model-scoped grace waiting. |
| `routing.model.grace.resolved` | `request_id`, `model_id`, `gateway_id`, `waited_ms` | Emit when model-scoped grace unblocks after model gateway recovery. |
| `routing.model.grace.timeout` | `request_id`, `model_id`, `waited_ms` | Emit when model-scoped grace expires without model gateway recovery. |
| `routing.model.infeasible` | `request_id`, `model_id`, `gateway_constraints`, `excluded_gateway_ids` | Create ROUTING_MODEL_INFEASIBLE event. |
| `routing.overflow.failed` | `request_id`, `model_id`, `tried_gateways`, `reason` | No feasible spillover path |
| `routing.overflow.failed` | `request_id`, `model_id`, `from_gateway`, `reason` | No feasible spillover path |
| `routing.overflow.triggered` | `request_id`, `model_id`, `from_gateway`, `to_gateway`, `reason` | Spillover branch selected |
| `routing.overflow.triggered` | `request_id`, `model_id`, `from_gateway`, `to_gateway`, `reason` | Spillover branch selected |
| `routing.resource.data.missing` | `request_id`, `model_id`, `gateway_ids` | Create ROUTING_RESOURCE_DATA_MISSING event. |
| `routing.startup.queued` | `request_id`, `model_id`, `uptime_s`, `timeout_s` | Emit when a request is held during startup window (no gateways yet). |
| `routing.startup.resolved` | `request_id`, `model_id`, `gateway_id`, `waited_ms`, `uptime_s` | Emit when startup-queued request unblocks after a gateway connects. |
| `routing.startup.timeout` | `request_id`, `model_id`, `waited_ms`, `uptime_s` | Emit when startup queue window exhausted with no gateway connecting. |
| `routing.upstream.all.excluded` | `request_id`, `model_id`, `excluded_gateway_ids` | Create ROUTING_UPSTREAM_ALL_EXCLUDED event. |
<!-- GENERATED:END region=routing -->
<!-- GENERATED:START region=scheduler inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `scheduler.eviction.cooldown.applied` | `model_id`, `gateway_id`, `protected_count`, `cooldown_s`, `timestamp` | — |
| `scheduler.eviction.cooldown.blocked` | `request_id`, `model_id`, `gateway_id`, `evicted_model_id`, `escape_reason`, `cooldown_remaining_s`, `candidates_in_cooldown`, `candidates_demand_protected`, `timestamp` | `request_id`, `cooldown_remaining_s`, `candidates_in_cooldown`, `candidates_demand_protected` |
| `scheduler.eviction.cooldown.overridden` | `model`, `node`, `remaining_s`, `requester`, `gateway_id`, `timestamp` | Emit when required eviction overrides cooldown for the selected victim. |
| `scheduler.eviction.demand.applied` | `model_id`, `gateway_id`, `protected_count`, `waiter_counts`, `timestamp` | — |
| `scheduler.routing.decided` | `model_id`, `original_model_id`, `selected_gateway`, `selection_reason`, `selection_tier`, `candidate_count`, `feasible_count`, `evaluation_time_ms`, `request_id`, `timestamp`, `candidates` | Create ROUTING_DECISION event. |
| `scheduler.routing.dequeued` | `request_id`, `model_id`, `gateway_id`, `wait_ms`, `timestamp` | Emit when a queued request is dequeued and assigned to a gateway. |
| `scheduler.routing.failed` | `model_id`, `original_model_id`, `candidate_count`, `evaluation_time_ms`, `request_id`, `timestamp`, `reason` | `original_model_id`, `request_id` |
| `scheduler.routing.queued` | `request_id`, `model_id`, `constraint`, `gateway_id`, `timestamp` | `gateway_id` |
| `scheduler.routing.timeout` | `request_id`, `model_id`, `constraint`, `wait_ms`, `timestamp` | Emit when a queued request exceeds wait timeout for its constraint. |
<!-- GENERATED:END region=scheduler -->
<!-- GENERATED:START region=token inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `token.count.completed` | `request_id`, `model_id`, `gateway_url`, `timestamp`, `success`, `count_time_ms`, `input_tokens`, `context_limit`, `allocated_max_tokens`, `error` | Create TOKEN_COUNT_COMPLETED event. |
| `token.count.precondition` | `request_id`, `model_id`, `target_gateway`, `selected_gateway`, `gateway_url`, `remote_id`, `sticky`, `loaded_on_gateway`, `known_to_gateway`, `skip_requested`, `legal_reason`, `content_type`, `tools_count` | `selected_gateway`, `gateway_url`, `remote_id`, `content_type` |
| `token.counting.failed` | `request_id`, `model_id`, `gateway_id`, `error` | Create TOKEN_COUNTING_FAILED event. |
<!-- GENERATED:END region=token -->

### scheduler.eviction.cooldown.blocked / cooldown.applied / demand.applied

Eviction hysteresis signals. When the eviction planner protects models from
eviction due to cooldown window or routing queue demand, informational events
are emitted. `scheduler.eviction.cooldown.applied` fires when ≥1 candidate
was shielded by cooldown. `scheduler.eviction.demand.applied` fires when ≥1
candidate had queued consumers. If ALL candidates are protected and the escape
hatch activates, `scheduler.eviction.cooldown.blocked` fires with the evicted
model and the reason the protection was overridden.

### scheduler.routing.failed / queued / dequeued / timeout

`scheduler.routing.failed` now represents permanent routing failure boundaries.
Retryable pre-routing failures emit `scheduler.routing.queued` and wait for
`gateway.resource.updated` signals. Successful wakeups emit
`scheduler.routing.dequeued`; exhausted wait budget emits
`scheduler.routing.timeout`.

### request.profile.resolved

Emitted after request preparation when a profile is in effect for the request.
This covers both auto-assignment by model basename and explicit profile override.

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | Request that resolved a profile |
| `model_id` | string | Selected model for this request |
| `profile_name` | string | Resolved profile name applied to request policy |

### request.alias.resolved

Emitted during request preparation when a user-local persona alias is resolved
to a backing model. This is an ingress convenience: routing and execution always
use the backing model ID.

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | Request that resolved a persona alias |
| `alias_id` | string | Persona alias ID requested by client |
| `backing_model_id` | string | Concrete model ID used for routing/execution |

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

### routing.capacity.preseeded

Emitted when cold-load admission seeds `CapacityPool` with **loading placeholder
capacity** (bounded by `capacity_pool.loading_phase_cap` in Stargate config),
not the model's full post-load `max_concurrent_requests`. Full catalog capacity
is restored when the load completes (`restore_model_capacity` after successful
remote load, and via telemetry-driven paths).

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | Request that triggered the seed |
| `model_id` | string | Model being cold-loaded |
| `gateway_id` | string | Target gateway |
| `placeholder_capacity` | int | Slots exposed while the model is still loading |
| `catalog_capacity` | int | Full `max_concurrent_requests` from gateway `model_details` |

### capacity.slot.leak.recovered

Canary signal emitted by `CapacityPool._recover_leaked_slot` when the
cancellation race in `_wait_for_slot` is detected: `_dispatch` resolved a
waiter's future (incrementing `in_flight`) but the waiter's task was cancelled
before a `CapacityToken` was created. Without recovery, the slot leaks
permanently. Non-zero rate under load is expected (asyncio scheduling race);
sustained high rate warrants timeout tuning investigation.

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | Request whose slot was leaked and recovered |
| `gateway_id` | string | Gateway where the slot was allocated |
| `model_id` | string | Model the slot was reserved for |
| `snapshot` | dict | `CapacityPool.get_snapshot()` at recovery time |

### capacity.pool.queued

Request entered the per-model FIFO admission queue in `CapacityPool`. Emitted
when no immediate slot is available and the request will wait for a slot to open.

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | Request entering the queue |
| `model_id` | string | Model being requested |
| `queue_position` | int | 1-indexed position in the FIFO queue |
| `allowed_gateways` | int | Number of gateways the request can be served by |
 
### capacity.pool.waiting

Request remains queued in the per-model FIFO admission queue. Non-terminal heartbeat
signal only; waiting is not itself an error as long as the caller still wants the work.

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | Request still waiting |
| `model_id` | string | Model being requested |
| `wait_ms` | float | Total time spent waiting so far (ms) |
| `queue_position` | int | Current 1-indexed position in the FIFO queue |
| `queue_depth` | int | Total current queue depth for this model |

### capacity.pool.admitted

Queued request was assigned a slot after waiting in the FIFO queue. The `wait_ms`
field indicates how long the request was queued before a slot opened.

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | Request that was admitted |
| `model_id` | string | Model the slot is for |
| `gateway_id` | string | Gateway where the slot was assigned |
| `wait_ms` | float | Time spent waiting in the queue (ms) |

### capacity.pool.full

Queue at max depth — request rejected immediately (overload protection). The
`max_queue_depth` setting on `CapacityPool` bounds the number of waiters per model
to prevent unbounded queue growth under sustained overload.

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | Request that was rejected |
| `model_id` | string | Model whose queue is full |
| `current_depth` | int | Queue depth at rejection time |
| `max_depth` | int | Configured maximum queue depth |

### capacity.pool.cancelled

Queued request removed before admission because the caller explicitly stopped waiting
or the local execution task was cancelled.

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | Request removed from the queue |
| `model_id` | string | Model the request was waiting for |
| `wait_ms` | float | Total time spent waiting before cancellation (ms) |
| `reason` | string | Cancellation source, e.g. `explicit_cancel` or `task_cancelled` |

### routing.eviction.blocked.busy

Emitted when routing cannot form an eviction plan *right now* because loaded
models are busy with in-flight work. This is a transient capacity state and
should be treated as retryable/queueable.

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | Request that hit transient eviction block |
| `model_id` | string | Model requested |
| `gateway_id` | string | Primary candidate gateway used for the summary fields (back-compat) |
| `loaded_count` | int | Loaded-model count on primary candidate |
| `busy_count` | int | Busy-model count on primary candidate |
| `vram_free` | int | Free VRAM on primary candidate (MB) |
| `candidate_breakdown` | list[dict] | Per-candidate snapshot — each entry carries `gateway_id`, `loaded_count`, `busy_count`, `loading_count`, `vram_free`, `constraints_failed`. Additive; existing consumers that read only the primary fields are unaffected. `loading_count` lets post-hoc queries correlate entry-time loading state with wait-exit constraint flips. |

### routing.eviction.insufficient.permanent

Emitted immediately before non-retryable INSUFFICIENT_VRAM when routing
determines that resources are insufficient even with eviction.

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | Request that failed permanently |
| `model_id` | string | Model requested |
| `gateway_id` | string | Gateway evaluated as permanently insufficient |
| `reason` | string | Human-readable primary failure reason |
| `failed_constraints` | list[string] | Constraint names that failed |
| `verdict_class` | string | Admission verdict when structural (`insufficient_structural`) |
| `needed_mb` | int | Margin-adjusted VRAM requirement |
| `footprint_est_mb` | int | Catalog footprint estimate |
| `margin_mb` | int | Capped headroom component |
| `attainable_mb` | int | Hardware ceiling minus nonreclaimable overhead |
| `reserved_mb` | int | VRAM reserved by in-flight loading models |

### routing.eviction.wait.* (pre-selection queue)

When the DecisionEngine returns no gateway because of transient
`eviction_blocked_by_busy_models` (busy models preventing eviction), the
request enters a server-side wait queue instead of returning 503. Applies to
both sticky and non-sticky models because the check runs before the
sticky/non-sticky failure split. These signals track the wait lifecycle.

| Signal | When |
|--------|------|
| `routing.eviction.wait.started` | Request enters wait queue; payload includes `queue_depth` (current waiters) and `timeout_s` |
| `routing.eviction.wait.resolved` | State changed, selection succeeded; payload includes `gateway_id` and `waited_ms` |
| `routing.eviction.wait.timeout` | Wait exited without resolution; payload includes `waited_ms`, `exit_reason`, `exit_constraint_summary` |
| `routing.eviction.wait.cancelled` | Client disconnected or task cancelled during wait |
| `routing.eviction.execute.failed` | T2 finalize-time eviction execution failed after admission; payload carries the planned eviction (`models_to_evict`, `freed_vram_mb`, `freed_ram_mb`), hysteresis context (`cooldown_protected_count`, `demand_protected_count`), and per-candidate `candidate_breakdown` for forensics. Always emitted before the 503 EVICTION_FAILED response. |

`queue_depth` in `.started` is a gauge for SRE capacity planning and monitoring.

`routing.eviction.wait.timeout.exit_reason` distinguishes two terminal paths
under one signal:

| `exit_reason` | Meaning |
|---------------|---------|
| `budget_exhausted` | Waited the full `eviction_wait_timeout_s` budget; `waited_ms ≈ timeout_s`. Genuine capacity timeout. |
| `non_transient` | First-iteration exit because the retry's trace no longer carries `eviction_blocked_by_busy_models`. Typically `waited_ms ≈ 0`. Indicates the rejection-time classifier saw a transient condition that the retry resolved as permanent — often a parallel load consuming VRAM between rejection and retry. |

`exit_constraint_summary` is a per-candidate snapshot
(`[{gateway_id, constraints_failed: [str]}]`) captured at exit so consumers
can inspect which constraint replaced the transient tag.

### routing.startup.* (startup gateway wait)

When Stargate receives a request before any gateway has connected, and Stargate
is still within its startup window (`request_queue.startup_queue_timeout_s`,
default 180s), the request is held rather than immediately rejected with
`GATEWAY_DISCONNECTED`. The wait wakes immediately when the first gateway
registers (generation-aware, event-driven — no polling). Once the window
expires, the request fails with the normal no-gateways error.

| Signal | When |
|--------|------|
| `routing.startup.queued` | Request held; payload includes `uptime_s` and remaining `timeout_s` |
| `routing.startup.resolved` | A gateway connected; payload includes `gateway_id`, `waited_ms`, `uptime_s` |
| `routing.startup.timeout` | Startup window expired with no gateway appearing; payload includes `waited_ms`, `uptime_s` |

### routing.model.grace.* (model-scoped gateway recovery)

When at least one gateway is healthy but none advertise the requested model,
while an unhealthy or circuit-broken gateway still lists the model in its
catalog, the request is held for `request_queue.model_gateway_grace_timeout_s`
(default 90s). Other models continue to route immediately. If a healthy gateway
advertising the model appears, the wait resolves; if the window expires, normal
`MODEL_NOT_FOUND` / no-feasible-gateway handling applies.

| Signal | When |
|--------|------|
| `routing.model.grace.queued` | Request held; payload includes `timeout_s` and `unhealthy_gateway_ids` |
| `routing.model.grace.resolved` | A healthy gateway recovered with the model; payload includes `gateway_id`, `waited_ms` |
| `routing.model.grace.timeout` | Grace window expired without recovery; payload includes `waited_ms` |

### OOM Recovery

**INVARIANT**: `routing.inference.oom.recovery.started` ⟹ (`routing.inference.oom.recovery.succeeded` ∨ `routing.inference.oom.recovery.failed`)

| Signal | Payload | Description |
|--------|---------|-------------|
| `routing.inference.oom.recovery.started` | request_id, model_id, gateway_id, evicting_count, evicting_models | Inference 500 detected; evicting idle models |
| `routing.inference.oom.recovery.succeeded` | request_id, model_id, gateway_id, evicted_count | Retry after eviction succeeded |
| `routing.inference.oom.recovery.failed` | request_id, model_id, gateway_id | Retry after eviction still failed |
| `routing.inference.oom.banned` | model_id, gateway_id | Model banned on gateway for session |

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
| `cloud.proxy.mcp.configured` | `provider`, `mcp_server_url` |
| `cloud.proxy.dispatch.catalogmiss` | `provider`, `model_id`, `reason` |
| `debug.cloud.params.stripped` | `provider`, `model`, `stripped` (list[str]), `surface` (`chat_completions` \| `responses` \| `responses_stream`) |

**INVARIANT**: `cloud.proxy.request.translation.failed` is emitted for adapter
conversion failures (`request`, `response`, `stream_chunk`) and is distinct from
provider HTTP failures.

**Note**: `cloud.proxy.dispatch.catalogmiss` fires at catalog-fetch time when
`capability_dispatch.resolve` raises `CatalogMissError` for a catalog model
(provider outside the dispatch surface map). The catalog entry is then served
WITHOUT a `dispatch` facet rather than with an invented default; the event makes
that omission observable. It does not fire for the in-surface unknown-claude
ceiling fallback, which resolves normally.

**Note**: `debug.cloud.params.stripped` fires only when the OpenAI-compatible
adapter drops params (`temperature`, `top_p`, `presence_penalty`,
`frequency_penalty`) that OpenAI reasoning-model families (`gpt-5`, `o1`,
`o3`, `o4`) reject at the upstream API. xAI Grok and non-reasoning OpenAI
models (gpt-4o, gpt-4-turbo) pass through unchanged and do not emit this
signal.

### Model Events

<!-- GENERATED:START region=model inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `model.available` | `model_id` | Publish aggregate routing availability for a model ID at Stargate scope. |
| `model.capacity.freed` | `url`, `model_id` | Create model.capacity.freed event (wake-only, no slot release). |
| `model.capacity.overflow.assigned` | `request_id`, `model_id`, `from_gateway`, `to_gateway`, `depth_before` | Admission moved to overflow gateway |
| `model.execution.completed` | `url`, `model_id`, `request_id`, `gateway_id` | Create model.execution.completed event (request-scoped slot release). |
| `model.execution.failed` | `url`, `model_id`, `request_id`, `gateway_id`, `error` | Create model.execution.failed event (request-scoped slot release). |
| `model.execution.started` | `url`, `model_id` | Create model.execution.started event. |
| `model.load.blocked` | `model_id`, `reason`, `required_vram_mb`, `available_vram_mb`, `required_ram_mb`, `available_ram_mb`, `bypassed_margin` | Create MODEL_LOAD_BLOCKED event (Recommendation #7: Observability). |
| `model.load.completed` | `model_id`, `gateway_url`, `gateway_name`, `timestamp`, `success`, `load_time_ms`, `error`, `request_id?` | `correlation_id` |
| `model.load.context.mismatch` | `model_id`, `requested_context`, `actual_context`, `reason` | Create MODEL_LOAD_CONTEXT_MISMATCH event. |
| `model.load.failed` | `model_id`, `error_message`, `failure_reason`, `worker_snapshot?` | Create MODEL_LOAD_FAILED event. |
| `model.load.failed` | `url`, `model_id`, `error`, `gateway_name`, `gateway_state_snapshot`, `worker_snapshot` | Create MODEL_LOAD_FAILED event. |
| `model.load.initiated` | `model_id`, `gateway_url`, `gateway_name`, `timestamp`, `already_loaded`, `request_id?` | `correlation_id` |
| `model.load.overflow.started` | `request_id`, `model_id`, `gateway_id`, `reason` | Overflow gateway cold-load initiated |
| `model.loaded` | `model_id`, `vram_usage_mb`, `ram_usage_mb`, `process_pid` | `ram_mb`, `vram_mb` |
| `model.loaded` | `gateway_id`, `model_id` | `ram_mb`, `vram_mb` |
| `model.loaded` | `url`, `model_id`, `gateway_name`, `vram_mb`, `ram_mb` | `ram_mb`, `vram_mb` |
| `model.loading.progress` | `model_id`, `phase`, `pct` | Create MODEL_LOADING_PROGRESS heartbeat event. |
| `model.loading.progress` | `url`, `model_id`, `phase`, `pct`, `gateway_name` | Create MODEL_LOADING_PROGRESS heartbeat event. |
| `model.loading.started` | `model_id` | `role=coordination`, `scope=global` — bridged from gateway WebSocket telemetry; opens cold-load window for batch coordinators |
| `model.loading.started` | `url`, `model_id` | `role=coordination`, `scope=global` — bridged from gateway WebSocket telemetry; opens cold-load window for batch coordinators |
| `model.loading.stuck` | `url`, `model_id`, `elapsed_s`, `ttl_s` | Signal that model load exceeded stuck TTL; reservation cleared. |
| `model.selection.health.observation` | `task`, `model_id`, `outcome`, `latency_ms`, `quality_score?`, `tokens_per_second?` | `quality_score`, `tokens_per_second` |
| `model.selection.rank.computed` | `task`, `candidates`, `selection_path` | `avoid_models` |
| `model.selection.score.updated` | `task`, `model_id`, `final_score`, `components` | — |
| `model.selection.switch.allowed` | `task`, `sticky_key`, `previous_model_id`, `new_model_id`, `delta` | — |
| `model.selection.switch.suppressed` | `task`, `sticky_key`, `current_model_id`, `contender_model_id`, `delta`, `reason` | — |
| `model.unavailable` | `model_id` | Publish aggregate routing loss for a model ID at Stargate scope. |
| `model.unloaded` | `model_id` | `reason` |
| `model.unloaded` | `gateway_id`, `model_id` | `reason` |
| `model.unloaded` | `url`, `model_id`, `gateway_name` | `reason` |
| `model.unloading.started` | `model_id` | Create MODEL_UNLOADING_STARTED event. |
<!-- GENERATED:END region=model -->

**Note**: Execution-capacity signals (`model.execution.*` and
`model.capacity.freed`) are documented under **Capacity & Slot Lifecycle**.

### Federation Events

<!-- GENERATED:START region=federation inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `federation.activation.filtered.empty` | `gateway_id`, `available_count`, `activated_count` | gateway has available models but activated_models is explicitly empty — all hidden from /v1/models |
| `federation.catalog.changed` | `gateway_id`, `old_model_count`, `new_model_count`, `event_type`, `models` | Create FEDERATION_GATEWAY_CATALOG_CHANGED event. |
| `federation.catalog.vram.drift` | `gateway_id`, `model_id`, `measured_mb`, `catalog_mb`, `drift_pct` | — |
| `federation.circuit.breaker.rejected` | `gateway_id`, `model_id`, `reason` | request rejected (gateway_wide_open, model_circuit_open, half_open_limit_reached) |
| `federation.connection.authenticated` | `remote_id`, `method` | Remote Stargate authenticated with Master. |
| `federation.connection.established` | `remote_id`, `transport` | `latency_ms` |
| `federation.connection.lost` | `remote_id`, `reason` | Remote Stargate disconnected from Master. |
| `federation.edge.container.exited` | `node_id`, `relay_stargate_id`, `error_type`, `socket_path` | Relay detected local edge container UDS link failure. |
| `federation.gateway.degraded` | `gateway_id`, `consecutive_timeouts`, `first_error_code` | Gateway crossed timeout threshold (REQUEST_TIMEOUT/INFERENCE_TIMEOUT/LOAD_TIMEOUT). Coordination signal only — routing is NOT excluded. Cleared by `federation.gateway.recovered` with `kind=degradation`. role=coordination. |
| `federation.gateway.liveness.stale` | `gateway_id`, `heartbeat_age_ms`, `threshold_ms`, `last_heartbeat_iso`, `backend_type` | Passive heartbeat staleness alert — no routing side effects. |
| `federation.gateway.reachability.restored` | `gateway_id`, `offline_duration_ms`, `model_count` | Create FEDERATION_GATEWAY_REACHABILITY_RESTORED event. |
| `federation.gateway.recovered` | `gateway_id`, `kind`, `reason`, `downtime_ms?` | Previously DEGRADED or UNHEALTHY gateway recovered. `kind` ∈ {`degradation`, `reachability`}; `reason` ∈ {`first_success`, `probe_succeeded`}. role=coordination. |
| `federation.gateway.removed` | `gateway_id`, `remote_id` | Record remote disconnect teardown after gateway removal from manager state. |
| `federation.gateway.unhealthy` | `gateway_id`, `consecutive_disconnects`, `first_error_code`, `cooldown_s` | Gateway crossed disconnect threshold (GATEWAY_DISCONNECTED/EDGE_UNREACHABLE). Routing excludes for `cooldown_s`. Cleared by `federation.gateway.recovered` with `kind=reachability`. role=coordination. |
| `federation.link.timeout` | `link_role`, `peer_id`, `close_code`, `close_reason`, `cause` | Native WS keepalive ping missed pong (`websockets` 1011 + reason contains `keepalive ping timeout`). `link_role` ∈ `remote_to_master`, `master_to_edge`. `cause` = `keepalive_ping`. role=observation, scope=node. |
| `federation.load.confirmed` | `request_id`, `remote_id`, `model_id`, `duration_ms` | `correlation_id` |
| `federation.load.failed` | `request_id`, `remote_id`, `model_id`, `error` | `correlation_id` |
| `federation.load.requested` | `request_id`, `target_remote`, `model_id` | `correlation_id` |
| `federation.model.lifecycle` | `gateway_id`, `msg_type`, `model_id` | master applied federated model lifecycle telemetry |
| `federation.orchestrator.decided` | `request_id`, `decision_type`, `target`, `reason`, `alternatives_considered` | `correlation_id`, `alternatives_considered` |
| `federation.orchestrator.evicted` | `target_remote`, `model_id`, `reason` | Orchestrator evicted a model from a remote. |
| `federation.peer.auth.failed` | `peer_id`, `reason` | edge peer auth failed (unknown_peer, invalid_api_key) |
| `federation.peer.disconnected` | `peer_id`, `remaining_peers` | authenticated peer disconnected from edge |
| `federation.request.inference.forwarded` | `request_id`, `peer_count` | edge forwarded request.inference.started to peers |
| `federation.resource.updated` | `gateway_id`, `vram_free_mb`, `ram_free_mb` | master applied RESOURCE_UPDATE from edge |
| `federation.routing.delegated` | `request_id`, `target_remote`, `model_id` | `correlation_id`, `reason` |
| `federation.routing.rejected` | `request_id`, `model_id`, `reason` | `correlation_id` |
| `federation.routing.routed.local` | `request_id`, `model_id`, `reason` | `correlation_id` |
| `federation.snapshot.sent` | `gateway_id`, `all_models_count`, `available_models_count`, `gap_count`, `trigger` | `trigger`: `"initial"` (wiring) or `"periodic"` (reconciliation timer) |
| `federation.telemetry.applied` | `remote_id`, `changes` | Telemetry applied to Master state. |
| `federation.telemetry.marked.stale` | `remote_id`, `age_seconds`, `threshold_seconds` | Telemetry from Remote exceeded staleness threshold. |
| `federation.telemetry.received` | `remote_id`, `model_count`, `resource_summary` | `resource_summary`, `telemetry_age_ms`, `msg_type`, `catalog_model_count`, `loaded_model_count`, `count_source` |
| `federation.telemetry.wired` | `gateway_url`, `gateway_id` | edge finished wiring local gateway telemetry |
| `federation.vram.request.failed` | `request_id`, `reason` | Record VRAM probe dispatch failure with explicit operational reason. |
| `federation.vram.request.sent` | `request_id`, `peer_id`, `device_index` | Track outbound VRAM probe dispatch to peer/device pairing for correlation. |
| `federation.vram.response.received` | `request_id`, `matched` | Capture VRAM probe response correlation success or orphaned-response mismatch. |
<!-- GENERATED:END region=federation -->

**`federation.telemetry.received` disambiguation fields**: `model_count` is the
backward-compatible count scoped by message type. The optional fields clarify its
source: `msg_type` (`GATEWAY_SNAPSHOT` or `RESOURCE_UPDATE`), `count_source`
(`authoritative_available_models` or `authoritative_loaded_models` — projected
from `FederatedGatewayManager` state after ingestion, not from inbound wire
payloads), `catalog_model_count` (set on snapshots), and `loaded_model_count`
(set on resource updates — derived from `gw.loaded_models`).

**`federation.activation.filtered.empty`**: Diagnostic signal emitted during
`GATEWAY_SNAPSHOT` application when a gateway reports `available_models > 0` but
`activated_models` is an explicitly empty set. Under strict activation semantics
this means all models are hidden from `/v1/models` for that gateway.

### Gateway Events

<!-- GENERATED:START region=gateway inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `gateway.draining` | `gateway_id`, `reason`, `timeout`, `timestamp` | Create GATEWAY_DRAINING event. |
| `gateway.model.ghost.cleaned` | `model_id`, `success`, `vram_freed_mb` | `vram_freed_mb` |
| `gateway.model.phantom.cleaned` | `model_id`, `success`, `vram_freed_mb` | `vram_freed_mb` |
| `gateway.model.phantom.detected` | `model_id`, `process_status`, `tracker_status` | `tracker_status` |
| `gateway.resource.updated` | `gateway_id`, `source` | Create GATEWAY_RESOURCE_UPDATE wake-up signal for federation. |
| `gateway.resource.updated` | `url`, `total_vram_mb`, `available_vram_mb`, `total_ram_mb`, `available_ram_mb`, `loaded_models`, `busy_models` | Signal an update to a gateway's resource info (VRAM/RAM/models). |
| `gateway.retry.attempted` | `gateway_url`, `method`, `path`, `attempt`, `max_retries`, `error_type`, `error_message`, `backoff_delay_ms` | Signal that a gateway request retry was attempted. |
| `gateway.shutdown` | `gateway_id`, `reason`, `timestamp` | Create GATEWAY_SHUTDOWN event. |
| `gateway.snapshot.resource.gap` | `all_models_count`, `resource_models_count`, `gap_count`, `gap_cause`, `sample_missing?` | `sample_missing` |
| `gateway.state.changed` | `url`, `connectivity`, `health`, `previous_connectivity`, `previous_health`, `transition_type`, `check_duration_ms` | `previous_connectivity`, `previous_health` |
| `gateway.vram.orphan.detected` | `hardware_used_mb`, `catalog_used_mb`, `discrepancy_mb`, `tracked_models` | positive discrepancy (hardware > tracked) |
| `gateway.vram.staleness.detected` | `hardware_used_mb`, `catalog_used_mb`, `discrepancy_mb`, `tracked_models` | negative discrepancy (tracked > hardware) |
<!-- GENERATED:END region=gateway -->

### Guidance Locality Events

<!-- GENERATED:START region=guidance inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `guidance.delivery.deduped` | `execution_id`, `guidance_resource_key`, `trigger_fan_in_count`, `dedup_scope?`, `request_id?`, `dispatch_id?`, `registry_schema_version?`, `producer_version?` | Emitted when the resolver collapses overlapping triggers into one bundle. |
| `guidance.delivery.recorded` | `execution_id`, `guidance_resource_key`, `projection_surface`, `delivered_tokens`, `fetch_scope`, `token_category`, `content_digest?`, `delivered_bytes?`, `is_duplicate?`, `dedup_scope?`, `request_id?`, `dispatch_id?`, `registry_schema_version?`, `producer_version?` | Emitted when a guidance artifact is delivered to a seat. |
| `guidance.restatement.detected` | `execution_id`, `guidance_resource_key`, `restated_overlap_tokens`, `request_id?`, `dispatch_id?`, `registry_schema_version?`, `producer_version?` | Emitted when restated-guidance overlap is found at closeout. |
| `guidance.workflow.summarized` | `execution_id`, `workflow_class`, `phase`, `token_vector`, `campaign_id?`, `seat_substrate?`, `request_id?`, `dispatch_id?`, `registry_schema_version?`, `producer_version?` | Emitted when a per-workflow rollup occurs at workflow/session close. |
<!-- GENERATED:END region=guidance -->

### RAG Events

<!-- GENERATED:START region=rag inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `rag.admission.first.burst.observed` | `model_id`, `workers_in_flight`, `stargate_queue_depth` | First OPEN→CLOSED cold-load transition. `workers_in_flight`: count of `wait_for_admission()` calls that allowed a worker through (returned True or timed out to proceed) since the gate was last OPEN or since startup. `stargate_queue_depth`: value from `GET /api/v1/admission/state` at transition time; `null` if Stargate unreachable. |
| `rag.admission.io.failed` | `operation`, `model_id`, `error` | Emitted by `services/rag/admission_gate/_io.py` when an HTTP call to Stargate fails during snapshot startup or burst queue-depth fetch. `operation`: `"snapshot"` or `"burst_fetch"`. `model_id`: routing key of the queried model. `error`: exception string. |
| `rag.article.auto.created` | `source_path`, `content_hash`, `scope` | Indexing created a skeletal article row for a source that had no article record |
| `rag.article.content.hash.mismatch` | `file`, `expected_hash`, `actual_hash` | source bytes diverged from article registry hash |
| `rag.article.content.hash.mismatch` | `file`, `expected_hash`, `actual_hash` | source bytes diverged from article registry hash |
| `rag.article.path.moved` | `old_path`, `new_path`, `content_hash` | Indexing detected a file move by content hash and migrated the SQLite article row and/or Chroma chunk metadata to the new source path |
| `rag.article.registry.failed` | `path`, `error` | article registry load failed at startup |
| `rag.article.registry.loaded` | `path`, `article_count` | article registry successfully loaded at startup |
| `rag.article.registry.write.failed` | `path`, `filename`, `error` | writing entry to article registry failed during ingest |
| `rag.article.upserted` | `source_path`, `created`, `title`, `content_hash`, `pipeline_stage`, `queue_state`, `queue_depth`, `frontier_status` | article metadata upsert completed; `created=true` for insert, `created=false` for update; `pipeline_stage` ∈ {`registered`, `queued`, `chunked`, `contextualized`}; `queue_state` is precise extraction_queue state when `pipeline_stage == "queued"` (values: `ready`, `in_flight`, `cooling_off`, `capacity_blocked`, `exhausted`), else `null`; `queue_depth` is global extraction_queue count; `frontier_status` ∈ {`reachable`, `unreachable`, `unknown`} |
| `rag.chroma.upsert.completed` | `file`, `operation_id`, `chunk_count`, `operation?`, `batch_index?`, `batch_total?` | Emitted after chunk rows are persisted to ChromaDB. |
| `rag.chroma.upsert.started` | `file`, `operation_id`, `chunk_count`, `operation?`, `batch_index?`, `batch_total?` | Emitted immediately before chunk rows are upserted into ChromaDB. |
| `rag.chunk.contextualization.completed` | `file`, `chunk_index`, `model`, `request_id`, `duration_seconds`, `output_chars`, `operation_id?`, `operation?` | Per-chunk: contextualization request returned a non-empty context prefix. Optional: `operation_id`, `operation`. |
| `rag.chunk.contextualization.failed` | `file`, `chunk_index`, `model`, `error`, `request_id?`, `duration_seconds?`, `operation_id?`, `operation?` | Per-chunk: contextualization LLM call failed or was tail-abandoned for this chunk position. `error` is `repr(exc)[:200]` or `ContextualizationTailAbandoned(...)`. Optional: `request_id`, `duration_seconds`, `operation_id`, `operation`. |
| `rag.chunk.contextualization.started` | `file`, `chunk_index`, `model`, `request_id`, `timeout_s`, `operation_id?`, `operation?` | Per-chunk: contextualization request submitted to Stargate. `request_id` is propagated as `X-Internal-Request-ID` for request-trace correlation. Optional: `operation_id`, `operation`. |
| `rag.chunk.noise.tagged` | `chunk_id`, `source`, `noise_reason` | per-chunk: heuristic tagged chunk as noise at index time. `noise_reason` ∈ {`citation_block`, `dense_table`, `garbled_extraction`, `boilerplate`, `legacy_bibliography`, `unspecified_noise`} |
| `rag.contextualization.applied` | `file`, `chunk_count`, `model` | contextual prefixes were applied before embedding |
| `rag.contextualization.completed` | `file`, `chunk_count`, `successful`, `failed`, `duration_seconds`, `model`, `max_concurrency`, _dynamic_ | all contextualization requests settled for this file before embedding; optional: `operation_id`, `operation` |
| `rag.contextualization.exception.record.failed` | `file`, `model`, `error`, `operation_id?`, `operation?` | RAG attempted to persist degraded contextualization diagnostics but the property index write failed. Indexing continues. Optional: `operation_id`, `operation`. |
| `rag.contextualization.exception.recorded` | `file`, `exception_id`, `total_chunks`, `cache_miss_chunks`, `successful_chunks`, `failed_chunks`, `abandoned_chunks`, `model`, `first_failure`, `operation_id?`, `operation?` | Durable diagnostic row was stored in `contextualization_exceptions` for a successful-but-degraded contextualization attempt. Optional: `operation_id`, `operation`. |
| `rag.contextualization.partial` | `file`, `total_chunks`, `failed_chunks`, `successful_chunks`, `model`, `first_failure`, `operation_id?`, `operation?` | Contextualization completed with `failed_chunks > 0`; file still indexed (failed chunks embedded prefix-free). Optional: `operation_id`, `operation`. |
| `rag.contextualization.started` | `file`, `chunk_count`, `model`, `max_concurrency`, _dynamic_ | contextualization dispatch started for this file before embedding; optional: `operation_id`, `operation` |
| `rag.contextualization.tail.abandoned` | `file`, `total_chunks`, `completed_chunks`, `abandoned_chunks`, `successful_chunks`, `failed_chunks`, `model`, `idle_seconds`, `tail_idle_timeout_s`, `operation_id?`, `operation?` | RAG stopped waiting for straggler contextualization chunks after enough chunks had already succeeded and no further progress occurred for the tail-idle budget. This is an exception path: file still indexes, abandoned chunks remain cache misses. Optional: `operation_id`, `operation`. |
| `rag.contextualize.cache.evaluated` | `file`, `total_chunks`, `cache_hits`, `cache_misses`, `contextualize_model`, `operation_id?`, `operation?` | per-file cache plan summary; `cache_hits + cache_misses == total_chunks`; optional: `operation_id`, `operation` |
| `rag.contextualize.cache.gc.completed` | `deleted_rows` | startup orphan sweep succeeded |
| `rag.contextualize.cache.gc.failed` | `error` | startup orphan sweep failed non-fatally — readiness not blocked |
| `rag.contextualize.cache.lookup.failed` | `file`, `requested_chunks`, `contextualize_model`, `error`, `operation_id?`, `operation?` | cache lookup degraded to full recompute (indexing continues); optional: `operation_id`, `operation` |
| `rag.contextualize.cache.store.completed` | `file`, `stored`, `requested`, `contextualize_model`, `operation_id?`, `operation?` | cache rows persisted after successful upsert + source commit; optional: `operation_id`, `operation` |
| `rag.contextualize.cache.store.failed` | `file`, `requested`, `contextualize_model`, `error`, `operation_id?`, `operation?` | index succeeded but cache persistence failed (best-effort); optional: `operation_id`, `operation` |
| `rag.corpus.hints.filter.failed` | `error` | Emitted when co-occurrence hint filtering fails. |
| `rag.corpus.hints.load.failed` | `path`, `error` | Emitted when corpus_hints.yaml cannot be loaded. |
| `rag.corpus.hints.skipped` | `reason` | Emitted when corpus-hints generation is intentionally skipped. |
| `rag.corpus.hints.update.failed` | `path`, `error` | Emitted when corpus_hints.yaml update fails after indexing. |
| `rag.corpus.hints.updated` | `path`, `scopes_updated`, `timestamp` | Emitted after corpus_hints.yaml is written following aggregation from the property index. |
| `rag.dependencies.activated` | `dependencies` | emitted when Stargate readiness, embedding readiness, and extraction runtime startup have succeeded, before optional watcher registration begins. |
| `rag.dependency.retry.scheduled` | `waiting_on`, `attempt`, `delay_seconds`, `error` | emitted once per retry while Stargate-backed activation is still blocked |
| `rag.directory.cleared` | `path`, `sources_cleared`, `chunks_cleared` | Emitted after all chunks for sources under a directory are deleted. |
| `rag.directory.index.completed` | `path`, `total_files`, `indexed`, `deleted`, `unchanged`, `duplicates`, `errors` | Emitted after all files in a directory index/reindex have been processed. |
| `rag.directory.index.started` | `path`, `total_files` | Emitted before concurrent directory indexing dispatch begins. |
| `rag.directory.sources.deleted` | `path`, `sources_deleted`, `chunks_deleted`, `articles_deleted` | directory-level delete completed across vector index and article metadata |
| `rag.embed.completed` | `file`, `operation_id`, `chunk_count`, _dynamic_ | Emitted after chunk embeddings return for indexing. |
| `rag.embed.diff.evaluated` | `file`, `operation_id`, `total_chunks`, `processed_chunks`, `skipped_chunks`, `legacy_id_count`, `operation?` | Emitted after embed diff-gate partition (aggregate skip/process counts). |
| `rag.embed.started` | `file`, `operation_id`, `chunk_count`, _dynamic_ | Emitted immediately before chunk embeddings are requested for indexing. |
| `rag.embedding.chunk.fallback` | `model`, `text_len`, `dim` | Emitted when a single-item embedding batch fails all retries and a zero vector is substituted. |
| `rag.embedding.query.failed` | `model_id`, `attempts`, `last_status`, `query_len`, `scope` | Emitted when query embedding retries are exhausted. |
| `rag.embedding.query.success` | `model_id`, `query_len`, `scope` | Emitted when a query embedding call succeeds. |
| `rag.embeddings.unavailable` | `error` | Emitted when the watcher is not started because the embedding endpoint is unhealthy. |
| `rag.entity.gate.io.failed` | `operation`, `error` | Emitted by `services/rag/entity_admission/_io.py` when cortex-api source-paths refresh or Event Service subscribe fails. `operation`: `"refresh"` or `"subscribe"`. `error`: exception string. Prior admitted set is retained (fail-safe). |
| `rag.exclusion.purged` | `files`, `chunks`, `sources?` | Indexed sources matching exclusion patterns purged during startup |
| `rag.extraction.admission.closed` | `pipeline_id`, `reason`, `active_reasons`, `signal` | Gate transitioned OPEN → CLOSED. `reason` ∈ {`iteration-timeout-burst`, `step-failure-ratio`, `gateway:<gateway_id>`, `model:<model_id>`}. `signal` is the upstream Stargate signal that drove the transition. |
| `rag.extraction.admission.opened` | `pipeline_id`, `cleared_reason`, `signal`, `closed_seconds` | Last active close-reason cleared; gate reopened. `closed_seconds` measures the wall-clock window between the matching `closed` and this `opened`. |
| `rag.extraction.admission.timeout` | `pipeline_id`, `waited_seconds`, `active_reasons` | The extraction worker's pre-dequeue wait timed out and the worker proceeded optimistically. Each occurrence is a tuning datum, not a failure. |
| `rag.extraction.batch.completed` | `file`, `chunk_count`, `successful`, `written`, `duration_seconds`, _dynamic_ | Batch extraction finished (successful ≤ chunk_count; written = 0 on partial failure). Optional payload: `extraction_model`, `finish_reason` (present when pipeline stop reason ≠ "stop", e.g. `"length"` = max_tokens truncation). |
| `rag.extraction.batch.skipped` | `file`, `chunk_count`, `skipped_count`, `max_attempts` | All chunks permanently failed — no pipeline call made |
| `rag.extraction.batch.started` | `file`, `chunk_count` | Batch extraction initiated for a file |
| `rag.extraction.batch.timed.out` | `file`, `chunk_count`, `timeout_seconds`, `duration_seconds` | Extraction batch exceeded dynamic timeout budget; all chunks recorded as transient failures |
| `rag.extraction.claim.recovered` | `source`, `claimed_at`, `claimed_age_seconds` | RAG startup cleared a claim left by a previous process before starting the worker |
| `rag.extraction.completed` | `chunk_id`, `entities`, `topics` | - |
| `rag.extraction.failed` | `chunk_id`, `error`, _dynamic_ | Per-chunk extraction failure (expected iteration result missing or invalid after batch parsing) |
| `rag.extraction.infrastructure.degraded` | `model_id`, `consecutive_timeouts` | Emitted when the extraction model tracker enters DEGRADED state. |
| `rag.extraction.infrastructure.recovered` | `model_id` | Emitted when the extraction model tracker exits DEGRADED state. |
| `rag.extraction.model.mismatch` | `file`, `expected_model`, `chunk_count` | Re-extraction triggered because existing chunks have different or missing extraction_model. |
| `rag.extraction.permanently.skipped` | `chunk_id`, `source`, `attempt_count` | Chunk crossed `max_extraction_attempts`; permanently abandoned. Persisted as `permanent=1` in `failed_extractions`. Emitted exactly once per chunk. |
| `rag.extraction.queue.woken` | `pipeline_id`, `reset_count` | Emit when extraction model availability wakes cooling-off queue items. |
| `rag.extraction.recovery.completed` | `file`, `entities`, `topics` | recovery pass for missing extraction metadata completed successfully |
| `rag.extraction.recovery.failed` | `file`, `reason` | recovery attempted but extraction metadata could not be committed |
| `rag.extraction.recovery.skipped` | `file`, `reason` | recovery skipped (e.g. no documents in ChromaDB, all chunks permanently failed) |
| `rag.extraction.source.claimed` | `source`, `attempts`, `queued_at`, `claimed_at` | source row atomically claimed from `extraction_queue`; row remains in-flight until completion, failure, or startup claim recovery |
| `rag.extraction.source.completed` | `source`, `duration_seconds` | source extraction completed and the queue row was deleted |
| `rag.extraction.source.failed` | `source`, `failure_category`, `error_type`, `increment_attempt` | source extraction failed and the row remains queued for backoff or exhaustion; `increment_attempt=false` means capacity-class failure did not consume source defect budget |
| `rag.extraction.structurally.unavailable` | `model_id`, `reason`, `detail` | Extraction model ID has no Stargate catalog entry; failures are marked permanent (no retry loop). |
| `rag.extraction.unavailable` | `pipeline`, `error` | Extraction pipeline not routable via Stargate at watcher start. Watcher is not started; RAG serves queries but does not index until restart. |
| `rag.file.deleted` | `file`, `deleted`, _dynamic_ | all chunks deleted, no replacement (file now empty); optional: `operation_id`, `operation` |
| `rag.file.deletion.failed` | `file`, `error` | watcher-triggered delete cleanup failed; indexed rows may still exist |
| `rag.file.indexed` | `file`, `deleted`, `indexed`, `duration_seconds`, _dynamic_ | file fully indexed; `duration_seconds` = wall-clock time to index this file; optional: `batch_start_ts` (ISO-8601), `processing_seconds` (Stargate-derived post-queue work time), `queue_wait_seconds` (time from pipeline step start to first inference started), `document_metadata` (dict — e.g. `article_title`, `article_authors`, `article_venue`, `published_date`, `article_doi` when file is in registry), `noise_chunks` (int — count of chunks tagged `is_noise` / legacy `is_bibliography` for this file), `operation_id` (per-attempt correlation handle), `operation` (`index`/`reindex` when route-originated) |
| `rag.file.indexing.failed` | `file`, `error`, `model?`, `operation_id?`, `operation?` | terminal indexing failure from unhandled exception. ¬emitted for retriable extraction failures (see `rag.file.retry.deferred`). Optional: `operation_id`, `operation`. |
| `rag.file.indexing.failure.cleared` | `file`, `reason` | row removed from `indexing_failures`. `reason` ∈ {`indexed_successfully`, `source_deleted`, `operator_cleared`}. Emitted only when a row actually existed. role=coordination. |
| `rag.file.indexing.failure.recorded` | `file`, `failure_category`, `failure_reason`, `attempt_count`, `error_type?`, `error_head?` | file-level failure persisted to `indexing_failures` table. `failure_category` ∈ {`permanent`, `transient`}. Optional: `error_type` (`type(exc).__qualname__` of the underlying exception), `error_head` (first ~200 chars of `str(exc)`) — both let `query-events --signal rag.file.indexing.failure.recorded` reveal the actual exception without consulting RAG logs. role=coordination. |
| `rag.file.indexing.failure.skipped` | `file`, `failure_reason`, `attempt_count` | reconcile/initial-reindex skipped the file because a permanent row exists with unchanged mtime/size, or a transient row is inside its backoff window. role=coordination. |
| `rag.file.indexing.gated` | `file`, `layer` | file in an entity-gated watch root skipped because no cortex entity backs it via `source_uri`. `layer` ∈ {`watcher_sweep`, `index_funnel`}. role=coordination. **Not** a failure row. Distinct from `rag.entity.gate.io.failed` (upstream refresh/subscribe outage). |
| `rag.file.indexing.retry.requested` | `file`, `scheduled` | operator requested a retry via admin API. `scheduled` reflects whether the watcher accepted the admission. role=coordination. |
| `rag.file.retry.deferred` | `file`, `reason`, _dynamic_ | extraction incomplete but file NOT marked indexed — watcher will re-attempt on next sweep. reasons: `extraction_incomplete`, `infrastructure_unavailable`. Optional: `operation_id`, `operation`. |
| `rag.file.skipped` | `file`, `reason`, _dynamic_ | file skipped; `reason` ∈ {`unchanged`, `duplicate_pdf`}; optional: `operation_id`, `operation` |
| `rag.hints.gaps.repaired` | `scopes`, `trigger` | Corpus hints were refreshed for scopes whose indexed file-set hash drifted. |
| `rag.hints.update.completed` | `file`, `operation_id`, _dynamic_ | Emitted after post-index corpus-hints refresh returns. |
| `rag.hints.update.started` | `file`, `operation_id`, _dynamic_ | Emitted before post-index corpus-hints refresh begins. |
| `rag.html.normalization.completed` | `file`, `output_chars` | HTML normalized to markdown successfully |
| `rag.html.normalization.failed` | `file`, `error` | HTML normalization failed; file indexing aborted |
| `rag.html.normalization.started` | `file` | HTML/HTM normalization started before chunking |
| `rag.indexing.failure.persist.failed` | `file`, `error` | Emitted when the attempt to persist an indexing failure record itself |
| `rag.orphan.purged` | `files`, `chunks`, `sources?` | Missing watched sources reconciled during startup; `chunks` counts only Chroma deletions |
| `rag.pending.reconciled` | `reconciled`, `cleared`, `failed_transient`, `failed_permanent` | Startup reconciliation of files interrupted mid-index |
| `rag.post.index.stale` | `stale_steps` | Emitted on startup when post-index enrichment steps are older than the last reindex. |
| `rag.property.index.rebuilt` | `collection`, `count` | - |
| `rag.property.index.unavailable` | `file` | indexing proceeded without property index availability |
| `rag.property.write.completed` | `file`, `operation_id`, `chunk_count`, `property_entries`, _dynamic_ | Emitted after SQLite-backed FTS and property metadata writes finish. |
| `rag.property.write.started` | `file`, `operation_id`, `chunk_count`, `property_entries`, _dynamic_ | Emitted before SQLite-backed FTS and property metadata writes begin. |
| `rag.scope.rejected` | `scope`, `reason`, `available` | - |
| `rag.scope.resolved` | `scope`, `prefix_count` | - |
| `rag.scope.vocabulary.load.failed` | `path`, `error` | Emitted when scope_vocabulary.yaml cannot be loaded. |
| `rag.scopes.listed` | `count` | - |
| `rag.search.embedding.failed` | `model_id`, `attempts`, `last_status`, `query_len`, `scope` | Emitted when embed_query retries are exhausted during a search request. |
| `rag.search.executed` | `query_len`, `top_k`, `results`, `scope` | Emitted after a search query completes. |
| `rag.search.no.results` | `query_len`, `scope` | Emitted when a search returns zero results. |
| `rag.search.tier.applied` | `tier_hits`, `scope` | Emitted when tier_weight is applied to a search request and at least one chunk matched. |
| `rag.shutdown` | - | Emit shutdown start for the RAG service process. |
| `rag.source.commit.completed` | `file`, `operation_id`, `chunk_count`, `stale_chunks`, _dynamic_ | Emitted after final source-level metadata commit and stale cleanup finish. |
| `rag.source.commit.started` | `file`, `operation_id`, `chunk_count`, `stale_chunks`, _dynamic_ | Emitted before final source-level metadata commit and stale cleanup begin. |
| `rag.source.deleted` | `source`, `chunks_deleted`, `article_deleted` | source-level delete completed across vector index and article metadata |
| `rag.start.degraded` | `waiting_on`, `error` | first transition from core boot into dependency-waiting mode |
| `rag.started` | - | emitted after core boot (event bus, config, property index, registry load attempt) completes |
| `rag.vocabulary.classification.failed` | `scopes`, `model`, `trigger`, `reasons` | LLM classification failed for one or more scopes; `reasons` is a `{scope: reason_string}` map; `trigger` is the repair trigger source |
| `rag.vocabulary.gaps.detected` | `scopes`, `reason` | vocabulary could not be filled for one or more scopes; `reason` ∈ {`no_model_available`, `no_terms`, `non_latin_terms`} |
| `rag.vocabulary.gaps.repaired` | `scopes`, `model` | vocabulary rows successfully written after LLM classification; `model` is the local Stargate model ID used |
| `rag.watch.directory.missing` | `path` | Emit startup warning when a configured watch directory is missing. |
| `rag.watch.file.deleted` | `file`, `deleted` | watcher deleted all chunks for a source file removed from disk |
| `rag.watch.initial.complete` | `path`, `files`, `reindexed`, `unchanged`, `errors` | emitted once per watch path at end of startup sweep; invariant: total_files == reindexed + unchanged + errors; `files` includes errored files |
| `rag.watch.initial.progress` | `path`, `total_files`, `processed`, `reindexed`, `unchanged`, `errors` | emitted approximately every 10% of total_files during startup sweep; `processed` is monotonic; invariant: `processed = reindexed + unchanged + errors` |
| `rag.watch.initial.started` | `path`, `total_files` | emitted once per watch path when startup sweep candidate list is finalized |
| `rag.watch.reconcile.complete` | `path`, `recovered`, `unchanged` | Emitted after a reconciliation sweep indexes files absent from the store. |
| `rag.watch.reconcile.failed` | `error` | Emitted when a reconciliation loop iteration fails unexpectedly. |
| `rag.watch.reconcile.repair.failed` | `error`, `roots` | Emitted when post-reconcile repair fails after a successful sweep. |
| `rag.watch.reindex.complete` | `file`, `deleted`, `indexed`, `unchanged` | Emit per-file reindex outcome from watcher or startup sweep. |
| `rag.watch.started` | `path`, `extensions`, `recursive` | Emit watcher activation for a configured watch directory. |
| `rag.watch.stopped` | `watchers` | Emit watcher shutdown with the count of stopped observers. |
| `rag.watchers.registered` | `count`, `paths` | Emitted when all inotify watchers are registered (fast path, before initial reindex). |
<!-- GENERATED:END region=rag -->

Note: `rag.contextualization.started` / `.completed` `chunk_count` now reports
**cache misses only** (actual LLM work), not total chunks. Use
`rag.contextualize.cache.evaluated.total_chunks` for the full file total and
`.cache_hits` for the reuse count.

### RAG Admission Gate Burst Measurement

**Role**: `observation`. Emitted by `services/rag/admission_gate/_state_changes.py`
(schedules the burst task) and `services/rag/admission_gate/_io.py`
(`_emit_first_burst_observed`) exactly once per model per process lifetime, the
first time the gate closes due to a cold-load window (`model.loading.started`).
Used to quantify the first-batch burst: how many contextualize workers submitted
requests to Stargate before `model.loading.started` arrived and closed the gate.
See `todo:rag-admission-gate-first-burst-measurement` and Worst-Case Cold-Load
Timing in `tmp/prompts/coordination-overhaul/phase4.md`.

<!-- GENERATED:START region=rag inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `rag.admission.first.burst.observed` | `model_id`, `workers_in_flight`, `stargate_queue_depth` | First OPEN→CLOSED cold-load transition. `workers_in_flight`: count of `wait_for_admission()` calls that allowed a worker through (returned True or timed out to proceed) since the gate was last OPEN or since startup. `stargate_queue_depth`: value from `GET /api/v1/admission/state` at transition time; `null` if Stargate unreachable. |
| `rag.admission.io.failed` | `operation`, `model_id`, `error` | Emitted by `services/rag/admission_gate/_io.py` when an HTTP call to Stargate fails during snapshot startup or burst queue-depth fetch. `operation`: `"snapshot"` or `"burst_fetch"`. `model_id`: routing key of the queried model. `error`: exception string. |
| `rag.article.auto.created` | `source_path`, `content_hash`, `scope` | Indexing created a skeletal article row for a source that had no article record |
| `rag.article.content.hash.mismatch` | `file`, `expected_hash`, `actual_hash` | source bytes diverged from article registry hash |
| `rag.article.content.hash.mismatch` | `file`, `expected_hash`, `actual_hash` | source bytes diverged from article registry hash |
| `rag.article.path.moved` | `old_path`, `new_path`, `content_hash` | Indexing detected a file move by content hash and migrated the SQLite article row and/or Chroma chunk metadata to the new source path |
| `rag.article.registry.failed` | `path`, `error` | article registry load failed at startup |
| `rag.article.registry.loaded` | `path`, `article_count` | article registry successfully loaded at startup |
| `rag.article.registry.write.failed` | `path`, `filename`, `error` | writing entry to article registry failed during ingest |
| `rag.article.upserted` | `source_path`, `created`, `title`, `content_hash`, `pipeline_stage`, `queue_state`, `queue_depth`, `frontier_status` | article metadata upsert completed; `created=true` for insert, `created=false` for update; `pipeline_stage` ∈ {`registered`, `queued`, `chunked`, `contextualized`}; `queue_state` is precise extraction_queue state when `pipeline_stage == "queued"` (values: `ready`, `in_flight`, `cooling_off`, `capacity_blocked`, `exhausted`), else `null`; `queue_depth` is global extraction_queue count; `frontier_status` ∈ {`reachable`, `unreachable`, `unknown`} |
| `rag.chroma.upsert.completed` | `file`, `operation_id`, `chunk_count`, `operation?`, `batch_index?`, `batch_total?` | Emitted after chunk rows are persisted to ChromaDB. |
| `rag.chroma.upsert.started` | `file`, `operation_id`, `chunk_count`, `operation?`, `batch_index?`, `batch_total?` | Emitted immediately before chunk rows are upserted into ChromaDB. |
| `rag.chunk.contextualization.completed` | `file`, `chunk_index`, `model`, `request_id`, `duration_seconds`, `output_chars`, `operation_id?`, `operation?` | Per-chunk: contextualization request returned a non-empty context prefix. Optional: `operation_id`, `operation`. |
| `rag.chunk.contextualization.failed` | `file`, `chunk_index`, `model`, `error`, `request_id?`, `duration_seconds?`, `operation_id?`, `operation?` | Per-chunk: contextualization LLM call failed or was tail-abandoned for this chunk position. `error` is `repr(exc)[:200]` or `ContextualizationTailAbandoned(...)`. Optional: `request_id`, `duration_seconds`, `operation_id`, `operation`. |
| `rag.chunk.contextualization.started` | `file`, `chunk_index`, `model`, `request_id`, `timeout_s`, `operation_id?`, `operation?` | Per-chunk: contextualization request submitted to Stargate. `request_id` is propagated as `X-Internal-Request-ID` for request-trace correlation. Optional: `operation_id`, `operation`. |
| `rag.chunk.noise.tagged` | `chunk_id`, `source`, `noise_reason` | per-chunk: heuristic tagged chunk as noise at index time. `noise_reason` ∈ {`citation_block`, `dense_table`, `garbled_extraction`, `boilerplate`, `legacy_bibliography`, `unspecified_noise`} |
| `rag.contextualization.applied` | `file`, `chunk_count`, `model` | contextual prefixes were applied before embedding |
| `rag.contextualization.completed` | `file`, `chunk_count`, `successful`, `failed`, `duration_seconds`, `model`, `max_concurrency`, _dynamic_ | all contextualization requests settled for this file before embedding; optional: `operation_id`, `operation` |
| `rag.contextualization.exception.record.failed` | `file`, `model`, `error`, `operation_id?`, `operation?` | RAG attempted to persist degraded contextualization diagnostics but the property index write failed. Indexing continues. Optional: `operation_id`, `operation`. |
| `rag.contextualization.exception.recorded` | `file`, `exception_id`, `total_chunks`, `cache_miss_chunks`, `successful_chunks`, `failed_chunks`, `abandoned_chunks`, `model`, `first_failure`, `operation_id?`, `operation?` | Durable diagnostic row was stored in `contextualization_exceptions` for a successful-but-degraded contextualization attempt. Optional: `operation_id`, `operation`. |
| `rag.contextualization.partial` | `file`, `total_chunks`, `failed_chunks`, `successful_chunks`, `model`, `first_failure`, `operation_id?`, `operation?` | Contextualization completed with `failed_chunks > 0`; file still indexed (failed chunks embedded prefix-free). Optional: `operation_id`, `operation`. |
| `rag.contextualization.started` | `file`, `chunk_count`, `model`, `max_concurrency`, _dynamic_ | contextualization dispatch started for this file before embedding; optional: `operation_id`, `operation` |
| `rag.contextualization.tail.abandoned` | `file`, `total_chunks`, `completed_chunks`, `abandoned_chunks`, `successful_chunks`, `failed_chunks`, `model`, `idle_seconds`, `tail_idle_timeout_s`, `operation_id?`, `operation?` | RAG stopped waiting for straggler contextualization chunks after enough chunks had already succeeded and no further progress occurred for the tail-idle budget. This is an exception path: file still indexes, abandoned chunks remain cache misses. Optional: `operation_id`, `operation`. |
| `rag.contextualize.cache.evaluated` | `file`, `total_chunks`, `cache_hits`, `cache_misses`, `contextualize_model`, `operation_id?`, `operation?` | per-file cache plan summary; `cache_hits + cache_misses == total_chunks`; optional: `operation_id`, `operation` |
| `rag.contextualize.cache.gc.completed` | `deleted_rows` | startup orphan sweep succeeded |
| `rag.contextualize.cache.gc.failed` | `error` | startup orphan sweep failed non-fatally — readiness not blocked |
| `rag.contextualize.cache.lookup.failed` | `file`, `requested_chunks`, `contextualize_model`, `error`, `operation_id?`, `operation?` | cache lookup degraded to full recompute (indexing continues); optional: `operation_id`, `operation` |
| `rag.contextualize.cache.store.completed` | `file`, `stored`, `requested`, `contextualize_model`, `operation_id?`, `operation?` | cache rows persisted after successful upsert + source commit; optional: `operation_id`, `operation` |
| `rag.contextualize.cache.store.failed` | `file`, `requested`, `contextualize_model`, `error`, `operation_id?`, `operation?` | index succeeded but cache persistence failed (best-effort); optional: `operation_id`, `operation` |
| `rag.corpus.hints.filter.failed` | `error` | Emitted when co-occurrence hint filtering fails. |
| `rag.corpus.hints.load.failed` | `path`, `error` | Emitted when corpus_hints.yaml cannot be loaded. |
| `rag.corpus.hints.skipped` | `reason` | Emitted when corpus-hints generation is intentionally skipped. |
| `rag.corpus.hints.update.failed` | `path`, `error` | Emitted when corpus_hints.yaml update fails after indexing. |
| `rag.corpus.hints.updated` | `path`, `scopes_updated`, `timestamp` | Emitted after corpus_hints.yaml is written following aggregation from the property index. |
| `rag.dependencies.activated` | `dependencies` | emitted when Stargate readiness, embedding readiness, and extraction runtime startup have succeeded, before optional watcher registration begins. |
| `rag.dependency.retry.scheduled` | `waiting_on`, `attempt`, `delay_seconds`, `error` | emitted once per retry while Stargate-backed activation is still blocked |
| `rag.directory.cleared` | `path`, `sources_cleared`, `chunks_cleared` | Emitted after all chunks for sources under a directory are deleted. |
| `rag.directory.index.completed` | `path`, `total_files`, `indexed`, `deleted`, `unchanged`, `duplicates`, `errors` | Emitted after all files in a directory index/reindex have been processed. |
| `rag.directory.index.started` | `path`, `total_files` | Emitted before concurrent directory indexing dispatch begins. |
| `rag.directory.sources.deleted` | `path`, `sources_deleted`, `chunks_deleted`, `articles_deleted` | directory-level delete completed across vector index and article metadata |
| `rag.embed.completed` | `file`, `operation_id`, `chunk_count`, _dynamic_ | Emitted after chunk embeddings return for indexing. |
| `rag.embed.diff.evaluated` | `file`, `operation_id`, `total_chunks`, `processed_chunks`, `skipped_chunks`, `legacy_id_count`, `operation?` | Emitted after embed diff-gate partition (aggregate skip/process counts). |
| `rag.embed.started` | `file`, `operation_id`, `chunk_count`, _dynamic_ | Emitted immediately before chunk embeddings are requested for indexing. |
| `rag.embedding.chunk.fallback` | `model`, `text_len`, `dim` | Emitted when a single-item embedding batch fails all retries and a zero vector is substituted. |
| `rag.embedding.query.failed` | `model_id`, `attempts`, `last_status`, `query_len`, `scope` | Emitted when query embedding retries are exhausted. |
| `rag.embedding.query.success` | `model_id`, `query_len`, `scope` | Emitted when a query embedding call succeeds. |
| `rag.embeddings.unavailable` | `error` | Emitted when the watcher is not started because the embedding endpoint is unhealthy. |
| `rag.entity.gate.io.failed` | `operation`, `error` | Emitted by `services/rag/entity_admission/_io.py` when cortex-api source-paths refresh or Event Service subscribe fails. `operation`: `"refresh"` or `"subscribe"`. `error`: exception string. Prior admitted set is retained (fail-safe). |
| `rag.exclusion.purged` | `files`, `chunks`, `sources?` | Indexed sources matching exclusion patterns purged during startup |
| `rag.extraction.admission.closed` | `pipeline_id`, `reason`, `active_reasons`, `signal` | Gate transitioned OPEN → CLOSED. `reason` ∈ {`iteration-timeout-burst`, `step-failure-ratio`, `gateway:<gateway_id>`, `model:<model_id>`}. `signal` is the upstream Stargate signal that drove the transition. |
| `rag.extraction.admission.opened` | `pipeline_id`, `cleared_reason`, `signal`, `closed_seconds` | Last active close-reason cleared; gate reopened. `closed_seconds` measures the wall-clock window between the matching `closed` and this `opened`. |
| `rag.extraction.admission.timeout` | `pipeline_id`, `waited_seconds`, `active_reasons` | The extraction worker's pre-dequeue wait timed out and the worker proceeded optimistically. Each occurrence is a tuning datum, not a failure. |
| `rag.extraction.batch.completed` | `file`, `chunk_count`, `successful`, `written`, `duration_seconds`, _dynamic_ | Batch extraction finished (successful ≤ chunk_count; written = 0 on partial failure). Optional payload: `extraction_model`, `finish_reason` (present when pipeline stop reason ≠ "stop", e.g. `"length"` = max_tokens truncation). |
| `rag.extraction.batch.skipped` | `file`, `chunk_count`, `skipped_count`, `max_attempts` | All chunks permanently failed — no pipeline call made |
| `rag.extraction.batch.started` | `file`, `chunk_count` | Batch extraction initiated for a file |
| `rag.extraction.batch.timed.out` | `file`, `chunk_count`, `timeout_seconds`, `duration_seconds` | Extraction batch exceeded dynamic timeout budget; all chunks recorded as transient failures |
| `rag.extraction.claim.recovered` | `source`, `claimed_at`, `claimed_age_seconds` | RAG startup cleared a claim left by a previous process before starting the worker |
| `rag.extraction.completed` | `chunk_id`, `entities`, `topics` | - |
| `rag.extraction.failed` | `chunk_id`, `error`, _dynamic_ | Per-chunk extraction failure (expected iteration result missing or invalid after batch parsing) |
| `rag.extraction.infrastructure.degraded` | `model_id`, `consecutive_timeouts` | Emitted when the extraction model tracker enters DEGRADED state. |
| `rag.extraction.infrastructure.recovered` | `model_id` | Emitted when the extraction model tracker exits DEGRADED state. |
| `rag.extraction.model.mismatch` | `file`, `expected_model`, `chunk_count` | Re-extraction triggered because existing chunks have different or missing extraction_model. |
| `rag.extraction.permanently.skipped` | `chunk_id`, `source`, `attempt_count` | Chunk crossed `max_extraction_attempts`; permanently abandoned. Persisted as `permanent=1` in `failed_extractions`. Emitted exactly once per chunk. |
| `rag.extraction.queue.woken` | `pipeline_id`, `reset_count` | Emit when extraction model availability wakes cooling-off queue items. |
| `rag.extraction.recovery.completed` | `file`, `entities`, `topics` | recovery pass for missing extraction metadata completed successfully |
| `rag.extraction.recovery.failed` | `file`, `reason` | recovery attempted but extraction metadata could not be committed |
| `rag.extraction.recovery.skipped` | `file`, `reason` | recovery skipped (e.g. no documents in ChromaDB, all chunks permanently failed) |
| `rag.extraction.source.claimed` | `source`, `attempts`, `queued_at`, `claimed_at` | source row atomically claimed from `extraction_queue`; row remains in-flight until completion, failure, or startup claim recovery |
| `rag.extraction.source.completed` | `source`, `duration_seconds` | source extraction completed and the queue row was deleted |
| `rag.extraction.source.failed` | `source`, `failure_category`, `error_type`, `increment_attempt` | source extraction failed and the row remains queued for backoff or exhaustion; `increment_attempt=false` means capacity-class failure did not consume source defect budget |
| `rag.extraction.structurally.unavailable` | `model_id`, `reason`, `detail` | Extraction model ID has no Stargate catalog entry; failures are marked permanent (no retry loop). |
| `rag.extraction.unavailable` | `pipeline`, `error` | Extraction pipeline not routable via Stargate at watcher start. Watcher is not started; RAG serves queries but does not index until restart. |
| `rag.file.deleted` | `file`, `deleted`, _dynamic_ | all chunks deleted, no replacement (file now empty); optional: `operation_id`, `operation` |
| `rag.file.deletion.failed` | `file`, `error` | watcher-triggered delete cleanup failed; indexed rows may still exist |
| `rag.file.indexed` | `file`, `deleted`, `indexed`, `duration_seconds`, _dynamic_ | file fully indexed; `duration_seconds` = wall-clock time to index this file; optional: `batch_start_ts` (ISO-8601), `processing_seconds` (Stargate-derived post-queue work time), `queue_wait_seconds` (time from pipeline step start to first inference started), `document_metadata` (dict — e.g. `article_title`, `article_authors`, `article_venue`, `published_date`, `article_doi` when file is in registry), `noise_chunks` (int — count of chunks tagged `is_noise` / legacy `is_bibliography` for this file), `operation_id` (per-attempt correlation handle), `operation` (`index`/`reindex` when route-originated) |
| `rag.file.indexing.failed` | `file`, `error`, `model?`, `operation_id?`, `operation?` | terminal indexing failure from unhandled exception. ¬emitted for retriable extraction failures (see `rag.file.retry.deferred`). Optional: `operation_id`, `operation`. |
| `rag.file.indexing.failure.cleared` | `file`, `reason` | row removed from `indexing_failures`. `reason` ∈ {`indexed_successfully`, `source_deleted`, `operator_cleared`}. Emitted only when a row actually existed. role=coordination. |
| `rag.file.indexing.failure.recorded` | `file`, `failure_category`, `failure_reason`, `attempt_count`, `error_type?`, `error_head?` | file-level failure persisted to `indexing_failures` table. `failure_category` ∈ {`permanent`, `transient`}. Optional: `error_type` (`type(exc).__qualname__` of the underlying exception), `error_head` (first ~200 chars of `str(exc)`) — both let `query-events --signal rag.file.indexing.failure.recorded` reveal the actual exception without consulting RAG logs. role=coordination. |
| `rag.file.indexing.failure.skipped` | `file`, `failure_reason`, `attempt_count` | reconcile/initial-reindex skipped the file because a permanent row exists with unchanged mtime/size, or a transient row is inside its backoff window. role=coordination. |
| `rag.file.indexing.gated` | `file`, `layer` | file in an entity-gated watch root skipped because no cortex entity backs it via `source_uri`. `layer` ∈ {`watcher_sweep`, `index_funnel`}. role=coordination. **Not** a failure row. Distinct from `rag.entity.gate.io.failed` (upstream refresh/subscribe outage). |
| `rag.file.indexing.retry.requested` | `file`, `scheduled` | operator requested a retry via admin API. `scheduled` reflects whether the watcher accepted the admission. role=coordination. |
| `rag.file.retry.deferred` | `file`, `reason`, _dynamic_ | extraction incomplete but file NOT marked indexed — watcher will re-attempt on next sweep. reasons: `extraction_incomplete`, `infrastructure_unavailable`. Optional: `operation_id`, `operation`. |
| `rag.file.skipped` | `file`, `reason`, _dynamic_ | file skipped; `reason` ∈ {`unchanged`, `duplicate_pdf`}; optional: `operation_id`, `operation` |
| `rag.hints.gaps.repaired` | `scopes`, `trigger` | Corpus hints were refreshed for scopes whose indexed file-set hash drifted. |
| `rag.hints.update.completed` | `file`, `operation_id`, _dynamic_ | Emitted after post-index corpus-hints refresh returns. |
| `rag.hints.update.started` | `file`, `operation_id`, _dynamic_ | Emitted before post-index corpus-hints refresh begins. |
| `rag.html.normalization.completed` | `file`, `output_chars` | HTML normalized to markdown successfully |
| `rag.html.normalization.failed` | `file`, `error` | HTML normalization failed; file indexing aborted |
| `rag.html.normalization.started` | `file` | HTML/HTM normalization started before chunking |
| `rag.indexing.failure.persist.failed` | `file`, `error` | Emitted when the attempt to persist an indexing failure record itself |
| `rag.orphan.purged` | `files`, `chunks`, `sources?` | Missing watched sources reconciled during startup; `chunks` counts only Chroma deletions |
| `rag.pending.reconciled` | `reconciled`, `cleared`, `failed_transient`, `failed_permanent` | Startup reconciliation of files interrupted mid-index |
| `rag.post.index.stale` | `stale_steps` | Emitted on startup when post-index enrichment steps are older than the last reindex. |
| `rag.property.index.rebuilt` | `collection`, `count` | - |
| `rag.property.index.unavailable` | `file` | indexing proceeded without property index availability |
| `rag.property.write.completed` | `file`, `operation_id`, `chunk_count`, `property_entries`, _dynamic_ | Emitted after SQLite-backed FTS and property metadata writes finish. |
| `rag.property.write.started` | `file`, `operation_id`, `chunk_count`, `property_entries`, _dynamic_ | Emitted before SQLite-backed FTS and property metadata writes begin. |
| `rag.scope.rejected` | `scope`, `reason`, `available` | - |
| `rag.scope.resolved` | `scope`, `prefix_count` | - |
| `rag.scope.vocabulary.load.failed` | `path`, `error` | Emitted when scope_vocabulary.yaml cannot be loaded. |
| `rag.scopes.listed` | `count` | - |
| `rag.search.embedding.failed` | `model_id`, `attempts`, `last_status`, `query_len`, `scope` | Emitted when embed_query retries are exhausted during a search request. |
| `rag.search.executed` | `query_len`, `top_k`, `results`, `scope` | Emitted after a search query completes. |
| `rag.search.no.results` | `query_len`, `scope` | Emitted when a search returns zero results. |
| `rag.search.tier.applied` | `tier_hits`, `scope` | Emitted when tier_weight is applied to a search request and at least one chunk matched. |
| `rag.shutdown` | - | Emit shutdown start for the RAG service process. |
| `rag.source.commit.completed` | `file`, `operation_id`, `chunk_count`, `stale_chunks`, _dynamic_ | Emitted after final source-level metadata commit and stale cleanup finish. |
| `rag.source.commit.started` | `file`, `operation_id`, `chunk_count`, `stale_chunks`, _dynamic_ | Emitted before final source-level metadata commit and stale cleanup begin. |
| `rag.source.deleted` | `source`, `chunks_deleted`, `article_deleted` | source-level delete completed across vector index and article metadata |
| `rag.start.degraded` | `waiting_on`, `error` | first transition from core boot into dependency-waiting mode |
| `rag.started` | - | emitted after core boot (event bus, config, property index, registry load attempt) completes |
| `rag.vocabulary.classification.failed` | `scopes`, `model`, `trigger`, `reasons` | LLM classification failed for one or more scopes; `reasons` is a `{scope: reason_string}` map; `trigger` is the repair trigger source |
| `rag.vocabulary.gaps.detected` | `scopes`, `reason` | vocabulary could not be filled for one or more scopes; `reason` ∈ {`no_model_available`, `no_terms`, `non_latin_terms`} |
| `rag.vocabulary.gaps.repaired` | `scopes`, `model` | vocabulary rows successfully written after LLM classification; `model` is the local Stargate model ID used |
| `rag.watch.directory.missing` | `path` | Emit startup warning when a configured watch directory is missing. |
| `rag.watch.file.deleted` | `file`, `deleted` | watcher deleted all chunks for a source file removed from disk |
| `rag.watch.initial.complete` | `path`, `files`, `reindexed`, `unchanged`, `errors` | emitted once per watch path at end of startup sweep; invariant: total_files == reindexed + unchanged + errors; `files` includes errored files |
| `rag.watch.initial.progress` | `path`, `total_files`, `processed`, `reindexed`, `unchanged`, `errors` | emitted approximately every 10% of total_files during startup sweep; `processed` is monotonic; invariant: `processed = reindexed + unchanged + errors` |
| `rag.watch.initial.started` | `path`, `total_files` | emitted once per watch path when startup sweep candidate list is finalized |
| `rag.watch.reconcile.complete` | `path`, `recovered`, `unchanged` | Emitted after a reconciliation sweep indexes files absent from the store. |
| `rag.watch.reconcile.failed` | `error` | Emitted when a reconciliation loop iteration fails unexpectedly. |
| `rag.watch.reconcile.repair.failed` | `error`, `roots` | Emitted when post-reconcile repair fails after a successful sweep. |
| `rag.watch.reindex.complete` | `file`, `deleted`, `indexed`, `unchanged` | Emit per-file reindex outcome from watcher or startup sweep. |
| `rag.watch.started` | `path`, `extensions`, `recursive` | Emit watcher activation for a configured watch directory. |
| `rag.watch.stopped` | `watchers` | Emit watcher shutdown with the count of stopped observers. |
| `rag.watchers.registered` | `count`, `paths` | Emitted when all inotify watchers are registered (fast path, before initial reindex). |
<!-- GENERATED:END region=rag -->

**Closure criteria** (from todo): if P95 `stargate_queue_depth` across ≥ 2 weeks
of production indexing is below `max_queue_depth`, close
`todo:rag-admission-gate-first-burst-measurement` as `done` with the data
attached. If P95 ≥ `max_queue_depth`, escalate
`todo:rag-admission-gate-startup-snapshot` to `high` priority and consider a
singleflight hold proposal.

| `rag.embed.started` | `file`, `operation_id`, `chunk_count` | emitted immediately before chunk embeddings are requested; optional: `operation` |
| `rag.embed.completed` | `file`, `operation_id`, `chunk_count` | emitted after chunk embeddings return; optional: `operation` |
| `rag.chroma.upsert.started` | `file`, `operation_id`, `chunk_count` | emitted immediately before each ChromaDB upsert sub-batch begins; optional: `operation`, `batch_index`, `batch_total` (present when a file is split across multiple backend batches) |
| `rag.chroma.upsert.completed` | `file`, `operation_id`, `chunk_count` | emitted after each ChromaDB upsert sub-batch returns; optional: `operation`, `batch_index`, `batch_total` |
| `rag.property.write.started` | `file`, `operation_id`, `chunk_count`, `property_entries` | emitted before FTS + property-index writes begin; optional: `operation` |
| `rag.property.write.completed` | `file`, `operation_id`, `chunk_count`, `property_entries` | emitted after FTS + property-index writes finish; optional: `operation` |
| `rag.source.commit.started` | `file`, `operation_id`, `chunk_count`, `stale_chunks` | emitted before stale cleanup and source-level metadata commit begin; optional: `operation` |
| `rag.source.commit.completed` | `file`, `operation_id`, `chunk_count`, `stale_chunks` | emitted after stale cleanup and source-level metadata commit finish; optional: `operation` |
| `rag.hints.update.started` | `file`, `operation_id` | emitted before post-index corpus-hints refresh begins; optional: `operation` |
| `rag.hints.update.completed` | `file`, `operation_id` | emitted after post-index corpus-hints refresh returns; optional: `operation` |
| `rag.embedding.chunk.fallback` | `model`, `text_len`, `dim` | chunk embedded as zero vector after all retry attempts exhausted; indicates content-specific model fault; chunk is indexed but not semantically retrievable |
| `rag.html.normalization.started` | `file` | HTML ingest entered normalization pipeline (before chunking) |
| `rag.html.normalization.completed` | `file`, `output_chars` | HTML normalization succeeded; output_chars = total chunk text length |
| `rag.html.normalization.failed` | `file`, `error` | HTML normalization failed; file indexing aborted for this file |
| `rag.directory.index.started` | `path`, `total_files` | emitted once before concurrent directory index/reindex dispatch; total_files = count of files to process |
| `rag.directory.index.completed` | `path`, `total_files`, `indexed`, `deleted`, `unchanged`, `duplicates`, `errors` | emitted after all files in a directory index/reindex have been processed; absence after `rag.directory.index.started` indicates interrupted session |
| `rag.directory.cleared` | `path`, `sources_cleared`, `chunks_cleared` | emitted after clear_directory (and force reindex pre-clear) removes directory-backed chunks |
| `rag.scope.resolved` | `scope`, `prefix_count` | scope(s) resolved to prefixes; `scope`: str or array of strings |
| `rag.scope.rejected` | `scope`, `reason`, `available` | scope validation failed |
| `rag.scopes.listed` | `count` | scope registry listing completed |
| `rag.post.index.stale` | `stale_steps` | startup: serving-critical post-index enrichment steps stale after last reindex; automatic repair runs in background, operator should run runbook if it does not clear |
| `rag.hints.gaps.repaired` | `scopes`, `trigger` | automatic corpus-hints refresh for scopes whose indexed file-set hash drifted; `trigger` ∈ {`startup`, `reconcile`, `watcher`} |
| `rag.vocabulary.gaps.detected` | `scopes`, `reason` | vocabulary auto-fill skipped; `reason` ∈ `no_model_available` (no gateway-owned Stargate model loaded), `no_terms` (scope has zero IDF-scored corpus hint rows — content not yet indexed) |
| `rag.vocabulary.gaps.repaired` | `scopes`, `model` | scope vocabulary rows written after LLM classification during automatic gap repair |
| `rag.vocabulary.classification.failed` | `scopes`, `model`, `trigger`, `reasons` | LLM vocabulary classification failed during automatic gap repair; `reasons` maps each scope to failure class |
| `rag.search.embedding.failed` | `model_id`, `attempts`, `last_status`, `query_len`, `scope` | search embedding retries exhausted; request degraded/fails before vector search |
| `rag.embedding.query.success` | `model_id`, `query_len`, `scope` | query embedding completed successfully |
| `rag.embedding.query.failed` | `model_id`, `attempts`, `last_status`, `query_len`, `scope` | query embedding retries exhausted before search |
| `rag.search.executed` | `query_len`, `top_k`, `results`, `scope` | search completed with ≥1 result; `scope`: str \| list[str] \| None |
| `rag.search.no.results` | `query_len`, `scope` | search completed with 0 results; `scope`: str \| list[str] \| None |
| `rag.search.tier.applied` | `tier_hits`, `scope` | emitted when `tier_weight` is present in the search request and ≥1 chunk had a matching `provenance_tier` tag; `tier_hits` = count of distance-adjusted chunks |
| `rag.corpus.hints.updated` | `path`, `scopes_updated`, `timestamp` | corpus_hints.yaml written after aggregation from property index |
| `rag.corpus.hints.update.failed` | `path`, `error` | corpus_hints.yaml update failed after indexing |
| `rag.corpus.hints.load.failed` | `path`, `error` | corpus_hints.yaml could not be loaded |
| `rag.scope.vocabulary.load.failed` | `path`, `error` | scope_vocabulary.yaml could not be loaded |
| `rag.corpus.hints.filter.failed` | `error` | co-occurrence hint filtering failed |
| `rag.corpus.hints.skipped` | `reason` | corpus-hints generation skipped intentionally |

### RAG Article Metadata Lifecycle

<!-- GENERATED:START region=rag inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `rag.admission.first.burst.observed` | `model_id`, `workers_in_flight`, `stargate_queue_depth` | First OPEN→CLOSED cold-load transition. `workers_in_flight`: count of `wait_for_admission()` calls that allowed a worker through (returned True or timed out to proceed) since the gate was last OPEN or since startup. `stargate_queue_depth`: value from `GET /api/v1/admission/state` at transition time; `null` if Stargate unreachable. |
| `rag.admission.io.failed` | `operation`, `model_id`, `error` | Emitted by `services/rag/admission_gate/_io.py` when an HTTP call to Stargate fails during snapshot startup or burst queue-depth fetch. `operation`: `"snapshot"` or `"burst_fetch"`. `model_id`: routing key of the queried model. `error`: exception string. |
| `rag.article.auto.created` | `source_path`, `content_hash`, `scope` | Indexing created a skeletal article row for a source that had no article record |
| `rag.article.content.hash.mismatch` | `file`, `expected_hash`, `actual_hash` | source bytes diverged from article registry hash |
| `rag.article.content.hash.mismatch` | `file`, `expected_hash`, `actual_hash` | source bytes diverged from article registry hash |
| `rag.article.path.moved` | `old_path`, `new_path`, `content_hash` | Indexing detected a file move by content hash and migrated the SQLite article row and/or Chroma chunk metadata to the new source path |
| `rag.article.registry.failed` | `path`, `error` | article registry load failed at startup |
| `rag.article.registry.loaded` | `path`, `article_count` | article registry successfully loaded at startup |
| `rag.article.registry.write.failed` | `path`, `filename`, `error` | writing entry to article registry failed during ingest |
| `rag.article.upserted` | `source_path`, `created`, `title`, `content_hash`, `pipeline_stage`, `queue_state`, `queue_depth`, `frontier_status` | article metadata upsert completed; `created=true` for insert, `created=false` for update; `pipeline_stage` ∈ {`registered`, `queued`, `chunked`, `contextualized`}; `queue_state` is precise extraction_queue state when `pipeline_stage == "queued"` (values: `ready`, `in_flight`, `cooling_off`, `capacity_blocked`, `exhausted`), else `null`; `queue_depth` is global extraction_queue count; `frontier_status` ∈ {`reachable`, `unreachable`, `unknown`} |
| `rag.chroma.upsert.completed` | `file`, `operation_id`, `chunk_count`, `operation?`, `batch_index?`, `batch_total?` | Emitted after chunk rows are persisted to ChromaDB. |
| `rag.chroma.upsert.started` | `file`, `operation_id`, `chunk_count`, `operation?`, `batch_index?`, `batch_total?` | Emitted immediately before chunk rows are upserted into ChromaDB. |
| `rag.chunk.contextualization.completed` | `file`, `chunk_index`, `model`, `request_id`, `duration_seconds`, `output_chars`, `operation_id?`, `operation?` | Per-chunk: contextualization request returned a non-empty context prefix. Optional: `operation_id`, `operation`. |
| `rag.chunk.contextualization.failed` | `file`, `chunk_index`, `model`, `error`, `request_id?`, `duration_seconds?`, `operation_id?`, `operation?` | Per-chunk: contextualization LLM call failed or was tail-abandoned for this chunk position. `error` is `repr(exc)[:200]` or `ContextualizationTailAbandoned(...)`. Optional: `request_id`, `duration_seconds`, `operation_id`, `operation`. |
| `rag.chunk.contextualization.started` | `file`, `chunk_index`, `model`, `request_id`, `timeout_s`, `operation_id?`, `operation?` | Per-chunk: contextualization request submitted to Stargate. `request_id` is propagated as `X-Internal-Request-ID` for request-trace correlation. Optional: `operation_id`, `operation`. |
| `rag.chunk.noise.tagged` | `chunk_id`, `source`, `noise_reason` | per-chunk: heuristic tagged chunk as noise at index time. `noise_reason` ∈ {`citation_block`, `dense_table`, `garbled_extraction`, `boilerplate`, `legacy_bibliography`, `unspecified_noise`} |
| `rag.contextualization.applied` | `file`, `chunk_count`, `model` | contextual prefixes were applied before embedding |
| `rag.contextualization.completed` | `file`, `chunk_count`, `successful`, `failed`, `duration_seconds`, `model`, `max_concurrency`, _dynamic_ | all contextualization requests settled for this file before embedding; optional: `operation_id`, `operation` |
| `rag.contextualization.exception.record.failed` | `file`, `model`, `error`, `operation_id?`, `operation?` | RAG attempted to persist degraded contextualization diagnostics but the property index write failed. Indexing continues. Optional: `operation_id`, `operation`. |
| `rag.contextualization.exception.recorded` | `file`, `exception_id`, `total_chunks`, `cache_miss_chunks`, `successful_chunks`, `failed_chunks`, `abandoned_chunks`, `model`, `first_failure`, `operation_id?`, `operation?` | Durable diagnostic row was stored in `contextualization_exceptions` for a successful-but-degraded contextualization attempt. Optional: `operation_id`, `operation`. |
| `rag.contextualization.partial` | `file`, `total_chunks`, `failed_chunks`, `successful_chunks`, `model`, `first_failure`, `operation_id?`, `operation?` | Contextualization completed with `failed_chunks > 0`; file still indexed (failed chunks embedded prefix-free). Optional: `operation_id`, `operation`. |
| `rag.contextualization.started` | `file`, `chunk_count`, `model`, `max_concurrency`, _dynamic_ | contextualization dispatch started for this file before embedding; optional: `operation_id`, `operation` |
| `rag.contextualization.tail.abandoned` | `file`, `total_chunks`, `completed_chunks`, `abandoned_chunks`, `successful_chunks`, `failed_chunks`, `model`, `idle_seconds`, `tail_idle_timeout_s`, `operation_id?`, `operation?` | RAG stopped waiting for straggler contextualization chunks after enough chunks had already succeeded and no further progress occurred for the tail-idle budget. This is an exception path: file still indexes, abandoned chunks remain cache misses. Optional: `operation_id`, `operation`. |
| `rag.contextualize.cache.evaluated` | `file`, `total_chunks`, `cache_hits`, `cache_misses`, `contextualize_model`, `operation_id?`, `operation?` | per-file cache plan summary; `cache_hits + cache_misses == total_chunks`; optional: `operation_id`, `operation` |
| `rag.contextualize.cache.gc.completed` | `deleted_rows` | startup orphan sweep succeeded |
| `rag.contextualize.cache.gc.failed` | `error` | startup orphan sweep failed non-fatally — readiness not blocked |
| `rag.contextualize.cache.lookup.failed` | `file`, `requested_chunks`, `contextualize_model`, `error`, `operation_id?`, `operation?` | cache lookup degraded to full recompute (indexing continues); optional: `operation_id`, `operation` |
| `rag.contextualize.cache.store.completed` | `file`, `stored`, `requested`, `contextualize_model`, `operation_id?`, `operation?` | cache rows persisted after successful upsert + source commit; optional: `operation_id`, `operation` |
| `rag.contextualize.cache.store.failed` | `file`, `requested`, `contextualize_model`, `error`, `operation_id?`, `operation?` | index succeeded but cache persistence failed (best-effort); optional: `operation_id`, `operation` |
| `rag.corpus.hints.filter.failed` | `error` | Emitted when co-occurrence hint filtering fails. |
| `rag.corpus.hints.load.failed` | `path`, `error` | Emitted when corpus_hints.yaml cannot be loaded. |
| `rag.corpus.hints.skipped` | `reason` | Emitted when corpus-hints generation is intentionally skipped. |
| `rag.corpus.hints.update.failed` | `path`, `error` | Emitted when corpus_hints.yaml update fails after indexing. |
| `rag.corpus.hints.updated` | `path`, `scopes_updated`, `timestamp` | Emitted after corpus_hints.yaml is written following aggregation from the property index. |
| `rag.dependencies.activated` | `dependencies` | emitted when Stargate readiness, embedding readiness, and extraction runtime startup have succeeded, before optional watcher registration begins. |
| `rag.dependency.retry.scheduled` | `waiting_on`, `attempt`, `delay_seconds`, `error` | emitted once per retry while Stargate-backed activation is still blocked |
| `rag.directory.cleared` | `path`, `sources_cleared`, `chunks_cleared` | Emitted after all chunks for sources under a directory are deleted. |
| `rag.directory.index.completed` | `path`, `total_files`, `indexed`, `deleted`, `unchanged`, `duplicates`, `errors` | Emitted after all files in a directory index/reindex have been processed. |
| `rag.directory.index.started` | `path`, `total_files` | Emitted before concurrent directory indexing dispatch begins. |
| `rag.directory.sources.deleted` | `path`, `sources_deleted`, `chunks_deleted`, `articles_deleted` | directory-level delete completed across vector index and article metadata |
| `rag.embed.completed` | `file`, `operation_id`, `chunk_count`, _dynamic_ | Emitted after chunk embeddings return for indexing. |
| `rag.embed.diff.evaluated` | `file`, `operation_id`, `total_chunks`, `processed_chunks`, `skipped_chunks`, `legacy_id_count`, `operation?` | Emitted after embed diff-gate partition (aggregate skip/process counts). |
| `rag.embed.started` | `file`, `operation_id`, `chunk_count`, _dynamic_ | Emitted immediately before chunk embeddings are requested for indexing. |
| `rag.embedding.chunk.fallback` | `model`, `text_len`, `dim` | Emitted when a single-item embedding batch fails all retries and a zero vector is substituted. |
| `rag.embedding.query.failed` | `model_id`, `attempts`, `last_status`, `query_len`, `scope` | Emitted when query embedding retries are exhausted. |
| `rag.embedding.query.success` | `model_id`, `query_len`, `scope` | Emitted when a query embedding call succeeds. |
| `rag.embeddings.unavailable` | `error` | Emitted when the watcher is not started because the embedding endpoint is unhealthy. |
| `rag.entity.gate.io.failed` | `operation`, `error` | Emitted by `services/rag/entity_admission/_io.py` when cortex-api source-paths refresh or Event Service subscribe fails. `operation`: `"refresh"` or `"subscribe"`. `error`: exception string. Prior admitted set is retained (fail-safe). |
| `rag.exclusion.purged` | `files`, `chunks`, `sources?` | Indexed sources matching exclusion patterns purged during startup |
| `rag.extraction.admission.closed` | `pipeline_id`, `reason`, `active_reasons`, `signal` | Gate transitioned OPEN → CLOSED. `reason` ∈ {`iteration-timeout-burst`, `step-failure-ratio`, `gateway:<gateway_id>`, `model:<model_id>`}. `signal` is the upstream Stargate signal that drove the transition. |
| `rag.extraction.admission.opened` | `pipeline_id`, `cleared_reason`, `signal`, `closed_seconds` | Last active close-reason cleared; gate reopened. `closed_seconds` measures the wall-clock window between the matching `closed` and this `opened`. |
| `rag.extraction.admission.timeout` | `pipeline_id`, `waited_seconds`, `active_reasons` | The extraction worker's pre-dequeue wait timed out and the worker proceeded optimistically. Each occurrence is a tuning datum, not a failure. |
| `rag.extraction.batch.completed` | `file`, `chunk_count`, `successful`, `written`, `duration_seconds`, _dynamic_ | Batch extraction finished (successful ≤ chunk_count; written = 0 on partial failure). Optional payload: `extraction_model`, `finish_reason` (present when pipeline stop reason ≠ "stop", e.g. `"length"` = max_tokens truncation). |
| `rag.extraction.batch.skipped` | `file`, `chunk_count`, `skipped_count`, `max_attempts` | All chunks permanently failed — no pipeline call made |
| `rag.extraction.batch.started` | `file`, `chunk_count` | Batch extraction initiated for a file |
| `rag.extraction.batch.timed.out` | `file`, `chunk_count`, `timeout_seconds`, `duration_seconds` | Extraction batch exceeded dynamic timeout budget; all chunks recorded as transient failures |
| `rag.extraction.claim.recovered` | `source`, `claimed_at`, `claimed_age_seconds` | RAG startup cleared a claim left by a previous process before starting the worker |
| `rag.extraction.completed` | `chunk_id`, `entities`, `topics` | - |
| `rag.extraction.failed` | `chunk_id`, `error`, _dynamic_ | Per-chunk extraction failure (expected iteration result missing or invalid after batch parsing) |
| `rag.extraction.infrastructure.degraded` | `model_id`, `consecutive_timeouts` | Emitted when the extraction model tracker enters DEGRADED state. |
| `rag.extraction.infrastructure.recovered` | `model_id` | Emitted when the extraction model tracker exits DEGRADED state. |
| `rag.extraction.model.mismatch` | `file`, `expected_model`, `chunk_count` | Re-extraction triggered because existing chunks have different or missing extraction_model. |
| `rag.extraction.permanently.skipped` | `chunk_id`, `source`, `attempt_count` | Chunk crossed `max_extraction_attempts`; permanently abandoned. Persisted as `permanent=1` in `failed_extractions`. Emitted exactly once per chunk. |
| `rag.extraction.queue.woken` | `pipeline_id`, `reset_count` | Emit when extraction model availability wakes cooling-off queue items. |
| `rag.extraction.recovery.completed` | `file`, `entities`, `topics` | recovery pass for missing extraction metadata completed successfully |
| `rag.extraction.recovery.failed` | `file`, `reason` | recovery attempted but extraction metadata could not be committed |
| `rag.extraction.recovery.skipped` | `file`, `reason` | recovery skipped (e.g. no documents in ChromaDB, all chunks permanently failed) |
| `rag.extraction.source.claimed` | `source`, `attempts`, `queued_at`, `claimed_at` | source row atomically claimed from `extraction_queue`; row remains in-flight until completion, failure, or startup claim recovery |
| `rag.extraction.source.completed` | `source`, `duration_seconds` | source extraction completed and the queue row was deleted |
| `rag.extraction.source.failed` | `source`, `failure_category`, `error_type`, `increment_attempt` | source extraction failed and the row remains queued for backoff or exhaustion; `increment_attempt=false` means capacity-class failure did not consume source defect budget |
| `rag.extraction.structurally.unavailable` | `model_id`, `reason`, `detail` | Extraction model ID has no Stargate catalog entry; failures are marked permanent (no retry loop). |
| `rag.extraction.unavailable` | `pipeline`, `error` | Extraction pipeline not routable via Stargate at watcher start. Watcher is not started; RAG serves queries but does not index until restart. |
| `rag.file.deleted` | `file`, `deleted`, _dynamic_ | all chunks deleted, no replacement (file now empty); optional: `operation_id`, `operation` |
| `rag.file.deletion.failed` | `file`, `error` | watcher-triggered delete cleanup failed; indexed rows may still exist |
| `rag.file.indexed` | `file`, `deleted`, `indexed`, `duration_seconds`, _dynamic_ | file fully indexed; `duration_seconds` = wall-clock time to index this file; optional: `batch_start_ts` (ISO-8601), `processing_seconds` (Stargate-derived post-queue work time), `queue_wait_seconds` (time from pipeline step start to first inference started), `document_metadata` (dict — e.g. `article_title`, `article_authors`, `article_venue`, `published_date`, `article_doi` when file is in registry), `noise_chunks` (int — count of chunks tagged `is_noise` / legacy `is_bibliography` for this file), `operation_id` (per-attempt correlation handle), `operation` (`index`/`reindex` when route-originated) |
| `rag.file.indexing.failed` | `file`, `error`, `model?`, `operation_id?`, `operation?` | terminal indexing failure from unhandled exception. ¬emitted for retriable extraction failures (see `rag.file.retry.deferred`). Optional: `operation_id`, `operation`. |
| `rag.file.indexing.failure.cleared` | `file`, `reason` | row removed from `indexing_failures`. `reason` ∈ {`indexed_successfully`, `source_deleted`, `operator_cleared`}. Emitted only when a row actually existed. role=coordination. |
| `rag.file.indexing.failure.recorded` | `file`, `failure_category`, `failure_reason`, `attempt_count`, `error_type?`, `error_head?` | file-level failure persisted to `indexing_failures` table. `failure_category` ∈ {`permanent`, `transient`}. Optional: `error_type` (`type(exc).__qualname__` of the underlying exception), `error_head` (first ~200 chars of `str(exc)`) — both let `query-events --signal rag.file.indexing.failure.recorded` reveal the actual exception without consulting RAG logs. role=coordination. |
| `rag.file.indexing.failure.skipped` | `file`, `failure_reason`, `attempt_count` | reconcile/initial-reindex skipped the file because a permanent row exists with unchanged mtime/size, or a transient row is inside its backoff window. role=coordination. |
| `rag.file.indexing.gated` | `file`, `layer` | file in an entity-gated watch root skipped because no cortex entity backs it via `source_uri`. `layer` ∈ {`watcher_sweep`, `index_funnel`}. role=coordination. **Not** a failure row. Distinct from `rag.entity.gate.io.failed` (upstream refresh/subscribe outage). |
| `rag.file.indexing.retry.requested` | `file`, `scheduled` | operator requested a retry via admin API. `scheduled` reflects whether the watcher accepted the admission. role=coordination. |
| `rag.file.retry.deferred` | `file`, `reason`, _dynamic_ | extraction incomplete but file NOT marked indexed — watcher will re-attempt on next sweep. reasons: `extraction_incomplete`, `infrastructure_unavailable`. Optional: `operation_id`, `operation`. |
| `rag.file.skipped` | `file`, `reason`, _dynamic_ | file skipped; `reason` ∈ {`unchanged`, `duplicate_pdf`}; optional: `operation_id`, `operation` |
| `rag.hints.gaps.repaired` | `scopes`, `trigger` | Corpus hints were refreshed for scopes whose indexed file-set hash drifted. |
| `rag.hints.update.completed` | `file`, `operation_id`, _dynamic_ | Emitted after post-index corpus-hints refresh returns. |
| `rag.hints.update.started` | `file`, `operation_id`, _dynamic_ | Emitted before post-index corpus-hints refresh begins. |
| `rag.html.normalization.completed` | `file`, `output_chars` | HTML normalized to markdown successfully |
| `rag.html.normalization.failed` | `file`, `error` | HTML normalization failed; file indexing aborted |
| `rag.html.normalization.started` | `file` | HTML/HTM normalization started before chunking |
| `rag.indexing.failure.persist.failed` | `file`, `error` | Emitted when the attempt to persist an indexing failure record itself |
| `rag.orphan.purged` | `files`, `chunks`, `sources?` | Missing watched sources reconciled during startup; `chunks` counts only Chroma deletions |
| `rag.pending.reconciled` | `reconciled`, `cleared`, `failed_transient`, `failed_permanent` | Startup reconciliation of files interrupted mid-index |
| `rag.post.index.stale` | `stale_steps` | Emitted on startup when post-index enrichment steps are older than the last reindex. |
| `rag.property.index.rebuilt` | `collection`, `count` | - |
| `rag.property.index.unavailable` | `file` | indexing proceeded without property index availability |
| `rag.property.write.completed` | `file`, `operation_id`, `chunk_count`, `property_entries`, _dynamic_ | Emitted after SQLite-backed FTS and property metadata writes finish. |
| `rag.property.write.started` | `file`, `operation_id`, `chunk_count`, `property_entries`, _dynamic_ | Emitted before SQLite-backed FTS and property metadata writes begin. |
| `rag.scope.rejected` | `scope`, `reason`, `available` | - |
| `rag.scope.resolved` | `scope`, `prefix_count` | - |
| `rag.scope.vocabulary.load.failed` | `path`, `error` | Emitted when scope_vocabulary.yaml cannot be loaded. |
| `rag.scopes.listed` | `count` | - |
| `rag.search.embedding.failed` | `model_id`, `attempts`, `last_status`, `query_len`, `scope` | Emitted when embed_query retries are exhausted during a search request. |
| `rag.search.executed` | `query_len`, `top_k`, `results`, `scope` | Emitted after a search query completes. |
| `rag.search.no.results` | `query_len`, `scope` | Emitted when a search returns zero results. |
| `rag.search.tier.applied` | `tier_hits`, `scope` | Emitted when tier_weight is applied to a search request and at least one chunk matched. |
| `rag.shutdown` | - | Emit shutdown start for the RAG service process. |
| `rag.source.commit.completed` | `file`, `operation_id`, `chunk_count`, `stale_chunks`, _dynamic_ | Emitted after final source-level metadata commit and stale cleanup finish. |
| `rag.source.commit.started` | `file`, `operation_id`, `chunk_count`, `stale_chunks`, _dynamic_ | Emitted before final source-level metadata commit and stale cleanup begin. |
| `rag.source.deleted` | `source`, `chunks_deleted`, `article_deleted` | source-level delete completed across vector index and article metadata |
| `rag.start.degraded` | `waiting_on`, `error` | first transition from core boot into dependency-waiting mode |
| `rag.started` | - | emitted after core boot (event bus, config, property index, registry load attempt) completes |
| `rag.vocabulary.classification.failed` | `scopes`, `model`, `trigger`, `reasons` | LLM classification failed for one or more scopes; `reasons` is a `{scope: reason_string}` map; `trigger` is the repair trigger source |
| `rag.vocabulary.gaps.detected` | `scopes`, `reason` | vocabulary could not be filled for one or more scopes; `reason` ∈ {`no_model_available`, `no_terms`, `non_latin_terms`} |
| `rag.vocabulary.gaps.repaired` | `scopes`, `model` | vocabulary rows successfully written after LLM classification; `model` is the local Stargate model ID used |
| `rag.watch.directory.missing` | `path` | Emit startup warning when a configured watch directory is missing. |
| `rag.watch.file.deleted` | `file`, `deleted` | watcher deleted all chunks for a source file removed from disk |
| `rag.watch.initial.complete` | `path`, `files`, `reindexed`, `unchanged`, `errors` | emitted once per watch path at end of startup sweep; invariant: total_files == reindexed + unchanged + errors; `files` includes errored files |
| `rag.watch.initial.progress` | `path`, `total_files`, `processed`, `reindexed`, `unchanged`, `errors` | emitted approximately every 10% of total_files during startup sweep; `processed` is monotonic; invariant: `processed = reindexed + unchanged + errors` |
| `rag.watch.initial.started` | `path`, `total_files` | emitted once per watch path when startup sweep candidate list is finalized |
| `rag.watch.reconcile.complete` | `path`, `recovered`, `unchanged` | Emitted after a reconciliation sweep indexes files absent from the store. |
| `rag.watch.reconcile.failed` | `error` | Emitted when a reconciliation loop iteration fails unexpectedly. |
| `rag.watch.reconcile.repair.failed` | `error`, `roots` | Emitted when post-reconcile repair fails after a successful sweep. |
| `rag.watch.reindex.complete` | `file`, `deleted`, `indexed`, `unchanged` | Emit per-file reindex outcome from watcher or startup sweep. |
| `rag.watch.started` | `path`, `extensions`, `recursive` | Emit watcher activation for a configured watch directory. |
| `rag.watch.stopped` | `watchers` | Emit watcher shutdown with the count of stopped observers. |
| `rag.watchers.registered` | `count`, `paths` | Emitted when all inotify watchers are registered (fast path, before initial reindex). |
<!-- GENERATED:END region=rag -->

### RAG Boot Fetch

Emitted by the MCP server boot path (`_boot_data_fetch._fetch_rag_pipeline_state`) when a per-endpoint or total fetch of RAG pipeline state fails during `cortex_brief`. Brief continues; the stanza is omitted or shows `unreachable` depending on which endpoint failed.

<!-- GENERATED:START region=mcp inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `mcp.adapter.request.shape` | `provider`, `model`, `mcp_version`, `tool_count`, `mcp_tool_count`, `has_tool_search` | Every MCP request — shape summary for v1/v2 migration tracking. |
| `mcp.adapter.search.seen` | `references_count`, `correlation_id?` | Response contained a tool_search_tool_result block. |
| `mcp.adapter.tool.seen` | `tool_name`, `server_name`, `correlation_id?` | Response contained an mcp_tool_use block (Anthropic-executed MCP tool). |
| `mcp.adapter.v2.configured` | `provider`, `server_name`, `always_loaded_count`, `deferred_count` | First request with mcp_v2=true built the toolset payload. |
| `mcp.agentbus.advisory.fired` | `advisory`, `turn_kind`, `chars`, `suppressed_by_profile`, `thread`, `subject` | Signal: mcp.agentbus.advisory.fired |
| `mcp.agentbus.branch.associated` | `thread_id`, `branch_name`, `association_id` | Signal: mcp.agentbus.branch.associated |
| `mcp.agentbus.checkpoint.auto_stamp` | `thread`, `turn_number`, `subject` | Signal: mcp.agentbus.checkpoint.auto_stamp |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.orphan.demoted` | `thread_id`, `orphan_turn_id`, `closeout_turn_id` | Signal: mcp.agentbus.dispatch.orphan.demoted |
| `mcp.agentbus.dispatch.orphaned` | `execution_id`, `thread_id`, `pipeline_id`, `linked_at`, `age_s`, `reason` | Signal: mcp.agentbus.dispatch.orphaned |
| `mcp.agentbus.dispatch.thread.reused` | `thread`, `dispatch_thread_id`, `lane`, `is_auto` | Emitted when generate reuses an existing agent-bus thread. |
| `mcp.agentbus.dispatch.thread.split` | `thread`, `dispatch_thread_id`, `lane` | Emitted when generate mints a sibling thread instead of reusing. |
| `mcp.agentbus.lane.bound` | `thread_id`, `parent_thread_id`, `lane_role`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.lane.bound |
| `mcp.agentbus.quiet_with_wip.fired` | `thread`, `seat`, `reason`, `alarm_id`, `wip_execution_ids` | Signal: mcp.agentbus.quiet_with_wip.fired |
| `mcp.agentbus.sidecar.orphaned` | `uri`, `error`, `thread_id?` | Signal: mcp.agentbus.sidecar.orphaned |
| `mcp.agentbus.sidecar.written` | `thread`, `turn_number`, `uri`, `sha256`, `bytes` | Signal: mcp.agentbus.sidecar.written |
| `mcp.agentbus.thread.abandoned` | `thread`, `reason`, `link_count`, `terminal_count`, `delivered_count` | Thread reaped by watchdog after TTL expiry or all-terminal-no-delivery condition. `reason` values: `pending_ttl_exceeded`, `admitted_ttl_exceeded`, `all_terminal_no_delivery`, `tracker_expired` |
| `mcp.agentbus.thread.closed` | `thread`, `via` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.closed` | `thread`, `via?` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.cse.bound` | `thread_id`, `cse_chat_url`, `cse_registration_id`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.thread.cse.bound |
| `mcp.agentbus.thread.lifecycle.transitioned` | `thread`, `from_state`, `to_state`, `trigger` | Thread lifecycle state machine transition. `role=coordination`. `trigger` values: `create`, `admit`, `turn_posted`, `delivery_sent`, `delivery_failed`, `watchdog_reap`, `reopen`. Emitted from `_transition_lifecycle_state` — single point of correctness for all callers |
| `mcp.agentbus.thread.reopened` | `thread`, `from_state`, `to_state` | Emitted alongside `mcp.agentbus.thread.lifecycle.transitioned` when a turn POST transitions a thread out of a terminal state (completed, abandoned, or failed) back to active |
| `mcp.agentbus.turn.body_over_briefing` | `thread`, `from_agent`, `to_agent`, `subject`, `body_chars`, `target_chars` | Turn body exceeded the briefing target (2k) without allow_long_body, a sidecar, or an inline-contract envelope. Advisory only — the turn was inserted unchanged. |
| `mcp.agentbus.turn.created` | `thread`, `turn_id`, `turn_number`, `from_agent`, `to_agent`, `subject`, `created_at` | Turn inserted in agent-bus store (all transport paths) |
| `mcp.agentbus.watchdog.sweep.failed` | `error` | Watchdog sweep pass raised an unhandled exception. Repeated occurrences indicate a persistent failure in the reap path. |
| `mcp.cortex.op.rejected` | `surface`, `family`, `op` | - |
| `mcp.cse.session.acknowledged` | `registration_id`, `ack_class` | Emit when harvest classifies a typed ACK — not on paste. |
| `mcp.cse.session.conflict` | `reason`, `registration_id`, `chat_url` | Emit on self-supersession or other conflict refusal. |
| `mcp.cse.session.harvested` | `registration_id`, `outcome`, `ack_class`, `turn_count` | Emit when harvest completes or reports an incomplete outcome. |
| `mcp.cse.session.pasted` | `registration_id`, `receipt`, `send_verified`, `replayed` | Emit after paste with receipt — never carries ack_class. |
| `mcp.cse.session.resolved` | `registration_id`, `chat_url`, `state` | Emit when identity resolves on a public provenance read. |
<!-- GENERATED:END region=mcp -->

### Doc Generate Events

<!-- GENERATED:START region=doc inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|

<!-- GENERATED:END region=doc -->

### Pipeline Events

Pipeline events are persisted to the Event Service and can be queried with
`scripts/query-events --op pipeline-trace --execution-id ID`.

<!-- GENERATED:START region=frontier inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `frontier.admit_pointer.loop_closure` | `request_id`, `execution_id`, `admit_target_thread`, `prompt_source_thread`, `prompt_bind_mode`, `prompt_turn_number`, `has_explicit_prompt_source`, `loop_closure`, `allowlisted_silent`, `would_have_refused`, `would_have_refused_total`, `reason`, `spawn_uses_latest_on_thread`, `refused` | B.3 admission refuse — joinable with review_child.prompt_bind (6655). |
| `frontier.cdp.mission.provenance` | `purpose`, `dispatch_thread_id`, `parent_thread`, `mission_kind`, `synthesized` | CDP mission bound a lane the caller did not fully declare. |
| `frontier.densify.review.admitted` | `parent_request_id`, `parent_execution_id`, `parent_dispatch_thread_id`, `densify_thread_id`, `staged_draft_uri`, `reasoning_trace_uri`, `density_triage`, `draft_adequacy`, `opt_out`, `opt_out_reason_code`, `reviewer_family`, `reviewer_model`, `target_thread_id`, `review_execution_id`, `auto_review_child`, `review_spawned`, `hold_reason` | Default-on densify candidate admitted, opted-out, or blank-held. |
| `frontier.densify.review.outcome` | `parent_request_id`, `review_execution_id`, `finding_delta`, `reviewer_concur_only`, `folded_finding_ids` | Validated densify_review_reconcile closeout on the densify thread. |
| `frontier.endpoint.option.rejected` | `request_id`, `agent`, `field`, `reason` | - |
| `frontier.endpoint.persona.resolved` | `request_id`, `agent`, `frontier_kind`, `default_model`, `allowed_models_count`, `allowed_options_count` | `frontier_kind`, `default_model`, `allowed_options_count` |
| `frontier.endpoint.requested` | `request_id`, `agent`, `model` | `agent` (nullable), `model` (nullable) |
| `frontier.handoff.created` | `request_id`, `to_agent`, `thread_id`, `reused` | Handoff thread created on agent-bus. |
| `frontier.handoff.deprecated.alias` | `request_id`, `normalized_op`, `seat` | Deprecated op=handoff,seat=cursor-sdk normalized to the generate path. |
| `frontier.handoff.executor.override` | `request_id`, `handoff_contract`, `recommended_executor`, `source`, `reason_code` | Audit when implement handoff resolves a non-default executor advisory. |
| `frontier.handoff.materialization.incomplete` | `request_id`, `packet_path`, `probe_root`, `source_ref` | Materialized packet absent at executor workspaces root (G-b probe miss). |
| `frontier.handoff.packet.enriched` | `request_id`, `packet_path`, `to_agent`, `skills_added`, `skills_already_wired`, `threads_added` | Web handoff packet auto-enriched before validation (assertion #19650). |
| `frontier.handoff.requested` | `request_id`, `role`, `to_agent`, `handoff_contract`, `model?` | `handoff_contract` (`consult` \ |
| `frontier.poll.hint.issued` | `request_id`, `thread_id`, `caller_agent`, `wait_seconds`, `after_turn`, `reply_from_agent`, `issued_at` | Admit returned poll_hint — correlates with mcp.agentbus.wait.called. |
| `frontier.review_child.context_missing` | `execution_id`, `thread_id`, `attempts` | Admission context miss exhausted reconcile window — fail closed, no spawn. |
| `frontier.review_child.prompt_bind` | `parent_execution_id`, `child_execution_id`, `delivery_thread_id`, `prompt_bind_mode`, `prompt_turn_number`, `latest_read_outcome`, `bound_prompt_class`, `bound_prompt_digest` | Spawn-hook prompt bind instrumentation — Path-1 vs Path-2 discriminator (6655). |
| `frontier.sdk.admit.duplicate_refused` | `dispatch_id`, `thread_id`, `work_fingerprint`, `holder_dispatch_id`, `holder_thread_id?` | Advisory: active peer blocked admit on content fingerprint. |
| `frontier.sdk.auto.auth_gate_blocked` | `thread_id`, `failure_count`, `budget`, `post_ack` | - |
| `frontier.sdk.auto.cdp_effort_bind` | `thread_id`, `execution_id`, `model`, `requested_effort`, `resolved_effort`, `lane` | - |
| `frontier.sdk.auto.closeout_persisted` | `dispatch_id`, `job_id`, `thread_id`, `envelope_sha256`, `closeout_status` | - |
| `frontier.sdk.auto.closeout_replay_abandoned` | `dispatch_id`, `thread_id`, `envelope_sha256`, `attempts` | - |
| `frontier.sdk.auto.closeout_replay_deferred` | `dispatch_id`, `thread_id`, `attempts`, `reason` | - |
| `frontier.sdk.auto.closeout_replay_discarded` | `dispatch_id`, `thread_id`, `discarded_reason` | - |
| `frontier.sdk.auto.closeout_replay_skipped` | `dispatch_id`, `thread_id`, `confirmed_by` | - |
| `frontier.sdk.auto.closeout_replay_suppressed_loss_report` | `dispatch_id`, `job_id`, `thread_id` | - |
| `frontier.sdk.auto.closeout_replayed` | `dispatch_id`, `thread_id`, `envelope_sha256`, `stored_checkpoint`, `recomputed_checkpoint`, `stored_tree_residue`, `recomputed_tree_residue` | - |
| `frontier.sdk.auto.empty_directive_scope_blocked` | `thread_id`, `contract`, `density`, `missed_tokens` | - |
| `frontier.sdk.auto.empty_directive_scope_waived` | `thread_id`, `contract` | - |
| `frontier.sdk.auto.execute_admission_blocked` | `thread_id`, `reason`, `tool_op` | - |
| `frontier.sdk.auto.execute_op_ran` | `thread_id`, `tool_op`, `idempotence`, `ok` | - |
| `frontier.sdk.auto.job_expired` | `thread_id`, `deadline`, `elapsed_s` | - |
| `frontier.sdk.auto.mechanical_executor_redirected` | `thread_id`, `requested_model`, `executor_model`, `contract`, `handoff_contract` | - |
| `frontier.sdk.auto.negotiation.agreed` | `thread_id`, `negotiation_id`, `revision`, `proposal_hash` | - |
| `frontier.sdk.auto.negotiation.countered` | `thread_id`, `negotiation_id`, `revision`, `proposal_hash` | - |
| `frontier.sdk.auto.negotiation.expired` | `thread_id`, `negotiation_id`, `revision` | - |
| `frontier.sdk.auto.negotiation.opened` | `thread_id`, `negotiation_id`, `revision`, `proposal_hash` | - |
| `frontier.sdk.auto.negotiation.ratified` | `thread_id`, `negotiation_id`, `revision`, `proposal_hash`, `agreement_ref` | - |
| `frontier.sdk.auto.negotiation.refused` | `thread_id`, `negotiation_id`, `reason`, `revision` | - |
| `frontier.sdk.auto.negotiation.round_limited` | `thread_id`, `negotiation_id`, `revision` | - |
| `frontier.sdk.auto.premium_bind` | `thread_id`, `dispatch_id`, `model`, `handoff_contract`, `lane`, `knobs` | - |
| `frontier.sdk.auto.second_read` | `thread_id`, `executor_dispatch_id`, `reflex_dispatch_id`, `fired`, `reason`, `model`, `contract`, `outcome` | - |
| `frontier.sdk.auto.thread_status_refused` | `thread_id`, `status` | - |
| `frontier.sdk.capture.divergence_observed` | `dispatch_id`, `thread_id`, `deviation` | - |
| `frontier.sdk.closeout.authorship_outcome` | `dispatch_id`, `outcome`, `baseline_present`, `vacancy_eligible`, `vacancy_fired`, `ledger_registration_available`, `authored_count`, `code_ref`, `schema_version` | One authorship-decision arm, including gate-skips and non-firing omit. |
| `frontier.sdk.closeout.outside_census` | `duration_ms`, `found_count`, `walked`, `mount_root` | - |
| `frontier.sdk.closeout.partial_work.production_specimen` | `dispatch_id`, `envelope_turn`, `thread_id`, `closeout_source`, `contract`, `replay_mode`, `natural_specimen_classification`, `code_ref`, `schema_version` | First envelope write with ``status: partial:work`` on the relay path. |
| `frontier.sdk.closeout.reconciled` | `dispatch_id`, `thread_id`, `suppressed_reason`, `verifying_path` | - |
| `frontier.sdk.closeout.relayed` | `dispatch_id`, `thread_id`, `execution_id`, `closeout_status`, `receipt_path`, `asked_by`, `purpose`, `story_id` | - |
| `frontier.sdk.closeout.relocated` | `dispatch_id`, `thread_id`, `execution_id`, `uri`, `body_chars`, `tier` | - |
| `frontier.sdk.closeout.sidecar_receipt_failed` | `dispatch_id`, `thread_id`, `reason`, `sidecar_path` | - |
| `frontier.sdk.cost_risk.warning` | `model_id`, `contract`, `suppressed`, `request_id?`, `execution_id?`, `suppression_reason?`, `cost_intent_reason?`, `suggested_knobs?`, `suggested_model?` | Cost-risk alignment warning emitted for mechanical opus/sonnet dispatches. |
| `frontier.sdk.gate.i1_clamp_transition` | `from_disposition`, `to_disposition`, `configured_ceiling`, `clamped_limit`, `provisioner_headroom`, `friction_id?` | - |
| `frontier.sdk.gate.limit_derived` | `derived_limit`, `ceiling`, `provisioner_headroom`, `regime_on` | - |
| `frontier.sdk.generate.requested` | `request_id`, `role`, `execution_id`, `handoff_contract`, `resolved_model` | SDK generate admitted — bypassing cloud pipeline. |
| `frontier.sdk.implement.source_ref_unresolved` | `dispatch_id`, `thread_id`, `execution_id` | - |
| `frontier.sdk.knob.dropped` | `model_id`, `knob`, `requested`, `reason` | Unsupported or invalid cursor-sdk knob dropped at alignment. |
| `frontier.sdk.materialization.incomplete` | `request_id`, `packet_path`, `probe_root`, `source_ref`, `origin_service`, `schema_version`, `failure_layer`, `execution_id?`, `thread_id?`, `route?` | Materialized packet absent at executor workspaces root (SDK generate path). |
| `frontier.sdk.reasoning_effort.rejected` | `model_id`, `requested` | Wrong-surface ``reasoning_effort`` rejected at cursor-sdk prepare (BIND_B). |
| `frontier.sdk.restart.bridge_reap_failed` | `dispatch_id`, `thread_id` | - |
| `frontier.sdk.review_child.spawned` | `execution_id`, `parent_execution_id`, `parent_thread_id`, `reviewer_model`, `reviewer_family`, `dedupe_key` | Auto review child spawned after generate/cursor-sdk worker completion. |
| `frontier.sdk.worker.cancelled` | `dispatch_id`, `method`, `reason`, `terminal_status`, `thread_id?`, `superseded_by?`, `error?` | Emitted when a cursor-sdk dispatch is interrupted (supersede / cancel). |
| `frontier.sdk.worker.completed` | `dispatch_id`, `thread_id`, `execution_id`, `duration_s`, `tool_call_count`, `result_bytes`, `outcome`, `resolved_model`, `usage`, `usage_capture_status`, `model_knobs_requested?`, `request_id?`, `sdk_request_id?`, `request_id_source?`, `sdk_run_id?`, `sdk_agent_id?`, `degraded_reasons?`, `asked_by?`, `purpose?`, `story_id?`, `admitted_via?` | - |
| `frontier.sdk.worker.delivery_failed` | `dispatch_id`, `thread_id`, `execution_id`, `status_code`, `result_bytes`, `sidecar_ref` | - |
| `frontier.sdk.worker.dispatched` | `dispatch_id`, `thread_id`, `execution_id`, `seat`, `admitted_via?`, `request_id?`, `asked_by?`, `purpose?`, `story_id?` | GIW worker lane start signal after ``mark_running``. |
| `frontier.sdk.worker.dispatched` | `request_id`, `thread_id`, `execution_id`, `dispatch_id?`, `asked_by?`, `purpose?`, `story_id?`, `admitted_via?`, `seat?` | SDK worker dispatch accepted. |
| `frontier.sdk.worker.failed` | `dispatch_id`, `thread_id`, `execution_id`, `error`, `origin_service`, `schema_version`, `failure_layer?`, `http_status?`, `worker_error_code?`, `transport_error_kind?`, `detail_summary?`, `degraded_reasons?` | - |
| `frontier.sdk.worker.failed` | `request_id`, `thread_id`, `execution_id`, `error`, `origin_service`, `schema_version`, `status_code?`, `code?`, `blocking_dispatch_id?`, `failure_layer?`, `transport_error_kind?`, `dispatch_id?`, `detail_summary?`, `retryable?`, `http_status?`, `worker_error_code?` | SDK worker dispatch rejected or unreachable. |
| `frontier.sdk.worker.lease.acquired` | `dispatch_id`, `source_repo` | - |
| `frontier.sdk.worker.lease.park_enter` | `parent_id`, `child_id`, `source_repo`, `nest_depth?` | - |
| `frontier.sdk.worker.lease.park_restore` | `parent_id`, `child_id`, `source_repo` | - |
| `frontier.sdk.worker.lease.promoted` | `dispatch_id`, `source_repo` | - |
| `frontier.sdk.worker.lease.queue_stalled` | `source_repo` | - |
| `frontier.sdk.worker.lease.released` | `dispatch_id`, `source_repo`, `stale` | - |
| `frontier.sdk.worker.orphaned` | `dispatch_id`, `thread_id`, `execution_id`, `resolved_model`, `timeout_s`, `bridge_aborted`, `terminal_status`, `since_last_progress_s?` | - |
| `frontier.sdk.worker.progress` | `dispatch_id`, `thread_id`, `resolved_model`, `elapsed_s`, `tool_call_count`, `execution_id?` | - |
| `frontier.sdk.worker.queued` | `dispatch_id`, `thread_id`, `source_repo`, `queue_position`, `holder_dispatch_id?`, `holder_thread_id?`, `holder_resolved_model?`, `holder_subject_preview?`, `resolved_model?`, `execution_id?`, `admitted_via?`, `asked_by?`, `purpose?`, `story_id?`, `queued_on?` | - |
| `frontier.sdk.worker.queued` | `request_id`, `thread_id`, `execution_id`, `queue_position`, `dispatch_id?`, `asked_by?`, `purpose?`, `story_id?`, `admitted_via?`, `seat?` | SDK worker dispatch durably queued awaiting write-lease. |
| `frontier.sdk.worker.resumed` | `dispatch_id`, `resume_of`, `sdk_agent_id`, `state_root`, `thread_id`, `execution_id` | - |
| `frontier.sdk.worker.timeout` | `dispatch_id`, `thread_id`, `execution_id`, `resolved_model`, `timeout_s`, `since_last_progress_s?`, `tool_call_count?` | - |
| `frontier.sdk.worker.toolcall` | `dispatch_id`, `thread_id`, `resolved_model`, `call_id`, `tool_name`, `status`, `arg_bytes`, `result_bytes`, `truncated`, `truncated_any`, `execution_id?`, `result_body_status?`, `result_retention_window_s?`, `result_retention_expires_at_unix_ms?`, `result_body?` | - |
| `frontier.skill_suggest_dispatch.completed` | `request_id`, `agent`, `route`, `latency_ms` | Skill-suggest dispatch returned via worker-hop capture path. |
| `frontier.skill_suggest_dispatch.degraded` | `request_id`, `agent`, `route`, `reason`, `latency_ms`, `execution_id?`, `thread_id?`, `dispatch_id?`, `last_worker_status?`, `last_heartbeat_at?` | Skill-suggest dispatch fell back to direct POST /skills/suggest. |
<!-- GENERATED:END region=frontier -->

**`frontier.sdk.worker.completed` — `request_id_source` (manual prose):**

| Value | Meaning |
|---|---|
| `stream` | Captured from live `SDKRequestMessage.request_id` during `observe_run_stream` |
| `post_wait` | Filled from `RunResult.request_id` or `Run.request_id` after `run.wait()` when stream missed (attr present on some builds; **absent on installed 0.1.9 `Run`/`RunResult`**) |
| `error` | Fallback from `CursorSDKError.request_id` on failure paths |
| `absent` | Neither stream nor post-wait nor error supplied an SDK request id |

**Local-bridge join keys (manual prose):** On the local-bridge path, platform `requestId` is often not on the wire (`SDKRequestMessage` cloud-leaning). When `request_id_source=absent`, emit `sdk_request_id_absent` in `degraded_reasons` and carry `sdk_run_id` / `sdk_agent_id` from `Run.id` / `Agent.id` as the backend-log join surface. Do not invent a synthetic `sdk_request_id` from `run.id`.

**POST /api/v1/team/handoff — response `seat_capability` (additive, manual prose):**

```
seat_capability (object, additive): resolved-seat advisory
  delivery: "auto" | "manual"
  api_dispatchable: bool
  auto_dispatchable: bool
  manual_handoff: bool
  tool_surface: "mcp" | "inline-only" | "sdk"
  picker_range: list[str]        # profile.allowed_models
  default_model: str | null
  recommended_executor: str | null   # mirrors top-level executor advisory; null on consult
```

**Documentation Contract Audit (2026-06-11, friction-16768 / thread 1545):**
- `docs/event-contracts.md`: updated — regenerated frontier region via `scripts/gen-event-catalog` to add `frontier.handoff.materialization.incomplete` (`FrontierHandoffMaterializationIncomplete` factory, G-b probe miss).
- `docs/tool-reference.md`: updated — handoff response fields `materialization_present`, `warnings` (graded materialization probe).

### Delivery Audit Signals

Emitted by the delivery-audit registry lifecycle producers. These node-scoped
signals expose correlation-only parent lifecycle and registry-write failures;
they do not claim artifact-level audit coverage.

<!-- GENERATED:START region=delivery inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `delivery.audit.parent.finalized` | `audit_id`, `aggregate_audit_status`, `execution_id?`, `request_id?`, `dispatch_id?`, `registry_schema_version?`, `producer_version?` | Emitted when a B3 delivery-audit parent row is finalized. |
| `delivery.audit.parent.opened` | `audit_id`, `execution_id?`, `request_id?`, `dispatch_id?`, `registry_schema_version?`, `producer_version?` | Emitted when a B3 delivery-audit parent row is opened. |
| `delivery.audit.registry.write.failed` | `audit_id?`, `execution_id?`, `request_id?`, `dispatch_id?`, `error_code?`, `error?`, `registry_schema_version?`, `producer_version?`, _dynamic_ | Emitted when a delivery-audit registry write fails before persisting state. |
<!-- GENERATED:END region=delivery -->

<!-- GENERATED:START region=pipeline inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `pipeline.cancelled` | `pipeline_id`, `execution_id`, `duration_seconds`, `reason`, `completed_steps`, `pending_steps` | Emitted when pipeline execution is cancelled (e.g., client disconnect). |
| `pipeline.checkpoint.failed` | `pipeline_id`, `execution_id`, `step_name`, `operation`, `error` | Emitted when checkpoint operation fails. |
| `pipeline.checkpoint.loaded` | `pipeline_id`, `execution_id`, `step_name`, `checkpoint_key`, `storage_backend`, `saved_at` | Emitted when step resumed from checkpoint. |
| `pipeline.checkpoint.saved` | `pipeline_id`, `execution_id`, `step_name`, `checkpoint_key`, `storage_backend` | Emitted after checkpoint successfully saved. |
| `pipeline.compaction.archived` | `execution_id`, `chat_id`, `anchor_id`, `turn_index`, `role`, `artifact_uri`, `assertion_id`, `tool_calls_count`, `synthesized` | Emitted when `archive_*_turn_v1` persists a turn to cortex (node-scoped) |
| `pipeline.compaction.artifact_load_skipped` | `execution_id`, `chat_id`, `anchor_id`, `attempted`, `loaded`, `skipped`, `skip_reasons?`, `sample_uri?` | Emitted when summarize loads collapse-set artifacts and one or more URIs fail (`missing`, `read_error`, `invalid_json` buckets in `skip_reasons`) (node-scoped) |
| `pipeline.compaction.assembled` | `execution_id`, `chat_id`, `anchor_id`, `turn_index`, `window_size`, `messages_count`, `total_turn_pairs` | Emitted when `assemble_thread_v1` builds the referential prefix (node-scoped) |
| `pipeline.compaction.summarized` | `execution_id`, `chat_id`, `anchor_id`, `turns_summarized`, `summary_assertion_id` | Emitted when chat summarization collapses older turns (Phase C) (node-scoped) |
| `pipeline.compaction.supersede.failed` | `execution_id`, `chat_id`, `anchor_id`, `summary_assertion_id`, `collapse_up_to`, `superseded_count`, `collapse_set_size`, `error` | Emitted when `supersede_collapsed=true` and a supersede call fails after the summary assertion is written (node-scoped) |
| `pipeline.completed` | `pipeline_id`, `execution_id`, `duration_seconds`, `step_count`, `output_step` | Emitted when pipeline completes successfully. |
| `pipeline.consensus.combine.completed` | `pipeline_id`, `execution_id`, `step_name`, `fact_count`, `chunk_count`, `cited_count`, `uncited_count`, `coverage_pct` | Emitted after verified facts are synthesised into a combined answer. |
| `pipeline.consensus.coverage.completed` | `pipeline_id`, `execution_id`, `step_name`, `total_facts`, `covered_count`, `uncovered_count`, `mean_score`, `coverage_pct`, `threshold` | Emitted after embedding-based fact coverage audit completes. |
| `pipeline.consensus.organize.completed` | `pipeline_id`, `execution_id`, `step_name`, `total_facts`, `sections_created`, `facts_assigned`, `valid_json` | Emitted after organize_facts generates and validates an outline. |
| `pipeline.dag.execution.completed` | `pipeline_id`, `execution_id`, `completed_count`, `skipped_count`, `failed_count`, `total_steps` | terminal DAG summary |
| `pipeline.deadlock.detected` | `pipeline_id`, `execution_id`, `incomplete_steps`, `pending_task_count` | emitted before deadlock failure raise |
| `pipeline.dispatch.async` | `pipeline_id`, `execution_id`, `has_delivery_hook`, `caller_agent`, `op`, `output_contract`, `endpoint_request_id?` | `caller_agent`, `endpoint_request_id` — when set, equals the endpoint `request_id` on matching `dispatch.skills.*` / `frontier.endpoint.*` rows; primary join key for skills-delivery telemetry |
| `pipeline.dispatch.cancelled` | `pipeline_id`, `execution_id`, `source` | Emitted when a running dispatch is cancelled by an explicit DELETE. |
| `pipeline.dispatch.completed` | `pipeline_id`, `execution_id`, `status`, `duration_s`, `caller_agent`, `op`, `output_contract` | `caller_agent` |
| `pipeline.dispatch.delivery.close.failed` | `pipeline_id`, `execution_id`, `thread`, `status_code`, `error_preview` | Emitted when ephemeral thread close failed after a successful delivery. |
| `pipeline.dispatch.delivery.failed` | `pipeline_id`, `execution_id`, `thread`, `status_code`, `error_preview`, `op`, `output_contract` | Agent-bus POST failed or oversized on-behalf delivery could not persist a cortex sidecar. `error_preview` may include `sidecar_write_failed body_chars=N` when content exceeded the bus limit and the sidecar write failed (POST not attempted). `status_code=413` on that path. |
| `pipeline.dispatch.delivery.sent` | `pipeline_id`, `execution_id`, `thread`, `to_agent`, `from_agent`, `op`, `output_contract`, `delivery_mode`, `sidecar_uri`, `content_sha256`, `sidecar_status` | Terminal-state turn posted successfully. On-behalf deliveries always write a cortex sidecar: `delivery_mode` ∈ {`inline`, `sidecar`}; `sidecar_uri` and `content_sha256` identify the durable copy; `sidecar_status` ∈ {`ok`, `failed`} when inline delivery proceeded despite a sidecar write failure. |
| `pipeline.dispatch.delivery.skipped` | `pipeline_id`, `execution_id`, `reason`, `op`, `output_contract` | Emitted when delivery was not attempted. |
| `pipeline.dispatch.journal.pruned` | `records_deleted`, `oldest_deleted_age_seconds` | `oldest_deleted_age_seconds` |
| `pipeline.dispatch.journal.read` | `execution_id`, `age_seconds` | Emitted when tracker fallback serves a terminal record from sqlite. |
| `pipeline.dispatch.journal.written` | `execution_id`, `status`, `bytes` | Emitted when a terminal dispatch record is persisted to sqlite. |
| `pipeline.dispatch.rejected` | `pipeline_id`, `reason` | Emitted when the async tracker refuses to admit a new execution. |
| `pipeline.dispatch.tracker.expired` | `pipeline_id`, `execution_id`, `status`, `age_seconds` | Emitted when a terminal tracker record is pruned by TTL. |
| `pipeline.execution.cancelled` | `pipeline_id`, `execution_id`, `cancelled_steps` | external cancellation summary |
| `pipeline.execution.timed.out` | `pipeline_id`, `execution_id`, `timeout_seconds`, `incomplete_steps` | emitted before timeout failure raise |
| `pipeline.failed` | `pipeline_id`, `execution_id`, `duration_seconds`, `error`, `failed_step` | Emitted when pipeline execution fails. |
| `pipeline.frontier.dispatch.capability.miss` | `execution_id`, `event_name`, `model`, `model_entity_id`, `miss_key`, `miss_reason` | Emitted when ``resolve_dispatch`` cannot infer the provider/surface (G13). |
| `pipeline.frontier.dispatch.capability.rejected` | `execution_id`, `event_name`, `model`, `model_entity_id`, `provider`, `knob`, `reject_code`, `reason` | Emitted once per rejected knob in the G9 ``ProtocolError`` envelope. |
| `pipeline.frontier.dispatch.capability.resolved` | `execution_id`, `event_name`, `model`, `model_entity_id`, `provider`, `api_surface`, _dynamic_ | adaptive-only: `reasoning_output_config_effort`. Pinned cross-stack name in `event_name` is `capability_dispatch.resolved` (node-scoped) |
| `pipeline.frontier.dispatch.completed` | `agent`, `execution_id`, `turns_used`, `tool_calls_made`, `reasoning_present`, `prompt_tokens`, `completion_tokens`, `provider`, `model_entity_id`, `op`, `finish_reason`, `block_reason`, `cached_tokens?` | native-endpoint loop returned terminal content; `model_entity_id` is the canonical Cortex `model:<slug>` for the admitted model; `op` ∈ {`generate`, `to_thread`, ``} (node-scoped) |
| `pipeline.frontier.dispatch.corpus.inlined` | `request_id`, `role`, `model`, `injected_count`, `dropped_count`, `injected_bytes`, `dropped_bytes`, `budget_bytes` | Corpus document bodies inlined for an inline-only dispatch. |
| `pipeline.frontier.dispatch.corpus.unresolved` | `request_id`, `role`, `model`, `uri` | A ``<corpus>`` URI could not be resolved (soft-drop). |
| `pipeline.frontier.dispatch.empty.completion` | `execution_id`, `agent`, `model`, `model_entity_id`, `provider`, `turns_used`, `tool_calls_made`, `finish_reason`, `block_reason` | Fires when `frontier_dispatch_v1` returns empty/whitespace-only content on the non-exhausted branch. Distinct from `.exhausted` (intentional no-content on max-turns). Emitted immediately before `EmptyCompletionError` is raised so terminal state converts from `completed` to `failed`. (node-scoped) |
| `pipeline.frontier.dispatch.exhausted` | `agent`, `execution_id`, `turns_used`, `tool_calls_made`, `provider`, `model_entity_id`, `op`, `finish_reason`, `block_reason`, `enforcement`, `exhaustion_summary` | native-endpoint loop hit `max_tool_turns` without terminal content; `model_entity_id` is the canonical Cortex `model:<slug>` for the admitted model; `op` ∈ {`generate`, `to_thread`, ``} (node-scoped) |
| `pipeline.frontier.dispatch.hydrated` | `agent`, `execution_id`, `briefing_bytes`, `section_counts`, `continuation_id` | `frontier_dispatch_v1` team-seat step loaded dispatched-agent Cortex boot; omitted in persona-free mode (node-scoped) |
| `pipeline.frontier.dispatch.mismatch` | `execution_id`, `agent`, `requested_model`, `model_entity_id`, `valid_family`, `mismatch_kind` | Emitted when `frontier_dispatch_v1` rejects an agent + model combination. `mismatch_kind="provider"` — model's provider doesn't match the agent's identity-bound provider family (e.g. oppie + anthropic model); suggests typo or wrong family. `mismatch_kind="variant"` — provider matches but model fails the agent's variant requirement (e.g. oppie + non-multi-agent xAI model); suggests stale model pin or missing beta-gate access. Precedes `pipeline_execution_failed` with `code=agent_model_mismatch`. `model_entity_id` is the canonical Cortex `model:<slug>` for the requested model — present here (not only on `.started`) so correlators can recover it on the rejection path where `.started` never fires (node-scoped) |
| `pipeline.frontier.dispatch.output.short` | `agent`, `execution_id`, `model`, `provider`, `boot_level`, `output_tokens`, `tool_calls_made`, `finish_reason`, `block_reason`, `content_preview`, `op`, `output_contract` | Team/full `frontier_dispatch_v1` dispatch returned <500 output tokens — captures first ~500 chars of content for triage of thinking-budget starvation, model confusion, or tool-loop misrouting. Emission is detector-gated on `boot_level ∈ {team, full}`; persona-free dispatches pass `boot_level='none'` and are filtered. Replaces the deprecated `mcp.frontier.output.short` signal as of Task-7 Phase 1 (node-scoped) |
| `pipeline.frontier.dispatch.refusal.suspected` | `agent`, `execution_id`, `model`, `provider`, `output_tokens`, `tool_calls_made`, `content_preview`, `reason` | Post-loop heuristic fires when an inline-contract dispatch returns a short refusal-shaped completion after the model already made tool calls — gated on `output_tokens < 80` AND `tool_calls_made > 0` AND a refusal-marker hit on the lowercase content ("i can't continue", "cannot comply", "i'm sorry", etc.). Distinct from `.output.short` (broad short-output heuristic) and `.termination.shadow` (provider=google thought-trace pattern). Emitted alongside `pipeline.frontier.dispatch.completed`; consumers should retry on a higher-capability model or shorten the write loop. Not gated on `boot_level` — refusal detection runs on persona-free dispatches too. (node-scoped) |
| `pipeline.frontier.dispatch.remotemcp.enabled` | `execution_id`, `agent`, `model`, `model_entity_id`, `provider` | remote-MCP path selected for this execution; adapter attached provider-native MCP descriptor before the native call; implies client-side tool loop disabled (node-scoped) |
| `pipeline.frontier.dispatch.remotemcp.misconfigured` | `execution_id`, `agent`, `model`, `model_entity_id`, `reason` | `resolve_mcp_env()` raised because `MCP_PUBLIC_URL`/`MCP_AUTH_TOKEN` is unset in the Stargate container env; precedes `pipeline_execution_failed`. `model_entity_id` is the canonical Cortex `model:<slug>` — present here (not only on `.started`) so correlators can recover it on the race where misconfigured fires before `.started` (env resolution fails during admission) (node-scoped) |
| `pipeline.frontier.dispatch.started` | `execution_id`, `agent`, `model`, `model_entity_id`, `provider`, `boot_level`, `remote_mcp`, `op`, `endpoint_request_id?` | Fires once per `frontier_dispatch_v1` execution, after hydration (if persona) and before the native call. `model` is the wire/provider-routed id; `model_entity_id` is the canonical Cortex `model:<slug>`. `boot_level` is internal observability vocabulary derived from agent presence, not a caller-facing parameter. `remote_mcp` in the payload is internal observability (card-derived remote-connector selection), not a caller-facing parameter. `op` ∈ {`generate`, `to_thread`, ``}. `endpoint_request_id` joins to endpoint `dispatch.skills.*` rows by `request_id`. MCP callers reach this via `team_dispatch` relay (node-scoped) |
| `pipeline.frontier.dispatch.termination.shadow` | `agent`, `execution_id`, `model`, `provider`, `boot_level`, `output_tokens`, `finish_reason`, `block_reason`, `reason`, `confidence`, `evidence`, `suggested_next_action`, `trace_visibility`, `generate_id`, `detector` | Advisory post-`pipeline.frontier.dispatch.completed` detection of likely silent-termination patterns (refusal / incapacity / policy / scope / loop / token_exhaustion) in the model's reasoning trace. v1 scope: provider=`google` + team-seat dispatch + thought summaries available. `.shadow` topic suffix marks v1 as NOT production-consumable during the calibration window — orchestrators MUST filter on suffix, not on a shadow boolean. Never replaces `.completed`, never fires on `.exhausted`. Replaces the deprecated `mcp.frontier.thought.termination.shadow` signal as of Task-7 Phase 1 (node-scoped) |
| `pipeline.frontier.dispatch.tool.called` | `agent`, `execution_id`, `tool_name`, `turn`, `elapsed_ms`, `provider` | tool executed successfully inside native-endpoint tool-use loop (node-scoped) |
| `pipeline.frontier.dispatch.tool.failed` | `agent`, `execution_id`, `tool_name`, `turn`, `elapsed_ms`, `error`, `provider`, `arguments`, `full_error`, `retry_count` | tool call returned error envelope or raised inside loop; `arguments` / `full_error` / `retry_count` aid observability and deterministic-retry policy (node-scoped) |
| `pipeline.frontier.dispatch.tool.requested` | `agent`, `execution_id`, `tool_name`, `provider`, `tool_call_id` | fired when the model begins generating a `tool_use` block in the streaming response (`content_block_start`), before tool execution; `tool_call_id` correlates with subsequent `.tool.called`/`.tool.failed` events — Anthropic: `content_block.id`; OpenAI/xAI: `item.id` or `item.call_id`; Google: `null` (no native id); emitted by `frontier_dispatch` handler `on_event` callback (node-scoped) |
| `pipeline.frontier.dispatch.tool.suppressed` | `execution_id`, `agent`, `model`, `provider`, `reason` | Emitted when the tool surface is coerced to empty or server-side built-ins are suppressed. Reason vocabulary (from `frontier_tools.py` + live callsites): `capability_tier_inline_only` — role demoted to inline-only substrate (`resolve_dispatch_tool_set`); `mcp_client_tool_loop_unsupported` — model card rejects client-side MCP tools (`admission_checks.py`); `server_tools_knob` — caller set `server_tools=False` while the card carries server-side built-ins (`gen_params.py`). Internal observability field, not caller-facing. (node-scoped) |
| `pipeline.frontier.dispatch.tools.wire` | `execution_id`, `agent`, `model`, `provider`, `hop`, `tools`, `tools_present` | Permanent observability for server-tool wire presence (hop-1 / hop-2). |
| `pipeline.map.completed` | `pipeline_id`, `execution_id`, `step_name`, `succeeded_count`, `failed_count`, `total_count`, `duration_seconds`, `met_threshold` | Event factory for when a map step finishes (success or failure). |
| `pipeline.map.iteration.completed` | `pipeline_id`, `execution_id`, `step_name`, `iteration_index`, `elapsed_seconds`, `inference_seconds`, `prompt_tokens`, `completion_tokens` | Event factory for when one map iteration completes successfully. |
| `pipeline.map.iteration.failed` | `pipeline_id`, `execution_id`, `step_name`, `iteration_index`, `error`, `duration_seconds`, `failure_type`, `truncated_response_path?`, `truncation_tokens?` | Event factory for when one map iteration fails. |
| `pipeline.map.iteration.inference.fallback` | `pipeline_id`, `execution_id`, `step_name`, `iteration_index`, `request_id`, `fallback_signal`, `reason` | Event factory for when fallback inference boundary timing is used. |
| `pipeline.map.iteration.inference.lost` | `pipeline_id`, `execution_id`, `step_name`, `iteration_index`, `request_id` | Event factory for when no inference boundary signal was observed. |
| `pipeline.map.iteration.inference.started` | `pipeline_id`, `execution_id`, `step_name`, `iteration_index`, `request_id`, `model_id`, `queue_wait_seconds` | Event factory for when inference begins for a map iteration. |
| `pipeline.map.iteration.started` | `pipeline_id`, `execution_id`, `step_name`, `iteration_index`, `model_id`, `gateway_id`, `request_id` | `request_id` |
| `pipeline.map.started` | `pipeline_id`, `execution_id`, `step_name`, `total_iterations`, `timeout_seconds`, `threshold` | Event factory for when a map step begins execution. |
| `pipeline.map.step.empty.iterations` | `pipeline_id`, `execution_id`, `step_name` | emitted when map_over resolves to empty collection (0 iterations); no iterations run |
| `pipeline.map.timeout.warning` | `pipeline_id`, `execution_id`, `step_name`, `elapsed_seconds`, `timeout_seconds`, `pending_iterations`, `completed_iterations`, `percent_elapsed` | Event factory for when a map step approaches timeout. |
| `pipeline.model.gate.claimed` | `pipeline_id`, `execution_id`, `step_id`, `model_id` | step acquired model gate; `model_id` is resolved target model identity |
| `pipeline.model.gate.failure.release` | `pipeline_id`, `execution_id`, `step_id`, `model_id`, `error_type` | explicit failure-boundary release marker |
| `pipeline.model.gate.released` | `pipeline_id`, `execution_id`, `step_id`, `model_id`, `outcome` | gate released (`success`\ |
| `pipeline.model.registry.lookup.failed` | `pipeline_id`, `execution_id`, `step_id`, `model_ref`, `error` | model_ref lookup failure |
| `pipeline.rag.coverage.selection.applied` | `pipeline_id`, `execution_id`, `step_name`, `enabled`, `applied`, `chunks_before`, `chunks_after` | Coverage-aware selection outcome after metadata boost scoring (only emitted when coverage selection is enabled) |
| `pipeline.rag.generation.context.refined` | `pipeline_id`, `execution_id`, `step_name`, `predicted_scopes`, `original_must_include`, `enriched_must_include`, `scope_anchors_added`, `flat_hint_count`, `register_scopes_included`, `register_scopes_total` | Emitted after generation context is refined with scope-filtered vocabulary. |
| `pipeline.rag.hints.filtered` | `pipeline_id`, `execution_id`, `step_name`, `query_terms`, `original_hint_count`, `filtered_hint_count`, `filtered_hints`, `fallback`, `scoring_mode`, `min_threshold`, `capped`, `cap_limit` | Emitted after corpus hints are filtered by co-occurrence with query terms. |
| `pipeline.rag.metadata.boost.applied` | `pipeline_id`, `execution_id`, `step_name`, `metadata_hit_count`, `avg_metadata_score`, `applied`, `chunks_after_boost` | Emitted after post-RRF metadata boost is applied (or skipped). |
| `pipeline.rag.neighbor.expansion.applied` | `pipeline_id`, `execution_id`, `step_name`, `enabled`, `neighbors_added`, `neighbors_fetched`, `sources_expanded`, `expansion_n`, `max_chunks`, `expansion_seconds` | Neighbor chunk expansion result — emitted when expansion is enabled, even if zero neighbors were added |
| `pipeline.rag.query.analysis.completed` | `pipeline_id`, `execution_id`, `step_name`, `needs_retrieval`, `scope`, `scope_confidence`, `out_of_scope_reason` | Scope-analysis decision consumed by retrieval |
| `pipeline.rag.query.rewrite.completed` | `pipeline_id`, `execution_id`, `step_name`, `rewrite_count`, `hyde_present` | Rewrite generation completed and available to retrieval |
| `pipeline.rag.query.rewrite.skipped` | `pipeline_id`, `execution_id`, `step_name`, `reason` | Rewrite generation bypassed (`rewrite_disabled`, `needs_retrieval_false`, `step_condition_false`) |
| `pipeline.rag.rerank.completed` | `pipeline_id`, `execution_id`, `step_name`, `rerank_enabled`, `model_id`, `chunks_input`, `chunks_output`, `windows_evaluated`, `max_rank_movement_observed`, `total_rerank_seconds` | Post-reranking: LLM reranking metrics or skip confirmation |
| `pipeline.rag.retrieval.bibliography.filtered` | `pipeline_id`, `execution_id`, `step_name`, `chunks_dropped` | Emitted when post-RRF junk/bibliography filter removes one or more chunks |
| `pipeline.rag.retrieval.completed` | `pipeline_id`, `execution_id`, `step_name`, `predicted_scope`, `scope_confidence`, `fallback_triggered`, `chunks_per_query`, `zero_result_queries`, `rrf_score_min`, `rrf_score_max`, `rrf_score_mean`, `chunks_after_merge`, `total_retrieval_seconds`, `neighbor_expansion_added`, `coverage_bias_applied`, `coverage_bias_query_class`, `coverage_bias_anchor_source`, `coverage_bias_boosted_chunks` | Post-retrieval: scope prediction + quality metrics; coverage-bias fields default when query-class bias is off (`coverage_bias_applied=false`, `coverage_bias_query_class=default`, `coverage_bias_anchor_source=null`, `coverage_bias_boosted_chunks=0`) |
| `pipeline.rag.retrieval.diversity.limited` | `pipeline_id`, `execution_id`, `step_name`, `per_source_limit`, `chunks_dropped`, `chunks_before`, `chunks_after` | Emitted when source-diversity cap removes chunks from a dominant source. |
| `pipeline.rag.retrieval.failed` | `pipeline_id`, `execution_id`, `step_name`, `error`, `total_retrieval_seconds` | All queries failed — no chunks to merge |
| `pipeline.rag.retrieval.params.resolved` | `pipeline_id`, `execution_id`, `step_name`, `consumer_model`, `consumer_tier`, `profile_class`, `max_chunks`, `top_k_per_query`, `rrf_k`, `scope`, `retrieval_mode`, `uses_explicit_prefixes`, `pool_b_enabled` | Pre-retrieval: effective parameters after three-tier merge; `scope` may be string or array of strings (multiscope); `pool_b_enabled` indicates sparse facet/IDF pool (Pool B) active |
| `pipeline.rag.retrieval.skipped` | `pipeline_id`, `execution_id`, `step_name`, `reason`, `out_of_scope_reason` | Retrieval skipped by semantic no-retrieval gate (query/corpus mismatch with no user prefix override) |
| `pipeline.rag.scope.rejected` | `pipeline_id`, `execution_id`, `step_name`, `reason`, `scope`, `details` | Scope validation rejected — fail-closed, 0 chunks returned |
| `pipeline.registry.unavailable` | `pipeline_id`, `missing_models` | Pipeline permanently skipped after deferred retry — model deps unresolvable. |
| `pipeline.started` | `pipeline_id`, `execution_id`, `domain`, `step_count`, `timeout_seconds` | Emitted when pipeline execution begins. |
| `pipeline.step.completed` | `pipeline_id`, `execution_id`, `step_name`, `duration_seconds`, `output_length`, `prompt_tokens`, `completion_tokens`, `model_call_count`, `model_id?`, `exit_code?`, `json_output_keys?`, `cached_tokens?` | `exit_code` (shell steps only) |
| `pipeline.step.condition.evaluated` | `pipeline_id`, `execution_id`, `step_name`, `condition`, `result`, `available_outputs` | Emitted when a step's condition expression is evaluated. |
| `pipeline.step.context.exceeded` | `pipeline_id`, `execution_id`, `step_name`, `model_id`, `estimated_tokens`, `context_length`, `effective_context_per_slot`, `prompt_chars` | Emitted when estimated prompt tokens exceed the model's context window. |
| `pipeline.step.domain.verification.completed` | `execution_id`, `step_id`, `domain`, `model_id`, `statement_count`, `passed_count`, `failed_count`, `duration_ms` | Create PIPELINE_STEP_DOMAIN_VERIFICATION_COMPLETED event. |
| `pipeline.step.domain.verification.started` | `execution_id`, `step_id`, `domain`, `model_id`, `statement_count` | Create PIPELINE_STEP_DOMAIN_VERIFICATION_STARTED event. |
| `pipeline.step.embedding.completed` | `execution_id`, `step_id`, `model_id`, `input_count`, `duration_ms`, `embedding_dim` | Create PIPELINE_STEP_EMBEDDING_COMPLETED event. |
| `pipeline.step.embedding.failed` | `execution_id`, `step_id`, `model_id`, `input_count`, `duration_ms`, `error`, `status_code` | Create PIPELINE_STEP_EMBEDDING_FAILED event. |
| `pipeline.step.embedding.started` | `execution_id`, `step_id`, `model_id`, `input_count` | Create PIPELINE_STEP_EMBEDDING_STARTED event. |
| `pipeline.step.failed` | `pipeline_id`, `execution_id`, `step_name`, `error`, `prompt_tokens`, `completion_tokens`, `model_call_count`, `duration_seconds?`, `traceback?` | `exc_type`: exception class name (e.g. `RemoteProtocolError`); always non-empty, primary diagnostic key when `error` is empty |
| `pipeline.step.model.deferred` | `pipeline_id`, `execution_id`, `step_id`, `model_id`, `reason` | deferral due to model admission gate |
| `pipeline.step.model.fallback` | `pipeline_id`, `execution_id`, `step_name`, `primary_model`, `fallback_model`, `primary_error_type`, `fallback_attempt`, `total_fallbacks`, `succeeded` | Executor-level fallback attempt outcome for eligible failures only |
| `pipeline.step.model.fallback.suppressed` | `pipeline_id`, `execution_id`, `step_name`, `primary_error_type`, `suppression_reason` | Explicit suppression boundary for deterministic local errors |
| `pipeline.step.model.resolved` | `pipeline_id`, `execution_id`, `step_name`, `model_id`, `selection_source` | Emitted immediately after model selection, before inference begins. |
| `pipeline.step.skipped` | `pipeline_id`, `execution_id`, `step_name`, `reason` | Emitted when step is skipped due to condition evaluation. |
| `pipeline.step.started` | `pipeline_id`, `execution_id`, `step_name`, `step_type`, `model_id`, `is_map_step` | Emitted when step execution begins (includes both regular and map steps). |
| `pipeline.subpipeline.expanded` | `pipeline_id`, `execution_id`, `parent_step_name`, `resolved_output_step`, `expanded_step_count` | Emitted when a ``sub_pipeline`` step is expanded into namespaced steps. |
<!-- GENERATED:END region=pipeline -->

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

<!-- GENERATED:START region=pipeline inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `pipeline.cancelled` | `pipeline_id`, `execution_id`, `duration_seconds`, `reason`, `completed_steps`, `pending_steps` | Emitted when pipeline execution is cancelled (e.g., client disconnect). |
| `pipeline.checkpoint.failed` | `pipeline_id`, `execution_id`, `step_name`, `operation`, `error` | Emitted when checkpoint operation fails. |
| `pipeline.checkpoint.loaded` | `pipeline_id`, `execution_id`, `step_name`, `checkpoint_key`, `storage_backend`, `saved_at` | Emitted when step resumed from checkpoint. |
| `pipeline.checkpoint.saved` | `pipeline_id`, `execution_id`, `step_name`, `checkpoint_key`, `storage_backend` | Emitted after checkpoint successfully saved. |
| `pipeline.compaction.archived` | `execution_id`, `chat_id`, `anchor_id`, `turn_index`, `role`, `artifact_uri`, `assertion_id`, `tool_calls_count`, `synthesized` | Emitted when `archive_*_turn_v1` persists a turn to cortex (node-scoped) |
| `pipeline.compaction.artifact_load_skipped` | `execution_id`, `chat_id`, `anchor_id`, `attempted`, `loaded`, `skipped`, `skip_reasons?`, `sample_uri?` | Emitted when summarize loads collapse-set artifacts and one or more URIs fail (`missing`, `read_error`, `invalid_json` buckets in `skip_reasons`) (node-scoped) |
| `pipeline.compaction.assembled` | `execution_id`, `chat_id`, `anchor_id`, `turn_index`, `window_size`, `messages_count`, `total_turn_pairs` | Emitted when `assemble_thread_v1` builds the referential prefix (node-scoped) |
| `pipeline.compaction.summarized` | `execution_id`, `chat_id`, `anchor_id`, `turns_summarized`, `summary_assertion_id` | Emitted when chat summarization collapses older turns (Phase C) (node-scoped) |
| `pipeline.compaction.supersede.failed` | `execution_id`, `chat_id`, `anchor_id`, `summary_assertion_id`, `collapse_up_to`, `superseded_count`, `collapse_set_size`, `error` | Emitted when `supersede_collapsed=true` and a supersede call fails after the summary assertion is written (node-scoped) |
| `pipeline.completed` | `pipeline_id`, `execution_id`, `duration_seconds`, `step_count`, `output_step` | Emitted when pipeline completes successfully. |
| `pipeline.consensus.combine.completed` | `pipeline_id`, `execution_id`, `step_name`, `fact_count`, `chunk_count`, `cited_count`, `uncited_count`, `coverage_pct` | Emitted after verified facts are synthesised into a combined answer. |
| `pipeline.consensus.coverage.completed` | `pipeline_id`, `execution_id`, `step_name`, `total_facts`, `covered_count`, `uncovered_count`, `mean_score`, `coverage_pct`, `threshold` | Emitted after embedding-based fact coverage audit completes. |
| `pipeline.consensus.organize.completed` | `pipeline_id`, `execution_id`, `step_name`, `total_facts`, `sections_created`, `facts_assigned`, `valid_json` | Emitted after organize_facts generates and validates an outline. |
| `pipeline.dag.execution.completed` | `pipeline_id`, `execution_id`, `completed_count`, `skipped_count`, `failed_count`, `total_steps` | terminal DAG summary |
| `pipeline.deadlock.detected` | `pipeline_id`, `execution_id`, `incomplete_steps`, `pending_task_count` | emitted before deadlock failure raise |
| `pipeline.dispatch.async` | `pipeline_id`, `execution_id`, `has_delivery_hook`, `caller_agent`, `op`, `output_contract`, `endpoint_request_id?` | `caller_agent`, `endpoint_request_id` — when set, equals the endpoint `request_id` on matching `dispatch.skills.*` / `frontier.endpoint.*` rows; primary join key for skills-delivery telemetry |
| `pipeline.dispatch.cancelled` | `pipeline_id`, `execution_id`, `source` | Emitted when a running dispatch is cancelled by an explicit DELETE. |
| `pipeline.dispatch.completed` | `pipeline_id`, `execution_id`, `status`, `duration_s`, `caller_agent`, `op`, `output_contract` | `caller_agent` |
| `pipeline.dispatch.delivery.close.failed` | `pipeline_id`, `execution_id`, `thread`, `status_code`, `error_preview` | Emitted when ephemeral thread close failed after a successful delivery. |
| `pipeline.dispatch.delivery.failed` | `pipeline_id`, `execution_id`, `thread`, `status_code`, `error_preview`, `op`, `output_contract` | Agent-bus POST failed or oversized on-behalf delivery could not persist a cortex sidecar. `error_preview` may include `sidecar_write_failed body_chars=N` when content exceeded the bus limit and the sidecar write failed (POST not attempted). `status_code=413` on that path. |
| `pipeline.dispatch.delivery.sent` | `pipeline_id`, `execution_id`, `thread`, `to_agent`, `from_agent`, `op`, `output_contract`, `delivery_mode`, `sidecar_uri`, `content_sha256`, `sidecar_status` | Terminal-state turn posted successfully. On-behalf deliveries always write a cortex sidecar: `delivery_mode` ∈ {`inline`, `sidecar`}; `sidecar_uri` and `content_sha256` identify the durable copy; `sidecar_status` ∈ {`ok`, `failed`} when inline delivery proceeded despite a sidecar write failure. |
| `pipeline.dispatch.delivery.skipped` | `pipeline_id`, `execution_id`, `reason`, `op`, `output_contract` | Emitted when delivery was not attempted. |
| `pipeline.dispatch.journal.pruned` | `records_deleted`, `oldest_deleted_age_seconds` | `oldest_deleted_age_seconds` |
| `pipeline.dispatch.journal.read` | `execution_id`, `age_seconds` | Emitted when tracker fallback serves a terminal record from sqlite. |
| `pipeline.dispatch.journal.written` | `execution_id`, `status`, `bytes` | Emitted when a terminal dispatch record is persisted to sqlite. |
| `pipeline.dispatch.rejected` | `pipeline_id`, `reason` | Emitted when the async tracker refuses to admit a new execution. |
| `pipeline.dispatch.tracker.expired` | `pipeline_id`, `execution_id`, `status`, `age_seconds` | Emitted when a terminal tracker record is pruned by TTL. |
| `pipeline.execution.cancelled` | `pipeline_id`, `execution_id`, `cancelled_steps` | external cancellation summary |
| `pipeline.execution.timed.out` | `pipeline_id`, `execution_id`, `timeout_seconds`, `incomplete_steps` | emitted before timeout failure raise |
| `pipeline.failed` | `pipeline_id`, `execution_id`, `duration_seconds`, `error`, `failed_step` | Emitted when pipeline execution fails. |
| `pipeline.frontier.dispatch.capability.miss` | `execution_id`, `event_name`, `model`, `model_entity_id`, `miss_key`, `miss_reason` | Emitted when ``resolve_dispatch`` cannot infer the provider/surface (G13). |
| `pipeline.frontier.dispatch.capability.rejected` | `execution_id`, `event_name`, `model`, `model_entity_id`, `provider`, `knob`, `reject_code`, `reason` | Emitted once per rejected knob in the G9 ``ProtocolError`` envelope. |
| `pipeline.frontier.dispatch.capability.resolved` | `execution_id`, `event_name`, `model`, `model_entity_id`, `provider`, `api_surface`, _dynamic_ | adaptive-only: `reasoning_output_config_effort`. Pinned cross-stack name in `event_name` is `capability_dispatch.resolved` (node-scoped) |
| `pipeline.frontier.dispatch.completed` | `agent`, `execution_id`, `turns_used`, `tool_calls_made`, `reasoning_present`, `prompt_tokens`, `completion_tokens`, `provider`, `model_entity_id`, `op`, `finish_reason`, `block_reason`, `cached_tokens?` | native-endpoint loop returned terminal content; `model_entity_id` is the canonical Cortex `model:<slug>` for the admitted model; `op` ∈ {`generate`, `to_thread`, ``} (node-scoped) |
| `pipeline.frontier.dispatch.corpus.inlined` | `request_id`, `role`, `model`, `injected_count`, `dropped_count`, `injected_bytes`, `dropped_bytes`, `budget_bytes` | Corpus document bodies inlined for an inline-only dispatch. |
| `pipeline.frontier.dispatch.corpus.unresolved` | `request_id`, `role`, `model`, `uri` | A ``<corpus>`` URI could not be resolved (soft-drop). |
| `pipeline.frontier.dispatch.empty.completion` | `execution_id`, `agent`, `model`, `model_entity_id`, `provider`, `turns_used`, `tool_calls_made`, `finish_reason`, `block_reason` | Fires when `frontier_dispatch_v1` returns empty/whitespace-only content on the non-exhausted branch. Distinct from `.exhausted` (intentional no-content on max-turns). Emitted immediately before `EmptyCompletionError` is raised so terminal state converts from `completed` to `failed`. (node-scoped) |
| `pipeline.frontier.dispatch.exhausted` | `agent`, `execution_id`, `turns_used`, `tool_calls_made`, `provider`, `model_entity_id`, `op`, `finish_reason`, `block_reason`, `enforcement`, `exhaustion_summary` | native-endpoint loop hit `max_tool_turns` without terminal content; `model_entity_id` is the canonical Cortex `model:<slug>` for the admitted model; `op` ∈ {`generate`, `to_thread`, ``} (node-scoped) |
| `pipeline.frontier.dispatch.hydrated` | `agent`, `execution_id`, `briefing_bytes`, `section_counts`, `continuation_id` | `frontier_dispatch_v1` team-seat step loaded dispatched-agent Cortex boot; omitted in persona-free mode (node-scoped) |
| `pipeline.frontier.dispatch.mismatch` | `execution_id`, `agent`, `requested_model`, `model_entity_id`, `valid_family`, `mismatch_kind` | Emitted when `frontier_dispatch_v1` rejects an agent + model combination. `mismatch_kind="provider"` — model's provider doesn't match the agent's identity-bound provider family (e.g. oppie + anthropic model); suggests typo or wrong family. `mismatch_kind="variant"` — provider matches but model fails the agent's variant requirement (e.g. oppie + non-multi-agent xAI model); suggests stale model pin or missing beta-gate access. Precedes `pipeline_execution_failed` with `code=agent_model_mismatch`. `model_entity_id` is the canonical Cortex `model:<slug>` for the requested model — present here (not only on `.started`) so correlators can recover it on the rejection path where `.started` never fires (node-scoped) |
| `pipeline.frontier.dispatch.output.short` | `agent`, `execution_id`, `model`, `provider`, `boot_level`, `output_tokens`, `tool_calls_made`, `finish_reason`, `block_reason`, `content_preview`, `op`, `output_contract` | Team/full `frontier_dispatch_v1` dispatch returned <500 output tokens — captures first ~500 chars of content for triage of thinking-budget starvation, model confusion, or tool-loop misrouting. Emission is detector-gated on `boot_level ∈ {team, full}`; persona-free dispatches pass `boot_level='none'` and are filtered. Replaces the deprecated `mcp.frontier.output.short` signal as of Task-7 Phase 1 (node-scoped) |
| `pipeline.frontier.dispatch.refusal.suspected` | `agent`, `execution_id`, `model`, `provider`, `output_tokens`, `tool_calls_made`, `content_preview`, `reason` | Post-loop heuristic fires when an inline-contract dispatch returns a short refusal-shaped completion after the model already made tool calls — gated on `output_tokens < 80` AND `tool_calls_made > 0` AND a refusal-marker hit on the lowercase content ("i can't continue", "cannot comply", "i'm sorry", etc.). Distinct from `.output.short` (broad short-output heuristic) and `.termination.shadow` (provider=google thought-trace pattern). Emitted alongside `pipeline.frontier.dispatch.completed`; consumers should retry on a higher-capability model or shorten the write loop. Not gated on `boot_level` — refusal detection runs on persona-free dispatches too. (node-scoped) |
| `pipeline.frontier.dispatch.remotemcp.enabled` | `execution_id`, `agent`, `model`, `model_entity_id`, `provider` | remote-MCP path selected for this execution; adapter attached provider-native MCP descriptor before the native call; implies client-side tool loop disabled (node-scoped) |
| `pipeline.frontier.dispatch.remotemcp.misconfigured` | `execution_id`, `agent`, `model`, `model_entity_id`, `reason` | `resolve_mcp_env()` raised because `MCP_PUBLIC_URL`/`MCP_AUTH_TOKEN` is unset in the Stargate container env; precedes `pipeline_execution_failed`. `model_entity_id` is the canonical Cortex `model:<slug>` — present here (not only on `.started`) so correlators can recover it on the race where misconfigured fires before `.started` (env resolution fails during admission) (node-scoped) |
| `pipeline.frontier.dispatch.started` | `execution_id`, `agent`, `model`, `model_entity_id`, `provider`, `boot_level`, `remote_mcp`, `op`, `endpoint_request_id?` | Fires once per `frontier_dispatch_v1` execution, after hydration (if persona) and before the native call. `model` is the wire/provider-routed id; `model_entity_id` is the canonical Cortex `model:<slug>`. `boot_level` is internal observability vocabulary derived from agent presence, not a caller-facing parameter. `remote_mcp` in the payload is internal observability (card-derived remote-connector selection), not a caller-facing parameter. `op` ∈ {`generate`, `to_thread`, ``}. `endpoint_request_id` joins to endpoint `dispatch.skills.*` rows by `request_id`. MCP callers reach this via `team_dispatch` relay (node-scoped) |
| `pipeline.frontier.dispatch.termination.shadow` | `agent`, `execution_id`, `model`, `provider`, `boot_level`, `output_tokens`, `finish_reason`, `block_reason`, `reason`, `confidence`, `evidence`, `suggested_next_action`, `trace_visibility`, `generate_id`, `detector` | Advisory post-`pipeline.frontier.dispatch.completed` detection of likely silent-termination patterns (refusal / incapacity / policy / scope / loop / token_exhaustion) in the model's reasoning trace. v1 scope: provider=`google` + team-seat dispatch + thought summaries available. `.shadow` topic suffix marks v1 as NOT production-consumable during the calibration window — orchestrators MUST filter on suffix, not on a shadow boolean. Never replaces `.completed`, never fires on `.exhausted`. Replaces the deprecated `mcp.frontier.thought.termination.shadow` signal as of Task-7 Phase 1 (node-scoped) |
| `pipeline.frontier.dispatch.tool.called` | `agent`, `execution_id`, `tool_name`, `turn`, `elapsed_ms`, `provider` | tool executed successfully inside native-endpoint tool-use loop (node-scoped) |
| `pipeline.frontier.dispatch.tool.failed` | `agent`, `execution_id`, `tool_name`, `turn`, `elapsed_ms`, `error`, `provider`, `arguments`, `full_error`, `retry_count` | tool call returned error envelope or raised inside loop; `arguments` / `full_error` / `retry_count` aid observability and deterministic-retry policy (node-scoped) |
| `pipeline.frontier.dispatch.tool.requested` | `agent`, `execution_id`, `tool_name`, `provider`, `tool_call_id` | fired when the model begins generating a `tool_use` block in the streaming response (`content_block_start`), before tool execution; `tool_call_id` correlates with subsequent `.tool.called`/`.tool.failed` events — Anthropic: `content_block.id`; OpenAI/xAI: `item.id` or `item.call_id`; Google: `null` (no native id); emitted by `frontier_dispatch` handler `on_event` callback (node-scoped) |
| `pipeline.frontier.dispatch.tool.suppressed` | `execution_id`, `agent`, `model`, `provider`, `reason` | Emitted when the tool surface is coerced to empty or server-side built-ins are suppressed. Reason vocabulary (from `frontier_tools.py` + live callsites): `capability_tier_inline_only` — role demoted to inline-only substrate (`resolve_dispatch_tool_set`); `mcp_client_tool_loop_unsupported` — model card rejects client-side MCP tools (`admission_checks.py`); `server_tools_knob` — caller set `server_tools=False` while the card carries server-side built-ins (`gen_params.py`). Internal observability field, not caller-facing. (node-scoped) |
| `pipeline.frontier.dispatch.tools.wire` | `execution_id`, `agent`, `model`, `provider`, `hop`, `tools`, `tools_present` | Permanent observability for server-tool wire presence (hop-1 / hop-2). |
| `pipeline.map.completed` | `pipeline_id`, `execution_id`, `step_name`, `succeeded_count`, `failed_count`, `total_count`, `duration_seconds`, `met_threshold` | Event factory for when a map step finishes (success or failure). |
| `pipeline.map.iteration.completed` | `pipeline_id`, `execution_id`, `step_name`, `iteration_index`, `elapsed_seconds`, `inference_seconds`, `prompt_tokens`, `completion_tokens` | Event factory for when one map iteration completes successfully. |
| `pipeline.map.iteration.failed` | `pipeline_id`, `execution_id`, `step_name`, `iteration_index`, `error`, `duration_seconds`, `failure_type`, `truncated_response_path?`, `truncation_tokens?` | Event factory for when one map iteration fails. |
| `pipeline.map.iteration.inference.fallback` | `pipeline_id`, `execution_id`, `step_name`, `iteration_index`, `request_id`, `fallback_signal`, `reason` | Event factory for when fallback inference boundary timing is used. |
| `pipeline.map.iteration.inference.lost` | `pipeline_id`, `execution_id`, `step_name`, `iteration_index`, `request_id` | Event factory for when no inference boundary signal was observed. |
| `pipeline.map.iteration.inference.started` | `pipeline_id`, `execution_id`, `step_name`, `iteration_index`, `request_id`, `model_id`, `queue_wait_seconds` | Event factory for when inference begins for a map iteration. |
| `pipeline.map.iteration.started` | `pipeline_id`, `execution_id`, `step_name`, `iteration_index`, `model_id`, `gateway_id`, `request_id` | `request_id` |
| `pipeline.map.started` | `pipeline_id`, `execution_id`, `step_name`, `total_iterations`, `timeout_seconds`, `threshold` | Event factory for when a map step begins execution. |
| `pipeline.map.step.empty.iterations` | `pipeline_id`, `execution_id`, `step_name` | emitted when map_over resolves to empty collection (0 iterations); no iterations run |
| `pipeline.map.timeout.warning` | `pipeline_id`, `execution_id`, `step_name`, `elapsed_seconds`, `timeout_seconds`, `pending_iterations`, `completed_iterations`, `percent_elapsed` | Event factory for when a map step approaches timeout. |
| `pipeline.model.gate.claimed` | `pipeline_id`, `execution_id`, `step_id`, `model_id` | step acquired model gate; `model_id` is resolved target model identity |
| `pipeline.model.gate.failure.release` | `pipeline_id`, `execution_id`, `step_id`, `model_id`, `error_type` | explicit failure-boundary release marker |
| `pipeline.model.gate.released` | `pipeline_id`, `execution_id`, `step_id`, `model_id`, `outcome` | gate released (`success`\ |
| `pipeline.model.registry.lookup.failed` | `pipeline_id`, `execution_id`, `step_id`, `model_ref`, `error` | model_ref lookup failure |
| `pipeline.rag.coverage.selection.applied` | `pipeline_id`, `execution_id`, `step_name`, `enabled`, `applied`, `chunks_before`, `chunks_after` | Coverage-aware selection outcome after metadata boost scoring (only emitted when coverage selection is enabled) |
| `pipeline.rag.generation.context.refined` | `pipeline_id`, `execution_id`, `step_name`, `predicted_scopes`, `original_must_include`, `enriched_must_include`, `scope_anchors_added`, `flat_hint_count`, `register_scopes_included`, `register_scopes_total` | Emitted after generation context is refined with scope-filtered vocabulary. |
| `pipeline.rag.hints.filtered` | `pipeline_id`, `execution_id`, `step_name`, `query_terms`, `original_hint_count`, `filtered_hint_count`, `filtered_hints`, `fallback`, `scoring_mode`, `min_threshold`, `capped`, `cap_limit` | Emitted after corpus hints are filtered by co-occurrence with query terms. |
| `pipeline.rag.metadata.boost.applied` | `pipeline_id`, `execution_id`, `step_name`, `metadata_hit_count`, `avg_metadata_score`, `applied`, `chunks_after_boost` | Emitted after post-RRF metadata boost is applied (or skipped). |
| `pipeline.rag.neighbor.expansion.applied` | `pipeline_id`, `execution_id`, `step_name`, `enabled`, `neighbors_added`, `neighbors_fetched`, `sources_expanded`, `expansion_n`, `max_chunks`, `expansion_seconds` | Neighbor chunk expansion result — emitted when expansion is enabled, even if zero neighbors were added |
| `pipeline.rag.query.analysis.completed` | `pipeline_id`, `execution_id`, `step_name`, `needs_retrieval`, `scope`, `scope_confidence`, `out_of_scope_reason` | Scope-analysis decision consumed by retrieval |
| `pipeline.rag.query.rewrite.completed` | `pipeline_id`, `execution_id`, `step_name`, `rewrite_count`, `hyde_present` | Rewrite generation completed and available to retrieval |
| `pipeline.rag.query.rewrite.skipped` | `pipeline_id`, `execution_id`, `step_name`, `reason` | Rewrite generation bypassed (`rewrite_disabled`, `needs_retrieval_false`, `step_condition_false`) |
| `pipeline.rag.rerank.completed` | `pipeline_id`, `execution_id`, `step_name`, `rerank_enabled`, `model_id`, `chunks_input`, `chunks_output`, `windows_evaluated`, `max_rank_movement_observed`, `total_rerank_seconds` | Post-reranking: LLM reranking metrics or skip confirmation |
| `pipeline.rag.retrieval.bibliography.filtered` | `pipeline_id`, `execution_id`, `step_name`, `chunks_dropped` | Emitted when post-RRF junk/bibliography filter removes one or more chunks |
| `pipeline.rag.retrieval.completed` | `pipeline_id`, `execution_id`, `step_name`, `predicted_scope`, `scope_confidence`, `fallback_triggered`, `chunks_per_query`, `zero_result_queries`, `rrf_score_min`, `rrf_score_max`, `rrf_score_mean`, `chunks_after_merge`, `total_retrieval_seconds`, `neighbor_expansion_added`, `coverage_bias_applied`, `coverage_bias_query_class`, `coverage_bias_anchor_source`, `coverage_bias_boosted_chunks` | Post-retrieval: scope prediction + quality metrics; coverage-bias fields default when query-class bias is off (`coverage_bias_applied=false`, `coverage_bias_query_class=default`, `coverage_bias_anchor_source=null`, `coverage_bias_boosted_chunks=0`) |
| `pipeline.rag.retrieval.diversity.limited` | `pipeline_id`, `execution_id`, `step_name`, `per_source_limit`, `chunks_dropped`, `chunks_before`, `chunks_after` | Emitted when source-diversity cap removes chunks from a dominant source. |
| `pipeline.rag.retrieval.failed` | `pipeline_id`, `execution_id`, `step_name`, `error`, `total_retrieval_seconds` | All queries failed — no chunks to merge |
| `pipeline.rag.retrieval.params.resolved` | `pipeline_id`, `execution_id`, `step_name`, `consumer_model`, `consumer_tier`, `profile_class`, `max_chunks`, `top_k_per_query`, `rrf_k`, `scope`, `retrieval_mode`, `uses_explicit_prefixes`, `pool_b_enabled` | Pre-retrieval: effective parameters after three-tier merge; `scope` may be string or array of strings (multiscope); `pool_b_enabled` indicates sparse facet/IDF pool (Pool B) active |
| `pipeline.rag.retrieval.skipped` | `pipeline_id`, `execution_id`, `step_name`, `reason`, `out_of_scope_reason` | Retrieval skipped by semantic no-retrieval gate (query/corpus mismatch with no user prefix override) |
| `pipeline.rag.scope.rejected` | `pipeline_id`, `execution_id`, `step_name`, `reason`, `scope`, `details` | Scope validation rejected — fail-closed, 0 chunks returned |
| `pipeline.registry.unavailable` | `pipeline_id`, `missing_models` | Pipeline permanently skipped after deferred retry — model deps unresolvable. |
| `pipeline.started` | `pipeline_id`, `execution_id`, `domain`, `step_count`, `timeout_seconds` | Emitted when pipeline execution begins. |
| `pipeline.step.completed` | `pipeline_id`, `execution_id`, `step_name`, `duration_seconds`, `output_length`, `prompt_tokens`, `completion_tokens`, `model_call_count`, `model_id?`, `exit_code?`, `json_output_keys?`, `cached_tokens?` | `exit_code` (shell steps only) |
| `pipeline.step.condition.evaluated` | `pipeline_id`, `execution_id`, `step_name`, `condition`, `result`, `available_outputs` | Emitted when a step's condition expression is evaluated. |
| `pipeline.step.context.exceeded` | `pipeline_id`, `execution_id`, `step_name`, `model_id`, `estimated_tokens`, `context_length`, `effective_context_per_slot`, `prompt_chars` | Emitted when estimated prompt tokens exceed the model's context window. |
| `pipeline.step.domain.verification.completed` | `execution_id`, `step_id`, `domain`, `model_id`, `statement_count`, `passed_count`, `failed_count`, `duration_ms` | Create PIPELINE_STEP_DOMAIN_VERIFICATION_COMPLETED event. |
| `pipeline.step.domain.verification.started` | `execution_id`, `step_id`, `domain`, `model_id`, `statement_count` | Create PIPELINE_STEP_DOMAIN_VERIFICATION_STARTED event. |
| `pipeline.step.embedding.completed` | `execution_id`, `step_id`, `model_id`, `input_count`, `duration_ms`, `embedding_dim` | Create PIPELINE_STEP_EMBEDDING_COMPLETED event. |
| `pipeline.step.embedding.failed` | `execution_id`, `step_id`, `model_id`, `input_count`, `duration_ms`, `error`, `status_code` | Create PIPELINE_STEP_EMBEDDING_FAILED event. |
| `pipeline.step.embedding.started` | `execution_id`, `step_id`, `model_id`, `input_count` | Create PIPELINE_STEP_EMBEDDING_STARTED event. |
| `pipeline.step.failed` | `pipeline_id`, `execution_id`, `step_name`, `error`, `prompt_tokens`, `completion_tokens`, `model_call_count`, `duration_seconds?`, `traceback?` | `exc_type`: exception class name (e.g. `RemoteProtocolError`); always non-empty, primary diagnostic key when `error` is empty |
| `pipeline.step.model.deferred` | `pipeline_id`, `execution_id`, `step_id`, `model_id`, `reason` | deferral due to model admission gate |
| `pipeline.step.model.fallback` | `pipeline_id`, `execution_id`, `step_name`, `primary_model`, `fallback_model`, `primary_error_type`, `fallback_attempt`, `total_fallbacks`, `succeeded` | Executor-level fallback attempt outcome for eligible failures only |
| `pipeline.step.model.fallback.suppressed` | `pipeline_id`, `execution_id`, `step_name`, `primary_error_type`, `suppression_reason` | Explicit suppression boundary for deterministic local errors |
| `pipeline.step.model.resolved` | `pipeline_id`, `execution_id`, `step_name`, `model_id`, `selection_source` | Emitted immediately after model selection, before inference begins. |
| `pipeline.step.skipped` | `pipeline_id`, `execution_id`, `step_name`, `reason` | Emitted when step is skipped due to condition evaluation. |
| `pipeline.step.started` | `pipeline_id`, `execution_id`, `step_name`, `step_type`, `model_id`, `is_map_step` | Emitted when step execution begins (includes both regular and map steps). |
| `pipeline.subpipeline.expanded` | `pipeline_id`, `execution_id`, `parent_step_name`, `resolved_output_step`, `expanded_step_count` | Emitted when a ``sub_pipeline`` step is expanded into namespaced steps. |
<!-- GENERATED:END region=pipeline -->

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

<!-- GENERATED:START region=consult inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `consult.provenance.recorded` | `todo`, `consult_thread`, `archive_sha256`, `adjudication_assertion_id`, `written_by` | Build the authority-transition event for a todo-keyed provenance write. |
<!-- GENERATED:END region=consult -->

**Correlation**: `call_id` (UUID) links started→finished pairs. `execution_id`
(from `X-Pipeline-Execution-Id` response header) correlates with
`pipeline.step.started`/`pipeline.step.completed` events in
the Event Service for actual model resolution.

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

## mcp.adapter.*

MCP adapter signals track the v1→v2 migration and MCP tool execution visibility.

### Signals

| Signal | When | Required Payload |
|---|---|---|
| `mcp.adapter.v2.configured` | First request where `mcp_v2=true` builds toolset | `provider`, `server_name`, `always_loaded_count`, `deferred_count` |
| `mcp.adapter.request.shape` | Every MCP request (v1 or v2) | `provider`, `model`, `mcp_version`, `tool_count`, `mcp_tool_count`, `has_tool_search` |
| `mcp.adapter.tool.seen` | Response contains `mcp_tool_use` block | `tool_name`, `server_name`, `correlation_id` (optional) |
| `mcp.adapter.search.seen` | Response contains `tool_search_tool_result` | `references_count`, `correlation_id` (optional) |

### Invariants

- `mcp.adapter.v2.configured` emits at most once per adapter instance lifetime
- `mcp.adapter.request.shape` emits exactly once per MCP request
- `mcp.adapter.tool.seen` emits once per `mcp_tool_use` block in the response
  (a single response may have multiple MCP tool calls)
- ∀ `mcp_tool_use` block in response: ¬ mapped to OpenAI `tool_calls` (server-executed)
- ∀ `server_tool_use` block in response: ¬ mapped to OpenAI `tool_calls` (server-executed)

### MCP OAuth Lifecycle

**Purpose**: Tracks OAuth 2.1 authorization bootstrap and bearer-token validation
for the self-hosted MCP server (`services/mcp-server/`).

**INVARIANT**: `mcp.oauth.code.issued` ⟹ eventually (`mcp.oauth.token.issued` ∨ code expiry)

**INVARIANT**: `mcp.oauth.token.exchange.failed` is terminal for one exchange attempt.

**INVARIANT**: `mcp.oauth.token.accepted` ⊕ `mcp.oauth.token.rejected` per presented token.

```text
mcp.oauth.server.started
  └─> mcp.oauth.authorization.validated
      └─> mcp.oauth.code.issued
          └─> mcp.oauth.token.issued | mcp.oauth.token.exchange.failed | mcp.oauth.code.expired
  └─> mcp.oauth.token.accepted
      └─> mcp.request.started(auth_mode="oauth")
          └─> mcp.request.completed | mcp.request.failed
  └─> mcp.oauth.token.rejected (request terminates)
```

<!-- GENERATED:START region=mcp inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `mcp.adapter.request.shape` | `provider`, `model`, `mcp_version`, `tool_count`, `mcp_tool_count`, `has_tool_search` | Every MCP request — shape summary for v1/v2 migration tracking. |
| `mcp.adapter.search.seen` | `references_count`, `correlation_id?` | Response contained a tool_search_tool_result block. |
| `mcp.adapter.tool.seen` | `tool_name`, `server_name`, `correlation_id?` | Response contained an mcp_tool_use block (Anthropic-executed MCP tool). |
| `mcp.adapter.v2.configured` | `provider`, `server_name`, `always_loaded_count`, `deferred_count` | First request with mcp_v2=true built the toolset payload. |
| `mcp.agentbus.advisory.fired` | `advisory`, `turn_kind`, `chars`, `suppressed_by_profile`, `thread`, `subject` | Signal: mcp.agentbus.advisory.fired |
| `mcp.agentbus.branch.associated` | `thread_id`, `branch_name`, `association_id` | Signal: mcp.agentbus.branch.associated |
| `mcp.agentbus.checkpoint.auto_stamp` | `thread`, `turn_number`, `subject` | Signal: mcp.agentbus.checkpoint.auto_stamp |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.orphan.demoted` | `thread_id`, `orphan_turn_id`, `closeout_turn_id` | Signal: mcp.agentbus.dispatch.orphan.demoted |
| `mcp.agentbus.dispatch.orphaned` | `execution_id`, `thread_id`, `pipeline_id`, `linked_at`, `age_s`, `reason` | Signal: mcp.agentbus.dispatch.orphaned |
| `mcp.agentbus.dispatch.thread.reused` | `thread`, `dispatch_thread_id`, `lane`, `is_auto` | Emitted when generate reuses an existing agent-bus thread. |
| `mcp.agentbus.dispatch.thread.split` | `thread`, `dispatch_thread_id`, `lane` | Emitted when generate mints a sibling thread instead of reusing. |
| `mcp.agentbus.lane.bound` | `thread_id`, `parent_thread_id`, `lane_role`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.lane.bound |
| `mcp.agentbus.quiet_with_wip.fired` | `thread`, `seat`, `reason`, `alarm_id`, `wip_execution_ids` | Signal: mcp.agentbus.quiet_with_wip.fired |
| `mcp.agentbus.sidecar.orphaned` | `uri`, `error`, `thread_id?` | Signal: mcp.agentbus.sidecar.orphaned |
| `mcp.agentbus.sidecar.written` | `thread`, `turn_number`, `uri`, `sha256`, `bytes` | Signal: mcp.agentbus.sidecar.written |
| `mcp.agentbus.thread.abandoned` | `thread`, `reason`, `link_count`, `terminal_count`, `delivered_count` | Thread reaped by watchdog after TTL expiry or all-terminal-no-delivery condition. `reason` values: `pending_ttl_exceeded`, `admitted_ttl_exceeded`, `all_terminal_no_delivery`, `tracker_expired` |
| `mcp.agentbus.thread.closed` | `thread`, `via` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.closed` | `thread`, `via?` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.cse.bound` | `thread_id`, `cse_chat_url`, `cse_registration_id`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.thread.cse.bound |
| `mcp.agentbus.thread.lifecycle.transitioned` | `thread`, `from_state`, `to_state`, `trigger` | Thread lifecycle state machine transition. `role=coordination`. `trigger` values: `create`, `admit`, `turn_posted`, `delivery_sent`, `delivery_failed`, `watchdog_reap`, `reopen`. Emitted from `_transition_lifecycle_state` — single point of correctness for all callers |
| `mcp.agentbus.thread.reopened` | `thread`, `from_state`, `to_state` | Emitted alongside `mcp.agentbus.thread.lifecycle.transitioned` when a turn POST transitions a thread out of a terminal state (completed, abandoned, or failed) back to active |
| `mcp.agentbus.turn.body_over_briefing` | `thread`, `from_agent`, `to_agent`, `subject`, `body_chars`, `target_chars` | Turn body exceeded the briefing target (2k) without allow_long_body, a sidecar, or an inline-contract envelope. Advisory only — the turn was inserted unchanged. |
| `mcp.agentbus.turn.created` | `thread`, `turn_id`, `turn_number`, `from_agent`, `to_agent`, `subject`, `created_at` | Turn inserted in agent-bus store (all transport paths) |
| `mcp.agentbus.watchdog.sweep.failed` | `error` | Watchdog sweep pass raised an unhandled exception. Repeated occurrences indicate a persistent failure in the reap path. |
| `mcp.cortex.op.rejected` | `surface`, `family`, `op` | - |
| `mcp.cse.session.acknowledged` | `registration_id`, `ack_class` | Emit when harvest classifies a typed ACK — not on paste. |
| `mcp.cse.session.conflict` | `reason`, `registration_id`, `chat_url` | Emit on self-supersession or other conflict refusal. |
| `mcp.cse.session.harvested` | `registration_id`, `outcome`, `ack_class`, `turn_count` | Emit when harvest completes or reports an incomplete outcome. |
| `mcp.cse.session.pasted` | `registration_id`, `receipt`, `send_verified`, `replayed` | Emit after paste with receipt — never carries ack_class. |
| `mcp.cse.session.resolved` | `registration_id`, `chat_url`, `state` | Emit when identity resolves on a public provenance read. |
<!-- GENERATED:END region=mcp -->

### MCP Request Auth Mode

`mcp.request.started`, `mcp.request.completed`, and `mcp.request.failed` include:

| Field | Type | Description |
|---|---|---|
| `auth_mode` | `"static"` \| `"oauth"` | Which bearer validation path admitted the request |

Additive field — backward compatible for existing consumers.

### MCP Observability: Adapter + OAuth Signal Coordination

With both `mcp.adapter.*` (cloud proxy side) and `mcp.oauth.*` (server side)
signals in place, the full MCP request lifecycle is observable:

```text
mcp.adapter.request.shape (proxy sends request with mcp_v2 toolset)
  → mcp.oauth.token.accepted | mcp.oauth.token.rejected (server authenticates)
    → mcp.adapter.tool.seen (proxy receives server-executed tool block)
      → mcp.adapter.search.seen (proxy sees tool search result)
```

## cloudproxy.mcp.*

Anthropic adapter in universal cloud proxy (`source: "universal-cloud-proxy"` via
event bus debug broadcaster). Join to MCP server `mcp.transport.*` / `mcp.request.*`
using `correlation_id` and timestamp; optional header `X-Cloudproxy-Correlation-Id`
is sent upstream and may appear on MCP ingress when the provider forwards it.

<!-- GENERATED:START region=cloudproxy inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `cloudproxy.mcp.correlation.assigned` | `correlation_id`, `provider` | UUID assigned for one Messages request |
| `cloudproxy.mcp.path.failed` | `correlation_id`, `provider`, `error`, `exc_type` | Exception during forward/stream |
| `cloudproxy.mcp.request.completed` | `correlation_id`, `provider`, `duration_s`, `outcome` | `cancelled`) |
| `cloudproxy.mcp.request.started` | `correlation_id`, `provider`, `model`, `has_mcp_servers`, `streaming` | About to POST to Anthropic |
| `cloudproxy.mcp.stream.cancelled` | `correlation_id`, `provider`, `model`, `duration_s`, `stage`, `reason` | Downstream cancelled/disconnected before stream completion |
| `cloudproxy.mcp.stream.heartbeat` | `correlation_id`, `provider`, `model`, `idle_s` | Idle keepalive emitted while waiting for Anthropic stream output |
<!-- GENERATED:END region=cloudproxy -->

## MCP Server Signals

The internet-facing MCP server (`source: "mcp-server"`) publishes to the
event service over the same `/tmp/universal-protocol/events.sock` socket.

<!-- GENERATED:START region=mcp inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `mcp.adapter.request.shape` | `provider`, `model`, `mcp_version`, `tool_count`, `mcp_tool_count`, `has_tool_search` | Every MCP request — shape summary for v1/v2 migration tracking. |
| `mcp.adapter.search.seen` | `references_count`, `correlation_id?` | Response contained a tool_search_tool_result block. |
| `mcp.adapter.tool.seen` | `tool_name`, `server_name`, `correlation_id?` | Response contained an mcp_tool_use block (Anthropic-executed MCP tool). |
| `mcp.adapter.v2.configured` | `provider`, `server_name`, `always_loaded_count`, `deferred_count` | First request with mcp_v2=true built the toolset payload. |
| `mcp.agentbus.advisory.fired` | `advisory`, `turn_kind`, `chars`, `suppressed_by_profile`, `thread`, `subject` | Signal: mcp.agentbus.advisory.fired |
| `mcp.agentbus.branch.associated` | `thread_id`, `branch_name`, `association_id` | Signal: mcp.agentbus.branch.associated |
| `mcp.agentbus.checkpoint.auto_stamp` | `thread`, `turn_number`, `subject` | Signal: mcp.agentbus.checkpoint.auto_stamp |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.orphan.demoted` | `thread_id`, `orphan_turn_id`, `closeout_turn_id` | Signal: mcp.agentbus.dispatch.orphan.demoted |
| `mcp.agentbus.dispatch.orphaned` | `execution_id`, `thread_id`, `pipeline_id`, `linked_at`, `age_s`, `reason` | Signal: mcp.agentbus.dispatch.orphaned |
| `mcp.agentbus.dispatch.thread.reused` | `thread`, `dispatch_thread_id`, `lane`, `is_auto` | Emitted when generate reuses an existing agent-bus thread. |
| `mcp.agentbus.dispatch.thread.split` | `thread`, `dispatch_thread_id`, `lane` | Emitted when generate mints a sibling thread instead of reusing. |
| `mcp.agentbus.lane.bound` | `thread_id`, `parent_thread_id`, `lane_role`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.lane.bound |
| `mcp.agentbus.quiet_with_wip.fired` | `thread`, `seat`, `reason`, `alarm_id`, `wip_execution_ids` | Signal: mcp.agentbus.quiet_with_wip.fired |
| `mcp.agentbus.sidecar.orphaned` | `uri`, `error`, `thread_id?` | Signal: mcp.agentbus.sidecar.orphaned |
| `mcp.agentbus.sidecar.written` | `thread`, `turn_number`, `uri`, `sha256`, `bytes` | Signal: mcp.agentbus.sidecar.written |
| `mcp.agentbus.thread.abandoned` | `thread`, `reason`, `link_count`, `terminal_count`, `delivered_count` | Thread reaped by watchdog after TTL expiry or all-terminal-no-delivery condition. `reason` values: `pending_ttl_exceeded`, `admitted_ttl_exceeded`, `all_terminal_no_delivery`, `tracker_expired` |
| `mcp.agentbus.thread.closed` | `thread`, `via` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.closed` | `thread`, `via?` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.cse.bound` | `thread_id`, `cse_chat_url`, `cse_registration_id`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.thread.cse.bound |
| `mcp.agentbus.thread.lifecycle.transitioned` | `thread`, `from_state`, `to_state`, `trigger` | Thread lifecycle state machine transition. `role=coordination`. `trigger` values: `create`, `admit`, `turn_posted`, `delivery_sent`, `delivery_failed`, `watchdog_reap`, `reopen`. Emitted from `_transition_lifecycle_state` — single point of correctness for all callers |
| `mcp.agentbus.thread.reopened` | `thread`, `from_state`, `to_state` | Emitted alongside `mcp.agentbus.thread.lifecycle.transitioned` when a turn POST transitions a thread out of a terminal state (completed, abandoned, or failed) back to active |
| `mcp.agentbus.turn.body_over_briefing` | `thread`, `from_agent`, `to_agent`, `subject`, `body_chars`, `target_chars` | Turn body exceeded the briefing target (2k) without allow_long_body, a sidecar, or an inline-contract envelope. Advisory only — the turn was inserted unchanged. |
| `mcp.agentbus.turn.created` | `thread`, `turn_id`, `turn_number`, `from_agent`, `to_agent`, `subject`, `created_at` | Turn inserted in agent-bus store (all transport paths) |
| `mcp.agentbus.watchdog.sweep.failed` | `error` | Watchdog sweep pass raised an unhandled exception. Repeated occurrences indicate a persistent failure in the reap path. |
| `mcp.cortex.op.rejected` | `surface`, `family`, `op` | - |
| `mcp.cse.session.acknowledged` | `registration_id`, `ack_class` | Emit when harvest classifies a typed ACK — not on paste. |
| `mcp.cse.session.conflict` | `reason`, `registration_id`, `chat_url` | Emit on self-supersession or other conflict refusal. |
| `mcp.cse.session.harvested` | `registration_id`, `outcome`, `ack_class`, `turn_count` | Emit when harvest completes or reports an incomplete outcome. |
| `mcp.cse.session.pasted` | `registration_id`, `receipt`, `send_verified`, `replayed` | Emit after paste with receipt — never carries ack_class. |
| `mcp.cse.session.resolved` | `registration_id`, `chat_url`, `state` | Emit when identity resolves on a public provenance read. |
<!-- GENERATED:END region=mcp -->

### Generation Quality Signal Family

`pipeline.frontier.dispatch.output.short` and
`pipeline.frontier.dispatch.termination.shadow` (currently shadow) are
members of a pre-declared **frontier generation-quality** signal family. The
family is a taxonomic label on this doc and a consumer-side query grouping —
it is NOT a literal wire-level prefix. Members previously lived at
`mcp.frontier.*` (see deprecation rows above); Task-7 Phase 1 moved anomaly
signals into the Stargate pipeline handler, and Task-7 Phase 2 collapsed
`frontier_generate` onto that same pipeline path for uniform observability.
Membership criteria (agent-bus thread 576, turn 6):

1. Signal is about whether a completed generation was *substantively* complete,
   not merely token-terminated.
2. Emission is post-hoc (after `pipeline.frontier.dispatch.completed`),
   advisory, and never replaces or blocks completion/exhausted events.
3. Schema includes `detector.mode`, `confidence`, `generate_id`
   (inherited from the upstream generation), and family-consistent correlation
   fields (`agent`, `provider`, `model`, `boot_level`, `finish_reason`, `block_reason`).

Rationale: threads 570, 575, 576 surfaced the same failure-class — event
surface treating `generate.completed` as a proxy for substantive success when
it is not. Future members (e.g. a `coherence_collapse` / high-token-zero-value
signal from thread 575) register here rather than bolt on independently.

Phrase-based detection sunset: the hybrid heuristic is the v1 primary; the
semantic detector is a known gap (API Claude mod #1, thread 576 t5). Sunset
commitment — phrase detection degrades to a secondary weak signal once the
semantic layer ships; target milestone is post-calibration-window close.



### Life Intent Events

Emitted by `libs/life_intent/events.py` for registry check → proposal → commit
lifecycle on the life-domain intent path.

<!-- GENERATED:START region=life inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `life.intent.committed` | `verb`, `proposal_id`, `entity_id`, `dispatch_ref` | life.intent.committed — commit applied and downstream scout fired. |
| `life.intent.proposed` | `verb`, `question_count`, `proposal_id` | life.intent.proposed — work order produced for a valid intent. |
| `life.intent.received` | `verb`, `ref_count`, `context` | life.intent.received — intent received and passed registry envelope check. |
| `life.intent.rejected` | `verb`, `reject_count`, `reject_codes` | life.intent.rejected — refuse-list / vocabulary / ref reject. |
<!-- GENERATED:END region=life -->


### Graph composition signals (imprint write / recall read)

Emitted by `libs/cortex_store/events_imprint.py` and `libs/cortex_store/events_recall.py`.
`graph.recall.*` is the G1 life-recall card surface; `graph.imprint.*` is the sibling write door.

<!-- GENERATED:START region=graph inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `graph.imprint.commit.received` | `proposal_id` | graph.imprint.commit.received — commit request accepted for processing. |
| `graph.imprint.commit.rejected` | `proposal_id`, `reject_codes` | graph.imprint.commit.rejected — typed commit reject. |
| `graph.imprint.committed` | `proposal_id`, `applied_count` | graph.imprint.committed — frozen op_plan fully applied. |
| `graph.imprint.proposed` | `statement_count`, `op_plan_count`, `candidate_count` | graph.imprint.proposed — op plan produced for a shape-valid patch. |
| `graph.imprint.received` | `statement_count`, `context` | graph.imprint.received — patch received and passed initial envelope check. |
| `graph.imprint.rejected` | `statement_count`, `reject_count`, `reject_codes` | graph.imprint.rejected — vocabulary/shape/refused-op reject. |
| `graph.imprint.remember.received` | `statement_count`, `context` | graph.imprint.remember.received — remember request accepted for processing. |
| `graph.imprint.remember.rejected` | `statement_count`, `reject_count`, `reject_codes`, `proposal_id` | graph.imprint.remember.rejected — degraded preview or typed reject. |
| `graph.imprint.remembered` | `proposal_id`, `applied_count`, `deduped`, `already_known` | graph.imprint.remembered — fully-resolved patch auto-committed or deduped. |
| `graph.recall.burst_not_covered` | `mode`, `hub_ids` | graph.recall.burst_not_covered — burst plug-in skipped or vocab miss. |
| `graph.recall.card_served` | `mode`, `resolved_count`, `nulls` | graph.recall.card_served — recall card returned on a successful route. |
| `graph.recall.escalated_to_delegate` | `mode`, `reason` | graph.recall.escalated_to_delegate — advisory _next points at delegate. |
| `graph.recall.resolver_miss` | `mode`, `q_present`, `seed_count` | graph.recall.resolver_miss — no hub resolved and no candidates returned. |
| `graph.recorder.already_known` | `entity_id`, `matched_assertion_id`, `reason`, `anchor` | graph.recorder.already_known — known-state skip on assert/imprint path. |
<!-- GENERATED:END region=graph -->


### Cortex Audit Signals

Emitted by `libs/cortex_store/dispatch_ops/ops_audit.py` and `ops_audit_detectors.py` via `record()` shim. All signals use `role="observation"`, `scope="global"` per existing shim defaults. Introduced in Phase 1b of `todo:cortex-graph-projection-and-audit-primitives`.

<!-- GENERATED:START region=cortex inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `cortex.digest.extract` | `journal_entity_id`, `entry_anchor`, `claim_count` | - |
| `cortex.digest.job.enqueued` | `job_id`, `journal_entity_id`, `entry_anchor`, `kind` | - |
| `cortex.digest.job.harvested` | `job_id`, `archive_uri` | - |
| `cortex.digest.job.parked` | `job_id`, `journal_entity_id`, `entry_anchor`, `reason` | - |
| `cortex.digest.job.parsed` | `job_id`, `claim_count` | - |
| `cortex.digest.job.staged` | `job_id`, `ledger_id`, `staging_batch_id` | - |
| `cortex.digest.job.submitted` | `job_id`, `execution_id` | - |
| `cortex.digest.job.verified` | `job_id`, `claim_count` | - |
| `cortex.digest.run` | `journal_entity_id`, `entry_anchor`, `session_id` | - |
| `cortex.digest.staged` | `journal_entity_id`, `entry_anchor`, `status`, `ledger_id`, `staging_batch_id` | - |
| `cortex.digest.verify` | `journal_entity_id`, `entry_anchor`, `claim_count` | - |
| `cortex.endeavor.audit.finding` | `host`, `tier`, `missing`, `resume_blocking` | - |
| `cortex.endeavor.birth.ack` | `host`, `reason` | - |
| `cortex.endeavor.birth.incomplete` | `host`, `missing`, `resume_blocking`, `ack` | - |
| `cortex.endeavor.lock.blocked` | `host`, `deliverable`, `pending_rows` | - |
| `cortex.endeavor.repaired` | `tier`, `repaired`, `residual`, `applied` | - |
| `cortex.endeavor.row.disposed` | `host`, `row_id`, `pin` | - |
| `cortex.endeavor.row.pending` | `host`, `row_id`, `pin` | - |
| `cortex.endeavor.strategy.pin.missing` | `host`, `row_id` | - |
| `cortex.entity.source.changed` | `entity_id`, `change`, _dynamic_ | Entity `source_uri` set/changed/dropped on create/update. `change` ∈ {`set`, `changed`, `dropped`}. Drives RAG EntityAdmissionGate debounced refresh. role=observation. |
| `cortex.implement.recon.waived` | `todo_id`, `waived_by`, `reason_code`, `reason`, `spec_sha256`, `waived_at`, `stale?`, `stale_reason?` | cortex.implement.recon.waived — audited skeptic-gate recon waiver applied. |
| `cortex.pinned_deliverable.written` | `rel_path`, `dispatch_id`, `thread_id`, `skipped` | cortex.pinned_deliverable.written — emitted when a packet-pinned deliverable is written to cortex (friction 19916). |
| `cortex.search.failed` | `exc_type`, `detail`, `q_len`, `intent` | Search boundary exception before re-raise; names real cause before generic client error. |
| `cortex.search.vector.degraded` | `reason`, `exc_type`, `q_len`, `duration_s` | Vector branch failed; hybrid search degraded to FTS-only. `reason` ∈ {`vector_embed_timeout`, `vector_unavailable`, `vector_error`}. |
| `cortex.skill_graph.drift.alert` | `drift_count`, `stale_edges`, `missing_edges`, `consecutive_dirty_runs`, `thread` | cortex.skill_graph.drift.alert — hysteresis threshold breach. |
| `cortex.skill_graph.drift.checked` | `drift_count`, `stale_edges`, `missing_edges`, `last_clean_ts`, `clean`, `exit_code`, `consecutive_dirty_runs` | cortex.skill_graph.drift.checked — periodic read-only drift metrics. |
| `cortex.skill_graph.drift.sweep.failed` | `error` | cortex.skill_graph.drift.sweep.failed — monitor sweep exception. |
| `cortex.skill_suggest.called` | `suggest_id`, `agent`, `transport`, `context_len`, `context_sha256`, `loaded_count`, `rerank_requested` | cortex.skill_suggest.called — entry telemetry (context hash+len only). |
| `cortex.skill_suggest.completed` | `suggest_id`, `agent`, `candidate_count`, `suggested_count`, `omitted_count`, `ranker_status`, `latency_ms`, `rank_execution_id?` | cortex.skill_suggest.completed — successful suggest path. |
| `cortex.skill_suggest.degraded` | `suggest_id`, `ranker_status`, `degraded_reason`, `latency_ms` | cortex.skill_suggest.degraded — rerank requested but Stage-A returned. |
| `cortex.skill_suggest.failed` | `suggest_id`, `exc_type`, `detail` | cortex.skill_suggest.failed — true endpoint errors (not rerank degrade). |
| `cortex.subgraph.render.called` | `render_id`, `root`, `hops`, `edge_types_count`, `top_k_assertions`, `include_superseded` | Entry to shared renderer (both REST and dispatch paths). `render_id` (uuid4 hex) correlates with `.completed` / `.failed` for the same call. |
| `cortex.subgraph.render.completed` | `render_id`, `root`, `hops`, `entity_count`, `edge_count`, `duration_ms`, `rendered_bytes` | Successful render; `rendered_bytes` is UTF-8 length of markdown (generated_at excluded). |
| `cortex.subgraph.render.failed` | `render_id`, `root`, `reason`, `hops` | Error path inside renderer; emitted before structured envelope return. Reason enum widened from V1.1 spec to field-level granularity. |
| `cortex.subgraph.walk.called` | `walk_id`, `root`, `hops`, `edge_types_count`, `direction`, `entity_cap`, `include_counts`, `promote_hubs` | cortex.subgraph.walk.called — emitted at entry to walk_subgraph. |
| `cortex.subgraph.walk.completed` | `walk_id`, `root`, `hops`, `entity_count`, `edge_count`, `duration_ms`, `envelope_bytes`, `table_bytes` | cortex.subgraph.walk.completed — emitted on successful walk. |
| `cortex.subgraph.walk.failed` | `walk_id`, `root`, `reason`, `hops` | cortex.subgraph.walk.failed — emitted on error paths inside walk_subgraph. |
| `cortex.supersede.would_reject` | `rule_ids`, `derivation_type`, `force`, `valid_from_inherited`, `parent_had_valid_from`, `reject_field_origins`, `mode`, `entity_id` | cortex.supersede.would_reject — durable shadow/hard_422 reject telemetry. |
| `cortex.view.rendered` | `document_id`, `view_rev`, `mode`, `sections_repaired_count`, `delta_create_count`, `delta_update_count`, `delta_delete_count` | cortex.view.rendered — emitted on register/refresh/full view_render. |
<!-- GENERATED:END region=cortex -->

**Implementation note:** `cortex.session.audit.blocked` is the only `coordination`-role signal in this family. The `record()` shim defaults to `role="observation"` — confirm shim supports per-call role override before Phase 2.1 BLOCK-mode flip; if not, surface as a precondition.



### Cortex Entity Identity Signals

Emitted by `entity_rekey_core`, `entity_merge`, and `entity_purge` via `record()` post-commit on identity mutations. All signals: `role="observation"`, `scope="global"`. Not AST-discoverable — documented here, not in GENERATED regions.

| Signal | Payload |
|--------|---------|
| `cortex.entity.rekeyed` | `old_id`, `new_id` |
| `cortex.entity.merged` | `source_id`, `target_id`, `entity_type` |
| `cortex.entity.purged` | `entity_id`, `entity_type`, `actor`, `reason`, `assertions_deleted`, `orphan_sweep` (dict from `purge_fk_orphans`) |



The consolidated `agent_bus(tool=...)` tool emits operation-level signals.
With atomic server-side endpoints, partial-failure and stage signals are
unnecessary — each operation succeeds or fails atomically.

<!-- GENERATED:START region=mcp inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `mcp.adapter.request.shape` | `provider`, `model`, `mcp_version`, `tool_count`, `mcp_tool_count`, `has_tool_search` | Every MCP request — shape summary for v1/v2 migration tracking. |
| `mcp.adapter.search.seen` | `references_count`, `correlation_id?` | Response contained a tool_search_tool_result block. |
| `mcp.adapter.tool.seen` | `tool_name`, `server_name`, `correlation_id?` | Response contained an mcp_tool_use block (Anthropic-executed MCP tool). |
| `mcp.adapter.v2.configured` | `provider`, `server_name`, `always_loaded_count`, `deferred_count` | First request with mcp_v2=true built the toolset payload. |
| `mcp.agentbus.advisory.fired` | `advisory`, `turn_kind`, `chars`, `suppressed_by_profile`, `thread`, `subject` | Signal: mcp.agentbus.advisory.fired |
| `mcp.agentbus.branch.associated` | `thread_id`, `branch_name`, `association_id` | Signal: mcp.agentbus.branch.associated |
| `mcp.agentbus.checkpoint.auto_stamp` | `thread`, `turn_number`, `subject` | Signal: mcp.agentbus.checkpoint.auto_stamp |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.orphan.demoted` | `thread_id`, `orphan_turn_id`, `closeout_turn_id` | Signal: mcp.agentbus.dispatch.orphan.demoted |
| `mcp.agentbus.dispatch.orphaned` | `execution_id`, `thread_id`, `pipeline_id`, `linked_at`, `age_s`, `reason` | Signal: mcp.agentbus.dispatch.orphaned |
| `mcp.agentbus.dispatch.thread.reused` | `thread`, `dispatch_thread_id`, `lane`, `is_auto` | Emitted when generate reuses an existing agent-bus thread. |
| `mcp.agentbus.dispatch.thread.split` | `thread`, `dispatch_thread_id`, `lane` | Emitted when generate mints a sibling thread instead of reusing. |
| `mcp.agentbus.lane.bound` | `thread_id`, `parent_thread_id`, `lane_role`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.lane.bound |
| `mcp.agentbus.quiet_with_wip.fired` | `thread`, `seat`, `reason`, `alarm_id`, `wip_execution_ids` | Signal: mcp.agentbus.quiet_with_wip.fired |
| `mcp.agentbus.sidecar.orphaned` | `uri`, `error`, `thread_id?` | Signal: mcp.agentbus.sidecar.orphaned |
| `mcp.agentbus.sidecar.written` | `thread`, `turn_number`, `uri`, `sha256`, `bytes` | Signal: mcp.agentbus.sidecar.written |
| `mcp.agentbus.thread.abandoned` | `thread`, `reason`, `link_count`, `terminal_count`, `delivered_count` | Thread reaped by watchdog after TTL expiry or all-terminal-no-delivery condition. `reason` values: `pending_ttl_exceeded`, `admitted_ttl_exceeded`, `all_terminal_no_delivery`, `tracker_expired` |
| `mcp.agentbus.thread.closed` | `thread`, `via` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.closed` | `thread`, `via?` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.cse.bound` | `thread_id`, `cse_chat_url`, `cse_registration_id`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.thread.cse.bound |
| `mcp.agentbus.thread.lifecycle.transitioned` | `thread`, `from_state`, `to_state`, `trigger` | Thread lifecycle state machine transition. `role=coordination`. `trigger` values: `create`, `admit`, `turn_posted`, `delivery_sent`, `delivery_failed`, `watchdog_reap`, `reopen`. Emitted from `_transition_lifecycle_state` — single point of correctness for all callers |
| `mcp.agentbus.thread.reopened` | `thread`, `from_state`, `to_state` | Emitted alongside `mcp.agentbus.thread.lifecycle.transitioned` when a turn POST transitions a thread out of a terminal state (completed, abandoned, or failed) back to active |
| `mcp.agentbus.turn.body_over_briefing` | `thread`, `from_agent`, `to_agent`, `subject`, `body_chars`, `target_chars` | Turn body exceeded the briefing target (2k) without allow_long_body, a sidecar, or an inline-contract envelope. Advisory only — the turn was inserted unchanged. |
| `mcp.agentbus.turn.created` | `thread`, `turn_id`, `turn_number`, `from_agent`, `to_agent`, `subject`, `created_at` | Turn inserted in agent-bus store (all transport paths) |
| `mcp.agentbus.watchdog.sweep.failed` | `error` | Watchdog sweep pass raised an unhandled exception. Repeated occurrences indicate a persistent failure in the reap path. |
| `mcp.cortex.op.rejected` | `surface`, `family`, `op` | - |
| `mcp.cse.session.acknowledged` | `registration_id`, `ack_class` | Emit when harvest classifies a typed ACK — not on paste. |
| `mcp.cse.session.conflict` | `reason`, `registration_id`, `chat_url` | Emit on self-supersession or other conflict refusal. |
| `mcp.cse.session.harvested` | `registration_id`, `outcome`, `ack_class`, `turn_count` | Emit when harvest completes or reports an incomplete outcome. |
| `mcp.cse.session.pasted` | `registration_id`, `receipt`, `send_verified`, `replayed` | Emit after paste with receipt — never carries ack_class. |
| `mcp.cse.session.resolved` | `registration_id`, `chat_url`, `state` | Emit when identity resolves on a public provenance read. |
<!-- GENERATED:END region=mcp -->

All signals: `role="observation"`, `scope="global"`.



### Cortex Bulk Write Signals

Emitted by the bulk dispatch ops in `libs/cortex_store/dispatch_ops/ops_bulk_entities.py` and `ops_bulk_relationships.py` via the `record()` shim. Bulk writes are atomic at the transaction boundary — either every item in the batch persists, or none do. All signals: `role="observation"`, `scope="global"`.

<!-- GENERATED:START region=mcp inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `mcp.adapter.request.shape` | `provider`, `model`, `mcp_version`, `tool_count`, `mcp_tool_count`, `has_tool_search` | Every MCP request — shape summary for v1/v2 migration tracking. |
| `mcp.adapter.search.seen` | `references_count`, `correlation_id?` | Response contained a tool_search_tool_result block. |
| `mcp.adapter.tool.seen` | `tool_name`, `server_name`, `correlation_id?` | Response contained an mcp_tool_use block (Anthropic-executed MCP tool). |
| `mcp.adapter.v2.configured` | `provider`, `server_name`, `always_loaded_count`, `deferred_count` | First request with mcp_v2=true built the toolset payload. |
| `mcp.agentbus.advisory.fired` | `advisory`, `turn_kind`, `chars`, `suppressed_by_profile`, `thread`, `subject` | Signal: mcp.agentbus.advisory.fired |
| `mcp.agentbus.branch.associated` | `thread_id`, `branch_name`, `association_id` | Signal: mcp.agentbus.branch.associated |
| `mcp.agentbus.checkpoint.auto_stamp` | `thread`, `turn_number`, `subject` | Signal: mcp.agentbus.checkpoint.auto_stamp |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.orphan.demoted` | `thread_id`, `orphan_turn_id`, `closeout_turn_id` | Signal: mcp.agentbus.dispatch.orphan.demoted |
| `mcp.agentbus.dispatch.orphaned` | `execution_id`, `thread_id`, `pipeline_id`, `linked_at`, `age_s`, `reason` | Signal: mcp.agentbus.dispatch.orphaned |
| `mcp.agentbus.dispatch.thread.reused` | `thread`, `dispatch_thread_id`, `lane`, `is_auto` | Emitted when generate reuses an existing agent-bus thread. |
| `mcp.agentbus.dispatch.thread.split` | `thread`, `dispatch_thread_id`, `lane` | Emitted when generate mints a sibling thread instead of reusing. |
| `mcp.agentbus.lane.bound` | `thread_id`, `parent_thread_id`, `lane_role`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.lane.bound |
| `mcp.agentbus.quiet_with_wip.fired` | `thread`, `seat`, `reason`, `alarm_id`, `wip_execution_ids` | Signal: mcp.agentbus.quiet_with_wip.fired |
| `mcp.agentbus.sidecar.orphaned` | `uri`, `error`, `thread_id?` | Signal: mcp.agentbus.sidecar.orphaned |
| `mcp.agentbus.sidecar.written` | `thread`, `turn_number`, `uri`, `sha256`, `bytes` | Signal: mcp.agentbus.sidecar.written |
| `mcp.agentbus.thread.abandoned` | `thread`, `reason`, `link_count`, `terminal_count`, `delivered_count` | Thread reaped by watchdog after TTL expiry or all-terminal-no-delivery condition. `reason` values: `pending_ttl_exceeded`, `admitted_ttl_exceeded`, `all_terminal_no_delivery`, `tracker_expired` |
| `mcp.agentbus.thread.closed` | `thread`, `via` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.closed` | `thread`, `via?` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.cse.bound` | `thread_id`, `cse_chat_url`, `cse_registration_id`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.thread.cse.bound |
| `mcp.agentbus.thread.lifecycle.transitioned` | `thread`, `from_state`, `to_state`, `trigger` | Thread lifecycle state machine transition. `role=coordination`. `trigger` values: `create`, `admit`, `turn_posted`, `delivery_sent`, `delivery_failed`, `watchdog_reap`, `reopen`. Emitted from `_transition_lifecycle_state` — single point of correctness for all callers |
| `mcp.agentbus.thread.reopened` | `thread`, `from_state`, `to_state` | Emitted alongside `mcp.agentbus.thread.lifecycle.transitioned` when a turn POST transitions a thread out of a terminal state (completed, abandoned, or failed) back to active |
| `mcp.agentbus.turn.body_over_briefing` | `thread`, `from_agent`, `to_agent`, `subject`, `body_chars`, `target_chars` | Turn body exceeded the briefing target (2k) without allow_long_body, a sidecar, or an inline-contract envelope. Advisory only — the turn was inserted unchanged. |
| `mcp.agentbus.turn.created` | `thread`, `turn_id`, `turn_number`, `from_agent`, `to_agent`, `subject`, `created_at` | Turn inserted in agent-bus store (all transport paths) |
| `mcp.agentbus.watchdog.sweep.failed` | `error` | Watchdog sweep pass raised an unhandled exception. Repeated occurrences indicate a persistent failure in the reap path. |
| `mcp.cortex.op.rejected` | `surface`, `family`, `op` | - |
| `mcp.cse.session.acknowledged` | `registration_id`, `ack_class` | Emit when harvest classifies a typed ACK — not on paste. |
| `mcp.cse.session.conflict` | `reason`, `registration_id`, `chat_url` | Emit on self-supersession or other conflict refusal. |
| `mcp.cse.session.harvested` | `registration_id`, `outcome`, `ack_class`, `turn_count` | Emit when harvest completes or reports an incomplete outcome. |
| `mcp.cse.session.pasted` | `registration_id`, `receipt`, `send_verified`, `replayed` | Emit after paste with receipt — never carries ack_class. |
| `mcp.cse.session.resolved` | `registration_id`, `chat_url`, `state` | Emit when identity resolves on a public provenance read. |
<!-- GENERATED:END region=mcp -->

### Cortex Relay Signals

Emitted by `services/mcp-server/tools/_cortex_relay.py::cx` via bare `mcp_events.record()` (mirrors `relay()` idiom). Makes every cortex-api REST relay observable. A `mcp.cortex.relay.called` with no terminal sibling within the relay budget indicates a connector-side abort. All signals: `role="observation"`, `scope="global"`.

<!-- GENERATED:START region=mcp inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `mcp.adapter.request.shape` | `provider`, `model`, `mcp_version`, `tool_count`, `mcp_tool_count`, `has_tool_search` | Every MCP request — shape summary for v1/v2 migration tracking. |
| `mcp.adapter.search.seen` | `references_count`, `correlation_id?` | Response contained a tool_search_tool_result block. |
| `mcp.adapter.tool.seen` | `tool_name`, `server_name`, `correlation_id?` | Response contained an mcp_tool_use block (Anthropic-executed MCP tool). |
| `mcp.adapter.v2.configured` | `provider`, `server_name`, `always_loaded_count`, `deferred_count` | First request with mcp_v2=true built the toolset payload. |
| `mcp.agentbus.advisory.fired` | `advisory`, `turn_kind`, `chars`, `suppressed_by_profile`, `thread`, `subject` | Signal: mcp.agentbus.advisory.fired |
| `mcp.agentbus.branch.associated` | `thread_id`, `branch_name`, `association_id` | Signal: mcp.agentbus.branch.associated |
| `mcp.agentbus.checkpoint.auto_stamp` | `thread`, `turn_number`, `subject` | Signal: mcp.agentbus.checkpoint.auto_stamp |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.orphan.demoted` | `thread_id`, `orphan_turn_id`, `closeout_turn_id` | Signal: mcp.agentbus.dispatch.orphan.demoted |
| `mcp.agentbus.dispatch.orphaned` | `execution_id`, `thread_id`, `pipeline_id`, `linked_at`, `age_s`, `reason` | Signal: mcp.agentbus.dispatch.orphaned |
| `mcp.agentbus.dispatch.thread.reused` | `thread`, `dispatch_thread_id`, `lane`, `is_auto` | Emitted when generate reuses an existing agent-bus thread. |
| `mcp.agentbus.dispatch.thread.split` | `thread`, `dispatch_thread_id`, `lane` | Emitted when generate mints a sibling thread instead of reusing. |
| `mcp.agentbus.lane.bound` | `thread_id`, `parent_thread_id`, `lane_role`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.lane.bound |
| `mcp.agentbus.quiet_with_wip.fired` | `thread`, `seat`, `reason`, `alarm_id`, `wip_execution_ids` | Signal: mcp.agentbus.quiet_with_wip.fired |
| `mcp.agentbus.sidecar.orphaned` | `uri`, `error`, `thread_id?` | Signal: mcp.agentbus.sidecar.orphaned |
| `mcp.agentbus.sidecar.written` | `thread`, `turn_number`, `uri`, `sha256`, `bytes` | Signal: mcp.agentbus.sidecar.written |
| `mcp.agentbus.thread.abandoned` | `thread`, `reason`, `link_count`, `terminal_count`, `delivered_count` | Thread reaped by watchdog after TTL expiry or all-terminal-no-delivery condition. `reason` values: `pending_ttl_exceeded`, `admitted_ttl_exceeded`, `all_terminal_no_delivery`, `tracker_expired` |
| `mcp.agentbus.thread.closed` | `thread`, `via` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.closed` | `thread`, `via?` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.cse.bound` | `thread_id`, `cse_chat_url`, `cse_registration_id`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.thread.cse.bound |
| `mcp.agentbus.thread.lifecycle.transitioned` | `thread`, `from_state`, `to_state`, `trigger` | Thread lifecycle state machine transition. `role=coordination`. `trigger` values: `create`, `admit`, `turn_posted`, `delivery_sent`, `delivery_failed`, `watchdog_reap`, `reopen`. Emitted from `_transition_lifecycle_state` — single point of correctness for all callers |
| `mcp.agentbus.thread.reopened` | `thread`, `from_state`, `to_state` | Emitted alongside `mcp.agentbus.thread.lifecycle.transitioned` when a turn POST transitions a thread out of a terminal state (completed, abandoned, or failed) back to active |
| `mcp.agentbus.turn.body_over_briefing` | `thread`, `from_agent`, `to_agent`, `subject`, `body_chars`, `target_chars` | Turn body exceeded the briefing target (2k) without allow_long_body, a sidecar, or an inline-contract envelope. Advisory only — the turn was inserted unchanged. |
| `mcp.agentbus.turn.created` | `thread`, `turn_id`, `turn_number`, `from_agent`, `to_agent`, `subject`, `created_at` | Turn inserted in agent-bus store (all transport paths) |
| `mcp.agentbus.watchdog.sweep.failed` | `error` | Watchdog sweep pass raised an unhandled exception. Repeated occurrences indicate a persistent failure in the reap path. |
| `mcp.cortex.op.rejected` | `surface`, `family`, `op` | - |
| `mcp.cse.session.acknowledged` | `registration_id`, `ack_class` | Emit when harvest classifies a typed ACK — not on paste. |
| `mcp.cse.session.conflict` | `reason`, `registration_id`, `chat_url` | Emit on self-supersession or other conflict refusal. |
| `mcp.cse.session.harvested` | `registration_id`, `outcome`, `ack_class`, `turn_count` | Emit when harvest completes or reports an incomplete outcome. |
| `mcp.cse.session.pasted` | `registration_id`, `receipt`, `send_verified`, `replayed` | Emit after paste with receipt — never carries ack_class. |
| `mcp.cse.session.resolved` | `registration_id`, `chat_url`, `state` | Emit when identity resolves on a public provenance read. |
<!-- GENERATED:END region=mcp -->

### Email Bridge Ingest Signals

Emitted by the `email-bridge` host-process satellite during `POST /ingest`.
These signals make MCP relay timeouts diagnosable after the client-side 30s
deadline: if `mcp.local.api.failed` reports `/ingest` timeout, the
`email.ingest.*` and `email.pipeline.*` events show which message and stage was
still running or failed. All signals: `role="observation"`, `scope="global"`.

<!-- GENERATED:START region=email inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|

<!-- GENERATED:END region=email -->


### Document OCR Signals

Single-file OCR events (``mcp.document.ocr.called``, ``mcp.document.ocr.completed``)
are emitted by ``libs/cortex_store/routes/documents.py`` when the
``POST /documents/ocr/file`` endpoint runs. Extraction sidecars use a separate
family: ``mcp.document.extract.*`` from ``services/mcp-server/tools/extract_document.py``.
Directory-batch events (``mcp.document.ocr.directory.*``, ``mcp.document.ocr.file.failed``)
are emitted by ``libs/cortex_store/routes/documents.py`` — the cortex-api endpoint
the MCP ``extract_directory`` tool relays to. All signals:
``role="observation"``, ``scope="global"``.

<!-- GENERATED:START region=mcp inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `mcp.adapter.request.shape` | `provider`, `model`, `mcp_version`, `tool_count`, `mcp_tool_count`, `has_tool_search` | Every MCP request — shape summary for v1/v2 migration tracking. |
| `mcp.adapter.search.seen` | `references_count`, `correlation_id?` | Response contained a tool_search_tool_result block. |
| `mcp.adapter.tool.seen` | `tool_name`, `server_name`, `correlation_id?` | Response contained an mcp_tool_use block (Anthropic-executed MCP tool). |
| `mcp.adapter.v2.configured` | `provider`, `server_name`, `always_loaded_count`, `deferred_count` | First request with mcp_v2=true built the toolset payload. |
| `mcp.agentbus.advisory.fired` | `advisory`, `turn_kind`, `chars`, `suppressed_by_profile`, `thread`, `subject` | Signal: mcp.agentbus.advisory.fired |
| `mcp.agentbus.branch.associated` | `thread_id`, `branch_name`, `association_id` | Signal: mcp.agentbus.branch.associated |
| `mcp.agentbus.checkpoint.auto_stamp` | `thread`, `turn_number`, `subject` | Signal: mcp.agentbus.checkpoint.auto_stamp |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.orphan.demoted` | `thread_id`, `orphan_turn_id`, `closeout_turn_id` | Signal: mcp.agentbus.dispatch.orphan.demoted |
| `mcp.agentbus.dispatch.orphaned` | `execution_id`, `thread_id`, `pipeline_id`, `linked_at`, `age_s`, `reason` | Signal: mcp.agentbus.dispatch.orphaned |
| `mcp.agentbus.dispatch.thread.reused` | `thread`, `dispatch_thread_id`, `lane`, `is_auto` | Emitted when generate reuses an existing agent-bus thread. |
| `mcp.agentbus.dispatch.thread.split` | `thread`, `dispatch_thread_id`, `lane` | Emitted when generate mints a sibling thread instead of reusing. |
| `mcp.agentbus.lane.bound` | `thread_id`, `parent_thread_id`, `lane_role`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.lane.bound |
| `mcp.agentbus.quiet_with_wip.fired` | `thread`, `seat`, `reason`, `alarm_id`, `wip_execution_ids` | Signal: mcp.agentbus.quiet_with_wip.fired |
| `mcp.agentbus.sidecar.orphaned` | `uri`, `error`, `thread_id?` | Signal: mcp.agentbus.sidecar.orphaned |
| `mcp.agentbus.sidecar.written` | `thread`, `turn_number`, `uri`, `sha256`, `bytes` | Signal: mcp.agentbus.sidecar.written |
| `mcp.agentbus.thread.abandoned` | `thread`, `reason`, `link_count`, `terminal_count`, `delivered_count` | Thread reaped by watchdog after TTL expiry or all-terminal-no-delivery condition. `reason` values: `pending_ttl_exceeded`, `admitted_ttl_exceeded`, `all_terminal_no_delivery`, `tracker_expired` |
| `mcp.agentbus.thread.closed` | `thread`, `via` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.closed` | `thread`, `via?` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.cse.bound` | `thread_id`, `cse_chat_url`, `cse_registration_id`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.thread.cse.bound |
| `mcp.agentbus.thread.lifecycle.transitioned` | `thread`, `from_state`, `to_state`, `trigger` | Thread lifecycle state machine transition. `role=coordination`. `trigger` values: `create`, `admit`, `turn_posted`, `delivery_sent`, `delivery_failed`, `watchdog_reap`, `reopen`. Emitted from `_transition_lifecycle_state` — single point of correctness for all callers |
| `mcp.agentbus.thread.reopened` | `thread`, `from_state`, `to_state` | Emitted alongside `mcp.agentbus.thread.lifecycle.transitioned` when a turn POST transitions a thread out of a terminal state (completed, abandoned, or failed) back to active |
| `mcp.agentbus.turn.body_over_briefing` | `thread`, `from_agent`, `to_agent`, `subject`, `body_chars`, `target_chars` | Turn body exceeded the briefing target (2k) without allow_long_body, a sidecar, or an inline-contract envelope. Advisory only — the turn was inserted unchanged. |
| `mcp.agentbus.turn.created` | `thread`, `turn_id`, `turn_number`, `from_agent`, `to_agent`, `subject`, `created_at` | Turn inserted in agent-bus store (all transport paths) |
| `mcp.agentbus.watchdog.sweep.failed` | `error` | Watchdog sweep pass raised an unhandled exception. Repeated occurrences indicate a persistent failure in the reap path. |
| `mcp.cortex.op.rejected` | `surface`, `family`, `op` | - |
| `mcp.cse.session.acknowledged` | `registration_id`, `ack_class` | Emit when harvest classifies a typed ACK — not on paste. |
| `mcp.cse.session.conflict` | `reason`, `registration_id`, `chat_url` | Emit on self-supersession or other conflict refusal. |
| `mcp.cse.session.harvested` | `registration_id`, `outcome`, `ack_class`, `turn_count` | Emit when harvest completes or reports an incomplete outcome. |
| `mcp.cse.session.pasted` | `registration_id`, `receipt`, `send_verified`, `replayed` | Emit after paste with receipt — never carries ack_class. |
| `mcp.cse.session.resolved` | `registration_id`, `chat_url`, `state` | Emit when identity resolves on a public provenance read. |
<!-- GENERATED:END region=mcp -->

### Document Extraction Signals

Emitted by ``services/mcp-server/tools/extract_document.py`` (the ``extract_document``
MCP tool, renamed from ``ingest_document`` in phase-c). All signals:
``role="observation"``, ``scope="global"``.

<!-- GENERATED:START region=mcp inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `mcp.adapter.request.shape` | `provider`, `model`, `mcp_version`, `tool_count`, `mcp_tool_count`, `has_tool_search` | Every MCP request — shape summary for v1/v2 migration tracking. |
| `mcp.adapter.search.seen` | `references_count`, `correlation_id?` | Response contained a tool_search_tool_result block. |
| `mcp.adapter.tool.seen` | `tool_name`, `server_name`, `correlation_id?` | Response contained an mcp_tool_use block (Anthropic-executed MCP tool). |
| `mcp.adapter.v2.configured` | `provider`, `server_name`, `always_loaded_count`, `deferred_count` | First request with mcp_v2=true built the toolset payload. |
| `mcp.agentbus.advisory.fired` | `advisory`, `turn_kind`, `chars`, `suppressed_by_profile`, `thread`, `subject` | Signal: mcp.agentbus.advisory.fired |
| `mcp.agentbus.branch.associated` | `thread_id`, `branch_name`, `association_id` | Signal: mcp.agentbus.branch.associated |
| `mcp.agentbus.checkpoint.auto_stamp` | `thread`, `turn_number`, `subject` | Signal: mcp.agentbus.checkpoint.auto_stamp |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.orphan.demoted` | `thread_id`, `orphan_turn_id`, `closeout_turn_id` | Signal: mcp.agentbus.dispatch.orphan.demoted |
| `mcp.agentbus.dispatch.orphaned` | `execution_id`, `thread_id`, `pipeline_id`, `linked_at`, `age_s`, `reason` | Signal: mcp.agentbus.dispatch.orphaned |
| `mcp.agentbus.dispatch.thread.reused` | `thread`, `dispatch_thread_id`, `lane`, `is_auto` | Emitted when generate reuses an existing agent-bus thread. |
| `mcp.agentbus.dispatch.thread.split` | `thread`, `dispatch_thread_id`, `lane` | Emitted when generate mints a sibling thread instead of reusing. |
| `mcp.agentbus.lane.bound` | `thread_id`, `parent_thread_id`, `lane_role`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.lane.bound |
| `mcp.agentbus.quiet_with_wip.fired` | `thread`, `seat`, `reason`, `alarm_id`, `wip_execution_ids` | Signal: mcp.agentbus.quiet_with_wip.fired |
| `mcp.agentbus.sidecar.orphaned` | `uri`, `error`, `thread_id?` | Signal: mcp.agentbus.sidecar.orphaned |
| `mcp.agentbus.sidecar.written` | `thread`, `turn_number`, `uri`, `sha256`, `bytes` | Signal: mcp.agentbus.sidecar.written |
| `mcp.agentbus.thread.abandoned` | `thread`, `reason`, `link_count`, `terminal_count`, `delivered_count` | Thread reaped by watchdog after TTL expiry or all-terminal-no-delivery condition. `reason` values: `pending_ttl_exceeded`, `admitted_ttl_exceeded`, `all_terminal_no_delivery`, `tracker_expired` |
| `mcp.agentbus.thread.closed` | `thread`, `via` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.closed` | `thread`, `via?` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.cse.bound` | `thread_id`, `cse_chat_url`, `cse_registration_id`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.thread.cse.bound |
| `mcp.agentbus.thread.lifecycle.transitioned` | `thread`, `from_state`, `to_state`, `trigger` | Thread lifecycle state machine transition. `role=coordination`. `trigger` values: `create`, `admit`, `turn_posted`, `delivery_sent`, `delivery_failed`, `watchdog_reap`, `reopen`. Emitted from `_transition_lifecycle_state` — single point of correctness for all callers |
| `mcp.agentbus.thread.reopened` | `thread`, `from_state`, `to_state` | Emitted alongside `mcp.agentbus.thread.lifecycle.transitioned` when a turn POST transitions a thread out of a terminal state (completed, abandoned, or failed) back to active |
| `mcp.agentbus.turn.body_over_briefing` | `thread`, `from_agent`, `to_agent`, `subject`, `body_chars`, `target_chars` | Turn body exceeded the briefing target (2k) without allow_long_body, a sidecar, or an inline-contract envelope. Advisory only — the turn was inserted unchanged. |
| `mcp.agentbus.turn.created` | `thread`, `turn_id`, `turn_number`, `from_agent`, `to_agent`, `subject`, `created_at` | Turn inserted in agent-bus store (all transport paths) |
| `mcp.agentbus.watchdog.sweep.failed` | `error` | Watchdog sweep pass raised an unhandled exception. Repeated occurrences indicate a persistent failure in the reap path. |
| `mcp.cortex.op.rejected` | `surface`, `family`, `op` | - |
| `mcp.cse.session.acknowledged` | `registration_id`, `ack_class` | Emit when harvest classifies a typed ACK — not on paste. |
| `mcp.cse.session.conflict` | `reason`, `registration_id`, `chat_url` | Emit on self-supersession or other conflict refusal. |
| `mcp.cse.session.harvested` | `registration_id`, `outcome`, `ack_class`, `turn_count` | Emit when harvest completes or reports an incomplete outcome. |
| `mcp.cse.session.pasted` | `registration_id`, `receipt`, `send_verified`, `replayed` | Emit after paste with receipt — never carries ack_class. |
| `mcp.cse.session.resolved` | `registration_id`, `chat_url`, `state` | Emit when identity resolves on a public provenance read. |
<!-- GENERATED:END region=mcp -->

### Structured Document Extraction Signals

Emitted by ``services/mcp-server/tools/local/extract_document_structured.py``.
All signals: ``role="observation"``, ``scope="global"``.

<!-- GENERATED:START region=mcp inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `mcp.adapter.request.shape` | `provider`, `model`, `mcp_version`, `tool_count`, `mcp_tool_count`, `has_tool_search` | Every MCP request — shape summary for v1/v2 migration tracking. |
| `mcp.adapter.search.seen` | `references_count`, `correlation_id?` | Response contained a tool_search_tool_result block. |
| `mcp.adapter.tool.seen` | `tool_name`, `server_name`, `correlation_id?` | Response contained an mcp_tool_use block (Anthropic-executed MCP tool). |
| `mcp.adapter.v2.configured` | `provider`, `server_name`, `always_loaded_count`, `deferred_count` | First request with mcp_v2=true built the toolset payload. |
| `mcp.agentbus.advisory.fired` | `advisory`, `turn_kind`, `chars`, `suppressed_by_profile`, `thread`, `subject` | Signal: mcp.agentbus.advisory.fired |
| `mcp.agentbus.branch.associated` | `thread_id`, `branch_name`, `association_id` | Signal: mcp.agentbus.branch.associated |
| `mcp.agentbus.checkpoint.auto_stamp` | `thread`, `turn_number`, `subject` | Signal: mcp.agentbus.checkpoint.auto_stamp |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.orphan.demoted` | `thread_id`, `orphan_turn_id`, `closeout_turn_id` | Signal: mcp.agentbus.dispatch.orphan.demoted |
| `mcp.agentbus.dispatch.orphaned` | `execution_id`, `thread_id`, `pipeline_id`, `linked_at`, `age_s`, `reason` | Signal: mcp.agentbus.dispatch.orphaned |
| `mcp.agentbus.dispatch.thread.reused` | `thread`, `dispatch_thread_id`, `lane`, `is_auto` | Emitted when generate reuses an existing agent-bus thread. |
| `mcp.agentbus.dispatch.thread.split` | `thread`, `dispatch_thread_id`, `lane` | Emitted when generate mints a sibling thread instead of reusing. |
| `mcp.agentbus.lane.bound` | `thread_id`, `parent_thread_id`, `lane_role`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.lane.bound |
| `mcp.agentbus.quiet_with_wip.fired` | `thread`, `seat`, `reason`, `alarm_id`, `wip_execution_ids` | Signal: mcp.agentbus.quiet_with_wip.fired |
| `mcp.agentbus.sidecar.orphaned` | `uri`, `error`, `thread_id?` | Signal: mcp.agentbus.sidecar.orphaned |
| `mcp.agentbus.sidecar.written` | `thread`, `turn_number`, `uri`, `sha256`, `bytes` | Signal: mcp.agentbus.sidecar.written |
| `mcp.agentbus.thread.abandoned` | `thread`, `reason`, `link_count`, `terminal_count`, `delivered_count` | Thread reaped by watchdog after TTL expiry or all-terminal-no-delivery condition. `reason` values: `pending_ttl_exceeded`, `admitted_ttl_exceeded`, `all_terminal_no_delivery`, `tracker_expired` |
| `mcp.agentbus.thread.closed` | `thread`, `via` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.closed` | `thread`, `via?` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.cse.bound` | `thread_id`, `cse_chat_url`, `cse_registration_id`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.thread.cse.bound |
| `mcp.agentbus.thread.lifecycle.transitioned` | `thread`, `from_state`, `to_state`, `trigger` | Thread lifecycle state machine transition. `role=coordination`. `trigger` values: `create`, `admit`, `turn_posted`, `delivery_sent`, `delivery_failed`, `watchdog_reap`, `reopen`. Emitted from `_transition_lifecycle_state` — single point of correctness for all callers |
| `mcp.agentbus.thread.reopened` | `thread`, `from_state`, `to_state` | Emitted alongside `mcp.agentbus.thread.lifecycle.transitioned` when a turn POST transitions a thread out of a terminal state (completed, abandoned, or failed) back to active |
| `mcp.agentbus.turn.body_over_briefing` | `thread`, `from_agent`, `to_agent`, `subject`, `body_chars`, `target_chars` | Turn body exceeded the briefing target (2k) without allow_long_body, a sidecar, or an inline-contract envelope. Advisory only — the turn was inserted unchanged. |
| `mcp.agentbus.turn.created` | `thread`, `turn_id`, `turn_number`, `from_agent`, `to_agent`, `subject`, `created_at` | Turn inserted in agent-bus store (all transport paths) |
| `mcp.agentbus.watchdog.sweep.failed` | `error` | Watchdog sweep pass raised an unhandled exception. Repeated occurrences indicate a persistent failure in the reap path. |
| `mcp.cortex.op.rejected` | `surface`, `family`, `op` | - |
| `mcp.cse.session.acknowledged` | `registration_id`, `ack_class` | Emit when harvest classifies a typed ACK — not on paste. |
| `mcp.cse.session.conflict` | `reason`, `registration_id`, `chat_url` | Emit on self-supersession or other conflict refusal. |
| `mcp.cse.session.harvested` | `registration_id`, `outcome`, `ack_class`, `turn_count` | Emit when harvest completes or reports an incomplete outcome. |
| `mcp.cse.session.pasted` | `registration_id`, `receipt`, `send_verified`, `replayed` | Emit after paste with receipt — never carries ack_class. |
| `mcp.cse.session.resolved` | `registration_id`, `chat_url`, `state` | Emit when identity resolves on a public provenance read. |
<!-- GENERATED:END region=mcp -->

**Documentation Contract Audit (2026-05-24, phase-f):** Added structured extraction rows above; retired `mcp.document.ocr.structured.*` vocabulary (phase-e rename to `mcp.document.extract.structured.*`).


### Document Evidence Signals

Emitted by ``services/mcp-server/tools/promote_document_to_evidence.py`` (the
``promote_document_to_evidence`` MCP tool, phase-d of the document ingestion
redesign). All signals: ``role="observation"``, ``scope="global"``.

<!-- GENERATED:START region=mcp inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `mcp.adapter.request.shape` | `provider`, `model`, `mcp_version`, `tool_count`, `mcp_tool_count`, `has_tool_search` | Every MCP request — shape summary for v1/v2 migration tracking. |
| `mcp.adapter.search.seen` | `references_count`, `correlation_id?` | Response contained a tool_search_tool_result block. |
| `mcp.adapter.tool.seen` | `tool_name`, `server_name`, `correlation_id?` | Response contained an mcp_tool_use block (Anthropic-executed MCP tool). |
| `mcp.adapter.v2.configured` | `provider`, `server_name`, `always_loaded_count`, `deferred_count` | First request with mcp_v2=true built the toolset payload. |
| `mcp.agentbus.advisory.fired` | `advisory`, `turn_kind`, `chars`, `suppressed_by_profile`, `thread`, `subject` | Signal: mcp.agentbus.advisory.fired |
| `mcp.agentbus.branch.associated` | `thread_id`, `branch_name`, `association_id` | Signal: mcp.agentbus.branch.associated |
| `mcp.agentbus.checkpoint.auto_stamp` | `thread`, `turn_number`, `subject` | Signal: mcp.agentbus.checkpoint.auto_stamp |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.orphan.demoted` | `thread_id`, `orphan_turn_id`, `closeout_turn_id` | Signal: mcp.agentbus.dispatch.orphan.demoted |
| `mcp.agentbus.dispatch.orphaned` | `execution_id`, `thread_id`, `pipeline_id`, `linked_at`, `age_s`, `reason` | Signal: mcp.agentbus.dispatch.orphaned |
| `mcp.agentbus.dispatch.thread.reused` | `thread`, `dispatch_thread_id`, `lane`, `is_auto` | Emitted when generate reuses an existing agent-bus thread. |
| `mcp.agentbus.dispatch.thread.split` | `thread`, `dispatch_thread_id`, `lane` | Emitted when generate mints a sibling thread instead of reusing. |
| `mcp.agentbus.lane.bound` | `thread_id`, `parent_thread_id`, `lane_role`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.lane.bound |
| `mcp.agentbus.quiet_with_wip.fired` | `thread`, `seat`, `reason`, `alarm_id`, `wip_execution_ids` | Signal: mcp.agentbus.quiet_with_wip.fired |
| `mcp.agentbus.sidecar.orphaned` | `uri`, `error`, `thread_id?` | Signal: mcp.agentbus.sidecar.orphaned |
| `mcp.agentbus.sidecar.written` | `thread`, `turn_number`, `uri`, `sha256`, `bytes` | Signal: mcp.agentbus.sidecar.written |
| `mcp.agentbus.thread.abandoned` | `thread`, `reason`, `link_count`, `terminal_count`, `delivered_count` | Thread reaped by watchdog after TTL expiry or all-terminal-no-delivery condition. `reason` values: `pending_ttl_exceeded`, `admitted_ttl_exceeded`, `all_terminal_no_delivery`, `tracker_expired` |
| `mcp.agentbus.thread.closed` | `thread`, `via` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.closed` | `thread`, `via?` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.cse.bound` | `thread_id`, `cse_chat_url`, `cse_registration_id`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.thread.cse.bound |
| `mcp.agentbus.thread.lifecycle.transitioned` | `thread`, `from_state`, `to_state`, `trigger` | Thread lifecycle state machine transition. `role=coordination`. `trigger` values: `create`, `admit`, `turn_posted`, `delivery_sent`, `delivery_failed`, `watchdog_reap`, `reopen`. Emitted from `_transition_lifecycle_state` — single point of correctness for all callers |
| `mcp.agentbus.thread.reopened` | `thread`, `from_state`, `to_state` | Emitted alongside `mcp.agentbus.thread.lifecycle.transitioned` when a turn POST transitions a thread out of a terminal state (completed, abandoned, or failed) back to active |
| `mcp.agentbus.turn.body_over_briefing` | `thread`, `from_agent`, `to_agent`, `subject`, `body_chars`, `target_chars` | Turn body exceeded the briefing target (2k) without allow_long_body, a sidecar, or an inline-contract envelope. Advisory only — the turn was inserted unchanged. |
| `mcp.agentbus.turn.created` | `thread`, `turn_id`, `turn_number`, `from_agent`, `to_agent`, `subject`, `created_at` | Turn inserted in agent-bus store (all transport paths) |
| `mcp.agentbus.watchdog.sweep.failed` | `error` | Watchdog sweep pass raised an unhandled exception. Repeated occurrences indicate a persistent failure in the reap path. |
| `mcp.cortex.op.rejected` | `surface`, `family`, `op` | - |
| `mcp.cse.session.acknowledged` | `registration_id`, `ack_class` | Emit when harvest classifies a typed ACK — not on paste. |
| `mcp.cse.session.conflict` | `reason`, `registration_id`, `chat_url` | Emit on self-supersession or other conflict refusal. |
| `mcp.cse.session.harvested` | `registration_id`, `outcome`, `ack_class`, `turn_count` | Emit when harvest completes or reports an incomplete outcome. |
| `mcp.cse.session.pasted` | `registration_id`, `receipt`, `send_verified`, `replayed` | Emit after paste with receipt — never carries ack_class. |
| `mcp.cse.session.resolved` | `registration_id`, `chat_url`, `state` | Emit when identity resolves on a public provenance read. |
<!-- GENERATED:END region=mcp -->

### Cortex Session Close Signals

Emitted by `libs/cortex_store/dispatch_ops/ops_journals.py` · `_op_session_close` via `record()`. All signals: `role="observation"`, `scope="global"`.

<!-- GENERATED:START region=cortex inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `cortex.digest.extract` | `journal_entity_id`, `entry_anchor`, `claim_count` | - |
| `cortex.digest.job.enqueued` | `job_id`, `journal_entity_id`, `entry_anchor`, `kind` | - |
| `cortex.digest.job.harvested` | `job_id`, `archive_uri` | - |
| `cortex.digest.job.parked` | `job_id`, `journal_entity_id`, `entry_anchor`, `reason` | - |
| `cortex.digest.job.parsed` | `job_id`, `claim_count` | - |
| `cortex.digest.job.staged` | `job_id`, `ledger_id`, `staging_batch_id` | - |
| `cortex.digest.job.submitted` | `job_id`, `execution_id` | - |
| `cortex.digest.job.verified` | `job_id`, `claim_count` | - |
| `cortex.digest.run` | `journal_entity_id`, `entry_anchor`, `session_id` | - |
| `cortex.digest.staged` | `journal_entity_id`, `entry_anchor`, `status`, `ledger_id`, `staging_batch_id` | - |
| `cortex.digest.verify` | `journal_entity_id`, `entry_anchor`, `claim_count` | - |
| `cortex.endeavor.audit.finding` | `host`, `tier`, `missing`, `resume_blocking` | - |
| `cortex.endeavor.birth.ack` | `host`, `reason` | - |
| `cortex.endeavor.birth.incomplete` | `host`, `missing`, `resume_blocking`, `ack` | - |
| `cortex.endeavor.lock.blocked` | `host`, `deliverable`, `pending_rows` | - |
| `cortex.endeavor.repaired` | `tier`, `repaired`, `residual`, `applied` | - |
| `cortex.endeavor.row.disposed` | `host`, `row_id`, `pin` | - |
| `cortex.endeavor.row.pending` | `host`, `row_id`, `pin` | - |
| `cortex.endeavor.strategy.pin.missing` | `host`, `row_id` | - |
| `cortex.entity.source.changed` | `entity_id`, `change`, _dynamic_ | Entity `source_uri` set/changed/dropped on create/update. `change` ∈ {`set`, `changed`, `dropped`}. Drives RAG EntityAdmissionGate debounced refresh. role=observation. |
| `cortex.implement.recon.waived` | `todo_id`, `waived_by`, `reason_code`, `reason`, `spec_sha256`, `waived_at`, `stale?`, `stale_reason?` | cortex.implement.recon.waived — audited skeptic-gate recon waiver applied. |
| `cortex.pinned_deliverable.written` | `rel_path`, `dispatch_id`, `thread_id`, `skipped` | cortex.pinned_deliverable.written — emitted when a packet-pinned deliverable is written to cortex (friction 19916). |
| `cortex.search.failed` | `exc_type`, `detail`, `q_len`, `intent` | Search boundary exception before re-raise; names real cause before generic client error. |
| `cortex.search.vector.degraded` | `reason`, `exc_type`, `q_len`, `duration_s` | Vector branch failed; hybrid search degraded to FTS-only. `reason` ∈ {`vector_embed_timeout`, `vector_unavailable`, `vector_error`}. |
| `cortex.skill_graph.drift.alert` | `drift_count`, `stale_edges`, `missing_edges`, `consecutive_dirty_runs`, `thread` | cortex.skill_graph.drift.alert — hysteresis threshold breach. |
| `cortex.skill_graph.drift.checked` | `drift_count`, `stale_edges`, `missing_edges`, `last_clean_ts`, `clean`, `exit_code`, `consecutive_dirty_runs` | cortex.skill_graph.drift.checked — periodic read-only drift metrics. |
| `cortex.skill_graph.drift.sweep.failed` | `error` | cortex.skill_graph.drift.sweep.failed — monitor sweep exception. |
| `cortex.skill_suggest.called` | `suggest_id`, `agent`, `transport`, `context_len`, `context_sha256`, `loaded_count`, `rerank_requested` | cortex.skill_suggest.called — entry telemetry (context hash+len only). |
| `cortex.skill_suggest.completed` | `suggest_id`, `agent`, `candidate_count`, `suggested_count`, `omitted_count`, `ranker_status`, `latency_ms`, `rank_execution_id?` | cortex.skill_suggest.completed — successful suggest path. |
| `cortex.skill_suggest.degraded` | `suggest_id`, `ranker_status`, `degraded_reason`, `latency_ms` | cortex.skill_suggest.degraded — rerank requested but Stage-A returned. |
| `cortex.skill_suggest.failed` | `suggest_id`, `exc_type`, `detail` | cortex.skill_suggest.failed — true endpoint errors (not rerank degrade). |
| `cortex.subgraph.render.called` | `render_id`, `root`, `hops`, `edge_types_count`, `top_k_assertions`, `include_superseded` | Entry to shared renderer (both REST and dispatch paths). `render_id` (uuid4 hex) correlates with `.completed` / `.failed` for the same call. |
| `cortex.subgraph.render.completed` | `render_id`, `root`, `hops`, `entity_count`, `edge_count`, `duration_ms`, `rendered_bytes` | Successful render; `rendered_bytes` is UTF-8 length of markdown (generated_at excluded). |
| `cortex.subgraph.render.failed` | `render_id`, `root`, `reason`, `hops` | Error path inside renderer; emitted before structured envelope return. Reason enum widened from V1.1 spec to field-level granularity. |
| `cortex.subgraph.walk.called` | `walk_id`, `root`, `hops`, `edge_types_count`, `direction`, `entity_cap`, `include_counts`, `promote_hubs` | cortex.subgraph.walk.called — emitted at entry to walk_subgraph. |
| `cortex.subgraph.walk.completed` | `walk_id`, `root`, `hops`, `entity_count`, `edge_count`, `duration_ms`, `envelope_bytes`, `table_bytes` | cortex.subgraph.walk.completed — emitted on successful walk. |
| `cortex.subgraph.walk.failed` | `walk_id`, `root`, `reason`, `hops` | cortex.subgraph.walk.failed — emitted on error paths inside walk_subgraph. |
| `cortex.supersede.would_reject` | `rule_ids`, `derivation_type`, `force`, `valid_from_inherited`, `parent_had_valid_from`, `reject_field_origins`, `mode`, `entity_id` | cortex.supersede.would_reject — durable shadow/hard_422 reject telemetry. |
| `cortex.view.rendered` | `document_id`, `view_rev`, `mode`, `sections_repaired_count`, `delta_create_count`, `delta_update_count`, `delta_delete_count` | cortex.view.rendered — emitted on register/refresh/full view_render. |
<!-- GENERATED:END region=cortex -->
<!-- GENERATED:START region=mcp inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `mcp.adapter.request.shape` | `provider`, `model`, `mcp_version`, `tool_count`, `mcp_tool_count`, `has_tool_search` | Every MCP request — shape summary for v1/v2 migration tracking. |
| `mcp.adapter.search.seen` | `references_count`, `correlation_id?` | Response contained a tool_search_tool_result block. |
| `mcp.adapter.tool.seen` | `tool_name`, `server_name`, `correlation_id?` | Response contained an mcp_tool_use block (Anthropic-executed MCP tool). |
| `mcp.adapter.v2.configured` | `provider`, `server_name`, `always_loaded_count`, `deferred_count` | First request with mcp_v2=true built the toolset payload. |
| `mcp.agentbus.advisory.fired` | `advisory`, `turn_kind`, `chars`, `suppressed_by_profile`, `thread`, `subject` | Signal: mcp.agentbus.advisory.fired |
| `mcp.agentbus.branch.associated` | `thread_id`, `branch_name`, `association_id` | Signal: mcp.agentbus.branch.associated |
| `mcp.agentbus.checkpoint.auto_stamp` | `thread`, `turn_number`, `subject` | Signal: mcp.agentbus.checkpoint.auto_stamp |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.orphan.demoted` | `thread_id`, `orphan_turn_id`, `closeout_turn_id` | Signal: mcp.agentbus.dispatch.orphan.demoted |
| `mcp.agentbus.dispatch.orphaned` | `execution_id`, `thread_id`, `pipeline_id`, `linked_at`, `age_s`, `reason` | Signal: mcp.agentbus.dispatch.orphaned |
| `mcp.agentbus.dispatch.thread.reused` | `thread`, `dispatch_thread_id`, `lane`, `is_auto` | Emitted when generate reuses an existing agent-bus thread. |
| `mcp.agentbus.dispatch.thread.split` | `thread`, `dispatch_thread_id`, `lane` | Emitted when generate mints a sibling thread instead of reusing. |
| `mcp.agentbus.lane.bound` | `thread_id`, `parent_thread_id`, `lane_role`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.lane.bound |
| `mcp.agentbus.quiet_with_wip.fired` | `thread`, `seat`, `reason`, `alarm_id`, `wip_execution_ids` | Signal: mcp.agentbus.quiet_with_wip.fired |
| `mcp.agentbus.sidecar.orphaned` | `uri`, `error`, `thread_id?` | Signal: mcp.agentbus.sidecar.orphaned |
| `mcp.agentbus.sidecar.written` | `thread`, `turn_number`, `uri`, `sha256`, `bytes` | Signal: mcp.agentbus.sidecar.written |
| `mcp.agentbus.thread.abandoned` | `thread`, `reason`, `link_count`, `terminal_count`, `delivered_count` | Thread reaped by watchdog after TTL expiry or all-terminal-no-delivery condition. `reason` values: `pending_ttl_exceeded`, `admitted_ttl_exceeded`, `all_terminal_no_delivery`, `tracker_expired` |
| `mcp.agentbus.thread.closed` | `thread`, `via` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.closed` | `thread`, `via?` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.cse.bound` | `thread_id`, `cse_chat_url`, `cse_registration_id`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.thread.cse.bound |
| `mcp.agentbus.thread.lifecycle.transitioned` | `thread`, `from_state`, `to_state`, `trigger` | Thread lifecycle state machine transition. `role=coordination`. `trigger` values: `create`, `admit`, `turn_posted`, `delivery_sent`, `delivery_failed`, `watchdog_reap`, `reopen`. Emitted from `_transition_lifecycle_state` — single point of correctness for all callers |
| `mcp.agentbus.thread.reopened` | `thread`, `from_state`, `to_state` | Emitted alongside `mcp.agentbus.thread.lifecycle.transitioned` when a turn POST transitions a thread out of a terminal state (completed, abandoned, or failed) back to active |
| `mcp.agentbus.turn.body_over_briefing` | `thread`, `from_agent`, `to_agent`, `subject`, `body_chars`, `target_chars` | Turn body exceeded the briefing target (2k) without allow_long_body, a sidecar, or an inline-contract envelope. Advisory only — the turn was inserted unchanged. |
| `mcp.agentbus.turn.created` | `thread`, `turn_id`, `turn_number`, `from_agent`, `to_agent`, `subject`, `created_at` | Turn inserted in agent-bus store (all transport paths) |
| `mcp.agentbus.watchdog.sweep.failed` | `error` | Watchdog sweep pass raised an unhandled exception. Repeated occurrences indicate a persistent failure in the reap path. |
| `mcp.cortex.op.rejected` | `surface`, `family`, `op` | - |
| `mcp.cse.session.acknowledged` | `registration_id`, `ack_class` | Emit when harvest classifies a typed ACK — not on paste. |
| `mcp.cse.session.conflict` | `reason`, `registration_id`, `chat_url` | Emit on self-supersession or other conflict refusal. |
| `mcp.cse.session.harvested` | `registration_id`, `outcome`, `ack_class`, `turn_count` | Emit when harvest completes or reports an incomplete outcome. |
| `mcp.cse.session.pasted` | `registration_id`, `receipt`, `send_verified`, `replayed` | Emit after paste with receipt — never carries ack_class. |
| `mcp.cse.session.resolved` | `registration_id`, `chat_url`, `state` | Emit when identity resolves on a public provenance read. |
<!-- GENERATED:END region=mcp -->

### `mcp.session.close.rejected` reason enum

| Reason | Triggered when | Agent fix |
|---|---|---|
| `session_id.invalid` | `session_id` does not match `{agent}-YYYY-MM-DD-HHMMSS-{3hex}` | Use the boot-held `session_id` or reformat with UTC second-resolution timestamp and 3-hex suffix. |
| `summary.too_short` | `len(summary) < 20` | Extend the summary to ≥20 chars. |
| `transcript_jsonl.invalid` | `transcript_jsonl_path` is missing, outside `CURSOR_AGENT_TRANSCRIPTS_ROOT`, or fails JSONL parse | Re-derive the path from `ls -lt $CURSOR_AGENT_TRANSCRIPTS_ROOT` (most recent UUID dir). |
| `session_summary.invalid` | `session_summary_md` empty or missing `## Session Summary` heading | Compose the structural layer per `session-close.mdc` Step 1. |
| `transcript.missing_structure` | Composed transcript <200 chars or missing `## Turn` and `## Session Summary` headings (defense-in-depth post-assembly check) | JSONL too short OR structural layer too thin — extend `session_summary_md`. |
| `transcript.hollow` | Composed transcript has zero User-voice blocks after assembly (JSONL contained no user messages) | Confirm `transcript_jsonl_path` points at the active session, not a tool-only record set. |
| `session.already_closed` | `session_journals.session_id` already has a row (migration 034 UNIQUE constraint). Detail object carries the prior `transcript_entity_id`, `transcript_path`, `journal_row_id`. | Treat as success — quote the IDs from the detail object. |

Maximum one retry on 422 (except `session.already_closed`, which is success-equivalent). If a second close also rejects on a non-`already_closed` reason, the agent surfaces the rejection reason explicitly to the user.

### Post-Write Hook Signals

Emitted by `libs/cortex_store/_post_write_hooks.py` (the `run_observed_hook` wrapper) and by `scripts/reconcile_post_write_hooks.py`. Each successful `POST /assertions` schedules four fire-and-forget hooks; this family makes their per-hook outcome observable so silent failures (assertion 8873/8874 class — `predicate_form IS NULL` for hours despite a hook firing) are detectable from the event stream alone, without diffing DB state.

All signals: `role="observation"`, `scope="global"`. Source: `cortex-api`.

**Signal naming**: `assertion.<hook>.<phase>` where:

- `<hook>` ∈ {`fts`, `enrichment`, `predicate_extract`, `embedding`}
- `<phase>` ∈ {`started`, `completed`, `completed_no_effect`, `failed`, `skipped`}

The hook never appears in the payload only — it is part of the signal name so existing per-signal filters (`signal LIKE 'assertion.predicate_extract.%'`) work without payload extraction. The phase distinction `completed` vs `completed_no_effect` carries the projection-fidelity invariant `[universal:rest]`: `completed` means the work ran AND the achieved-state sentinel observed the durable effect; `completed_no_effect` means the work ran without raising but the sentinel reports the durable effect is missing (the silent-failure shape).

**Mandatory payload (every `assertion.<hook>.*` event)**:

| Field | Type | Semantics |
|---|---|---|
| `assertion_id` | int | Primary join key. Always present. |
| `hook` | str | One of {fts, enrichment, predicate_extract, embedding}. Redundant with signal name; carried in payload to simplify cross-hook joins. |
| `attempt_id` | str (uuid4) | Distinguishes multiple invocations of the same hook for the same assertion. Required because enrichment internally re-invokes `reindex_assertion_fts` at the end of its run, producing two `assertion.fts.*` event sequences for one assertion insert; the wrapper assigns a fresh `attempt_id` to each call so the two sequences stay correlatable but distinct. Reconciliation re-dispatches also get fresh `attempt_id`s. |
| `phase` | str | Same enum as the signal-name phase suffix. Carried in payload for SQL convenience. |

**Phase-specific payload**:

| Phase | Additional fields | When emitted |
|---|---|---|
| `started` | — | Immediately before the hook's work function runs. Enables live trace + duration computation. |
| `completed` | `achieved=true`, `duration_ms` (int), `result` (dict\|null) | Work returned without raising AND the achieved-state sentinel confirmed the durable effect (FTS row present, `predicate_form` populated, embedding present in Chroma, enrichment per-kind result map shows `written` for at least one kind). For enrichment, `result` is a per-kind map: `{"prospective_summary": "written"\|"no_output"\|"skipped_disabled"\|"failed", "events_json": ...}`. For other hooks, `result` may be `null`. |
| `completed_no_effect` | `achieved=false`, `duration_ms`, `result` (dict\|null) | Work returned without raising BUT the achieved-state sentinel reports no durable effect. The canonical silent-failure shape (e.g. predicate-extract HTTP 200 but `predicate_form` still NULL; enrichment ran but every kind reports `no_output` or `skipped_disabled`). Reconciliation treats these as gaps after the grace window. |
| `failed` | `achieved=false`, `duration_ms`, `error_type` (str — exception class name), `error` (str — `str(exc)[:1000]`) | Hook work raised an exception. The exception is suppressed at the wrapper boundary (hooks remain fire-and-forget per invariant; write path is not perturbed) but the failure is durable in the event stream. |
| `skipped` | `reason` (str) | Hook deliberately did not run. `reason` enum: `feature_disabled` (e.g. `_ENRICHMENT_ENABLED=false`), `prerequisite_missing` (e.g. embedding hook called with empty claim text), `duplicate_attempt` (reserved for future dedup). No `duration_ms` (no work ran). |

**Reconciliation gap signal** — emitted by the periodic reconciler (`scripts/reconcile_post_write_hooks.py`):

| Signal | Payload | Description |
|---|---|---|
| `assertion.<hook>.reconcile.gap` | `assertion_id`, `hook`, `assertion_age_s` (int), `grace_window_s` (int), `last_attempt_id` (str\|null), `last_phase` (str\|null), `redispatched` (bool) | A live assertion older than the grace window is missing the achieved-state for `<hook>`. `last_attempt_id` / `last_phase` reflect the most recent terminal event observed for this `(assertion_id, hook)` pair (null if no event was ever seen — the harshest silent-failure mode: hook never fired at all). `redispatched=true` if reconciliation also re-invoked the hook; `false` in `--dry-run` mode. |

The gap signal name is parameterized on hook for the same filter reason as the main family. The plan's `assertion.enrichment.reconcile.gap` is one realization of the pattern; reconciliation may also emit `assertion.predicate_extract.reconcile.gap`, `assertion.fts.reconcile.gap`, `assertion.embedding.reconcile.gap`.

**Invariants**:

- ∀ successful `POST /assertions`: exactly four `started` events SHOULD appear (one per hook), unless a hook is `skipped` for a `feature_disabled` reason. Enrichment additionally produces a second `assertion.fts.started` (with a distinct `attempt_id`) when its post-work FTS reindex fires.
- ∀ `started` event: a terminal event (`completed` | `completed_no_effect` | `failed`) MUST eventually follow with the same `attempt_id`. Absence of a terminal event (live-event path crashed mid-hook) is itself a class of silent failure — caught by reconciliation, not by event-stream invariants.
- `skipped` events have no preceding `started` (the wrapper short-circuits before `started` fires when the skip reason is detectable up-front).
- Per `[universal:no-bc]`: existing `logger.warning` lines that previously narrated hook outcomes are removed when the wrapper lands. Events are the single live mechanism; logs duplicating event content are deleted, not left in parallel.

**Query examples**:

```bash
# All hook outcomes for one assertion
scripts/query-events --sql "
  SELECT ts_unix_ms, signal, json_extract(payload,'\$.attempt_id') attempt, json_extract(payload,'\$.achieved') achieved, json_extract(payload,'\$.error_type') err
  FROM events
  WHERE signal LIKE 'assertion.%'
    AND json_extract(payload,'\$.assertion_id') = 8873
  ORDER BY seq"

# Silent-failure shape: hook completed but didn't achieve
scripts/query-events --sql "
  SELECT signal, COUNT(*) n
  FROM events
  WHERE signal LIKE 'assertion.%.completed_no_effect'
    AND ts_unix_ms > (unixepoch()-3600)*1000
  GROUP BY signal"

# Reconciliation-discovered gaps in last 24h
scripts/query-events --sql "
  SELECT signal, json_extract(payload,'\$.assertion_id') id, json_extract(payload,'\$.assertion_age_s') age
  FROM events
  WHERE signal LIKE 'assertion.%.reconcile.gap'
    AND ts_unix_ms > (unixepoch()-86400)*1000
  ORDER BY seq DESC"
```



### Ops Notify (v1.5)

Emitted by `services/mcp-server/tools/notify.py` after email-bridge reports a successful pager delivery. Tracks attention-plane delivery and `(unreferenced)` ref thin-rate for Bind B measurement.

| Signal | Role | Required Payload | Optional Payload |
|--------|------|------------------|------------------|
| `ops.notify.sent` | observation | `from_agent`, `ref`, `unreferenced`, `subject`, `tag`, `stamped_at` | `surface` (from request metadata merge) |

Source: `mcp-server`. Not emitted when `PAGER_NOTIFY_ENABLED=0` or when email-bridge returns non-sent.

### MCP Maintenance (Drain) Signals

Emitted by `services/mcp-server/middleware/drain.py` during graceful restart. Track the drain lifecycle so observability can correlate `manage(action="sync_restart", service="mcp")` calls with the request rejection window and confirm that the drain completes within the configured timeout.

All signals: `role="observation"`, `scope="global"`. Source: `mcp-server`.

<!-- GENERATED:START region=mcp inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `mcp.adapter.request.shape` | `provider`, `model`, `mcp_version`, `tool_count`, `mcp_tool_count`, `has_tool_search` | Every MCP request — shape summary for v1/v2 migration tracking. |
| `mcp.adapter.search.seen` | `references_count`, `correlation_id?` | Response contained a tool_search_tool_result block. |
| `mcp.adapter.tool.seen` | `tool_name`, `server_name`, `correlation_id?` | Response contained an mcp_tool_use block (Anthropic-executed MCP tool). |
| `mcp.adapter.v2.configured` | `provider`, `server_name`, `always_loaded_count`, `deferred_count` | First request with mcp_v2=true built the toolset payload. |
| `mcp.agentbus.advisory.fired` | `advisory`, `turn_kind`, `chars`, `suppressed_by_profile`, `thread`, `subject` | Signal: mcp.agentbus.advisory.fired |
| `mcp.agentbus.branch.associated` | `thread_id`, `branch_name`, `association_id` | Signal: mcp.agentbus.branch.associated |
| `mcp.agentbus.checkpoint.auto_stamp` | `thread`, `turn_number`, `subject` | Signal: mcp.agentbus.checkpoint.auto_stamp |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.orphan.demoted` | `thread_id`, `orphan_turn_id`, `closeout_turn_id` | Signal: mcp.agentbus.dispatch.orphan.demoted |
| `mcp.agentbus.dispatch.orphaned` | `execution_id`, `thread_id`, `pipeline_id`, `linked_at`, `age_s`, `reason` | Signal: mcp.agentbus.dispatch.orphaned |
| `mcp.agentbus.dispatch.thread.reused` | `thread`, `dispatch_thread_id`, `lane`, `is_auto` | Emitted when generate reuses an existing agent-bus thread. |
| `mcp.agentbus.dispatch.thread.split` | `thread`, `dispatch_thread_id`, `lane` | Emitted when generate mints a sibling thread instead of reusing. |
| `mcp.agentbus.lane.bound` | `thread_id`, `parent_thread_id`, `lane_role`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.lane.bound |
| `mcp.agentbus.quiet_with_wip.fired` | `thread`, `seat`, `reason`, `alarm_id`, `wip_execution_ids` | Signal: mcp.agentbus.quiet_with_wip.fired |
| `mcp.agentbus.sidecar.orphaned` | `uri`, `error`, `thread_id?` | Signal: mcp.agentbus.sidecar.orphaned |
| `mcp.agentbus.sidecar.written` | `thread`, `turn_number`, `uri`, `sha256`, `bytes` | Signal: mcp.agentbus.sidecar.written |
| `mcp.agentbus.thread.abandoned` | `thread`, `reason`, `link_count`, `terminal_count`, `delivered_count` | Thread reaped by watchdog after TTL expiry or all-terminal-no-delivery condition. `reason` values: `pending_ttl_exceeded`, `admitted_ttl_exceeded`, `all_terminal_no_delivery`, `tracker_expired` |
| `mcp.agentbus.thread.closed` | `thread`, `via` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.closed` | `thread`, `via?` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.cse.bound` | `thread_id`, `cse_chat_url`, `cse_registration_id`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.thread.cse.bound |
| `mcp.agentbus.thread.lifecycle.transitioned` | `thread`, `from_state`, `to_state`, `trigger` | Thread lifecycle state machine transition. `role=coordination`. `trigger` values: `create`, `admit`, `turn_posted`, `delivery_sent`, `delivery_failed`, `watchdog_reap`, `reopen`. Emitted from `_transition_lifecycle_state` — single point of correctness for all callers |
| `mcp.agentbus.thread.reopened` | `thread`, `from_state`, `to_state` | Emitted alongside `mcp.agentbus.thread.lifecycle.transitioned` when a turn POST transitions a thread out of a terminal state (completed, abandoned, or failed) back to active |
| `mcp.agentbus.turn.body_over_briefing` | `thread`, `from_agent`, `to_agent`, `subject`, `body_chars`, `target_chars` | Turn body exceeded the briefing target (2k) without allow_long_body, a sidecar, or an inline-contract envelope. Advisory only — the turn was inserted unchanged. |
| `mcp.agentbus.turn.created` | `thread`, `turn_id`, `turn_number`, `from_agent`, `to_agent`, `subject`, `created_at` | Turn inserted in agent-bus store (all transport paths) |
| `mcp.agentbus.watchdog.sweep.failed` | `error` | Watchdog sweep pass raised an unhandled exception. Repeated occurrences indicate a persistent failure in the reap path. |
| `mcp.cortex.op.rejected` | `surface`, `family`, `op` | - |
| `mcp.cse.session.acknowledged` | `registration_id`, `ack_class` | Emit when harvest classifies a typed ACK — not on paste. |
| `mcp.cse.session.conflict` | `reason`, `registration_id`, `chat_url` | Emit on self-supersession or other conflict refusal. |
| `mcp.cse.session.harvested` | `registration_id`, `outcome`, `ack_class`, `turn_count` | Emit when harvest completes or reports an incomplete outcome. |
| `mcp.cse.session.pasted` | `registration_id`, `receipt`, `send_verified`, `replayed` | Emit after paste with receipt — never carries ack_class. |
| `mcp.cse.session.resolved` | `registration_id`, `chat_url`, `state` | Emit when identity resolves on a public provenance read. |
<!-- GENERATED:END region=mcp -->

**Wire shape — restart error envelope** (also returned by proxies after exhausting retries):

```json
{
  "jsonrpc": "2.0",
  "id": <jsonrpc_id>,
  "error": {
    "code": -32099,
    "message": "MCP server is restarting; retry in 30s",
    "data": {
      "reason": "server_restarting",
      "retry_after_s": 30
    }
  }
}
```

HTTP status `503`. Headers `Retry-After: 30` and `Connection: close`. Clients should retry with backoff. **`services/universal_cloud_proxy/mcp_executor.py`** retries on this envelope AND on connection-class errors for Google/OpenRouter frontier-completions MCP lanes (providers without native MCP). **SDK stdio lane** (cursor-sdk → `fastmcp-remote` → `https://mcp.k-1.me`) receives a graceful `-32099` (`Retry-After: 30`) but no automatic retry — neither the bridge launcher (`scripts/mcp-fastmcp-remote-bridge.py`) nor the cloud-proxy executor is in the retry path for this lane; retry belongs in cursor-sdk dispatch orchestration or the caller. Direct HTTPS MCP clients see server-side drain behavior only.

**Query example** — restart-cycle observability (drain start → server.started elapsed):

```bash
scripts/query-events --sql "
  WITH drains AS (
    SELECT seq, ts_unix_ms AS t_drain, json_extract(payload,'\$.in_flight') AS in_flight
    FROM events WHERE signal='mcp.maintenance.drain.started'
  ),
  starteds AS (
    SELECT seq, ts_unix_ms AS t_started
    FROM events WHERE signal='mcp.oauth.server.started'
  )
  SELECT d.t_drain, d.in_flight, (
    SELECT MIN(s.t_started) FROM starteds s WHERE s.t_started > d.t_drain
  ) AS t_started_next,
  (SELECT MIN(s.t_started) FROM starteds s WHERE s.t_started > d.t_drain) - d.t_drain AS cycle_ms
  FROM drains d ORDER BY d.t_drain DESC LIMIT 10"
```

The `cycle_ms` distribution is the calibration target for `_RESTART_RETRY_DELAYS_S` in the stdio and cloud proxies. The two retry delays SHOULD sum to at least the 95th percentile of `cycle_ms`.

### Grok Build Dispatch Signals

> **RETIRED (2026-07-15).** Runtime harness removed — no `libs/grokbuild`, no `grokbuild_worker`, no MCP tool / `canonical.yaml` advertisement. Section retained for **forensics** against historical `mcp.grokbuild.*` / `grokbuild.*` rows in the event store. Do not emit new signals in these families.

**V2 process topology (2026-05-22, historical).** The grokbuild call path was a multi-process pipeline: MCP tool (`services/mcp-server/tools/grokbuild.py`) → Stargate proxy (`services/universal-stargate/systems/proxy/routers/api/grokbuild.py`) → `grokbuild-worker` FastAPI service (`services/grokbuild_worker/app.py`) on port 8090 → grok CLI subprocess. Two signal families coexisted:

* **`mcp.grokbuild.*`** — lib-level events emitted by `libs/grokbuild/events_*.py` factories. The audit-rich vocabulary (`read_only_violation`, `git_status_pre/post`, `git_diff_stat`, `sidecar_gaps`, `dispatch_conflict`, etc.). In V1 this flowed through `mcp_events.record` in the mcp-server process. In V2 the lib runs inside the worker process where `mcp_events` is not importable; the worker's lifespan startup installs a UDS publisher into `libs/grokbuild/events_core.register_uds_publisher`, so these signals continue to land in the event service from the worker process. Drop-in from caller perspective.
* **`grokbuild.*`** — worker-level events emitted by `services/grokbuild_worker/events.py`. Coarser-grained — SSE-friendly accepted/started/progress/completed signals for the async build surface (Phase B), plus worker lifecycle and per-op tracking events. These do NOT carry the audit-rich payload that `mcp.grokbuild.dispatch.completed` carries.

All signals: `role="observation"`, `scope="global"`.

**Lib signals (`mcp.grokbuild.*`).** Source: `mcp-server` in V1; `grokbuild-worker` in V2 (via the UDS publisher hook). Payload contracts unchanged across versions.

<!-- GENERATED:START region=mcp inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `mcp.adapter.request.shape` | `provider`, `model`, `mcp_version`, `tool_count`, `mcp_tool_count`, `has_tool_search` | Every MCP request — shape summary for v1/v2 migration tracking. |
| `mcp.adapter.search.seen` | `references_count`, `correlation_id?` | Response contained a tool_search_tool_result block. |
| `mcp.adapter.tool.seen` | `tool_name`, `server_name`, `correlation_id?` | Response contained an mcp_tool_use block (Anthropic-executed MCP tool). |
| `mcp.adapter.v2.configured` | `provider`, `server_name`, `always_loaded_count`, `deferred_count` | First request with mcp_v2=true built the toolset payload. |
| `mcp.agentbus.advisory.fired` | `advisory`, `turn_kind`, `chars`, `suppressed_by_profile`, `thread`, `subject` | Signal: mcp.agentbus.advisory.fired |
| `mcp.agentbus.branch.associated` | `thread_id`, `branch_name`, `association_id` | Signal: mcp.agentbus.branch.associated |
| `mcp.agentbus.checkpoint.auto_stamp` | `thread`, `turn_number`, `subject` | Signal: mcp.agentbus.checkpoint.auto_stamp |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.orphan.demoted` | `thread_id`, `orphan_turn_id`, `closeout_turn_id` | Signal: mcp.agentbus.dispatch.orphan.demoted |
| `mcp.agentbus.dispatch.orphaned` | `execution_id`, `thread_id`, `pipeline_id`, `linked_at`, `age_s`, `reason` | Signal: mcp.agentbus.dispatch.orphaned |
| `mcp.agentbus.dispatch.thread.reused` | `thread`, `dispatch_thread_id`, `lane`, `is_auto` | Emitted when generate reuses an existing agent-bus thread. |
| `mcp.agentbus.dispatch.thread.split` | `thread`, `dispatch_thread_id`, `lane` | Emitted when generate mints a sibling thread instead of reusing. |
| `mcp.agentbus.lane.bound` | `thread_id`, `parent_thread_id`, `lane_role`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.lane.bound |
| `mcp.agentbus.quiet_with_wip.fired` | `thread`, `seat`, `reason`, `alarm_id`, `wip_execution_ids` | Signal: mcp.agentbus.quiet_with_wip.fired |
| `mcp.agentbus.sidecar.orphaned` | `uri`, `error`, `thread_id?` | Signal: mcp.agentbus.sidecar.orphaned |
| `mcp.agentbus.sidecar.written` | `thread`, `turn_number`, `uri`, `sha256`, `bytes` | Signal: mcp.agentbus.sidecar.written |
| `mcp.agentbus.thread.abandoned` | `thread`, `reason`, `link_count`, `terminal_count`, `delivered_count` | Thread reaped by watchdog after TTL expiry or all-terminal-no-delivery condition. `reason` values: `pending_ttl_exceeded`, `admitted_ttl_exceeded`, `all_terminal_no_delivery`, `tracker_expired` |
| `mcp.agentbus.thread.closed` | `thread`, `via` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.closed` | `thread`, `via?` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.cse.bound` | `thread_id`, `cse_chat_url`, `cse_registration_id`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.thread.cse.bound |
| `mcp.agentbus.thread.lifecycle.transitioned` | `thread`, `from_state`, `to_state`, `trigger` | Thread lifecycle state machine transition. `role=coordination`. `trigger` values: `create`, `admit`, `turn_posted`, `delivery_sent`, `delivery_failed`, `watchdog_reap`, `reopen`. Emitted from `_transition_lifecycle_state` — single point of correctness for all callers |
| `mcp.agentbus.thread.reopened` | `thread`, `from_state`, `to_state` | Emitted alongside `mcp.agentbus.thread.lifecycle.transitioned` when a turn POST transitions a thread out of a terminal state (completed, abandoned, or failed) back to active |
| `mcp.agentbus.turn.body_over_briefing` | `thread`, `from_agent`, `to_agent`, `subject`, `body_chars`, `target_chars` | Turn body exceeded the briefing target (2k) without allow_long_body, a sidecar, or an inline-contract envelope. Advisory only — the turn was inserted unchanged. |
| `mcp.agentbus.turn.created` | `thread`, `turn_id`, `turn_number`, `from_agent`, `to_agent`, `subject`, `created_at` | Turn inserted in agent-bus store (all transport paths) |
| `mcp.agentbus.watchdog.sweep.failed` | `error` | Watchdog sweep pass raised an unhandled exception. Repeated occurrences indicate a persistent failure in the reap path. |
| `mcp.cortex.op.rejected` | `surface`, `family`, `op` | - |
| `mcp.cse.session.acknowledged` | `registration_id`, `ack_class` | Emit when harvest classifies a typed ACK — not on paste. |
| `mcp.cse.session.conflict` | `reason`, `registration_id`, `chat_url` | Emit on self-supersession or other conflict refusal. |
| `mcp.cse.session.harvested` | `registration_id`, `outcome`, `ack_class`, `turn_count` | Emit when harvest completes or reports an incomplete outcome. |
| `mcp.cse.session.pasted` | `registration_id`, `receipt`, `send_verified`, `replayed` | Emit after paste with receipt — never carries ack_class. |
| `mcp.cse.session.resolved` | `registration_id`, `chat_url`, `state` | Emit when identity resolves on a public provenance read. |
<!-- GENERATED:END region=mcp -->

`.rejected` `reason_code` enum (validator side, unchanged from V1; V2 added `capacity_exhausted` on the worker-side `grokbuild.dispatch.rejected`, see below): `retired_op`, `retired_output_format`, `retired_param`, `unknown_op`, `bad_tier`, `bad_reasoning_effort`, `bad_effort`, `bad_max_turns`, `bad_best_of_n`, `bad_timeout_seconds`, `bad_resume_strict_without_session_id`, `bad_output_format`, `cwd_missing`, `not_a_git_repo`, `git_unreachable`, `working_tree_dirty`, `grok_not_in_path`, `missing_grok_auth`, `sidecar_unavailable`, `dispatch_conflict`.

**Auth lifecycle signals** (`grokbuild.auth.*`). Emitted by the worker process via the
UDS publisher hook (same path as `grokbuild.*` worker-level events; not audit-rich
`mcp.grokbuild.*` vocab).

| Signal | Fields | Notes |
|---|---|---|
| `grokbuild.auth.required` | `reason_code` (str — `"expired"` \| `"missing"`), `grok_auth_dir` (str), `deploy_shape` (str — `"bare-metal"` \| `"container"`), `trigger` (str — `"startup"` \| `"dispatch_rejection"` \| `"periodic"`), `debounce_key` (str — ISO timestamp of active latch, empty before Phase 2 notifier) | Fired when `probe_grok_auth()` returns non-OK. Debounced in Phase 2 — raw event fires every probe failure; debounced agent-bus notification is gated by the file latch. |
| `grokbuild.auth.restored` | `grok_auth_dir` (str), `downtime_s` (float — 0 before Phase 3 tracking is wired) | Fired when probe succeeds after a prior non-OK result. Clears the debounce latch in Phase 3. |

**Invariant** (Option A, OQ-1): `grokbuild.auth.required{trigger="startup"}` ⟹
`grokbuild.worker.started` emitted in the same boot cycle. `grok_auth` is **excluded**
from `degraded_checks` and from the `/health` `status` rollup — auth expiry is surfaced
only via `grokbuild.auth.required` and the `checks.grok_auth` field, never as worker
`degraded`. So `status="ok"` is possible while `checks.grok_auth="expired"`.

`read_only_violation` semantics: `True` iff `mode == "read_only"` AND `(git_diff_stat.strip() OR git_status_post.strip())`. Validator enforces clean pre-state, so any non-empty post-state porcelain is divergence. Reading `git_status_post` alone catches all YX-coded changes (staged, unstaged, untracked); `git_diff_stat` is OR'd for defense in depth on `audit_incomplete=True` paths.

`audit_incomplete` semantics: `True` iff a git invocation in `_capture_post_state` failed (`subprocess.CalledProcessError`, `TimeoutExpired`, or `OSError`). Subscribers MUST treat `audit_incomplete=true` as "do not trust this dispatch's `read_only_violation` verdict" — distinct from a clean repo (`git_status_post=""`, `audit_incomplete=false`), which is a TRUE clean signal.

**Lib signals (`git.integrate.*`).** Source: `git-integration-worker` in Phase 4 (via UDS publisher hook on `libs/git_integrate/events.py`, mirroring grokbuild). Payload contracts established Phase 2; emitters wired in Phase 3 (`integrate_op`, `status_op`).

All signals: `role="observation"`, `scope="global"`.

<!-- GENERATED:START region=git inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `git.commit.created` | `integration_id`, `arc`, `commit_sha` | - |
| `git.integrate.completed` | `integration_id`, `arc`, `phase`, `merge_commit`, `master_sha`, `duration_s` | Master advanced at ref level; optional worktree teardown follows. Emitted by `integrate_op` on success. |
| `git.integrate.gate.failed` | `integration_id`, `arc`, `phase`, `gate_cmd`, `gate_exit`, `duration_s` | Green-gate returned non-zero on the integrated tree; arc worktree reset to pre-merge tip. Emitted by `integrate_op`. |
| `git.integrate.rejected` | `integration_id`, `reason_code`, `reason`, `arc`, `phase` | Admission or merge-phase refusal. No master advance. Correlation fields inline so no `.requested` join required for admission rejects. Emitted by `validate_integrate` / `integrate_op`. |
| `git.integrate.requested` | `integration_id`, `arc`, `phase`, `worktree_path`, `diff_sha256` | Integration admitted past validation; retry loop entered. Emitted by `integrate_op` after admission succeeds. |
| `git.integrate.retried` | `integration_id`, `arc`, `attempt`, `reason` | Non-ff CAS advance; optimistic retry loop continues. Emitted by `integrate_op`. |
| `git.land.completed` | `integration_id`, `arc`, `phase`, `merge_commit`, `master_sha`, `committed`, `commit_sha`, `duration_s` | - |
| `git.land.requested` | `integration_id`, `arc`, `phase`, `worktree_path`, `diff_sha256`, `committed` | - |
| `git.log.read` | `head`, `n`, `since`, `truncated` | Oneline git history query (no diffs). Emitted by fs(op=recent_commits). Payload: head, n, since, truncated. |
| `git.path.commit.completed` | `commit_id`, `branch`, `commit_sha`, `path_count`, `duration_s` | Gated path-scoped commit succeeded. Emitted by `commit_op` after `commit_paths` advances the branch. |
| `git.path.commit.rejected` | `commit_id`, `reason_code`, `reason`, `branch` | Gated path-scoped commit refused (validation or commit failure). No branch advance. Emitted by `commit_op`. |
| `git.status.read` | `worktree_path`, `dirty`, `branch` | Read-only status probe served (MCP `git_status` path). Emitted by `status_op`. |
<!-- GENERATED:END region=git -->

**Cursor SDK catalog signals (`cursor.catalog.*`).** Source: Stargate Master
`CursorSdkCatalogPoller` polling `GET /api/v1/cursor/catalog` on
git_integration_worker.

<!-- GENERATED:START region=cursor inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `cursor.catalog.available` | `worker_url`, `model_count` | - |
| `cursor.catalog.drift.detected` | `worker_url`, `divergence_count`, `sample` | - |
| `cursor.catalog.fetch.failed` | `worker_url`, `error` | - |
| `cursor.catalog.unavailable` | `worker_url`, `reason` | - |
| `cursor.catalog.updated` | `worker_url`, `model_count` | - |
<!-- GENERATED:END region=cursor -->

**CDP-ask satellite signals (`cdp_ask.*`).** Source: `libs/cdp_ask/` — warm CSE
followup paste and reattach (`followup_events.py`), attended-operator target
resolution, and stop-ack check-in (`stop_ack_events.py`). All `role="observation"`,
`scope="node"`, delivered best-effort to the hub Event Service over TCP (satellites)
or UDS (local hub); emit never raises into the paste path.

`cdp_ask.fresh_run.inheritance` is the falsifier for
`decision:fresh-run-no-silent-inheritance`: it fires only on a *verified* followup
paste, so emission itself witnesses that context inheritance occurred, and
`declared` records whether the caller named the target CSE or the resolver chose
it. `silent=true` is a violation reading.

<!-- GENERATED:START region=cdp_ask inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `cdp_ask.attended.refused` | `code`, `candidates_considered?`, `candidate_count?` | Emit when attended-operator resolver refuses (404/409/424). |
| `cdp_ask.attended.resolve` | `registration_id`, `cdp_url`, `chat_url`, `purpose`, `source`, `dormant` | Emit when the attended-operator resolver returns a unique target. |
| `cdp_ask.followup.paste_attempt` | `registration_id`, `resolution_path` | Warm CSE followup paste started on attached lane. `registration_id`, `resolution_path` ∈ {chat_url, registration_id, execution_id}. |
| `cdp_ask.followup.paste_verified` | `registration_id`, `resolution_path`, `send_verified`, `streaming_at_paste`, `error_code`, `lane_created`, `receipt`, `target_binding` | Warm CSE followup paste verification outcome. `send_verified`, optional `streaming_at_paste`, `error_code` — no prompt bodies. |
| `cdp_ask.followup.reattach_attempt` | `chat_url`, `holder`, `purpose` | Emit when opt-in warm reattach starts navigating to a CSE URL. |
| `cdp_ask.followup.reattach_result` | `registration_id`, `lane_created`, `ok`, `error_code` | Emit after warm reattach completes (lane reused, launched, or typed failure). |
| `cdp_ask.followup.unbound_capped` | `registration_id`, `receipt`, `target_binding` | Emit when unbound paste path applies automation-visible receipt cap. |
| `cdp_ask.fresh_run.inheritance` | `registration_id`, `resolution_path`, `target_binding`, `reattach_used`, `declared`, `silent`, `purpose` | Emit the fresh-run verdict when a followup paste inherits a live CSE. |
| `cdp_ask.stop_ack.ack` | `execution_id`, `ack`, `job?` | Emit when a scraped STOP-ACK token is parsed from the model reply. |
| `cdp_ask.stop_ack.checkin_attempt` | `execution_id`, `registration_id`, `purpose`, `route`, `lane_created` | Emit when a STOP-ACK check-in paste is attempted or pager fallback is chosen. |
| `cdp_ask.stop_ack.no_ack` | `execution_id`, `registration_id`, `ghost_reap_candidate` | Emit when STOP-ACK TTL expires without a parsed ACK (ghost-reap candidate). |
<!-- GENERATED:END region=cdp_ask -->

**Worker lifecycle signals (`git_worker.*`).** Source: `git-integration-worker` (UDS publisher via `git_worker_lifecycle_events.py` and `git_worker_drain_events.py`).

All signals: `role="observation"`, `scope="node"`.

<!-- GENERATED:START region=git_worker inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `git_worker.admission.rejected` | `kind`, `route`, `intent_id`, `drain_epoch` | - |
| `git_worker.dispatch.rejected` | _dynamic_ | - |
| `git_worker.drain.completed` | `intent_id`, `drain_epoch`, `worker_id`, `pid`, `completed_at`, `active_count` | - |
| `git_worker.drain.started` | `reason`, `intent_id`, `drain_epoch`, `worker_id`, `pid`, `worker_started_at`, `active_count`, `active_ops` | - |
| `git_worker.started` | `worker_id`, `pid`, `port`, `version`, `origin_service`, `schema_version`, `started_at`, `source_repo`, `bind_host`, `build_sha?`, `health_url?` | - |
<!-- GENERATED:END region=git_worker -->

**Hop-cadence signals (`giw.cursor_auto.hop_cadence_*`).** Source: `services/git_integration_worker/cursor_auto/hop_cadence_events.py`. Seat binding, succession confirm, release-deferral, and census OVERLAP. There is no `hop_cadence_orphan_released` signal — extras are non-holders released by rebind.

<!-- GENERATED:START region=giw inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `giw.cursor_auto.hop_cadence_binding_indeterminate` | `thread_id`, `reason` | Predecessor binding could not be resolved; not a first-seat claim. |
| `giw.cursor_auto.hop_cadence_fence_started` | `thread_id`, `superseded_registration_id`, `execution_id`, `satellite_execution_id` | Joinable hop-claim armed a request fence on the lane. |
| `giw.cursor_auto.hop_cadence_identity_bound` | `thread_id`, `identity_source`, `watch_present`, `registration_id` | Caller identity resolved on a watched lane at request admission. |
| `giw.cursor_auto.hop_cadence_lease_lost` | `thread_id`, `registration_id`, `identity_source`, `superseded_registration_id`, `successor_execution_id` | Lease refused a superseded predecessor at agent_bus.request admission. |
| `giw.cursor_auto.hop_cadence_lease_reclaimed` | `thread_id`, `superseded_registration_id`, `execution_id`, `action` | CSE-terminal release cleared the request fence for a superseded seat. |
| `giw.cursor_auto.hop_cadence_liveness_probe_failed` | `thread_id`, `error`, `mark`, `fail_open` | Liveness/capacity probe exception swallowed → empty snap fail-open. |
| `giw.cursor_auto.hop_cadence_lookup_failed_observe` | `thread_id`, `registration_id`, `observed_at`, `snap_kind`, `snap_empty`, `fail_open`, `total_rows`, `running_count`, `free_slots`, `watch_reg_hit`, `row_details`, `row_detail_cap`, `row_detail_omitted`, `lane_empty` | Per-row first-reject snapshot when predecessor lookup finds no incumbent. |
| `giw.cursor_auto.hop_cadence_overlap` | `lane`, `execution_ids` | ≥2 operator-purpose streams on one recorded lane (census OVERLAP). |
| `giw.cursor_auto.hop_cadence_predecessor_pushed` | `thread_id`, `registration_id`, `execution_id`, `new_registration_id`, `idempotency_key`, `ok`, `error` | Stand-down paste attempt into the predecessor CSE (outcome in payload). |
| `giw.cursor_auto.hop_cadence_refuse` | `thread_id`, `reason`, `registration_id`, `signal` | Cadence hop refused at request/fire time while incumbent registration streams. |
| `giw.cursor_auto.hop_cadence_registration_advanced` | `thread_id`, `prior_registration_id`, `new_registration_id`, `superseding_execution_id`, `superseded_execution_id` | Watch ``registration_id`` advanced to the matched active-work row (succession confirm or join-half heal after LOOKUP_FAILED). |
| `giw.cursor_auto.hop_cadence_release_deferred` | `thread_id`, `execution_id`, `reason`, `idle_streak` | Succession release refused this tick; reason is the running-split or idle-streak gate. |
| `giw.cursor_auto.hop_cadence_release_without_receipt` | `thread_id`, `execution_id`, `reason` | Succession release proceeded or deferred without a successful push receipt. |
| `giw.cursor_auto.hop_cadence_revoke_breaker` | `thread_id`, `revocation_count`, `breaker_n` | Cadence hop breaker tripped (stall revocations or unjoinable hop failures). |
| `giw.cursor_auto.hop_cadence_seat_rebound` | `thread_id`, `prior_registration_id`, `new_registration_id`, `superseded_execution_id` | Seat holder on a lane rebound to the successor registration. |
| `giw.cursor_auto.hop_cadence_succession_claim_missing_execution_id` | `thread_id`, `job_id`, `mark` | Hop fire claimed succession without a joinable execution_id. |
| `giw.cursor_auto.hop_cadence_succession_confirmed` | `thread_id`, `matched_key`, `watch_registration_id`, `prior_registration_id`, `superseded_execution_id`, `membership_scope`, `freshness` | Live active-work membership first intersects a watch succession claim. |
| `giw.cursor_auto.hop_cadence_succession_revoked` | `thread_id`, `execution_id`, `stall_stage`, `revocation_count` | Succession claim revoked after joinable ``cdp.generate.stalled``. |
| `giw.cursor_auto.queue_owner_restart_bus_unposted` | `job_id`, `thread_id`, `status_code`, `mark`, `bus_notify_mark` | Ledger terminalized queue_owner_restart but bus notify did not land. |
| `giw.cursor_auto.reconcile.rehydrate_exhausted` | `job_id`, `thread_id`, `generation` | A row hit the rehydrate generation cap and was terminalized for real. |
| `giw.cursor_auto.reconcile.rehydrated` | `job_id`, `thread_id`, `generation`, `restart_intent_id` | A queued-never-claimed row survived a GIW restart and is live again. |
| `giw.cursor_auto.reconcile.superseded_by_successor` | `job_id`, `thread_id`, `successor_job_id`, `generation` | A rehydrate-eligible row was terminalized because a same-thread |
| `giw.cursor_auto.wire_skew` | `boundary`, `dropped_fields`, `sender`, `count` | Signal: giw.cursor_auto.wire_skew |
<!-- GENERATED:END region=giw -->

**SDK lane signals (`sdk.lane*`, `sdk.lane_b.*`).** Source: `git-integration-worker` (`cursor_sdk_events.py`). Emitted on admit (lane selection) and Lane-B worktree lifecycle. Row-10 D6: `sdk.lane.selected` carries `contract` and `selecting_predicate` on every admit.

All signals: `role="observation"`, `scope="node"`.

<!-- GENERATED:START region=sdk inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `sdk.lane.selected` | `dispatch_id`, `thread_id`, `lane`, `reason`, `regime_active`, `regime_state`, `contract`, `selecting_predicate` | - |
| `sdk.lane_b.archived` | `branch`, `tag`, `tip_sha` | - |
| `sdk.lane_b.branch_retained` | `dispatch_id`, `branch`, `commits_ahead` | - |
| `sdk.lane_b.committed` | `dispatch_id`, `thread_id`, `head_sha`, `commits_ahead`, `files_committed` | - |
| `sdk.lane_b.debt_aged` | `branch`, `age_s`, `refusing` | - |
| `sdk.lane_b.debt_opened` | `branch` | - |
| `sdk.lane_b.discharged` | `branch`, `verb` | - |
| `sdk.lane_b.disposition_marked` | `branch`, `reason`, `dispatch_id`, `tip_sha?` | - |
| `sdk.lane_b.mint_rolled_back` | `dispatch_id`, `thread_id`, `reason`, `reason_code` | - |
| `sdk.lane_b.minted` | `dispatch_id`, `thread_id`, `worktree_path`, `branch`, `branch_point`, `mint_wait_ms` | - |
| `sdk.lane_b.orphan_aged` | `branch`, `tip_sha`, `age_s`, `origin_dispatch_id?` | - |
| `sdk.lane_b.reaped` | `dispatch_id`, `branch_deleted`, `branch?`, `tip_sha?`, `reason?` | - |
| `sdk.lane_b.salvage_failed` | `dispatch_id`, `branch`, `worktree_path`, `error` | - |
| `sdk.lane_b.salvaged` | `dispatch_id`, `thread_id`, `head_sha`, `trigger` | - |
| `sdk.lane_b.workspaces_write_refused` | `dispatch_id`, `thread_id`, `uri` | - |
| `sdk.lane_b.worktree_missing_observed` | `dispatch_id`, `thread_id`, `lease_key`, `source_repo` | - |
<!-- GENERATED:END region=sdk -->

**CDP registry signals (`cdp.port.*`, `cdp.occupancy.*`, `cdp.provenance.*`).** Source:
`claude_bundles` via best-effort UDS ingest on
`libs/claude_bundles/cdp_registry_events.py` (`emit()` →
`/tmp/universal-protocol/events.sock`). The registry journal is the durable
authority for provenance episodes; these events are observability mirrors and
must not be used as the sole source for a join.

All signals: `role="observation"`, `scope="node"`.

| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `cdp.occupancy.updated` | `live_cse_count`, `open_attachment_count`, `live_cse_target_count`, `live_port_count`, `registry_capacity_count`, `freshness`, `previous_freshness` | `error`; `live_cse_count` is unique normalized CSE session URLs, `open_attachment_count` is CSE-bearing live CDP hosts, and `live_cse_target_count` preserves qualifying page-target evidence. |
| `cdp.provenance.bound` | `episode_id`, `chat_url`, `registration_id`, `cdp_url`, `lane_thread`, `parent_thread`, `lane_role`, `evidence_class`, `attribution_source`, `correlation_id` | `lineage_state`, `association_id`; durable provenance episode appended for a CSE URL and registry host. |
| `cdp.provenance.conflict` | `chat_url`, `candidate_count` | `correlation_id`; multiple active provenance candidates prevent a unique join. |
| `cdp.provenance.historical` | `episode_id`, `chat_url`, `reason` | An earlier immutable episode remains retained but is no longer current. |
| `cdp.provenance.unresolved` | `chat_url`, `reason` | `correlation_id`; a CSE identity lacks sufficient evidence for a unique join. |
| `cdp.port.exit_kill_decision` | `purpose`, `registration_id`, `port`, `kill` | Kill/deregister decision at process exit. Emitted by `deregister_on_exit` (`libs/claude_bundles/project_ask_abort.py:147`). |
| `cdp.port.dormant` | `registration_id`, `port`, `purpose`, `chat_url`, `reason` | Chrome released while the CSE URL and seeded profile persist. Emitted by `make_dormant` (`libs/claude_bundles/cdp_registry/dormant.py`). `port` is the freed historical port. |
| `cdp.port.relaunched` | `registration_id`, `port`, `purpose`, `chat_url` | Dormant seat reopened on a fresh port with its retained profile. Emitted by `relaunch_dormant`. |
| `cdp.port.dormant_reclaimed` | `registration_ids`, `count`, `trigger` | Dormant rows dropped past TTL (`CDP_DORMANT_TTL_S`) or over the row cap (`CDP_DORMANT_MAX_ROWS`); profiles are then reclaimed by the released-row hygiene pass. |
| `cdp.port.orphan_scan` | `ports_live`, `ports_skipped_registered`, `ports_examined`, `matched_count`, `rejected_count`, `unevaluable_count` | Orphan scan summary on every scan. Emitted by `log_orphan_scan` (`libs/claude_bundles/cdp_registry/session_address.py:190`). |

`.rejected` `reason_code` enum (Phase 3): `arc_branch_mismatch`, `approval_missing`, `diff_mismatch`, `integrate_conflict`, `gate_failed`, `max_attempts_exhausted`, `worktree_not_found`, `not_a_git_repo`.

**Worker signals (`grokbuild.*`).** Source: `grokbuild-worker`. Added V2. SSE-friendly tracker vocabulary plus per-op tracking events; does NOT carry the lib's audit fields (those live on the parallel `mcp.grokbuild.dispatch.completed`).

<!-- GENERATED:START region=grokbuild inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|

<!-- GENERATED:END region=grokbuild -->

**Spool prune (log-only).** Startup `prune_spool()` removes aged dispatch dirs by mtime; it logs `removed_count` only — no `grokbuild.result.pruned` event (Phase 2 chose log-only over UDS publish).

**C.1 header-vs-sidecar JOIN example (discrepancy detection).** Both the
MCP-server header path (C.1(i): `mcp.request.completed` with
`caller_identity=grok-build-dispatch` + `dispatch_id`) and the sidecar parse
path (C.1(ii): `mcp.grokbuild.dispatch.toolcalls`) are CO-PRIMARY attribution
sources. Their disagreement is itself an anomaly signal:

```sql
-- Find dispatches where sidecar tool_count disagrees with MCP-server request count.
SELECT
    tc.dispatch_id,
    json_extract(tc.payload,'$.tool_count') AS sidecar_count,
    COUNT(mrc.id) AS header_count
FROM events tc
LEFT JOIN events mrc
    ON json_extract(mrc.payload,'$.dispatch_id') = json_extract(tc.payload,'$.dispatch_id')
    AND mrc.signal = 'mcp.request.completed'
    AND json_extract(mrc.payload,'$.caller_identity') = 'grok-build-dispatch'
WHERE tc.signal = 'mcp.grokbuild.dispatch.toolcalls'
  AND tc.ts_unix_ms > (unixepoch()-86400)*1000
GROUP BY tc.dispatch_id
HAVING sidecar_count != header_count
```

**Dual-vocabulary observability note.** A single `op="build"` dispatch produces signals from BOTH families:

1. Worker side: `grokbuild.dispatch.accepted` → `grokbuild.dispatch.started` → `grokbuild.dispatch.completed` (coarse, SSE-friendly).
2. Lib side: `mcp.grokbuild.dispatch.called` → `mcp.grokbuild.dispatch.completed` (audit-rich, with `git_diff_stat`, `read_only_violation`).

Both share the same `dispatch_id` so subscribers can JOIN by that field. Admission rejections emit on whichever side rejected:

* Validator/registry rejection → `mcp.grokbuild.dispatch.rejected` only.
* Tracker capacity rejection → `grokbuild.dispatch.rejected` only (worker rejects before the lib is invoked).

**Query examples**:

```bash
# All read-only violations in last 24h (excluding audit-incomplete dispatches)
scripts/query-events --sql "
  SELECT ts_unix_ms,
         json_extract(payload,'\$.dispatch_id') AS dispatch_id,
         json_extract(payload,'\$.git_status_post') AS post,
         json_extract(payload,'\$.git_diff_stat') AS diff
  FROM events
  WHERE signal LIKE 'mcp.grokbuild.dispatch.%'
    AND json_extract(payload,'\$.read_only_violation') = 1
    AND COALESCE(json_extract(payload,'\$.audit_incomplete'), 0) = 0
    AND ts_unix_ms > (unixepoch()-86400)*1000
  ORDER BY ts_unix_ms DESC"

# Async build capacity-rejection rate
scripts/query-events --sql "
  SELECT date(ts_unix_ms/1000,'unixepoch') AS day,
         COUNT(*) AS rejections,
         AVG(CAST(json_extract(payload,'\$.running') AS INT)) AS avg_running_at_reject
  FROM events
  WHERE signal = 'grokbuild.dispatch.rejected'
    AND json_extract(payload,'\$.reason_code') = 'capacity_exhausted'
  GROUP BY day ORDER BY day DESC"

# Join worker + lib views of one dispatch
scripts/query-events --sql "
  SELECT signal, ts_unix_ms,
         json_extract(payload,'\$.outcome') AS outcome,
         json_extract(payload,'\$.read_only_violation') AS ro_violation,
         json_extract(payload,'\$.exit_code') AS exit_code
  FROM events
  WHERE json_extract(payload,'\$.dispatch_id') = :dispatch_id
  ORDER BY ts_unix_ms ASC"

# Registry write-failure heartbeat (sentinel entries_pruned=-1)
scripts/query-events --sql "
  SELECT ts_unix_ms,
         json_extract(payload,'\$.schema_version') AS sv
  FROM events
  WHERE signal = 'mcp.grokbuild.registry.recovered'
    AND json_extract(payload,'\$.entries_pruned') = -1
  ORDER BY ts_unix_ms DESC LIMIT 50"

# Worker degraded-state alerts in last 24h
scripts/query-events --sql "
  SELECT ts_unix_ms, json_extract(payload,'\$.checks') AS failing
  FROM events
  WHERE signal = 'grokbuild.worker.degraded'
    AND ts_unix_ms > (unixepoch()-86400)*1000
  ORDER BY ts_unix_ms DESC"

# Sidecar gap rate — partial NDJSON sidecars (chunk or exit writes failed)
scripts/query-events --sql "
  SELECT signal,
         COUNT(*) AS n,
         SUM(CAST(json_extract(payload,'\$.sidecar_gaps') AS INT)) AS total_gaps
  FROM events
  WHERE signal LIKE 'mcp.grokbuild.dispatch.%'
    AND COALESCE(json_extract(payload,'\$.sidecar_gaps'), 0) > 0
    AND ts_unix_ms > (unixepoch()-86400)*1000
  GROUP BY signal"

# Admission rejection histogram by reason (lib side)
scripts/query-events --sql "
  SELECT json_extract(payload,'\$.reason_code') AS reason, COUNT(*) AS n
  FROM events
  WHERE signal = 'mcp.grokbuild.dispatch.rejected'
    AND ts_unix_ms > (unixepoch()-86400)*1000
  GROUP BY reason ORDER BY n DESC"

# Api-path token cost by tier (last 24h)
scripts/query-events --sql "
  SELECT json_extract(payload,'\$.tier') AS tier,
         COUNT(*) AS dispatches,
         SUM(CAST(json_extract(payload,'\$.prompt_tokens') AS INT)) AS prompt_total,
         SUM(CAST(json_extract(payload,'\$.completion_tokens') AS INT)) AS completion_total,
         SUM(CAST(json_extract(payload,'\$.reasoning_tokens') AS INT)) AS reasoning_total,
         AVG(CAST(json_extract(payload,'\$.duration_s') AS REAL)) AS avg_duration_s
  FROM events
  WHERE signal = 'mcp.grokbuild.apidispatch.completed'
    AND ts_unix_ms > (unixepoch()-86400)*1000
  GROUP BY tier ORDER BY tier"
```


### Tool Catalog Discovery Signals

Emitted by `tool_search` (manifest discovery for demoted tools) and the
`dispatch` envelope when a non-existent tool name is invoked.

| Signal | Trigger | Payload |
|---|---|---|
| `mcp.tool.search.called` | every `tool_search(...)` call | `query`, `limit` |
| `mcp.tool.search.empty` | `tool_search` invoked with empty query | (none) |
| `mcp.tool.search.miss` | `tool_search` returned no matches | `query` |
| `mcp.tool.dispatch.unknown` | `dispatch(tool=X)` for X not in overflow_registry | `tool` (the invented name) |

Use `mcp.tool.search.miss` and `mcp.tool.dispatch.unknown` rates as the primary
rollback signals (see `tasks/discoveries/mcp-tool-definition-context-churn.md`
§ Q10 thresholds). A sudden spike in either signal post-deploy means agents
cannot find demoted tools — investigate manifest coverage or descriptor
quality before disabling the lean partition.

## MCP Stdio Proxy Signals

Fallback-only stdio bridge (`source: "mcp-fastmcp-remote-bridge"`) emits `mcp.bridge.stdio.started`
(`transport=stdio`) at launch — distinct from server-side `mcp.transport.request.*` lifecycle signals. Supersedes legacy `proxy.*` names and the retired custom `mcp-stdio-proxy` publisher. Emitted NDJSON-direct via the events ingest socket — bypasses `event_factory`, so it has no GENERATED catalog row by design (curated here, parity with `fleet.service.step`).

<!-- GENERATED:START region=mcp inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `mcp.adapter.request.shape` | `provider`, `model`, `mcp_version`, `tool_count`, `mcp_tool_count`, `has_tool_search` | Every MCP request — shape summary for v1/v2 migration tracking. |
| `mcp.adapter.search.seen` | `references_count`, `correlation_id?` | Response contained a tool_search_tool_result block. |
| `mcp.adapter.tool.seen` | `tool_name`, `server_name`, `correlation_id?` | Response contained an mcp_tool_use block (Anthropic-executed MCP tool). |
| `mcp.adapter.v2.configured` | `provider`, `server_name`, `always_loaded_count`, `deferred_count` | First request with mcp_v2=true built the toolset payload. |
| `mcp.agentbus.advisory.fired` | `advisory`, `turn_kind`, `chars`, `suppressed_by_profile`, `thread`, `subject` | Signal: mcp.agentbus.advisory.fired |
| `mcp.agentbus.branch.associated` | `thread_id`, `branch_name`, `association_id` | Signal: mcp.agentbus.branch.associated |
| `mcp.agentbus.checkpoint.auto_stamp` | `thread`, `turn_number`, `subject` | Signal: mcp.agentbus.checkpoint.auto_stamp |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.orphan.demoted` | `thread_id`, `orphan_turn_id`, `closeout_turn_id` | Signal: mcp.agentbus.dispatch.orphan.demoted |
| `mcp.agentbus.dispatch.orphaned` | `execution_id`, `thread_id`, `pipeline_id`, `linked_at`, `age_s`, `reason` | Signal: mcp.agentbus.dispatch.orphaned |
| `mcp.agentbus.dispatch.thread.reused` | `thread`, `dispatch_thread_id`, `lane`, `is_auto` | Emitted when generate reuses an existing agent-bus thread. |
| `mcp.agentbus.dispatch.thread.split` | `thread`, `dispatch_thread_id`, `lane` | Emitted when generate mints a sibling thread instead of reusing. |
| `mcp.agentbus.lane.bound` | `thread_id`, `parent_thread_id`, `lane_role`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.lane.bound |
| `mcp.agentbus.quiet_with_wip.fired` | `thread`, `seat`, `reason`, `alarm_id`, `wip_execution_ids` | Signal: mcp.agentbus.quiet_with_wip.fired |
| `mcp.agentbus.sidecar.orphaned` | `uri`, `error`, `thread_id?` | Signal: mcp.agentbus.sidecar.orphaned |
| `mcp.agentbus.sidecar.written` | `thread`, `turn_number`, `uri`, `sha256`, `bytes` | Signal: mcp.agentbus.sidecar.written |
| `mcp.agentbus.thread.abandoned` | `thread`, `reason`, `link_count`, `terminal_count`, `delivered_count` | Thread reaped by watchdog after TTL expiry or all-terminal-no-delivery condition. `reason` values: `pending_ttl_exceeded`, `admitted_ttl_exceeded`, `all_terminal_no_delivery`, `tracker_expired` |
| `mcp.agentbus.thread.closed` | `thread`, `via` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.closed` | `thread`, `via?` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.cse.bound` | `thread_id`, `cse_chat_url`, `cse_registration_id`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.thread.cse.bound |
| `mcp.agentbus.thread.lifecycle.transitioned` | `thread`, `from_state`, `to_state`, `trigger` | Thread lifecycle state machine transition. `role=coordination`. `trigger` values: `create`, `admit`, `turn_posted`, `delivery_sent`, `delivery_failed`, `watchdog_reap`, `reopen`. Emitted from `_transition_lifecycle_state` — single point of correctness for all callers |
| `mcp.agentbus.thread.reopened` | `thread`, `from_state`, `to_state` | Emitted alongside `mcp.agentbus.thread.lifecycle.transitioned` when a turn POST transitions a thread out of a terminal state (completed, abandoned, or failed) back to active |
| `mcp.agentbus.turn.body_over_briefing` | `thread`, `from_agent`, `to_agent`, `subject`, `body_chars`, `target_chars` | Turn body exceeded the briefing target (2k) without allow_long_body, a sidecar, or an inline-contract envelope. Advisory only — the turn was inserted unchanged. |
| `mcp.agentbus.turn.created` | `thread`, `turn_id`, `turn_number`, `from_agent`, `to_agent`, `subject`, `created_at` | Turn inserted in agent-bus store (all transport paths) |
| `mcp.agentbus.watchdog.sweep.failed` | `error` | Watchdog sweep pass raised an unhandled exception. Repeated occurrences indicate a persistent failure in the reap path. |
| `mcp.cortex.op.rejected` | `surface`, `family`, `op` | - |
| `mcp.cse.session.acknowledged` | `registration_id`, `ack_class` | Emit when harvest classifies a typed ACK — not on paste. |
| `mcp.cse.session.conflict` | `reason`, `registration_id`, `chat_url` | Emit on self-supersession or other conflict refusal. |
| `mcp.cse.session.harvested` | `registration_id`, `outcome`, `ack_class`, `turn_count` | Emit when harvest completes or reports an incomplete outcome. |
| `mcp.cse.session.pasted` | `registration_id`, `receipt`, `send_verified`, `replayed` | Emit after paste with receipt — never carries ack_class. |
| `mcp.cse.session.resolved` | `registration_id`, `chat_url`, `state` | Emit when identity resolves on a public provenance read. |
<!-- GENERATED:END region=mcp -->

### OAuth Signals

OAuth signals are emitted by the auth admission middleware and OAuth service
when OAuth is enabled (`MCP_OAUTH_ENABLED=true` with a valid HTTPS issuer).

<!-- GENERATED:START region=mcp inventory_sha=4416bea7d52a generated=2026-08-20T05:44:08Z -->
| Signal | Required Payload | Optional Payload |
|--------|------------------|------------------|
| `mcp.adapter.request.shape` | `provider`, `model`, `mcp_version`, `tool_count`, `mcp_tool_count`, `has_tool_search` | Every MCP request — shape summary for v1/v2 migration tracking. |
| `mcp.adapter.search.seen` | `references_count`, `correlation_id?` | Response contained a tool_search_tool_result block. |
| `mcp.adapter.tool.seen` | `tool_name`, `server_name`, `correlation_id?` | Response contained an mcp_tool_use block (Anthropic-executed MCP tool). |
| `mcp.adapter.v2.configured` | `provider`, `server_name`, `always_loaded_count`, `deferred_count` | First request with mcp_v2=true built the toolset payload. |
| `mcp.agentbus.advisory.fired` | `advisory`, `turn_kind`, `chars`, `suppressed_by_profile`, `thread`, `subject` | Signal: mcp.agentbus.advisory.fired |
| `mcp.agentbus.branch.associated` | `thread_id`, `branch_name`, `association_id` | Signal: mcp.agentbus.branch.associated |
| `mcp.agentbus.checkpoint.auto_stamp` | `thread`, `turn_number`, `subject` | Signal: mcp.agentbus.checkpoint.auto_stamp |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.admit.failed` | `execution_id`, `thread`, `status_code`, `error_preview` | POST /threads/{id}/dispatch-admit failed (non-2xx or transport error). Tracker state is unchanged; fire-and-forget path is observable but non-fatal |
| `mcp.agentbus.dispatch.orphan.demoted` | `thread_id`, `orphan_turn_id`, `closeout_turn_id` | Signal: mcp.agentbus.dispatch.orphan.demoted |
| `mcp.agentbus.dispatch.orphaned` | `execution_id`, `thread_id`, `pipeline_id`, `linked_at`, `age_s`, `reason` | Signal: mcp.agentbus.dispatch.orphaned |
| `mcp.agentbus.dispatch.thread.reused` | `thread`, `dispatch_thread_id`, `lane`, `is_auto` | Emitted when generate reuses an existing agent-bus thread. |
| `mcp.agentbus.dispatch.thread.split` | `thread`, `dispatch_thread_id`, `lane` | Emitted when generate mints a sibling thread instead of reusing. |
| `mcp.agentbus.lane.bound` | `thread_id`, `parent_thread_id`, `lane_role`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.lane.bound |
| `mcp.agentbus.quiet_with_wip.fired` | `thread`, `seat`, `reason`, `alarm_id`, `wip_execution_ids` | Signal: mcp.agentbus.quiet_with_wip.fired |
| `mcp.agentbus.sidecar.orphaned` | `uri`, `error`, `thread_id?` | Signal: mcp.agentbus.sidecar.orphaned |
| `mcp.agentbus.sidecar.written` | `thread`, `turn_number`, `uri`, `sha256`, `bytes` | Signal: mcp.agentbus.sidecar.written |
| `mcp.agentbus.thread.abandoned` | `thread`, `reason`, `link_count`, `terminal_count`, `delivered_count` | Thread reaped by watchdog after TTL expiry or all-terminal-no-delivery condition. `reason` values: `pending_ttl_exceeded`, `admitted_ttl_exceeded`, `all_terminal_no_delivery`, `tracker_expired` |
| `mcp.agentbus.thread.closed` | `thread`, `via` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.closed` | `thread`, `via?` | Atomic close completed (marks all turns read + status=closed). `via` values: `"reply"` (reply-with-close), `"ephemeral_delivery"` (auto-close from pipeline `bus_lifecycle: ephemeral`), `"watchdog_reaper"` (watchdog-initiated close); field may be absent for plain manual closes |
| `mcp.agentbus.thread.cse.bound` | `thread_id`, `cse_chat_url`, `cse_registration_id`, `association_id`, `prior_association_id`, `bound_by` | Signal: mcp.agentbus.thread.cse.bound |
| `mcp.agentbus.thread.lifecycle.transitioned` | `thread`, `from_state`, `to_state`, `trigger` | Thread lifecycle state machine transition. `role=coordination`. `trigger` values: `create`, `admit`, `turn_posted`, `delivery_sent`, `delivery_failed`, `watchdog_reap`, `reopen`. Emitted from `_transition_lifecycle_state` — single point of correctness for all callers |
| `mcp.agentbus.thread.reopened` | `thread`, `from_state`, `to_state` | Emitted alongside `mcp.agentbus.thread.lifecycle.transitioned` when a turn POST transitions a thread out of a terminal state (completed, abandoned, or failed) back to active |
| `mcp.agentbus.turn.body_over_briefing` | `thread`, `from_agent`, `to_agent`, `subject`, `body_chars`, `target_chars` | Turn body exceeded the briefing target (2k) without allow_long_body, a sidecar, or an inline-contract envelope. Advisory only — the turn was inserted unchanged. |
| `mcp.agentbus.turn.created` | `thread`, `turn_id`, `turn_number`, `from_agent`, `to_agent`, `subject`, `created_at` | Turn inserted in agent-bus store (all transport paths) |
| `mcp.agentbus.watchdog.sweep.failed` | `error` | Watchdog sweep pass raised an unhandled exception. Repeated occurrences indicate a persistent failure in the reap path. |
| `mcp.cortex.op.rejected` | `surface`, `family`, `op` | - |
| `mcp.cse.session.acknowledged` | `registration_id`, `ack_class` | Emit when harvest classifies a typed ACK — not on paste. |
| `mcp.cse.session.conflict` | `reason`, `registration_id`, `chat_url` | Emit on self-supersession or other conflict refusal. |
| `mcp.cse.session.harvested` | `registration_id`, `outcome`, `ack_class`, `turn_count` | Emit when harvest completes or reports an incomplete outcome. |
| `mcp.cse.session.pasted` | `registration_id`, `receipt`, `send_verified`, `replayed` | Emit after paste with receipt — never carries ack_class. |
| `mcp.cse.session.resolved` | `registration_id`, `chat_url`, `state` | Emit when identity resolves on a public provenance read. |
<!-- GENERATED:END region=mcp -->

Query example — all tool calls in last 5 minutes:
```
scripts/query-events --sql "SELECT ts_unix_ms, signal, json_extract(payload,'$.path') path FROM events WHERE source='mcp-server' AND signal LIKE 'mcp.tool.%' AND ts_unix_ms > (unixepoch()-300)*1000 ORDER BY seq"
```

## Management API Signals

Emitted by `ManageAPIServer` in `scripts/model_manager/ui/api_server.py`
on every lifecycle operation received over `manage.sock`.

| Signal | Role | Scope | When emitted |
|--------|------|-------|--------------|
| `manage.service.requested` | observation | global | API request received, before execution |
| `manage.service.completed` | observation | global | Operation finished successfully |
| `manage.service.failed` | observation | global | Operation raised an error |
| `manage.restart.deferred` | observation | global | Stop/restart/sync_restart deferred by the drain gate (busy / in_progress / probe_error) |

### Payload Keys

`manage.service.requested`: `method` (str), `service` (str)
`manage.service.completed`: `method` (str), `service` (str), `duration_s` (float)
`manage.service.failed`: `method` (str), `service` (str), `error` (str), `duration_s` (float)
`manage.restart.deferred`: `method` (str), `service` (str), `state` (str ∈ {busy, in_progress, probe_error}), `reason` (str), `retry_after_s` (int)

### MCP Layer Signals

The `manage` MCP tool emits its own observation signals:

| Signal | When |
|--------|------|
| `mcp.manage.service.called` | Tool invoked |
| `mcp.manage.service.completed` | Tool returned success |
| `mcp.manage.service.failed` | Tool returned error |

### Query Example

```bash
# Recent manage API calls
scripts/query-events --sql "SELECT signal, payload FROM events WHERE signal LIKE 'manage.service.%' ORDER BY seq DESC LIMIT 50"

# Failed rebuilds
scripts/query-events --sql "SELECT payload FROM events WHERE signal='manage.service.failed' ORDER BY seq DESC LIMIT 10"
```

## Manage GPU image build signals

Emitted by `./manage` TUI paths: local `build_image` (`ServiceController`) and
remote `deploy_remote` when `--build` is used. Source: `manage`. Role:
`observation`. Scope: `node`.

| Signal | When |
|--------|------|
| `build.image.started` | Operator started a GPU image build (local host or remote hostname) |
| `build.image.completed` | Build subprocess finished (`success`, `duration_s`) |
| `build.image.mismatch` | After rsync + restart, local vs remote `universal-llm-gateway:gpu` build labels differ |

### Payload keys

- `build.image.started`: `host` (str), `scope` (str, e.g. `all` / `llama`)
- `build.image.completed`: `host`, `scope`, `success` (bool), `duration_s` (float)
- `build.image.mismatch`: `host`, `mismatched_fields` (list of str), `local_labels`, `remote_labels` (dicts of compared label keys)

### Query example

```bash
scripts/query-events --sql "SELECT signal, payload FROM events WHERE signal LIKE 'build.image.%' ORDER BY seq DESC LIMIT 30"
```

## Relay socket-dir recovery signal

Emitted by `_recover_root_owned_socket_dir` in
`scripts/model_manager/ui/controller/service_config.py` via the sync path in
`observation_event.emit_relay_socket_recovery`. Source: `manage`. Role:
`observation`. Scope: `node`.

| Signal | When |
|--------|------|
| `relay.socket.recovery` | Socket dir recovery path activated (root-owned dir detected; attempt made regardless of outcome) |

### Payload keys

- `relay.socket.recovery`: `socket_dir` (str), `owner_uid` (int, uid before recovery), `recovered` (bool)

Post-deploy, this signal should trend to zero after the relay startup order fix
lands. Any non-zero activation indicates the ordering bug is still reachable
(e.g. via `update.py:restart_local_edge` or a non-relay caller).

### Query example

```bash
scripts/query-events --sql "SELECT signal, payload FROM events WHERE signal = 'relay.socket.recovery' ORDER BY seq DESC LIMIT 20"
```

## Fleet Operation Signals

Emitted by `scripts/model_manager/observation_event.py` during fleet-wide
service lifecycle operations (stop/start sequences initiated via the manage
API or TUI). Source: `manage`. Role: `observation`. Scope: `node`.

| Signal | Payload | Description |
|---|---|---|
| `fleet.service.step` | `phase`, `service`, `success`, `duration_s` | Per-service timing for each stop/start step within a fleet operation. Emitted after every individual service operation so bottlenecks can be identified by querying grouped by `service`. (NDJSON-direct via events ingest socket — bypasses event_factory) |
| `manage.restart.window.opened` | `window_id`, `scope`, `service_set`, `deadline_at`, `reason` | Operator-authored restart window opened — MUST precede the first stop in the window's service set |
| `manage.restart.window.cleared` | `window_id`, `scope`, `service_set`, `reason` | Restart window cleared (healthy, fleet completed, or TTL sweep) |

### Payload keys

- `fleet.service.step`: `phase` (str, e.g. `stop` / `start`), `service` (str, service name), `success` (bool), `duration_s` (float, rounded to 3 decimal places)
- `manage.restart.window.opened`: `window_id` (str), `scope` (str ∈ {service, fleet}), `service_set` (list[str]), `deadline_at` (str, ISO-8601), `reason` (str)
- `manage.restart.window.cleared`: `window_id` (str), `scope` (str), `service_set` (list[str]), `reason` (str)

### Query example

```bash
scripts/query-events --sql "SELECT json_extract(payload,'$.service') svc, AVG(json_extract(payload,'$.duration_s')) avg_s FROM events WHERE signal='fleet.service.step' GROUP BY svc ORDER BY avg_s DESC"
```



The event service emits its own lifecycle and error signals via the same
event bus. These are routed to the `events` table like all other events.

| Signal | Role | Scope | Description |
|---|---|---|---|
| `event.service.started` | coordination | global | Event service process started, DB opened |
| `event.service.stopped` | coordination | global | Event service graceful shutdown |
| `events.db.write.failed` | observation | node | SQLite batch insert failed (e.g. disk full) |
| `events.dropped.subscribe` | coordination | node | Subscriber queue full, oldest event dropped |
| `events.dropped.ingest` | coordination | global | Ingest ``_db_queue`` full; publisher event dropped (ephemeral fanout only — not persisted; rate-limited, see payload) |
| `publisher.events.dropped` | coordination | node | `UDSEventPublisher._buffer` full; event dropped before reaching UDS (rate-limited, same-buffer emission, see payload) |

`events.dropped.ingest` is pushed only to live WebSocket subscribers (same path as
`events.dropped.subscribe`), not written to SQLite, because the DB writer is
already saturated when the ingest queue overflows.

**`events.dropped.ingest` payload keys**: `count` (int, drops since last notice,
≥1), `queue_depth` (int), `queue_max` (int), `signal_sample` (str, signal field of
the last dropped event), `source` (literal `event_service`).

`publisher.events.dropped` is the symmetric publisher-side signal for
`UDSEventPublisher` buffer overflow (`libs/universal_event_bus/events/debug_broadcaster.py`).
It is emitted into the publisher's own outbound `_buffer` using the same eviction
pattern as dropped user events; it travels to the event service over the same
UDS path and is ingested as a normal event (persisted, subject to the same
`events.dropped.ingest` risk if the ingest queue is also saturated). `scope=node`
because publisher buffers are per-process and not meaningful when re-emitted on
master.

**`publisher.events.dropped` payload keys**: `count` (int, drops since last
notice, ≥1), `buffer_depth` (int), `buffer_max` (int), `signal_sample` (str,
signal field of the last dropped event), `source` (str, publisher identity —
caller-configurable via `UDSEventPublisher(source=...)`, default
`uds_event_publisher`; e.g. `universal_stargate`).

**Saturation interaction — the two drop signals are not independently guaranteed
under sustained overload.** Both notices fan out through the same per-subscriber
queue path. If the DB ingest queue overflows at the same moment a subscriber
queue is also full, the `events.dropped.ingest` notice can itself be evicted by
the subscribe-overflow path and replaced with a `events.dropped.subscribe` notice
(`count=1`). A consumer subscribed only to `events.dropped.ingest` may therefore
miss drops under exactly the conditions that matter most. Throttle handlers
should subscribe to the `events.dropped.*` wildcard and react to either signal
class rather than depending on a specific one.

### Request Snapshot Signals

Request snapshots provide structured before/after records for pipeline
evaluation. The event service routes `request.snapshot.*` signals to both
the `events` table (for querying) and the `request_snapshots` table (for
structured lifecycle queries).

| Signal | Phase | Description |
|---|---|---|
| `request.snapshot.received` | received | Raw request as received by Stargate |
| `request.snapshot.routed` | routed | Routing decision (model, gateway, profile) |
| `request.snapshot.completed` | completed | Response body for non-streaming requests. Nested field `usage` holds token counts; parent signal name is authoritative (not `.usage` child signal). |

### Dispatch economics query operation

Cross-substrate seat token rollup exposed as Event Service / MCP observability operation
`dispatch-economics-token-rollup` (orthogonal to B3 `delivery-audit-token-rollup`).

**Params:** `{since_ts?, until_ts?, minutes?, seat_substrate?, dispatch_id?, execution_id?, request_id?}`

**Join sources (read-only):**

| Substrate | Signal | Canonical mapping |
|---|---|---|
| cursor-sdk | `frontier.sdk.worker.completed` | nested `usage.input_tokens` → `prompt_tokens`; carries `usage_capture_status` |
| stargate-snapshot | `request.snapshot.completed` | nested `usage.prompt_tokens` / `usage.completion_tokens`; `usage.prompt_tokens_details.cached_tokens` → `cache_read_tokens` |
| pipeline-frontier | `pipeline.frontier.dispatch.completed` | flat `prompt_tokens` / `completion_tokens` / `cached_tokens` |
| web-anthropic-cdp | CDP harvest archive (query-time stub) | `usage_capture_status=unavailable`; token columns null |

**Response:** `{rows[], summary{token sums + *_coverage}, join_audit{double_count_rate, orphan_rate, merge_conflict_count, cdp_stub_count}}`

Dedupe on shared non-null `execution_id` with token priority **sdk(captured) > pipeline > snapshot**.
Null token fields remain null (not coerced to zero). Cross-seat headline totals in `summary.comparable_total_tokens`
use `prompt_tokens + completion_tokens` when both are non-null. No G3 dollar fields.

**Query example:**

```python
observability(operation="dispatch-economics-token-rollup", params={"minutes": 1440})
```

### Dispatch economics dollar equivalents (G3)

Query-time join atop G2 exposing comparable seat spend in USD without mutating the G2
token rollup schema or `ModelCapabilityCard`.

**Operation:** `dispatch-economics-dollar-equivalents`

**Params:** `{since_ts?, until_ts?, minutes?, seat_substrate?, dispatch_id?, execution_id?, request_id?}`

**Rate SOT:** `config/model_rates.yaml` (manual seed + `aliases:` + optional `pinned: true`
overrides; override path via `MODEL_RATES_PATH`) plus catalog ingest projection persisted at
`~/.gateway/model_rates_catalog.yaml` (override via `MODEL_RATES_CATALOG_PATH`)
and refreshed in-memory from cloud-proxy catalog refresh (`source=catalog_refresh`).
Catalog refresh emits structured ingest counts
`{upserted, skipped, rejected_negative, rejected_zero_rate}`
and writes the disk projection when upserts occur; `/catalog` dispatch strip behavior is unchanged.
All-zero catalog rates (`input_rate_per_m` and `output_rate_per_m` both 0.0, and
`source != manual_seed_local`) resolve to `cost_source=unavailable` with `cost_usd=null`
— never an authoritative $0 estimate.

**Row fields (added atop each G2 row):**

| Field | Type | Description |
|---|---|---|
| `cost_usd` | float \| null | Authoritative wire USD, rate×tokens estimate, or null |
| `cost_source` | string | `wire` \| `rate_x_tokens` \| `unavailable` |
| `input_rate_per_m` | float \| null | Resolved prompt rate (USD per 1M tokens) when looked up |
| `output_rate_per_m` | float \| null | Resolved completion rate (USD per 1M tokens) when looked up |
| `cache_write_rate_per_m` | float \| null | Resolved cache-write rate when the rate row defines one |
| `cache_read_rate_per_m` | float \| null | Resolved cache-read rate when the rate row defines one |
| `rate_source` | string \| null | `manual_seed`, `catalog_refresh`, etc. |
| `wire_key` | string \| null | Winning wire field (`cost_usd`, `spend`, `cost`) when `cost_source=wire` |

**Precedence:** authoritative USD wire (`cost_usd` / `spend` / `cost`, not `credits`) wins;
else four-term rate×tokens estimate
`(prompt_tokens/1e6)*input_rate_per_m + (cache_write_tokens/1e6)*cache_write_rate_per_m + (cache_read_tokens/1e6)*cache_read_rate_per_m + (completion_tokens/1e6)*output_rate_per_m`
(cache terms skipped when the corresponding rate is null / absent on the rate row);
else `cost_usd=null`, `cost_source=unavailable` (null ≠ 0.0).
`cost_source=rate_x_tokens` is an **estimate** (prefer wire USD when SDK emits it).
Cursor-sdk pinned seeds take rates from https://cursor.com/docs/models-and-pricing.

**Wire predicate (F1):** `0.0` counts as authoritative zero only when the key is present on
the usage dict **and** `usage_capture_status == "captured"`. Missing-key defaults do not win
as wire over a valid rate row.

**Multi-member wire (F3):** spend keys are scanned across all coalesced substrate members;
wire substrate priority is **sdk > pipeline > snapshot** (independent of G2 token priority).

**Response:** G2 body plus `pricing_audit{wire_count, rate_computed_count, unavailable_count, unavailable_rate, …}`.

**Query example:**

```python
observability(operation="dispatch-economics-dollar-equivalents", params={"minutes": 1440})
```

| `request.snapshot.failed` | failed | Error details on failure (`error`, `error_code`, `error_source`, `error_data` — incl. `topology_snapshot` for `MODEL_NOT_FOUND`, `caller_hint`) |

## System Signals

Cross-service diagnostic signals emitted by any service. These signals are not tied to
a specific request or pipeline execution — they capture infrastructure-level events such
as caught exceptions.

**Invariant**: `system.exception` MUST NOT be emitted for control-flow use of exceptions
(e.g., `asyncio.CancelledError`, `StopIteration`). It targets abnormal runtime faults
caught in operational code paths via `except Exception`.

| Signal | Role | Scope | Description |
|---|---|---|---|
| `system.exception` | observation | global | A caught Exception was observed in a service handler or code path |

### `system.exception` Payload

| Field | Required | Type | Description |
|---|---|---|---|
| `exception_type` | yes | string | `type(exc).__name__`, e.g. `'ValueError'` |
| `message` | yes | string | `str(exc)` truncated to 500 chars |
| `service` | yes | string | Originating service, e.g. `'cloud_proxy'`, `'stargate'`, `'rag'` |
| `handler` | no | string | Function or class name where the exception was caught |
| `request_id` | no | string | Request correlation ID if inside a request context |
| `traceback` | no | string | Last frames from `traceback.format_exc()`, truncated to 1000 chars |

### Usage

```python
from universal_event_bus import capture_exception

async with capture_exception("stargate", handler="proxy_request", event_bus=bus):
    await do_risky_operation()
```

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
scripts/query-events --sql "SELECT payload FROM events WHERE signal LIKE 'pipeline.step.%' ORDER BY seq DESC LIMIT 500"

# Timed-out steps with partial token counts
scripts/query-events --sql "SELECT payload FROM events WHERE signal='pipeline.step.failed' ORDER BY seq DESC LIMIT 200"
```
