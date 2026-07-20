# Universal Stargate `src` — Architecture (Step 9, Post-Enhance)

<!-- GENERATED:START source_corpus=overhaul-stargate-src/source (post-enhance) inventory_sha=fb90071da9506ce7510156b162fa8b generated=2026-07-20 session=web-anthropic-2026-07-20 arc=agent-bus:5478 -->

_Generated from docstrings, signatures, and imports; claims reflect what the source **declares**, not verified runtime behavior. CDP Sonnet draft verifies doc↔declaration consistency, not docstring↔behavior truth._

## Scope of this pass

Subsystem: `services/universal-stargate/src` (label **universal-stargate-src**). Feedstock: docstring-quality **0 critical / 0 warning** across all 86 modularize-scanned files (all ≤300 lines — 0 yellow, 0 red), post §5.6 enhance harvest (54 edits) — `cortex://notes/system/threads/overhaul-stargate-src/source/doc-scan-summary.txt`, `cortex://notes/system/threads/cdp-ask-archive-new-d5d38e73.md`. The prior aborted underfed step-9 draft `b398934a` was **not** promoted into this pass — `cortex://notes/system/threads/overhaul-stargate-src/source/doc-source-manifest.txt`.

This pass covers `core/`, `scheduling/` (consumers, events, schemas, gateway state), and top-level `schemas/`, with emphasis on the package-shadow split of `scheduling/events/{request,federation_signaling,pipeline,routing_failures,model_lifecycle}/` and the `scheduling/events` barrel. Coverage is bounded by what the staged corpus excerpted — see `missing_coverage` below; several leaf modules named in package `__init__`/consumer docstrings were not themselves staged this pass and are not described beyond their declared names.

## 1. Package map

| Package | Declared purpose | Source |
|---|---|---|
| `src` (root) | "Universal Stargate `src` package — scheduling, schemas, and core helpers." | `cortex://notes/system/threads/overhaul-stargate-src/source/doc-excerpts/__init__.py` |
| `src.core` | "Stargate core utilities — gateway tracking, transport, streaming, monitoring." | `cortex://notes/system/threads/overhaul-stargate-src/source/doc-excerpts/core/__init__.py` |
| `src.scheduling` | "Stargate scheduling package — event bus wiring, consumers, and gateway state." WebSocket-first control plane; event-driven consumers only (no polling loops — Phase 2 complete). | `cortex://notes/system/threads/overhaul-stargate-src/source/doc-excerpts/scheduling/__init__.py` |
| `src.schemas` | "Pydantic schemas for API request/response validation." | `cortex://notes/system/threads/overhaul-stargate-src/source/doc-excerpts/schemas/__init__.py` |

<!-- HUMAN: the staged corpus does not include a top-level services/universal-stargate/src README or service-entry module beyond these four package __init__.py docstrings — service wiring (e.g. how core/scheduling/schemas compose into the running Stargate process) is not covered by this pass. -->

## 2. `scheduling/events` — package-shadow and the thin barrel

### 2.1 Barrel (`scheduling/events/__init__.py`)

Declared as the single import surface for all event signal constants and `@event_factory` classes across twelve domain modules (routing, routing_failures, gateway, model_lifecycle, request, queue, federation_signaling, federation_load, cloud, proxy, pipeline, system), plus `routing_debug`, `snapshot`, and a `cursor_catalog` domain not named in the docstring's domain list but present in both the import block and `__all__`. The module docstring documents the `EventBus`-injected fields (`timestamp` ISO-8601 with `Z` suffix, monotonic `id`) and gives canonical usage (`from src.scheduling.events import GatewayStateChanged`).

Structurally the barrel is **pure re-export**: a `from .<domain> import *` block per domain followed by an explicit `__all__` enumerating every signal constant and factory/event class name across domains (~240 entries). It contains no business logic of its own — the "thin" characterization is architectural (zero decision logic, zero state), not textual (the `__all__` list itself is long by construction).

— `cortex://notes/system/threads/overhaul-stargate-src/source/doc-excerpts/scheduling/events/__init__.py`, `cortex://notes/system/threads/overhaul-stargate-src/source/review/events-init.py` (identical content, cross-checked)

**Cross-check (package-shadow review probe):** signal string constants for the five package-shadowed domains (`request`, `federation_signaling`, `pipeline`, `routing_failures`, `model_lifecycle`) show **0 drift vs HEAD, 0 missing**; no `signal=` renames; the historical combined `__all__` (243 symbols) still resolves 243/243; `check-imports` PASS; modularize 86/0/0. — `cortex://notes/system/threads/overhaul-stargate-src/source/review/events-probe.txt`

