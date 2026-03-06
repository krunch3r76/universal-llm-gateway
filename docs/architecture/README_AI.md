# Architecture Documentation — AI Index

Comprehensive, maintained architecture documentation for the Universal LLM Gateway.
Each file covers one subsystem and is self-contained for RAG consumption.

## File Routing Guide

| Question domain | Read this file |
|---|---|
| What is this project? How do the services connect? | `overview.md` |
| How does a request flow from client to inference? | `stargate.md` |
| How does the Gateway manage models and workers? | `gateway.md` |
| How do Master/Relay/Edge nodes federate? | `federation.md` |
| How does routing and capacity admission work? | `routing.md` |
| How do pipelines work (DAG, handlers, execution)? | `pipeline-system.md` |
| What shared libraries exist and what do they provide? | `libraries.md` |
| How does the event system work (signals, contracts)? | `event-system.md` |
| What tools, scripts, and CLIs are available? | `tools.md` |
| Where are config files, env vars, paths? | `configuration.md` |
## Supersedes

These files consolidate and replace the following scattered README_AI.md files:

- `./README_AI.md` → `overview.md`
- `services/universal-stargate/README_AI.md` → `stargate.md`
- `services/_universal-llm-gateway/README_AI.md` → `gateway.md`
- `services/universal-stargate/systems/federation/README_AI.md` → `federation.md`
- `services/universal-stargate/systems/routing/README_AI.md` → `routing.md`
- `services/universal-stargate/systems/routing/capacity/README_AI.md` → `routing.md`
- `services/universal-stargate/systems/pipeline/README_AI.md` → `pipeline-system.md`
- `services/universal-stargate/systems/pipeline/core/execution/README_AI.md` → `pipeline-system.md`
- `libs/*/README_AI.md` → `libraries.md`
- `tools/pipeline_test/README_AI.md` → `tools.md`

## Maintenance Contract

These docs are committed to accuracy. Update triggers:
- `/commit` that touches service or library source code
- `/journal-entry` that captures architectural decisions
- Architecture changes that affect subsystem boundaries

## Staleness Detection

Every doc references source paths (e.g., `systems/proxy/stargate/proxy.py`).
If a referenced path no longer exists, that doc section is stale.

Rule of thumb: if you find a discrepancy between a doc and source code,
source code is authoritative. Note the discrepancy and propose an update.
