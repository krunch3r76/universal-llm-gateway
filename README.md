# Universal LLM Gateway

An OpenAI-compatible inference stack where nothing leaves your hardware. Models run unprivileged inside network-isolated containers (network_mode: "none"): no exfiltration, no phoning home, no cloud dependency.

## What it solves

- **Contain untrusted models**: execution runs unprivileged with zero network access
- **One API across many GPU nodes**: route to local + remote machines via federation
- **Multi-model workflows**: pipelines are "virtual models" behind a single `model` name

## Status: Alpha (v0.1.0)

Production-used on single-GPU deployments. Under active development.

## Capabilities (implemented)

| Capability | Endpoint | Notes |
|---|---|---|
| Chat completions (SSE streaming) | `POST /v1/chat/completions` | |
| Embeddings | `POST /v1/embeddings` | |
| Images (Flux.2) | `POST /v1/images/generations` | Under active development |
| Audio transcription (Whisper) | `POST /v1/audio/transcriptions` | Under active development |
| Model list | `GET /v1/models` | |
| Health | `GET /health` | |

All endpoints are served by Stargate on `:9999`.

### Roadmap
- [x] **Simplified onboarding process** — `./manage` bootstraps environment and launches TUI ([demo](https://krunch3r76.github.io/assets/universal-llm-gateway/measure_demo_02-18-2026_01.mp4))
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
Client → Master Stargate:9999 (host, router-only)
         ↓ Unix socket (local) or HTTPS (remote)
         Edge container (network_mode: "none")
         ├─ Edge Stargate (federation endpoint)
         └─ Gateway + Worker (inference)
```

For remote GPU nodes, a **Relay Stargate** on the remote host bridges the Master to the network-isolated Edge container on that host.

### Components

| Component | Role |
|-----------|------|
| **Master Stargate** (host, port 9999) | Client endpoint, routing decisions, federation orchestration |
| **Relay Stargate** (remote host, port 9999) | Federation peer on remote GPU nodes, auth boundary |
| **Edge Stargate** (container, no network) | Federation endpoint inside the container, Unix socket bridge |
| **Gateway** (container-internal) | Inference engine, worker lifecycle, model loading |
| **Worker** | LLM engine process (llama.cpp, vLLM, Whisper, Flux) |

### Key Design Decisions

- **Network isolation**: Edge containers run with `network_mode: "none"` — zero network access. All communication via Unix sockets.
- **Non-root execution**: Containers run as unprivileged users — no root escalation surface.
- **Privilege separation**: Each Edge runs isolated with minimal capabilities — models cannot affect other models or the host system.
- **Router-only Master**: Masters have no local Gateway. They orchestrate via Edge and Relay stargates.
- **HTTP-authoritative control plane**: Model load completion is determined by the HTTP response from the load endpoint. Telemetry is for monitoring, not authoritative completion.
- **WebSocket telemetry**: Real-time state synchronization from Edge/Relay stargates to Master.

### Request Flow

1. Client sends request to Master Stargate
2. DecisionEngine selects an Edge (T0/T1/T2 feasibility scoring)
3. Master loads model on the Edge if needed (via Unix socket or relay)
4. Master forwards inference request through the same path
5. Response streams back: Worker → Gateway → Edge → (relay) → Master → Client

## Quick Start

<a href="https://krunch3r76.github.io/assets/universal-llm-gateway/measure_demo_02-18-2026_01.mp4">
  <img src="https://krunch3r76.github.io/assets/universal-llm-gateway/measure_thumbnail_02-18-2026_01.jpg" alt="Click to play setup demo" width="100%">
</a>

```bash
git clone https://github.com/krunch3r76/universal-llm-gateway.git
cd universal-llm-gateway
./manage
```

## API Endpoints

OpenAI-compatible. All requests go to Stargate on port **9999**.

### Chat Completions

**`POST /v1/chat/completions`** — Text generation with streaming support, pipeline routing, and automatic model loading.

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
- Directed acyclic graph (DAG) execution with automatic parallelization
- Explicit object-flow (`stepName.json.field` bindings) — no hidden state
- Automatic dependency resolution from `handler_inputs`
- Built-in retry, timeout, checkpointing, and map/reduce
- OpenAI-compatible — pipelines are just model IDs

See [Pipeline System README](services/universal-stargate/systems/pipeline/README.md) for architecture and schema reference.

## Federation

Federation lets a Master Stargate distribute inference across multiple GPU nodes. Each remote node runs a Relay Stargate on the host that bridges to a network-isolated Edge container. The Master routes requests to the best available node based on feasibility scoring (loaded model, GPU capacity, queue depth). Clients still talk to `:9999` — federation is transparent.

## Project Structure

```
universal-llm-gateway/
├── manage                            # Entry point — bootstraps venv, launches TUI
├── services/
│   ├── _universal-llm-gateway/       # Gateway service (container-internal)
│   └── universal-stargate/           # Stargate service (port 9999)
├── libs/
│   ├── inference_djinn/              # LLM engines (llama.cpp, vLLM, Whisper, Flux)
│   ├── model_id/                     # ModelId type-safe identifiers
│   ├── process_ipc/                  # Process supervision and IPC
│   ├── provenance/                   # Model provenance tracking
│   ├── universal_concurrency/        # Async concurrency primitives
│   ├── universal_event_bus/          # Event messaging system
│   ├── universal_logging/            # Structured logging
│   ├── universal_protocol/           # RPC protocol definitions
│   ├── universal_transport/          # Transport layer (Unix sockets, HTTP)
│   └── universal_workspace/          # Workspace path resolution
├── scripts/
│   └── model_manager/               # TUI application (Textual, MVC)
├── config/                           # Model catalog, templates, stargate configs
├── docker/                           # Dockerfiles, Compose configs, build scripts
├── pipelines/                        # Shipped pipeline definitions
└── tools/                            # Developer utilities (pipeline viewer)
```

## License

MIT License — see [LICENSE](LICENSE).

## Author

**krunch3r76** ([@krunch3r76](https://github.com/krunch3r76))
- Issues: [GitHub Issues](https://github.com/krunch3r76/universal-llm-gateway/issues)
