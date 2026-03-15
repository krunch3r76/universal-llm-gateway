# Stargate Federation: Phase 1 Production Architecture

**Document Type:** Implementation Specification  
**Status:** MUST SHIP — Phase 1 Production Bar  
**Date:** 2026-01-06 (v2)  
**Scope:** Network-Isolated Gateway Distribution (Single Master, Static Config)  
**Revision Notes:** v2 adds operational safety (§16), metrics (§17), failure modes (§18), VPS checklist (§19), extended tests (§20)

---

## 1. Executive Summary

**System:** Universal LLM Gateway — distributed inference routing  
**Problem:** Gateways run in network-isolated containers (`network_mode: none`), breaking direct HTTP routing  
**Solution:** Federation protocol where Remote Stargates proxy to isolated Gateways via Unix sockets

**Phase 1 Scope:**
- Single Master Stargate receives all client requests
- Remote Stargates forward requests to local isolated Gateways
- Static configuration (no dynamic discovery)
- Full cancellation propagation
- Secure authentication with TLS enforcement

**Non-Goals (Explicit Exclusions):**
- Public token network
- Dynamic Stargate discovery
- Multi-Master active-active coordination
- Blockchain integration
- Catalog synchronization between Gateways

**Operational Safety (Sections 16-20):**
- Startup assertions and config guards (fail-fast on misconfiguration)
- Visual console warnings for dangerous configurations
- Required metrics for all known failure modes
- Known failure modes with Phase-1-safe mitigations
- VPS transition checklist with pre-cutover validation

---

## 2. System Architecture

### 2.1 Component Definitions

| Component | Role | Port | Description |
|-----------|------|------|-------------|
| **Stargate** | Coordinator/Router | 9999 | Receives client requests, makes routing decisions |
| **Gateway** | Inference Orchestrator | 9998 | Manages model lifecycle, routes to Workers via IPC |
| **Worker** | Inference Engine | N/A | Loads GGUF models, executes inference |

### 2.2 Federation Topology

```
                           ┌──────────────────────────┐
                           │       Client             │
                           └─────────┬────────────────┘
                                     │ HTTPS
                                     ▼
                           ┌──────────────────────────┐
                           │   Master Stargate        │
                           │   mode: MASTER           │
                           │ DecisionEngine / Routing │
                           └─────────┬────────────────┘
                                     │
         ┌───────────────────────────┼───────────────────────────┐
         │ HTTPS                     │ HTTPS                     │ Unix
         ▼                           ▼                           ▼
┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐
│ Remote Stargate │         │ Remote Stargate │         │ Local Gateway   │
│ mode: REMOTE    │         │ mode: REMOTE    │         │ (local to Master)│
│ Forward only    │         │ Forward only    │         │                 │
└─────────┬───────┘         └─────────┬───────┘         └─────────────────┘
          │ Unix                      │ Unix
          ▼                           ▼
┌─────────────────┐         ┌─────────────────┐
│ Local Gateway   │         │ Local Gateway   │
│ (isolated)      │         │ (isolated)      │
└─────────────────┘         └─────────────────┘
```

### 2.3 Stargate Modes

```
mode: S → {MASTER, REMOTE, STANDALONE}

INVARIANT: ∀ s ∈ S: |{MASTER, REMOTE, STANDALONE} ∩ {mode(s)}| = 1
```

| Mode | Behavior |
|------|----------|
| **MASTER** | Receives client requests, aggregates telemetry, makes routing decisions, forwards to local Gateway OR Remote Stargate |
| **REMOTE** | Connects to local Gateway via Unix socket, exposes telemetry to Master, accepts proxied requests, NEVER receives direct client requests |
| **STANDALONE** | Single Stargate, direct Gateway connections, no federation |

### 2.4 Communication Channels

| Channel | Protocol | Transport | Direction |
|---------|----------|-----------|-----------|
| Client → Master | HTTPS | TCP | Request/Response |
| Master → Remote | HTTPS/WSS | TCP | Inference forwarding, telemetry pull |
| Remote → Gateway | HTTP/WS | Unix Socket | Request forwarding, telemetry |
| Gateway → Worker | IPC | Unix Socket | Model operations |

### 2.5 Request Flow

```
1. Client → Master:9999/v1/chat/completions (HTTPS)
2. Master queries telemetry cache (event-driven, from WebSocket)
3. DecisionEngine selects optimal Gateway (T0/T1/T2 tiers)
4. IF local Gateway: Master forwards via Unix socket
   IF remote Gateway: Master forwards to Remote Stargate via HTTPS
5. Remote forwards to local Gateway via Unix socket
6. Gateway routes to Worker, returns response
7. Response propagates back to Client
```

---

## 3. Protocol Specification

### 3.1 Wire Format

**All WebSocket messages use unified `signal`/`payload` structure:**

```json
{
  "signal": "resource_update",
  "payload": {
    "source": {
      "stargate_id": "jupiter",
      "gateway_id": "jupiter/localhost"
    },
    "available_vram_mb": 32000,
    "loaded_models": ["llama-3-8b-8192"]
  },
  "timestamp": "2026-01-06T12:00:00.000Z",
  "id": 42
}
```

**INVARIANT (Canonical Event Structure):**
```
∀ WebSocket message m:
  REQUIRED: m.signal ∈ SIGNAL_CONSTANTS ∧ m.payload: dict
  OPTIONAL: m.timestamp: str, m.id: int
  unknown_signal(m) ⟹ log_and_increment_counter(m)
```

### 3.2 Signal Constants

**Location:** `signals.py` (shared by all handlers)

```python
# Federation signal constants
FEDERATION_INIT = "federation_init"
FEDERATION_AUTH = "federation_auth"
FEDERATION_AUTH_RESULT = "federation_auth_result"
FEDERATION_PING = "federation_ping"
FEDERATION_PONG = "federation_pong"

# Telemetry signal constants (same as Gateway → Stargate)
RESOURCE_UPDATE = "resource_update"
MODEL_LOADED = "model_loaded"
MODEL_UNLOADED = "model_unloaded"
MODEL_BUSY = "model_busy"
MODEL_IDLE = "model_idle"

SIGNAL_CONSTANTS = frozenset([
    FEDERATION_INIT, FEDERATION_AUTH, FEDERATION_AUTH_RESULT,
    FEDERATION_PING, FEDERATION_PONG,
    RESOURCE_UPDATE, MODEL_LOADED, MODEL_UNLOADED,
    MODEL_BUSY, MODEL_IDLE,
])
```

**INVARIANT:** `∀ handler h: h.signal ∈ SIGNAL_CONSTANTS`

### 3.3 Authentication Handshake

**Sequence:**

```
1. Master connects to Remote via WSS
2. Master sends federation_auth (within 5s deadline)
3. Remote validates credentials + protocol version
4. Remote sends federation_auth_result (accepted/rejected)
5. IF rejected: Remote closes connection (code 4001)
6. IF accepted: Telemetry flow begins
```

**Messages:**

```json
// Master → Remote
{
  "signal": "federation_auth",
  "payload": {
    "stargate_id": "earth",
    "api_key": "sk-federation-...",
    "protocol_version": "1.0"
  }
}

// Remote → Master (success)
{
  "signal": "federation_auth_result",
  "payload": {
    "accepted": true,
    "stargate_id": "jupiter",
    "protocol_version": "1.0"
  }
}
```

**INVARIANTS:**
```
∀ connection c:
  ¬authenticated(c, t=5s) ⟹ close(c, code=4003)
  telemetry_flow(c) ⟹ authenticated(c)
  protocol_version(master) ≠ protocol_version(remote) ⟹ close(c, code=4002)
```

### 3.4 Protocol Version Validation

**Version:** `1.0` (Phase 1)

**Rule:** Strict equality. No minor version skew. Fail-fast on mismatch.

```python
PROTOCOL_VERSION = "1.0"

async def validate_version(remote_version: str) -> bool:
    if remote_version != PROTOCOL_VERSION:
        logger.error(f"Protocol mismatch: {PROTOCOL_VERSION} vs {remote_version}")
        return False
    return True
```

### 3.5 Federation WebSocket Endpoint

**Remote exposes:** `wss://remote:9999/ws/federation`

