# Universal LLM Gateway

Run models on your own hardware. Nothing phones home, nothing leaks out. Models sit in network-isolated containers (`network_mode: "none"`), there's one client-facing port (`:9999`), and the whole stack is structurally air-gapped by default.

That's the starting point — a private, self-hosted gateway to local models and pipelines.

When local isn't enough, flip on the cloud proxy. Now your pipelines can reach out to cloud models for the heavy lifting while everything else stays on your machine. The proxy is the single path to the internet. Kill it and you're air-gapped again.

Take it further: bring in frontier models through MCP and they can drive your local pipelines, query your RAG corpora, use your tools — all grounded in Cortex, a shared substrate where humans and AI agents build on each other's work across sessions.

---

## Architecture

Four layers, each building on the one below:

```
Layer 3: Coordination    Agent bus, boot system, MCP (Model Context Protocol) tool surface
                         Multiple agents share state, hand off work,
                         and improve the boot that initializes them.
                         ┌─────────────────────────────────────────┐
Layer 2: Belief Revision │ AGM-compliant belief revision —          │
                         │ entrenchment ordering, supersession,     │
                         │ contradiction detection, spreading       │
                         │ activation, Dream State consolidation.   │
                         └─────────────────────────────────────────┘
Layer 1: Persistent      Cortex knowledge graph — entities,
         Memory          assertions, temporal bounds, hybrid search,
                         session edges, enrichment. Agents remember
                         across sessions.
                         ┌─────────────────────────────────────────┐
Layer 0: Inference       │ Stargate — route to local (llama.cpp,   │
         Routing         │ vLLM), cloud (Anthropic, xAI, OpenAI,   │
                         │ Google, OpenRouter), and federated GPU   │
                         │ nodes. OpenAI-compatible + provider-     │
                         │ native APIs on a single endpoint (:9999) │
                         └─────────────────────────────────────────┘
```

Layer 0 alone is a fully functional model gateway — no cloud credentials, no frontier access, no external dependencies. Just local models on your hardware. Cortex (Layers 1–3) activates as a unit: persistent memory, belief revision, and multi-agent coordination come together or not at all.

### Request Flow

```
Client → Master Stargate:9999 (sole client-facing endpoint)
         ├─ Unix socket → Edge container (network_mode: "none") → Gateway → Worker (local inference)
         ├─ TCP (remote) → Relay Stargate → Edge container → Gateway (federated inference)
         ├─ UDS → Cloud Proxy → Anthropic/xAI/OpenAI/Google/OpenRouter (cloud inference)
         └─ UDS → Cortex API, RAG, Agent Bus, Event Service (cognitive services)
```

## Status: Alpha

Production-used on single-GPU and multi-node federated deployments. Under active development.

## Quick Start

```bash
git clone https://github.com/krunch3r76/universal-llm-gateway.git
cd universal-llm-gateway
./manage
```

`./manage` bootstraps the Python venv, installs dependencies, and launches the TUI. All services — inference, memory, and coordination — start and stop through `./manage`.

## Subsystems

### Stargate (Inference Routing — Layer 0)

The sole client-facing endpoint, Stargate routes requests to local models, federated GPU nodes, and cloud providers through a single OpenAI-compatible API.

| Capability | Endpoint |
|---|---|
| Chat completions (SSE — Server-Sent Events — streaming) | `POST /v1/chat/completions` |
| Embeddings | `POST /v1/embeddings` |
| Model list (local + cloud + pipeline) | `GET /v1/models` |
| Model selection (task-aware, cost budgets) | `POST /v1/models/select` |
| Provider-native APIs (Anthropic, xAI, OpenAI) | `POST /api/v1/providers/{provider}/...` |
| Health | `GET /health` |

**Pipelines** are virtual models that orchestrate multiple real models behind a single `model` name. They support graph execution with automatic parallelization, loops, conditional branching, retry, and hot-reload. Shipped pipelines include consensus (multi-model verification with veto gates), RAG answer (corpus-grounded retrieval + generation), consultation (domain-specialized assistants), and code review.

**Federation** distributes inference across multiple GPU nodes. It provides WebSocket telemetry, single-flight model loading, and automatic routing based on GPU capacity and queue depth. The client experience remains unchanged — all requests go to `:9999`.

**Hardening**: Edge containers run with `network_mode: "none"`, non-root, capability-dropped, and memory-limited. The Gateway (inference engine) is never exposed to any network. Pipeline tool execution runs in a separate hardened sidecar.

### Agent Bus (Coordination — Layer 3)

Structured inter-agent messaging with threads, turns, read/unread tracking, and thread lifecycle management. Agents post directives, review specifications, hand off work, and close threads with summaries. Session identity and provenance are tracked across all coordination activity.

