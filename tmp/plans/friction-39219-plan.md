# friction:39219 — Conductor hop steer-inject spool inheritance

**Genre:** implementation plan (cursor-sdk / GIW implement seat)  
**Work key:** `friction:39219`  
**Branch:** `cursor-sdk/lane-16681` @ `1c6e371be26d9e3a0aba2c09a2951b851141a2bd` (citation re-verification base)  
**Specimen:** agent-bus:16671 — operator steer inject `9ec4d5defdaa1f28` on predecessor `08e40b79e92f-8677c25c`; successors `561fcb8810d0-1ac6ba48` (hop reactor) and park-harvest re-admit; CHECKPOINT residue at #22 carried none of the inject constraints; operator re-injected at #20 and #27.

---

## 1. Defect (two sentences)

Conductor successor admits (hop reactor, park-harvest continue, hop watchdog) mint a new `dispatch_id` and clone ledger/`team_dispatch` fields via `build_hop_team_dispatch_body`, but steer-inject rows live only in `{steer_spool_dir()}/{predecessor_dispatch_id}.json`, so the successor bridge reads an empty or unrelated spool file. When the predecessor closes, `apply_steer_undelivered_closeout` runs `expire_pending_entries` and records metadata without directive text, so any inject the predecessor bridge never consumed is lost before the successor can see it.

**Confirmed citations (lane-B @ `1c6e371`, read this dispatch):**

| Claim | Citation | Status |
|-------|----------|--------|
| Deposit → spool append | `cursor_sdk_steer_inject.py:221` (`append_spool_entry`) | **confirmed** |
| Spool path shape | `mcp_bridge_steer_inject.py:78-80` (`spool_path`); `cursor_sdk_context.py:298-303` (`steer_spool_dir`) | **confirmed** |
| Bridge consume keyed to env dispatch id | `mcp_bridge_steer_inject.py:336` (`consume_next_steer_envelope`); `:446-450` (`CURSOR_SDK_DISPATCH_ID`) | **confirmed** |
| Terminal closeout expires pending | `cursor_sdk_steer_inject.py:344-357` (`apply_steer_undelivered_closeout`); `routes/cursor_sdk.py:2313-2319` (call site); `mcp_bridge_steer_inject.py:383` (`expire_pending_entries`) | **confirmed** |
| Closeout residue omits directive text | `cursor_sdk_steer_inject.py:330-341` (`steer_undelivered_field`) | **confirmed** (nested trace cited `mcp_bridge_steer_inject.py:330-340` — **wrong file**; logic is in `cursor_sdk_steer_inject.py`) |
| Recovery filters on running dispatch id | `cursor_sdk_steer_inject.py:452-453` (`body.dispatch_id` match) | **confirmed** |
| Hop body clone, no spool | `conductor_hop.py:1383-1464` (`build_hop_team_dispatch_body`); `:1669`, `:1688` (reactor); `conductor_park_harvest.py:746`, `:750`; `conductor_hop_watchdog.py:229`, `:232`; park defer `:1657-1658` | **confirmed** |
| CDP ledger inherit only, not steer JSON | `cursor_sdk_generate_ledger_inherit.py:32-75` (`*.cdp-generates.jsonl` under spool root) | **confirmed** |
| Successor id minted on successful POST | `conductor_hop.py:1477-1516` (`post_conductor_hop_team_dispatch`) | **confirmed** |

**Moved / unverified from nested trace:** wrapper `work_outcome: unverified`; all file:line rows above were re-read on this checkout. Trace base sha `1c6e371` matches current HEAD.

---

## 2. Design — how inject entries reach the successor

### 2.1 Mechanism (mirror `inherit_outstanding_generates`)

Add **`inherit_undelivered_steer_inject`** (name provisional) in `scripts/mcp_bridge_steer_inject.py`:

- **Inputs:** `parent_dispatch_id`, `child_dispatch_id`, optional `spool_dir`, optional `inherited_via` string (e.g. `conductor_hop`).
- **Behavior:** Under per-file locks (same `_spool_lock` as `append_spool_entry`), read parent spool JSON; for each eligible row (see §2.2), append a **new `pending` row** on the child spool with:
  - same `directive`, `authority_turn_id`, and `ttl_s` as the parent row;
  - **fresh** `deposited_at` (now) and **fresh** random `entry_id`;
  - provenance fields on the child row: `origin_entry_id`, `origin_deposited_at` (copied from parent row), `inherited_from` (= parent dispatch id), `inherited_via` (caller-supplied string, e.g. `conductor_hop`).
- **Fail-open:** Never raise into hop admit (pattern: `cursor_sdk_generate_ledger_inherit.py:32-57`, `routes/cursor_sdk.py:4404-4420`).
- **Idempotency:** Skip when child spool already contains the same `authority_turn_id` in `pending`, `delivered`, or `expired` (same dedupe strategy as `recover_undelivered_steer_from_thread` / `_spool_known_authority_ids`).

**Call site (single hook, covers legs a/b/c):** Inside `post_conductor_hop_team_dispatch` in `conductor_hop.py`, immediately after a successful Stargate POST (`resp.status_code < 400` and no payload error), when `hop_from` is the predecessor:

1. Resolve **`successor_dispatch_id`** from the Stargate response payload: `payload.get("dispatch_id")`, else `payload.get("execution_id")` (both strings, stripped).
2. Compare to **`admit_dispatch_id`** (the id sent on the wire and used for `claim_stop_service`). If they differ: log a **warning** with both ids and **skip** inherit (do not copy to either id).
3. Otherwise call:

```text
await asyncio.to_thread(
    inherit_undelivered_steer_inject,
    parent_id=stop_id,
    child_id=successor_dispatch_id,
    inherited_via="conductor_hop",
)
```

Do **not** copy on failed POST / released stop claim. All three paths (`maybe_fire_conductor_hop_reactor`, `fire_park_harvest_continue`, watchdog admit) already funnel through `post_conductor_hop_team_dispatch`.

Optional thin wrapper in `cursor_sdk_steer_inject.py` for GIW imports + frontier event (`SdkSteerInjectInherited` or extend existing spool events) — implement seat choice; event is desirable for forensics.

### 2.2 Which rows to carry

| Parent bucket | Parent state | Copy to child? | Child bucket | Rationale |
|---------------|--------------|----------------|--------------|-----------|
| `pending` | Not yet claimed by predecessor bridge | **Yes** | `pending` | Direct undelivered inject. |
| `expired` | Terminal closeout (`expire_pending_entries`) or TTL skip, never in `delivered` | **Yes** | `pending` | Hop runs after predecessor terminal; closeout often expires pending **before** copy — must read `expired` or directive is gone (ordering hazard). |
| `delivered` | Predecessor bridge already injected envelope | **No** | — | Successor is a new bridge session, but re-queuing parent `delivered` rows is out of scope for this friction; standing-ruling propagation across hops is **`friction:a:39221`**. |
| `expired` | Same row already copied to this child (dedupe hit) | **No** | — | Idempotent retry. |

**Should the successor see an already-delivered ruling again?** **Not via this change.** Only **pending** and **expired-never-delivered** rows copy forward. Operator re-inject or **`friction:a:39221`** covers standing-ruling propagation.

**Explicit non-goals for copy:** Do not mutate parent spool during copy (read-only parent). Do not copy rows missing `directive` text. Do not copy parent **`delivered`** rows.

### 2.3 Ordering vs `expire_pending_entries`

Typical sequence: predecessor terminal closeout → `apply_steer_undelivered_closeout` → pending → `expired` → hop reactor posts successor. Implementation **must** treat parent **`expired`** (terminal reason) as a source equal to **`pending`**. Add a test that runs closeout expire on parent, then inherit, then `claim_pending(child)` succeeds.

Race (narrow): inherit between last pending claim and expire — locking parent file during read+copy prevents double delivery to two bridges; child copy is append-only.

### 2.4 TTL refresh on copy

Child rows get a **fresh** `deposited_at` at copy time so TTL checks in `consume_next_steer_envelope` / claim paths do not immediately skip inherited directives after long hop delays. Parent timestamps are preserved only in `origin_deposited_at` for forensics.

