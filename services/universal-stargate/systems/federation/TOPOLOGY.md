# Federation Topology

**Version**: 2026-01-17  
**Status**: Canonical architecture reference

## Overview

The federation system enables distributed inference routing across network-isolated environments using a three-tier architecture with clear separation of concerns.

## Canonical Principles (2026-01-16)

> **A Relay Stargate is a stateless forwarding role, not a containment boundary.**

> **Topology describes reachability, not ownership.**

> **Master is a pure orchestrator with no local Gateway.**

These principles guide all topology decisions and eliminate hierarchical "ownership" confusion.

## Three-Tier Architecture

```
┌──────────────────────────────────────────────────────────┐
│ CONTROL PLANE (Master)                                   │
│ - Client requests                                        │
│ - Routing decisions                                      │
│ - Telemetry aggregation                                 │
│ - NO execution                                           │
│ Port: 9999 (host process)                               │
└──────────────────┬───────────────────────────────────────┘
                   │ (federation WS/HTTP or Unix socket)
                   ↓
┌──────────────────────────────────────────────────────────┐
│ RELAY PLANE (Optional Relays)                           │
│ - Stateless forwarding                                  │
│ - Auth boundary                                         │
│ - Telemetry aggregation                                 │
│ - NO execution                                           │
│ Port: 9999 (host process, different physical host)     │
└──────────────────┬───────────────────────────────────────┘
                   │ (federation WS/Unix socket)
                   ↓
┌──────────────────────────────────────────────────────────┐
│ EXECUTION PLANE (Edge Stargates + Gateways)            │
│ - Token counting                                        │
│ - Model loading                                         │
│ - Inference execution                                   │
│ - Resource management                                   │
│ Port: 9999 (container internal, network-isolated)      │
│ Gateway Port: 9998 (container internal)                │
└──────────────────────────────────────────────────────────┘
```

**Key property**: Execution authority is **never shared**. All execution decisions happen at the Edge/Gateway level.

**Port Notes**:
- Master and Relay can both use port 9999 because they run on different physical hosts
- Edge also uses port 9999 internally, no conflict due to `network_mode: none` (isolated container)
- Gateway uses port 9998 internally, only accessible from Edge within same container

## Stargate Modes

### Code Pattern: Detecting Mode

```python
# In code, gateway_manager presence is the canonical mode indicator
if proxy.gateway_manager is not None:
    # Edge mode: container deployment with colocated Gateway
else:
    # Master or Relay: host process, no direct gateway access
```

### Edge Mode (Execution Node)

**Role**: Passive endpoint, accepts inbound connections, executes inference

**Deployment**: Container (Edge Stargate + Gateway colocated)

```
┌─────────────────────────┐
│ Edge Stargate           │
│ - mode: edge            │
│ - gateway_manager ✓     │  ← ONLY Edge has this
│ - execution_capable ✓   │
│ - Inbound only          │
└───────┬─────────────────┘
        │ (direct access via gateway_manager)
        ↓
┌─────────────────────────┐
│ Local Gateway           │
│ - VRAM/RAM              │
│ - Model workers         │
└─────────────────────────┘
```

**Characteristics**:
- Has `gateway_manager` (direct gateway access) - **ONLY mode with this**
- Gateway colocated in same container
- Accepts inbound federation connections via `/ws/federation/edge` (WebSocket telemetry)
- Exposes `/api/v1/federation/*` HTTP endpoints (inference, tokens, models)
- NO outbound federation connections
- Can be network-isolated (`network_mode: none` in Docker)
- Validates inbound peers via `allowed_peers` config

**Use cases**:
- Network-isolated containers (`network_mode: none`, secure enclaves)
- Single-node deployments (no federation)
- GPU workers behind firewalls/NAT
- Direct Master→Edge via Unix socket (Pattern 1)

### Remote Mode (Relay Node)

**Role**: Active federation peer, connects TO Master, forwards to local Edge

**Deployment**: Host process (no container, no gateway_manager)

