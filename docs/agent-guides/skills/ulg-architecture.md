# ULG Architecture — universal-llm-gateway Layer

Version: 2.0 rewrite  
Created: 2026-05-13  
Authority: This skill governs universal-llm-gateway repo work and composes with `cortex://agent-skills/architecture-invariants.md` for cross-workspace constraints: transport, model IDs, events, MCP relay, REST, quality gates, and related invariants.

## When to read

Read this before any universal-llm-gateway repo work that touches service operations, route changes, rebuild commands, or model-lifecycle decisions.

Read this for any work in `workspaces://universal-llm-gateway/...`. Pair it with `cortex://agent-skills/architecture-invariants.md` before changing transport, model IDs, events, MCP relay, REST surfaces, quality gates, or shared architectural behavior.

## Core rule

Preserve the ULG topology and authority boundaries.

Formal rules:

- `∀ client_request: client_request enters ULG through :9999`.
- `∀ external_client: external_client MUST NOT target :9998`.
- `∀ service_operation: service_operation MUST use manage MCP tool OR ./manage TUI`.
- `∀ model_lifecycle_decision: authority(model_lifecycle_decision) = Stargate`.
- `∀ issue_investigation: query(Event Service) precedes application_logs`.
- `∀ generated_metadata_artifact under ~/.rag/store/rag_metadata.db: direct_edit(artifact) = forbidden`.
- `∀ ecosystem_component: use universal_logging AND do_not_use stdlib logging directly`.
- `∀ client_params: Gateway and Workers pass client_params through unmodified except allowed removals`.

## Procedures and invariants

### Topology

Use `:9999` Stargate as the sole client-facing endpoint. Treat `:9998` Gateway as container-internal only.

Full hop chain:

```text
client → :9999 (host Stargate) → UDS → Edge Stargate (container)
      → localhost:9998 → Gateway (container)
```

Write topology descriptions as:

- `Stargate runs on port 9999 — the only client-facing endpoint`.
- `Gateway runs inside the edge container; accessed by the edge Stargate on container-local port 9998`.

Do not write `Gateway on 9998` without container-internal context.

Formal rules:

- `∀ topology_description: mentions(:9998) -> states(container_internal_only)`.
- `∀ agent_memory_seed: mentions(Gateway, 9998) -> states(unreachable_from_outside_container)`.
- `∀ instruction_to_agent: mentions(Stargate) -> states(:9999 is sole client-facing endpoint)`.

Use the full topology reference for roles, diagram, federation, SSH, and ports: `workspaces://universal-llm-gateway/.cursor/rules/topology_ws.mdc`.

### Service lifecycle

Perform all service operations through `manage` MCP tool for agents or `./manage` TUI for humans.

Do not use:

- `pkill -f "universal-"`
- `docker restart` / `docker stop`
- `systemctl`
- direct script starts

Agent post-code-change loop:

1. `quality_gate(files=[...])`
2. `manage(action="sync_restart", service=X)`
3. `manage(action="wait_healthy", service=X, timeout=120)`

Formal rules:

- `∀ deploy: code_changed(deploy) -> quality_gate(files=[...]) before sync_restart`.
- `∀ deploy: sync_restart(deploy) -> manage(action="wait_healthy", service=X, timeout=120) after sync_restart`.
- `∀ service_operation: forbidden(tool) iff tool ∈ {pkill, docker restart, docker stop, systemctl, direct_script_start}`.

Per-service `sync_restart` strategy:

| Service | `sync_restart` strategy |
|---|---|
| `gateway` | Just restart — `libs/`, `services/`, `config/` are bind-mounted from host |
| `mcp` | Cached `--refresh-source` rebuild + restart (~20s; source baked into image) |
| `stargate`, `rag`, `cloud_proxy`, `cortex_api`, `agent_bus`, `event_service` | Restart |