**INVARIANT (WS Guard):**
```
∀ WS_endpoint e in Remote mode:
  e = /ws/federation ∨ rejected(e)
  ∧ /ws/federation requires mode_check ∧ authenticated_peer
```

---

## 4. Request Forwarding

### 4.1 Federation Internal Endpoint

**Endpoint:** `POST /api/v1/federation/inference`

**Purpose:** Separate from client-facing `/v1/chat/completions` to:
- Apply federation-specific authentication
- Use defined response contract
- Enable loop detection via hop counting

### 4.2 Request Format

```json
{
  "request": { /* original client request */ },
  "federation": {
    "source_stargate": "earth",
    "correlation_id": "uuid",
    "hop_count": 1,
    "max_hops": 3
  }
}
```

**Headers:**
```
X-Federation-Source: earth
X-Federation-Key: sk-federation-...
X-Federation-Hop-Count: 1
X-Correlation-Id: uuid
```

### 4.3 Loop Prevention Middleware (MANDATORY)

**INVARIANT:**
```
∀ request r with federation metadata:
  hop_count(r) ≤ max_hops(r)
  ∧ (hop_count(r) = max_hops(r)) ⟹ reject(r, 400, "Hop limit exceeded")
  ∧ forward(r) ⟹ hop_count(r') = hop_count(r) + 1
∀ stargate s: forward(s, r) ⟹ hop_middleware_applied(r)
```

**Implementation:**

```python
MAX_HOPS_DEFAULT = 3
HOP_COUNT_HEADER = "X-Federation-Hop-Count"

async def federation_hop_middleware(request: Request, call_next):
    """Inject, increment, and validate federation hop count."""
    
    hop_count_str = request.headers.get(HOP_COUNT_HEADER, "0")
    try:
        hop_count = int(hop_count_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid hop count header")
    
    max_hops = config.federation.max_hops
    if hop_count >= max_hops:
        raise HTTPException(
            status_code=400, 
            detail=f"Hop limit exceeded: {hop_count} >= {max_hops}"
        )
    
    request.state.federation_hop_count = hop_count + 1
    return await call_next(request)
```

### 4.4 Header Sanitization at Ingress

**INVARIANT:**
```
∀ request r at public_ingress:
  strip_headers(r, X-Federation-*)
  ∧ set(r.hop_count, 0)
```

**Implementation:** Master's public endpoint MUST strip all `X-Federation-*` headers before internal processing.

### 4.5 Response Format

| Scenario | Response | Notes |
|----------|----------|-------|
| Non-streaming success | `{ "status": "ok", "response": {...} }` | Full response body |
| Streaming | SSE passthrough | Remote is transparent proxy |
| Gateway error | `{ "status": "error", "code": "...", "message": "..." }` | Structured error |
| Remote error | HTTP 5xx with error envelope | Federation-layer failure |

### 4.6 SSE Streaming Passthrough

**INVARIANT:**
```
∀ Remote r forwarding SSE:
  ¬full_buffering(r) ∧ ¬parse_content(r) ∧ ¬modify_content(r)
  ∧ upstream_disconnect ⟹ immediate_downstream_cancel
```

**Flow:**
```
Client ←SSE← Master ←SSE← Remote ←SSE← Gateway ←IPC← Worker
```

Remote NEVER parses or modifies streaming content.

---

## 5. Cancellation & Correlation

### 5.1 Cancellation Propagation

**INVARIANT:**
```
∀ request r, client_disconnect(r) ⟹
  cancel_propagates(Master) →
  cancel_propagates(Remote) →
  cancel_propagates(Gateway) →
  cancel_worker(r)
```

### 5.2 Correlation Tracking

**Purpose:** Map client correlation IDs to remote request IDs for cancellation.

**INVARIANT:**
```
∀ active_request r: ∃ mapping(correlation_id → remote_request_id)
ws_disconnect(remote) ⟹ pending_cancellations queued for retry
ws_reconnect(remote) ⟹ pending_cancellations replayed
```

### 5.3 Request States

```python
class RequestState(StrEnum):
    ACTIVE = "active"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    EXPIRED = "expired"
```

**INVARIANT (Idempotence):**
```
∀ request r:
  state(r) ∈ {ACTIVE, CANCELLED, COMPLETED, EXPIRED}
  ∧ transition_to_terminal(r) ⟹ all_subsequent_ops_noop(r)
```

### 5.4 Cancellation Replay Ordering

**INVARIANT:**
```
∀ remote r, on reconnect(r):
  replay_pending_cancellations(r) BEFORE accept_new_requests(r)
  ∧ new_request_acceptance blocked until replay_complete(r)
```

**Why:** Under reconnect storms, if new requests are accepted before pending cancellations are replayed, stale requests may race with cancellation attempts — recreating the exact failure mode cancellation tracking was designed to prevent.

**Implementation:**
```python
async def on_remote_reconnect(self, remote_id: str) -> None:
    """Replay pending cancellations BEFORE accepting new requests."""
    # Block new request acceptance
    self._accepting_requests[remote_id] = False
    
    try:
        pending = self._pending_cancels.pop(remote_id, [])
        for remote_request_id in pending:
            await self._cancel_callback(remote_id, remote_request_id)
    finally:
        # Re-enable request acceptance
        self._accepting_requests[remote_id] = True
```

### 5.5 CorrelatedRequestTracker (E2)

**Location:** `universal_transport/core/correlation/tracker.py`

**INVARIANT:**
```
∀ stateful_tracker: extends(Sequential) ∧ ¬uses(asyncio.Lock)
```

```python
@dataclass
class TrackedRequest:
    correlation_id: str
    remote_id: str
    remote_request_id: str
    started_at: float
    state: RequestState = RequestState.ACTIVE
    retry_count: int = 0

class CorrelatedRequestTracker(Sequential):
    """Lock-free request tracking via Sequential base class.
    
    INVARIANT:
      ¬uses_asyncio_lock(self)
      ∀ terminal_state t: transition_to(t) ⟹ subsequent_ops_noop
    """
    
    def __init__(
        self,
        cancel_callback: Callable[[str, str], Awaitable[bool]],
        id_generator: Callable[[], str],
        ttl_seconds: float = 300.0,
        max_retry: int = 3,
    ):
        super().__init__()
        self._cancel_callback = cancel_callback
        self._id_generator = id_generator
        self._ttl = ttl_seconds
        self._max_retry = max_retry
        self._active: dict[str, TrackedRequest] = {}
        self._pending_cancels: dict[str, list[str]] = {}
    
    async def register(self, remote_id: str) -> tuple[str, str]:
        """Register request, return (correlation_id, remote_request_id)."""
        correlation_id = self._id_generator()
        remote_request_id = self._id_generator()
        
        self._active[correlation_id] = TrackedRequest(
            correlation_id=correlation_id,
            remote_id=remote_id,
            remote_request_id=remote_request_id,
            started_at=time.time(),
        )
        return correlation_id, remote_request_id
    
    async def complete(self, correlation_id: str) -> None:
        """Mark completed — idempotent."""
        tracked = self._active.get(correlation_id)
        if not tracked or tracked.state != RequestState.ACTIVE:
            return
        tracked.state = RequestState.COMPLETED
        self._active.pop(correlation_id, None)
    
    async def cancel(self, correlation_id: str) -> bool:
        """Cancel request — idempotent, queues retry on failure."""
        tracked = self._active.get(correlation_id)
        if not tracked:
            return True
        if tracked.state != RequestState.ACTIVE:
            return True
        
        try:
            success = await self._cancel_callback(
                tracked.remote_id, tracked.remote_request_id
            )
            if success:
                tracked.state = RequestState.CANCELLED
                self._active.pop(correlation_id, None)
            return success
        except Exception:
            if tracked.remote_id not in self._pending_cancels:
                self._pending_cancels[tracked.remote_id] = []
            self._pending_cancels[tracked.remote_id].append(
                tracked.remote_request_id
            )
            return False
    
    async def on_remote_reconnect(self, remote_id: str) -> None:
        """Retry pending cancellations on reconnect."""
        pending = self._pending_cancels.pop(remote_id, [])
        for remote_request_id in pending:
            # Find and retry (see full spec for implementation)
            pass
```

### 5.6 Cancel Endpoint

