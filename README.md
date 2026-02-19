# Universal LLM Gateway

Privacy-by-design, zero-trust federated inference on your own GPUs.

- **Privacy by design**: prompts and outputs stay on your hardware; the execution plane runs with zero network access (`network_mode: "none"`)
- **Zero-trust security**: model execution is unprivileged and isolated — no network access, minimal host escalation surface

Universal LLM Gateway is an OpenAI-compatible inference stack built for a hostile-model threat model. Under the hood:

- **Stargate (9999)**: client API, routing, federation orchestration
- **Gateway (9998)**: execution plane (workers + model loading) inside a network-isolated container (`network_mode: "none"`)

## What it solves

- **Contain untrusted models**: execution runs unprivileged with zero network access
- **One API across many GPU nodes**: route to local + remote machines via federation
- **Multi-model workflows**: pipelines are “virtual models” (DAGs) behind a single `model` name

## Status: Alpha (v0.1.0)

Production-used on single-GPU deployments. Under active development.

## Capabilities (implemented)

| Capability | Endpoint | Notes |
|---|---|---|
| Chat completions (SSE streaming) | `POST /v1/chat/completions` | Stargate |
| Embeddings | `POST /v1/embeddings` | Stargate |
| Images (Flux.2) | `POST /v1/images/generations` | Implemented; not recently revalidated |
| Audio transcription (Whisper, file) | `POST /v1/audio/transcriptions` | Implemented on Gateway; last known working |
| Audio live transcription (Whisper, WS) | `WS /v1/audio/live_transcribe` | Implemented; experimental / needs work |
| Model list | `GET /v1/models` | Stargate |
| Health | `GET /health` | Stargate + Gateway |

Notes:
- Most clients talk to **Stargate** on `:9999`. Gateway is the execution plane and is usually not accessed directly.
- Stargate provides an authenticated Gateway forwarder: `GET/POST /gateway/{path}` forwards to Gateway (useful for execution-plane endpoints not yet first-class on Stargate).

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

- **Network isolation**: Gateway containers run with `network_mode: "none"` — zero network access. All communication via Unix sockets.
- **Non-root execution**: Containers run as unprivileged users — no root escalation surface.
- **Privilege separation**: Each Gateway runs isolated with minimal capabilities — models cannot affect other models or the host system.
- **Router-only Master**: Masters have no local Gateway. They orchestrate via relay stargates.
- **HTTP-authoritative control plane**: Model load completion is determined by the HTTP response from the load endpoint. Telemetry is for monitoring, not authoritative completion.
- **WebSocket telemetry**: Real-time state synchronization from relay stargates to Master.

### Request Flow

1. Client sends request to Master Stargate
2. DecisionEngine selects a relay stargate (T0/T1/T2 feasibility scoring)
3. Master loads model on remote Gateway if needed (via relay → edge → Gateway)
4. Master forwards inference request through the same path
5. Response streams back: Worker → Gateway → edge → relay → Master → Client

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

OpenAI-compatible. Most examples use Stargate port **9999**.

### Chat Completions

**`POST /v1/chat/completions`** — Text generation with streaming support, pipeline routing, and automatic model loading.

### Image Generation

**`POST /v1/images/generations`** — Flux.2 support with quality/style mapping and caption upsampling (implemented; not recently revalidated).

```bash
curl -X POST http://localhost:9999/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{"model": "flux.2-dev", "prompt": "A serene mountain landscape at sunset", "size": "1024x1024"}'
```

### Audio Transcription

**`WS /v1/audio/live_transcribe`** — Real-time Whisper transcription with VAD profiles (`sensitive` / `balanced` / `aggressive`) (experimental / needs work).

**`POST /v1/audio/transcriptions`** — File transcription (Whisper) (implemented on Gateway). If you need to access it via Stargate, use the Gateway forwarder:

```bash
curl -X POST http://localhost:9999/gateway/v1/audio/transcriptions \
  -F "model=whisper-large-v3" \
  -F "file=@audio.wav"
```

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

### Federation API

All endpoints require `X-Federation-Source` + `X-Federation-Key` headers.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/federation/models/load` | POST | Model load orchestration |
| `/api/v1/federation/tokens/count` | POST | Token counting |
| `/api/v1/federation/inference` | POST | Inference request |
| `/api/v1/federation/inference/{id}` | DELETE | Cancel inference |
| `/ws/federation` | WebSocket | Telemetry connection |

## Project Structure

```
universal-llm-gateway/
├── manage                            # Entry point — bootstraps venv, launches TUI
├── services/
│   ├── _universal-llm-gateway/       # Gateway service (port 9998)
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
