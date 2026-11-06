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

## Operational Authority Boundary (IntentStore Protocol vs Reference Fixture)

Production execution truth lives in the **Cortex concern aggregate** (`libs/cortex_store`).
Callers bind the `IntentStore` protocol to that aggregate (or its successor) for
operational reads and writes. `ReferenceSqliteIntentLedger` in this package is **only**
a reference / conformance-fixture implementation used by phase-4 tests and harness
code. Do **not** stand it up as a parallel operational store beside cortex_store.

## Reconcile

1. **Terminal local states** (`completed`, `failed`, `cancelled`) — no lookup admit.
2. **Found** — adopt owner state (no rank regression); ref conflicts block with
   persisted `blocked_reason` (`ref_conflict` is compensation-only).
3. **Retry on miss** — only when `adapter.admit_idempotent` is true (today: `agent_bus` only),
   **recorded and current `store_epoch` match**, and `claim_retry` wins the CAS race.
   Retry reuses persisted `payload.admit_kwargs`. CDP miss with a durable local row
   does not retry (safe block).
4. **All other misses** (stargate, epoch mismatch, empty epoch) — `blocked_unknown`, zero redispatch.
5. **Open CDP leg / bridge MSG without correlated REPLY** — after local receipt, adopt
   `accepted`/`running` with `owner_unknown_reason` recorded; steady across repeated
   reconciles (does not flip to `blocked_unknown` on the next pass). This is conservative
   for duplicate prevention but does not distinguish healthy in-flight legs from true unknowns.

Compensation from `blocked_unknown` requires an explicit `CompensatingAction`.
`blocked_unknown` re-query is read-only at the actuator (no second admit) until owner-proven
terminal evidence with matching `actuator_ref` (except `ref_conflict`, which never auto-adopts).

## Verification

```bash
ruff check libs/execution_conformance
ruff format --check libs/execution_conformance
pytest libs/execution_conformance -q
```

Machine-readable capability matrix: `execution_conformance.manifest.MANIFEST`.

## Store epoch (persisted UUID)

Each durable owner SQLite file carries a singleton meta-table
`_execution_store_epoch` with a UUID written at first open (`INSERT OR IGNORE`).
Resolvers: `agent_bus_store.db.connection._db_path`, cdp `_db_path`. Stargate uses
a per-tracker-instance `uuid4` (not `id(tracker)`). **Fail closed** when the DB
file is missing, empty, or unreadable (`store_epoch == ""`). **Retry on miss**
for `agent_bus` requires `record.store_epoch == lookup.store_epoch`; recreate or
replace the owner DB changes epoch and blocks non-idempotent retry paths.

## Drain / stop-new (out of scope)

Phase-4 matrix covers eight capabilities (`identity` … `unknown_recovery`).
Separate `drain` and `stop_new` actuator ops are not modeled in this package;
cancel semantics are documented per manifest cell notes.