```
┌─────────────────────────┐
│ Remote Stargate (Relay) │
│ - mode: remote          │
│ - gateway_manager: None │  ← No direct gateway access
│ - Outbound to Master    │
└───────┬─────────────────┘
        │ (federation WS)
        ↓
┌─────────────────────────┐     ┌─────────────────────────┐
│ Master Stargate         │     │ Local Edge (container)  │
│ - Routing               │     │ - Unix socket           │
└─────────────────────────┘     │ - Federation protocol   │
                                │ - Has gateway_manager   │
                                └─────────────────────────┘
```

**Characteristics**:
- **No `gateway_manager`** - cannot access Gateway directly
- Connects TO Master via WebSocket
- Routes to local Edge container via `local_edge` (Unix socket federation)
- Exposes `/api/v1/federation/*` endpoints for Master to invoke
- Forwards telemetry from local Edge to Master
- **No guarantee** local Edge container is running

**Use cases**:
- VPS nodes with network access to Master
- Relay nodes aggregating multiple network-isolated Edges
- Multi-hop routing (Master → Relay1 → Relay2 → Edge)

**Execution capability**: Relay itself has no execution - all execution via Edge container

### Master Mode (Orchestrator)

**Role**: Pure orchestrator, no local execution, routes all work to Edges

**Deployment**: Host process (no container, no gateway_manager)

```
┌─────────────────────────┐
│ Master Stargate         │
│ - mode: master          │
│ - gateway_manager: None │  ← No direct gateway access
│ - NO local Gateway      │
└───────┬─────────────────┘
        │ (federation WS/HTTP or Unix socket)
        ↓
┌─────────────────────────┐
│ Edge Stargates          │
│ (via Relay or direct)   │
│ - Have gateway_manager  │
└─────────────────────────┘
```

**Characteristics**:
- **No `gateway_manager`** - cannot access any Gateway directly
- Receives all client requests (API entrypoint)
- Makes routing decisions (DecisionEngine)
- Aggregates telemetry from Edge nodes (via Relay or direct)
- Forwards to Edges via HTTP (TCP or Unix socket)
- Can connect to local Edge via `local_edge` config (Unix socket)
- Can connect to remote Relays/Edges via `remotes` config (TCP)
- **No guarantee** any Edge is running or reachable

**`execution_capable` config field**:
- `true` if Master has at least one execution target: `remotes` **or** `local_edge`
- `false` if Master has neither (pure router with no execution targets)
- This is about **reachable targets**, not "Master executes locally"
- **Note**: Even with `execution_capable=true`, actual availability depends on Edge container state

**Use cases**:
- Dedicated routing/orchestration node
- Multi-tenant coordinator
- Nodes without GPU/inference hardware
- Combined local + remote execution (default deployment)

## Mode Comparison

| Aspect | Edge | Remote (Relay) | Master |
|--------|------|----------------|--------|
| **`gateway_manager`** | **Present** | None | None |
| **Deployment** | Container | Host process | Host process |
| **Execution** | ✓ (via gateway_manager) | ✗ (forwards to Edge) | ✗ (pure orchestrator) |
| **Local Gateway** | Colocated in container | None (via Edge) | None |
| **Outbound federation** | None | WebSocket to Master | N/A |
| **Inbound connections** | `/ws/federation/edge` | HTTP + optional WS | `/ws/federation/master` |
| **Network requirements** | None (can be isolated) | Access to Master + Edge | Access to Remotes/Edge |
| **Auth** | `allowed_peers` | `master.api_key` | `remotes[].api_key` |
| **Availability guarantee** | N/A (is the target) | Container may not run | No execution target may be up |

**Code pattern summary**:
```python
# Edge detection (container with colocated Gateway)
is_edge = proxy.gateway_manager is not None

# Master/Relay detection (host process, no direct gateway)
is_host_process = proxy.gateway_manager is None
```

## Deployment Patterns

**Default**: All patterns use **Relay topology** (WebSocket-based telemetry) unless explicitly documented as Golem topology (HTTP polling).

**Mutual exclusivity**: Each Remote/Edge pair operates in exactly one topology mode (either Relay or Golem), configured via `disable_websocket` flag.

### Pattern 1: Master → Local Edge (Unix Socket)

**Use case**: Master on same host as Edge container (network-isolated)

```
Client → Master (host:9999) → Edge (container:9999) → Gateway (container:9998)
                │
                └── Unix socket (shared volume):
                    ├─ Telemetry: WebSocket over Unix socket
                    └─ Requests: HTTP over Unix socket (httpx)
```