**Endpoint:** `DELETE /api/v1/federation/inference/{remote_request_id}`

**INVARIANT:**
```
∀ cancel request: requires_federation_auth ∧ rate_limited
remote_request_id ∉ client_responses
```

**Remote Implementation:**
```python
@app.delete("/api/v1/federation/inference/{remote_request_id}")
async def cancel_federated_request(remote_request_id: str):
    ctx = remote_active_requests.get(remote_request_id)
    if not ctx:
        return {"status": "not_found"}
    await gateway_client.cancel(ctx.gateway_correlation_id)
    return {"status": "cancelled"}
```

---

## 6. Security Model

### 6.1 TLS Enforcement

**INVARIANT:**
```
require_tls = true ⟹ ∀ connection c: is_tls(c) ∨ reject(c)
∀ TLS connection c: valid_ca_chain(c) ∧ hostname_verified(c) ∨ reject(c)
```

**Configuration:**
```yaml
federation:
  require_tls: true
  tls:
    cert_file: "/etc/stargate/certs/stargate.crt"
    key_file: "/etc/stargate/certs/stargate.key"
    ca_file: "/etc/stargate/certs/ca.crt"
```

### 6.2 Identity Binding

**INVARIANT:**
```
∀ federation_connection c:
  authenticated_peer(c) = config.remotes[c.socket].stargate_id
  ∧ payload.source.stargate_id ≠ authenticated_peer(c) ⟹ reject(c)
```

**Implementation:** Map `stargate_id` to authenticated socket. Reject any payload where `source.stargate_id` differs from the authenticated peer.

### 6.3 API Key Authentication

**Headers:**
```
X-Federation-Source: <stargate_id>
X-Federation-Key: <api_key>
```

**Validation:**
```python
import hmac

def verify_federation_key(provided: str, expected: str) -> bool:
    """Constant-time comparison."""
    return hmac.compare_digest(provided.encode(), expected.encode())
```

**INVARIANT:**
```
∀ request r to /api/v1/federation/*:
  authenticated(r) ⟺ 
    header(r, "X-Federation-Source") ∈ allowed_masters
    ∧ verify_federation_key(header(r, "X-Federation-Key"), expected_key)
```

### 6.4 API Key Redaction

**INVARIANT:**
```
∀ log_entry l, ∀ secret s ∈ {api_key, X-Federation-Key}:
  s ∉ content(l) ∧ s ∉ structured_fields(l)
```

Add `X-Federation-Key` to `universal_logging` redaction list.

### 6.5 Connection Limits

**INVARIANT:**
```
∀ peer p:
  |unauthenticated_connections(p)| ≤ max_unauthenticated_per_ip
  |federation_connections(p)| ≤ max_federation_per_peer
```

**Configuration:**
```yaml
federation:
  connection_limits:
    max_unauthenticated_per_ip: 5
    max_federation_per_peer: 10
    auth_deadline_seconds: 5
```

### 6.6 Remote Mode Endpoint Restrictions

**INVARIANT:**
```
∀ request r on Remote mode:
  path(r) ∈ REMOTE_MODE_ALLOWED_PREFIXES ∨ rejected(r)
```

**Declarative Allow-List:**
```python
REMOTE_MODE_ALLOWED_PREFIXES = frozenset([
    "/api/v1/federation/",
    "/health",
    "/healthz",
    "/metrics",
    "/ws/federation",
])

FEDERATION_AUTH_REQUIRED = frozenset([
    "/api/v1/federation/inference",
    "/ws/federation",
])

@app.middleware("http")
async def federation_guard(request: Request, call_next):
    if config.federation.mode != "remote":
        return await call_next(request)
    
    path = request.url.path
    is_allowed = any(
        path.startswith(prefix) 
        for prefix in REMOTE_MODE_ALLOWED_PREFIXES
    )
    
    if not is_allowed:
        return JSONResponse(
            status_code=403,
            content={"error": "Endpoint disabled in remote mode"}
        )
    
    return await call_next(request)
```

### 6.7 Message Size Limits

**INVARIANT:**
```
∀ telemetry_frame f:
  size(f) ≤ MAX_TELEMETRY_FRAME_SIZE (1MB)
  ∧ |f.payload.loaded_models| ≤ MAX_MODELS_PER_GATEWAY (100)
```

---

## 7. Telemetry & State Management

### 7.1 Telemetry Signals

| Signal | Direction | Payload | Purpose |
|--------|-----------|---------|---------|
| `resource_update` | Remote → Master | source, vram, ram, models, active_requests | Resource state |
| `model_loaded` | Remote → Master | source, model_id, vram_used, ram_used | Model lifecycle |
| `model_unloaded` | Remote → Master | source, model_id | Model lifecycle |
| `model_busy` | Remote → Master | source, model_id | Inference active |
| `model_idle` | Remote → Master | source, model_id | Inference complete |
| `federation_ping` | Master → Remote | {} | Keepalive |
| `federation_pong` | Remote → Master | {} | Keepalive response |

### 7.2 Telemetry Freshness

**INVARIANT:**
```
freshness(t) = local_receipt_time(t) - now()
remote_timestamp(t) is informational_only

stale(s) ⟺ (now - last_message(s)) > stale_threshold_ms
unreachable(s) ⟺ (now - last_message(s)) > unreachable_threshold_ms

stale(s) ⟹ staleness_penalty(s) in scoring
unreachable(s) ⟹ tier(all gateways under s) = T0
```

**Configuration:**
```yaml
federation:
  telemetry_stale_threshold_ms: 5000
  telemetry_unreachable_threshold_ms: 10000
```

### 7.3 Snapshot Authority

**INVARIANT:**
```
∀ resource_update u:
  is_complete_snapshot(u.loaded_models, u.busy_models, u.active_requests)
  ∧ receiver_treats_as_authoritative(u)
```

`resource_update` is the complete current state. Receiver MUST replace, not merge.

### 7.4 Routing Snapshot Consistency

**INVARIANT:**
```
∀ routing_decision d:
  ∃ snapshot s: d computed against s
  ∧ s is coherent (single point-in-time view)
  ∧ partial_telemetry_application MUST NOT affect active routing
```

**Why:** Under burst telemetry updates, routing must observe a consistent state. Half-applied telemetry creates impossible routing decisions (e.g., model appears loaded but capacity not yet updated).

**Implementation Pattern:**
```python
async def collect_gateways() -> list[Gateway]:
    """Collect coherent snapshot for routing decision."""
    # Snapshot is taken atomically — no awaits between reads
    return [
        build_gateway_snapshot(gw)
        for gw in federated_gateway_manager.get_all_gateways()
    ]
```

Telemetry updates MUST complete atomically before next `collect_gateways()` call observes them.

### 7.5 ModelId Parsing

**INVARIANT:**
```
∀ model_id operation: uses ModelId objects ∧ ¬string_manipulation
∀ wire message: model_id as string, parsed to ModelId at reception
∀ telemetry payload: parse_telemetry_payload() called before business logic
```

```python
from model_id import ModelId

def parse_telemetry_payload(signal: str, payload: dict) -> dict:
    """Parse all model ID fields at reception boundary."""
    parsed = payload.copy()
    
    if "model_id" in parsed:
        parsed["model_id"] = ModelId.parse(parsed["model_id"])
    
    for field in ("loaded_models", "busy_models", "available_models"):
        if field in parsed and isinstance(parsed[field], list):
            parsed[field] = frozenset(
                ModelId.parse(m) for m in parsed[field]
            )
    
    return parsed
```

### 7.6 Telemetry Backpressure

**INVARIANT:**
```
∀ remote r:
  |queue(r)| ≤ max_queue_size
  ∧ rate(r) ≤ max_events_per_second
```

**Configuration:**
```yaml
federation:
  telemetry_backpressure:
    max_queue_per_remote: 100
    max_events_per_second: 50
    overflow_policy: "drop_oldest"
```

### 7.7 RateLimitedEventSource (E3)

**Location:** `universal_event_bus/backpressure/rate_limited_source.py`