Do not call `manage(action="rebuild", service=X)` for `gateway` or `mcp` as an agent. Full `--no-cache --pull` rebuild is heavy; `gateway` recompiles vLLM CUDA from source and takes 60-90 minutes. Route engine, dependency, and Dockerfile changes through `./manage` → Services → Build Image. Treat host-process rebuilds for `event_service`, `cortex_api`, `agent_bus`, and `email_bridge` as restart-equivalent paths.

Formal rules:

- `∀ X: X ∈ {gateway, mcp} -> agent_forbidden(manage(action="rebuild", service=X))`.
- `∀ X: X ∈ {event_service, cortex_api, agent_bus, email_bridge} -> rebuild(X) = restart_equivalent(X)`.
- `manage.sock missing -> ask_user_to_start("./manage")`. Canonical UDS:
  `transport_utils.MANAGE_SOCKET` (default `/tmp/universal-protocol/manage.sock`;
  also `~/.gateway/topology.yaml` → `manage_socket`). Agents: MCP
  `manage(action="status")` — not `test -S` on repo root or `~/.gateway/manage.sock`.

### MCP rebuild verification

Treat `wait_healthy` as necessary but insufficient for MCP rebuild verification. If the Docker build step fails, the controller restarts the existing image and `wait_healthy` still returns healthy. Always verify the image timestamp.

Step 1 — confirm the new image was built:

```bash
docker images universal-mcp-server --format "Created: {{.CreatedAt}}"
```

Expected result: timestamp ≤ 5 minutes ago. If the timestamp is older, the build failed silently and the old image is running.

Step 2 — confirm the container is running the new image:

```bash
docker inspect mcp-server --format '{{.State.StartedAt}}'
```

The container start time must be after rebuild initiation.

Step 3 — confirm server startup through events:

```text
observability(operation="raw_sql", params={"sql": "SELECT ts, signal FROM events WHERE signal='mcp.oauth.server.started' ORDER BY ts DESC LIMIT 1"})
```

The `ts` value must be after rebuild initiation. `mcp.oauth.server.started` fires on every successful server init.

MCP lifecycle rules:

- `services/mcp-server/` source edits require `manage(action="sync_restart", service="mcp")`.
- New dependency in `requirements.txt` requires TUI `./manage` → Services → Build Image, or `scripts/sync-and-restart-mcp.sh --no-cache`.
- Base image or compiled library changes require the same no-cache TUI path.
- Plain `restart` does not pick up edits to `services/mcp-server/` because MCP source is baked into the image and `docker/compose/mcp-server.yml` contains no source bind-mounts.
- Gateway differs from MCP because `docker/compose/gpu-edge.yml` bind-mounts `libs/` and `services/` for gateway. Use the uniform `sync_restart` verb; let strategy vary per service.

Formal rule: `∀ mcp_source_edit: path_under("services/mcp-server/") -> manage(action="sync_restart", service="mcp") AND verify(image_timestamp)`.

### Cortex data safety

`~/.cortex/cortex.db` is the live Cortex graph. Treat it as production data.

Manual writes via the `sqlite3` CLI against `~/.cortex/cortex.db` are forbidden by
default. If unavoidable: open the session with `PRAGMA foreign_keys=ON`, perform only
the minimum change, and verify `PRAGMA foreign_key_check` returns zero rows before
`COMMIT`.

`foreign_keys=ON` is enforced per connection, not globally. Runtime code that opens
`~/.cortex/cortex.db` must use `cortex_store.db.cortex_conn()` or `_connect()` —
never a bare `sqlite3.connect()` without enabling foreign keys. Standalone scripts
that touch the cortex DB must import and use that factory.

Formal rules:

- `∀ manual_sqlite3_edit(cortex.db): forbidden by default`.
- `∀ unavoidable_manual_edit: foreign_keys=ON before writes AND foreign_key_check=0 before commit`.
- `∀ standalone_script touching cortex.db: connection = cortex_store.db.cortex_conn() OR _connect(path)`.

