# System Overview

A federated LLM inference stack with DAG-based pipeline orchestration,
network-isolated execution, and an OpenAI-compatible API surface.

## Two-Service Architecture

| Service | Port | Deployment | Responsibility |
|---|---|---|---|
| **Stargate** | 9999 | Host process | Client API, routing, federation, pipelines, profiles |
| **Gateway** | 9998 | Container (network_mode: none) | Inference execution, worker management, model loading |

```
Client ──► Stargate:9999 ──► [Unix socket] ──► Edge Stargate ──► Gateway:9998 ──► Worker (RPC)
           (orchestration)                      (container)        (execution)      (inference)
```

Port 9999 is the sole client-facing endpoint. Port 9998 is container-internal
only — unreachable from the host or clients.

## Three Roles

| Role | Process | Gateway | Function |
|---|---|---|---|
| Master | Host | None | Orchestrates, routes to Edges |
| Relay | Host | None | Bridges Master to network-isolated Edge |
| Edge | Container | Colocated | Executes inference |

Mode detection: `proxy.gateway_manager is not None` ⟹ Edge.

## Default Topology

```
Master (host:9999)
 ├─ local_edge (Unix socket) → Edge-localhost (container) → Gateway (:9998)
 └─ remotes (TCP) → Relay-jupiter (:9999) → Edge-jupiter (UDS) → Gateway (:9998)
```

## Request Lifecycle

1. **Client** → POST `/v1/chat/completions` to Master:9999
2. **Stargate proxy** → `RequestPreparer` validates, resolves model ID, applies profiles
3. **Pipeline check** → if model ID matches a pipeline, dispatch to `PipelineExecutor`
4. **Routing** → `ModelRouter` → `DecisionEngine` selects gateway (feasibility tiers T0/T1/T2)
5. **Admission** → `AdmissionQueue` gates on per-(gateway, model) capacity
6. **Forward** → `FederatedRequestForwarder` sends to Remote Stargate
7. **Relay** → forwards to Edge via Unix socket
8. **Edge** → forwards to Gateway:9998 → Worker process → inference engine
9. **Response** flows back the same path

## Key Subsystems

| Subsystem | Location | See doc |
|---|---|---|
| Proxy + request handling | `systems/proxy/` | `stargate.md` |
| Gateway + workers | `services/_universal-llm-gateway/` | `gateway.md` |
| Federation (Master/Relay/Edge) | `systems/federation/` | `federation.md` |
| Routing + capacity | `systems/routing/` | `routing.md` |
| Pipeline execution | `systems/pipeline/` | `pipeline-system.md` |
| Shared libraries | `libs/` | `libraries.md` |
| Event coordination | `libs/universal_event_bus/` | `event-system.md` |
| Tools and scripts | `tools/`, `scripts/` | `tools.md` |
| Config files and env vars | Various | `configuration.md` |

## Source Code Layout

```
services/
  universal-stargate/       Stargate service (client API, orchestration)
    systems/
      proxy/                Request handling, streaming, lifecycle
      federation/           Federation (master, relay, edge, link protocols)
      routing/              Gateway selection, capacity, admission
      pipeline/             Pipeline execution engine
      cloud/                Cloud model proxy
      audio/                Audio/Whisper API
      profiles/             Model profiles and transformations
  _universal-llm-gateway/   Gateway service (inference execution)
    src/core/workers/       Worker process management
  rag/                      RAG indexing and search service

libs/
  universal_event_bus/      Event pub/sub, coordination
  universal_protocol/       JSON-RPC, errors, telemetry, streaming
  universal_transport/      Async transport (Unix/TCP sockets, framing)
  process_ipc/              Worker process IPC
  universal_logging/        Structured JSON logging
  universal_concurrency/    FIFO capacity primitives
  universal_hot_reload/     File watching with debounce
  model_id/                 Model ID parsing and normalization
  provenance/               Pipeline artifact provenance tracking

pipelines/                  Pipeline definitions (committed)
pipelines.local/            Pipeline definitions (local dev, gitignored)
tools/                      Pipeline test, viewer, utilities
scripts/                    CLI tools, deployment, management
```
