# PROGRESS — friction:34156 review-return continuation (auto-resume on CDP reply)

work_key: friction:34156 (assertion a:34156 on service:cursor-sdk) · parent lane agent-bus:12286
branch: fix/34156-review-return-resume · base master 2cc2af8c9
retrieval report (prompt author): cortex://notes/system/threads/12286-fable-review-return-continuation-retrieval-report.md

## Phase

- [x] 1 read existing park / harvest / resume code + CDP reply posting path
- [x] 2 design (hypothesize-simulate; two rival shapes, one killed) — bound below
- [ ] 3 tests for done items 1–5 (red)
- [ ] 4 implement behind `CURSOR_SDK_AWAIT_CDP_REPLY_RESUME` (default off until last increment)
- [ ] 5 CDP code review (cdp/opus-5.5, contract=code-review)
- [ ] 6 apply simple review items; file the rest as feature frictions
- [ ] 7 land on master; GIW restart; fleet_liveness at land sha
- [ ] 8 live falsifier; MEMO to agent-bus:12286

## Phase 1 — what exists (file:line, observed)

CDP generate lifecycle (Stargate):
- admit posts pointer turn `cdp generate — {request_id}` on `dispatch_thread_id` (or a new handoff thread), body `CDP generate admitted (model=…, execution_id=…) …` — `services/universal-stargate/systems/frontier_consult/cdp_generate.py:628-661`; link via `admit_handoff_dispatch(... pipeline_id="cdp-generate", caller_agent, parent_thread_id)` :672; inflight leg `upsert_inflight_leg` :690.
- team_dispatch 202 payload: `execution_id`, `thread_id`, `thread`, `reply_from_agent="web-anthropic"`, `substrate="web-anthropic-cdp"`, `poll_hint.arguments={thread, after_turn, wait_seconds, completion="proof_reply_from", from_agent, execution_id}`, `terminal: False` — cdp_generate.py:786-824; handoff_response.py:116-146.
- terminal: `finalize_cdp_generate` (cdp_generate_reconcile.py:215) posts on-behalf turn from `web-anthropic` to `caller_agent or "dispatch"` with subject `cdp reply — {exec[:8]}` | `cdp FAILED — …` | `cdp UNVERIFIED — …` (cdp_generate_worker.py:101-108), body header `# CDP generate result (model)` + `- execution_id: \`…\`` + archive_uri (:115-192); terminates the thread_dispatch_link (`terminate_handoff_dispatch`, completed|failed) — ok path posts then terminates (:404-421), failure path terminates then posts (:338-339). `WALL_CLOCK_EXCEEDED_ABORT_UNCONFIRMED` emits nothing until reconcile decides (:308-310, :389-390) ⇒ a never-terminal generate is possible ⇒ TTL needed.
- bus `GET /threads/{t}/wait?after_turn&wait=0&completion=proof_reply_from&from_agent=web-anthropic&execution_id=X` → `{complete, producer:{execution_id,state∈unknown|unlinked|in_flight|terminal,terminal_status,…}, qualifying_reply_turn,…}` — libs/agent_bus_store/routes/wait.py:90-180, producer_projection.py:70-136.