```python
class OverflowPolicy(StrEnum):
    DROP_OLDEST = "drop_oldest"
    DROP_NEWEST = "drop_newest"
    BACKPRESSURE = "backpressure"
    LOG_AND_DROP = "log_and_drop"

@dataclass
class RateLimitConfig:
    max_queue_size: int = 100
    max_events_per_second: float = 50.0
    overflow_policy: OverflowPolicy = OverflowPolicy.DROP_OLDEST

class RateLimitedEventSource:
    """Per-source rate limiting with bounded queue."""
    
    def __init__(self, source_id: str, config: RateLimitConfig):
        self._source_id = source_id
        self._config = config
        self._queue = asyncio.Queue(maxsize=config.max_queue_size)
        self._rate_limiter = TokenBucket(config.max_events_per_second)
    
    async def enqueue(self, event: dict) -> bool:
        """Enqueue with rate limiting. Returns True if queued."""
        if not self._rate_limiter.try_acquire():
            return False
        
        if self._queue.full():
            if self._config.overflow_policy == OverflowPolicy.DROP_OLDEST:
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            else:
                return False
        
        try:
            self._queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            return False
```

### 7.8 Subscriber Pattern

**INVARIANT:**
```
∀ federation_component f:
  ¬calls(f, EventBus.unsubscribe)
  ∧ connection_lifecycle ∈ local_registry_state
```

**Pattern:** Single long-lived subscriber per event type → connection registry fan-out.

```python
class FederationEventForwarder:
    """Forward events to connected Remotes via registry, not unsubscribe."""
    
    def __init__(self):
        self._connections: dict[str, WebSocket] = {}
        self._subscribed = False
    
    async def register_connection(self, remote_id: str, ws: WebSocket):
        self._connections[remote_id] = ws
        if not self._subscribed:
            event_bus.subscribe("resource_update", self._forward)
            self._subscribed = True
    
    async def unregister_connection(self, remote_id: str):
        self._connections.pop(remote_id, None)
        # DO NOT call event_bus.unsubscribe()
    
    async def _forward(self, event: Event):
        for remote_id, ws in list(self._connections.items()):
            try:
                await ws.send_json(event.to_dict())
            except Exception:
                await self.unregister_connection(remote_id)
```

### 7.9 Bounded Send Queue

**INVARIANT:**
```
∀ federation_forwarder f:
  single_sender_task(f) ∧ bounded_queue(f)
  ∧ sustained_backpressure(f) ⟹ drop_and_disconnect(f)
```

---

## 8. Routing Integration

### 8.1 FederatedGateway Instance

```python
@dataclass
class FederatedGateway:
    """Gateway instance that routes via Remote Stargate.
    
    This is the INSTANCE (behavior), not the Snapshot (data).
    Gets wrapped in a Gateway snapshot by collect_gateways().
    """
    
    gateway_id: str
    remote_stargate_id: str
    remote_stargate_url: str
    
    ram_free_mb: int = 0
    vram_free_mb: int = 0
    ram_total_mb: int = 0
    vram_total_mb: int = 0
    
    loaded_models: frozenset[ModelId] = field(default_factory=frozenset)
    busy_models: frozenset[ModelId] = field(default_factory=frozenset)
    loading_models: frozenset[ModelId] = field(default_factory=frozenset)
    available_models: frozenset[ModelId] = field(default_factory=frozenset)
    
    active_requests: int = 0
    telemetry_timestamp: float = 0.0
    last_heartbeat: float = 0.0
    
    _http_client: Any = field(default=None, repr=False)
    
    @property
    def telemetry_age_ms(self) -> int:
        return int((time.time() - self.telemetry_timestamp) * 1000)
    
    async def forward_request(self, request: dict) -> Response:
        return await self._http_client.post(
            f"{self.remote_stargate_url}/api/v1/federation/inference",
            json=request,
            headers=self._federation_headers()
        )
```

### 8.2 FederatedGatewayManager

```python
class FederatedGatewayManager(Sequential):
    """Lock-free state management via Sequential base."""
    
    def __init__(self):
        super().__init__()
        self._gateways: dict[str, FederatedGateway] = {}
    
    async def update_from_event(self, event: Event) -> None:
        source = event.payload["source"]
        gateway_id = source["gateway_id"]
        
        payload = parse_telemetry_payload(event.signal, event.payload)
        
        if gateway_id not in self._gateways:
            self._gateways[gateway_id] = FederatedGateway(
                gateway_id=gateway_id,
                remote_stargate_id=source["stargate_id"],
                ...
            )
        
        gw = self._gateways[gateway_id]
        gw.loaded_models = payload.get("loaded_models", frozenset())
        gw.busy_models = payload.get("busy_models", frozenset())
        gw.active_requests = payload.get("active_requests", 0)
        gw.telemetry_timestamp = time.time()
```

### 8.3 Gateway Collection

```python
async def collect_gateways() -> list[Gateway]:
    gateways = []
    
    # Local gateways (existing)
    for instance in local_gateway_manager.get_all():
        gateways.append(build_gateway_snapshot(instance))
    
    # Federated gateways
    for fgw in federated_gateway_manager.get_all_gateways():
        snapshot = Gateway(
            ref=fgw,  # Router calls ref.forward_request()
            name=fgw.gateway_id,
            ram_free_mb=fgw.ram_free_mb,
            vram_free_mb=fgw.vram_free_mb,
            loaded_models=fgw.loaded_models,
            busy_models=fgw.busy_models,
            active_requests=fgw.active_requests,
            telemetry_timestamp=fgw.telemetry_timestamp,
            ...
        )
        gateways.append(snapshot)
    
    return gateways
```

### 8.4 Capacity-Aware Routing

**INVARIANT:**
```
∀ gateway, placement:
  (is_loaded(gateway, model) ∧ has_capacity(gateway)) ⟹ tier = T1
  (is_loaded(gateway, model) ∧ ¬has_capacity(gateway)) ⟹ tier = T0
```

### 8.5 Pre-Route Token Estimation

**INVARIANT:**
```
∀ request r:
  estimated_tokens(r) > max_context ⟹ reject(r, 400) BEFORE routing
  ∧ exact_count(r) computed AFTER model_load
```

Master applies heuristic token estimate before routing. Hard-reject if estimate exceeds maximum context.

---

## 9. FederationWebSocketClient (E1)

**Location:** `universal_protocol/federation/ws_client.py`

**CRITICAL:** Does NOT extend `ResilientStateChannel` (incompatible wire format).

### 9.1 Send-Path Backpressure

**INVARIANT:**
```
∀ FederationWebSocketClient f:
  outbound_messages(f) MUST be enqueued into bounded_queue(f)
  ∧ ∃! sender_task(f): dequeues ∧ sends
  ∧ sustained_queue_overflow(f) ⟹ disconnect(f) ∧ schedule_reconnect(f)
  ∧ ¬inline_await_send(f)
```

**Why:** Inline `await ws.send()` reintroduces head-of-line blocking. All sends MUST go through the bounded queue with a dedicated sender task.

### 9.2 Implementation

