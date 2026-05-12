# Async Pipeline Dispatch

Stargate's async pipeline dispatch surface decouples pipeline execution from
the request/response lifecycle. Clients (MCP tools, agent automation) that
cannot tolerate multi-minute synchronous waits spawn pipelines via
`POST /api/v1/pipelines/dispatch` and poll results via
`GET /api/v1/pipelines/executions/{execution_id}`.

Phase 1 introduces the **transport boundary**: admission, in-process tracker,
polling endpoint, and MCP tool pair. Phase 2 will add push delivery via
`result_delivery`.

## Topology

```
MCP client ──► :9999 host Stargate ─UDS─► edge Stargate ──► PipelineExecutor
  │                                           │
  │                                           ├── async: execute_async() (bg task)
  │                                           │         └─► tracker.complete/fail
  │                                           │
  └── GET /api/v1/pipelines/executions/{id} ◄─┘
          └─► tracker.wait_for_terminal(wait)
```

Invariant: `:9999` is the sole client-facing endpoint — async dispatch adds no
new ingress. All client interaction is HTTP against Stargate's existing port.

## Endpoints

### `POST /api/v1/pipelines/dispatch` → `202 Accepted`

Admission-only. Validates the pipeline exists, reserves a tracker slot, spawns
an `asyncio.Task` running `PipelineExecutor.execute_async(...)`, and returns
immediately.

Request body (Pydantic `DispatchRequest`, `extra="allow"`):

```json
{
  "model": "pipeline-id",
  "messages": [{"role": "user", "content": "..."}],
  "pipeline_options": {},
  "result_delivery": null,
  "caller_agent": "cursor"
}
```

`caller_agent` is optional dispatch provenance. When present, Stargate stores it
on the tracker record and includes it in `pipeline.dispatch.async` and
`pipeline.dispatch.completed`.

Response:

```json
{
  "execution_id": "uuid",
  "pipeline": "pipeline-id",
  "started_at": "2026-04-18T12:34:56Z",
  "status": "running"
}
```

Error envelope (all `/api/v1/pipelines/*` errors):

```json
{ "error": { "code": "...", "message": "..." } }
```

| Status | Code | Meaning |
|---|---|---|
| 400 | `invalid_body` / `validation_error` | malformed JSON or schema failure |
| 404 | `pipeline_not_found` | unknown pipeline id |
| 503 | `capacity_exhausted` | tracker saturated with running executions |

### `GET /api/v1/pipelines/executions/{execution_id}`

Returns the tracker record. Supports `?wait=<seconds>` (server-clamped to 60s)
for short-poll semantics — well under the MCP 300s read timeout.

Response shape mirrors `PipelineExecutionRecord.to_dict()`:

```json
{
  "execution_id": "uuid",
  "pipeline": "pipeline-id",
  "status": "running|completed|failed",
  "started_at": "...",
  "completed_at": "..." | null,
  "result": { "content": "...", "model": "...",
              "usage": {"prompt_tokens": …, "completion_tokens": …,
                        "total_tokens": …, "reasoning_tokens": …},
              "reasoning": <provider-shape> | null,
              "duration_s": 12.3 } | null,
  "error":  { "code": "...", "message": "...",
              "data": <structured-upstream-body> | null } | null
  "caller_agent": "cursor" | null
}
```

`usage.reasoning_tokens` is a subset of `completion_tokens` surfaced so
callers can distinguish visible-output spend from reasoning spend.
`result.reasoning` preserves the provider's structured shape when any
pipeline step produced a reasoning trace — typically a list of
thinking/content blocks (Claude) or a plain string (xAI Grok,
OpenAI Responses API). `error.data` carries the parsed upstream JSON
body when the failure originated from a provider HTTP 4xx/5xx with a
JSON response; `null` otherwise.

### Reasoning traces by provider

| Provider / surface | `result.reasoning` | `usage.reasoning_tokens` |
|---|---|---|
| OpenAI /chat/completions | — | integer (from `completion_tokens_details.reasoning_tokens`) |
| OpenAI /responses (bridged) | structured blocks | integer |
| Anthropic Messages | structured thinking blocks (see B2b) | 0 — Anthropic bills thinking as output; no separate count |
| xAI Grok /chat/completions | string (upstream-provided) | integer when present |
| Google Gemini compat | — | 0 — requires native `generateContent` + `thinkingConfig` to surface |