Caller identity of a CDP generate fired from inside a cursor-sdk dispatch:
- bus turns carry `from=dispatch` (specimen 14692#3/#4); no caller dispatch id on the link. The only process that knows both the dispatch id and the fired execution_id is the stdio MCP middlebox `scripts/mcp_bridge_contract_filter.py` (`run_filtered_stdio_proxy` ALWAYS proxies — `scripts/mcp-fastmcp-remote-bridge.py:156`; filter only when contract∈{implement,pure-mechanical}). Its env has `CURSOR_SDK_DISPATCH_ID`, `ULG_STEER_SPOOL_DIR` (= `$DATA_DIR/steer-spool`, shared with GIW: `cursor_sdk_context.py:298-303, :325-327`), `ULG_DISPATCH_THREAD_ID`. It already records `pending_methods[id]=method` on requests (:217-220) and rewrites results (`_maybe_inject_steer`, :151-164).

GIW terminal path (routes/cursor_sdk.py):
- `_finalize_success` :3114 → `_deliver_sdk_closeout` :2077: closeout post :2208 → `merge_conductor_closeout_hop_authority` :2275 → `_mark_terminal_and_promote` :2282 (mark_terminal, hop reactor, `maybe_prune_worktree_on_terminal` :1677) → `_row_hop_defers_link_terminate` :2295 else `_terminate_link` :2300 → `persist_resume_retain` :2306 when designed-stop tokens.
- Retention: `maybe_prune_worktree_on_terminal` honours `dispatch_retain_active` → `resume_retain_active` needs `record_json.resume_retain` + `sdk_agent_id` + inside `cursor_sdk_timeout_retain_s()` (cursor_sdk_resume.py:242-288). ⇒ stamp `resume_retain` BEFORE `_mark_terminal_and_promote`.
- Periodic tick: `stale_lease_sweeper` every `CURSOR_STALE_SWEEP_S` (30 s) → `reconcile_stale_leases` :1826; promote half calls `_resume_parked_rows` :1908 and `sweep_conductor_hop_watchdog` :1927; boot: `routes/cursor_sdk_startup_reconcile.py`.

Park-for-restart plane (reuse, not rewrite):
- columns `park_kind, park_intent_id, parked_at, park_resumed_by, park_expires_at` + `record_json.park` — cursor_sdk_park_ledger.py (`mark_parked` sets status cancelled :186-200; `open_park_rows` filters `park_kind='park_for_restart'` :204-219 ⇒ a new kind is invisible to the restart reactor; `mark_park_resumed` CAS on `park_resumed_by IS NULL` :222-232; `park_projection` :317).
- resume child mint: `cursor_sdk_park_resume.build_park_resume_request` :138-183 (inherits thread_id, execution_id, caller_agent, model, model_knobs, skills, lane, worktree, work_key, source_ref; `prompt_preamble` = notice + original preamble; `admitted_via`; `resume_of`); `admit_cursor_dispatch(req, cfg, controller)` :3294 is the in-process admit; `_existing_child` :194 reconciles crash-between-admit-and-stamp; `_post_resumed_turn` :245; `_expire` :276 terminates link + awareness turn.
- eligibility: `cursor_sdk_resume.resume_eligibility_reason` :119 — any `park_kind` skips live/failed checks; needs `sdk_agent_id` + store dir. `reject_resume_if_ineligible` :157 (422 CURSOR_RESUME_INELIGIBLE, thread_mismatch).
- ledger admit (cursor_dispatch_ledger.py:1192): work-key gate exempts `resume_of` of any `park_kind` parent (`_resume_parent_work_key_exempt` :779-801); hand `resume_of` stamps `park_resumed_by` in-tx only for `park_for_restart` (`_stamp_hand_park_resume` :804-822); giw admits stamp after route success (:1437-1443). No refusal today when `park_resumed_by` is already set ⇒ a second hand resume mints a second child.
- conductor R1: conductor_park_harvest.py `park_harvest_continue_owed` / `fire_park_harvest_continue` keyed on PARKED_TRANSPORT + worker-thread reply; `_is_conductor_row` via `cursor_sdk_conductor_identity.is_conductor_dispatch_row` (SQL `contract` column). Non-conductor rows get nothing.

## Phase 2 — design (hypothesize-simulate)

Q: make a non-conductor cursor-sdk dispatch that terminalizes with outstanding CDP generates resume automatically (same agent, reply text in prompt, no operator act, exactly once, survives GIW restart).
OOS: conductor rows (R1 stays), nested cursor-sdk children (nest park), Stargate/agent_bus schema, wait semantics.
Detent: standard (3 axes).

frame:  incumbent = "review posted, resume after the VERDICT" + operator hand resume_of (14693#3); conductor-only reactor.
rival:  (A) park-on-outstanding-generate + GIW reactor tick (durable park columns, resume_of child when replies land) vs (B) closeout joins generates (gated coroutine blocks on the bus until replies land, then admits the child) ; attribution (a) bridge-observed generate ledger in the steer spool vs (b) new `caller_dispatch_id` wire field MCP→Stargate→thread_dispatch_links ; granularity once-after-all vs once-per-reply.
killed: (B) — not durable across a GIW restart mid-join, holds the capacity slot/write lease for the whole CDP wall (minutes–hour), and the agent is already gone so "join" alone cannot continue it; (b) — three services outside the brief's locus for a field the bridge already knows (goes to the review request as a proposal, owner service:stargate); once-per-reply — N children on one lineage, child→grandchild chain, lease contention; the common case is one review.

Bound: A + a + once-after-all-land-or-fail (TTL bounds the never-terminal case).

Simulated inputs: (1) specimen 14693/14692 ⇒ park at closeout, reactor admits `bb28…-c1` after 14692#4 lands, prompt carries VERDICT text. (2) GIW restart between park and reply ⇒ columns durable, boot/tick reactor resumes (no same-process guard for this kind). (3) two generates, one FAILED one ok ⇒ one child with both texts; one never terminal ⇒ TTL text, no hang. (4) hand resume_of racing reactor ⇒ ledger transaction serializes: loser gets 409 naming the winner child. (5) conductor row ⇒ `_is_conductor_row` short-circuits, R1 untouched. (6) no bridge record / bus down ⇒ fail-open to today's behaviour (plain complete). (7) dispatch already harvested its own reply before exit ⇒ reply turn exists at terminal ⇒ not outstanding ⇒ no park.
Dissent (front-runner): the bridge ledger is a file the GIW trusts; a bridge crash after Stargate admitted but before the append loses the record ⇒ that generate is not awaited (degrades to today). Accepted; the proposal (b) closes it later.
Falsifier: after land + GIW restart, a freeform dispatch fires a CDP generate and exits; a `resume_of` child is admitted within one `CURSOR_STALE_SWEEP_S` of the reply landing, prompt carries the reply text. Fails if the operator must resume.

### Shape

- `scripts/mcp_bridge_generate_ledger.py` (new, pure + file append): observe `tools/call` request (`team_dispatch` or `dispatch(tool=team_dispatch)` with `op=generate`) and its result; when the result is a CDP admit (`reply_from_agent=="web-anthropic"`, `terminal is False`, `execution_id`) append one JSONL row `{execution_id, thread_id, after_turn, from_agent, model, fired_at}` to `<ULG_STEER_SPOOL_DIR>/<safe dispatch_id>.cdp-generates.jsonl`. Never raises on the relay hot path.
- `scripts/mcp_bridge_contract_filter.py`: thread the observer through `_copy_downstream` (stash args per id) / `_copy_upstream` (observe result).
- `services/git_integration_worker/cursor_sdk_await_reply.py` (new): `PARK_KIND_AWAIT_REPLY="await_cdp_reply"`, flag, read bridge ledger, bus snapshot per generate (reply turn naming the execution_id after `after_turn` from `web-anthropic`; producer state as secondary), `mark_await_parked` (park columns + `record_json.park={kind, awaited[], …}` + `resume_retain`; status untouched), `open_await_rows`, `build_await_resume_request` (CDP-REPLY-RESUME v1 preamble + reply bodies, capped), `resume_await_parked_dispatches` reactor (all landed ∨ TTL ⇒ admit once; refusals recorded; permanent ineligibility ⇒ expire: terminate link + awareness turn).
- `services/git_integration_worker/cursor_sdk_await_reply_events.py` (new): `sdk.await_reply.parked|resume_admitted|resume_refused|expired`.
- routes/cursor_sdk.py: hook in `_deliver_sdk_closeout` after the closeout post, before `_mark_terminal_and_promote` (non-conductor, flag on, outstanding ≠ ∅ ⇒ park + AWAITING turn + defer `_terminate_link`); `reconcile_stale_leases` promote half calls the reactor; `admit_cursor_dispatch` maps `ResumeAlreadyAdmitted` → 409.
- cursor_dispatch_ledger.py: `_stamp_hand_park_resume` covers `await_cdp_reply`; new `ResumeAlreadyAdmitted` raised in the admit transaction when the await parent already has `park_resumed_by` (≠ this child) or a live `resume_of` child.

## Decisions

- D1 status stays `completed`/`failed` from the ordinary terminal; the park columns carry the waiting state ("not plain complete" = `park_kind=await_cdp_reply`, `park_resumed_by NULL`, `record_json.park.awaited`). `cancelled` would misreport a finished run.
- D2 resume once after all awaited generates land or fail; TTL `CURSOR_SDK_AWAIT_CDP_REPLY_TTL_S` (default 7200 s) resumes with a timeout line per missing generate. Reason: one coherent continuation, one child per lineage, simplest idempotency.
- D3 bus link stays open while await-parked (as the restart park does); the child inherits execution_id/thread_id so the caller's poll_hint continues; expiry-without-child terminates it.
- D4 fail-open: no bridge record, bridge crash, conductor row, flag off ⇒ today's behaviour.

## Open questions

- Event catalog regen required when adding event factories? (check `scripts/gen_event_catalog` convention at implement time)

## Next step

Phase 3: write red tests `services/git_integration_worker/tests/test_cursor_sdk_await_reply.py` + `scripts/test_mcp_bridge_generate_ledger.py`.

## Housekeeping

PROGRESS.md is gitignored at the repo root; it is force-tracked on this branch for continuity and removed in the last commit before landing so master does not carry it.