### Sandbox routing

Route file operations by sandbox.

| Sandbox | Root | Example path |
|---|---|---|
| `cortex` | MCP data dir | `notes/system/transcripts/cursor-2026-04-08-0127.md` |
| `context` | `tasks/` | `specs/some-spec.md` |
| `workspaces` | `/mnt/torus/projects/` | `universal-llm-gateway/docs/tool-reference.md` |

Formal rules:

- `∀ workspaces_path: path MUST include repo_name_prefix`.
- `∀ context_path: path is relative_to("tasks/")`.

### API namespaces

Use `/v1/*` only for standard OpenAI-compatible endpoints and response shapes. Put Stargate administrative, monitoring, topology, RAG, debug, and project-specific endpoints under `/api/v1/*` or another explicit nonstandard namespace. Put browser UIs, health checks, and non-API surfaces under explicit namespaces such as `/cloud-ui`, `/local-ui`, and `/health`.

Formal rules:

- `∀ Stargate_endpoint: endpoint ∈ /v1/* iff endpoint is standard OpenAI-compatible surface`.
- `∀ custom_endpoint: custom_endpoint MUST NOT live under /v1/*`.
- `∀ project_specific_endpoint: namespace(project_specific_endpoint) ∈ {/api/v1/*, explicit_nonstandard_namespace}`.

| Bad | Good |
|---|---|
| `GET /v1/local-models` | `GET /api/v1/local-models` |
| `GET /v1/gateway-states` | `GET /api/v1/gateway-states` |

### Generated metadata write authority

Do not directly edit generated metadata artifacts under `~/.rag/store/rag_metadata.db`.

| Artifact | Source of truth | Refresh |
|---|---|---|
| `scope_vocabulary` table | `scripts/rag/classify_vocabulary.py` | Post-index refresh |
| `corpus_hints` table | `services/rag/corpus_hints.py` | Post-index refresh |

Manual edits are wrong because the next refresh overwrites them silently, creates invisible drift between corpus state and vocabulary, and masks upstream problems in classifier quality or corpus coverage.

For vocabulary quality issues, do one of these in order:

1. Prefer prompt or handler-level fixes: adjust prompts, handler logic, or anchor selection code.
2. Improve `scripts/rag/classify_vocabulary.py`.
3. Fix the corpus by indexing better source material so IDF scoring shifts naturally.
4. Use an explicit override only with user approval; document the edit and mark it for re-evaluation after next refresh.

Formal rule: `∀ vocabulary_quality_issue: direct_db_edit = forbidden unless user_approved_explicit_override AND documented_stopgap`.

### Stargate model lifecycle authority

Treat Stargate as the sole authority for model loading and unloading. No service outside Stargate decides when a model loads, which gateway it loads on, or whether it stays resident.

External services must be correct without observing Stargate load state. A subscriber that receives no lifecycle signals must produce the same correct result as one that sees the full stream; only throughput or queue pressure may differ.

External services have exactly one structural question about a model: `Does this model exist in the catalog?`

- If yes, Stargate can serve it and loading happens transparently.
- If no, treat it as structural failure. Fail fast. Do not retry.

Observe catalog presence through `GET /v1/models/{id}` using the `available` field, or through aggregate `model.available` / `model.unavailable` events.

Batch coordinators for RAG indexing/contextualization, fine-tuning data prep, and bulk evaluation may subscribe to lifecycle signals to throttle, defer, or back off when transparent load-on-demand would degrade throughput. They must not take authority over Stargate decisions.

Official coordination surface: `role="coordination"` events on the Event Service WebSocket.

