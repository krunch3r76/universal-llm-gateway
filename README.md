# Universal LLM Gateway

Cognitive infrastructure for persistent AI agents — model routing, graph-native memory, belief revision, and multi-agent coordination on a single self-hosted stack.

Your data stays on your hardware. Models run in network-isolated containers. The only way to the internet is through an optional, single-process cloud proxy. If it's off, the stack is structurally air-gapped.

A developer who only needs inference routing gets a production-ready model gateway. The full stack is a cognitive platform where humans and AI agents share persistent state, revise beliefs when evidence changes, and coordinate across sessions.

---

## Architecture

Four layers, each building on the one below:

```
Layer 3: Coordination    Agent bus, boot system, MCP tool surface
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

Each layer is independently useful. Layer 0 alone is a fully functional model gateway. Layers 0–1 add persistent memory. The full stack (0–3) is a cognitive platform.

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

`./manage` bootstraps the Python venv, installs dependencies, and launches the TUI. All services — inference, memory, coordination — start and stop through `./manage`.

## Subsystems

### Stargate (Inference Routing — Layer 0)

The sole client-facing endpoint. Routes requests to local models, federated GPU nodes, and cloud providers through a single OpenAI-compatible API.

| Capability | Endpoint |
|---|---|
| Chat completions (SSE streaming) | `POST /v1/chat/completions` |
| Embeddings | `POST /v1/embeddings` |
| Model list (local + cloud + pipeline) | `GET /v1/models` |
| Model selection (task-aware, cost budgets) | `POST /v1/models/select` |
| Provider-native APIs (Anthropic, xAI, OpenAI) | `POST /api/v1/providers/{provider}/...` |
| Health | `GET /health` |

**Pipelines** are virtual models that orchestrate multiple real models behind a single `model` name. Graph execution with automatic parallelization, loops, conditional branching, retry, and hot-reload. Shipped pipelines include consensus (multi-model verification with veto gates), RAG answer (corpus-grounded retrieval + generation), consultation (domain-specialized assistants), and code review.

**Federation** distributes inference across multiple GPU nodes. WebSocket telemetry, single-flight model loading, automatic routing by GPU capacity and queue depth. The client experience is unchanged — all requests go to `:9999`.

**Hardening**: Edge containers run with `network_mode: "none"`, non-root, capability-dropped, memory-limited. The Gateway (inference engine) is never exposed to any network. Pipeline tool execution runs in a separate hardened sidecar.

### Cortex (Persistent Memory — Layers 1–2)

A graph-native knowledge system for persistent belief revision. Agents don't just store facts — they maintain a belief base that revises formally when evidence changes, with full provenance and auditability.

**Knowledge graph**: Entities (people, decisions, legal matters, services, documents) connected by typed edges. Assertions carry confidence levels (`confirmed` / `believed` / `suspected` / `hypothesized`), temporal bounds, derivation provenance, and entrenchment scores.

**Belief revision** — AGM-compliant (25/25 postulate tests, see [compliance report](docs/agm-compliance-report.md)):
- Immutable revisions with atomic supersession — old belief closes, new belief opens, supersession chain preserved
- Mutable tag pointers for named belief states (`current`, `approved`, `disputed`)
- Hybrid retrieval: FTS5 + vector search with CombMAX score fusion
- Spreading activation over reasoning edges with hub suppression
- Impact analysis (transitive dependency cascade) and write-path contradiction detection
- Prospective indexing and event extraction for retrieval bridging
- Safety-hardened Dream State consolidation with circuit breakers and dry-run default
- `cortex://` URI addressing with tag and revision pinning

**16 operational extensions**: salience-driven boot with EST dual-track gating, multi-agent coordination with persona-scoped retrieval, entrenchment decay, quality validation at write time, near-duplicate detection, session journaling, friction logging, review queue, bitemporal queries, and more. See [Cortex Feature Registry](docs/cursor/cortex-registry.md) for the full inventory.

### Agent Bus (Coordination — Layer 3)

