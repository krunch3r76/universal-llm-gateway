# Federation API Contract

HTTP and WebSocket API specifications for federation system.

---

## HTTP ENDPOINTS

### POST /api/v1/federation/models/load

**Mode**: Remote (Edge, Relay with local_edge)  
**Auth**: Federation API key (via header or query)  
**Purpose**: Orchestrate model loading on remote gateway

**Request**:
```json
{
  "model_id": "deepseek-llm-67b-chat-q4-k-m-4096-hybrid",
  "sticky": true
}
```

**Response** (200 OK):
```json
{
  "status": "ok",
  "model_id": "deepseek-llm-67b-chat-q4-k-m-4096-hybrid",
  "message": "Model loaded successfully"
}
```

**Response** (503 Service Unavailable):
```json
{
  "detail": "Model load failed: <reason>"
}
```

**Idempotency**: Returns 200 if model already loaded  
**Timeout**: 185s (slightly longer than Edge's 180s)  
**Waiting**: Blocks until MODEL_LOADED or MODEL_LOAD_FAILED event

**Files**: `remote/api/models.py`

---

### POST /api/v1/federation/models/unload

**Mode**: Remote (Edge, Relay with local_edge)  
**Auth**: Federation API key  
**Purpose**: Unload model from remote gateway

**Request**:
```json
{
  "model_id": "deepseek-llm-67b-chat-q4-k-m-4096-hybrid"
}
```

**Response** (200 OK):
```json
{
  "status": "ok",
  "model_id": "deepseek-llm-67b-chat-q4-k-m-4096-hybrid",
  "message": "Model unloaded"
}
```

**Idempotency**: Returns 200 if model not loaded  
**Timeout**: 60s (unload should be fast)

**Files**: `remote/api/models.py`

---

### POST /api/v1/federation/tokens/count

**Mode**: Remote (Edge, Relay with local_edge)  
**Auth**: Federation API key  
**Purpose**: Count tokens via remote gateway (authoritative tokenizer)

**Request**:
```json
{
  "model": "deepseek-llm-67b-chat-q4-k-m-4096-hybrid",
  "messages": [
    {"role": "user", "content": "Hello"}
  ]
}
```

**Alternative** (prompt format):
```json
{
  "model": "deepseek-llm-67b-chat-q4-k-m-4096-hybrid",
  "prompt": "Hello world"
}
```

**Response** (200 OK):
```json
{
  "token_count": 42,
  "context_limit": 4096,
  "max_generation_tokens": 4054
}
```

**Response** (503 Service Unavailable):
```json
{
  "detail": "Token counting failed: <reason>"
}
```

**Notes**:
- Proxies to Gateway's `/api/v1/tokens/count` endpoint
- Translates `model` → `model_name` for Gateway schema
- Relay topology: Remote → Edge (Unix socket) → Gateway (inside container)
- Direct topology: Remote → Gateway (via socket or HTTP)

**Invariant**: Token counting MUST occur on gateway that will execute inference (authoritative tokenizer)

**Files**: `remote/api/tokens.py`

---

### POST /api/v1/federation/inference

**Mode**: Remote (Edge, Relay with local_edge)  
**Auth**: Federation API key  
**Purpose**: Proxy inference request to remote gateway

**Request**:
```json
{
  "model": "deepseek-llm-67b-chat-q4-k-m-4096-hybrid",
  "messages": [
    {"role": "user", "content": "Hello"}
  ],
  "stream": true,
  "max_tokens": 100
}
```

**Response** (streaming=true):
- Content-Type: `application/newline-delimited-json`
- Body: SSE chunks (newline-delimited JSON)
- Headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no`

**Response** (streaming=false):
- Content-Type: `application/json`
- Body: Complete JSON response

**Capacity Check**: Edge validates active request count before forwarding

**Timeout**: 1800s (30 minutes)

**Files**: `remote/api/inference.py`

---

### GET /api/v1/federation/telemetry

**Mode**: Remote (HTTP polling topology - Golem only)  
**Auth**: Federation API key  
**Purpose**: HTTP polling fallback for telemetry (WebSocket preferred)

**Response** (200 OK):
```json
{
  "gateway_id": "edge-jupiter-gateway",
  "available_vram_mb": 8192,
  "total_vram_mb": 16384,
  "loaded_models": ["model-1", "model-2"],
  "busy_models": ["model-1"],
  "timestamp": 1706745600.0
}
```

**Notes**:
- Legacy endpoint for HTTP polling topology
- WebSocket topology preferred (real-time)
- Polled by Master at configured interval

**Files**: `remote/api/telemetry.py`

---

### POST /api/v1/federation/cancel

**Mode**: Remote  
**Auth**: Federation API key  
**Purpose**: Cancel active inference request

**Request**:
```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Response** (200 OK):
```json
{
  "status": "cancelled"
}
```

**Response** (404 Not Found):
```json
{
  "detail": "Request not found or already completed"
}
```

**Files**: `remote/api/cancel.py`

---

## WEBSOCKET ENDPOINTS

### WS /ws/federation/master

**Mode**: Master  
**Direction**: Accepts connections FROM Remote  
**Auth**: Federation API key + stargate_id in handshake  
**Purpose**: Receive telemetry from Remote Stargates

**Handshake**:
```json
{
  "type": "FederationInit",
  "stargate_id": "relay-jupiter",
  "api_key": "<api-key>",
  "version": "1.0"
}
```

**Response**:
```json
{
  "type": "FederationAuth",
  "status": "authenticated",
  "stargate_id": "master"
}
```

**Message Flow**:
- Remote → Master: Telemetry events (RESOURCE_UPDATE, MODEL_LOADED, etc.)
- Master → Remote: Pings (keepalive)

**Keepalive**: Ping every `ping_interval_ms` (≤30000)

**Files**: `link/ws/master/endpoint.py`, `link/ws/master/server.py`

---

### WS /ws/federation/edge

**Mode**: Edge  
**Direction**: Accepts connections FROM Relay (via Unix socket)  
**Auth**: Federation API key + stargate_id in handshake  
**Purpose**: Forward telemetry from Edge → Relay → Master

**Handshake**: Same as Master endpoint

**Message Flow**:
- Edge → Relay: Telemetry events (forwarded to Master)
- Relay → Edge: Pings (keepalive)

**Transport**: Unix socket (network-isolated container)

**Files**: `edge/router.py`, `edge/server.py`

---

## TELEMETRY PROTOCOL

### Message Envelope

All telemetry messages use standardized envelope:

```json
{
  "type": "telemetry.resource.updated",
  "timestamp": 1706745600.0,
  "data": {
    "source": {
      "stargate_id": "relay-jupiter",
      "gateway_id": "edge-jupiter-gateway"
    },
    "available_vram_mb": 8192,
    "available_ram_mb": 32768
  }
}
```

**Naming Convention**: `telemetry.<domain>.<action>` (lowercase, dot-separated, past tense)

**Validation**: Runtime enforcement via `TELEMETRY_PAYLOAD_TYPES` registry

---

### Telemetry Signal Types

| Signal | Payload | Purpose |
|--------|---------|---------|
| `telemetry.resource.updated` | ResourceUpdate | VRAM/RAM availability |
| `telemetry.model.loaded` | ModelLoaded | Model loaded successfully |
| `telemetry.model.unloaded` | ModelUnloaded | Model unloaded |
| `telemetry.model.busy` | ModelBusy | Model started processing |
| `telemetry.model.idle` | ModelIdle | Model finished processing |
| `telemetry.model.loading.started` | ModelLoadingStarted | Load initiated |
| `telemetry.model.load.failed` | ModelLoadFailed | Load failed |
| `telemetry.heartbeat` | TelemetryHeartbeat | Keepalive (every 5s) |
| `telemetry.gateway.snapshot` | GatewaySnapshot | Full state snapshot |

**Factory Pattern**: All payloads constructed via `@telemetry_factory` functions

**Files**: `libs/universal_protocol/messages/telemetry.py`

---

### GatewaySnapshot Telemetry (2026-01-18)

**Purpose**: Initial telemetry with dual model lists for routing vs display

**Payload**:
```json
{
  "source": {
    "stargate_id": "relay-jupiter",
    "gateway_id": "edge-jupiter-gateway"
  },
  "available_models": ["model-1-4k", "model-1-32k", "model-2-4k"],
  "activated_models": ["model-1-32k", "model-2-4k"],
  "activated_contexts": {
    "model-1": [32768],
    "model-2": [4096]
  },
  "model_resources": {
    "model-1-32k": {"vram_mb": 8000, "ram_mb": 16000},
    "model-2-4k": {"vram_mb": 4000, "ram_mb": 8000}
  },
  "available_vram_mb": 16384,
  "total_vram_mb": 24576
}
```

**Dual Lists**:
- `available_models`: Full catalog (Master can route to any)
- `activated_models`: Filtered by activation rules (shown in `/v1/models`)
- `activated_contexts`: Original activation rules from catalog

**Filtering**: Edge applies `apply_activation_filtering()` based on:
- `activated_gpu_contexts` / `activated_cpu_contexts` from catalog
- Available VRAM/RAM resources
- Context size from synthetic model ID

**Fallback**: If `activated_contexts` empty, `activated_models = available_models`

**Reference**: See `tmp/summaries/activated-contexts-model-listing.md`

---

## ERROR RESPONSES

### Standard Error Envelope

```json
{
  "code": "MODEL_LOAD_FAILED",
  "message": "Failed to load model: out of memory",
  "source": "edge",
  "retryable": false,
  "data": {
    "model_id": "model-1",
    "gateway_id": "edge-jupiter-gateway"
  }
}
```

**Fields**:
- `code`: ErrorCode enum value (SCREAMING_SNAKE_CASE)
- `message`: Human-readable description
- `source`: Origin (rpc|stream|engine|worker|gateway|edge|master)
- `retryable`: Whether caller should retry
- `data`: Optional context (model_id, gateway_id, etc.)

**Invariant**: ∀ error_response: shape = {code, message, source, retryable, data}

---

### HTTP Status Codes

| Status | Meaning | Retryable |
|--------|---------|-----------|
| 200 | Success | N/A |
| 400 | Invalid request (client error) | No |
| 403 | Authentication failed | No |
| 404 | Resource not found | No |
| 422 | Validation error | No |
| 500 | Internal server error | Maybe |
| 503 | Service unavailable (capacity, timeout) | Yes |

**Capacity Errors**: Always 503 with structured error envelope

---

## UNIX SOCKET TRANSPORT

### Relay → Edge Forwarding

**Pattern**: Relay forwards HTTP requests to Edge via Unix socket

**URL Format**: Use placeholder URL `http://edge` (transport determines actual connection)

```python
# ✅ CORRECT
async with httpx.AsyncClient(
    transport=httpx.AsyncHTTPTransport(uds="/tmp/universal-protocol/edge.sock"),
    timeout=30.0,
) as client:
    response = await client.post("http://edge/api/v1/federation/tokens/count", json=body)
```

**Invariant**: `∀ relay_forward where transport = unix_socket: url = "http://edge/*" ∧ transport = UDS`

**Why Placeholder**: httpx requires well-formed URL even with UDS transport. Hostname ignored (transport handles routing), but must be valid DNS-style name.

**Anti-pattern**: Using `http://localhost` fails (httpx interprets as network address)

---

## AUTHENTICATION

### API Key Authentication

**Header**: `Authorization: Bearer <api-key>` (preferred)  
**Query**: `?api_key=<api-key>` (fallback)

**Validation**: 
- Master validates Remote by `stargate_id` in `allowed_peers`
- Edge validates Relay by `stargate_id` in `allowed_peers`
- API key must match configured value

**Security**: Keys stored in environment variables, never hardcoded

---

### Federation Handshake

**Step 1**: Client sends `FederationInit` with credentials

```json
{
  "type": "FederationInit",
  "stargate_id": "relay-jupiter",
  "api_key": "<api-key>",
  "version": "1.0"
}
```

**Step 2**: Server validates and responds with `FederationAuth`

```json
{
  "type": "FederationAuth",
  "status": "authenticated",
  "stargate_id": "master"
}
```

**Failure**: Connection closed with 403 status

**Timeout**: 5s for handshake completion

---

## RELATED DOCUMENTATION

- **Topology Details**: See `TOPOLOGY.md` (deployment patterns, connection graphs)
- **Orchestration**: See `ORCHESTRATION.md` (single-flight, retry, metrics)
- **Operations**: See `OPERATIONS.md` (troubleshooting, configuration)
- **Anti-Patterns**: See `ANTI_PATTERNS.md` (historical bugs, detection)
