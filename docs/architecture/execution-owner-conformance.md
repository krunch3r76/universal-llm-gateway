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
   persisted `blocked_reason` (`ref_conflict` is sticky and exits only via
   `resolve_ref_conflict`; `compensate_unknown` on ref_conflict yields
   `outcome_unknown` and the next reconcile re-blocks as ref_conflict).
3. **Retry on miss** — only when `adapter.admit_idempotent` is true (today: `agent_bus` only),
   **recorded and current `store_epoch` match**, and `claim_retry` wins the CAS race.
   Retry reuses persisted `payload.admit_kwargs`. CDP miss with a durable local row
   does not retry (safe block).
4. **All other misses** (`stargate_pipeline` with `admit_idempotent=False`, epoch mismatch,
   unprovisioned epoch on crash-recovery retry) — `blocked_unknown`, zero redispatch.
   **`claim_retry` loss** — re-read ledger; return advanced state or a non-mutating
   in-flight outcome (never fall through to `_block_unknown` after a lost CAS).
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

## Store epoch (read-only witness + explicit provisioning)

Conformance **never** writes owner SQLite from `lookup` / `store_epoch` / `reserve`.
`read_store_epoch(path)` opens `file:…?mode=ro` and returns
`EpochReadResult(status=ok|unprovisioned|unavailable, epoch=…)`.

- **`provision_store_epoch(path)`** — tests/bootstrap only; creates `_execution_store_epoch`.
- **`rotate_store_epoch(path)`** — post backup/restore; assigns a new UUID so retry
  paths fail closed with `epoch_mismatch`.

**Retry on miss** (`agent_bus` only) requires matching non-empty UUIDs. **Unprovisioned**
epoch allows first `dispatch_intent` admit without mutating owner DB; crash-recovery
retry fails closed (`epoch_unverified`). **Unavailable** (locked/busy) returns a
**transient** reconcile outcome — no `_block_unknown`, no `floor_retry` — so a later
pass can succeed.

Stargate shares one epoch + lock per `PipelineExecutionTracker` instance (WeakKeyDictionary).

CDP `CdpInflightAdapter.admit` uses reference `INSERT OR REPLACE`; production requires
owner insert-if-absent wiring.

## Drain / stop-new (out of scope)

Phase-4 matrix covers eight capabilities (`identity` … `unknown_recovery`).
Separate `drain` and `stop_new` actuator ops are not modeled in this package;
cancel semantics are documented per manifest cell notes.