```python
@dataclass
class FederationWebSocketClient:
    """Federation WebSocket client with explicit signal/payload protocol.
    
    INVARIANT:
      wire_format = {signal: str, payload: dict}
      ∧ ¬extends(ResilientStateChannel)
    """
    
    remote_id: str
    ws_url: str
    api_key: str
    local_stargate_id: str
    on_telemetry: Callable[[str, str, dict], Awaitable[None]]
    on_auth_failed: Callable[[str, str], Awaitable[None]] | None = None
    
    reconnect_delay: float = 1.0
    max_reconnect_delay: float = 60.0
    max_send_queue: int = 100
    
    _ws: Any = field(default=None, init=False, repr=False)
    _authenticated: bool = field(default=False, init=False)
    _running: bool = field(default=False, init=False)
    
    async def connect(self) -> None:
        """Connect with auto-reconnect."""
        self._running = True
        current_delay = self.reconnect_delay
        
        while self._running:
            try:
                self._ws = await websockets.connect(self.ws_url)
                current_delay = self.reconnect_delay
                
                if not await self._authenticate():
                    await self._ws.close()
                    if self.on_auth_failed:
                        await self.on_auth_failed(self.remote_id, "Auth failed")
                    return
                
                await self._receive_loop()
                
            except Exception as e:
                logger.warning(f"Connection to {self.remote_id} failed: {e}")
            
            if self._running:
                await asyncio.sleep(current_delay)
                current_delay = min(current_delay * 2, self.max_reconnect_delay)
    
    async def _authenticate(self) -> bool:
        """Perform federation auth handshake."""
        auth_msg = {
            "signal": FEDERATION_AUTH,
            "payload": {
                "stargate_id": self.local_stargate_id,
                "api_key": self.api_key,
                "protocol_version": PROTOCOL_VERSION,
            }
        }
        await self._ws.send(json.dumps(auth_msg))
        
        try:
            response = await asyncio.wait_for(self._ws.recv(), timeout=5.0)
            msg = json.loads(response)
            
            if msg.get("signal") != FEDERATION_AUTH_RESULT:
                return False
            
            payload = msg.get("payload", {})
            if not payload.get("accepted"):
                return False
            
            if payload.get("protocol_version") != PROTOCOL_VERSION:
                return False
            
            self._authenticated = True
            return True
            
        except asyncio.TimeoutError:
            return False
    
    async def _receive_loop(self) -> None:
        """Receive and dispatch telemetry."""
        while self._running and self._ws:
            try:
                raw = await self._ws.recv()
                msg = json.loads(raw)
                signal = msg.get("signal")
                payload = msg.get("payload", {})
                
                if signal == FEDERATION_PONG:
                    continue
                
                await self.on_telemetry(self.remote_id, signal, payload)
                
            except Exception:
                break
    
    async def disconnect(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()
```

---

## 10. Health Checks

**Endpoint:** `GET /healthz`

```json
{
  "status": "healthy",
  "mode": "remote",
  "stargate_id": "jupiter",
  "last_gateway_telemetry_ms": 1245,
  "federation": {
    "connected_to_master": true
  },
  "uptime_s": 86400
}
```

**Status Logic:**

```python
def get_health_status() -> tuple[str, int]:
    if config.federation.mode == "remote":
        gateway_silence_ms = time.time() * 1000 - last_gateway_message_ms
        if gateway_silence_ms > config.federation.telemetry_unreachable_threshold_ms:
            return "unhealthy", 503
        return "healthy", 200
    
    elif config.federation.mode == "master":
        reachable_gateways = [g for g in all_gateways if not is_unreachable(g)]
        if not reachable_gateways:
            return "degraded", 200
        return "healthy", 200
    
    return "healthy", 200
```

---

## 11. Configuration Schema

### 11.1 Master Configuration

```yaml
federation:
  mode: "master"
  stargate_id: "earth"
  
  remotes:
    - stargate_id: "jupiter"
      url: "https://jupiter:9999"
      api_key: "${FEDERATION_KEY_JUPITER}"
    - stargate_id: "saturn"
      url: "https://saturn:9999"
      api_key: "${FEDERATION_KEY_SATURN}"
  
  protocol_version: "1.0"
  max_hops: 3
  
  telemetry_stale_threshold_ms: 5000
  telemetry_unreachable_threshold_ms: 10000
  
  reconnect_interval_ms: 1000
  max_reconnect_attempts: 10
  health_check_interval_ms: 5000
  
  require_tls: true
  tls:
    cert_file: "/etc/stargate/certs/stargate.crt"
    key_file: "/etc/stargate/certs/stargate.key"
    ca_file: "/etc/stargate/certs/ca.crt"
  
  connection_limits:
    max_unauthenticated_per_ip: 5
    max_federation_per_peer: 10
    auth_deadline_seconds: 5
  
  http_pool:
    max_connections: 100
    max_keepalive_connections: 20
    max_connections_per_remote: 20
  
  telemetry_backpressure:
    max_queue_per_remote: 100
    max_events_per_second: 50
    overflow_policy: "drop_oldest"
```

### 11.2 Remote Configuration

```yaml
federation:
  mode: "remote"
  stargate_id: "jupiter"
  
  allowed_masters:
    - stargate_id: "earth"
      api_key: "${FEDERATION_KEY_FROM_EARTH}"
  
  local_gateway:
    socket_path: "/tmp/gateway.sock"
    gateway_id: "jupiter/localhost"
  
  protocol_version: "1.0"
  max_hops: 3
  telemetry_unreachable_threshold_ms: 10000
```

### 11.3 Environment Overrides

| Config Path | Environment Variable | Default |
|-------------|---------------------|---------|
| `federation.mode` | `FEDERATION_MODE` | `standalone` |
| `federation.stargate_id` | `FEDERATION_STARGATE_ID` | hostname |
| `federation.max_hops` | `FEDERATION_MAX_HOPS` | `3` |
| `federation.require_tls` | `FEDERATION_REQUIRE_TLS` | `true` |

---

## 12. Global Identity

### 12.1 Identifier Structure

```
stargate_id := hostname | configured_name
gateway_id  := "{stargate_id}/{local_gateway_name}"

Examples:
  stargate_id: "jupiter"
  gateway_id:  "jupiter/localhost"
```

**INVARIANT:**
```
∀ stargate_id: unique(stargate_id) across federation
∀ gateway_id: unique(gateway_id) across federation
∀ g ∈ G, ∃! s ∈ S: local(g) = s
```

### 12.2 Identity Collision Rejection

**INVARIANT:**
```
∀ federation_connection c:
  authenticated_peer(c).stargate_id ∈ active_connections
  ⟹ reject(c) ∧ log_security_event ∧ increment_collision_counter
```

**Collision Handling Policy (Phase 1):**
- First authenticated connection wins
- Subsequent connections with same `stargate_id` are rejected (code 4004)
- No hot-replacement — operator must disconnect stale peer manually
- Security event logged with both source IPs for audit

**Implementation:**
```python
async def on_federation_auth(ws: WebSocket, payload: dict) -> bool:
    stargate_id = payload["stargate_id"]
    
    if stargate_id in active_federation_connections:
        logger.security(
            "Identity collision rejected",
            extra={
                "stargate_id": stargate_id,
                "existing_peer": active_federation_connections[stargate_id].remote_ip,
                "rejected_peer": ws.client.host,
            }
        )
        metrics.increment("federation.identity_collision")
        await ws.close(code=4004, reason="Identity already connected")
        return False
    
    active_federation_connections[stargate_id] = ConnectionState(ws)
    return True
```

---

## 13. Structured Logging

**Required Fields:**

```python
logger.info(
    "Forwarding request to Remote",
    extra={
        "federation_hop": 1,
        "correlation_id": correlation_id,
        "source_stargate": "earth",
        "target_stargate": "jupiter",
        "target_gateway": "jupiter/localhost",
        "model_id": str(model_id),
        "request_type": "inference",
        "latency_ms": 12,
    }
)
```

**Trace Header Propagation:**
```
X-Correlation-Id
X-Federation-Hop-Count
X-Federation-Source
X-Trace-Id
```

---

## 14. Testing Obligations

### 14.1 Required Tests

| Test | Purpose |
|------|---------|
| **SSE Passthrough** | Prove no buffering; upstream disconnect → immediate downstream cancel |
| **Cancellation Chain** | Client disconnect propagates through all hops |
| **Auth Deadline** | Connections close after 5s without auth |
| **Hop Limit** | Requests rejected at max_hops |
| **Header Stripping** | X-Federation-* stripped at ingress |
| **Identity Binding** | Payload stargate_id mismatch rejected |
| **Identity Collision** | Second connection with same stargate_id rejected (code 4004) |
| **TLS Verification** | Invalid certs rejected |
| **Size Caps** | Messages > 1MB rejected |
| **Rate Limiting** | Excess telemetry dropped (not queued) |
| **Snapshot Authority** | resource_update replaces state, doesn't merge |
| **Snapshot Consistency** | Routing sees coherent state, not half-applied telemetry |
| **ModelId Parsing** | All signals correctly parse ModelId fields |
| **Idempotent Cancel** | Cancel on completed request is no-op |
| **Reconnect Retry** | Pending cancellations replayed on reconnect |
| **Reconnect Order** | Cancellation replay completes before new requests accepted |

### 14.2 CI Requirements

- All tests must pass before merge
- Coverage of all signal types in telemetry parsing
- Integration test for full Master → Remote → Gateway flow

---

## 15. Lib Dependencies