**Key points**:
- Master runs as host process (TCP 9999 for client access)
- Edge runs in container with `network_mode: none` (internal port 9999)
- No port conflict: different network namespaces
- Connection via Unix socket (shared volume mount)
- **Both telemetry and requests use Unix socket** (different protocols)

**Unix socket transport**:
- **Telemetry**: WebSocket protocol over Unix socket (`LocalEdgeClient`)
- **Requests**: HTTP protocol over Unix socket (`httpx.AsyncHTTPTransport(uds=...)`)

```python
# HTTP over Unix socket pattern (established in codebase)
transport = httpx.AsyncHTTPTransport(uds="/tmp/edge.sock")
client = httpx.AsyncClient(
    transport=transport,
    base_url="http://localhost",  # Host ignored for UDS
)
await client.post("/api/v1/federation/inference", json=body)
```

**Config**:
```yaml
# Master
federation:
  mode: master
  stargate_id: master-localhost
  local_edge:
    socket_path: "/tmp/universal-protocol/edge.sock"
    stargate_id: edge-localhost
    api_key: "${FEDERATION_KEY_EDGE_LOCALHOST}"  # REQUIRED

# Edge
federation:
  mode: edge
  stargate_id: edge-localhost
  allowed_peers:
    - stargate_id: master-localhost
      api_key: "${FEDERATION_KEY_EDGE_LOCALHOST}"
```

### Pattern 2: Master → Remote Relay → Edge

**Use case**: Edge on different physical host, Relay bridges the gap

```
Client → Master (host-A:9999) → Relay (host-B:9999) → Edge (container:9999) → Gateway (container:9998)
                (TCP/internet)          (Unix socket)
```

**Key points**:
- Master on workstation A (orchestrator only)
- Relay on workstation B (host process, TCP 9999)
- Edge in container on workstation B (internal port 9999, network-isolated)
- No port conflict: Master and Relay on different hosts

**Config**:
- Master: `mode: master`, `remotes: [{stargate_id: relay-B, url: http://host-B:9999}]`
- Relay: `mode: remote`, `master: {url: http://host-A:9999}`, `local_edge: {socket_path: /tmp/edge.sock}`
- Edge: `mode: edge`, `allowed_peers: [{stargate_id: relay-B, api_key: $KEY}]`

**Network isolation**: Edge can use `network_mode: none` (Docker), only Unix socket to Relay

### Pattern 3: Multi-Relay Fan-Out

**Use case**: Geographic distribution, multiple isolated environments

```
                Master (host)
                  |
        +---------+---------+
        |         |         |
      Relay1   Relay2   Relay3     (host processes)
        |         |         |
      Edge1    Edge2    Edge3      (containers)
```

**Benefit**: Each Relay aggregates local Edges, reducing Master connections

### Pattern 4: Multi-Hop Relay Chain (Future)

**Use case**: Deep network topology, NAT traversal, multi-datacenter routing

```
Master (host, datacenter A)
    |
    └── Relay1 (host, datacenter A) ─── TCP ───→ Relay2 (host, datacenter B)
                                                    |
                                                    └── Relay3 (host, datacenter B)
                                                            |
                                                            └── Edge (container)
                                                                  └── Gateway
```

**How it works**:
- Relay1 has no `local_edge`, only `remotes: [relay2]`
- Relay2 has no `local_edge`, only `remotes: [relay3]` (intermediate)
- Relay3 has `local_edge` pointing to Edge container (terminal)
- Requests forward through chain until reaching Relay with `local_edge`
- Telemetry flows back upstream through same chain

**Status**: Architecture supports this; not yet implemented. Current deployments use single-hop (Master → Relay → Edge).

### Pattern 5: Single-Node (No Federation)

**Use case**: Development, small deployments

```
Client → Edge → Gateway
         (local HTTP)
```

**Config**:
- Edge: `mode: edge`, no `federation` section (or minimal)
- No Master, no network federation

## Protocol Boundaries

### Federation Protocol (Stargate ↔ Stargate)

**Used for**:
- Master ↔ Remote (WebSocket or HTTP polling)
- Relay ↔ Edge (WebSocket over Unix socket or TCP)
- Master ↔ Edge (Unix socket, when local_edge configured)

