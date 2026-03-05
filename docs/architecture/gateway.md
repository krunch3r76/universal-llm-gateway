# Gateway Service

Inference execution engine. Manages worker processes, model loading/unloading,
and serves inference requests inside network-isolated containers.

**Source**: `services/_universal-llm-gateway/`

## Entry Point

`src/main.py` → `create_app()` (app factory):
- TCP: `--host 0.0.0.0 --port 9998` (default)
- Unix socket: `--unix-socket /path` (overrides host/port)
- CPU priority fix for Docker environments
- Engine env loading from Docker env files

## Worker Architecture

One worker process per loaded model. Workers run in separate processes
for CUDA isolation.

```
Gateway (FastAPI, port 9998)
  ├─ WorkerController
  │   ├─ ModelLoader         → start_worker_if_needed() → finalize_load()
  │   ├─ ModelUnloader       → unload flow
  │   └─ ChatHandler         → inference routing
  ├─ ProcessLifecycleManager → process start/stop/crash handling
  └─ Worker Process (subprocess)
      └─ python -m src.core.workers.worker
          └─ Inference engine (llama.cpp via process_ipc RPC)
```

IPC between Gateway and workers uses Unix sockets in `/tmp/universal-protocol/`.

### Worker State Machine

```
IDLE → LOADING → LOADED → BUSY → IDLE → UNLOADING → IDLE
```

### Concurrency Control

`FifoCapacityGate` (from `universal_concurrency`) limits per-worker
concurrency to `parallel_slots` (configured in model loader config).
Default: 1 (serial execution).

## Model Load Flow

1. `POST /api/v1/models/{model_id}/load` (or auto-load)
2. `start_worker_if_needed()` — spawns worker subprocess
3. Worker loads model via `process_ipc` RPC
4. `finalize_load()` — registers model, emits `ModelLoaded` telemetry
5. Model available for inference

## Telemetry

Gateway reports to Edge Stargate via WebSocket:
- `GATEWAY_SNAPSHOT` — full catalog + resources on connect
- `RESOURCE_UPDATE` — VRAM/RAM, active requests (periodic)
- `MODEL_LOADED` / `MODEL_UNLOADED` — model lifecycle
- `MODEL_BUSY` / `MODEL_IDLE` — per-model state
- `TELEMETRY_HEARTBEAT` — liveness

Telemetry constructed via `@telemetry_factory` in `libs/universal_protocol/`.

## API Endpoints

### OpenAI-Compatible
- `GET /v1/models` — list loaded models
- `POST /v1/chat/completions` — chat inference (streaming + non-streaming)
- `POST /v1/embeddings` — embeddings
- `POST /v1/audio/transcriptions` — Whisper
- `POST /v1/images/generations` — image generation

### Management
- `POST /api/v1/models/{model_id}/load` — load model
- `DELETE /api/v1/models/{model_id}` — unload model
- `GET /api/v1/status/detailed` — system status
- `GET /api/v1/catalog/*` — model catalog

### WebSocket
- `/ws/stargate` — Stargate telemetry channel
- `/ws/state` — state updates

## How Edge Stargate Connects

Edge Stargate and Gateway run in the same container.
Edge config: `url: "http://localhost:9998"` (container-local).
Edge uses `SingleGatewayManager` with the gateway URL.
Requests: HTTP `POST /v1/chat/completions` from Edge Stargate to Gateway.

## Module Map

```
src/
  main.py                      Entry point
  app/app_factory.py           FastAPI app creation
  core/
    workers/
      controller.py            WorkerController (delegates to loader/unloader/chat)
      model_operations/
        load_flow.py           Model load orchestration
      process/
        lifecycle.py           ProcessLifecycleManager
      worker/
        __main__.py            Worker subprocess entry point
    websocket/
      init_cache.py            Initial catalog with max_concurrent_requests
  routes/                      HTTP route handlers
```
