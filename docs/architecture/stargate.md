# Stargate Service

Client-facing orchestration service. Handles HTTP API, request routing,
federation coordination, pipeline execution, and model profiles.

**Source**: `services/universal-stargate/`

## Entry Point

`start_proxy.py` → `main()`:
1. Path setup (adds project root to `sys.path`)
2. JSON monkey patch (`ModelId` serialization)
3. Logging config (`config/logging_config.py`)
4. App import (`systems/proxy/app.py`)
5. Uvicorn (TCP or Unix socket via `STARGATE_UNIX_SOCKET` env)

## App Creation (`systems/proxy/app.py`)

1. Load `FederationConfig` (from `STARGATE_CONFIG` or `config/stargate_config.yaml`)
2. Create FastAPI app with lifespan context manager
3. Add mode-specific middleware (auth, hop count, endpoint guard)
4. Mount routers: `v1`, `api`, `health`, `schedule`, `monitoring`, `forwarding`,
   `cloud_passthrough`

## Lifespan Startup

`init_proxy()` → `StargateProxy` → `startup_proxy()`:
- HTTP client, gateway manager, resource manager
- Token and parameter managers
- Capacity pool
- Federation integration (`init_federation`)
- Request components (`RequestPreparer`, `RequestExecutor`)
- Pipeline system (`PipelineRegistry`, `PipelineExecutor`)
- Hot reload, event consumers, shutdown handler

## Core Components

| Component | File | Role |
|---|---|---|
| `StargateProxy` | `systems/proxy/stargate/proxy.py` | Central orchestrator |
| `RequestPreparer` | `systems/proxy/core/nonstreaming/preparer.py` | Validation, model ID, profiles, sticky |
| `RequestExecutor` | `systems/proxy/core/nonstreaming/executor/core.py` | Gateway selection, forwarding |
| `RequestContext` | `systems/proxy/core/nonstreaming/context.py` | Per-request state |
| `PipelineExecutor` | `systems/pipeline/core/executor.py` | DAG-based pipeline execution |
| `PipelineRegistry` | `systems/pipeline/registry/core.py` | Pipeline loading and model resolution |

## Request Flow (Non-Streaming)

```
POST /v1/chat/completions
  → chat_completions() (routers/v1/chat_completion.py)
  → StargateProxy.submit_chat_request()
  → process_chat_completion() (stargate/requests/chat.py)
    ├─ RequestPreparer.prepare_request()
    ├─ Pipeline? → PipelineExecutor.execute()
    └─ Model? → RequestExecutor.execute_request()
         ├─ select_gateway_and_load_model()
         │   ├─ Master mode: _route_to_federated_gateway()
         │   └─ Edge: model_manager.ensure_model_loaded()
         ├─ apply_federated_token_management()
         └─ _execute_federated_request()
              └─ FederatedRequestForwarder.forward_request()
```

Capacity retry loop: on 503/504 or retryable 502, retries with backoff
until `queue_timeout` or `upstream_retry_timeout`.

## API Surface

### OpenAI-Compatible (routers/v1/)
- `POST /v1/chat/completions` — chat (streaming + non-streaming)
- `GET /v1/models` — list models (`?include_sources=true` for debugging)
- `POST /v1/embeddings` — embeddings
- `POST /v1/audio/transcriptions` — Whisper
- `POST /v1/pipelines/estimate` — estimate token budgets and first-fit-decreasing batches for pipeline inputs

**`/v1/models` returns both local and cloud models.** Cloud model IDs contain
`/` (e.g. `google/gemini-2.5-pro`, `anthropic/claude-3.5-sonnet`); local GPU
model IDs do not. Cloud models are sourced from the cloud proxy and only appear
when the cloud proxy service is running. Stargate is the **sole inference
endpoint** for both model types — callers never contact the cloud proxy directly
for inference.

## Cloud Proxy Integration

`systems/cloud/` integrates the cloud proxy (`universal_cloud_proxy`, port 8200)
into Stargate's model catalog and request routing.

- Cloud models are registered in Stargate's catalog when the proxy is healthy
- Inference requests for cloud model IDs are forwarded internally by Stargate
  to the cloud proxy, which relays them to OpenRouter
- External callers always use `POST /v1/chat/completions` on Stargate (`:9999`)
  regardless of whether the target model is local or cloud

The cloud proxy's own `/v1/chat/completions` endpoint is **internal** — it is
not intended for direct client access. Its metadata endpoints (`/api/models`,
`/api/select`) may be queried directly for model discovery and capability
filtering when needed.

### Internal/Federation (routers/api/)
- `POST /api/v1/federation/inference` — federated inference forwarding
- `POST /api/v1/federation/models/load` — remote model load
- `POST /api/v1/federation/tokens/count` — remote token counting
- `GET /api/v1/health` — health check

### Cloud Metadata Passthrough (routers/cloud_passthrough.py)
- `GET /api/models` — cloud model catalog passthrough via Stargate
- `POST /api/select` — cloud model selection passthrough via Stargate

These endpoints forward to cloud proxy metadata APIs over UDS or TCP and return
503 when the cloud proxy is unavailable. They preserve the single endpoint
contract: clients can stay on Stargate `:9999` without calling cloud proxy
ports directly.

## Estimator-Driven Review Flow

Code-review callers (`/consult-review`, `scripts/consult --role reviewer --pipeline`)
use a two-step orchestration:

1. `POST /v1/pipelines/estimate` with file sizes to compute token estimates and batches
2. Run `POST /v1/chat/completions` with model `code-review` for each batch in parallel

This keeps batching policy centralized in Stargate and avoids per-caller drift.

### WebSocket
- `/ws/federation/master` — accept Remote connections (Master mode)
- `/ws/federation/edge` — accept Relay connections (Edge mode)

## Profiles and Transformations

- `systems/profiles/`: model profile management (per-model generation defaults)
- `systems/transformations/`: request/response transformations (registry pattern)
- Profiles applied during `RequestPreparer.prepare_request()`
  - Master mode (no local gateway): applies client-facing profile policy
    (system prompts + shared generation params), then forwards to execution target.
  - Normal mode (local gateway): applies full profile policy with engine-aware params.

## Module Map

```
systems/proxy/
  stargate/          Proxy orchestrator, request handlers
  core/              Non-streaming/streaming execution, lifecycle
  routers/           HTTP route handlers (v1, api, health, monitoring)

systems/federation/  Federation subsystem (see federation.md)
systems/routing/     Routing subsystem (see routing.md)
systems/pipeline/    Pipeline subsystem (see pipeline-system.md)
systems/cloud/       Cloud model proxy (OpenRouter, etc.)
systems/audio/       Audio transcription API
systems/profiles/    Model profiles and generation defaults
```
