# Universal LLM Gateway

A **privacy and security-first** federated LLM inference gateway built on zero-trust principles. Gateway containers run with zero network access (`network_mode: "none"`) and non-root Docker processes — your prompts never leave your hardware, and models cannot exfiltrate data, access the network, or escalate privileges regardless of their behavior. OpenAI-compatible API with multi-model pipeline orchestration.

## Status: Alpha (v0.1.0)

Production-tested on single-GPU deployments. Under active development.

### What Works
- **Privacy by design**: Gateway containers have zero network access — your prompts and outputs never leave your hardware
- **Zero-trust security**: Models run as non-root in isolated containers — they cannot exfiltrate data, access external services, or escalate privileges regardless of behavior
- **Defense in depth**: Network isolation (`network_mode: "none"`) + unprivileged processes + Unix socket communication
- Federated inference routing with network-isolated Gateway containers
- Single-GPU deployments: GGUF/llama.cpp, vLLM, Whisper, Flux
- Pipeline system for multi-model DAG workflows (example pending)
- WebSocket telemetry for real-time state synchronization
- Docker-based deployment with Unix socket isolation
- OpenAI-compatible API (`/v1/chat/completions`, `/v1/images/generations`, `/v1/audio/live_transcribe`)
- Real-time audio transcription with VAD profiles

### Roadmap
- [ ] **Simplified onboarding process** (expanded Quick Start in this README, installation guides, CLI wizards)
- [ ] Multi-GPU / tensor parallelism (vLLM)
- [ ] Native VPS deployment tooling (one-command setup)
- [ ] Pipeline system stabilization (v6 schema)
- [ ] Simplified model onboarding (CLI wizard or web UI)
- [ ] Flux image generation testing and stabilization
- [ ] Codebase refactoring and modularization
- [ ] Contributor documentation and development guides

Contributions welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup.

---

## Architecture

```
Client → Master Stargate:9999 (router-only)
         ↓ HTTPS (authenticated)
         relay stargate:9999 (federation peer)
         ↓ forwarding
         edge stargate (container-coupled)
         ↓ Unix socket (no network)
         Gateway:9998 (network_mode: "none")
         ↓ RPC
         Worker (inference_djinn)
```

### Components

| Component | Role |
|-----------|------|
| **Master Stargate** (port 9999) | Client endpoint, routing decisions, federation orchestration |
| **relay stargate** (port 9999) | Federation peer, auth boundary, telemetry forwarding |
| **edge stargate** | Container-coupled Gateway proxy, Unix socket bridge |
| **Gateway** (port 9998) | Core inference engine, worker lifecycle, model loading |
| **Worker** | LLM engine process (llama.cpp, vLLM, Whisper, Flux) |

### Key Design Decisions

- **Network isolation**: Gateway containers run with `network_mode: "none"` — zero network access. All communication via Unix sockets. Models cannot exfiltrate prompts, outputs, or sensitive data over the network.
- **Non-root execution**: All containers run as unprivileged users — no root escalation surface. Models cannot gain system-level privileges.
- **Privilege separation**: Each Gateway runs in its own isolated container with minimal capabilities — models cannot affect other models or the host system.
- **Router-only Master**: Masters have no local Gateway. They orchestrate via relay stargates.
- **HTTP-authoritative operations**: Model load completion verified via HTTP response, not telemetry events.
- **WebSocket telemetry**: Real-time state synchronization from relay stargates to Master.

### Request Flow

1. Client sends request to Master Stargate
2. DecisionEngine selects a relay stargate (T0/T1/T2 feasibility scoring)
3. Master loads model on remote Gateway if needed (via relay → edge → Gateway)
4. Master forwards inference request through the same path
5. Response streams back: Worker → Gateway → edge → relay → Master → Client

## Quick Start

### Local Development

```bash
./scripts/dev-start.sh

# Test
curl http://localhost:9999/health

# Chat completion
curl -X POST http://localhost:9999/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "hermes3-llama3.1-8b-16384",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true
  }'
```

### Production Deployment

1. **Build Docker images**: `cd docker && ./scripts/build/build.sh`
2. **Configure federation**: Copy `.env.example`, generate keys, edit `config/stargate_config.yaml`
3. **Deploy**: `docker compose -f docker/compose/federation-isolated.yml up -d`

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed installation and environment setup.

## API Endpoints

OpenAI-compatible. All examples use Stargate port **9999**.

### Chat Completions

**`POST /v1/chat/completions`** — Text generation with streaming support, pipeline routing, and automatic model loading.

### Image Generation

**`POST /v1/images/generations`** — Flux.2 model support with quality/style mapping and caption upsampling.

```bash
curl -X POST http://localhost:9999/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{"model": "flux.2-dev", "prompt": "A serene mountain landscape at sunset", "size": "1024x1024"}'
```

