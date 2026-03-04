# Universal LLM Gateway

An OpenAI-compatible inference stack where nothing leaves your hardware. Models run unprivileged inside network-isolated containers (`network_mode: "none"`): no exfiltration, no phoning home, no cloud dependency.

## What it solves

- **Contain untrusted models**: execution runs unprivileged with zero network access
- **One API across many GPU nodes**: route to local + remote machines via federation
- **Multi-model workflows**: pipelines are "virtual models" behind a single `model` name
- **Secure tool-like capabilities**: pipelines perform actions (search, shell, verification) on behalf of models — models never get direct system access
- **Optional cloud routing**: a separate, opt-in cloud proxy service isolates all outbound internet access to a single process — if it's not running, outbound traffic is impossible by construction

## Status: Alpha (v0.1.0)

Production-used on single-GPU and multi-node federated deployments. Under active development.

## Capabilities

| Capability | Endpoint | Notes |
|---|---|---|
| Chat completions (SSE streaming) | `POST /v1/chat/completions` | |
| Embeddings | `POST /v1/embeddings` | |
| Images (Flux.2) | `POST /v1/images/generations` | Under active development |
| Audio transcription (Whisper) | `POST /v1/audio/transcriptions` | Under active development |
| Model list | `GET /v1/models` | Includes local, federated, cloud, and pipeline models |
| Health | `GET /health` | |

All endpoints are served by Stargate on `:9999`.

