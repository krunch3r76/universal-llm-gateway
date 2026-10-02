## Run to completion (binding default)
<!-- width-seat:v1:start -->
> **Width seat** (generated from `libs/implement_admission/conductor_width_seat.py`; do not hand-edit): ACTIVE = `cdp/opus-5.5` · `reasoning_effort=high` · `effort_when_bind_gates_wave=max` only when a bind gates a wave · CLI `--model opus-5.5` · usage channel **Opus**. SKETCH (G1) = `cdp/opus-5.5` · `reasoning_effort=extra`. SKEPTIC (G4) = `cdp/opus-5.5` · `reasoning_effort=extra` (one rung above ACTIVE; cross-family review stays on this effort). RESTORE = `cdp/fable-5.1` · `reasoning_effort=high` (only when Kaywan asks; set `ACTIVE = RESTORE`).
<!-- width-seat:v1:end -->


The packet admit is a **standing** authorization for the whole mission, not a
per-G-row one. Default posture once running:

- **¬ pause between G-rows for a "continue?" ack.** Drive from the first OPEN
  G-row to the last in one continuous commission. CHECKPOINT is a progress
  report, not a waypoint that blocks on a reply before the next G-row starts.
- **Per-G-row checkpoint-and-hop (binding default, operator 2026-09-01,
  `a:31787` → bind `a:31807`).** "One continuous commission" names the
  mission, not a single dispatch. At each gated G-row *close* (its witness
  hangs) where no designed stop is owed, the conductor **ends its own
  dispatch** and the substrate admits its successor on the same worker thread
  and Lane-B checkout: append the score journal, write the hop CHECKPOINT
  (§ Hop CHECKPOINT contract), then finish with `stop: ROW_HOP` as the last
  line of the closeout. **Never** call
  `team_dispatch(reuse_thread=<this thread>)` yourself — while you are live it
  is refused (`422 CURSOR_WORKER_THREAD_OCCUPIED`, holder = you; `a:30799` /
  9675#8) and would orphan the thread if it were not. The successor is a
  **new top-level `dispatch_id`, ¬ `nest_under`**, same `source_ref`, same
  branch — the same shape as crash-resume, fired by the substrate on your
  terminal instead of by a liaison after a crash. Not the pause-and-ask
  anti-pattern (7419): nobody is asked, nothing waits; the mission still runs
  start-to-finish under one standing admit as a chain of short dispatches.
  Bounds each dispatch's token footprint to roughly one movement and caps a
  crash's loss to the in-flight row (`a:31786`). A mission that ends inside
  one dispatch simply stops `DONE`.
- **Density hop (GIW steer):** when a same-row steer asks you to write the
  ten-field hop CHECKPOINT with Next-pickup = the open G-row and end with
  `stop: ROW_HOP`, do that and stop the dispatch. GIW measures context for you.
- Owed stops win at a boundary:
  `stop_after` ⇒ `ROW_PINNED`; explicit see-score or `OPERATOR_GATE` ⇒
  `ROW_PINNED`; G3→G5 fires in-process CDP score-ratify on ACTIVE
  (reasoning_effort and effort_when_bind_gates_wave per ACTIVE when a bind gates a wave)
  via `team_dispatch(op=generate, model=cdp/opus-5.5, contract=freeform, purpose=ask, dispatch_thread_id=<this worker thread id>)` then continues — a live summoning chat is not a
  human stop; named
  hold ⇒ `HOLD_MERGE`. A live nested child forbids the hop (W3) — harvest,
  then hop. If you end with the mission open and **no** token, the substrate
  still re-admits you (budgeted) — that is the safety net, not the default.
- **¬ a second gate on the mission's own merge.** `git-posture` gates
  `git_land` / `git_integrate` on "operator directs a merge" — for a conductor
  mission, admitting the packet **is** that direction, standing for the
  mission's own Lane-B branch. Land on green (tests pass, AC met) as part of
  *completion*; do not round-trip for a separate "ok to merge?"
- **Stop only for a true operator-only gate** — credentials, an irreversible
  non-revertible act, or a genuine unranked fork. Forks go to the judgment
  ladder (`cdp/opus-5.5`; `cdp/fable` only when Kaywan asks) first; `needs-attended`
  is for the human-only remainder, not for "should I proceed" or "should I
  merge."
- **Named exception overrides the default.** If a mission genuinely needs the
  merge held for review (destructive scope, force-push, cross-repo blast
  radius), name that in the packet `<invariants>` — silence means rubber-
  stamped, not the other way around.
- **Flagging is not a hold.** Judging a mission large, doctrine-touching, or
  risky and wanting to "lay out the plan first" is not a named exception —
  it is commentary. Nest Composer and drive every G-row to green; note the
  concern on the CHECKPOINT while the work proceeds. Stopping before any
  G-row starts — zero files touched, nothing nested — is the same violation
  as skipping straight to a merge ask (refuse-and-close, incident 7419;
  distinct from absorb, incident 7407, which hand-codes instead of nesting).
- **After-ship `cdp/opus-5.5` code review (a:32146) is a stronger-model gate, not
  a background nicety.** On codework that claims land / cert / terminal `DONE`:
  **review harvest ≺ land ≺ DONE** (dogfood 10013 / a:32221–32222; 9638 hop3 /
  a:32226). Fire `team_dispatch(model=cdp/opus-5.5, job=delivery-review, …)`
  and **harvest a real verdict body** before those claims. Filtered nested
  seats (`implement` / `pure-mechanical`) see that same call on `tools/list`. The
  stdio bridge refuses a `tools/call` outside the contract primary list, and
  refuses `team_dispatch` unless the arguments are the review shape
  (`op=generate`, `job=delivery-review`, `model` in `cdp/opus-5` | `cdp/opus-5.5` |
  `cdp/fable`, `job=freeform`, no other keys). That bound is the bridge
  process for the filtered seat; it is not a server-side identity check on
  `/mcp/code`.   A consult already admitted and still healthy is not a skip — poll until harvest.
  CDP consult refused or failed (including `X display exhausted`, `Chrome on :9225 did not reach CDP`, empty body,
  `stall_stage=mark_terminal`, `stall_stage=completed_without_proof`): do not wait and do not retry the CDP pool.
  Choose at once by the question and record `cdp_fail_route` on the scoreboard row.
  `nested-grok`: nested `team_dispatch` `seat=cursor-sdk` `model=cursor/grok-4.7` `job=freeform` `model_knobs={"effort":"xhigh"}`.
  `operator`: post the question and its context on this worker thread `to=web-anthropic`, plus a one-line pointer on the operator lane (`parent_thread`) `to=web-anthropic`. The operator answers on the worker thread. `CONSULT_PENDING` watches that reply.
  Name that route in the artifact id (`G4-REVIEW-nested-grok`, `G4-REVIEW-operator`, `G6-REVIEW-nested-grok`, `G6-REVIEW-operator`) and in the Gated row's Stops cell as `cdp_fail_route=<route>`. The fold records `witness:BIND:<id>:route=<route>` for the artifact that matches the stamp. A second verdict on the same gate is commentary unless that Stops stamp changes later.
  Do not label DEFERRED and continue. Do not substitute Composer. Do not `PARKED_TRANSPORT` on this failure. Do not discard the lane.
  Optional Sidecar overlays that the
  scoreboard marks non-blocking remain commentary only — they do not replace
  the gated after-ship picker. Does not replace path-sim R-after (Grok).
  **Reader (BINDING):** `fired(gate) ⇒ reader = summoning-thread lead at harvest`.
  `¬wait(latency) ≠ ¬read`. Harvest MUST quote overlay `read_sha256` ∨ name why
  unread. A check with no reader is not an independent check (dogfood 9655 /
  assertion 30663).