Callers that need a uniform reasoning-presence check should use
`bool(result.reasoning)` rather than `result.usage.reasoning_tokens > 0`
— the latter under-reports for Anthropic.

404 with `code="execution_not_found"` if unknown or evicted by TTL.

### `GET /api/v1/pipelines/dispatch/stats`

Returns a tracker occupancy snapshot:

```json
{
  "running": 0,
  "completed": 0,
  "failed": 0,
  "terminal": 0,
  "max_records": 256,
  "retention_seconds": 86400.0,
  "oldest_terminal_age_seconds": null,
  "oldest_running_age_seconds": null
}
```

Useful for dashboards and admission-aware dispatchers that want to inspect load
before enqueueing more async runs.

### `DELETE /api/v1/pipelines/executions/{execution_id}`

Cancels an in-flight async-dispatched execution by cancelling its retained
background task. Response is the terminal tracker record (status `failed`,
error code `pipeline_execution_cancelled`). Idempotent: cancelling an already
terminal execution returns that record unchanged.

## PipelineExecutionTracker

In-process store of `PipelineExecutionRecord` objects keyed by `execution_id`.

| Responsibility | Behavior |
|---|---|
| Admission | `register_execution()` — rejects with `TrackerCapacityError` when capacity reached and every slot is running |
| Terminal transition | `complete_execution()` / `fail_execution()` — idempotent; only first call wins |
| Polling | `get()`, async `wait_for_terminal(timeout)` via per-record `asyncio.Event` |
| Retention | TTL pruning: terminal records evicted `retention_seconds` (default 86400s / 24h) after terminal transition; running records never dropped |
| Provenance | Stores optional `caller_agent` string from dispatch body for observability attribution |
| Events | Emits `pipeline.dispatch.async` on admit, `pipeline.dispatch.completed` on terminal, `pipeline.dispatch.rejected` on capacity refusal, `pipeline.dispatch.tracker.expired` on TTL prune |

¬`asyncio.Lock` or `Semaphore` — state mutations are single-step dict
operations under the asyncio single-thread invariant. Concurrency coordination
lives in `asyncio.Event` per record.

## PipelineExecutor Refactor

`PipelineExecutor.execute()` was decomposed to support both the sync
(`/v1/chat/completions`) and async (`/api/v1/pipelines/dispatch`) surfaces
without duplicating setup or DAG-execution logic:

```
execute(context)                   # sync entrypoint — preserved external behavior
├── generate_execution_id()
├── prepare_execution(context, execution_id=…) → PreparedPipelineExecution
├── _run_prepared_execution(prepared)          → PipelineExecutionOutcome
└── _build_chat_completion_response(prepared, outcome) → Response
                                    (sets X-Pipeline-Execution-Id header)

execute_async(context, *, execution_id, started_at, tracker)
├── prepare_execution(...)
├── _run_prepared_execution(...)
└── tracker.complete_execution() | tracker.fail_execution()
```

The `X-Pipeline-Execution-Id` response header invariant is preserved via
`ResponseBuilder.build_response()` in the sync path.

## MCP Tools

`services/mcp-server/tools/pipeline.py` exposes:

- `pipeline_async(pipeline, messages, options?, result_delivery?)` — thin relay
  to `POST /api/v1/pipelines/dispatch`; returns `execution_id` immediately
- `pipeline_result(execution_id, wait_seconds=0.0)` — relay to
  `GET /api/v1/pipelines/executions/{id}?wait=…`; short-polls with server-side
  wait clamped to 60s

Both tools follow the MCP tool relay invariant (HTTP-only, no direct DB or
internal imports).

## Events

See `docs/event-contracts.md` for the signal reference table. The dispatch
signals are node-scoped and complement the existing global `pipeline.started` /
`pipeline.completed` / `pipeline.failed` signals:

- `pipeline.*` describe the **DAG lifecycle** (steps, completion, cancellation)
- `pipeline.dispatch.*` describe the **async transport boundary** (admission,
  tracker terminal state, capacity rejection)

Both emit together on async runs; only pipeline signals emit on sync runs.

## Result Delivery (Phase B3)

When a dispatched execution carries a `result_delivery` config, the tracker
invokes a one-shot delivery hook at terminal transition. Stargate posts a
turn to the configured agent-bus thread so dispatchers that can't or won't
poll still receive the result.

### Hook contract

`PipelineExecutionTracker` accepts an optional
`delivery_sender: Callable[[PipelineExecutionRecord], Awaitable[None]]`.
When wired, the tracker fires it after `pipeline.dispatch.completed` on both
`complete_execution` and `fail_execution` paths. Duplicate-ignore early
returns do NOT fire the hook.

