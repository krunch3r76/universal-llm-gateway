<!-- target:* -->
# Event System Reference

## Role of Events

Events are **central to the system architecture** — not an afterthought for debugging.

They serve three purposes, in order of importance:
1. **Coordination**: concurrent subsystems communicate and synchronize via events
2. **Coherence**: event contracts enforce lifecycle guarantees across nodes
3. **Observability**: the persisted event log is the authoritative record of system behavior

## Event Service (PREFERRED INTERFACE)

All services publish events to a centralized **Event Service** (SQLite-backed).
Use the Event Service for queries whenever possible — it provides structured operations,
cross-service correlation, and SQL access.

## Edge Container Event Paths

Edge containers still write local JSONL event logs internally. These are not
wired into host UDS event ingestion.

| Node | Path pattern | Notes |
|------|------|-------|
| Edge Stargate (Docker) | `/tmp/stargate-events/current.jsonl` | Container-local events |
| Edge Gateway (Docker) | `/tmp/_universal-gateway-events/current.jsonl` | Container-local events |

Access via `docker exec`:

```bash
docker exec CONTAINER cat /tmp/stargate-events/current.jsonl
docker exec CONTAINER cat /tmp/_universal-gateway-events/current.jsonl
```

## Event Service Queries (PREFERRED)

The Event Service centralizes all events from all publishers into SQLite.
Use named operations for common queries.

```bash
# Recent failures across all services
scripts/query-events --op recent-failures --limit 20

# Signal frequency (what's happening now?)
scripts/query-events --op noise-profile --minutes 5

# Trace a request end-to-end
scripts/query-events --op request-trace --request-id ID

# Request lifecycle phases (received → routed → completed)
scripts/query-events --op request-lifecycle --request-id ID

# Pipeline step-by-step trace
scripts/query-events --op pipeline-trace --execution-id ID

# Compare two pipeline runs side-by-side
scripts/query-events --op compare-runs --run-a ID1 --run-b ID2

# Model load/execute/unload timeline
scripts/query-events --op model-timeline --model-id MODEL

# Federation health
scripts/query-events --op federation-health

# Raw SQL for custom queries
scripts/query-events --sql "SELECT signal, COUNT(*) c FROM events WHERE source='mcp-server' GROUP BY signal ORDER BY c DESC LIMIT 20"

# Live subscription (WebSocket)
scripts/query-events --subscribe --filter signal=pipeline.*
```

### MCP Agent Access

Agents with MCP tools use `observability` instead of the CLI:
```
observability(operation="recent-failures", params={"limit": 20})
observability(operation="pipeline-trace", params={"execution_id": "c4c7448d"})
observability(operation="raw_sql", params={"sql": "SELECT * FROM events WHERE signal LIKE 'mcp.%' ORDER BY seq DESC LIMIT 20"})
```

## Multi-Node Debugging

### Event Scoping

Events carry `scope` (`global` | `node`). Not all signals appear on all nodes:

- **`scope: global`** — available via Event Service queries
- **`scope: node`** — exists only at the originating node (edge container or remote host). These are observation signals (e.g., `gateway.resource.updated`, `federation.model.lifecycle` busy/idle) that are not re-emitted on master to reduce noise.

When an expected signal is absent from master events, check the originating node.

```bash
# Master/host view (Event Service)
scripts/query-events --op request-trace --request-id ID

# Edge Stargate (container — JSONL, no event service access)
docker exec CONTAINER cat /tmp/stargate-events/current.jsonl | python3 -c "
import json, sys
for line in sys.stdin:
    e = json.loads(line.strip())
    if e.get('scope') == 'node': print(json.dumps(e))
"

# Edge Gateway (container — JSONL, no event service access)
docker exec CONTAINER cat /tmp/_universal-gateway-events/current.jsonl | python3 -c "
import json, sys
for line in sys.stdin:
    e = json.loads(line.strip())
    if e.get('scope') == 'node': print(json.dumps(e))
"
```

## Event Contracts

Consult the canonical event-contracts reference for:
- Required payload fields
- Lifecycle guarantees (e.g., `request.started` ⟹ `request.completed`)
- Correlation field semantics

## Common Debugging Patterns

### CLI (`scripts/query-events`)

| Issue | CLI Query |
|-------|-----------|
| Request timeout | `scripts/query-events --sql "SELECT * FROM events WHERE signal='request.timed.out' ORDER BY seq DESC LIMIT 10"` |
| Routing rejection | `scripts/query-events --sql "SELECT * FROM events WHERE signal='federation.routing.rejected' ORDER BY seq DESC LIMIT 10"` |
| Stale telemetry | `scripts/query-events --sql "SELECT * FROM events WHERE signal='federation.telemetry.marked.stale' ORDER BY seq DESC LIMIT 10"` |
| Load failure | `scripts/query-events --sql "SELECT * FROM events WHERE signal='federation.load.failed' ORDER BY seq DESC LIMIT 10"` |

### MCP Agent Access

| Issue | MCP Query |
|-------|-----------|
| Request timeout | `observability(params={"sql": "SELECT * FROM events WHERE signal='request.timed.out' ORDER BY seq DESC LIMIT 10"}, operation="raw_sql")` |
| Routing rejection | `observability(params={"sql": "SELECT * FROM events WHERE signal='federation.routing.rejected' ORDER BY seq DESC LIMIT 10"}, operation="raw_sql")` |
| Recent failures | `observability(operation="recent-failures", params={"limit": 20})` |

## Pipeline Debugging

### CLI

```bash
scripts/query-events --op pipeline-trace --execution-id ID
scripts/query-events --op compare-runs --run-a ID1 --run-b ID2
```

### MCP Agent Access

```
observability(operation="pipeline-trace", params={"execution_id": "ID"})
observability(operation="compare-runs", params={"run_a": "ID1", "run_b": "ID2"})
pipeline_consult(execution_id="ID", step_name="step", problem="description of the issue")
```
<!-- /target:* -->