| Signal | Meaning | Recommended reaction |
|---|---|---|
| `model.loading.started` | Cold-load window opened on a gateway | Pause new submissions for this model_id |
| `model.loaded` | Cold-load completed; model resident | Resume submissions |
| `model.load.failed` | Cold-load failed | Restore optimism — next submission triggers a retry that fails loudly |
| `model.unloaded` | Model removed (eviction or shutdown) | Pause new submissions; await `model.loaded` |
| `worker.evicted` | Stargate evicted to free VRAM for `trigger_model_id` | Same as `model.unloaded`; carries `trigger_model_id` for observability |
| `model.capacity.freed` | Wake-only hint that capacity may have grown | Re-check queue depth; do not release tracked slots |

Formal rules:

- `∀ lifecycle_signal: subscriber_treats(lifecycle_signal) = advisory`.
- `missed_signal -> no_state_corruption`.
- `late_signal -> no_forward_progress_block`.
- `∀ wait_on_lifecycle_signal: wait MUST have timeout_cap`.
- `ignore_entire_stream -> correctness_same AND throughput_may_differ`.
- `model_absent_from_catalog -> structural_failure AND fail_fast AND do_not_retry`.
- `model_present_in_catalog -> submit_request AND Stargate_loads_on_demand`.

Keep internal state internal. Do not reference `LOADING`, `BUSY`, `ENGINE_DEAD`, the load reservation state machine, sticky placement bindings, or eviction plans in external service code, enums, public API responses, or events emitted by external services.

### Event Service primary

Start every issue investigation with the Event Service:

```bash
scripts/query-events --op recent-failures --limit 20      # START HERE
scripts/query-events --op noise-profile --minutes 5       # signal frequency
scripts/query-events --op pipeline-trace --execution-id ID # pipeline debugging
scripts/query-events --op request-trace --request-id ID   # request debugging
```

Only then fall back to application logs.

For named operations that return time-bounded results, default the window to since the most recent `system.started` signal, the Stargate session boundary. Event Service operations own this default in `services/event-service/operations.py`. MCP `observability` and CLI `scripts/query-events` pass through unchanged. Callers override with `since_ts` in Unix milliseconds or `minutes` params. Exempt ID-scoped lookups: `request-trace`, `pipeline-trace`, `compare-runs`, and `request-lifecycle`.

Agents with MCP access use `observability` instead of CLI:

```text
observability(operation="recent-failures", params={"limit": 20})
observability(operation="pipeline-trace", params={"execution_id": "ID"})
observability(operation="raw_sql", params={"sql": "SELECT ..."})
pipeline_consult(execution_id="ID", step_name="step", problem="description")
```

Events carry `scope` as `global` or `node`. `scope: node` signals exist only at the originating node and are not re-emitted on master.

Use the full event debugging reference for paths, query cookbook, multi-node, RAG, cloud proxy, and pipeline debugging: `workspaces://universal-llm-gateway/.cursor/rules/event-debugging_ws.mdc`.

Formal rule: `∀ issue_investigation: Event_Service_query before application_log_read`.

### Logging

Use `universal_logging`; do not use stdlib `logging` directly.

```python
# Correct
from universal_logging import get_logger
logger = get_logger(__name__)

# Forbidden
import logging
logger = logging.getLogger("cortex-api.foo")
```

Import level constants from `universal_logging`: `from universal_logging import ERROR, WARNING, INFO, DEBUG`.

Formal rule: `∀ ecosystem_component: logger_source = universal_logging`.

### Async verification

Check non-blocking behavior first.

| Check | Requirement |
|---|---|
| Request path | Do not `await gather()` on handlers |
| Event publish | `await publish_nowait()` for async paths or `publish_from_sync()` for sync paths |
| I/O | Use async I/O or `run_in_executor()` |
| CPU | Use `run_in_executor()` |

Event bus rules:

- Use `await event_bus.publish_nowait(event)` in async request paths for fire-and-forget publication.
- Use `await event_bus.publish(event)` in async background/init code when waiting for subscribers is intended.
- Use `event_bus.publish_from_sync(event)` in sync code to schedule onto the running loop.
- Do not call `event_bus.publish_nowait(event)` bare in sync code; it is a silent-drop trap.
- Do not use `event_bus.publish_async()` or `publish_async_nowait()`; those names were renamed.
- Do not use `event_bus.unsubscribe()`; it was removed.

