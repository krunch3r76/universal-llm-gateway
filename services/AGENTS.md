# Services — Agent Guide (grok-direct)

This subtree holds runnable services: **universal-stargate** (proxy, routing, pipelines API), **grokbuild-worker**, **mcp-server** (vortex), **rag**, gateway workers, and related deploy artifacts. Conventions here **supplement** repo-root `/AGENTS.md`—read that first for identity, cortex boot, MCP wiring, worktree discipline, and session close.

---

## Service shapes

| Class | Examples | How agents interact |
|---|---|---|
| **Long-lived host processes** | Stargate (`:9999` default), grokbuild-worker (`:8090`), MCP vortex (HTTPS), RAG, agent-bus | `manage` MCP for lifecycle; `observability` / Event Service for diagnosis |
| **Containerized / worker pools** | LLM gateway workers, some pipeline runners | `manage(action="rebuild"\|"restart", ...)` then `wait_healthy`; see `service-lifecycle` skill |

Do not `systemctl` or `pkill` from agent sessions unless the operator explicitly directs it—prefer `manage`.

---

## Stargate and grokbuild routing

- **Stargate** listens by default on **`http://localhost:9999`** (`STARGATE_PORT`, `STARGATE_URL`, or unix socket via `STARGATE_UNIX_SOCKET`—see `libs/transport_utils` and `services/universal-stargate/scripts/stargate_service_manager.py`).
- **grokbuild-worker** listens on **`127.0.0.1:8090`**; Stargate forwards **`/api/v1/grokbuild/*`** verbatim (`services/universal-stargate/systems/proxy/routers/api/grokbuild.py`).
- Operator shell helpers `scripts/grok-worktree*` POST/DELETE through Stargate, not directly to `:8090`.

Auth on the grokbuild proxy path is **pass-through**—bearer tokens are validated at vortex/MCP layers, not re-checked inside Stargate for grokbuild forwards.

---

## Logging and events

**Invariant:** all Python under `services/` uses `universal_logging`:

```python
from universal_logging import get_logger
logger = get_logger(__name__)
```

Never `import logging` / `logging.getLogger` in service code.

**Events:** prefer Event Service queries before tailing log files. Grokbuild emits dual vocabularies (`mcp.grokbuild.*` audit-rich + `grokbuild.*` worker/SSE)—JOIN on `dispatch_id` (`docs/event-contracts.md`, `docs/grokbuild-topology.md`). When adding signals, update event vocabulary docs in the same change.

---

## Restart and health

| Action | MCP |
|---|---|
| Status / health | `manage(action="health", service="...")` |
| Restart after code change | `manage(action="restart", ...)` or `rebuild` + `wait_healthy` |

Workspace skill: `.cursor/skills/service-lifecycle/SKILL.md` (read via `fs(sandbox="workspaces", ...)` per root `AGENTS.md`).

---

## Service-specific rules (glob-matched in Cursor)

When editing paths under `services/`, Cursor injects extra rules—grok-direct agents should load the same guidance on demand via `fs` or cortex rule entities when touching:

- `universal-stargate/systems/routing/` → routing_ws.mdc
- `universal-stargate/systems/federation/` → federation_ws.mdc
- `universal-stargate/systems/pipeline/` → pipeline_ws.mdc, frontier-model-context_ws.mdc
- `mcp-server/` → mcp-selector-naming_ws.mdc, mcp-tool-param-types_ws.mdc
- `rag/` → rag_ws.mdc

Architecture docs (overhaul-only write path): `docs/architecture/*.md`.

---

## Cross-reference

| Topic | Location |
|---|---|
| Agent identity, boot, session close | `/AGENTS.md` |
| Lib import / SLOC conventions | `/libs/AGENTS.md` |
| Grokbuild topology | `docs/grokbuild-topology.md` |
| Deployment topology | `.cursor/rules/topology_ws.mdc` (agent-requestable) |
