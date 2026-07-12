# Universal LLM Gateway

> **Active development — not ready for public use.** This repository is under major, daily overhauls as scope expands into frontier agent capabilities (multi-agent coordination, belief revision, MCP tool surfaces, and cloud-augmented pipelines). APIs, docs, and layout change frequently. Public release is the goal; today this is a working research-and-production codebase, not a polished product.

Run models on your own hardware so **intellectual property, proprietary data, client materials, and other protected information stay on infrastructure you control**. Inference runs in **network-isolated sandboxes** (`network_mode: "none"`) with a single audited client path (`:9999`) — the same defense-in-depth pattern used when AI workloads touch confidential or regulated data: models get compute, not unrestricted network or filesystem access. Your corpora, session state, and operational context remain local unless you deliberately route specific requests elsewhere.

That's the starting point — a self-hosted gateway for teams that need **serious information security** alongside capable local and cloud-augmented inference.

When local isn't enough, enable the cloud proxy. Pipelines can use cloud models for heavier reasoning while your documents, knowledge graph, and session history stay on your machine. The proxy is the only outbound path; disable it to restore full on-prem isolation.

Take it further: bring in frontier models through MCP and they can drive your local pipelines, search your document corpus, use your tools — all grounded in a persistent knowledge graph (Cortex) where humans and AI agents build on each other's work across sessions.

---

## Architecture

Four layers, each building on the one below:

```
Layer 3: Coordination    Agent bus, session initialization, MCP (Model Context Protocol) tool surface
                         Multiple agents share state, hand off work,
                         and improve the initial context provided to future sessions.
                         ┌─────────────────────────────────────────┐
Layer 2: Belief Revision │ Fact updates with full history tracking, │
                         │ contradiction detection, context         │
                         │ traversal, and automated cleanup of      │
                         │ outdated information.                    │
                         └─────────────────────────────────────────┘
Layer 1: Persistent      Cortex knowledge graph — entities,
         Memory          assertions, temporal bounds, hybrid search,
                         reasoning links. Agents remember
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
         ├─ Unix socket → Cloud Proxy → Anthropic/xAI/OpenAI/Google/OpenRouter (cloud inference)
         └─ Unix socket → Cortex API, RAG, Agent Bus, Event Service (cognitive services)
```

## Status

**Alpha — internal evolution, not a release candidate.** The stack is production-used on single-GPU and multi-node federated deployments, but the project is expanding fast: new subsystems land often, interfaces shift, and documentation lags code. Treat this repo as an early look at where the project is headed, not as stable open-source software ready for adoption. Issues and contributions are welcome; expect breakage between pulls.

## Quick Start

```bash
git clone https://github.com/krunch3r76/universal-llm-gateway.git
cd universal-llm-gateway
./manage
```

`./manage` bootstraps the Python venv, installs dependencies, and launches the terminal management interface. All services — inference, memory, and coordination — start and stop through `./manage`.

## Subsystems

### Stargate (Inference Routing — Layer 0)

The sole client-facing endpoint, Stargate routes requests to local models, federated GPU nodes, and cloud providers through a single OpenAI-compatible API.


| Capability                                              | Endpoint                                |
| ------------------------------------------------------- | --------------------------------------- |
| Chat completions (SSE — Server-Sent Events — streaming) | `POST /v1/chat/completions`             |
| Embeddings                                              | `POST /v1/embeddings`                   |
| Model list (local + cloud + pipeline)                   | `GET /v1/models`                        |
| Model selection (task-aware, cost budgets)              | `POST /v1/models/select`                |
| Provider-native APIs (Anthropic, xAI, OpenAI)           | `POST /api/v1/providers/{provider}/...` |
| Health                                                  | `GET /health`                           |


**Pipelines** are virtual models that orchestrate multiple real models behind a single `model` name. They support graph execution with automatic parallelization, loops, conditional branching, retry, and hot-reload. Shipped pipelines include consensus (multi-model verification where any model can flag an error), RAG answer (corpus-grounded retrieval + generation), consultation (domain-specialized assistants), and code review.

**Federation** distributes inference across multiple GPU nodes. It provides WebSocket telemetry, efficient model loading to prevent memory spikes, and automatic routing based on GPU capacity and queue depth. The client experience remains unchanged — all requests go to `:9999`.

**Hardening**: Edge containers run with `network_mode: "none"`, non-root, capability-dropped, and memory-limited. The Gateway (inference engine) is never exposed to any network. Pipeline tool execution runs in a separate hardened sidecar.

### Agent Bus (Coordination — Layer 3)

Structured inter-agent messaging with threads, turns, read/unread tracking, and thread lifecycle management. Agents post directives, review specifications, hand off work, and close threads with summaries. Authorship and session identity are tracked across all coordination activity.

### MCP Server (Tool Surface)

