---
name: debug-with-events
description: Debug opaque/silent failures in gateway workers by instrumenting with debug events.
---

# Debug with Events

Structured diagnostic workflow for failures where logs are insufficient —
especially inside containers where log hierarchies may hide output or where
the failure point is ambiguous across multiple subsystems.

**When to use**: Silent hangs, phantom VRAM, state desync between gateway
and Stargate, timeouts with no logged error, any "the process is running but
the system doesn't know" scenario.

**Key insight**: `emit_debug_event` writes directly to the event service UDS
socket (respects `EVENTS_INGEST_SOCK` env var), bypassing the gateway event bus.
Events carry `role="debug"` and are auto-pruned at session boundary.

**Related cortex skills**:

- `cortex:agent-skills/ulg-architecture.md` §Event Service Primary — Event Service is the primary investigation surface; this skill operationalizes that architectural rule for opaque-failure forensics.
- `cortex:agent-skills/architecture-invariants.md` — `[universal:events]` rule (one-update-path-per-state-key, event-driven state) that this skill's debug events must not violate when promoted to permanent observability per Step 7.

---

## Container Event Architecture

Each edge container runs its own Event Service. Understanding this is critical
for knowing where your debug events land and where to find them after a crash.

### Two Event Services per node

| Event Service | Location | Persistence | Contains |
|---|---|---|---|
| **Host** | `/tmp/universal-protocol/events.sock` | Disk (`~/.events/events.db`) | Host events, bridged container globals, debug events (if run from host) |
| **Container** | `/tmp/container-events/events.sock` | Disk (`/golem/logs/events.db` → host: `tmp/gpu-nodes/<node>/logs/events.db`) | Worker lifecycle, inference timing, model load events, debug events (if run from container) |

Container events with `scope: global` are bridged to the host Event Service
automatically. `scope: node` events stay in the container DB only.

### Where debug events go

`emit_debug_event` writes to `$EVENTS_INGEST_SOCK`:
- **On host**: goes to host Event Service (default `/tmp/universal-protocol/events.sock`)
- **In container**: goes to container Event Service (`/tmp/container-events/events.sock`)

Debug events use `scope: node` by default — they stay in whichever Event
Service received them. To make a debug event visible from the host, pass
`scope="global"` (it will be bridged).

### Crash forensics

Container events persist to disk at `tmp/gpu-nodes/<node>/logs/events.db`
(volume-mounted). After a container crash:

```bash
# Query the container's persisted events directly from the host
sqlite3 tmp/gpu-nodes/localhost/logs/events.db \
  "SELECT signal, json_extract(payload,'$.step'), timestamp FROM events ORDER BY seq DESC LIMIT 30"

# Or for a remote node
ssh krunch3r@jupiter "sqlite3 ~/universal-llm-gateway/tmp/gpu-nodes/jupiter/logs/events.db \
  \"SELECT signal, json_extract(payload,'$.error'), timestamp FROM events WHERE signal LIKE 'debug.%' ORDER BY seq DESC LIMIT 20\""
```

This is the primary forensic tool for container crashes — the event trail
survives because the DB file is on a volume mount, not inside the container
filesystem.

---

## Step 1: Observe symptoms

Establish the observable state before touching code.

```
# GPU state
nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader

# Model status (aggregated across nodes)
curl -s http://localhost:9999/api/v1/model-status/{model_id} | python3 -m json.tool

# Per-node model state
curl -s http://localhost:9999/api/v1/node-models | python3 -c "
import json, sys
for node in json.load(sys.stdin).get('nodes', []):
    for m in node.get('models', []):
        if '{keyword}' in m.get('id',''):
            print(json.dumps({'node': node['node_id'], 'model': m}, indent=2))
"

# Recent failures and event noise
scripts/query-events --op recent-failures --limit 20
scripts/query-events --op noise-profile --minutes 5

# Model-specific event timeline
scripts/query-events --op model-timeline --param model_id={model_id}

# Process-level check (for orphan detection)
ps aux | grep llama-server | grep -v grep
```

**Record**: Write down VRAM usage, model status, event gaps, and any orphan
processes. This is your baseline.

---

## Step 2: Identify the instrumentation targets

Narrow down which code path to instrument based on symptoms.

| Symptom | Likely code path | Start instrumenting |
|---|---|---|
| Model stuck at "loading" | Load flow (gateway → worker → engine) | `loader.py` → `engine_lifecycle.py` → `engine_factory.py` |
| Phantom VRAM | Engine load succeeds but state transition fails | `loader.py` finalize path, `state_machine.py` |
| Timeout with no error | Engine creation or health wait | `engine_factory.py`, `native/server.py` |
| Worker unresponsive | Worker process startup or RPC | `communication/orchestration.py`, worker bootstrap |
| State desync | Resource tracker vs Stargate | `tracker.py`, `transitions.py`, telemetry publisher |

Read the suspected files before instrumenting. Understand the call chain so
you place events at decision boundaries, not in the middle of computation.

---

## Step 3: Instrument with debug events

### The helper

```python
from universal_event_bus.events.debug import emit_debug_event
```

`emit_debug_event(signal, payload, source)` — async, never raises, writes
directly to `/tmp/universal-protocol/events.sock`. Available in any code
that runs inside the container (gateway, workers) or on the host.

### Instrumentation pattern

Add a module-level helper to keep call sites clean:

```python
from typing import Any
from universal_event_bus.events.debug import emit_debug_event

async def _debug(step: str, model_id: str, **extra: Any) -> None:
    await emit_debug_event(
        "debug.{subsystem}.{flow}",          # e.g. "debug.load.flow.step"
        {"step": step, "model_id": model_id, **extra},
        source="{service}",                   # e.g. "gateway", "gateway-worker"
    )
```

