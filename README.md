# Universal LLM Gateway

An OpenAI-compatible inference stack where nothing leaves your hardware. Models run unprivileged inside network-isolated containers (`network_mode: "none"`): no exfiltration, no phoning home, no cloud dependency.

> **Documentation status**: This project is production-used but documentation is under active overhaul. Comprehensive onboarding guides, API references, and subsystem docs are coming in the next few weeks. What follows is an accurate capability summary — expect rough edges and missing detail in the linked docs.

## What it solves

- **Contain untrusted models**: execution runs unprivileged with zero network access
- **One API across many GPU nodes**: route to local + remote machines via federation
- **Multi-model workflows**: pipelines are "virtual models" behind a single `model` name
- **Secure tool-like capabilities**: pipelines perform actions (search, shell, verification) on behalf of models — models never get direct system access
- **Knowledge retrieval**: RAG service with semantic search, knowledge extraction, scoped corpora, and LLM-driven query rewriting
- **Cloud model tool access**: MCP server exposes system capabilities (filesystem, RAG, web search, browser automation) as tools for Anthropic and other cloud model APIs — with per-tool-category security policies
- **Optional cloud routing**: a separate, opt-in cloud proxy isolates all outbound internet access to a single process — if it's not running, outbound traffic is impossible by construction

## Status: Alpha (v0.0.0)

Production-used on single-GPU and multi-node federated deployments. Under active development.

## Capabilities

| Capability | Endpoint | Notes |
|---|---|---|
| Chat completions (SSE streaming) | `POST /v1/chat/completions` | Local, federated, cloud, and pipeline models |
| Embeddings | `POST /v1/embeddings` | Local embedding models (bge-m3) |
| Images (Flux.2) | `POST /v1/images/generations` | Under active development |
| Audio transcription (Whisper) | `POST /v1/audio/transcriptions` | Under active development |
| Model list | `GET /v1/models` | Local, federated, cloud, and pipeline models |
| Model selection | `POST /v1/models/select` | Task-aware three-tier selection with cost budgets |
| Intelligence profiles | `GET /v1/models/{id}/profile` | Per-model capability profiles |
| Health | `GET /health` | |

All endpoints are served by Stargate on `:9999` — the sole client-facing endpoint.

### Roadmap

