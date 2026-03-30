# Cloud Proxy

> **Documentation status**: This is a capability overview. Comprehensive API reference and configuration guide are pending.

An optional, standalone service that routes inference requests to cloud API providers. It is the **only component in the system with outbound internet access** — if it's not running, the system is local-only by construction.

## Security Model

- **Isolation by construction**: no other component can make outbound requests; cloud access is opt-in per deployment
- **Credential containment**: API keys live exclusively in the cloud proxy process; Stargate and edge containers never see them
- **Network boundary**: communicates with Stargate over loopback only (UDS at `/tmp/universal-protocol/cloud-proxy.sock`); outbound connections restricted to declared provider domains
- **Uniform routing**: cloud models appear in `/v1/models` alongside local models — no separate API surface

## Providers

| Provider | Adapter | Notes |
|----------|---------|-------|
| OpenRouter | OpenAI-compatible | Primary multi-provider gateway |
| Anthropic | Native adapter | Direct Claude API |
| OpenAI | OpenAI-compatible | Direct OpenAI API |
| xAI | OpenAI-compatible | Grok at `https://api.x.ai/v1` |
| Google | OpenAI-compatible | Via OpenRouter or direct |

## API Endpoints

### Inference

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `POST /v1/chat/completions` | POST | Forward chat completion with auth injection and SSE relay |
| `POST /v1/embeddings` | POST | Forward embedding request with auth |

### Provider-native (non–OpenAI-shaped)

Workspace IDs (`anthropic/...`, `xai/...`) stay on `POST /v1/chat/completions`. These routes accept **raw provider model IDs** and **native JSON** bodies; Stargate exposes the same paths on `:9999` under `/api/v1/providers/...`.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `POST /api/v1/providers/anthropic/messages` | POST | Anthropic Messages API passthrough |
| `POST /api/v1/providers/xai/responses` | POST | xAI Responses API passthrough |
| `POST /api/v1/providers/openai/responses` | POST | Reserved — returns **501** (not implemented) in phase 1 |

### Catalog

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `GET /catalog` | GET | Cached model list for Stargate integration |
| `GET /catalog/pricing` | GET | Models with per-token pricing for cost-aware routing |
| `GET /api/models` | GET | Full provider catalog with pricing metadata |
| `GET /api/models/{id}` | GET | Single model pricing lookup |
| `POST /api/refresh` | POST | Force catalog refresh from providers |

### Model Selection

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `POST /api/select` | POST | Task-aware model selection — matches task tags, quality tiers, and cost constraints |

### Browser UI

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `GET /` | GET | Interactive model browser with task tags, quality tiers, and cost filters |

### System

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `GET /health` | GET | Liveness check with provider summary |

### Stargate Passthrough

These endpoints are proxied through Stargate on `:9999` so clients don't need direct cloud proxy access:

- `GET /api/models` — cloud model catalog
- `POST /api/select` — task-aware selection
- `POST /api/refresh` — force catalog refresh
- `POST /api/v1/providers/anthropic/messages` — native Anthropic Messages
- `POST /api/v1/providers/xai/responses` — native xAI Responses
- `POST /api/v1/providers/openai/responses` — OpenAI-native stub (501)

## Configuration

Config file: `~/.gateway/cloud-proxy.yaml`

```yaml
socket_path: /tmp/universal-protocol/cloud-proxy.sock
# OR: host: 0.0.0.0 / port: 8200 (mutually exclusive with socket_path)

stargate_url: http://localhost:9999    # local model catalog source

providers:
  - provider: openrouter
    api_key_env: OPENROUTER_API_KEY    # env var name (preferred over inline key)
    max_concurrent: 20
    refresh_interval_hours: 24
    allow_prefixes:
      - "anthropic/"
      - "openai/"
      - "google/"
    native_tools: []                   # tool-use allowlist per provider
    # mcp_server_url: ...              # optional MCP integration
    # mcp_auth_token: ...

  - provider: anthropic
    api_key_env: ANTHROPIC_API_KEY
    max_concurrent: 10
    allow_prefixes:
      - "claude-"

  - provider: xai
    api_key_env: XAI_API_KEY
    base_url: https://api.x.ai/v1
```

### Key Options

| Option | Purpose |
|--------|---------|
| `socket_path` | Unix domain socket path (default transport) |
| `host` / `port` | TCP binding (alternative to UDS) |
| `stargate_url` | Stargate URL for local model catalog merge |
| `providers[].allow_prefixes` | Filter which models are exposed from each provider |
| `providers[].max_concurrent` | Concurrent request limit per provider |
| `providers[].native_tools` | Tool-use allowlist per provider |
| `providers[].mcp_server_url` | Publish `provider/model-mcp` variants that auto-attach the remote MCP server |
| `providers[].refresh_interval_hours` | Catalog refresh frequency |

## Events

Event stream: `/tmp/cloud-proxy-events/current.jsonl`

Covers provider requests, catalog refreshes, selection decisions, errors.

## Key Files

| File | Responsibility |
|------|---------------|
| `cloud_proxy.py` | FastAPI app, endpoint routing |
| `forwarder.py` | Request forwarding with auth injection and SSE relay |
| `catalog.py` | Provider catalog management and periodic refresh |
| `local_catalog.py` | Stargate model catalog integration |
| `config.py` | Configuration dataclasses and YAML parsing |
| `tagging.py` | Task tag classification for model selection |
| `browser_routes.py` | Browser UI API endpoints |
| `browser.py` | Browser UI HTML/JS serving |
| `events.py` | Event emission |