The default sender is
`systems.pipeline.core.execution.async_tracker_delivery.deliver_result`,
wired in `component_factory` with `event_bus` + `AGENT_BUS_TOKEN` bound via
`functools.partial`. If `AGENT_BUS_TOKEN` is unset, the sender is `None` and
no delivery runs (startup warning logged).

### Envelope shape

Body is a JSON document:

```json
{
  "execution_id": "...",
  "pipeline": "frontier-dispatch",
  "status": "completed",
  "completed_at": "2026-04-19T00:00:10Z",
  "content": "...",
  "reasoning": <provider-shape or null>,
  "usage": {"prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ...},
  "duration_s": 10.0,
  "error": {"code", "message", "data"}
}
```

`content` / `reasoning` / `usage` / `duration_s` present only on success;
`error` present only on failure.

### Event triad

All three signals are `role: observation`, `scope: node`:

- `pipeline.dispatch.delivery.sent` — 2xx from agent-bus
- `pipeline.dispatch.delivery.failed` — non-2xx or transport error (tracker
  record unchanged; poll still returns the terminal result)
- `pipeline.dispatch.delivery.skipped` — required field missing from
  `result_delivery`, or no delivery config on a record when a sender is
  wired (`reason ∈ {incomplete_delivery_config, no_delivery_config}`)
- `pipeline.dispatch.cancelled` — explicit operator cancellation (`DELETE
  /api/v1/pipelines/executions/{id}`), payload `{pipeline_id, execution_id, source}`

### Failure model

One-shot. Non-2xx responses and transport errors both emit `.failed` and
return; the tracker record is not mutated. Callers that require guaranteed
delivery must poll as a fallback or re-drive via MCP.

Authentication uses `Authorization: Bearer $AGENT_BUS_TOKEN`. The
`from`/`to` fields are constrained to the `AgentName` enum (9 fixed values)
on the agent-bus side, capping impersonation surface to registered
identities.

## Durability (post-B4)

Terminal tracker records are journaled to `~/.gateway/pipeline-dispatch.db`
(`$DATA_DIR/pipeline-dispatch.db` when `DATA_DIR` is set) and survive Stargate
restart. `GET /api/v1/pipelines/executions/{id}` checks the in-memory tracker
first, then falls back to this sqlite journal on tracker miss.

Running records are not durable. In-flight dispatches are cancelled on restart
and are not resurrected from the journal. Callers that need running-dispatch
durability must re-dispatch after restart with the original request body.

## Frontier-Dispatch Pipeline

One unified pipeline — `frontier-dispatch` — serves all frontier providers
and all trAId personas. The pipeline routes directly to Stargate's
provider-native endpoints (Anthropic messages, OpenAI/xAI responses, Google
generateContent) via the in-process `CloudProxyClient` forwarder, runs a
bounded tool-use loop via `libs/agent_seat/native_loop`, and conditionally
hydrates the dispatched agent's Cortex boot when the caller specifies a
persona.

Pipeline caller shape:

```json
{
  "pipeline_id": "frontier-dispatch",
  "pipeline_options": {
    "model": "openai/gpt-5.4",
    "agent": "gatherer",
    "max_tool_turns": 10,
    "generation_parameters": {"reasoning_effort": "high"}
  },
  "messages": [{"role": "user", "content": "..."}]
}
```

`pipeline_options.model` is required; `agent` is optional; everything else has
sensible defaults.

MCP callers typically reach this via `team_generate` for persona consults or
`frontier_generate` for raw persona-free calls:

```python
team_generate(
    agent="gatherer",
    messages=[{"role": "user", "content": "..."}],
    reasoning_effort="high",
    caller_agent="cursor",
)
# Then: pipeline(op="result", execution_id=<id>, wait_seconds=60.0)
```

### Runtime modes

| Mode | Trigger | Behavior |
|---|---|---|
| Team-seat | `pipeline_options.agent ∈ {gatherer, skeptic, synthesizer, reviewer}` | Cortex hydration + birth prompt + curated team toolset (`cortex`, `rag`, `agent_bus`) injected for the call when client-side MCP is enabled. Emits `pipeline.frontier.dispatch.hydrated`. |
| Persona-free | `pipeline_options.agent` omitted | Raw native call. No hydration event. Optional read toolset (`cortex`, `rag`) via `pipeline_options.mcp` (default `true`). |

