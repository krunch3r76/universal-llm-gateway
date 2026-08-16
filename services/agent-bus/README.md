# Agent Bus

Inter-agent message bus — threads, turns, read-tracking, checkpoint/dispatch
provenance. REST API over UDS; also exposed as the `agent_bus` MCP tool.

Implementation note: runtime lives in `libs/agent_bus_store/`. This service
directory keeps the container/process wrapper only (`src/main.py` delegates to
`agent_bus_store.server.create_app`).

Agent conventions (threads vs reply, CHECKPOINT discipline, dispatch polling):
see `agent_skill:agent-bus-discipline`.
Event vocabulary (`mcp.agentbus.*`): `docs/event-contracts.md`.

## Interfaces

| Interface | Socket | Protocol |
|---|---|---|
| API | `/tmp/universal-protocol/agent-bus.sock` (override: `AGENT_BUS_SOCK`) | HTTP (threads, turns, wait) |
| Health | same socket | HTTP `GET /health` |

## Module layout

| File | Purpose |
|---|---|
| `src/main.py` | Thin wrapper — delegates to `agent_bus_store.server.create_app` |
| `libs/agent_bus_store/server.py` | App wiring |
| `libs/agent_bus_store/db/` | Threads, turns, lifecycle, migrations |
| `libs/agent_bus_store/routes/` | HTTP route handlers |
| `libs/agent_bus_store/checkpoint_projection*.py` | CHECKPOINT tip/lineage projection |
| `libs/agent_bus_store/watchdog.py` | Quiet-thread / orphan sweep |

## Management

```
manage(action="sync_restart", service="agent_bus")
```

Provenance standing (critical-infrastructure status, what "primary CI" names
for this service) is recorded on `decision:agent-bus-provenance-ci` — see
that entity for the current enforcement mechanism.