The entry point for outside agents to connect into the stack. Frontier models and external agents initiate an MCP connection and get access to 30+ tools spanning Cortex, file read/write (two isolated storage areas: knowledge graph files and project files), RAG, local pipelines, service lifecycle, observability, agent bus, browser automation, and web search.

Runs as a containerized FastAPI service on `:443` with TLS and bearer auth. Each tool category has a distinct security policy.


| Tool Category   | Examples                                                                                                                                        |
| --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Cortex          | Entity/assertion CRUD, search, activate, supersede, impact, tags, edges, journal                                                                |
| File I/O        | Read/write across `cortex` and `workspaces` sandboxes with markdown section ops                                                                 |
| RAG             | Semantic search, answer generation, article upsert, scope routing                                                                               |
| Frontier models | Delegate sub-tasks to other specialized AI roles or hand off work back to the human operator |
| Infrastructure  | Service lifecycle (`manage`), observability queries, pipeline execution                                                                         |


### RAG (Retrieval-Augmented Generation)

When the gateway indexes a document, it does more than chunk and embed it. An LLM extracts entities, topics, and relations from the content, building a structured vocabulary alongside the vector and full-text indexes. This is where the useful work happens: search time is fast and cheap because the hard analysis ran at ingest.

Querying draws on that index in two parallel paths. The semantic path uses vector similarity and keyword search to match documents by meaning. The lexical path matches exact terms against the extracted vocabulary, retrieving identifiers and technical terms that semantic search can blur. Search terms are validated against the index vocabulary before any LLM call, so queries never run on words that aren't actually there. Results from both paths merge and pass through a local reranker before answer generation.

Supported formats: Markdown, PDF, EPUB, HTML, and plain text. The gateway watches for file changes and re-indexes automatically. Python code indexing via tree-sitter is implemented but not yet active.

### Cortex (Persistent Memory)

> Scoped documentation: `libs/cortex_store/` (`docs/architecture/appendices/libraries.md`)

Cortex is a graph-native knowledge system for persistent, formally-structured belief revision. It stores what agents and humans know — and tracks what changes, when, and why.

**Knowledge graph**: Entities (people, decisions, documents, legal matters, services) connected by typed edges. Assertions carry confidence levels (`confirmed` / `believed` / `suspected` / `hypothesized`), temporal bounds, source traceability, and priority scores based on reliability and usage.

**Belief revision** — AGM (Alchourrón-Gärdenfors-Makinson) compliant (25/25 postulate tests, see [compliance report](docs/agm-compliance-report.md)):

- Immutable history — updating a fact creates a new record and links the old one to it, rather than overwriting data
- Named labels that can be moved between fact states (`current`, `approved`, `disputed`)
- Hybrid search combining keyword matching (SQLite FTS5) with vector similarity scoring
- Context retrieval that finds related information by traversing links between facts, automatically ignoring overly connected hub topics
- Contradiction detection that warns when a new fact contradicts existing ones or breaks downstream dependencies
- Automated cleanup processes that prune outdated or low-confidence facts safely

**Humans and agents write to the same graph.** There is no separate "AI memory" — agents add entities and assertions using the same schema as human-authored knowledge. Who contributed what is tracked through provenance; neither side gets structural preference.

**Operational note**: The heavier cognitive operations — enrichment, belief revision, automated fact cleanup — currently work best with frontier-quality reasoning, served by the optional cloud proxy or high-end local hardware. Your knowledge graph stays on your machine either way; only the prompt for that specific operation goes out.

Agents access Cortex through MCP tools, which relay to the Cortex REST API over Unix sockets.

### Event Observability

Centralized event store (SQLite-backed) with structured queries, named operations, and real-time WebSocket subscriptions. All services publish events. Session-scoped defaults anchor queries to the current Stargate session.

```bash
scripts/query-events --op recent-failures --limit 10
scripts/query-events --op pipeline-trace --execution-id ID
scripts/query-events --op noise-profile --minutes 5
```

## Design Philosophy

**Protected information, by design.** Trade secrets, legal files, client deliverables, internal research, and other confidential material stay on hardware you operate. Optional cloud inference sends only the prompts and context you route through the proxy — not your full corpus or knowledge graph. Network boundaries are enforced by container policy and explicit egress controls, not vendor promises.

**Sandboxed inference.** Local models run in hardened containers: no direct network access, dropped capabilities, non-root execution. This is deliberate isolation — models can reason over your data through controlled APIs, but cannot independently reach the open internet or wander outside the paths you configure (Stargate routing, optional cloud proxy, MCP tool permissions).

**Agents as co-participants.** Agents do not merely execute instructions — they record observations, update facts, link related ideas, and improve the initial context provided to future sessions. The knowledge graph is a shared workspace where human and AI contributions are tracked equally, distinguished only by who made them.