**Signals** (telemetry):
- `FederationAuth`, `FederationAuthResult` (auth handshake)
- `FederationPing`, `FederationPong` (keepalive)
- `GatewayResourceUpdate`, `ModelLoaded`, `ModelUnloaded` (telemetry)

**Transport** (dual-protocol over Unix socket):

| Purpose | Protocol | Transport | Implementation |
|---------|----------|-----------|----------------|
| Telemetry | WebSocket | TCP or Unix socket | `websockets` library, `LocalEdgeClient` |
| Requests | HTTP | TCP or Unix socket | `httpx.AsyncHTTPTransport(uds=...)` |

**Key insight**: Unix socket is a transport layer, not a protocol. Both WebSocket and HTTP work over Unix sockets:
- **WebSocket over Unix socket**: `websockets.connect("ws+unix:///path/to/sock")` (telemetry)
- **HTTP over Unix socket**: `httpx.AsyncHTTPTransport(uds="/path/to/sock")` (requests)

### Gateway Protocol (Stargate ↔ Gateway)

**Used for**:
- Edge Stargate ↔ Local Gateway (always local)

**Not used for**:
- Stargate ↔ Stargate connections (federation protocol only)

**Signals**:
- `ModelLoadRequest`, `ModelUnloadRequest` (lifecycle)
- `GatewayResourceUpdate` (telemetry)
- `InferenceRequest` (execution)

## Request Flow

### Inference Request (Typical Path)

```
1. Client → Master:9999/v1/chat/completions
   ↓ DecisionEngine selects Edge
   
2. Master → Relay:9999/api/v1/federation/tokens/count
   ↓ Relay forwards to local Edge
   
3. Edge → Gateway (token count)
   ↓ Returns token count
   
4. Master adjusts max_tokens (Master-side)
   
5. Master → Relay:9999/api/v1/federation/inference
   ↓ Relay forwards to local Edge
   
6. Edge → Gateway (inference)
   ↓ Streams response
   
7. Response flows back: Gateway → Edge → Relay → Master → Client
```

**Key properties**:
- Token counting is **authoritative at Gateway** (not advisory)
- Master makes routing decisions, but Gateway makes execution decisions
- Relay is stateless passthrough (no decisions)

### Telemetry Flow

```
Gateway → Edge Stargate (Gateway protocol)
          ↓ Translates to federation telemetry
          ↓
       Relay Stargate (federation protocol over Unix)
          ↓ Forwards to Master
          ↓
       Master Stargate (aggregates for routing)
```

**Telemetry types**:
- **Push (WebSocket)**: Real-time updates, low-latency
- **Poll (HTTP)**: Master polls Remote/Edge at interval (Golem)

## Authentication

### Master → Remote/Edge

Master authenticates outbound requests using `remotes[].api_key`:
- HTTP header: `X-Federation-Key: {api_key}`
- Validated by `FederationAuthMiddleware` on Remote/Edge

### Remote → Master

Remote authenticates outbound WebSocket using `master.api_key`:
- WebSocket message: `{signal: "FederationAuth", payload: {api_key: ...}}`
- Validated by Master's WebSocket auth handler

### Relay → Edge (Unix Socket)

Relay authenticates to Edge using `local_edge.api_key`:
- WebSocket message over Unix socket (same as Remote → Master)
- Edge validates against `allowed_peers[].api_key`

