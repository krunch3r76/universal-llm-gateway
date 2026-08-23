# Debug-with-Events Reference

Supporting reference for `debug-with-events` SKILL.md.

## Container Event Architecture

Each edge container runs its own Event Service. Knowing which one receives your
debug events determines where to find them after a crash.

| Event Service | Socket | Persistence | Contains |
|---|---|---|---|
| **Host** | `/tmp/universal-protocol/events.sock` | `~/.events/events.db` | Host events, bridged container globals, host-run debug events |
| **Container** | `/tmp/container-events/events.sock` | `/golem/logs/events.db` → host `tmp/gpu-nodes/<node>/logs/events.db` | Worker lifecycle, inference timing, model load, container-run debug events |

`emit_debug_event` writes to `$EVENTS_INGEST_SOCK` — host socket on host, container
socket in container. Debug events default to `scope: node` (stay in the receiving
service); pass `scope="global"` to bridge a container event to the host.

### Crash forensics

Container events persist to a volume-mounted DB, so the trail survives a container crash:
```bash
sqlite3 tmp/gpu-nodes/localhost/logs/events.db \
  "SELECT signal, json_extract(payload,'$.step'), timestamp FROM events ORDER BY seq DESC LIMIT 30"
# remote node
ssh <user>@<satellite-host> "sqlite3 ~/universal-llm-gateway/tmp/gpu-nodes/<satellite-host>/logs/events.db \
  \"SELECT signal, json_extract(payload,'$.error'), timestamp FROM events WHERE signal LIKE 'debug.%' ORDER BY seq DESC LIMIT 20\""
```

## `emit_debug_event` API

Location: `libs/universal_event_bus/events/debug.py`.
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
- Host: host Event Service. Container: container Event Service (disk-persisted, volume-mounted).

## Event query operations

| Operation | Use for |
|---|---|
| `signal-events` | Query by signal pattern — most useful for debug events |
| `model-timeline` | All lifecycle events for a specific model |
| `recent-failures` | System-wide failure events |
| `noise-profile` | Signal frequency histogram |
| `raw-sql` | Custom queries with `json_extract` on payloads |

## Key files by subsystem

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