### MCP Server (Tool Surface)

The entry point for outside agents to connect into the stack. Frontier models and external agents initiate an MCP connection and get access to 30+ tools spanning Cortex, file I/O (two sandboxes), RAG, local pipelines, service lifecycle, observability, agent bus, browser automation, and web search.

Runs as a containerized FastAPI service on `:443` with TLS and bearer auth. Each tool category has a distinct security policy.

| Tool Category | Examples |
|---|---|
| Cortex | Entity/assertion CRUD, search, activate, supersede, impact, tags, edges, journal |
| File I/O | Read/write across `cortex` and `workspaces` sandboxes with markdown section ops |
| RAG | Semantic search, answer generation, article upsert, scope routing |
| Frontier models | `team_generate(agent=...)` for persona consults; `frontier_generate(model=...)` for raw provider-native reasoning and synthesis |
| Infrastructure | Service lifecycle (`manage`), observability queries, pipeline execution |

### RAG (Retrieval)

Local semantic search backed by ChromaDB with corpus-grounded multi-stage retrieval. Query rewriting validates every candidate term against the actually-indexed vocabulary before any LLM call. Two-pool hybrid retrieval (dense+sparse Pool A, named-entity OR-query Pool B) with RRF (Reciprocal Rank Fusion) merge and facet-guided LLM reranking.

Indexes Markdown, PDF, EPUB, HTML, and plain text. Code indexing (Python via tree-sitter AST) is built but not yet active. Knowledge extraction produces entities, topics, and relations for hybrid structured+vector search. File watching auto-indexes changes.

### Cortex (Persistent Memory)

> Scoped documentation: `services/cortex-api/openapi.yaml`

Cortex is a graph-native knowledge system for persistent, formally-structured belief revision. It stores what agents and humans know — and tracks what changes, when, and why.

**Knowledge graph**: Entities (people, decisions, documents, legal matters, services) connected by typed edges. Assertions carry confidence levels (`confirmed` / `believed` / `suspected` / `hypothesized`), temporal bounds, derivation provenance, and entrenchment scores.

**Belief revision** — AGM (Alchourrón-Gärdenfors-Makinson) compliant (25/25 postulate tests, see [compliance report](docs/agm-compliance-report.md)):
- Immutable revisions with atomic supersession — old belief closes, new belief opens, supersession chain preserved
- Mutable tag pointers for named belief states (`current`, `approved`, `disputed`)
- Hybrid retrieval: FTS5 (SQLite Full-Text Search 5) + vector search with CombMAX score fusion
- Spreading activation over reasoning edges with hub suppression
- Impact analysis (transitive dependency cascade) and write-path contradiction detection
- Safety-hardened Dream State consolidation with circuit breakers and dry-run default
- `cortex://` URI addressing with tag and revision pinning

**Human and agent context share the same structure**: entities, confidence taxonomy, and supersession mechanics apply equally to a human's life context and an agent's observations. Provenance distinguishes contributions, not structural privilege.

**Operational note**: The full cognitive stack — enrichment, belief revision, Dream State consolidation, boot narrative synthesis — currently requires frontier-quality reasoning. This is served by the optional cloud proxy or high-end local hardware. The graph never moves; only the inference call does.

Agents access Cortex through MCP tools, which relay to the Cortex REST API over UDS.

### Event Observability

Centralized event store (SQLite-backed) with structured queries, named operations, and real-time WebSocket subscriptions. All services publish events. Session-scoped defaults anchor queries to the current Stargate session.

```bash
scripts/query-events --op recent-failures --limit 10
scripts/query-events --op pipeline-trace --execution-id ID
scripts/query-events --op noise-profile --minutes 5
```

## Design Philosophy

**Privacy by construction.** Data stays on your hardware unless you explicitly run the cloud proxy. No telemetry, no phoning home. Container policy enforces network isolation, not application logic.

**Agents as co-participants.** Agents do not merely execute instructions — they contribute observations, revise beliefs, seed reasoning edges, and improve the boot that initializes future sessions. The knowledge graph is a shared epistemic workspace where human and AI contributions are structurally equal, distinguished only by provenance.

**Self-evolving boot.** Each session's agent-seeded observations accumulate entrenchment and surface in future boots when contextually relevant. The system improves through use rather than manual maintenance. Salience scoring, spreading activation, and hybrid search ensure the most relevant context surfaces first.

**Belief revision as first-class primitive.** Supersession, not deletion. Entrenchment ordering, not FIFO. Contradiction detection at write time. AGM compliance. When evidence changes, the system revises beliefs formally — lower-entrenchment beliefs contract first, revisions make minimal changes, and the full dependency cascade remains traceable.