### Audio Transcription

**`WS /v1/audio/live_transcribe`** — Real-time Whisper transcription with VAD profiles (`sensitive` / `balanced` / `aggressive`).

### System

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Health check |
| `GET /v1/models` | List available models |
| `GET /api/v1/gateways/status` | Gateway status and model distribution |
| `GET /api/v1/monitoring/gateway-states` | Real-time Gateway telemetry |

## Pipelines

Virtual models that orchestrate multiple real models behind a single `model` name. Use a pipeline ID as the `model` parameter in any standard API request.

```bash
curl -X POST http://localhost:9999/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "es-en-colloquial", "messages": [{"role": "user", "content": "Hola, ¿cómo andas?"}]}'
```

**Key features:**
- DAG-based execution with automatic parallelization
- Explicit object-flow (`stepName.json.field` bindings) — no hidden state
- Automatic dependency resolution from `handler_inputs`
- Built-in retry, timeout, checkpointing, and map/reduce
- OpenAI-compatible — pipelines are just model IDs

See [Pipeline System README](services/universal-stargate/systems/pipeline/README.md) for architecture and schema reference.

## Federation

Distributed inference routing across network-isolated Gateway containers.

### Configuration

**Master Stargate** (router-only):
```yaml
federation:
  mode: master
  stargate_id: "master-node"
  remotes:
    - stargate_id: "compute-1"
      url: "https://compute-1:9999"
      api_key: "${FEDERATION_KEY_COMPUTE_1}"
```

**Remote Stargate** (compute node):
```yaml
federation:
  mode: remote
  stargate_id: "compute-1"
  local_edge:
    socket_path: "/sockets/gateway.sock"
    stargate_id: "edge-compute-1"
    api_key: "${FEDERATION_KEY_EDGE}"
  master:
    stargate_id: "master-node"
    url: "https://master:9999"
    api_key: "${FEDERATION_KEY}"
```

**Gateway container** (network-isolated):
```yaml
services:
  gateway:
    image: universal-llm-gateway:gpu
    network_mode: "none"
    volumes:
      - /tmp/universal-sockets:/sockets
      - ${MODEL_PATH_ROOT}:/golem/models:ro
```

### Federation API

All endpoints require `X-Federation-Source` + `X-Federation-Key` headers.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/federation/models/load` | POST | Model load orchestration |
| `/api/v1/federation/tokens/count` | POST | Token counting |
| `/api/v1/federation/inference` | POST | Inference request |
| `/api/v1/federation/inference/{id}` | DELETE | Cancel inference |
| `/ws/federation` | WebSocket | Telemetry connection |

## Configuration

### Environment Variables

```bash
# Gateway
GATEWAY_HOST=0.0.0.0          # Bind address
GATEWAY_PORT=9998              # Port
GATEWAY_UNIX_SOCKET=           # Unix socket path (overrides TCP)
GATEWAY_API_KEY=               # Required — generate with: python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# Stargate
STARGATE_HOST=0.0.0.0
STARGATE_PORT=9999
STARGATE_UNIX_SOCKET=          # Unix socket path (overrides TCP)

LOG_LEVEL=info
```

See `.env.example` for a complete template with key generation instructions.

### Model Catalog

Models are defined as individual YAML files organized by domain:

```
config/models/
├── text_llm/llama-cpp/*.yaml    # GGUF models
├── text_llm/vllm/*.yaml          # HF/AWQ/GPTQ models
├── audio/whisper/*.yaml          # Whisper models
├── graphics/diffusers/*.yaml     # Image generation models
└── translation/ctranslate2/*.yaml
```

## Project Structure

```
universal-llm-gateway/
├── services/
│   ├── _universal-llm-gateway/    # Gateway service (port 9998)
│   └── universal-stargate/        # Stargate service (port 9999)
├── libs/
│   ├── inference_djinn/           # LLM engines (llama.cpp, vLLM, Whisper, Flux)
│   ├── process_ipc/              # Process supervision and IPC
│   ├── universal_protocol/       # RPC protocol definitions
│   ├── universal_transport/      # Transport layer (Unix sockets, HTTP)
│   ├── universal_event_bus/      # Event messaging system
│   └── universal_logging/        # Structured logging
├── config/                       # Configuration files and model catalog
├── docker/                       # Dockerfiles, Compose configs, build scripts
├── scripts/                      # Utility and deployment scripts
└── pipelines/                    # Shipped pipeline definitions (mature)
```

## License

MIT License — see [LICENSE](LICENSE).

## Author

**krunch3r76** ([@krunch3r76](https://github.com/krunch3r76))
- Issues: [GitHub Issues](https://github.com/krunch3r76/universal-llm-gateway/issues)
