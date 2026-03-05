# Federation System

Distributed routing across network-isolated Gateways. Master orchestrates,
Relay forwards statelessly, Edge executes locally.

**Source**: `services/universal-stargate/systems/federation/`

## Roles

| Mode | execution_capable | Gateway | Connection |
|---|---|---|---|
| MASTER | false | None | Accepts Remote WebSocket connections |
| REMOTE (Relay) | false | None | Connects to Master, forwards to Edge |
| EDGE | true | Required | Passive (accepts inbound from Relay) |

**Detection**: `proxy.gateway_manager is not None` ⟹ Edge.

## Topologies

### Relay Topology (default, VPS)

```
Master (host:9999)
 ├─ local_edge (UDS) → Edge-localhost (container) → Gateway:9998
 └─ Relay-jupiter:9999 (TCP) → Edge-jupiter (UDS) → Gateway:9998
```

### Golem Topology

```
Master (host:9999) → Edge (direct, no relay) → Gateway:9998
```

Master connects directly to Edge Stargates; Relay role collapses.
Uses HTTP polling instead of WebSocket for telemetry.

## Routing Flow

1. Client → Master:9999 `/v1/chat/completions`
2. `FederatedGatewayManager.get_healthy_gateways()` → list of `FederatedGateway`
3. `DecisionEngine.select()` — feasibility (T0/T1/T2) + utility scoring
4. `AdmissionQueue.acquire()` — capacity gating
5. `FederatedRequestForwarder.forward_request()` → Remote `/api/v1/federation/inference`
6. Relay → Edge (Unix socket) → Gateway → Worker → inference
7. Response flows back

## Telemetry Lifecycle

### Push (WebSocket, default)

```
Gateway → Edge Stargate → [WebSocket] → Relay/Master
   (telemetry events)      (federation protocol)
```

`MasterTelemetryReceiver.handle_message()` → `FederatedGatewayManager.update_from_event()`

### Pull (HTTP polling, Golem topology)

Master polls Remote at `telemetry_poll_interval_ms`.
Remote computes deltas; Master applies via `apply_delta()` / `apply_snapshot()`.

### Telemetry Event Types

| Type | Content | Trigger |
|---|---|---|
| `GATEWAY_SNAPSHOT` | Full catalog + resources | On connect |
| `RESOURCE_UPDATE` | VRAM/RAM, active requests | Periodic |
| `MODEL_LOADED` | Model ID, resources | After load completes |
| `MODEL_UNLOADED` | Model ID | After unload |
| `MODEL_LOADING_STARTED` | Model ID | Load begins |
| `MODEL_LOAD_FAILED` | Model ID, error | Load failed |
| `MODEL_BUSY` / `MODEL_IDLE` | Model ID | State change |
| `TELEMETRY_HEARTBEAT` | — | Liveness probe |

## Key Classes

| Class | File | Role |
|---|---|---|
| `FederatedGatewayManager` | `master/manager/federated_gateway_manager.py` | Gateway state from telemetry |
| `FederatedRequestForwarder` | `master/routing/forward.py` | Forward requests to Remotes |
| `DecisionEngine` | `systems/routing/selection/decision/engine.py` | Feasibility + scoring + selection |
| `MasterTelemetryReceiver` | `master/telemetry/receiver.py` | Receives telemetry, updates manager |
| `FederationIntegration` | `integration/core.py` | Mode-specific setup |
| `LoadOrchestrator` | `master/orchestration/load_orchestrator.py` | Model load coordination, eviction |
| `FederationCircuitBreaker` | `master/routing/` | Blocks requests to failing gateways |

## Dual Model Lists

| Field | Scope | Purpose |
|---|---|---|
| `available_models` | Internal | Full catalog for routing decisions |
| `activated_models` | Public | Filtered subset for `/v1/models` endpoint |

Routing accepts requests for ANY catalog model. `/v1/models` shows only
user-curated "activated" contexts. Edge sends both lists in `GATEWAY_SNAPSHOT`.

## Invariants

```
∀ Stargate s:
  mode(s) = MASTER ⟹ gateway(s) = ∅
  mode(s) = REMOTE ⟹ stateless(s)
  mode(s) = EDGE ⟹ ∃! gateway(s)

∀ (gateway, model): |in_flight_loads| ≤ 1  (single-flight)
∀ Edge telemetry: received_by_relay ⟹ forwarded_to_master
load_complete ⟺ HTTP_2xx  (not telemetry)
```

## API Endpoints

| Endpoint | Method | Mode | Purpose |
|---|---|---|---|
| `/api/v1/federation/models/load` | POST | Remote | Model load orchestration |
| `/api/v1/federation/inference` | POST | Remote | Inference forwarding |
| `/api/v1/federation/tokens/count` | POST | Remote | Token counting |
| `/ws/federation/master` | WS | Master | Accept Remote connections |
| `/ws/federation/edge` | WS | Edge | Accept Relay connections |

## Module Map

```
federation/
  common/           Shared config, types, middleware, protocol
  edge/             Edge mode (passive WS server, telemetry)
  integration/      Federation lifecycle (init, wire)
  link/
    ws/             WebSocket protocols (master, remote, local)
    http_polling/   HTTP polling (Golem topology)
  master/
    manager/        FederatedGatewayManager (gateway state)
    orchestration/  LoadOrchestrator (load coordination)
    routing/        Request forwarding, circuit breaker
    telemetry/      Telemetry receiver
  remote/
    api/            Inference and telemetry relay endpoints
    integration/    Remote lifecycle
```

## Relay Reachability Semantics (Operator View)

`relay-*` status in `./manage topology` is derived from:

1. Master source probe (`/v1/models?include_sources=true`)
2. Recent federation telemetry events (`federation.telemetry.received`)
3. Node env presence (`~/.gateway/nodes/<host>.env`)

This allows distinguishing:

- connected but no models
- unreachable (no recent telemetry)
- configured only (master not running)

## Configuration

See `configuration.md` for full YAML reference. Key fields:

- `federation.mode`: `master` | `remote` | `edge`
- `federation.local_edge`: socket path, stargate_id, api_key
- `federation.remotes[]`: url, stargate_id, api_key
- `federation.allowed_peers[]`: (Edge mode) authorized callers