| Lib | Usage |
|-----|-------|
| `universal_protocol` | `generate_request_id()`, `error_envelope()` |
| `universal_transport` | `create_unix_client()` |
| `universal_event_bus` | `Sequential`, `Event` |
| `model_id` | `ModelId.parse()` |
| `universal_logging` | Structured logging with context |

---

## 16. Operational Safety

### 16.1 Startup Assertions (MANDATORY)

**INVARIANT:**
```
∀ startup s:
  assertions_pass(s) ∨ fatal_exit(s)
  ∧ ¬soft_warnings_for_critical_config
```

**Required Startup Checks:**

| Check | Condition | Action |
|-------|-----------|--------|
| **Gateway Socket** | Remote mode + socket_path not connectable | FATAL: "Gateway socket {path} unreachable" |
| **TLS in Production** | `require_tls: false` AND `env=prod` AND NOT `I_UNDERSTAND_INSECURE=true` | FATAL: "TLS disabled in production" |
| **CA File Exists** | `require_tls: true` AND ca_file not readable | FATAL: "CA file {path} not found" |
| **Hostname Verification** | Remote URL is IP without SAN coverage | WARN: "Remote URL is IP; ensure cert SAN includes it" |
| **Ping Interval** | `ping_interval_ms` > typical idle timeout (30s) | FATAL: "Ping interval too high for VPS; set < 30s" |
| **Protocol Version** | Local version undefined or empty | FATAL: "Protocol version not configured" |
| **Pool Size** | `max_connections_per_remote` < 5 | WARN: "HTTP pool may starve under concurrent streams" |

**Implementation:**

```python
def validate_federation_config(config: FederationConfig) -> None:
    """Fail-fast on misconfiguration. Called at startup."""
    
    # Gateway socket (Remote mode)
    if config.mode == "remote":
        sock_path = config.local_gateway.socket_path
        if not Path(sock_path).exists():
            raise ConfigurationError(f"Gateway socket {sock_path} not found")
        # Attempt connect
        try:
            test_socket_connectivity(sock_path)
        except Exception as e:
            raise ConfigurationError(f"Gateway socket {sock_path} unreachable: {e}")
    
    # TLS enforcement
    if not config.require_tls:
        if os.environ.get("ENV") == "prod":
            if not os.environ.get("I_UNDERSTAND_INSECURE"):
                raise ConfigurationError(
                    "TLS disabled in production. Set I_UNDERSTAND_INSECURE=true to override."
                )
        logger.warning(
            "⚠️  TLS DISABLED — API keys transmitted in plaintext. "
            "Only use on fully isolated networks."
        )
    
    # CA file
    if config.require_tls and config.tls.ca_file:
        if not Path(config.tls.ca_file).is_file():
            raise ConfigurationError(f"CA file not found: {config.tls.ca_file}")
    
    # Ping interval sanity
    if config.ping_interval_ms > 30_000:
        raise ConfigurationError(
            f"Ping interval {config.ping_interval_ms}ms too high; "
            "WS will drop on VPS. Set < 30000ms."
        )
```

### 16.2 Visual Console Warnings

**Purpose:** Human-error mitigation for dangerous configurations.

**Required Warnings:**

| Condition | Console Output |
|-----------|----------------|
| TLS disabled | Red banner: `⚠️  TLS DISABLED — credentials in plaintext` |
| Hostname is IP | Yellow banner: `⚠️  Remote URL is IP address; verify cert SAN` |
| Protocol mismatch on connect | Single loud log: `FATAL: Protocol mismatch {local} vs {remote}` |
| Degraded health | Yellow banner: `⚠️  DEGRADED: No reachable gateways` |

**Implementation:**

```python
def log_startup_banner(config: FederationConfig) -> None:
    """Emit visual warnings for operator attention."""
    
    if not config.require_tls:
        logger.warning("\n" + "=" * 60)
        logger.warning("⚠️  TLS DISABLED — API keys transmitted in PLAINTEXT")
        logger.warning("    Only use on fully isolated networks.")
        logger.warning("=" * 60 + "\n")
    
    for remote in config.remotes:
        parsed = urlparse(remote.url)
        if _is_ip_address(parsed.hostname):
            logger.warning(
                f"⚠️  Remote '{remote.stargate_id}' URL is IP address. "
                "Ensure TLS certificate SAN includes this IP."
            )
```

### 16.3 Config Guards

**INVARIANT:**
```
∀ dangerous_config c:
  ∃ guard g: validates(g, c) ∧ (invalid(c) ⟹ fatal ∨ loud_warning)
```

**Guards:**

| Config | Guard | Behavior |
|--------|-------|----------|
| `require_tls: false` | Prod environment check | FATAL unless `I_UNDERSTAND_INSECURE=true` |
| `max_hops: > 10` | Sanity limit | WARN: "High hop limit increases loop risk" |
| `auth_deadline_seconds: < 2` | Minimum threshold | WARN: "Auth deadline may be too aggressive" |
| `telemetry_unreachable_threshold_ms: < 5000` | GC pause buffer | WARN: "Threshold may cause false unreachable" |

---

## 17. Metrics & Observability

### 17.1 Required Metrics (MANDATORY)

**INVARIANT:**
```
∀ failure_mode f: ∃ metric m: detects(m, f)
∀ metric m: labeled_by(m, {remote_id, stargate_id}) where applicable
```

#### Connection & Lifecycle

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `federation_ws_connected` | Gauge | `remote_id` | Current WS connection state |
| `federation_ws_reconnect_total` | Counter | `remote_id`, `reason` | Reconnection frequency |
| `federation_identity_collision_total` | Counter | `stargate_id` | Duplicate identity attempts |
| `federation_auth_failure_total` | Counter | `remote_id`, `reason` | Auth failures by cause |
| `federation_protocol_mismatch_total` | Counter | `local_version`, `remote_version` | Version mismatches |

#### Telemetry & State

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `federation_telemetry_received_total` | Counter | `remote_id`, `signal` | Telemetry by type |
| `federation_telemetry_dropped_total` | Counter | `remote_id`, `reason` | DROP_OLDEST events |
| `federation_telemetry_queue_depth` | Gauge | `remote_id` | Current queue size |
| `federation_telemetry_queue_bytes` | Gauge | `remote_id` | Estimated queue memory |
| `time_since_last_resource_update_ms` | Gauge | `remote_id` | Snapshot staleness |
| `federation_unreachable_flap_total` | Counter | `remote_id` | False unreachable events |

#### Request Handling

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `federation_request_total` | Counter | `remote_id`, `status` | Forwarded requests |
| `federation_request_latency_ms` | Histogram | `remote_id` | E2E latency |
| `federation_ttft_ms` | Histogram | `remote_id` | Time to first token (streaming) |
| `federation_max_inter_chunk_gap_ms` | Histogram | `remote_id` | Streaming stall detection |
| `tracked_requests_active` | Gauge | `remote_id` | Active request count |
| `tracked_requests_ttl_expired_total` | Counter | `remote_id` | Leaked request tracking |

#### Cancellation

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `cancel_requested_total` | Counter | `remote_id` | Cancel attempts |
| `cancel_delivered_total` | Counter | `remote_id` | Cancel reached Remote |
| `cancel_acked_total` | Counter | `remote_id` | Cancel confirmed by Worker |
| `cancel_failed_total` | Counter | `remote_id`, `reason` | Cancel failures |
| `cancel_without_mapping_total` | Counter | `remote_id` | Race condition hits |
| `pending_cancels_queue_depth` | Gauge | `remote_id` | Awaiting reconnect |
| `cancel_replay_duration_ms` | Histogram | `remote_id` | Reconnect replay time |

#### HTTP & Pool

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `federation_http_pool_in_flight` | Gauge | `remote_id` | Active connections |
| `federation_http_pool_wait_ms` | Histogram | `remote_id` | Pool wait time |
| `federation_health_check_latency_ms` | Histogram | `remote_id` | Control plane latency |
| `federation_send_queue_depth` | Gauge | `remote_id` | WS outbound queue |

#### Security

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `federation_tls_disabled_connections_total` | Counter | `remote_id` | Plaintext connections |
| `federation_headers_stripped_total` | Counter | — | Ingress sanitization |
| `federation_identity_mismatch_total` | Counter | `expected`, `received` | Payload/auth mismatch |
| `remote_guard_rejected_total` | Counter | `path`, `reason` | Endpoint blocking |