**Working-tree state at time of staging** (uncommitted, per `git status`): the five former monolithic modules (`federation_signaling.py`, `model_lifecycle.py`, `pipeline.py`, `request.py`, `routing_failures.py`) are staged as **deleted**, and five new untracked package directories of the same names exist in their place; `scheduling/events/__init__.py` itself, `federation_load.py`, `gateway.py`, `queue.py`, `routing_debug.py`, `snapshot.py`, `system.py`, and several ancestor `__init__.py` files show as modified. This is consistent with an in-flight, not-yet-committed package-shadow split. — `cortex://notes/system/threads/overhaul-stargate-src/source/review/git-status.txt`

### 2.2 `scheduling/events/request/` (package-shadow of former `request.py`)

Facade docstring: "Package-shadow of the former `request.py` module. Implementation lives in responsibility-named submodules." Re-exports from four submodules:

| Submodule | Signals declared | Notes |
|---|---|---|
| `lifecycle.py` | `REQUEST_QUEUED`, `REQUEST_PROCESSING`, `REQUEST_INFERENCE_STARTED`, `REQUEST_PROFILE_RESOLVED`, `REQUEST_ALIAS_RESOLVED`, `REQUEST_COMPLETED` | Admission through successful completion. `RequestInferenceStarted` docstring: emitted on downstream-confirmed runtime start, a later boundary than `request.processing`. |
| `failure.py` | `REQUEST_FAILED`, `REQUEST_TIMEOUT` (`request.timed.out`), `REQUEST_DEADLINE_EXCEEDED`, `REQUEST_CLIENT_DISCONNECTED`, `REQUEST_REMOVED` | Module docstring: split out from lifecycle because the `REQUEST_FAILED` diagnostic docstring (payload shape + three example SQL/jq diagnostic queries covering `MODEL_NOT_FOUND`, unavailable-pipeline, and offline-edge cases) alone would push a combined module over the SLOC ceiling. `REQUEST_DEADLINE_EXCEEDED` is explicitly declared distinct from `REQUEST_TIMEOUT`: deadline = inference-budget exceeded mid-flight; timeout = queue TTL expired pre-admission. |
| `federation_snapshot.py` | `FEDERATION_SNAPSHOT_SENT` | "Historically owned by the request module; kept here per Opus bind." Payload documents the `all_models_count` vs `available_models_count` gap (models visible in `/v1/models` vs. actually routable). |
| `model_selection.py` | `MODEL_SELECTION_HEALTH_OBSERVATION`, `MODEL_SELECTION_SCORE_UPDATED`, `MODEL_SELECTION_RANK_COMPUTED`, `MODEL_SELECTION_SWITCH_SUPPRESSED`, `MODEL_SELECTION_SWITCH_ALLOWED` | Reputation/anti-thrash signals for profile selection; "switch suppressed/allowed" pair declares a `delta` vs. `min_switch_delta` threshold semantic. |

— `cortex://notes/system/threads/overhaul-stargate-src/source/review/request/__init__.py`, `.../review/request/lifecycle.py`, `.../review/request/failure.py`, `.../review/request/federation_snapshot.py`, `.../review/request/model_selection.py`

### 2.3 `scheduling/events/federation_signaling/` (package-shadow of former `federation_signaling.py`)

Facade docstring: control-plane signaling between Master and remote Edge/Relay nodes — authentication, telemetry flow, routing delegation, VRAM probes, gateway health.