Use async replacements in async paths:

| Sync forbidden | Async required |
|---|---|
| `open()` | `aiofiles.open()` |
| `requests` | `httpx.AsyncClient` |
| `subprocess.run()` | `asyncio.create_subprocess_exec()` |

Formal rules:

- `∀ request_path: await_gather_on_handlers = forbidden`.
- `∀ sync_code: bare(event_bus.publish_nowait(event)) = forbidden`.
- `∀ state_key: exists_exactly_one(update_path(state_key))`.
- `∀ state_key: state_driven_by_events AND NOT scattered_updates AND NOT polling`.

### Pure passthrough

Pass client generation parameters through Gateway and Workers unmodified. Only Stargate may modify or inject generation parameters. Gateway is a pure API-layer passthrough. Workers are pure RPC-to-engine passthrough.

Allowed removals: `worker_id`, `correlation_id`, `_request_id`, `timeout_hint`.

Formal rules:

- `∀ client_param: Gateway(client_param) = client_param unless client_param ∈ {worker_id, correlation_id, _request_id, timeout_hint}`.
- `∀ client_param: Worker(client_param) = client_param unless client_param ∈ {worker_id, correlation_id, _request_id, timeout_hint}`.
- `∀ generation_param_modification: modifier = Stargate`.

### Concurrency

Prefer coordination mechanisms in this order: atomic ops, events, routing, sequential execution, queues, locks. Use events as the preferred coordination mechanism between concurrent subsystems.

Formal rules:

- `∀ check_then_act: no await between check and act`.
- `∀ model_id: exists_exactly_one(load_operation(model_id)) OR loaded(model_id)`.
- `∀ per_request_component: stateless(per_request_component) AND state_is_parameter_injected`.

The model load uniqueness rule is enforced by the Sequential base class.

### Cortex tool awareness

Read the tool descriptor before the first `CallMcpTool` call to any tool or sub-operation you have not used this session.

Descriptor path pattern in Cursor IDE:

```text
/home/io/.cursor/projects/mnt-torus-projects-universal-llm-gateway/mcps/user-vortex/tools/<tool>.json
```

Formal rules:

- `∀ CallMcpTool(tool, sub_operation): first_use_this_session(tool, sub_operation) -> read_descriptor_before_call`.
- `guess_op_names = forbidden`.
- `infer_required_params_from_context = forbidden`.
- `retry_after_400_instead_of_reading_descriptor = forbidden`.

Prefer `fs` markdown ops for large markdown files:

| Op | Purpose | Extra args |
|---|---|---|
| `md_list` | Heading tree (TOC) | `path` |
| `md_read` | Read one section by heading | `path`, `section` |
| `md_replace` | Replace section body | `path`, `section`, `content` |
| `md_append` | Append to section body | `path`, `section`, `content` |
| `md_delete` | Delete section + body | `path`, `section` |
| `list` | Directory listing | `path` |
| `read` | Full file read | `path` |
| `write` | Write/overwrite file | `path`, `content` |

Use `section="Parent/Child"` for nested section paths when the child heading is one level deeper, such as `###` under `##`. The path is built from heading levels, not document position. Writing a `##` heading inside an `md_replace` body creates a sibling section at the same level, not a child.

Treat `Large ... payload flagged / Stored as rs_XXXXX` as a caching notice, not an error. Large responses around 128KB are stored for deferred retrieval; the underlying write or call succeeded. Do not retry.

Verify durable artifacts directly after flagged-payload write responses:

| Operation | Verify with |
|---|---|
| `cortex(tool="session_close", ...)` | `cortex(tool="entity_get", arguments='{"entity_id": "transcript:YYYY-MM-DD-HHmm"}')` |
| `cortex(tool="assert", ...)` | `cortex(tool="entity_get", ...)` — assertion appears |
| `fs(op="write", ...)` | `fs(op="read", ...)` same path |
| `agent_bus(tool="reply", ...)` | Response contains `turn_number` field |