## Components

| Component | Role | Deployment |
|---|---|---|
| **Stargate** (`:9999`) | Client endpoint, routing, federation orchestration | Host process |
| **Gateway** | Inference engine, worker lifecycle, model loading | Container (`network_mode: "none"`) |
| **Cortex API** | AGM-compliant knowledge graph — entities, assertions, belief revision, session edges. REST gateway over UDS; sole access path for agents. See [compliance report](docs/agm-compliance-report.md). | Host subprocess (UDS) |
| **Agent Bus** | Inter-agent messaging | Host subprocess (UDS) |
| **RAG Service** | Semantic search, indexing, knowledge extraction | Host process (UDS) |
| **Cloud Proxy** | Cloud API relay (optional) | Container (UDS) |
| **MCP Server** | Tool server for agents | Container (`:443`, TLS) |
| **Event Service** | Centralized event store | Host subprocess (UDS) |

Managed via `./manage` (TUI). Config at `~/.gateway/stargate.yaml`.

## Model Support

| Type | Engine | Examples |
|---|---|---|
| Text LLM | llama-cpp, vLLM | Qwen, Llama, Phi, Mistral |
| Embedding | llama-cpp | bge-m3, qwen3-embedding |
| Visual | llama-cpp | Gemma 4, LLaVA |
| Audio | Whisper | Transcription |
| Graphics | Diffusers | Flux.2 |
| Cloud | Anthropic, xAI, OpenAI, Google, OpenRouter | Claude, Grok, GPT, Gemini |

## For AI Agents

If you are an agent encountering this repo for the first time:

1. **Boot**: `cortex_boot(agent="your_agent")` — returns session state, life context, continuation, and operational context
2. **Tool reference**: `docs/tool-reference.md` — all MCP tool signatures, sandbox routing, dispatch catalog
3. **Cortex orientation**: `.cursor/rules/cortex-orientation.mdc` — access patterns, confidence taxonomy, agent responsibilities
4. **Feature registry**: `docs/cursor/cortex-registry.md` — belief revision primitives and operational extensions, classification guide
5. **Boot sequence**: `notes/system/shared/boot-sequence.md` (cortex sandbox) — boot flow and on-demand modules

## Project Structure

```
universal-llm-gateway/
├── manage                            # Entry point — bootstraps venv, launches TUI
├── services/
│   ├── _universal-llm-gateway/       # Gateway (container-internal inference)
│   ├── universal-stargate/           # Stargate (port 9999, routing + orchestration)
│   │   └── systems/                  # Subsystems: pipeline, federation, routing
│   ├── rag/                          # RAG service (UDS)
│   ├── universal_cloud_proxy/        # Cloud proxy (UDS, optional)
│   ├── mcp-server/                   # MCP tool server (port 443, TLS)
│   ├── event-service/                # Event service entry point
│   └── agent-bus/                    # Agent bus entry point
├── libs/
│   ├── cortex_store/                 # Cortex knowledge graph (FastAPI + SQLite)
│   ├── agent_bus_store/              # Agent bus (FastAPI + SQLite)
│   ├── event_store/                  # Event service (FastAPI + SQLite)
│   └── ...                           # transport_utils, model_id, protocol, etc.
├── pipelines/                        # Shipped pipeline definitions
├── config/                           # Model catalog, stargate configs
├── docs/                             # Architecture, event contracts, research
│   └── cursor/                       # Agent-facing reference (feature registry)
└── scripts/                          # CLI tools, model manager TUI
```

## Documentation

| Area | Location |
|---|---|
| System architecture | [`docs/architecture/`](docs/architecture/overview.md) |
| Pipeline system | [`services/universal-stargate/systems/pipeline/README.md`](services/universal-stargate/systems/pipeline/README.md) |
| Federation | [`services/universal-stargate/systems/federation/README.md`](services/universal-stargate/systems/federation/README.md) |
| RAG service | [`services/rag/README.md`](services/rag/README.md) |
| MCP server | [`services/mcp-server/README.md`](services/mcp-server/README.md) |
| Event contracts | [`docs/event-contracts.md`](docs/event-contracts.md) |
| Cortex feature registry | [`docs/cursor/cortex-registry.md`](docs/cursor/cortex-registry.md) |
| AGM compliance | [`docs/agm-compliance-report.md`](docs/agm-compliance-report.md) |

## License

MIT License — see [LICENSE](LICENSE).

## Author

**krunch3r76** ([@krunch3r76](https://github.com/krunch3r76))
- Issues: [GitHub Issues](https://github.com/krunch3r76/universal-llm-gateway/issues)
