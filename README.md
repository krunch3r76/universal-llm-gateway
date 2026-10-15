# Universal LLM Gateway

> **Rent the model. Own the mind.**

Today's strongest AI lives in a rented room: memory at the platform's
pleasure, continuity that dies with the tab. This is the other
arrangement: an owned room for machine cognition (memory, belief graph,
agent bus, provenance) on hardware you control. Beliefs keep their
evidence and revise instead of resetting. Agents pick up Tuesday where
Monday ended. Frontier models visit as consultants; the house remembers.

Running today: local and cloud models behind one self-hosted API, a
belief base with full revision history (AGM postulate suite: 25/25 in
[docs/agm-compliance-report.md](docs/agm-compliance-report.md)),
multi-agent coordination, and network-isolated inference with one
audited path out.

**Status:** working alpha. Daily production use on single-GPU and
multi-node federated deployments, and daily breaking changes. Issues
and contributions are welcome from builders comfortable with churn.

## Why this exists

Custody of cognition. Built from necessity before philosophy: one real
life under material pressure (deadlines, disputes, seasons that punish
dropped threads) needed a mind that doesn't reset, and the substrate
that held is the product.

Work in the Cursor IDE and work in claude.ai share this house, so a
session in one can continue the other. A hard judgment rents a frontier
model. Long and repeated pipelines run on the open-model local gateway,
with OpenRouter as the scout for what earns a local slot.

## Architecture

Four layers. Layer 0 runs alone. Layers 1, 2, and 3 turn on together.

0. **Inference routing (Stargate, `:9999`).** Local llama.cpp or vLLM,
   cloud (Anthropic, xAI, OpenAI, Google, OpenRouter), or federated GPU
   nodes. OpenAI-compatible and provider-native APIs on one port.
1. **Persistent memory (Cortex).** Entities, assertions, temporal
   bounds, hybrid search, reasoning links.
2. **Belief revision.** Fact updates keep history, flag contradictions,
   and prune outdated records. 25/25 AGM postulate tests:
   [docs/agm-compliance-report.md](docs/agm-compliance-report.md).
3. **Coordination.** Agent bus, session initialization, MCP tool
   surface. Agents share state and hand off work.

```
Client → Master Stargate:9999 (sole client-facing endpoint)
         ├─ Unix socket → Edge container (network_mode: "none") → Gateway → Worker (local)
         ├─ TCP (remote) → Relay Stargate → Edge container → Gateway (federated)
         ├─ Unix socket → Cloud Proxy → Anthropic/xAI/OpenAI/Google/OpenRouter
         └─ Unix socket → Cortex API, RAG, Agent Bus, Event Service
```

Layer 0 alone is a model gateway: local models on your hardware, no
cloud credentials, no frontier access. The Gateway process is never
placed on a network. The longer subsystem write-up is
[docs/stack-detail.md](docs/stack-detail.md).

## Checking a live system

Services publish to one SQLite event store. `scripts/query-events`
reads it. Session-scoped defaults anchor a query to the current
Stargate session. Contracts:
[docs/event-contracts.md](docs/event-contracts.md).

```bash
scripts/query-events --op recent-failures --limit 10
scripts/query-events --op pipeline-trace --execution-id ID
scripts/query-events --op noise-profile --minutes 5
```

Limits that are already true of this tree:

- Subsystems still land often, interfaces shift, and docs lag. Expect
  breakage between pulls.
- Python code indexing via tree-sitter is implemented and not yet
  active ([docs/stack-detail.md](docs/stack-detail.md), RAG).
- Enrichment, belief revision, and automated fact cleanup work best
  with frontier-quality reasoning (cloud proxy or high-end local
  hardware). The graph stays on the machine. Only that operation's
  prompt goes out.

## Quick Start

```bash
git clone https://github.com/krunch3r76/universal-llm-gateway.git
cd universal-llm-gateway
./manage
```

`./manage` bootstraps the Python venv, installs dependencies, and
launches the terminal management interface. Inference, memory, and
coordination start and stop through `./manage`. Config:
`~/.gateway/stargate.yaml`.

## Documentation

- [Stack detail](docs/stack-detail.md): subsystems, design notes, components, tree, models
- [For AI agents](docs/for-ai-agents.md)
- [System architecture](docs/architecture/)
- [AGM compliance](docs/agm-compliance-report.md)
- [Event contracts](docs/event-contracts.md)
- [Pipeline](services/universal-stargate/systems/pipeline/README.md), [federation](services/universal-stargate/systems/federation/README.md), [RAG](services/rag/README.md), [MCP](services/mcp-server/README.md)
- [Cortex feature registry](docs/cursor/cortex-registry.md)

## License

MIT. See [LICENSE](LICENSE).

## Author

**krunch3r76** ([@krunch3r76](https://github.com/krunch3r76))

- Issues: [GitHub Issues](https://github.com/krunch3r76/universal-llm-gateway/issues)
