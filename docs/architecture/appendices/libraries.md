# Appendix: Shared Libraries (`libs/`)

Cross-cutting reference for the shared libraries in `libs/`. These are
imported by Gateway, Stargate, MCP server, pipeline handlers, and scripts
as top-level modules via `PYTHONPATH=libs/`.

---

## `libs/agent_bus_store`

Embeddable agent-bus library. Provides the in-process store backing the
`agent_bus` MCP tool. Can also run standalone (`python -m agent_bus_store serve`).
Used by the `agent_bus` service.

---

## `libs/agent_seat`

Agent-seat primitives: tool definitions, tool hydration, system-prompt assembly,
and the native tool loop. Shared between the MCP server's `frontier_dispatch`
tool and Stargate pipeline handlers that drive multi-turn frontier dispatch.
Works with `libs/llm_adapters` for provider-native request/response translation.

---

## `libs/cortex_store`

Embeddable Cortex store library. Backing store for the Cortex knowledge graph.
Used by the `cortex_api` service. Subpackages include `dispatch_ops/`
(including bulk entity / relationship writes and modular audit detectors),
`entity_aliases.py`, HTTP `routes/` (assertions, ingest, resolve), optional
`ingest_chunker/` for chunked ingest, and `type_schemas.py` for typed payloads.

---

## `libs/doc_extraction`

Deterministic tree-sitter extraction for Python code inventory. Provides
`extract_file_inventory` / `extract_subsystem_inventory` for the doc-generate
pipeline (`/overhaul` command). No runtime service dependency.

---

## `libs/event_store`

Embeddable Event Service library. Can run standalone
(`python -m event_store serve`). Backing store and query layer for the Event
Service. Used by the `event_service` systemd service.

---

## `libs/frontier_observability`

Caller-agnostic observability helpers for frontier dispatch responses. Shared
between Stargate's `frontier_dispatch_v1` pipeline handler and the MCP
server's `frontier_dispatch` tool. Centralises usage / token / latency event
emission so callers don't duplicate it.

---

## `libs/inference_djinn`

Async inference worker package. Manages asynchronous inference workers over
Unix sockets. Supports GGUF, VLLM, and ExLlamaV3 engines with automatic chat
template detection. Used exclusively by Gateway workers.

---

## `libs/intelligence_profiles`

Per-model quality and suitability metadata. Provides `IntelligenceProfile`
(per-model schema) and `ModelRequirements` (declarative pipeline model
selection). Used by pipeline handlers to select the best available model for a
task without hard-coding model IDs.

---

## `libs/llm_adapters`

Provider adapters for frontier LLM calls. Owns provider-native
request/response shape translation (`build_frontier_request`,
`parse_frontier_response`, `append_tool_round`). Shared between MCP server and
Stargate pipeline handlers. Works with `libs/agent_seat`'s native tool loop.
Providers: Anthropic, OpenAI, OpenRouter, Google.

---

## `libs/model_id`

Shared Model ID parsing and normalisation library. Parse once at API
boundaries, pass `ModelId` objects internally. Key properties:
`.routing_key` (gateway API calls), `.normalized` (dict keys),
`.catalog_lookup_id` (config lookup), `.synthetic_id` (wire serialisation).
`str(model_id)` returns `.original` — display only, not for equality checks.

See workspace rule `modelid.mdc` for the full invariant set.

---

## `libs/ocr_core`

Shared OCR and vision-resize helpers: PDF/image page rendering, adaptive resize to
token budgets, Stargate vision calls (`ocr_pages`, `ocr_directory`). Used by the
MCP server's document OCR tools and related ingest paths; keeps OCR logic out of
`services/mcp-server/tools/` as a thin relay layer.

---

## `libs/process_ipc`

Simplified inter-process communication for single-worker process management.
Provides the IPC socket layer between Gateway and individual inference workers.

---

## `libs/provenance`

Provenance tracking for pipeline artifacts. `Provenance` is an immutable
record tracking content authorship and processing lineage. Used by RAG
contextualization and doc-generate pipelines.

---

## `libs/role_lint`

