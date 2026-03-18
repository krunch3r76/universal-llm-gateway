# Universal LLM Gateway

A privacy-first, OpenAI-compatible inference stack built on three principles:

1.  **Hardened by Construction.** Your data never leaves your hardware by default. Models run unprivileged in **network-isolated Docker containers** (`network_mode: "none"`). There is no telemetry, no phoning home, and no exfiltration path. The *only* way to the internet is through an optional, single-process cloud proxy. If it's off, the stack is structurally air-gapped.

2.  **A Safer Model for Tools.** Instead of giving LLMs direct tool-execution authority, we use **prompt-driven pseudo-tooling**. The model outputs a structured decision (e.g., JSON), and the engine maps it to a pre-defined operation executed in a hardened sidecar. The model *chooses*, it never *executes*. Adversarial tool use is structurally prevented.

3.  **Minimal Network Surface.** Most communication happens over Unix domain sockets. By default, there is only **one client-facing TCP port (`:9999`)**. The core inference engine is never exposed to any network.

---

> **Documentation status**: This project is production-used but documentation is under active overhaul. Comprehensive onboarding guides, API references, and subsystem docs are coming in the next few weeks. What follows is an accurate capability summary — expect rough edges and missing detail in the linked docs.

## What it solves

- **Privacy by default**: Inference and tool execution stay on your hardware. Outbound internet exists only in an optional, single-process cloud proxy — if it's off, nothing can call out.
- **Contain untrusted models**: Execution runs unprivileged with zero network access. Edge containers are isolated by construction.
- **Prevent adversarial tool use**: With **pipeline pseudo-tooling**, models emit structured decisions that our engine maps to pre-defined operations in a hardened sidecar. Models never invoke tools directly, making prompt injection attacks that target tool execution structurally impossible.
- **Provide hardened traditional pathways**: **Native tool-calling** (OpenAI/Anthropic-style) is planned and will execute in the same hardened sidecar. **MCP** is under active development for cloud model APIs with tiered security per tool category. These pathways are optional and hardened.
- **Unify multi-GPU nodes**: Route to local and remote machines via federation through a single client endpoint.
- **Build multi-model workflows**: Pipelines are "virtual models" behind a single `model` name. RAG, consensus, and consultation pipelines ship today.
- **Enable knowledge retrieval**: Perform local RAG with semantic search, knowledge extraction, scoped corpora, and LLM-driven query rewriting.
- **Isolate cloud access**: A separate cloud proxy contains all outbound API traffic and credentials, which never leave that single process.

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

All endpoints are served by Stargate on `:9999` — the **sole client-facing endpoint** in the default setup.

### Roadmap

