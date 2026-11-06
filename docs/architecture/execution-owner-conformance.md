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

## Store epoch (best-effort)

Conformance records `store_epoch` from the real actuator DB path resolvers
(`agent_bus_store.db.connection._db_path`, cdp `_db_path`). Epoch is
`inode:size` (no mtime) — a witness only, not a cryptographic store UUID.
**Fail closed** when epoch is empty or the store file is missing. **Retry on
miss** for `agent_bus` only: safe on epoch mismatch because `idempotency_key`
dedups at the actuator; non-idempotent actuators always block on miss.

## Drain / stop-new (out of scope)

Phase-4 matrix covers eight capabilities (`identity` … `unknown_recovery`).
Separate `drain` and `stop_new` actuator ops are not modeled in this package;
cancel semantics are documented per manifest cell notes.