Then bracket each phase:

```python
await _debug("phase_start", model_id, some_param=value)
result = await do_the_thing()
await _debug("phase_done", model_id, elapsed_s=round(time.monotonic() - t0, 2))
```

### Placement rules

1. **At every decision boundary** — if/else branches, error handlers, early returns
2. **Before and after slow operations** — engine creation, health checks, RPC calls
3. **In exception handlers** — capture `error=str(e)`, `error_type=type(e).__name__`
4. **Include elapsed time** — `elapsed_s=round(time.monotonic() - t0, 2)` on "done" events
5. **Include identifying data** — model_id, engine_pid, context_size, engine_type

### Signal naming convention

`debug.{subsystem}.{noun}` — e.g.:
- `debug.worker.load.step`
- `debug.factory.phase`
- `debug.load.flow.step`
- `debug.rpc.command`

---

## Step 4: Deploy and reproduce

```
# Quality gate
python3 -m compileall -q {instrumented_files}
ruff check {instrumented_files}

# Rebuild (requires ./manage running)
manage(action="rebuild", service="gateway")
manage(action="wait_healthy", service="gateway", timeout=120)

# If orphan processes exist, kill them first
kill {pid}

# Trigger the failing operation
curl -X POST http://localhost:9999/api/v1/models/{model_id}/load
```

If `./manage` is not running, ask the user to rebuild manually.

---

## Step 5: Query debug events

```bash
# By signal (most useful — shows chronological trace of your debug events)
scripts/query-events --op signal-events --param signal=debug.load.flow.step --param limit=50

# Multiple signal families (run in parallel for the full picture)
scripts/query-events --op signal-events --param signal=debug.worker.load.step --param limit=50
scripts/query-events --op signal-events --param signal=debug.factory.phase --param limit=50

# Raw SQL for cross-signal analysis
scripts/query-events --op raw-sql \
  --param sql="SELECT signal, timestamp, json_extract(payload,'$.step') as step, json_extract(payload,'$.error') as error FROM events WHERE signal LIKE 'debug.%' ORDER BY seq DESC LIMIT 50"
```

MCP equivalent:
```
observability(operation="signal-events", params={"signal": "debug.load.flow.step", "limit": 50})
```

### Reading the trace

Reconstruct the timeline from the events. Look for:

| Pattern | Meaning |
|---|---|
| `phase_start` with no `phase_done` | Crash or hang inside that phase |
| `phase_done` → `exception` | Success followed by failure in the next step |
| Gap in expected sequence | Code path diverged (took an early return or different branch) |
| `error_type` in payload | Exact exception class — search the codebase for it |

---

## Step 6: Fix the root cause

The debug events pinpoint the exact failure location. Fix the bug in the
source code, not in the instrumentation.

Common root causes surfaced by this workflow:

| Debug evidence | Root cause pattern |
|---|---|
| Engine loads fine, state transition crashes | Dataclass/constructor mismatch (missing fields, wrong kwargs) |
| Health check times out, process survives | Timeout too short or kill path broken |
| Worker log shows nothing after a step | Logging hierarchy mismatch (wrong logger name) |
| RPC succeeds, finalize never runs | Exception in post-RPC code swallowed |

---

## Step 7: Clean up instrumentation

**Mandatory**. Debug events are temporary diagnostic instrumentation.

```bash
# Verify what needs removal
rg "emit_debug_event|_debug\(" {instrumented_files}

# Remove: import, helper function, all await _debug(...) calls
# Remove: any unused imports (time, Any) left behind
# Verify:
ruff check {instrumented_files}
python3 -m compileall -q {instrumented_files}
```

Do not leave debug events in committed code. They are for diagnosis sessions,
not permanent telemetry. If the failure mode warrants permanent observability,
add a proper event via `@event_factory` in the service's event vocabulary.

---

## Reference

### `emit_debug_event` API

Location: `libs/universal_event_bus/events/debug.py`

```python
async def emit_debug_event(
    signal: str,              # dot-notation signal name
    payload: dict[str, Any],  # arbitrary structured data
    source: str = "pipeline", # originating service
) -> None: ...
```

- Writes NDJSON to `$EVENTS_INGEST_SOCK` (default `/tmp/universal-protocol/events.sock`)
- `role="debug"`, `scope="node"` — auto-pruned at session boundary
- Silent on failure — never raises, never affects correctness
- On host: writes to host Event Service (disk-persisted)
- In container: writes to container Event Service (disk-persisted at `/golem/logs/events.db`, volume-mounted to host)

### Event query operations

| Operation | Use for |
|---|---|
| `signal-events` | Query by signal pattern, most useful for debug events |
| `model-timeline` | All lifecycle events for a specific model |
| `recent-failures` | System-wide failure events |
| `noise-profile` | Signal frequency histogram |
| `raw-sql` | Custom queries with `json_extract` on payloads |

### Key files by subsystem

| Area | Files |
|---|---|
| Load flow (gateway) | `src/core/workers/model_operations/loader.py`, `load_flow.py` |
| Worker engine lifecycle | `src/core/workers/worker/engine_lifecycle.py` |
| Engine factory | `src/core/workers/engine_factory.py` |
| State machine | `src/core/workers/state_machine.py` |
| Resource tracker | `src/core/resources/tracker.py`, `transitions.py` |
| Native GGUF engine | `libs/inference_djinn/engines/gguf/native/engine.py` |
| Llama server manager | `libs/inference_djinn/engines/gguf/native/server.py` |
| RPC orchestration | `src/core/workers/process/communication/orchestration.py` |