Provider selection is derived from the model id prefix: `openai/*` → OpenAI
Responses, `xai/*` → xAI Responses, `anthropic/*` → Anthropic Messages,
`google/*` → Google generateContent. Reasoning is configured via
`pipeline_options.generation_parameters` (e.g. `reasoning_effort: high` for
OpenAI / Google compat, `thinking: {type: enabled, budget_tokens: N}` for
Anthropic); xAI Grok reasoning is model-baked.

### Frontier Dispatch Handler (`frontier_dispatch_v1`)

The step handler lives at
`systems/pipeline/core/handlers/frontier_dispatch.py`. Per dispatch:

1. **Resolve** — extract `model`, optional `agent`, `max_tool_turns`, and
   generation parameters from `pipeline_options` (step-level fields act as
   fallbacks).
2. **Hydrate (team-seat only)** — async-fetch the dispatched agent's
   Cortex boot via `libs/agent_seat/hydration.py`; assemble the system
   prompt (birth prompt + subagent preamble + hydration briefing card)
   via `libs/agent_seat/prompts.py`. Emits
   `pipeline.frontier.dispatch.hydrated`.
3. **Dispatch** — build a `FrontierRequest`, invoke
   `libs/agent_seat/native_loop.run_native_tool_loop` with an in-process
   `send_native` closure that posts to `CloudProxyClient` with the
   `X-Pipeline-Execution-Id` / `X-Pipeline-Step-Id` / `X-Pipeline-Internal`
   headers propagated so the dispatch tracker can associate upstream
   activity.
4. **Run bounded tool loop** — the native loop handles multi-turn tool
   resolution uniformly across providers (Anthropic `tool_use` blocks,
   OpenAI Responses `function_call` items, Google function call parts).
   Tool calls execute locally via `libs/agent_seat/executor.execute_tool`.
5. **Observe** — a `cancel_check` closure polls `pipeline_dispatch_tracker`
   at every turn boundary; on cancellation the loop terminates cleanly with
   `NativeLoopResult.cancelled=True`.
6. **Return** — `StepOutput.raw` = final assistant content;
   `StepOutput.json` carries the full tool-call trace, `turns_used`,
   `exhausted`, `cancelled`, `provider`, and `hydration` metadata.

Event signals emitted per dispatch (all `scope: node`):

- `pipeline.frontier.dispatch.hydrated` — briefing loaded (team-seat only)
- `pipeline.frontier.dispatch.tool.called` — per successful tool invocation
- `pipeline.frontier.dispatch.tool.failed` — per error envelope / exception
- `pipeline.frontier.dispatch.completed` — loop returned terminal content
- `pipeline.frontier.dispatch.exhausted` — `max_tool_turns` hit without terminus

All five carry `provider`; the four non-hydrated signals carry
`agent: str | None` so persona-free dispatches remain attributable.

### YAML layout

```
pipelines.local/
  frontier_dispatch/  v1/  frontier-dispatch-v1.yaml
                      models.yaml     # empty — caller supplies model
```

Pipelines are auto-discovered at Stargate startup. `prompts.yaml` is not
needed: the handler builds its system prompt from the caller-supplied
`agent` field directly.

### Shared library surface

The tool-resolution loop is the single source of truth for both transports:

| Caller | Transport | Where |
|---|---|---|
| Stargate pipeline (`frontier_dispatch_v1`) | In-process `CloudProxyClient` | `systems/pipeline/core/handlers/frontier_dispatch.py` |
| MCP `frontier_generate` | HTTP → Stargate `POST /api/v1/frontier/generate` (async dispatch envelope) | `services/mcp-server/tools/frontier.py` |

Both callers depend on `libs/agent_seat/native_loop.run_native_tool_loop`
(loop), `libs/llm_adapters/*` (provider request/response translation), and
`libs/agent_seat/executor.execute_tool` (MCP tool dispatch).

### Usage

See the agent skill `agent-skills/frontier-dispatch.md` (cortex sandbox) for invocation
patterns (`pipeline(op="async", pipeline_id="…")`), polling via
`pipeline(op="result", execution_id="…")`, and per-provider reasoning
surfacing.

## Non-Goals

- No running-state resurrection on restart — only terminal records are journaled;
  in-flight executions are cancelled via `asyncio.Task` teardown.
- No delivery retry — one shot at terminal transition; retry belongs on
  the dispatcher side.
- No auth/quota — capacity is the sole admission control.