#### System

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `event_loop_lag_ms` | Histogram | — | Async health |
| `clock_jump_detected_total` | Counter | `direction` | Time anomalies |
| `local_gateway_unreachable_total` | Counter | — | Socket failures |

### 17.2 Required Alerts

| Alert | Condition | Severity | Action |
|-------|-----------|----------|--------|
| `FederationDisconnected` | `federation_ws_connected == 0` for > 30s | Critical | Check network/auth |
| `TelemetryDropsHigh` | `rate(telemetry_dropped_total) > 10/s` | Warning | Investigate backpressure |
| `IdentityCollision` | `identity_collision_total > 0` | Critical | Investigate duplicate nodes |
| `TLSDisabledInProd` | `tls_disabled_connections_total > 0` AND env=prod | Critical | Security review |
| `CancelRaceConditions` | `rate(cancel_without_mapping_total) > 1/min` | Warning | Review cancel timing |
| `TrackerLeaking` | `tracked_requests_active` growing unbounded | Warning | Check complete() calls |
| `StreamingStalls` | `p99(ttft_ms) > 5000` OR `p99(max_inter_chunk_gap_ms) > 3000` | Warning | Check proxy buffering |
| `PoolSaturation` | `http_pool_wait_ms p99 > 500` | Warning | Increase pool size |
| `SnapshotStale` | `time_since_last_resource_update_ms > 30000` | Warning | Check Remote health |

### 17.3 Observability Blind Spots

**Known limitations requiring extra vigilance:**

| Blind Spot | Why It's Hard | Mitigation |
|------------|---------------|------------|
| DROP_OLDEST hides overload | System "looks healthy" while routing degrades | Alert on sustained drop rate + stale→reachable flaps |
| SSE buffering | 200 OK returned while UX destroyed | Track TTFT + inter-chunk gap; streaming canary |
| Cancellation success | "Client disconnected" logged but Worker continues | End-to-end cancel metrics; gauge by stage |
| Health=200 but degraded | `/healthz` returns 200 in degraded state | Log WARN; document liveness-only semantics |
| Half-applied telemetry | Routing sees inconsistent state | Atomic snapshot swap; concurrency tests |

---

## 18. Known Failure Modes & Mitigations

### 18.1 Connection Lifecycle

| Failure Mode | Root Cause | Impact | Mitigation |
|--------------|------------|--------|------------|
| **GC pause → false unreachable** | 5-15s pause exceeds 10s threshold | Avoidable T0 decisions | `event_loop_lag_ms` metric; warn if threshold < (p99 lag × 3) |
| **Half-open WS + identity collision** | NAT idle drop; old WS "alive" in state | Federation wedged until manual intervention | Aggressive ping/pong; auto-remove stale connections |
| **Reconnect storm** | Deterministic failure (wrong key/version) | CPU/log spam; request starvation | Exponential backoff + jitter; stop on deterministic failures |
| **Socket fragility** | Gateway restart; stale socket file | Hanging streams, not fast errors | Startup assertion; periodic liveness probe |

### 18.2 Request Handling

| Failure Mode | Root Cause | Impact | Mitigation |
|--------------|------------|--------|------------|
| **Cancel race** | Disconnect before mapping registered | Worker keeps running; wasted resources | Register mapping before ANY awaited I/O |
| **Tracker growth** | Exception skips `complete()` | Memory leak; cancel correctness degrades | `tracked_requests_active` gauge; `complete()` in `finally` |
| **Shared pool starvation** | Long SSE streams saturate pool | Health/cancel blocked; false "dead" | Pool saturation metrics; startup warning if pool < streams + headroom |

### 18.3 Protocol & State

| Failure Mode | Root Cause | Impact | Mitigation |
|--------------|------------|--------|------------|
| **Spec/code drift on send queue** | Inline `await ws.send()` bypasses queue | HOL blocking; deadlocks | Unit test asserting queue usage; `send_queue_depth` metric |
| **Snapshot drift** | DROP_OLDEST + infrequent resource_update | Routing on stale data | Require periodic resource_update; alert on age |
| **Partial telemetry visibility** | `await` in update path | Impossible routing decisions | Pure sync updates; atomic pointer swap |
| **Version mismatch during deploy** | Master updated, Remote not | Hard failure | Stop reconnecting on mismatch; single loud log |

### 18.4 Security

| Failure Mode | Root Cause | Impact | Mitigation |
|--------------|------------|--------|------------|
| **TLS disabled and forgotten** | "Temporary" override becomes permanent | Credential theft; replay attacks | Prod fatal guard; red banner on every boot |
| **Hostname verification bypass** | VPS cutover uses IPs; operator disables verification | TLS defeated | Startup assertion; metric labels for failure reasons |
| **Secret leakage** | Incomplete redaction list | Keys in logs/errors | Automated redaction tests; `secret_redaction_miss_total` |
| **Header spoofing** | Late/partial stripping of X-Federation-* | Misroutes; auth confusion | Outermost middleware; assert no headers remain |
| **Identity binding gaps** | HTTP vs WS validate differently | Trust bypass | Single shared auth middleware |

---

## 19. VPS Transition Checklist

### 19.1 Pre-Cutover Validation

**INVARIANT:**
```
∀ VPS deployment: pre_cutover_checks_pass ∨ abort_deployment
```

#### Network Path

| Check | Validation | Action if Fails |
|-------|------------|-----------------|
| **WS ping interval** | `ping_interval_ms < 30000` | Reduce interval; VPS idle timeouts are aggressive |
| **Reverse proxy detection** | Check for `X-Forwarded-*` headers | Ensure `X-Accel-Buffering: no` and streaming headers set |
| **Body size limits** | Test 1MB+ request bodies | Configure proxy `client_max_body_size` |
| **TCP keepalive** | Verify WS stays open under idle | Enable TCP keepalives at OS level |

#### TLS

| Check | Validation | Action if Fails |
|-------|------------|-----------------|
| **Certificate validity** | Cert not expired; chain complete | Replace certificate |
| **Hostname matching** | Remote URL hostname matches cert CN/SAN | Use DNS name, not IP; or add IP to SAN |
| **CA trust** | CA file includes issuer chain | Update ca_file |

#### Connectivity

| Check | Validation | Action if Fails |
|-------|------------|-----------------|
| **WS handshake** | Connect + auth to all Remotes | Check firewall, DNS, TLS |
| **Local socket** | Gateway socket exists and connectable | Verify Gateway is running |
| **HTTP pool** | Pool size >= expected concurrent streams + 5 | Increase `max_connections_per_remote` |

### 19.2 VPS-Specific Configuration

```yaml
federation:
  # VPS-safe defaults
  ping_interval_ms: 15000  # < typical 30s idle timeout
  reconnect_interval_ms: 1000
  max_reconnect_delay_ms: 30000  # Cap backoff
  
  # Timeouts adjusted for network variability
  telemetry_stale_threshold_ms: 10000  # 2x LAN value
  telemetry_unreachable_threshold_ms: 20000  # 2x LAN value
  auth_deadline_seconds: 10  # 2x LAN value
  
  # Pool sizing for concurrent streams
  http_pool:
    max_connections_per_remote: 30  # Higher for VPS latency
    max_keepalive_connections: 10
```

### 19.3 Reverse Proxy Configuration

**Nginx:**
```nginx
location /v1/ {
    proxy_pass http://stargate:9999;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    
    # SSE/streaming
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600s;  # Long-running streams
    
    # Body size
    client_max_body_size 10M;
}

location /ws/ {
    proxy_pass http://stargate:9999;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
}
```

**Caddy:**
```caddy
reverse_proxy stargate:9999 {
    flush_interval -1  # Disable buffering
    transport http {
        read_timeout 3600s
    }
}
```

### 19.4 Monitoring for VPS Issues

| Symptom | Likely Cause | Metric to Check |
|---------|--------------|-----------------|
| Frequent reconnects | Idle timeout | `last_pong_age_ms`, `ws_reconnect_total{reason}` |
| High TTFT | Proxy buffering | `ttft_ms`, `max_inter_chunk_gap_ms` |
| Random 413/502 | Body size limit | `http_413_total`, `upstream_502_total` |
| Auth failures from NAT | IP-based limits hit | `connection_limit_reject_total` |
| Stale telemetry | Dropped updates | `telemetry_dropped_total`, `time_since_last_resource_update_ms` |

