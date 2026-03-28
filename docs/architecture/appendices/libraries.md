# Shared Libraries

All libraries live in `libs/` and are importable via `PYTHONPATH` (set by `sitecustomize.py`).

## Library Overview

| Library | Purpose | Key Consumers |
|---|---|---|
| `universal_event_bus` | Event pub/sub for coordination and observability | All services |
| `universal_protocol` | JSON-RPC, error envelopes, telemetry, streaming | Stargate, Gateway |
| `universal_transport` | Async transport (Unix/TCP sockets, framing) | Gateway workers, IPC |
| `process_ipc` | Worker process lifecycle and IPC | Gateway |
| `universal_logging` | Structured JSON logging | All services and libs |
| `universal_concurrency` | FIFO-fair capacity primitives | Gateway |
| `universal_hot_reload` | Async file watching with debounce | Stargate, RAG, Gateway |
| `model_id` | Model ID parsing and normalization | Stargate, Gateway |
| `provenance` | Pipeline artifact provenance tracking | Consensus pipelines |
| `inference_djinn` | Inference engine abstraction | Gateway workers |
| `transport_utils` | Shared HTTP transport helpers for UDS/TCP resolution | Stargate pipelines, tooling |
| `event_store` | Embeddable event-service core (ingest, query, retention, operations) | Event Service, observability tooling |
| `agent_bus_store` | Embeddable agent-bus core (FastAPI app, routes, DB layer, CLI server) | Agent Bus service wrapper, orchestration tooling |

## universal_event_bus

Event-driven pub/sub for coordination and observability.

**Key exports**: `Event`, `EventBus`, `event_factory`, `MinimalEventDebugBroadcaster`

**Event system**:
- `Event`: dataclass with `signal`, `payload`, `timestamp`, `id`
- Direct `Event()` construction **blocked at runtime**; must use `@event_factory`
- Signal format: `^[a-z]+(\.[a-z]+){1,4}$` (validated by decorator)

**Publish modes**:
- `publish_async_nowait()` — request path (fire-and-forget)
- `await publish_async()` — background/init (waits for delivery)

**Sequential execution**: `@sequential` decorator for lock-free sequential operations.
`FederatedGatewayManager` uses this for thread-safe state updates.

**Rate limiting**: `RateLimitedEventSource` for backpressure on federation telemetry.

## universal_protocol

Transport-agnostic protocol: JSON-RPC 2.0, WebSocket streaming, SSE, state channels.

**Error system**:
- `ProtocolError(code, message, source, retryable, data)` with `to_dict()`
- `ErrorCode` (StrEnum): `STICKY_CAPACITY`, `STREAM_LIMIT_EXCEEDED`, `OOM`, etc.
- Subtypes: `RPCError`, `StreamError`, `EngineError`
- `error_envelope()` for dict-shaped errors
- `is_retryable(code)`, `get_http_status(code)` utilities

**Telemetry**:
- 9 types via `@telemetry_factory`: `ResourceUpdate`, `ModelLoaded`, `ModelUnloaded`,
  `ModelBusy`, `ModelIdle`, `ModelLoadingStarted`, `ModelLoadFailed`,
  `TelemetryHeartbeat`, `GatewaySnapshot`
- `TelemetrySource(stargate_id, gateway_id, node_id)`
- Wire format: `MessageEnvelope(type, timestamp, data)`

**Streaming**: `BoundedQueue.try_put()` for fire-and-forget (no HOL blocking).
Sustained overflow (3 consecutive failures) triggers reconnect.

## universal_transport

Async transport with length-prefixed framing for multi-MB messages.

**Transports**: `AsyncUnixTransport`, `AsyncTCPTransport`, servers for both.
**MessagePump**: correlation-based request/response matching, concurrent I/O.
**Protocols**: `LengthPrefixedProtocol` with JSON, MessagePack, raw, protobuf serializers.
**Client/Server**: `create_unix_client()`, `create_tcp_client()`, `create_unix_server()`, etc.