---

## 3. `files_expected`

| Path | Change |
|------|--------|
| `scripts/mcp_bridge_steer_inject.py` | `inherit_undelivered_steer_inject` (+ helpers: list eligible rows, dedupe by `authority_turn_id`, provenance fields) |
| `services/git_integration_worker/cursor_sdk_steer_inject.py` | Optional re-export / event emit wrapper (if events live here) |
| `services/git_integration_worker/cursor_sdk_closeout/conductor_hop.py` | Call inherit from `post_conductor_hop_team_dispatch` after successful POST; resolve successor id from payload |
| `services/git_integration_worker/tests/test_cursor_sdk_steer_inject.py` | Unit tests for inherit helper (temp spool dir) |
| `services/git_integration_worker/tests/test_conductor_hop.py` | Hop POST success → spool copied (mock Stargate HTTP); payload vs admit id assertion |

---

## 4. Acceptance criteria

1. **AC1 — pending survives hop:** Given parent spool with one `pending` inject, after successful `post_conductor_hop_team_dispatch`, child spool has that directive as `pending` with fresh `deposited_at` / `entry_id` and provenance fields; `claim_pending(child_id)` returns it; parent row unchanged.
2. **AC2 — expired-after-closeout:** Given parent with inject moved to `expired` via `expire_pending_entries` (terminal reason), inherit still copies to child `pending` and bridge can claim on child.
3. **AC3 — delivered not copied:** Given parent with inject only in `delivered`, inherit does **not** create child rows; predecessor `delivered` unchanged.
4. **AC4 — idempotent:** Second inherit with same parent/child pair does not duplicate `authority_turn_id` on child.
5. **AC5 — hop fields unchanged:** `build_hop_team_dispatch_body` output unchanged except for intended hop metadata; no inject payload on wire body (spool-only path).
6. **AC6 — failed POST:** When Stargate POST fails or stop not claimed, inherit is **not** invoked; parent spool unchanged.
7. **AC7 — copy failure after POST ok:** If inherit raises after a successful Stargate POST, the hop still returns success (fail-open); the failure is logged at exception level and emits an observable event (or structured log) with parent/child ids and error detail. **Eligible rows are not copied for that hop** — they are lost until the operator re-injects. There is **no** automatic retrier or second inherit attempt in this change.
8. **AC8 — payload successor id:** Inherit targets the id from the Stargate response (`dispatch_id`, else `execution_id`). When mock payload id matches admit id, child spool is written; when payload id differs from `admit_dispatch_id`, inherit is skipped and a warning is logged.

---

## 5. Tests (failure modes)

| # | Breaks when | Test name (proposed) | Setup |
|---|-------------|----------------------|--------|
| T1 | **Service down** (Stargate unreachable) | `test_hop_inherit_skipped_when_post_fails` | Mock HTTP error on POST; assert no child spool file created |
| T2 | **Concurrency** with `append_spool_entry` | `test_inherit_while_append_serializes` | Thread append on parent while inherit runs; no lost rows, no duplicate `authority_turn_id` on child |
| T3 | **Partial failure** (POST ok, copy raises) | `test_inherit_fail_open_copy_lost_no_retry` | Mock inherit to raise once after POST ok; hop returns ok; child spool has **no** rows; **no** second inherit call; event/log emitted |
| T4 | **Ordering** vs `expire_pending_entries` | `test_inherit_reads_expired_after_terminal_closeout` | Parent pending → `apply_steer_undelivered_closeout` → inherit → child claim succeeds |
| T5 | **cancel_discard successor** | `test_inherit_then_successor_cancel_expires_child_pending` | Copy to child; simulate successor terminal + closeout expire; child pending cleared; parent unchanged |
| T6 | **TTL refresh** | `test_inherit_fresh_deposited_at_delivers_after_old_ttl` | Parent `pending` row with `deposited_at` &gt; 300 s ago; inherit to child; run `consume_next_steer_envelope` for child with **`CURSOR_SDK_DISPATCH_ID` unset** (ledger/env path as in existing steer tests); envelope still delivers |
| T7 | **Delivered excluded** | `test_inherit_skips_delivered_parent_rows` | Parent delivered only → no child spool rows |
| T8 | **Payload id mismatch** | `test_hop_inherit_skipped_when_payload_id_differs_from_admit` | Mock POST success with payload `dispatch_id` ≠ wire `admit_dispatch_id`; assert warning logged and no child spool write |