**CRITICAL**: Relay authenticates as **its own stargate_id** (not Edge's ID)

## Configuration Summary

### Edge (Execution Node)

```yaml
federation:
  mode: edge
  stargate_id: edge-1
  allowed_peers:
    - stargate_id: relay-1
      api_key: "${FEDERATION_KEY_EDGE}"
```

### Remote (Relay Node with Local Edge)

```yaml
federation:
  mode: remote
  stargate_id: relay-1
  master:
    stargate_id: master
    url: "http://master:9999"
    api_key: "${FEDERATION_KEY_RELAY}"
  local_edge:
    socket_path: "/tmp/universal-protocol/edge.sock"
    stargate_id: edge-1
    api_key: "${FEDERATION_KEY_EDGE}"
```

### Master (Orchestrator)

```yaml
federation:
  mode: master
  stargate_id: master
  
  # Option A: Local Edge via Unix socket (Pattern 1)
  local_edge:
    socket_path: "/tmp/universal-protocol/edge.sock"
    stargate_id: edge-localhost
    api_key: "${FEDERATION_KEY_EDGE}"
  
  # Option B: Remote Relays via TCP (Pattern 2)
  remotes:
    - stargate_id: relay-1
      url: "http://relay:9999"
      api_key: "${FEDERATION_KEY_RELAY}"
```

**Note**: Master can have both `local_edge` (Unix socket) and `remotes` (TCP) simultaneously.

### Default Deployment (Both Routes)

The standard localhost deployment uses **both** local Edge and remote Relay+Edge:

```yaml
# Master (host process)
federation:
  mode: master
  stargate_id: master-localhost
  
  # Local Edge container (Unix socket)
  local_edge:
    socket_path: "/tmp/universal-protocol/edge.sock"
    stargate_id: edge-localhost
    api_key: "${FEDERATION_KEY_EDGE_LOCALHOST}"
  
  # Remote Relay + Edge (TCP)
  remotes:
    - stargate_id: relay-jupiter
      url: "http://compute-node.local:9999"
      api_key: "${FEDERATION_KEY_JUPITER}"
```

**Topology**:
```
Master (host:9999)
    ├─ local_edge: → Edge-localhost (container, UDS)
    │   └─ Gateway-localhost (container:9998)
    └─ remotes:
        └─ Relay-jupiter (TCP) → Edge-jupiter (container, UDS)
            └─ Gateway-jupiter (container:9998)
```

**Deploy with**: `./scripts/deploy-gpu-relay.sh restart`

## Network Isolation

### Why Network Isolation?

**Use cases**:
- Security: GPU workers with no network access (`network_mode: none`)
- Compliance: Air-gapped environments

**Note**: Golem Network is NOT network-isolated. Golem providers expose HTTP servers and use HTTP polling (see "Golem Topology" section). Network isolation refers to containers with `network_mode: none` that communicate exclusively via Unix socket.

### How It Works

1. **Edge Stargate**: `network_mode: none` in Docker (no network stack)
2. **Unix Socket Bridge**: Master or Relay connects via shared volume mount
3. **Dual Protocol**: Both WebSocket (telemetry) and HTTP (requests) over Unix socket
4. **Zero Config Change**: Edge doesn't know it's network-isolated

**Unix socket enables full federation**:
- Telemetry push: WebSocket over Unix socket (`LocalEdgeClient`)
- Request forwarding: HTTP over Unix socket (`httpx.AsyncHTTPTransport`)

**Example Docker Compose**:

```yaml
services:
  master:  # or relay
    network_mode: bridge
    volumes:
      - edge-socket:/tmp/universal-protocol
  
  edge:
    network_mode: none  # No network!
    volumes:
      - edge-socket:/tmp/universal-protocol
```

**Socket path**: `/tmp/universal-protocol/edge.sock`

## Design Decisions

### Two Primary Topologies: Relay vs Golem

**Default**: Relay topology (WebSocket push telemetry) is used unless `disable_websocket: true` is set.

**Mutual exclusivity**: Each remote operates in exactly ONE topology mode:
- `disable_websocket: false` (default) → Relay topology
- `disable_websocket: true` → Golem topology

The federation system supports two distinct deployment patterns, each optimized for different network constraints:

#### Relay Topology (VPS/Enterprise) - **DEFAULT**

**Network profile**: Bidirectional TCP connectivity, persistent WebSocket connections allowed

**Default behavior**: All remotes use this topology unless explicitly configured otherwise.

```
┌──────────────┐
│ Master       │ ← Client requests
│ (VPS)        │
└──────┬───────┘
       │ WebSocket (push telemetry)
       ↓
┌──────────────┐
│ Relay        │
│ (VPS)        │
└──────┬───────┘
       │ Unix socket (federation WS)
       ↓
┌──────────────┐
│ Edge         │ network_mode: none
│ (Isolated)   │ (no network stack)
└──────┬───────┘
       │ Gateway WS
       ↓
┌──────────────┐
│ Gateway      │
│ (GPU)        │
└──────────────┘
```

**Characteristics**:
- **Transport**: WebSocket (persistent, bidirectional)
- **Telemetry**: Push-based (Edge → Relay → Master in real-time)
- **Latency**: Low (~10-50ms telemetry updates)
- **Network**: Requires stable TCP connectivity
- **Use case**: VPS providers, enterprise deployments, private infrastructure

**Config pattern** (default, no explicit flag needed):
```yaml
# Master
remotes:
  - stargate_id: relay-jupiter
    url: "http://relay:9999"
    api_key: "${KEY}"
    # disable_websocket: false  # DEFAULT, not needed

# Relay
mode: remote
master:
  url: "http://master:9999"
  # disable_websocket: false  # DEFAULT, not needed
local_edge:
  socket_path: "/tmp/edge.sock"  # Unix socket to isolated Edge
```

**Benefits**:
- Real-time telemetry (no polling overhead)
- Low latency for routing decisions
- Efficient resource utilization

#### Golem Topology (Restrictive Networks) - **EXPLICIT ONLY**

**Network profile**: Outbound HTTP only, no persistent connections, stateless providers

**Explicit configuration required**: Must set `disable_websocket: true` to activate this topology.

```
┌──────────────┐
│ Master       │ ← Client requests
│ (Orchestrator)│
└──────┬───────┘
       │ HTTP polling (Master initiates)
       ↓
┌──────────────┐
│ Remote       │ Outbound HTTP only
│ (Golem)      │ No WebSocket server
└──────┬───────┘
       │ Gateway WS (local only)
       ↓
┌──────────────┐
│ Gateway      │
│ (GPU)        │
└──────────────┘
```

**Characteristics**:
- **Transport**: HTTP polling (Master → Remote)
- **Telemetry**: Pull-based (Master polls at interval, default 5s)
- **Latency**: Higher (~5s telemetry updates)
- **Network**: Outbound HTTP only (no inbound, no WebSocket)
- **Use case**: Golem Network, restrictive firewalls, stateless providers

**Config pattern** (explicit `disable_websocket: true` required):
```yaml
# Master
remotes:
  - stargate_id: golem-provider-1
    url: "http://provider:10999"
    api_key: "${KEY}"
    disable_websocket: true  # REQUIRED for Golem topology
    telemetry_poll_interval_ms: 5000

# Remote (Golem Provider)
mode: remote
master:
  url: "http://requestor:9999"
disable_websocket: true  # REQUIRED for Golem topology
```

**Benefits**:
- Works in restrictive network environments
- No persistent connections (stateless providers)
- Firewall-friendly (outbound HTTP only)

#### Topology Comparison

| Aspect | Relay (DEFAULT) | Golem (EXPLICIT) |
|--------|-----------------|------------------|
| **Activation** | Default (no flag) | `disable_websocket: true` required |
| **Transport** | WebSocket (bidirectional) | HTTP polling (Master-initiated) |
| **Telemetry** | Push (real-time) | Pull (interval-based) |
| **Latency** | Low (10-50ms) | Medium (5s default) |
| **Network** | Bidirectional TCP | Outbound HTTP only |
| **Connections** | Persistent | Stateless |
| **Edge isolation** | Unix socket (network_mode: none) | N/A (Gateway local) |
| **Use case** | VPS, enterprise | Golem, restrictive firewalls |
| **Config flag** | `disable_websocket: false` (default) | `disable_websocket: true` (required) |

#### Hybrid Deployments

**Mixed fleet**: Master can manage both topologies simultaneously (mutually exclusive per-remote)

```
Master
  ├─ Relay1 (WebSocket, push telemetry) ← DEFAULT topology
  │   └─ Edge1 (Unix socket, isolated)
  ├─ Relay2 (WebSocket, push telemetry) ← DEFAULT topology
  │   └─ Edge2 (Unix socket, isolated)
  └─ Golem1 (HTTP polling, pull telemetry) ← EXPLICIT topology
      └─ Gateway (local)
```

**Config** (explicit `disable_websocket` only for Golem):
```yaml
remotes:
  - stargate_id: relay-1
    url: "http://relay1:9999"
    # disable_websocket: false  # DEFAULT, omit
  - stargate_id: relay-2
    url: "http://relay2:9999"
    # disable_websocket: false  # DEFAULT, omit
  - stargate_id: golem-1
    url: "http://golem1:10999"
    disable_websocket: true   # REQUIRED for Golem topology
    telemetry_poll_interval_ms: 5000
```

**Benefit**: Optimize per-provider based on network constraints

#### Golem-Specific Optimizations

**Problem**: Golem providers may have transient availability, high churn

**Solutions implemented**:
1. **Adaptive polling**: Fast polling (5s) during active requests, slower (30s+) when idle
2. **Lean telemetry**: `telemetry_log_level: INFO` (only state changes, no debug spam)
3. **Circuit breaker**: Auto-disable providers with sustained failures
4. **Delta compression**: Only send changed models (not full catalog each poll)

**Config**:
```yaml
remotes:
  - stargate_id: golem-provider-1
    disable_websocket: true
    telemetry_poll_interval_ms: 5000
    telemetry_log_level: INFO  # Lean logging
```

### Why Not Gateway Protocol for Relay → Edge?

**Original problem**: Relay tried to use `GatewayClient` to connect to Edge Stargate
- ❌ Protocol mismatch (Gateway protocol vs Stargate protocol)
- ❌ Edge is a Stargate, not a Gateway

**Solution**: Federation protocol end-to-end
- ✅ Single protocol for all Stargate ↔ Stargate connections
- ✅ Existing auth, telemetry, reconnection semantics
- ✅ Unix socket already supported by federation protocol

### Why Master Has No Gateway?

**Before**: Master could optionally have local Gateway for "sticky" models

**After (2026-01-16)**: Master is pure orchestrator
- ✅ Simpler deployment (no GPU required)
- ✅ Clear separation of control and execution
- ✅ Uniform request path (always routes to Edge)

**If local execution needed**: Run an Edge Stargate on same host as Master

### Why Relay Role, Not "Relay Mode"?

**Canonical principle**: "A Relay is a stateless forwarding role, not a containment boundary"

- Remote mode Stargate can act as Relay (when `local_edge` configured)
- But Remote mode can also be a direct execution node (no `local_edge`)
- "Relay" describes behavior, not mode

## Troubleshooting

### Symptom: Edge rejects Master/Relay auth

**Cause**: Master/Relay authenticating with wrong stargate_id

**Fix**: Ensure `LocalEdgeClient` uses **caller's stargate_id** (from `FederationConfig.stargate_id`), not `local_edge.stargate_id`. Edge validates against `allowed_peers` keyed by caller ID.

### Symptom: Telemetry not reaching Master

**Cause**: Signal name mismatch (e.g., `"MODEL_LOADED"` vs `"ModelLoaded"`)

**Fix**: Always import and use constants from `common/protocol/signals.py`

### Symptom: Master shows no available models

**Cause**: Telemetry freshness threshold exceeded (default 10s)

**Fix**: 
- Check WebSocket connectivity (Master → Remote)
- Verify ping interval ≤ 30s (VPS safe)
- Check `telemetry_stale_threshold_ms` in config

### Symptom: HTTP requests fail to local Edge (Unix socket)

**Cause**: Missing `api_key` in Master's `local_edge` config, or socket path mismatch

**Fix**:
- Verify `local_edge.api_key` is set in Master config
- Verify socket path matches Edge's listening socket
- Check socket file exists: `ls -la /tmp/universal-protocol/edge.sock`
- Verify shared volume mount in Docker Compose

### Anti-pattern: Treating `local_edge.socket_path` as Gateway socket

**WRONG**: `local_edge.socket_path` is the Gateway's Unix socket  
**CORRECT**: `local_edge.socket_path` is the **Edge Stargate's** Unix socket

```
Master → Edge Stargate (socket_path) → Gateway (internal)
         ↑ local_edge.socket_path points here
```

The Edge Stargate exposes federation protocol endpoints (`/api/v1/federation/*`, `/ws/federation/edge`). The Gateway is internal to the Edge container and not directly exposed.

**If you need Gateway socket directly**: Use `gateway.socket_path` in the main stargate config (not `federation.local_edge.socket_path`).

## See Also

- `README_AI.md`: Implementation details, invariants, anti-patterns
- `config/*.yaml`: Configuration examples