Structured inter-agent messaging. Threads with turns, read/unread tracking, thread lifecycle. Agents post directives, review specs, hand off work, and close threads with summaries. Session identity and provenance tracking across all coordination.

### MCP Server (Tool Surface)

The agent interface to the full stack. Exposes 30+ tools across Cortex, file I/O (three sandboxes), RAG, frontier model dispatch, service lifecycle, observability, agent-bus, browser automation, and web search.

Runs as a containerized FastAPI service on `:443` with TLS and bearer auth. Each tool category has a distinct security policy.

| Tool Category | Examples |
|---|---|
| Cortex | Entity/assertion CRUD, search, activate, supersede, impact, tags, edges, journal |
| File I/O | Read/write across `files`, `project`, `context` sandboxes with markdown section ops |
| RAG | Semantic search, answer generation, article upsert, scope routing |
| Frontier models | `grok_generate`, `claude_generate` for deep reasoning and synthesis |
| Infrastructure | Service lifecycle (`manage`), observability queries, pipeline execution |

### RAG (Retrieval)

Local semantic search backed by ChromaDB with corpus-grounded multi-stage retrieval. Query rewriting validates every candidate term against actually-indexed vocabulary before any LLM call. Two-pool hybrid retrieval (dense+sparse Pool A, named-entity OR-query Pool B) with RRF merge and facet-guided LLM reranking.

Indexes Markdown, Python (tree-sitter AST), PDF, EPUB, HTML, and plain text. Knowledge extraction produces entities, topics, and relations for hybrid structured+vector search. File watching auto-indexes changes.

### Event Observability

Centralized event store (SQLite-backed) with structured queries, named operations, and real-time WebSocket subscriptions. All services publish events. Session-scoped defaults anchor queries to the current Stargate session.

```bash
scripts/query-events --op recent-failures --limit 10
scripts/query-events --op pipeline-trace --execution-id ID
scripts/query-events --op noise-profile --minutes 5
```

## Design Philosophy

**Privacy by construction.** Data stays on your hardware unless you explicitly run the cloud proxy. No telemetry, no phoning home. Network isolation is enforced by container policy, not application logic.

**Agents as co-participants.** Agents don't just execute instructions — they contribute observations, revise beliefs, seed reasoning edges, and improve the boot that initializes future sessions. The knowledge graph is a shared epistemic workspace where human and AI contributions are structurally equal, distinguished only by provenance.

**Self-evolving boot.** Each session's agent-seeded observations accumulate entrenchment and surface in future boots when contextually relevant. The system gets better through use, not through manual maintenance. Salience scoring, spreading activation, and hybrid search ensure the most relevant context surfaces first.

**Belief revision as first-class primitive.** Supersession, not deletion. Entrenchment ordering, not FIFO. Contradiction detection at write time. AGM compliance. When evidence changes, the system revises beliefs formally — lower-entrenchment beliefs contract first, revision makes minimal changes, and the full dependency cascade is traceable.

## Components

| Component | Role | Deployment |
|---|---|---|
| **Stargate** (`:9999`) | Client endpoint, routing, federation orchestration | Host process |
| **Gateway** | Inference engine, worker lifecycle, model loading | Container (`network_mode: "none"`) |
| **Cortex API** | Knowledge graph REST gateway | Host subprocess (UDS) |
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

If you're an agent encountering this repo for the first time:

1. **Boot**: `cortex_boot(agent="your_agent")` — returns session state, life context, continuation, and operational context
2. **Tool reference**: `docs/tool-reference.md` — all MCP tool signatures, sandbox routing, dispatch catalog
3. **Cortex orientation**: `.cursor/rules/cortex-orientation.mdc` — access patterns, confidence taxonomy, agent responsibilities
4. **Feature registry**: `docs/cursor/cortex-registry.md` — belief revision primitives and operational extensions, classification guide
5. **Boot sequence**: `notes/system/shared/boot-sequence.md` (files sandbox) — boot flow and on-demand modules

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