**Test constraints:** Use temporary `spool_dir` (existing test pattern in `test_cursor_sdk_steer_inject.py`). **No** `manage` stop/start, **no** signals to live GIW, **no** subprocess that touches fleet services.

---

## 6. Live-process hazard

- Code ships in **`git_integration_worker`**. It becomes active only after **GIW process restart** (drain-gated; owned by web-anthropic / lead — **not** this implement seat).
- Until restart: behavior remains “successors do not inherit inject.”
- Implement seat: land on lane branch; lead verifies restart intent separately.
- Tests must not stop/start/signaled GIW or Stargate.

---

## 7. Out of scope

- Live conductor thread **16671** (no steer, cancel, or dispatch).
- **`friction:a:39221`** — standing-ruling / operator-steer propagation across conductor hops (re-queue of parent **`delivered`** injects for every successor). This plan copies only **pending** and **expired-never-delivered** rows.
- `resume_of` / `park_for_restart` child admit (only copies CDP generate ledger today at `routes/cursor_sdk.py:4397-4414`) — separate friction unless product binds steer across restart hops the same as conductor hops.
- Cortex writes, agent-bus behavior changes, Stargate API shape changes.
- Land to `master`, GIW restart, force GIW.
- Scoreboard / CHECKPOINT propagation of inject text (spool inheritance is the minimal fix aligned to trace).
- Automatic **retrier** or background re-inherit after copy failure.

### Open questions

1. ~~**TTL on copied rows**~~ — **Resolved:** refresh `deposited_at` on child; preserve parent time in `origin_deposited_at` (§2.4, T6).
2. **Event name:** New `SdkSteerInjectInherited` vs extending `SdkSteerInjectSpooled` with `inherited_from` — pick one for Event Service queries.
3. **Multi-hop chains:** Copy from immediate `hop_from` only (each hop re-inherits whatever is still pending/expired on the immediate parent) — default immediate parent only; no walk of distant ancestors.
4. ~~**`entry_id` on copy**~~ — **Resolved:** new random `entry_id` per child row; dedupe solely on `authority_turn_id`; `origin_entry_id` retains parent value.

---

## Retrieval-before-authoring (plan packet)

| Scope | Query | Yield |
|-------|-------|-------|
| `llm_prompting` | implementation plan structure constraints last | **null (off-topic)** — weak_match reformulation not run; DCCD/constraint papers not plan genre |
| `claude_api` | long context instructions placement end | **ok** — queries/instructions after long context (`prompting-best-practices.md`) |
| `prompt_injection` | instruction hierarchy constraints last | **weak** — post-prompting / instruction-last defenses (tldrsec); applied: acceptance criteria and implement instructions after design facts |
| `agent_skills_research` | progressive disclosure procedural memory | **ok** — modular skill units loaded on match; applied: plan sections ordered defect → design → files → AC → tests |

**Choice-to-evidence:** Plan structure (facts first, binding AC last) — `claude_api` placement guidance + `prompt_injection` post-prompting pattern. Inherit helper shape — **my judgment**, patterned on observed `inherit_outstanding_generates` (code, not corpus).

**target:** friction:39219

---

## Implement seat — before you post

For each change, name the input or state where it breaks (service down, concurrency with `append_spool_entry`, partial failure (successor POST ok, copy fails — rows lost, no retrier), ordering against `expire_pending_entries` at predecessor closeout, successor `cancel_discard`'d, Stargate payload id ≠ admit id, parent row only in `delivered`) and the test that covers it. Change only what this plan names; anything else goes in the review request as a proposal, not in the diff. Run `git diff --stat <base>..HEAD` and account for every file. Once the request is posted, stop committing.
