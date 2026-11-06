# Execution owner conformance (phase 4)

Lane-B package `libs/execution_conformance/` models the **local intent ledger** and
**read-only actuator adapters** used to verify crash/restart behavior without
mutating Stargate admission, MCP tools, or cortex store.

## Actuators

| Actuator | Owner surface | Durable across restart |
|---|---|---|
| `agent_bus` | `create_thread` + `threads.idempotency_key` | Yes (SQLite) |
| `stargate_pipeline` | `PipelineExecutionTracker.records` | No (in-process) |
| `cdp_ask` | `cdp_inflight_leg` SQLite ledger | Yes (`DATA_DIR`) |
| `cursor_bridge` | Bus-store MSG/REPLY model (MCP runtime not imported) | Yes (bus SQLite) |

## Local intent ledger

Caller-minted `intent_id` rows are persisted at `reserved` **before** any remote
admit call. Receipt columns store the actuator reference (`thread_id`,
`execution_id`, …) once the owner acknowledges.

## Reconcile

1. **Found** — adopt owner state; do not redispatch.
2. **Not found + durable owner** — at most one retry with the same identity key.
3. **Not found + memory-only owner** — `outcome_unknown` → `blocked_unknown`; zero redispatch.
4. **CDP open leg without board proof** — after local receipt, `outcome_unknown` (no retry).
5. **Bridge MSG without REPLY** — `blocked_unknown` (no resend).

Compensation from `blocked_unknown` requires an explicit `CompensatingAction`.

## Verification

```bash
ruff check libs/execution_conformance
ruff format --check libs/execution_conformance
pytest libs/execution_conformance -q
```

Machine-readable capability matrix: `execution_conformance.manifest.MANIFEST`.
