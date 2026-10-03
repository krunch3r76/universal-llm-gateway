# PROGRESS — friction:34156 review-return continuation (auto-resume on CDP reply)

work_key: friction:34156 (assertion a:34156 on service:cursor-sdk) · parent lane agent-bus:12286
branch: cursor-sdk/lane-14701 · base master 2cc2af8c9
retrieval report (prompt author): cortex://notes/system/threads/12286-fable-review-return-continuation-retrieval-report.md

## Phase

- [x] 1 read existing park / harvest / resume code + CDP reply posting path
- [x] 2 design (hypothesize-simulate; two rival shapes, one killed) — bound below
- [x] 3 tests for done items 1–5 (red → green)
- [x] 4 implement behind `CURSOR_SDK_AWAIT_CDP_REPLY_RESUME` (default off until last increment)
- [ ] 5 CDP code review (cdp/opus-5.5, contract=code-review)
- [ ] 6 apply simple review items; file the rest as feature frictions
- [ ] 7 land on master; GIW restart; fleet_liveness at land sha
- [ ] 8 live falsifier; MEMO to agent-bus:12286

## Bound shape (unchanged) + R1 override

Bound: park-on-outstanding-generate + GIW reactor tick; bridge generate ledger; once-after-all-land-or-fail (TTL).

**R1 (operator, overrides simulated input 7):** "already harvested" means the bridge
observed the agent *receive* the qualifying reply (wait/get/fetch result returned to
the agent for that execution_id), not merely that the reply turn exists on the bus.
Landed-but-unconsumed ⇒ park and resume at once with the reply text. Received before
exit ⇒ no park. Landed after exit ⇒ park, resume on the tick.

## Files

| Path | Role |
|---|---|
| `scripts/mcp_bridge_generate_ledger.py` | fire + received JSONL rows |
| `scripts/mcp_bridge_contract_filter.py` | observer on copy loops |
| `services/git_integration_worker/cursor_sdk_await_reply.py` | park, snapshot, resume request, reactor |
| `services/git_integration_worker/cursor_sdk_await_reply_events.py` | `sdk.await_reply.*` |
| `services/git_integration_worker/cursor_sdk_await_reply_gate.py` | `ResumeAlreadyAdmitted` |
| `services/git_integration_worker/cursor_dispatch_ledger.py` | refuse + stamp await park |
| `services/git_integration_worker/routes/cursor_sdk.py` | closeout hook + reconcile + 409 map |
| `services/git_integration_worker/models/cursor_api.py` | `giw_await_reply_resume` admitted_via |

## Next step

Flag default on → remove this PROGRESS.md → CDP code review.

## Housekeeping

PROGRESS.md is gitignored at the repo root; it is force-tracked on this branch for continuity and removed in the last commit before landing so master does not carry it.
