# Event System

Events are central to the architecture — not an afterthought for debugging.
They serve coordination (concurrent subsystems synchronize via events),
coherence (lifecycle guarantees), and observability (authoritative record).

## Event Construction

All events constructed via `@event_factory`. Direct `Event()` is blocked at runtime.

```python
@event_factory
def ModelExecutionCompleted(model_id: str) -> Event: ...
```

Signal format: `^[a-z]+(\.[a-z]+){1,4}$` (e.g., `model.execution.completed`).

Events carry `role` (coordination | observation) and `scope` (node | global). See `docs/event-contracts.md` § Signal Classification.

## Publish Modes

| Mode | Use case | Blocks? |
|---|---|---|
| `publish_async_nowait()` | Request path | No |
| `await publish_async()` | Background/init | Yes (waits for delivery) |

`event_bus.publish()` (sync) has been removed.

## Event Persistence

| Node | Path |
|---|---|
| Master (host) | `/tmp/stargate-events/current.jsonl` |
| Master (host) | `/tmp/pipeline-events/current.jsonl` (pipeline signals only) |
| Master (host) | `/tmp/rag-events/current.jsonl` (RAG signals) |
| Edge Stargate (container) | `/tmp/stargate-events/current.jsonl` |
| Edge Gateway (container) | `/tmp/_universal-gateway-events/current.jsonl` |

Container paths are not volume-mounted — access via `docker exec`.

## Telemetry Events

Constructed via `@telemetry_factory` in `libs/universal_protocol/`.

| Type | Content |
|---|---|
| `ResourceUpdate` | VRAM/RAM, active requests |
| `ModelLoaded` / `ModelUnloaded` | Model lifecycle |
| `ModelBusy` / `ModelIdle` | Per-model state |
| `ModelLoadingStarted` / `ModelLoadFailed` | Load progress |
| `TelemetryHeartbeat` | Liveness |
| `GatewaySnapshot` | Full catalog + resources |

Wire format: `MessageEnvelope(type, timestamp, data)`.

## Pipeline Events

Published to EventBus as `pipeline.*` signals. Persisted to
`/tmp/pipeline-events/current.jsonl` (filtered sink).

| Signal | Trigger |
|---|---|
| `pipeline.started` / `pipeline.completed` / `pipeline.failed` | Pipeline lifecycle |
| `pipeline.step.started` / `pipeline.step.completed` / `pipeline.step.failed` | Step lifecycle |
| `pipeline.step.inputs.captured` / `pipeline.step.output.captured` | Step I/O |
| `pipeline.model.invocation` | Each `_call_model()` |
| `pipeline.rag.scope.rejected` | Scope validation failed; retrieval returns 0 chunks (fail-closed) |
| `pipeline.rag.retrieval.params.resolved` | Effective retrieval params resolved after validation |
| `pipeline.rag.retrieval.completed` / `pipeline.rag.retrieval.failed` | Retrieval terminal outcomes on non-rejected path |

`pipeline.step.failed` payload includes `prompt_tokens`, `completion_tokens`,
`model_call_count` — populated even on timeout.

## RAG Events

Published to EventBus as `rag.*` signals. Persisted to
`/tmp/rag-events/current.jsonl`.

| Signal | Trigger |
|---|---|
| `rag.started` / `rag.shutdown` | RAG lifecycle |
| `rag.watch.started` / `rag.watch.directory.missing` / `rag.watch.initial.complete` / `rag.watch.reindex.complete` / `rag.watch.reconcile.complete` / `rag.watch.stopped` | Watcher lifecycle, missing-path detection, initial/reconcile progress, and live reindex activity |
| `rag.scope.resolved` / `rag.scope.rejected` / `rag.scopes.listed` | Scope resolution, validation, and scope discovery API |
| `rag.extraction.completed` / `rag.extraction.failed` | Index-time structured extraction outcome per chunk |
| `rag.extraction.batch.started` / `rag.extraction.batch.completed` | File-level extraction batch lifecycle |
| `rag.property.index.rebuilt` / `rag.pending.reconciled` | Property index rebuilds and startup recovery of interrupted indexing |
| `rag.file.indexed` / `rag.file.deleted` / `rag.file.skipped` / `rag.file.indexing.failed` | File-level indexing outcomes (success, deletion, skip reason, failure) |
| `rag.search.executed` / `rag.search.no_results` | Search completion telemetry and zero-result visibility |

## Routing Events

| Signal | Trigger |
|---|---|
| `ROUTING_DECISION` | Successful gateway selection |
| `ROUTING_DECISION_FAILED` | No feasible gateway |

## Federation Events

| Signal | Trigger |
|---|---|
| `federation.gateway.connected` / `disconnected` | Gateway lifecycle |
| `federation.telemetry.marked.stale` | Stale telemetry |
| `federation.routing.rejected` | Routing rejection |
| `federation.load.failed` | Model load failure |
| `GATEWAY_RESOURCE_UPDATE` | Telemetry update from edge |
| `federation.catalog.vram.drift` | nvidia-smi measured VRAM diverges >5% from catalog estimate |

### `federation.catalog.vram.drift`

Emitted by the Master WebSocket client (`gateway_websocket/handler/system.py`) when
a `RESOURCE_UPDATE.model_vram` reading diverges from the catalog's `vram_usage` by more
than 5%. Indicates the catalog profile may need updating. Observability-only — does not
trigger automatic catalog correction.

Payload: `gateway_id`, `model_id`, `measured_mb`, `catalog_mb`, `drift_pct`.
Rate-limited: once per model per hour (cooldown in `ws_client/state.py:can_report_vram_drift`).

Source: `src/scheduling/events.py:FEDERATION_CATALOG_VRAM_DRIFT` / `create_catalog_vram_drift_event()`

> Note: unlike other events this factory is a plain function (not `@event_factory`) because
> it is called from an async context that already holds a running event loop. The signal
> constant and payload contract are the same.

## Request Events

| Signal | Trigger |
|---|---|
| `request.started` | Request begins processing |
| `request.completed` | Request completes (success) |
| `request.failed` | Request fails |
| `request.timed.out` | Request timeout |

Lifecycle guarantee: `request.started` ⟹ exactly one of
`request.completed`, `request.failed`, `request.timed.out`.

## Event Contracts

Signal format: `^[a-z]+(\.[a-z]+){1,4}$`
Required fields: `signal`, `payload`, `timestamp`, `id`.
Correlation: `payload.request_id` for request tracing,
`payload.correlation_id` for federated tracing.

## Architecture Change Rule

∀ architecture changes: event vocabulary must evolve to match.
- New capability → new signal(s)
- Changed flow → update/add signals at decision points
- New failure mode → new signal for visibility
- Removed behavior → deprecate/remove signals

## Quick Event Queries

```bash
tail -20 /tmp/stargate-events/current.jsonl | jq -c '.'
jq -c 'select(.signal == "request.failed")' /tmp/stargate-events/current.jsonl
jq -c 'select(.signal | startswith("federation"))' /tmp/stargate-events/current.jsonl
jq -c 'select(.payload.request_id == "ID")' /tmp/stargate-events/current.jsonl
jq -c 'select(.signal | test("pipeline.step.(started|completed|failed)"))' /tmp/pipeline-events/current.jsonl
```
