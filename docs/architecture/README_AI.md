# Architecture Documentation — AI Index

Comprehensive, maintained architecture documentation for the Universal LLM Gateway.

## Directory Layout

| Directory | Contents | Write authority |
|---|---|---|
| `docs/architecture/*.md` | Subsystem docs — one per overhaul target | `/overhaul` only |
| `docs/architecture/appendices/` | Cross-cutting reference (config, tools, libs, events) | `/doc-check` factual corrections |
| `docs/architecture/design/` | Historical design docs and proposals | User-directed only |

## File Routing Guide

### Subsystem docs (overhaul-produced)

| Question domain | Read this file |
|---|---|
| What is this project? How do the services connect? | `overview.md` |
| How does a request flow from client to inference? | `stargate.md` |
| How does the Gateway manage models and workers? | `gateway.md` |
| How do Master/Relay/Edge nodes federate? | `federation.md` |
| How does routing and capacity admission work? | `routing.md` |
| How do pipelines work (DAG, handlers, execution)? | `pipeline-system.md` |
| How does doc-generate extraction work? | `handlers.md` |

### Appendices (cross-cutting reference)

| Question domain | Read this file |
|---|---|
| Where are config files, env vars, paths? | `appendices/configuration.md` |
| What tools, scripts, and CLIs are available? | `appendices/tools.md` |
| What shared libraries exist and what do they provide? | `appendices/libraries.md` |
| How does the event system work (signals, contracts)? | `appendices/event-system.md` |

### Design docs (historical)

| Document | Topic |
|---|---|
| `design/federation-proposal.md` | Original federation architecture proposal |
| `design/federation-phase1-must-ship.md` | Phase 1 implementation specification |
| `design/federation-can-wait.md` | Deferred capabilities and risks |
| `design/federation-roles.md` | Federation topologies and roles |
| `design/event-driven-state.md` | Event-driven state architecture |
| `design/multi-master-future.md` | Multi-master forward-looking design |

## Maintenance Contract

Subsystem docs are produced by `/overhaul {service_dir}` and must not be
edited directly. Appendices may receive targeted factual corrections via
`/doc-check` during `/commit`. Design docs are historical and not actively
maintained.

## Staleness Detection

Every doc references source paths (e.g., `systems/proxy/stargate/proxy.py`).
If a referenced path no longer exists, that doc section is stale.

Rule of thumb: if you find a discrepancy between a doc and source code,
source code is authoritative. Note the discrepancy and propose an update.