| Submodule | Signals declared |
|---|---|
| `connection.py` | `FEDERATION_CONNECTION_ESTABLISHED`, `FEDERATION_CONNECTION_LOST`, `FEDERATION_CONNECTION_AUTHENTICATED`, `FEDERATION_PEER_AUTH_FAILED`, `FEDERATION_PEER_DISCONNECTED` |
| `health.py` | `FEDERATION_GATEWAY_DEGRADED`, `FEDERATION_GATEWAY_UNHEALTHY`, `FEDERATION_GATEWAY_RECOVERED`, `FEDERATION_GATEWAY_LIVENESS_STALE`, `FEDERATION_EDGE_CONTAINER_EXITED`, `FEDERATION_LINK_TIMEOUT` |
| `routing_decisions.py` | `FEDERATION_ROUTING_DELEGATED`, `FEDERATION_ROUTING_ROUTED_LOCAL`, `FEDERATION_ROUTING_REJECTED`, `FEDERATION_REQUEST_INFERENCE_STARTED_FORWARDED`, `FEDERATION_VRAM_REQUEST_SENT`, `FEDERATION_VRAM_REQUEST_FAILED`, `FEDERATION_VRAM_RESPONSE_RECEIVED`, `FEDERATION_ACTIVATION_FILTERED_EMPTY`, `FEDERATION_CIRCUIT_BREAKER_REQUEST_REJECTED` |
| `telemetry.py` | `FEDERATION_TELEMETRY_RECEIVED`, `FEDERATION_TELEMETRY_MARKED_STALE`, `FEDERATION_TELEMETRY_APPLIED`, `FEDERATION_TELEMETRY_WIRED`, `FEDERATION_MODEL_LIFECYCLE_EVENT`, `FEDERATION_RESOURCE_UPDATED`, `FEDERATED_GATEWAY_REMOVED` |

Declared invariants worth noting verbatim from docstrings: `FEDERATION_GATEWAY_DEGRADED` is explicitly **not** a routing-exclusion signal ("the gateway remains routable — this is a coordination signal for batch consumers... to throttle or pause"), whereas `FEDERATION_GATEWAY_UNHEALTHY` **does** exclude the gateway from routing for a declared `cooldown_s`, recovered only via a HALF_OPEN probe. `FEDERATION_GATEWAY_RECOVERED` carries a `kind` discriminator (`degradation` | `reachability` | `liveness`) tying it back to whichever of the three failure signals it clears.

— `cortex://notes/system/threads/overhaul-stargate-src/source/review/federation_signaling/__init__.py`, `.../connection.py`, `.../health.py`, `.../routing_decisions.py`, `.../telemetry.py`

### 2.4 `scheduling/events/pipeline/` (package-shadow of former `pipeline.py`)

Facade docstring: "registry, gate, embedding, and domain-verification event signals," split into `signal_constants.py` and `factories.py`. Declared signals: `PIPELINE_REGISTRY_UNAVAILABLE` (pipeline permanently skipped — model deps unresolvable against gateway catalogs + registered pipelines, emitted once per unavailable pipeline after each registry load/reload), `PIPELINE_EXECUTION_TIMED_OUT`, `PIPELINE_DEADLOCK_DETECTED`, `PIPELINE_EXECUTION_CANCELLED`, model-gate lifecycle (`PIPELINE_STEP_MODEL_DEFERRED`, `PIPELINE_MODEL_GATE_CLAIMED`, `PIPELINE_MODEL_GATE_RELEASED`, `PIPELINE_MODEL_GATE_RELEASED_ON_FAILURE`, `PIPELINE_MODEL_REGISTRY_LOOKUP_FAILED`), `PIPELINE_DAG_EXECUTION_COMPLETED` (terminal summary: completed/skipped/failed/total counts), embedding step lifecycle (`PIPELINE_STEP_EMBEDDING_{STARTED,COMPLETED,FAILED}`), and domain-verification step lifecycle (`PIPELINE_STEP_DOMAIN_VERIFICATION_{STARTED,COMPLETED}`, the latter carrying `passed_count`/`failed_count`/`duration_ms`).

Note this `scheduling.events.pipeline` package is distinct from the separately-documented `systems/pipeline` subsystem (multi-model workflow orchestration) — see § 6 Related SOT; the events here are the *signals*, not the orchestrator itself.

— `cortex://notes/system/threads/overhaul-stargate-src/source/review/pipeline/__init__.py`, `.../pipeline/signal_constants.py`, `.../pipeline/factories.py`

### 2.5 `scheduling/events/routing_failures/` (package-shadow of former `routing_failures.py`)

Facade docstring: "resource-data gaps, infeasibility, eviction blocks, upstream exclusion, capacity divergence, overflow, and capacity slot leak recovery." Nine declared signals:

- `ROUTING_RESOURCE_DATA_MISSING` — model in gateway catalog but absent from `model_details`; distinguishes a startup resource gap from genuine `MODEL_NOT_FOUND`.
- `ROUTING_MODEL_INFEASIBLE` — model exists somewhere but every candidate gateway is infeasible; accompanies the `NO_FEASIBLE_GATEWAY` (503, retryable) response.
- `ROUTING_EVICTION_BLOCKED_BUSY` — eviction temporarily blocked because all loaded models on a gateway are busy; payload includes an additive `candidate_breakdown` per-gateway snapshot.
- `ROUTING_EVICTION_INSUFFICIENT_PERMANENT` — permanent hardware-constraint insufficiency, emitted immediately before a `RESOURCE_UNAVAILABLE` response; carries optional admission-verdict fields (`verdict_class`, `needed_mb`, `footprint_est_mb`, `margin_mb`, `attainable_mb`, `reserved_mb`).
- `ROUTING_UPSTREAM_ALL_EXCLUDED` — all gateways for a model excluded by upstream 5xx failures; signals "do not retry on the same gateway."
- `ROUTING_CAPACITY_DIVERGENCE` — telemetry-derived busy/idle state disagrees with local `CapacityPool` slot state; docstring is explicit this is an **observability** signal — "routing correctness still relies on CapacityPool admission."
- `ROUTING_CAPACITY_PRESEEDED` — cold-load request seeds `CapacityPool` with a bounded loading-phase placeholder rather than full post-load capacity, to close a cold-load admission bypass without herd-admission risk.
- `ROUTING_OVERFLOW_TRIGGERED` / `ROUTING_OVERFLOW_FAILED` — non-sticky overflow-routing pass finding (or failing to find) a feasible alternate gateway after primary saturation.
- `CAPACITY_SLOT_LEAK_RECOVERED` — canary signal for a declared `CapacityPool._wait_for_slot` cancellation race (`_dispatch` admits/increments `in_flight` before the waiter's `CapacityToken` is created; the waiter is cancelled in between); docstring states non-zero rate under load is expected, sustained high rate may indicate cancellation/timeout tuning issues.

— `cortex://notes/system/threads/overhaul-stargate-src/source/review/routing_failures/__init__.py`, `.../signal_constants.py`, `.../factories.py`

### 2.6 `scheduling/events/model_lifecycle/` (package-shadow of former `model_lifecycle.py`)

Facade docstring: "loaded/unloaded, loading progress/failure, execution, capacity free, worker eviction, and availability." Declared signals: `MODEL_LOADED`, `MODEL_UNLOADED`, `MODEL_LOADING_STARTED`, `MODEL_LOADING_PROGRESS` (heartbeat; docstring states the node loader MUST emit at most every 15s while loading, requires non-empty `phase` and `pct` in `[0,100]` — the factory enforces both at call time, raising `ValueError` otherwise), `MODEL_LOAD_FAILED` (payload documents two optional best-effort forensic snapshots, `gateway_state_snapshot` and `worker_snapshot`, explicitly declared as "may be absent... subscribers MUST tolerate either being None"), `MODEL_LOADING_STUCK` (stuck-TTL auto-clear), `MODEL_EXECUTION_STARTED/COMPLETED/FAILED` (per-request lifecycle; `COMPLETED`/`FAILED` declare `request_id` and `gateway_id` as **required** for slot tracking — consumed by `GatewayTracker`'s auto-release subscription per docstring), `MODEL_CAPACITY_FREED` (explicitly "wake-only... NOT a slot-release signal"), `WORKER_EVICTED` (coordination signal so downstream batch consumers anticipate a cold-load window post-eviction), `MODEL_AVAILABLE`/`MODEL_UNAVAILABLE` (aggregate Stargate-scope routing availability — "not equivalent to model.loaded on a specific gateway").

— `cortex://notes/system/threads/overhaul-stargate-src/source/review/model_lifecycle/__init__.py`, `.../signal_constants.py`, `.../factories.py`

## 3. `scheduling` — consumers and gateway state

### 3.1 Consumers (`scheduling/consumers/`)

The package `__init__.py` re-exports eight consumers with no logic of its own (same pure-facade shape as the events barrel): `MetricsConsumer`, `ModelCacheConsumer`, `ModelLoadingConsumer`, `MonitoringConsumer`, `ResourceUpdateConsumer`, `RoutingConsumer`, `RoutingDecisionConsumer`, `RoutingMetricsConsumer` — role assignment per the parent `scheduling/__init__.py` docstring: performance/reliability stats, model-availability cache sync, model load lifecycle updates, uptime/downtime tracking + status push, resource/capacity state updates, gateway availability for routing decisions, routing-decision stream processing, and routing performance-metrics aggregation, respectively. — `cortex://notes/system/threads/overhaul-stargate-src/source/doc-excerpts/scheduling/consumers/__init__.py`, `cortex://notes/system/threads/overhaul-stargate-src/source/doc-excerpts/scheduling/__init__.py`

Individual consumer module docstrings (`metrics_consumer.py`, `model_cache_consumer.py`, etc.) were **not staged** in this pass's excerpt corpus — `missing_coverage`.

### 3.2 Gateway state and errors (`scheduling/gateway_state.py`, `scheduling/gateway_errors.py`)

`GatewayState` composes two independently-declared orthogonal axes: `ConnectivityState` (`REACHABLE`/`UNREACHABLE` — network-level reachability) and `HealthState` (`HEALTHY`/`UNHEALTHY`/`UNKNOWN` — application-level service status, `UNKNOWN` used "e.g., unreachable"). `is_available()` requires both `REACHABLE` and `HEALTHY`. `has_changed()`/`get_transition_description()` are declared as logging-support helpers for detecting/describing state transitions. — `cortex://notes/system/threads/overhaul-stargate-src/source/doc-excerpts/scheduling/gateway_state.py`

`gateway_errors.py` declares a typed exception hierarchy rooted at `GatewayError` (message + optional `gateway_url` + structured `context`, with a `to_dict()` serializer keyed by `error_type`/`message`/`gateway_url`/`context`), specialized as `ConnectivityError` (network-unreachable, wraps an optional underlying exception), `HealthError` (reachable but degraded/unhealthy, carries `health_status`/`health_data`), `GatewayTimeoutError` (`timeout_seconds` + `operation`), `ModelLoadError`/`ModelUnloadError` (per-model failure detail), and `NoHealthyGatewaysError` (`total_gateways` + `gateway_states` snapshot). — `cortex://notes/system/threads/overhaul-stargate-src/source/doc-excerpts/scheduling/gateway_errors.py`

`scheduling/event_utils.py` and `scheduling/gateway_logging.py` are named as re-exports/collaborators in the parent `scheduling/__init__.py` docstring (`StateTransitionDebugger`, `EventRateLimiter`, `format_state_transition_for_logging`, `validate_state_change_payload`, `GatewayLogger`) but were **not staged** as excerpts this pass — `missing_coverage`.

Sibling modules **not** named in `scheduling/__init__.py` (known via `doc-inventory.md` only):
- `scheduling/debuggable_event_bus.py` — **DEPRECATED** per its own module docstring ("Debug broadcasting is now integrated directly into the main EventBus"); not a current architecture surface.
- `scheduling/event_to_udp_bridge.py` — bridges event-driven scheduling to the GUI transport layer (Unix socket by default); see also §4.3 Transport.

### 3.2b Federation liveness alert bridge (`scheduling/events/consumers/`)

Package docstring: "Event-bus consumers for federation observation signals." `LivenessAlertBridge` subscribes to `FEDERATION_GATEWAY_LIVENESS_STALE` / `FEDERATION_GATEWAY_RECOVERED` (the same health signals described in §2.3) and posts operator-visible alert/recovery briefings to a dedicated agent-bus thread over HTTP (skips posting when no agent-bus token is configured). — `cortex://notes/system/threads/overhaul-stargate-src/source/doc-inventory.md` · live `services/universal-stargate/src/scheduling/events/consumers/liveness_alert_bridge.py` (step-11 apply)

### 3.3 Top-level `scheduling/events/*.py` domain modules outside the five package-shadowed ones

`cloud.py`, `cursor_catalog.py`, `federation_load.py`, `gateway.py`, `proxy.py`, `queue.py`, `routing.py`, `routing_debug.py`, `snapshot.py`, `system.py`, and the `routing_factories_*.py` group are named only via the barrel's domain-list docstring and `__all__` re-exports (§2.1) and via `doc-inventory.md` heading/line-count entries — their own module docstrings were not staged as excerpts this pass. Declared signal names visible via the barrel include gateway state-transition and resource-reservation signals (`GATEWAY_STATE_CHANGED`, `GATEWAY_RETRY_ATTEMPTED`, `GATEWAY_RESOURCE_UPDATE`, `RESOURCE_RESERVED`, `RESOURCE_RELEASED`), federation-load/catalog signals (`FEDERATION_GATEWAY_CATALOG_CHANGED`, `FEDERATION_CATALOG_VRAM_DRIFT`, `FEDERATION_LOAD_{REQUESTED,CONFIRMED,FAILED}`, `FEDERATION_ORCHESTRATOR_{DECIDED,EVICTED}`), cloud-proxy availability/catalog signals, master-queue signals (`QUEUE_MASTER_{ENTERED,WOKEN,TIMEOUT,TOCTOU}`), and capacity-pool admission signals (`CAPACITY_POOL_{QUEUED,WAITING,ADMITTED,FULL,CANCELLED}`, `CAPACITY_ADMISSION_{PAUSED,RESUMED}`) — content beyond the names/constants is `missing_coverage`.

## 4. `core/` — gateway tracking, transport, streaming, monitoring

### 4.1 Gateway tracking (`core/gateway/`, `core/gateway_tracker.py`)

`core/gateway/status_registry.py` declares `GatewayStatusRegistry`: single responsibility "record each gateway's availability state (available, draining, or shutdown) as timestamped dataclass entries." Explicit single-writer assumption ("all mutations happen on one async event loop. No thread synchronization provided"). Declared methods: `register_gateway`, `mark_draining`, `mark_shutdown`, `get_available_gateways` (excludes draining), `is_available`.

`core/gateway/__init__.py` additionally re-exports `InFlightRequestTracker`/`in_flight_tracker` from `in_flight_requests.py` (378 lines per modularize scan) as "request/model tracking for eviction protection" — the module's own docstring/signatures were **not staged** this pass; its public surface is known only via this re-export and via `gateway_tracker.py`'s facade docstring — `missing_coverage` for its own declared contract.

`core/gateway_tracker.py` declares `GatewayTracker` as a **facade** unifying `GatewayStatusRegistry` and `InFlightRequestTracker`, explicitly noting admission control (`CapacityPool`) lives elsewhere (`systems/routing/capacity/` — see § 6 Related SOT), and that in-flight tracking here is "NOT authoritative for admission control... records counts for observability and soft assertions only." Declared responsibilities: eviction protection (routing_keys with in-flight requests are shielded), observability (per-gateway request counts and capacity-key counters), and lifecycle (registration, draining, shutdown, stale-request cleanup via an optional background `asyncio` task, `start_background_cleanup`/`stop_background_cleanup`). `mark_shutdown()` returns the set of in-flight request IDs needing retry/reroute; `complete_request()` is declared idempotent.

— `cortex://notes/system/threads/overhaul-stargate-src/source/doc-excerpts/core/gateway/__init__.py`, `.../core/gateway/status_registry.py`, `.../core/gateway_tracker.py`

### 4.2 Shutdown handling (`core/shutdown_handler.py`)

`GatewayShutdownHandler` subscribes (per module docstring) to `GATEWAY_SHUTDOWN` and `GATEWAY_STATE_CHANGED` (disconnect detection via `connectivity == "unreachable"` transitions) and clears in-flight tracking on either, "preventing stale slot reservations from blocking new requests" / "503 errors from phantom capacity." Declares separate handling for `GATEWAY_DRAINING` (marks unavailable for *new* requests only — does **not** trigger retry of in-flight requests, unlike the shutdown/disconnect paths which do trigger an optional `retry_callback` per affected request). Tracks `_shutdown_count`/`_disconnect_count` counters exposed via getters. — `cortex://notes/system/threads/overhaul-stargate-src/source/doc-excerpts/core/shutdown_handler.py`

### 4.3 Transport (`core/transport/`)

Package docstring: "Event transport layer for Universal Stargate monitoring. Provides pluggable transports (Unix socket, UDP, TCP) for broadcasting monitoring events from the proxy to GUI and remote clients." Re-exports `EventTransport` (abstract base), `UnixStreamTransport`, `UDPDatagramTransport`, `TCPStreamTransport`, `TransportServer`.

`base.py` declares `EventTransport(ABC)` with abstract `start()`/`stop()` (both declared idempotent) and `send_event(event_data: dict) -> bool`, whose docstring is explicit that implementations "should NOT raise exceptions — catch and log errors, then return False. This ensures one transport failure doesn't affect others." Also declares `is_started`/`transport_name` properties. Explicitly declared thread-unsafe-by-design: "Thread Safety: Not needed. All transports operate in single async context."

`server.py`, `tcp_stream.py`, `udp_datagram.py`, `unix_stream.py` concrete implementations were **not staged** as excerpts this pass beyond their re-export names in `transport/__init__.py` — `missing_coverage` for their individual declared contracts.

— `cortex://notes/system/threads/overhaul-stargate-src/source/doc-excerpts/core/transport/__init__.py`, `.../core/transport/base.py`

Cross-ref: `scheduling/event_to_udp_bridge.py` (§3.2) is the GUI-facing bridge onto this transport layer.

### 4.4 Streaming (`core/streaming/`)

`ndjson_framing.py` declares `iter_ndjson_lines_bytes(response: httpx.Response) -> AsyncIterator[bytes]`: converts arbitrary byte chunks from an httpx streaming response into complete NDJSON lines "without intermediate UTF-8 decode/encode," buffering partial lines across chunk boundaries, splitting on `b"\n"`, skipping blank/whitespace-only lines, and flushing any residual buffer at stream end. Docstring states an explicit framing invariant: "∀ yielded: exactly one JSON object terminated by `b"\n"`."

`stream_flag.py` declares `client_requested_stream(body: Mapping) -> bool`, a strict `is True` identity check against `body["stream"]` — explicitly **not** a truthy check, so that `"stream": 1` or `"stream": "false"` cannot accidentally enable streaming. Docstring frames this as "the strict guard used at every independent request-body parse point that cannot reach the single proxy-ingress coercion boundary (federation relay/direct paths, native-provider passthrough)," implying (per docstring only) a separate, non-staged coercion boundary exists elsewhere in the proxy-ingress path — `missing_coverage` for that boundary's own module.

`core/streaming/__init__.py` package docstring (lead-resolved step 10): streaming helpers for NDJSON framing and stream-flag control; core primitives live in this package. A prior double string-literal artifact after `# ruff: noqa: N999` was removed in-checkout during step 10 (second literal was a no-op expression, not `__doc__`).

— `workspaces://universal-llm-gateway/services/universal-stargate/src/core/streaming/__init__.py` (post step-10 fix); staged excerpt may lag until re-stage · `.../core/streaming/ndjson_framing.py`, `.../core/streaming/stream_flag.py`

### 4.5 Monitoring (`core/monitoring/`)

Package docstring: "Async monitoring components for non-blocking event logging," re-exporting `AsyncChunkLogger`/`create_async_chunk_logger`. The implementation module (`async_chunk_logger.py`, 268 lines per modularize scan) was **not staged** as an excerpt this pass — `missing_coverage` for its declared contract beyond the two re-exported names. — `cortex://notes/system/threads/overhaul-stargate-src/source/doc-excerpts/core/monitoring/__init__.py`

## 5. `scheduling/schemas/` and top-level `schemas/`

`scheduling/schemas/__init__.py` docstring: "Data schemas for the scheduling system. Updated for new API response formats," re-exporting `QueuedRequest` (from `requests.py`) and `RequestStatus` (from `resources.py`) — neither submodule was staged as an excerpt this pass; their own field-level declarations are `missing_coverage`. — `cortex://notes/system/threads/overhaul-stargate-src/source/doc-excerpts/scheduling/schemas/__init__.py`

Top-level `schemas/` declares the pydantic request/response validation surface for the HTTP API, distinct from the scheduling-internal schemas above:

- `chat_completion.py` — OpenAI-compatible `ChatMessage`, `ChatCompletionRequest` (docstring: `model_post_init` raises `ValueError` unless exactly one of `messages`/`prompt` is set), `ChatCompletionChoice`, `ChatCompletionUsage`, `ChatCompletionResponse`, and streaming-chunk variants `ChatCompletionStreamDelta`/`StreamChoice`/`StreamResponse`. Module docstring flags a project-wide `model_dump()` convention: always `exclude_unset=True`, always `mode="python"` (not `"json"`), pointing at `docs/pydantic-passthrough-rules.md` (not part of this staged corpus).
- `model_info.py` — `ModelInfo` (deliberately over-broad optional-field schema serving both basic and detailed responses from one model) and `ModelListResponse` (pagination/filter/sort metadata all optional).
- `responses.py` — shared cross-endpoint schemas: `HealthResponse`, `MetricsResponse`, `ErrorResponse`, `ValidationErrorResponse`, `SuccessResponse`.
- `tokens.py` — multi-modal-aware `Message`/`ContentPart` (discriminated union of `ContentPartText`/`ContentPartImage`), `TokenCountRequest` (validator requires exactly one of `messages`/`prompt`), `TokenCountResponse`, `TokenCountError`, and `TokenMetrics` (distinct from per-request `TokenCountResponse` — declared for service-level token-throughput monitoring). Same `model_dump()` convention note as `chat_completion.py`.

— `cortex://notes/system/threads/overhaul-stargate-src/source/doc-excerpts/schemas/__init__.py`, `.../schemas/chat_completion.py`, `.../schemas/model_info.py`, `.../schemas/responses.py`, `.../schemas/tokens.py`

## 6. Related SOT (cross-links — not restated here)

This document does not duplicate the following related architecture SOT; consult directly for their domains and treat this document's package-shadow claims (§2) as scoped strictly to `services/universal-stargate/src`:

- **Stargate live state & eviction model** — runtime model/node placement is API-derived, not filesystem-catalog-derived; VRAM is not a hard placement ceiling (eviction of idle models is automatic). `cortex://notes/system/threads/overhaul-stargate-src/source/docs/architecture/stargate-live-state.md`
- **Gateway core** (`WorkerController`, `ModelRegistry`, `core/events/types` package-shadow, `VramReconciler`) — a *different* `core/` than the one in this document (that SOT's `core.events.types` is a separate gateway-side event-type package-shadow, not `src.scheduling.events` covered in §2 here). `cortex://notes/system/threads/overhaul-stargate-src/source/docs/architecture/related-gateway.md`
- **Routing system** (`systems/routing`) — `ModelRouter`, T0/T1/T2 feasibility tiers, `CapacityPool` (referenced directly from this document's §4.1 `GatewayTracker` docstring as the actual admission-control authority), typed eviction outcomes (`EvictionOutcome`, fail-closed `UNCONFIRMED_NO_BUS`). `cortex://notes/system/threads/overhaul-stargate-src/source/docs/architecture/related-routing.md`
- **Pipeline system** (`systems/pipeline`) — the orchestrator (`PipelineExecutor`/`PipelineRegistry`, DAG execution, step handlers) that the `scheduling.events.pipeline` signals in §2.4 report *on*; the events package documented here is not the orchestrator itself. `cortex://notes/system/threads/overhaul-stargate-src/source/docs/architecture/related-pipeline.md`

## 7. Non-goals / explicitly out of scope for this document

- Behavioral/runtime attestation of any declared contract above (all claims are doc↔declaration, not doc↔behavior).
- Inventing APIs, signals, or modules not present in the staged corpus.
- Rewriting or promoting into the live repository checkout, or into `docs/architecture/universal-stargate-src.md` directly — this is a staged CDP draft (materialized as `.md.generated`, arc `agent-bus:5478` step 9) for downstream review/promotion, not a repo write.
- Stargate's own `doc-generate` tool, signal renames, or re-planning the package-shadow split itself.

<!-- GENERATED:END -->

## Coverage sidecar

**unsupported_claims:** none — every substantive claim above is grounded in a staged excerpt/inventory/review file and cited inline; no runtime behavior is asserted.

**missing_coverage:**
- `core/gateway/in_flight_requests.py` (own docstring/signatures — known only via re-export + facade mentions)
- `core/monitoring/async_chunk_logger.py` (own contract — known only via package re-export)
- `core/transport/{server,tcp_stream,udp_datagram,unix_stream}.py` (concrete transport implementations)
- `scheduling/consumers/{metrics,model_cache,model_loading,monitoring,resource,routing,routing_decision,routing_metrics}_consumer.py` (individual consumer contracts)
- `scheduling/{event_utils,gateway_logging,event_to_udp_bridge}.py` (excerpts not staged; `debuggable_event_bus.py` is DEPRECATED — see §3.2)
- `scheduling/events/{cloud,cursor_catalog,federation_load,gateway,proxy,queue,routing,routing_debug,snapshot,system,routing_factories_*}.py` (top-level event domains outside the five package-shadowed ones)
- `scheduling/schemas/{requests,resources}.py` (field-level schema declarations)
- `docs/pydantic-passthrough-rules.md` (referenced by two schema modules; not in this corpus)
- Service composition / process-entry wiring of `core` + `scheduling` + `schemas` (no service-entry module staged)

**human_markers:** 1 — `core/__init__.py` service-wiring gap (§1). (`core/streaming/__init__.py` double-string resolved in step 10.)

**review_notes:** Package-shadow split (§2) is corroborated by two independent staged sources agreeing exactly (barrel content identical in `doc-excerpts/` and `review/`; probe confirms 0 signal drift, 243/243 `__all__` resolution) and by `git-status.txt` showing the expected delete-old/add-new working-tree shape for an in-flight, uncommitted split. No contradictions found between the two corpus locations for the five emphasized subpackages. Step-11 CDP review (`45a3ab52`, PASS_WITH_WARNINGS): applied Critical (describe `scheduling/events/consumers/` LivenessAlertBridge) + Warnings (§3.2 attribution + DEPRECATED flag) + Suggestion (transport↔bridge cross-ref).