- [x] **Simplified onboarding** — `./manage` bootstraps environment and launches TUI ([demo](https://krunch3r76.github.io/assets/universal-llm-gateway/measure_demo_02-18-2026_01.mp4))
- [x] **RAG service** — ChromaDB-backed semantic search with file watching, recency scoring, scope registry, knowledge extraction, and article registry
- [x] **Pipeline pseudo-tooling** — prompt-driven tool calling: any model becomes tool-capable without native function-calling support, and adversarial tool invocations are structurally prevented because models produce structured JSON decisions that the engine maps to pre-defined operations — models never call tools directly
- [x] **Consensus pipeline (v7/v7.1)** — multi-model answer generation with decomposition, domain-specific verification, veto, citation enforcement, and structured synthesis
- [x] **Pipeline event observability** — dedicated `/tmp/pipeline-events/` stream with per-step metrics (tokens, duration, call count)
- [x] **Cloud proxy** — OpenRouter/Anthropic/OpenAI/Google integration with quality-tier autoselection, cost-aware routing, configurable provider allow-lists, and browser UI for interactive model selection
- [x] **RAG-augmented routing** — `rag-context` pipeline rewrites queries into embedding-optimized sub-queries, executes parallel retrieval with RRF merge, and returns assembled context chunks; `rag-answer` wraps it with grounded answer generation; `rag-answer-deep` adds iterative refinement; `source_prefixes` and named scopes restrict retrieval to any indexed corpus subset
- [x] **Consultation pipelines** — domain-specialized assistants (researcher, architect, planner, prompt engineer) available as pipeline model IDs
- [x] **Knowledge extraction** — LLM-driven entity, topic, and relation extraction from indexed documents, stored in a property index for hybrid structured+vector search
- [x] **Intelligence profiles** — per-model capability metadata and task-aware model selection with cost budgets
- [x] **MCP server** — internet-facing MCP tool server for Anthropic API integration; exposes filesystem, project browsing, RAG search, web search/fetch, clips, SQLite, and browser automation as model-callable tools; tiered security policies per tool category
- [ ] **MCP: web and OpenAI API support** — extend MCP server to support web-based MCP clients and OpenAI's tool protocol
- [ ] Multi-GPU / tensor parallelism (vLLM)
- [ ] Native VPS deployment tooling (one-command setup)
- [ ] Simplified model onboarding (CLI wizard or web UI)
- [ ] **Documentation overhaul** — comprehensive onboarding, API reference, subsystem guides (in progress)

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
| **RAG Service** (host, UDS default) | Semantic search, file indexing, knowledge extraction, ChromaDB vector store |
| **Cloud Proxy** (host, UDS default) | Optional cloud API relay (OpenRouter, Anthropic, OpenAI, Google); UDS at `/tmp/universal-protocol/cloud-proxy.sock` |
| **MCP Server** (container, port 443) | Internet-facing tool server for cloud model APIs (Anthropic MCP); TLS + bearer auth |

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

`./manage` bootstraps the Python venv, installs dependencies, and launches the Model Manager TUI. Subcommands: `./manage relay` (headless relay), `./manage topology` (show topology), `./manage update` (headless update).

## API Endpoints

OpenAI-compatible. All requests go to Stargate on port **9999**.

### Chat Completions

**`POST /v1/chat/completions`** — Text generation with streaming support, pipeline routing, and automatic model loading.

### Model Intelligence

| Endpoint | Purpose |
|----------|---------|
| `GET /v1/models` | List all available models (local, federated, cloud, pipeline) |
| `GET /v1/models/{id}/profile` | Per-model capability profile |
| `POST /v1/models/select` | Task-aware model selection (requirements, cost budgets, quality tiers) |

### System

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Health check |
| `GET /api/v1/gateways/status` | Gateway status and model distribution |
| `GET /api/v1/monitoring/gateway-states` | Real-time Gateway telemetry |

### Cloud Proxy Passthrough

| Endpoint | Purpose |
|----------|---------|
| `GET /api/models` | Cloud provider model catalog with pricing |
| `POST /api/select` | Task-aware cloud model selection |
| `POST /api/refresh` | Force cloud catalog refresh |

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
- Runtime option overrides via `pipeline_options` in the request body
- Hot-reload of pipeline YAML definitions
- Automatic model selection via `model_ref: "auto"` with `model_requirements`
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
| `generate` | LLM text generation | Via Gateway/cloud |
| `shell_v1` | Execute commands in the pipeline-tools sidecar | `network_mode: none`, read-only, capability-dropped |
| `rag_source_v1` | Fetch full file content from indexed corpus | Host-only (UDS or TCP per config) |
| `assess_loop_v1` | Iterative model-driven decision loop | Engine dispatches actions per JSON decisions |
| `pipeline_call` | Invoke another pipeline as a sub-pipeline | HTTP loopback |
| `select_output` | Choose output from a previous step | In-engine |
| `select_winner` | Select winner from map/reduce outputs | In-engine |

### Shipped Pipelines

| Pipeline | Model ID | Purpose |
|---|---|---|
| **Consensus v7** | `consensus-chain-v7` | Multi-model answer generation with decomposition, domain-specific verification, veto gates, and structured synthesis |
| **Consensus v7.1** | `consensus-chain-v7.1` | v7 + citation enforcement (`assert_then_revise`), uncited-claim filtering, and coherence repair |
| **RAG Answer** | `rag-answer` | General-purpose RAG Q&A: calls `rag-context` sub-pipeline for retrieval, then generates grounded answer |
| **RAG Answer Deep** | `rag-answer-deep` | RAG with iterative refinement for complex queries |
| **RAG Context** | `rag-context` | Reusable retrieval sub-pipeline — query rewriting + RRF retrieval, returns context chunks |
| **Journal Agent** | `journal-agent` | Iterative context gathering via assess loop — RAG search, git log, journal index, then model-driven expansion |
| **Consult** | `consult-researcher`, `consult-architect`, `consult-planner`, `consult-prompt-engineer` | Domain-specialized consultation pipelines |
| **Modularize** | `modularize` | Code modularization: analyze → critique → finalize → refactor |
| **Code Review** | `code-review` | Automated review → validate → merge |
| **Doc Generate** | `doc-generate` | Docstring extraction → draft → review |
| **RAG Extraction** | `rag-extraction` | LLM-driven knowledge extraction for RAG indexing |

See [Pipeline System README](services/universal-stargate/systems/pipeline/README.md) for architecture and schema reference.

## RAG Service

> Scoped documentation: [services/rag/README.md](services/rag/README.md)

A semantic search and knowledge management service backed by ChromaDB. At its core: embed the query, retrieve top-k nearest chunks, inject into the generation prompt.

The `rag-context` pipeline layers advanced retrieval on top: a model rewrites the user question into 1–3 embedding-optimized sub-queries (handling vocabulary mismatch, step-back expansion, and multi-hop decomposition), runs them in parallel, and merges results via reciprocal rank fusion. `rag-answer` wraps `rag-context` with grounded answer generation. `rag-answer-deep` adds iterative refinement.

- **Indexing**: Markdown, code (Python via tree-sitter AST), PDF (native via `pymupdf4llm`), EPUB/ebook, HTML, and plain text — chunked by structure (headers, paragraphs, code blocks, AST for source code)
- **Knowledge extraction**: LLM-driven entity, topic, and relation extraction from indexed chunks; stored in a SQLite property index for hybrid structured+vector search
- **Search**: Cosine similarity with configurable recency scoring and property boost (entities, topics, relations); recency driven by `published_date` (preferred for research papers) or `indexed_at`
- **Corpus scoping**: Named scope registry (`GET /scopes`) with per-scope path roots, retrieval parameters, and union scopes; `source_prefixes` for ad-hoc queries
- **Article registry**: Optional `article_registry.yaml` for citation metadata (title, authors, venue, DOI, published_date)
- **Corpus hints**: Scope-specific vocabulary hints for retrieval tuning
- **File watching**: Automatic reindexing via inotify with periodic reconciliation, pending journal for crash recovery
- **Embeddings**: Uses a local embedding model (`bge-m3`) via the Gateway — no external API calls
- **PDF deduplication**: Content-hash (`pdf_hash`) dedup across files

Config: `~/.gateway/rag.yaml`. Store: `~/.rag/store/` (ChromaDB persistent data).

## Cloud Proxy

> Scoped documentation: [services/universal_cloud_proxy/README.md](services/universal_cloud_proxy/README.md)

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

**Additional features:**
- **Browser UI**: Interactive model selection at the proxy root with task tags, quality tiers, and cost filters
- **Cost-aware routing**: `/catalog/pricing` endpoint exposes per-model pricing for routing decisions
- **Task-aware selection**: `POST /api/select` matches model capabilities to task requirements

## MCP Server (Under Active Development)

> Scoped documentation: [services/mcp-server/README.md](services/mcp-server/README.md)

An internet-facing MCP (Model Context Protocol) server that exposes system capabilities as tools to cloud models. Currently supports the **Anthropic API** (`mcp_servers` parameter); **web-based MCP** and **OpenAI API** support are next.

Runs as a containerized FastAPI service on `:443` with TLS and bearer token auth. Different tool categories carry different security policies — browser automation requires an explicit Compose override that relaxes the container's seccomp profile, while most tools run under Docker's default restrictions.

| Tool Category | Tools | Security Policy |
|---|---|---|
| **Filesystem** | `read_file`, `write_file`, `edit_file`, `delete_file`, `list_files` | Sandboxed to `/data/files` volume; path traversal rejected at code level |
| **Project** | `list_project_files`, `read_project_file`, `search_project` | Read-only mount; only git-tracked files visible |
| **RAG** | `rag_search`, `rag_answer`, `rag_list_scopes` | Routes through Stargate pipelines via `host.docker.internal` |
| **Web** | `web_search`, `web_fetch` | Brave Search API; SSRF guard blocks private/loopback URLs |
| **Clips** | `list_clips`, `read_clip` | Read-only access to bookmarklet-saved content |
| **Context** | `read_todo`, `read_journal`, `list_discoveries`, etc. | Tasks directory; configurable read-only or read-write mount |
| **SQLite** | `sqlite_query`, `sqlite_execute`, `sqlite_list_databases` | Parameterized queries; DROP/PRAGMA blocked by default |
| **Browser** | `browser_navigate`, `browser_get_content`, `browser_click`, `browser_fill`, `browser_screenshot` | **Requires separate Compose override** — relaxes seccomp to allow Firefox's internal namespace syscalls (`clone`, `unshare`, `setns`); no capabilities added; still non-root |

**Security tiers:**
- **Default container**: Docker default seccomp, non-root (`uid 1000`), memory-limited (2GB), bearer token auth, TLS
- **With browser override**: Adds 4 syscalls (`clone`, `clone3`, `unshare`, `setns`) for Playwright Firefox's internal process sandbox. No capability escalation, no filesystem escape — scoped to Firefox's own startup path. To re-tighten: restart without the override
- **Project access**: Read-only volume mount + code-level defense-in-depth (only git-tracked, non-binary files)
- **Web access**: SSRF guard rejects private/loopback; fetch timeouts enforced

## Federation

Federation lets a Master Stargate distribute inference across multiple GPU nodes. Each remote node runs a Relay Stargate on the host that bridges to a network-isolated Edge container. The Master routes requests to the best available node based on feasibility scoring (loaded model, GPU capacity, queue depth). Clients still talk to `:9999` — federation is transparent.

**Features:**
- WebSocket-based real-time telemetry (HTTP polling fallback)
- Single-flight model loading with coalescing and retry
- Orchestration metrics endpoint (`GET /api/v1/federation/orchestration/metrics`)
- Gateway proxy for remote job/model/status inspection
- In-flight inference cancellation (`DELETE /api/v1/federation/inference/{id}`)

## Model Catalog

Models are defined in `config/models/{type}/{engine}/{model-id}.yaml` with structured metadata.

| Model Type | Engine | Examples |
|------------|--------|----------|
| Text LLM | llama-cpp, vLLM | Qwen, Llama, Phi, Mistral |
| Embedding | llama-cpp | bge-m3 |
| Visual | llama-cpp | LLaVA, vision models |
| Audio | Whisper | Transcription |
| Graphics | Diffusers | Flux.2 image generation |
| Translation | CTranslate2 | Multilingual translation |

Each model config includes download source (HuggingFace), SHA256 verification, quantization metadata, parameter count, and capability flags. The TUI (`./manage`) handles model discovery, download, and registration.

## Event Observability

Every major subsystem emits structured JSONL events for debugging and monitoring:

| Event Stream | Path | Scope |
|---|---|---|
| Stargate | `/tmp/stargate-events/current.jsonl` | Routing, federation, proxy lifecycle |
| Pipeline | `/tmp/pipeline-events/current.jsonl` | Per-step metrics, handler execution |
| RAG | `/tmp/rag-events/current.jsonl` | Indexing, search, extraction |
| Cloud Proxy | `/tmp/cloud-proxy-events/current.jsonl` | Provider requests, catalog refreshes |
| Gateway | `/tmp/_universal-gateway-events/current.jsonl` | Worker lifecycle, model loading |

## Project Structure

```
universal-llm-gateway/
├── manage                            # Entry point — bootstraps venv, launches TUI
├── services/
│   ├── _universal-llm-gateway/       # Gateway service (container-internal)
│   ├── universal-stargate/           # Stargate service (port 9999)
│   │   └── systems/                  # Subsystems: pipeline, federation, routing,
│   │                                 #   profiles, transformations, graphics, proxy
│   ├── rag/                          # RAG service (UDS default)
│   ├── universal_cloud_proxy/        # Cloud proxy service (UDS default)
│   └── mcp-server/                   # MCP tool server (port 443, TLS)
├── libs/
│   ├── inference_djinn/              # LLM engines (llama.cpp, vLLM, Whisper, Flux)
│   ├── intelligence_profiles/        # Per-model profiles and selection
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
│   ├── answer_v1/                    # RAG-augmented answer (calls rag-context)
│   ├── consult/                      # Domain-specialized consultation pipelines
│   ├── modularize/                   # Code modularization pipeline
│   └── tools/                        # Pseudo-tool handlers (shell, RAG, assess loop)
├── docs/
│   ├── architecture/                 # System architecture reference
│   └── vision/                       # Design vision documents per subsystem
└── tools/                            # Developer utilities (pipeline viewer, test infra)
```

## Documentation

> **Under active overhaul.** Comprehensive guides and API references are planned for the coming weeks. The existing docs are accurate but incomplete — expect gaps.

| Area | Location | Status |
|------|----------|--------|
| System architecture | [`docs/architecture/`](docs/architecture/overview.md) | Reference-quality |
| Pipeline system | [`services/universal-stargate/systems/pipeline/README.md`](services/universal-stargate/systems/pipeline/README.md) | Reference-quality |
| Federation | [`services/universal-stargate/systems/federation/README.md`](services/universal-stargate/systems/federation/README.md) | Reference-quality |
| RAG service | [`services/rag/README.md`](services/rag/README.md) | New — capability overview |
| Cloud proxy | [`services/universal_cloud_proxy/README.md`](services/universal_cloud_proxy/README.md) | New — capability overview |
| MCP server | [`services/mcp-server/README.md`](services/mcp-server/README.md) | New — capability overview |
| Gateway internals | [`services/_universal-llm-gateway/README.md`](services/_universal-llm-gateway/README.md) | Exists |
| Stargate internals | [`services/universal-stargate/README.md`](services/universal-stargate/README.md) | Exists |
| Event contracts | [`docs/event-contracts.md`](docs/event-contracts.md) | Reference |
| Setup / contributing | [`CONTRIBUTING.md`](CONTRIBUTING.md) | Exists |
| Onboarding guide | — | **Pending** |
| API reference | — | **Pending** |
| Deployment guide | — | **Pending** |
| Configuration reference | — | **Pending** |

## License

MIT License — see [LICENSE](LICENSE).

## Author

**krunch3r76** ([@krunch3r76](https://github.com/krunch3r76))
- Issues: [GitHub Issues](https://github.com/krunch3r76/universal-llm-gateway/issues)