### Roadmap
- [x] **Simplified onboarding** — `./manage` bootstraps environment and launches TUI ([demo](https://krunch3r76.github.io/assets/universal-llm-gateway/measure_demo_02-18-2026_01.mp4))
- [x] **RAG service** — ChromaDB-backed semantic search with file watching and recency scoring
- [x] **Pipeline pseudo-tooling** — prompt-driven tool calling: any model becomes tool-capable without native function-calling support, and adversarial tool invocations are structurally prevented because models produce structured JSON decisions that the engine maps to pre-defined operations — models never call tools directly
- [x] **Consensus pipeline (v7)** — multi-model answer generation with decomposition, domain-specific verification, veto, and structured synthesis
- [x] **Pipeline event observability** — dedicated `/tmp/pipeline-events/` stream with per-step metrics (tokens, duration, call count)
- [x] **Cloud proxy** — OpenRouter integration with quality-tier autoselection and configurable provider allow-lists; browser UI for interactive model selection; vision model routing expected to work (untested)
- [x] **RAG-augmented routing** — `rag-context` pipeline rewrites queries into embedding-optimized sub-queries, executes parallel retrieval with RRF merge, and returns assembled context chunks; `rag-answer` wraps it with grounded answer generation; both callable as virtual model IDs or via `./scripts/ask`; `source_prefixes` scopes retrieval to any indexed corpus subset; persona memory and project-scoped assistants are the next layer (see `stargate-persona-memory-model` in backlog)
- [ ] **RAG recency scoring normalization** — current flat additive recency boost will be replaced with bucket-weighted hybrid scoring (cosine + BM25, time-bucketed, min-max normalized per bucket) so highly relevant older chunks can still outrank weakly relevant recent ones
- [ ] Multi-GPU / tensor parallelism (vLLM)
- [ ] Native VPS deployment tooling (one-command setup)
- [ ] Simplified model onboarding (CLI wizard or web UI)
- [ ] Codebase refactoring and modularization

Contributions welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup.

---

## Architecture

```
Client → Master Stargate:9999 (host, orchestrator)
         ├─ Unix socket (local) → Edge container (network_mode: "none")
         │                         ├─ Edge Stargate (federation endpoint)
         │                         └─ Gateway + Worker (inference)
         ├─ TCP (remote) → Relay Stargate → Edge container → Gateway
         └─ loopback (optional) → Cloud Proxy (UDS /tmp/universal-protocol/cloud-proxy.sock) → OpenRouter/Anthropic/etc (HTTPS)

Pipeline-tools sidecar (network_mode: "none", read-only) ← shell execution for pipeline steps
RAG Service (UDS /tmp/universal-protocol/rag.sock by default) ← semantic search for pipeline handlers
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
| **Pipeline-tools sidecar** (container, no network) | Hardened Alpine container for shell execution in pipeline steps |
| **RAG Service** (host, UDS default) | Semantic search, file indexing, ChromaDB vector store |
| **Cloud Proxy** (host, UDS default) | Optional cloud API relay (OpenRouter, Anthropic, OpenAI); UDS at `/tmp/universal-protocol/cloud-proxy.sock` |

### Key Design Decisions

- **Network isolation**: Edge containers run with `network_mode: "none"` — zero network access. All communication via Unix sockets.
- **Non-root execution**: Containers run as unprivileged users — no root escalation surface.
- **Privilege separation**: Each Edge runs with minimal capabilities — models are isolated from the host system. Multiple models may share an Edge container but are isolated from all other hosts and the underlying OS.
- **Router-only Master**: Masters have no local Gateway. They orchestrate via Edge and Relay stargates.
- **Structural privacy**: Outbound internet access exists only in the optional cloud proxy. Every other component is local-only or network-isolated. No data leaves the network unless the cloud proxy is explicitly started.
- **Container-per-concern security tiers**: Inference runs in `network_mode: none` edge containers. Tool execution runs in a capability-dropped, read-only sidecar. Cloud access runs in a domain-whitelisted proxy. Each concern gets the minimum privilege it needs — enforced by container policy, not application code.

### Request Flow

1. Client sends request to Master Stargate
2. DecisionEngine selects an Edge (T0/T1/T2 feasibility scoring) or cloud backend
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
  -d '{"model": "consensus-chain-v7", "messages": [{"role": "user", "content": "How does mRNA translation work?"}]}'
```

**Key features:**
- Graph execution with automatic parallelization, loops, and conditional branching
- Explicit object-flow (`stepName.json.field` bindings) — no hidden state
- Automatic dependency resolution from `handler_inputs`
- Built-in retry, timeout, checkpointing, and map/reduce
- OpenAI-compatible — pipelines are just model IDs

### Secure Tooling: The Sidecar Model

Pipelines provide tool-like capabilities without giving models direct system access. Instead of models calling tools, **pipeline handlers** execute actions server-side based on structured model output. The model never directly touches the filesystem, network, or any external service — the pipeline engine mediates every action.

Shell commands run inside a **pipeline-tools sidecar** — a hardened Alpine container that enforces isolation by construction:

```
Pipeline Step (YAML)  →  Handler  →  docker exec pipeline-tools sh -c "..."
                                      ├── --network none
                                      ├── --read-only (workspace mounted ro)
                                      ├── --cap-drop ALL --security-opt no-new-privileges
                                      ├── --user 1000:1000 (non-root)
                                      └── --pids-limit 64, --memory 256m
```

Commands come from static YAML definitions, not from model output — the model produces structured JSON decisions (e.g., `{"action": "expand_git", "reason": "..."}`), and the engine maps these to pre-defined operations.

| Handler | Action | Isolation |
|---|---|---|
| `shell_v1` | Execute commands in the pipeline-tools sidecar | `network_mode: none`, read-only, capability-dropped |
| `rag_search_v1` | Semantic search against the RAG service | Host-only (UDS or TCP per config) |
| `rag_source_v1` | Fetch full file content from indexed corpus | Host-only (UDS or TCP per config) |
| `assess_loop_v1` | Iterative model-driven decision loop | Engine dispatches actions per JSON decisions |

**Tooling by policy (under construction):** The sidecar establishes a container-per-concern security tier. Today, the sidecar is `network_mode: none` with a static command set. The planned evolution introduces per-pipeline tool whitelists and network policy tiers — a sidecar that needs HTTP access to an approved API runs with outbound traffic restricted to declared domains (the same pattern used by the cloud proxy), while sidecars that only need filesystem access stay fully network-isolated. The pipeline YAML declares what a step needs; the container policy enforces it.

### Shipped Pipelines

| Pipeline | Model ID | Purpose |
|---|---|---|
| **Consensus v7** | `consensus-chain-v7` | Multi-model answer generation with decomposition, domain-specific verification, veto gates, and structured synthesis |
| **Consensus v7.1** | `consensus-chain-v7.1` | v7 + citation enforcement (`assert_then_revise`), uncited-claim filtering, and coherence repair |
| **RAG Answer v1** | `rag-answer` | General-purpose RAG Q&A: calls `rag-context` sub-pipeline for retrieval, then generates grounded answer via phi4 |
| **RAG Context** | `rag-context` | Reusable retrieval sub-pipeline — query rewriting + RRF retrieval, returns context chunks; callable by any pipeline as a service |
| **RAG Journal v3.5** | `journal-agent` | Iterative context gathering via assess loop — RAG search, git log, journal index, then model-driven expansion |

See [Pipeline System README](services/universal-stargate/systems/pipeline/README.md) for architecture and schema reference.

## RAG Service

A **single-pass dense retrieval** RAG service — one ChromaDB collection, one embedding model, cosine similarity over HNSW. At its core this is the "Naive RAG" pattern: embed the query, retrieve top-k nearest chunks, inject into the generation prompt.

The `rag-context` pipeline layers "Advanced RAG" on top: a small model (phi4) rewrites the user question into 1–3 embedding-optimized sub-queries (handling vocabulary mismatch, step-back expansion, and multi-hop decomposition), runs them in parallel, and merges results via reciprocal rank fusion. `rag-answer` wraps `rag-context` to add grounded answer generation from the retrieved context.

- **Indexing**: Markdown, code, PDF (native via `pymupdf4llm`), EPUB/ebook, and plain text — chunked by structure (headers, paragraphs, code blocks, AST for source code). PDF content-hashing (`pdf_hash`) for cross-file deduplication.
- **Search**: Cosine similarity with configurable recency scoring; recency is driven by `published_date` (preferred for research papers) or `indexed_at` timestamp; appropriate recency strategies vary by corpus type and are applied scope-conditionally
- **Corpus scoping**: Named scope registry (`GET /scopes`) with per-scope path roots and tunable retrieval parameters — consumers reference scopes by identifier; `source_prefixes` still available for ad-hoc queries
- **File watching**: Automatic reindexing via inotify with periodic reconciliation
- **Embeddings**: Uses a local embedding model (`bge-m3`) via the Gateway — no external API calls

Config: `~/.rag/config.yaml`. Store: `~/.rag/store/` (ChromaDB persistent data).

## Cloud Proxy (Experimental)

> Functional for text generation. Quality-tier autoselection and browser UI shipped. Vision model routing is implemented but untested. Provider catalog and edge cases under ongoing refinement.

The cloud proxy is a **separate, optional service** that routes requests to cloud API providers (OpenRouter, Anthropic, OpenAI, Google, etc.). It is the **only component in the system with outbound internet access**, making cloud integration a structural security decision rather than a configuration flag.

**Security model:**
- **Isolation by construction**: if the cloud proxy isn't running, no component can make outbound requests — the system is local-only by default
- **Credential containment**: API keys live exclusively in the cloud proxy process; Stargate and edge containers never see them
- **Network boundary**: the proxy communicates with Stargate over loopback only; outbound connections are restricted to declared provider domains
- **Uniform routing**: cloud models appear in `/v1/models` alongside local models and use the same routing infrastructure — no separate API surface

```yaml
# ~/.gateway/cloud-proxy.yaml
providers:
  - provider: openrouter
    api_key_env: OPENROUTER_API_KEY
    max_concurrent: 20
    allow_prefixes:
      - "anthropic/"
      - "openai/"
      - "google/"
```

Cloud models use provider IDs directly (e.g., `anthropic/claude-sonnet-4-20250514`). In pipeline configs, they're just another model alias — the pipeline system doesn't know or care where inference runs.

## Federation

Federation lets a Master Stargate distribute inference across multiple GPU nodes. Each remote node runs a Relay Stargate on the host that bridges to a network-isolated Edge container. The Master routes requests to the best available node based on feasibility scoring (loaded model, GPU capacity, queue depth). Clients still talk to `:9999` — federation is transparent.

## Project Structure

```
universal-llm-gateway/
├── manage                            # Entry point — bootstraps venv, launches TUI
├── services/
│   ├── _universal-llm-gateway/       # Gateway service (container-internal)
│   ├── universal-stargate/           # Stargate service (port 9999)
│   ├── rag/                          # RAG service (UDS default)
│   └── universal_cloud_proxy/        # Cloud proxy service (port 8200)
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
│   ├── consensus/                    # Multi-model consensus (v7, v7.1, v8.0)
│   ├── rag/                          # RAG sub-pipelines (query rewriting, retrieval)
│   ├── answer_v1/                    # RAG-augmented answer pipeline (calls rag-context)
│   ├── modularize/                   # Code modularization pipeline (analyze→critique→finalize→refactor)
│   └── tools/                        # Pseudo-tool handlers (shell, RAG, assess loop)
└── tools/                            # Developer utilities (pipeline viewer, test infra)
```

## License

MIT License — see [LICENSE](LICENSE).

## Author

**krunch3r76** ([@krunch3r76](https://github.com/krunch3r76))
- Issues: [GitHub Issues](https://github.com/krunch3r76/universal-llm-gateway/issues)
