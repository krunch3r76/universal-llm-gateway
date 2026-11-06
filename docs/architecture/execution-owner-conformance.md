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

1. **Terminal local states** (`completed`, `failed`, `cancelled`) — no lookup admit.
2. **Found** — adopt owner state (no rank regression); ref conflicts block.
3. **Retry on miss** — only when `adapter.admit_idempotent` is true (today: `agent_bus` only),
   store epoch matches, and `claim_retry` wins the CAS race. Retry reuses persisted
   `payload.admit_kwargs`.
4. **All other misses** (cdp, bridge, stargate, epoch mismatch) — `blocked_unknown`, zero redispatch.
5. **Open CDP leg / bridge MSG without correlated REPLY** — block after local receipt;
   reserved-without-receipt adopts from owner.

Compensation from `blocked_unknown` requires an explicit `CompensatingAction`.

## Verification

```bash
ruff check libs/execution_conformance
ruff format --check libs/execution_conformance
pytest libs/execution_conformance -q
```

Machine-readable capability matrix: `execution_conformance.manifest.MANIFEST`.