Stdlib-only validation for ``role:{slug}`` Cortex payloads. Rejects
identity-coded prose in linted fields so dispatch entities stay
execution-contract language; first-person persona copy belongs on
``persona_seed_ref`` / birth prompts, not on the role entity. Used by the
Cortex sync script path that publishes roles. See
``notes/system/specs/role-schema-self-concept-lint.md``.

---

## `libs/sse`

Domain-agnostic Server-Sent Events (SSE) primitives. W3C-compliant byte→event
framing (`iter_sse_events`), accumulator driver (`accumulate_sse_stream`),
reducer protocol (`SSEReducer[State]`), and exception hierarchy
(`SSEStallError`, `SSETimeoutError`, `SSEReductionError`, `SSEProviderError`,
`SSEParseError`). Used by `libs/llm_adapters/streaming/*` (per-provider stream
reducers, future) and any consumer needing pure SSE handling. No LLM knowledge.

Promoted from `libs/universal_protocol/sse/` in the `sse-lib-promotion`
refactor. The `universal_protocol.sse` shim is deprecated — import from `sse`
directly.

---

## `libs/transport_utils`

UDS/TCP service client factories. Canonical entry point for all
internal HTTP clients: `make_async_client(url)` / `make_sync_client(url)`.
Exports pre-wired socket path constants (`CORTEX_SOCKET_PATH`,
`RAG_SOCKET_PATH`, etc.) and default URL helpers. All service-to-service HTTP
clients MUST use this library — not raw `httpx.AsyncClient(transport=...)`.

See workspace rule `uds-only-transport.mdc` for the full invariant.

---

## `libs/universal_concurrency`

FIFO-fair capacity gating and waiter management as semaphore replacements.
Provides composable primitives for service-specific admission-control policy.
Used by Stargate's routing and admission layers.

---

## `libs/universal_event_bus`

Shared event-driven infrastructure for Gateway and Stargate. Provides the
event bus, publish/subscribe transports (sync and async), monitoring, and
actor patterns with race-free design. This is the in-process half of the
event pipeline; the Event Service (`libs/event_store`) is the persistence
half.

Event construction invariant: `@event_factory` — never `Event(...)` directly.
Signal format: `^[a-z]+(\.[a-z]+){1,4}$` (no underscores, hyphens, or digits).

---

## `libs/universal_hot_reload`

Pure-async file monitoring with debouncing, backed by `watchfiles` (Rust).
Provides hot-reload support for configuration files and catalog YAML without
service restarts. Used by Gateway, Stargate, and any service needing live
file watching.

---

## `libs/universal_logging`

Reusable logging module with automatic initialisation, caller information, and
flexible configuration. Used by every service and lib as the single logging
entry point (`from universal_logging import get_logger`).

---

## `libs/universal_protocol`

HTTP/1.1 + WebSocket over Unix Sockets protocol layer. Provides the error
envelope (`ProtocolError`, `ErrorCode`), stream and request ID generation,
WebSocket streaming primitives (`UnboundedStreamQueue`, `StreamContext`,
`StreamClient`), and RPC client/server infrastructure.

SSE primitives were promoted to `libs/sse/` (see above). The
`universal_protocol.sse` shim remains for backward compatibility during
migration but is deprecated.

---

## `libs/universal_transport`

Ecosystem foundation layer. Modern async transport with length-prefixed
framing, eliminating `asyncio` readline buffer limits and handling multi-MB
messages efficiently. Underlying transport used by `universal_protocol`'s
RPC layer.

---

## `libs/universal_workspace`

Workspace root and static catalog path resolution. Thin utility used by
services that need to locate the repo root or config YAML at startup without
hardcoding paths.

---

## `libs/web_fetcher`

Browser-based web fetching service with Cloudflare bypass. Exposes a ASGI app
(`create_app()`) used by the `web_fetcher` service for pipeline steps that
need to retrieve live web content.

---

## Standalone Files

| File | Purpose |
|---|---|
| `libs/markdown_sections.py` | Section-level markdown read/write helpers used by `libs/doc_extraction` and pipeline doc handlers |
| `libs/pipeline_assess_registry.py` | Registry of pipeline assessment metrics |
| `libs/provider_model_limits.py` | Static per-provider context-length and rate-limit constants |