Formal rules:

- `flagged_payload_response -> underlying_operation_succeeded`.
- `flagged_payload_response -> verify_durable_artifact_directly`.
- `flagged_payload_response -> do_not_retry_write`.
- `retry_succeeded_write -> duplicate_entities OR duplicate_assertions OR duplicate_thread_turns`.

## Anti-patterns

Do not wait for `model.loaded` before sending the first request; send the request and let Stargate load on demand.

Do not refuse to submit because `model.available` is false as though a catalog gap were transient; catalog absence is structural and must fail fast.

Do not block forever on `await model_loaded_event.wait()`; cap the wait with a wide timeout and proceed optimistically on timeout.

Do not probe `?include_status=true` and branch on internal state strings; gate on catalog presence and coordinate through signals.

Do not classify `LOADING`, `BUSY`, or `ENGINE_DEAD` as availability reasons in external code; these are internal Stargate states and must not be exposed to callers.

Do not re-emit Stargate lifecycle signals from external services; subscribers consume them and only Stargate emits them.

Do not place custom endpoints under `/v1/*`.

Do not directly edit `scope_vocabulary` or `corpus_hints` in `~/.rag/store/rag_metadata.db`.

Do not treat response-store notices as failures.

Do not bypass `manage` with Docker, systemctl, pkill, or direct script starts.

Formal prohibitions:

- `do not wait_for(model.loaded) when sending_first_request`.
- `do not branch_on_internal_state when external_service_code`.
- `do not re_emit(Stargate_lifecycle_signal) when service != Stargate`.
- `do not use /v1/* when endpoint is custom_or_project_specific`.
- `do not retry when flagged_payload_response indicates stored response`.

## Examples

Correct topology sentence: `Stargate runs on port 9999 — the only client-facing endpoint`.

Correct Gateway sentence: `Gateway runs inside the edge container; accessed by the edge Stargate on container-local port 9998`.

Correct deploy loop:

```text
quality_gate(files=[...])
manage(action="sync_restart", service=X)
manage(action="wait_healthy", service=X, timeout=120)
```

Correct MCP source-edit deploy:

```text
manage(action="sync_restart", service="mcp")
docker images universal-mcp-server --format "Created: {{.CreatedAt}}"
docker inspect mcp-server --format '{{.State.StartedAt}}'
observability(operation="raw_sql", params={"sql": "SELECT ts, signal FROM events WHERE signal='mcp.oauth.server.started' ORDER BY ts DESC LIMIT 1"})
```

Correct Event Service first check:

```bash
scripts/query-events --op recent-failures --limit 20
```

Correct MCP equivalent:

```text
observability(operation="recent-failures", params={"limit": 20})
```

Correct async logging import:

```python
from universal_logging import get_logger
logger = get_logger(__name__)
```

## Minimal operating summary

Read this before ULG repo work. Use `:9999` as the only client-facing endpoint and treat `:9998` as container-internal Gateway access. Use `manage` or `./manage` for service lifecycle. Run `quality_gate(files=[...])`, then `manage(action="sync_restart", service=X)`, then `manage(action="wait_healthy", service=X, timeout=120)` after code edits. Never agent-run `manage(action="rebuild", service="gateway")` or `manage(action="rebuild", service="mcp")`. For MCP source edits, use `manage(action="sync_restart", service="mcp")` and verify the new image timestamp with `docker images universal-mcp-server --format "Created: {{.CreatedAt}}"`. Query Event Service before logs. Keep Stargate authoritative for model lifecycle. Keep `/v1/*` standard OpenAI-compatible only. Route files through the correct sandbox. Use `universal_logging`. Keep Gateway and Workers pure passthrough. Treat lifecycle signals and response-store notices correctly.