### 19.5 Path Normalization Under Proxies

**INVARIANT:**
```
∀ request r through proxy chain:
  path_normalized_identically(each_layer) ∨ bypass_possible
```

**Risk:** VPS deployments often introduce double-layer proxies (CDN → Nginx → Stargate). Path normalization differences between layers can enable bypass attacks.

**Testing Required:**

```python
def test_path_bypass_through_proxy():
    """Test bypass attempts via direct and proxied requests."""
    bypass_patterns = [
        "/v1%2Fchat%2Fcompletions",
        "/v1/../api/v1/federation/inference",
        "/api/v1/federation/inference/../../v1/chat/completions",
        "//api/v1/federation/inference",
        "/api/v1/federation/inference%00",
    ]
    
    for pattern in bypass_patterns:
        # Test direct to Stargate
        direct = requests.get(f"http://localhost:9999{pattern}")
        
        # Test through proxy
        proxied = requests.get(f"https://vps.example.com{pattern}")
        
        # Both should reject or normalize identically
        assert direct.status_code == proxied.status_code
```

**Recommendation:** Include path bypass tests in pre-VPS validation; test through actual proxy chain, not just direct.

### 19.6 Post-Cutover Verification

```bash
# 1. Verify WS stability (should stay connected)
watch -n 5 'curl -s localhost:9999/metrics | grep federation_ws_connected'

# 2. Check for telemetry drops
curl -s localhost:9999/metrics | grep telemetry_dropped_total

# 3. Verify streaming latency
curl -s localhost:9999/metrics | grep ttft_ms

# 4. Test cancellation chain
# (Initiate request, disconnect, verify Worker stops)

# 5. Verify no TLS warnings
journalctl -u stargate --since "10 min ago" | grep -i tls

# 6. Test path normalization through proxy
curl -v "https://vps.example.com/v1%2Fchat%2Fcompletions"
# Should return 404 or be normalized correctly, NOT bypass
```

---

## 20. Testing Obligations (Extended)

### 20.1 Additional Required Tests

| Test | Purpose | Failure Mode Covered |
|------|---------|---------------------|
| **Cancel before mapping** | Disconnect immediately after request start | Cancel race condition |
| **Tracker cleanup** | Force exception in request path | Memory leak / TTL expiry |
| **Inline send detection** | Assert outbound uses queue | HOL blocking |
| **Telemetry atomicity** | Concurrent reads during update | Partial visibility |
| **Secret redaction** | Log + echo secrets; verify not present | Key leakage |
| **Path bypass attempts** | `%2F`, `..`, `//` patterns | Endpoint bypass |
| **Proxy headers present** | X-Forwarded-* + SSE headers | VPS buffering |
| **Pool exhaustion** | Saturate pool; verify health check | False unreachable |

### 20.2 VPS-Specific Tests

| Test | Purpose |
|------|---------|
| **Idle timeout survival** | WS stays up for 5 minutes idle |
| **Large body handling** | 5MB request body succeeds |
| **Streaming under proxy** | TTFT < 500ms through Nginx |
| **NAT reconnection** | Recover after simulated NAT drop |

---

## 21. Invariants Summary

```
-- Identity
∀ stargate_id: unique
∀ gateway_id: unique
∀ federation_connection c: payload.source.stargate_id = authenticated_peer(c)
∀ connection c: stargate_id(c) ∈ active_connections ⟹ reject(c) ∧ log_security_event

-- Mode
∀ s ∈ S: |{MASTER, REMOTE, STANDALONE} ∩ {mode(s)}| = 1

-- Wire Format
∀ WebSocket message m: structure(m) = {signal, payload, [timestamp], [id]}
∀ handler h: h.signal ∈ SIGNAL_CONSTANTS
unknown_signal(m) ⟹ log_and_increment_counter(m)

-- Authentication
∀ connection c: ¬authenticated(c, t=5s) ⟹ close(c, code=4003)
∀ connection c: telemetry_flow(c) ⟹ authenticated(c)
protocol_version(master) ≠ protocol_version(remote) ⟹ close(c, code=4002)

-- TLS
require_tls = true ⟹ ∀ c: is_tls(c) ∨ reject(c)
∀ TLS c: valid_ca_chain(c) ∧ hostname_verified(c) ∨ reject(c)

-- Secrets
∀ log_entry l, ∀ secret s: s ∉ content(l)

-- Connections
∀ peer p: |unauthenticated_connections(p)| ≤ max_per_ip
∀ peer p: |federation_connections(p)| ≤ max_per_peer

-- Loop Prevention
∀ request r: hop_count(r) ≤ max_hops(r)
hop_count(r) = max_hops(r) ⟹ reject(r, 400)
∀ forward: hop_count incremented
∀ request at ingress: X-Federation-* stripped, hop_count = 0

-- Cancellation
∀ request r, client_disconnect(r) ⟹ cancel_worker(r)
∀ request r: state(r) ∈ {ACTIVE, CANCELLED, COMPLETED, EXPIRED}
transition_to_terminal(r) ⟹ subsequent_ops_noop(r)
ws_disconnect(remote) ⟹ pending_cancels queued
ws_reconnect(remote) ⟹ pending_cancels replayed BEFORE new_requests_accepted
∀ cancel c: register_mapping(c) BEFORE first_await(c)

-- State Management
∀ stateful_tracker: extends(Sequential) ∧ ¬uses(asyncio.Lock)
∀ federation_component f: ¬calls(f, EventBus.unsubscribe)
∀ federation_forwarder f: single_sender_task ∧ bounded_queue
∀ FederationWebSocketClient f: outbound via bounded_queue ∧ ¬inline_await_send
∀ request r: complete(r) ∨ cancel(r) ∨ expire(r) called in finally

-- Telemetry
freshness(t) = local_receipt_time - now (remote timestamps informational)
∀ resource_update u: is_complete_snapshot ∧ receiver_treats_as_authoritative
∀ routing_decision d: computed against coherent_snapshot ∧ ¬partial_telemetry_visible
∀ telemetry_update u: ¬awaits_during_apply(u) ∨ atomic_snapshot_swap(u)
∀ remote r: |queue(r)| ≤ max_queue_size ∧ rate(r) ≤ max_rate
∀ frame f: size(f) ≤ 1MB ∧ |f.loaded_models| ≤ 100
∃ periodic resource_update: interval ≤ 30s

-- ModelId
∀ model_id op: uses ModelId objects ∧ ¬string_manipulation
∀ wire message: parsed to ModelId at reception

-- SSE
∀ Remote r forwarding SSE: ¬buffer ∧ ¬parse ∧ ¬modify
upstream_disconnect ⟹ immediate_downstream_cancel

-- Routing
∀ request r: estimated_tokens(r) > max_context ⟹ reject(r, 400) BEFORE routing
(is_loaded ∧ has_capacity) ⟹ T1
(is_loaded ∧ ¬has_capacity) ⟹ T0

-- Endpoints
∀ request r on Remote: path(r) ∈ ALLOWED_PREFIXES ∨ rejected(r)
∀ WS e on Remote: e = /ws/federation ∨ rejected(e)

-- Protocol
∀ protocol_change: immediate_failure ∧ ¬silent_fallback

-- Operational Safety (NEW)
∀ startup s: assertions_pass(s) ∨ fatal_exit(s)
∀ dangerous_config c: ∃ guard g: validates(g, c)
(require_tls = false ∧ env = prod ∧ ¬I_UNDERSTAND_INSECURE) ⟹ fatal_exit
∀ failure_mode f: ∃ metric m: detects(m, f)

-- VPS Transition
ping_interval < typical_idle_timeout (30s)
∀ reverse_proxy p: streaming_headers_set(p) ∨ ttft_alert
∀ VPS deployment: pre_cutover_checks_pass ∨ abort_deployment
```

---

*Document: Phase 1 MUST SHIP*  
*Status: Ready for Implementation*  
*Revision: 2026-01-06 (v2 — added operational safety, metrics, failure modes, VPS checklist)*