- [x] **Simplified onboarding** — `./manage` bootstraps environment and launches TUI ([demo](https://krunch3r76.github.io/assets/universal-llm-gateway/measure_demo_02-18-2026_01.mp4))
- [x] **RAG service** — ChromaDB-backed semantic search with file watching, recency scoring, scope registry, knowledge extraction, and article registry
- [x] **Pipeline pseudo-tooling** — Prompt-driven tool calling where any model becomes tool-capable. Models produce structured JSON decisions that the engine maps to pre-defined operations, structurally preventing adversarial tool invocation.
- [x] **Consensus pipeline (v7/v7.1)** — Multi-model answer generation with decomposition, domain-specific verification, veto, citation enforcement, and structured synthesis
- [x] **Pipeline event observability** — Dedicated `/tmp/pipeline-events/` stream with per-step metrics (tokens, duration, call count)
- [x] **Cloud proxy** — OpenRouter/Anthropic/OpenAI/Google integration with quality-tier autoselection, cost-aware routing, configurable provider allow-lists, and browser UI for interactive model selection
- [x] **RAG-augmented routing** — `rag-context` pipeline implements corpus-grounded multi-stage retrieval: scope classification → facet prediction → second-pass facet refinement → multi-query rewriting → two-pool hybrid retrieval (dense+sparse Pool A, named-entity OR-query Pool B) → RRF merge with source habituation → facet-guided LLM reranking; `rag-answer` adds grounded answer generation; `rag-answer-deep` adds iterative refinement; `source_prefixes` and named scopes restrict retrieval to any indexed corpus subset
- [x] **Consultation pipelines** — Domain-specialized assistants (researcher, architect, planner, prompt engineer) available as pipeline model IDs
- [x] **Knowledge extraction** — LLM-driven entity, topic, and relation extraction from indexed documents, stored in a property index for hybrid structured+vector search
- [x] **Intelligence profiles** — Per-model capability metadata and task-aware model selection with cost budgets
- [x] **MCP server** — Internet-facing MCP tool server for Anthropic API integration; exposes filesystem, project browsing, RAG search, web search/fetch, clips, SQLite, and browser automation as model-callable tools; tiered security policies per tool category
- [ ] **MCP: web and OpenAI API support** — Extend MCP server to support web-based MCP clients and OpenAI's tool protocol
- [ ] **Native tool-calling** — OpenAI/Anthropic-style tool use, executed in the same hardened sidecar with allow-list policy (no new trust surface)
- [ ] Multi-GPU / tensor parallelism (vLLM)
- [ ] Native VPS deployment tooling (one-command setup)
- [ ] Simplified model onboarding (CLI wizard or web UI)
- [ ] **Documentation overhaul** — Comprehensive onboarding, API reference, subsystem guides (in progress)

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

### Deployment: Docker, Containers, and Minimal TCP

The stack is **container-native** and exposes **the fewest possible TCP ports**. Most communication is local over **Unix domain sockets**.

| Component | Where it runs | Network | Exposed Ports |
|------|----------------|---------|---------|
| **Master Stargate** | Host process (or container) | Binds to host | **`:9999`** — The only client-facing TCP port. |
| **Edge Node** | **Docker container** per GPU | `network_mode: "none"` | **None.** The host talks to it over a Unix socket. |
| **Pipeline-tools Sidecar** | **Docker container** (Alpine) | `network_mode: "none"` | **None.** Invoked via `docker exec`. No listening ports. |
| **RAG Service** | Host process or container | Listens on **UDS** by default | **None** (unless manually configured for TCP).|
| **Cloud Proxy** | Host process or **container** | Listens on **UDS** by default | **None.** Communicates with Stargate via UDS. Optional. |
| **Relay Stargate** (remote) | Host process on GPU machine | Binds to host | **`:9999`** on that host for Master → Relay traffic only. |
| **MCP Server** (optional)| **Docker container** | TLS on 443 | **`:443`** if you run MCP for cloud model tools. |

**Minimal exposure by design:**
-   **Single Machine**: Expose **`:9999`** (Stargate). That’s it.
-   **Federation**: Expose **`:9999`** on the master and on each remote host (for the Relay).
-   **With MCP**: Optionally add `:443` for the MCP server container.

The **Gateway (inference engine)** is never exposed. It listens on `localhost:9998` *inside* the Edge container, which has no network. Stargate reaches it over the container's internal loopback. Inference is always behind the socket boundary.

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
| **Cortex API** (container, port 8300) | REST gateway to cortex.db (knowledge graph) and todos.db; sole access path for agents |

### Key Design Decisions

- **Privacy-First**: Your data stays on your hardware unless you explicitly opt in by running the cloud proxy. No telemetry. No phoning home.
- **Hardening by Construction**: Network isolation (`network_mode: "none"`), non-root execution, privilege separation, and read-only mounts are enforced by container policy, not application logic.
- **Novel Tooling First**: The default model, pseudo-tooling, eliminates direct model-to-tool invocation. Traditional pathways like native tool-calling and MCP are optional, hardened alternatives that reuse the same security primitives.
- **Container-per-Concern**: Inference, tool execution, and cloud access run in separate, purpose-built containers. Each component gets the minimum privilege required for its job.

### Request Flow

1. Client sends request to Master Stargate (`:9999`).
2. DecisionEngine selects a local Edge, remote Edge, or cloud backend.
3. Master loads the model on the target Edge if needed (via Unix socket or remote relay).
4. Master forwards the inference request through the same path.
5. Response streams back: Worker → Gateway → Edge → (Relay) → Master → Client.

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

Pipelines are **virtual models** that orchestrate multiple real models behind a single `model` name. Use a pipeline ID as the `model` parameter in any standard API request.

```bash
curl -X POST http://localhost:9999/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "consensus-chain-v7", "messages": [{"role": "user", "content": "How does mRNA translation work?"}]}'
```

**Key features:**
- Graph execution with automatic parallelization, loops, and conditional branching
- Explicit object-flow (`stepName.json.field` bindings) without hidden state
- Automatic dependency resolution from `handler_inputs`
- Built-in retry, timeout, checkpointing, and map/reduce
- Hot-reload of pipeline YAML definitions
- OpenAI-compatible — pipelines are just model IDs

### Tooling: Structurally Safe by Default

The stack's default tooling method is novel and designed to prevent prompt injection attacks from escalating to arbitrary code execution. Traditional, hardened pathways are optional.

| Pathway | Status | How it works | Hardening |
|--------|--------|--------------|-----------|
| **Pipeline Pseudo-Tooling** | **Shipped** | A model emits a structured decision (JSON). The engine maps it to a **pre-defined** operation from a static YAML file. A **sidecar** runs the command. The model never calls the tool; it just chooses an action. | Commands come from static YAML, not model output. Sidecar: `network_mode: none`, read-only mounts, capability-dropped, non-root, memory-limited. |
| **Native Tool-Calling** (OpenAI/Anthropic) | **Not yet** | Models would receive a tool schema and return tool calls. The gateway would execute them in the same sidecar under the same policies. | **Same sidecar**, same allow-list discipline — no new trust surface. |
| **MCP** (Cloud Models) | **Active Development** | An internet-facing MCP server exposes tools (filesystem, RAG, web, browser) to Anthropic and others. | Tiered security per tool category. Execution is isolated. |

**Why pseudo-tooling?** It fundamentally changes the trust boundary. The model never generates shell commands or API calls. It returns a bounded choice, and our engine maps that choice to an operation you've already defined. The attack surface shrinks from "arbitrary execution" to "picking from a list."

### The Sidecar: Hardened Execution Environment

All shell and filesystem actions run inside the **pipeline-tools sidecar**, a dedicated Alpine container with strict, non-negotiable limits:

```
Pipeline Step (YAML)  →  Handler  →  docker exec pipeline-tools sh -c "..."
                                      ├── --network none
                                      ├── --read-only (workspace mounted ro)
                                      ├── --cap-drop ALL --security-opt no-new-privileges
                                      ├── --user 1000:1000 (non-root)
                                      └── --pids-limit 64, --memory 256m
```

The model never writes the command string. This same hardened environment will execute native tool calls when they are supported.

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

A local semantic search and knowledge management service backed by ChromaDB, with an LLM-driven pipeline layer that implements corpus-grounded multi-stage retrieval.

**What makes it different from standard RAG:** Most systems either do naive vector search on the raw query (missing lexical variants) or use unconstrained LLM rewriting that hallucinates terms not in the corpus. This system validates every candidate term against actually-indexed vocabulary via the property index *before* any LLM call. Rewrites only ever include vocabulary that exists in the corpus.

**Corpus-grounded query rewriting**: Queries are decomposed into retrieval facets, refined through a second LLM pass to surface deeper named entities (e.g. `Zettelkasten`, `NEPOMUK`, `PIMO` from a `personal_knowledge_management` facet), then expanded into multiple embedding-optimized sub-queries with a HyDE passage — all constrained to validated corpus vocabulary.

**Two-pool hybrid retrieval**: Pool A runs standard dense+sparse hybrid (ChromaDB + BM25). Pool B constructs OR-joined FTS5 named-entity queries per facet with `sparse_only=True`, bypassing dense embedding for exact-match retrieval of proper nouns and technical terms that embedding models tend to dilute. Results merge via RRF with source habituation to ensure coverage breadth over source depth.

**Facet-guided reranking**: A sliding-window LLM reranker receives the retrieval facets explicitly and re-orders candidates to prefer chunks matching multiple facets simultaneously over generic documents that merely mention the domain.

- **Indexing**: Markdown, code (Python via tree-sitter AST), PDF (native via `pymupdf4llm`), EPUB, HTML, and plain text, chunked by structure.
- **Knowledge Extraction**: Extracts entities, topics, and relations from documents to power hybrid structured+vector search.
- **Scope Vocabulary Registers**: Terms are LLM-classified into `academic`, `practitioner`, and `specification` registers per scope, so rewrites target the right vocabulary for each query type.
- **Corpus Scoping**: Query specific subsets of your data using a named scope registry or ad-hoc `source_prefixes`.
- **File Watching**: Automatically re-indexes changed files via inotify.
- **Local Embeddings**: Uses a local model via the Gateway. No external API calls.
- **PDF Deduplication**: Content-hash (`pdf_hash`) dedup across files.
- **Post-Index Enrichment**: After corpus indexing, operator-driven workflows rebuild corpus hints from the property index, LLM-classify scope vocabulary registers, and tag bibliography/noise chunks. Watermarks track enrichment freshness; `post_index_enforcement: strict` blocks search until enrichment is current. See [Post-Index Refresh Runbook](tasks/runbooks/rag-post-index-refresh.md).

Config: `~/.gateway/rag.yaml`. Store: `~/.rag/store/`. Enrichment artifacts: `~/.rag/corpus_hints.yaml`, `~/.rag/scope_vocabulary.yaml`.

## Cloud Proxy

> Scoped documentation: [services/universal_cloud_proxy/README.md](services/universal_cloud_proxy/README.md)

The cloud proxy is the **only** component allowed to reach the internet, and it is **entirely optional**. If you don't run it, nothing can call out. It routes requests to providers like OpenRouter, Anthropic, and OpenAI, and is the single process where API keys and outbound traffic are contained.

- **Isolation by Construction**: The system is local-only by default. Running the proxy is an explicit choice to enable outbound traffic.
- **Credential Containment**: API keys live exclusively in the cloud proxy process. They are never seen by Stargate or the inference containers.
- **Network Boundary**: The proxy communicates with Stargate over a local Unix domain socket.
- **Uniform Routing**: Cloud models appear in `/v1/models` alongside local models and use the same routing infrastructure.

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
The proxy also includes a browser UI for interactive model selection, cost-aware routing, and task-aware selection logic.

## MCP Server (Under Active Development)

> Scoped documentation: [services/mcp-server/README.md](services/mcp-server/README.md)

**Traditional tooling, hardened.** The MCP server provides the optional pathway for standard tool-calling to cloud models (Anthropic today; web and OpenAI next). It exposes tools—filesystem, RAG, web search, browser automation—via the Model Context Protocol, applying the same privacy and hardening mindset.

It runs as a containerized FastAPI service on `:443` with TLS and bearer token auth. Each tool category has a distinct security policy, allowing you to choose what to expose.

| Tool Category | Tools | Security Policy |
|---|---|---|
| **Filesystem** | `read_file`, `write_file`, `edit_file`, etc. | Sandboxed to `/data/files` volume; path traversal prohibited |
| **Project** | `list_project_files`, `read_project_file`, `search_project_files`, `project(...)` | Mount under `PROJECT_ROOT`; list/search default to git-tracked only — `include_untracked=True` for `tmp/` and other gitignored trees |
| **RAG** | `rag_search`, `rag_answer`, etc. | Routes through Stargate pipelines via `host.docker.internal` |
| **Web** | `web_search`, `web_fetch` | Brave Search API; SSRF guard blocks private/loopback URLs |
| **Browser** | `browser_navigate`, `browser_click`, etc. | **Requires seccomp override** to allow Firefox's internal sandboxing syscalls; no new capabilities are added |

This tiered security model ensures that enabling powerful tools like browser automation is an explicit, reversible decision.

## Federation

Federation lets a Master Stargate distribute inference across multiple GPU nodes. Each remote node runs a Relay Stargate on the host that bridges to a network-isolated Edge container. The Master routes requests to the best available node based on GPU capacity, loaded models, and queue depth. The client experience is unchanged—all requests go to the single Master Stargate at `:9999`.

- WebSocket-based real-time telemetry with HTTP polling fallback.
- Single-flight model loading with request coalescing.
- Metrics endpoint (`GET /api/v1/federation/orchestration/metrics`).
- In-flight inference cancellation (`DELETE /api/v1/federation/inference/{id}`).

## Model Catalog

Models are defined in `config/models/{type}/{engine}/{model-id}.yaml` with structured metadata, including download source, SHA256 checksum, and capability flags. The TUI (`./manage`) handles model discovery, download, and registration.

| Model Type | Engine | Examples |
|------------|--------|----------|
| Text LLM | llama-cpp, vLLM | Qwen, Llama, Phi, Mistral |
| Embedding | llama-cpp | bge-m3 |
| Visual | llama-cpp | LLaVA, vision models |
| Audio | Whisper | Transcription |
| Graphics | Diffusers | Flux.2 image generation |
| Translation | CTranslate2 | Multilingual translation |

## Event Observability

All services publish structured events to the centralized **Event Service** (SQLite-backed, Docker container). Query via CLI or MCP tool:

```bash
scripts/query-events --op recent-failures --limit 10
scripts/query-events --op pipeline-trace --execution-id ID
scripts/query-events --op noise-profile --minutes 5
scripts/query-events --sql "SELECT signal, COUNT(*) c FROM events GROUP BY signal ORDER BY c DESC LIMIT 20"
scripts/query-events --subscribe --filter signal=pipeline.*   # live WebSocket
```

MCP agents use `query_observability` for the same queries. See [`docs/event-service.md`](docs/event-service.md) for the full API.

| Publisher | Source field | Key signals |
|---|---|---|
| Stargate | `stargate` | `request.*`, `federation.*`, `pipeline.*` |
| Gateway | `gateway` | `gateway.resource.*`, `model.*` |
| RAG | `rag` | `rag.started`, `rag.watch.*`, `rag.search.*` |
| Cloud Proxy | `cloud-proxy` | `cloud.proxy.*`, `mcp.adapter.*` |
| MCP Server | `mcp-server` | `mcp.request.*`, `mcp.tool.*`, `mcp.pipeline.consult.*` |

## Project Structure

```
universal-llm-gateway/
├── manage                            # Entry point — bootstraps venv, launches TUI
├── services/
│   ├── _universal-llm-gateway/       # Gateway service (container-internal)
│   ├── universal-stargate/           # Stargate service (port 9999)
│   │   └── systems/                  # Subsystems: pipeline, federation, routing,..
│   ├── rag/                          # RAG service (UDS default)
│   ├── universal_cloud_proxy/        # Cloud proxy service (UDS default)
│   ├── mcp-server/                   # MCP tool server (port 443, TLS)
│   └── cortex-api/                   # Cortex knowledge system API (port 8300, mcp-network)
├── libs/                             # Shared libraries
├── scripts/
│   └── model_manager/                # TUI application (Textual, MVC)
├── config/                           # Model catalog, templates, stargate configs
├── docker/                           # Dockerfiles, Compose configs, build scripts
├── pipelines/                        # Shipped pipeline definitions
├── docs/                             # System architecture and vision docs
└── tools/                            # Developer utilities
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
| Cortex API | [`services/cortex-api/openapi.yaml`](services/cortex-api/openapi.yaml) | OpenAPI spec |
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