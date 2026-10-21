# CDP operator proxy — reference

Sections **How this seat works** and **Standing authority** load on trigger via hop `md_read` (not inlined in the hop body).

## How this seat works

**Polling is removed** on the Castle maestro operator seat. Do not call `agent_bus` / `agent_bus_read` `tool=wait` with `poll_hint` loops or sleep between polls. Worker completion is harvested on **closeout wakes** and milestone traffic (`to=maestro` per L17 allowlist: PLAN, VERDICT, LANDED, DONE, FAILED, BLOCKED, QUESTION, RULING, ACK, AMEND, SEAT_REGISTRATION). `predicate_unmet` on a wait response meant the completion predicate was unsatisfied — it is **not** a reason to re-poll from this seat.

### Producer liveness (O14 D1–D4)

Dispatch-link liveness is substrate-derived — not thread_count, not unrelated closeout subjects.

| Need | Recipe |
|---|---|
| Pin one dispatch | `agent_bus(wait, wait_seconds=0, execution_id=<id>, …)` — use `poll_hint.arguments_json` when armed (CDP admits ship `execution_id`) |
| Read one producer | Response `producer.state` ∈ `{in_flight, terminal, unlinked, unknown}` — authority `thread_dispatch_links`. `unknown` + `no_liveness_signal`: cannot tell. `unknown` + `stream_dead_no_terminal`: dead stream, link not terminalized. |
| Scan the lane | Same `wait` response `producers[]` when unpinned |
| Watcher arm | `watch-bus-consult-and-page.py --execution-id <id>`; `tmp/watchers/<label>.state.json` carries `producer`, `verdict`, `stall_reason` |
| CHECKPOINT read | Server-rendered `### In-flight producers` under `## Derived` (O14 D3) |
| Closeout relay | Subject `status:` token must match body `status:` (O14 D4) |

Use **zero-second** `wait` only for a pinned `execution_id` snapshot — not a polling loop.

### Park → resume (steer-restart)

`team_dispatch(op="steer", steer="park_for_restart", …)` relays to GIW `POST /dispatch/{id}/park`. The parent PARKED turn is non-terminal for execution tracking: track the **resume child** terminal CLOSEOUT (`resume_of` inherits `execution_id`). `park_for_restart` continues the dispatch; mistaken admits use `cancel_discard` (see agent-bus-discipline reference).

### Watcher mis-arm (IDE seats)

When a bus-consult arm misfires (arm-only, double wake, tail-skipped, hold-turn, hang-tail, killed-tail, tab-orphan, poller-as-armed, wake-no-relay, wrong target): same turn before close — `friction()`, assert on `runbook:bus-consult-watcher`, re-arm leg 2 for **owned** labels only. `poller_alive ⇏ wake_armed`. SoT: `runbook:bus-consult-watcher`.

## Standing authority

The maestro seat holds **ulg-code** (`team_dispatch`, `agent_bus` send on the private lane, `manage` without `force` on `git_integration_worker`). It does **not** poll dispatches. Commissions are `team_dispatch(op=generate, seat=cursor-sdk, lane=B, …)`; completion and exceptions return via **worker closeout wakes** and **milestone** posts to `to=maestro`, not via `poll_hint` loops.

Under backlog on the same `work_key`, the predecessor keeps running (409 `CURSOR_SOURCE_REF_IN_FLIGHT`) — do not spawn a duplicate; stop mistaken admits with `steer=cancel_discard`. Negative lane claims require a live read of that worker thread in the same turn.

Full operator protocol: skill `cdp-operator-proxy` shell + `runbook:maestro-loop` (read at arrival; Appendix is history).