## process_ipc

Worker process lifecycle: supervisor, health monitoring, IPC.

**Key classes**:
- `ProcessSupervisor` — manages worker lifecycle via HTTP JSON-RPC over Unix sockets
- `WorkerProcess` — worker subprocess wrapper
- `WorkerInterface` (ABC) — `initialize`, `process_command`, `emit_event`, `health_check`, `shutdown`

**Signals**: factory functions (`Ready()`, `ProcessCrashDetected()`, etc.) returning `dict[str, Any]`.

## universal_logging

Centralized JSON logging with canonical schema.

**Setup**: `setup(config)` with truncation, max string length.
**Schema**: `@timestamp`, `level`, `logger`, `message`, `caller`, `error`, `extra`.
**Renderers**: JSON, compact JSON, pretty JSON, colorized JSON.
**Bootstrap**: `BootstrapLogger` for pre-setup logging.

## universal_concurrency

FIFO-fair concurrency primitives for capacity and request queuing.

- `FifoCapacityGate` — FIFO capacity gate (semaphore replacement)
- `CapacityCounter` — counter with release callback
- `FifoWaitQueue` — O(1) FIFO waiter queue
- Atomic `try_acquire()` for fast path

Used by Gateway for per-worker concurrency limiting.

## model_id

Model ID parsing, normalization, and validation.

`ModelId` properties: `base_id`, `context_length`, `is_hybrid`, `routing_key`,
`normalized`, `catalog_lookup_id`, `synthetic_id`, `original`.

Functions: `parse_model_id()`, `validate_model_id()`, `get_compute_type()`.

## provenance

Pipeline artifact provenance for cross-model independence.

- `Provenance(originator_model_id, originator_step_id, lineage)` with `with_processor()`
- `is_independent(provenance, evaluator_model_id)` — independence check
- Used by consensus pipelines to prevent model self-verification

## transport_utils

Small shared transport helpers for service clients that support both Unix domain
socket and TCP connectivity.

- `rag_client.py` resolves RAG endpoint from `~/.gateway/stargate.yaml` with
  UDS default (`unix:///tmp/universal-protocol/rag.sock`) and TCP opt-in
- Exposes `make_sync_client()` and `make_async_client()` using `httpx`
  UDS transports when needed

## event_store

Embeddable event-service library extracted from `services/event-service/`.

- `server.py` provides `run_service()`, `start_event_service()`, and `create_app()`
- `ingest.py` handles NDJSON ingest over UDS/TCP with queue-backed DB writer
- `store.py` owns SQLite schema, retention, and realtime ring buffer
- `operations.py`, `operations_impl.py`, and `operations_trace.py` define named
  observability operations and dispatch
- `query.py` and `subscribe.py` expose HTTP query and WebSocket subscribe handlers

## agent_bus_store

Embeddable agent-bus library extracted from `services/agent-bus/src/`.

- `server.py` provides `create_app()`, `run_service()`, and `start_agent_bus()`
- `__main__.py` provides CLI startup via `python -m agent_bus_store serve`
- `db/` contains SQLite schema and CRUD helpers for legacy messages, threads, and turns
- `routes/` contains FastAPI route handlers for `/messages`, `/threads`, and `/turns`
- `auth.py`, `models.py`, and `turns_models.py` expose auth dependency and Pydantic API models

## Dependency Graph

```
universal_logging  ←── all libs and services
        ↑
universal_event_bus ←── all services, process_ipc
        ↑
universal_transport ←── process_ipc
        ↑
process_ipc ←── gateway (workers, lifecycle)
universal_protocol ←── gateway (RPC, streaming), stargate (federation, proxy)
model_id ←── stargate (routing), gateway (resolution)
provenance ←── consensus pipelines
universal_concurrency ←── gateway (FifoCapacityGate)
universal_hot_reload ←── stargate, rag, gateway (file watching)
```