**Self-evolving context.** Each session's agent-authored observations gain priority over time and surface in future sessions when relevant. The system improves through use rather than manual maintenance. Context is ranked by relevance and recent usage so the most useful information surfaces first.

**Belief revision as first-class primitive.** History is preserved, not overwritten. When evidence changes, the system formally updates facts: less reliable claims are pruned first, changes are kept minimal, and the entire history of why a fact changed remains traceable.

## Components


| Component              | Role                                                                                                                                                                                              | Deployment                         |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------- |
| **Stargate** (`:9999`) | Client endpoint, routing, federation orchestration                                                                                                                                                | Host process                       |
| **Gateway**            | Inference engine, worker lifecycle, model loading                                                                                                                                                 | Container (`network_mode: "none"`) |
| **Cortex API**         | Knowledge graph — entities, assertions, belief revision, session history. REST gateway over Unix socket; sole access path for agents. See [compliance report](docs/agm-compliance-report.md). | Host subprocess (Unix socket)      |
| **Agent Bus**          | Inter-agent messaging                                                                                                                                                                             | Host subprocess (Unix socket)      |
| **RAG Service**        | Semantic search, indexing, knowledge extraction                                                                                                                                                   | Host process (Unix socket)         |
| **Cloud Proxy**        | Cloud API relay (optional)                                                                                                                                                                        | Container (Unix socket)            |
| **MCP Server**         | Tool server for agents                                                                                                                                                                            | Container (`:443`, TLS)            |
| **Event Service**      | Centralized event store                                                                                                                                                                           | Host subprocess (Unix socket)      |


Managed via `./manage` (terminal management interface). Config at `~/.gateway/stargate.yaml`.

## Model Support


| Type      | Engine                                     | Examples                  |
| --------- | ------------------------------------------ | ------------------------- |
| Text LLM  | llama-cpp, vLLM                            | Qwen, Llama, Phi, Mistral |
| Embedding | llama-cpp                                  | bge-m3, qwen3-embedding   |
| Visual    | llama-cpp                                  | Gemma 4, LLaVA            |
| Audio     | Whisper                                    | Transcription             |
| Graphics  | Diffusers                                  | Flux.2                    |
| Cloud     | Anthropic, xAI, OpenAI, Google, OpenRouter | Claude, Grok, GPT, Gemini |


## For AI Agents

If you are an agent encountering this repo for the first time:

1. **Boot**: `cortex_brief(agent="your_agent")` — returns session state, life context, continuation, and operational context
2. **Tool reference**: `docs/tool-reference.md` — all MCP tool signatures, sandbox routing, dispatch catalog
3. **Agent guide**: `AGENTS.md` — identity, cortex boot ritual, MCP wiring, and session-close protocol (Cursor IDE users also get `.cursor/commands/cortex-boot.md`, which is IDE-local and not part of this repo)
4. **Cortex skills**: `agent_skill` entities surfaced by boot; read full skill docs from `agent-skills/<NAME>.md` in the Cortex sandbox
5. **Feature registry**: `docs/cursor/cortex-registry.md` — belief revision primitives and operational extensions, classification guide
6. **Boot sequence**: `notes/system/shared/boot-sequence.md` (cortex sandbox) — boot flow and on-demand modules

## Project Structure

```
universal-llm-gateway/
├── manage                            # Entry point — bootstraps venv, launches TUI
├── services/
│   ├── _universal-llm-gateway/       # Gateway (container-internal inference)
│   ├── universal-stargate/           # Stargate (port 9999, routing + orchestration)
│   │   └── systems/                  # Subsystems: pipeline, federation, routing
│   ├── rag/                          # RAG service (Unix socket)
│   ├── universal_cloud_proxy/        # Cloud proxy (Unix socket, optional)
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


| Area                    | Location                                                                                                               |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| System architecture     | `[docs/architecture/](docs/architecture/)`                                                                             |
| Pipeline system         | `[services/universal-stargate/systems/pipeline/README.md](services/universal-stargate/systems/pipeline/README.md)`     |
| Federation              | `[services/universal-stargate/systems/federation/README.md](services/universal-stargate/systems/federation/README.md)` |
| RAG service             | `[services/rag/README.md](services/rag/README.md)`                                                                     |
| MCP server              | `[services/mcp-server/README.md](services/mcp-server/README.md)`                                                       |
| Event contracts         | `[docs/event-contracts.md](docs/event-contracts.md)`                                                                   |
| Cortex feature registry | `[docs/cursor/cortex-registry.md](docs/cursor/cortex-registry.md)`                                                     |
| AGM compliance          | `[docs/agm-compliance-report.md](docs/agm-compliance-report.md)`                                                       |


## License

MIT License — see [LICENSE](LICENSE).

## Author

**krunch3r76** ([@krunch3r76](https://github.com/krunch3r76))

- Issues: [GitHub Issues](https://github.com/krunch3r76/universal-llm-gateway/issues)

