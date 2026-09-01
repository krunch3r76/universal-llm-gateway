---
name: cursor-sdk-thread-consolidation
description: Before/after team_dispatch(op=generate, seat=cursor-sdk) — the Q/R thread-consolidation contract (dispatch_thread_id shell, preflight gate, poll recipe). Prevents the thread-doubling failure mode.
trigger_short: "team_dispatch op=generate seat=cursor-sdk ∨ dispatch_thread_id ∨ poll_hint ∨ thread doubling"
skill_category: dispatch-delegation
skill_binding: {skill_class: workflow}
related_skills: ["orchestrator-workflow", "dispatch-shape", "agent-bus-discipline", "consult-routing"]
trigger_match_terms: ["cursor-sdk-thread-consolidation", "dispatch_thread_id", "reuse_thread", "consolidation_split_warning", "poll_hint", "thread doubling", "team_dispatch", "cursor-sdk generate"]
---

# cursor-sdk Q/R thread consolidation

**Applies to:** `op=generate, seat=cursor-sdk` dispatches from lead/orchestrator seats.

The platform (`resolve_cursor_sdk_thread_targets`) auto-consolidates cursor-sdk Q/R — routing the result turn back to the dispatch shell instead of minting a new thread — **only** when `dispatch_thread_id` is **numeric, pending, and empty** (no prior turns). When those three conditions are not met, Stargate auto-provisions a separate result thread, producing the thread-doubling symptom the contract below prevents.

## Decision table

| Shape | Condition | Platform behavior |
|---|---|---|
| `dispatch_thread_id=<numeric pending empty shell>` | Pre-staged shell; no prior turns | Auto-consolidation fires; result lands on the shell; no new thread minted |
| `reuse_thread=<id>` (explicit) | Active arc thread must receive the result | Platform routes result to the named thread; `dispatch_thread_id` remains the context/compaction key |
| Neither, or `dispatch_thread_id` names an active non-empty arc | Fresh dispatch or mistaken arc pointer | Stargate auto-provisions a new result thread; lead polls `poll_hint` on the new thread |

## Anti-pattern — pre-created shell pointing at a different active arc (2672 failure mode)

**Never** pre-create a pending shell thread **and** point `dispatch_thread_id` at a different active arc. The shell is orphaned; Stargate sees the active `dispatch_thread_id` as non-consolidatable and mints a third result thread silently.

```
# WRONG — three threads: arc-coordination (A), orphaned shell (B), auto-minted result (C)
create_thread(slug="sdk-result-shell") → thread B  (pending, empty)
team_dispatch(op=generate, seat=cursor-sdk, …, dispatch_thread_id=A)
# A is active/non-empty → consolidation does NOT fire → Stargate mints C

# RIGHT — two threads: arc-coordination (A) stays lean; shell (B) serves as context + result
create_thread(slug="sdk-result-shell") → thread B  (pending, empty)
team_dispatch(op=generate, seat=cursor-sdk, …, dispatch_thread_id=B)
# B is pending+empty → consolidation fires → result lands on B; lead polls B via poll_hint
```

Since the `consolidation_split_warning` ship (commit 10112551), `team_dispatch(op=generate, seat=cursor-sdk)` emits a warning in the response when `dispatch_thread_id` names an active non-pending arc and `reuse_thread` is absent. The warning is advisory — dispatch proceeds — but treat it as an immediate signal to audit the thread binding before reading results.

## Pre-dispatch preflight (mandatory gate — verify BEFORE calling `team_dispatch`)

**Read before you fire.** Both consolidation conditions must be confirmed by a live probe, not assumed. Performing this check after `team_dispatch` returns is too late — the fork is already admitted by the time `consolidation_split_warning` surfaces, and the thread-doubling outcome cannot be unwound without manual cleanup.

```
agent_bus(tool="fetch", arguments='{"thread": "<dispatch_thread_id>", "compact": true, "last": 1}')
```

| Gate | Required value | Fail → halt and correct BEFORE dispatching |
|---|---|---|
| `bus_lifecycle_state` | `"pending"` | Thread was activated (turns staged or status changed). Close it; create a fresh pending shell; re-point `dispatch_thread_id`. |
| `turn_count` | `0` (zero prior turns) | A staging turn was posted to the shell, defeating the empty condition. Delete the errant turn(s) to restore empty state — OR explicitly pass `reuse_thread=<id>` when delivery to an existing thread is intentional and understood as non-consolidating. |

**Halt-on-fail invariant:** stop before calling `team_dispatch` when either gate is red. The two-thread split that results is not cosmetic — it produces a coordination thread (A) and an orphaned result thread (B) that the lead must track separately, defeating the purpose of consolidation.

*Grounded in friction 20133 — lesson_gap, service:mcp-server, 2026-06-19. Thread 2729 was created active with a staged instruction turn before dispatch; both consolidation conditions were defeated; `consolidation_split_warning` fired but the lead did not act on it, accepting threads 2729 + 2730 instead of halting and correcting.*

## Lead-seat obligation

| Thread role | Owner | Contains |
|---|---|---|
| **Arc coordination** | Lead (pre-created pending shell) | Context pre-staging turns; briefings; resumptions across the arc lifecycle |
| **SDK result / closeout** | Stargate (on-behalf delivery) | Single closeout turn carrying the sidecar pointer and acceptance evidence |

For consolidated Q/R, these must resolve to **one thread**. Stage context on the pending shell and pass that shell as `dispatch_thread_id`; Stargate delivers the result turn to the same thread, closing the loop without extra threads to track or poll.

Cross-links: `consult-routing` § Implement lane — source_ref (dispatch shape authority); `dispatch-shape` § Handoff poll hints (use `poll_hint.arguments_json` to poll the consolidated shell thread post-dispatch); `agent-bus-discipline` § Dispatch polling (cursor-sdk closeout matcher); `agent-bus-discipline` § Thread lifecycle (close the consolidated thread after reading the closeout — reduces false-unread counts at next boot).

## Post-dispatch poll recipe (cursor-sdk — mandatory)

After `team_dispatch(op=generate, seat=cursor-sdk)`, poll completion from `poll_hint` — **not** subject substring matching.

**Canonical poll (preferred):**

```python
agent_bus(tool="wait", arguments=poll_hint.arguments_json)
# wire-equivalent: wait(thread=N, after_turn=T, completion="first_reply_from", from_agent="cursor-sdk")
```

Re-call until `complete=true`. The machine closeout turn always posts with `from_agent="cursor-sdk"` regardless of subject wording.

**Anti-pattern — subject-regex polling:**

`¬` match on subject containing `closeout|complete`. Machine closeout subjects are `cursor-sdk dispatch {dispatch_id}` (and `FAILED`/`DELIVERY FAILED` variants) — `status` lives in the JSON body, not the subject. Human-authored closeouts may happen to include `closeout` in the subject (thread 4878 matched incidentally); that is not a reliable completion signal. A subject-regex poll loop on thread 4879 slept forever while turn 2 already carried `status=partial` (~18:34Z).

**Body JSON adjudication (after `complete=true`):**

`get` the qualifying turn and parse the body as JSON. Terminal states: `status ∈ {complete, partial, failed}`; inspect `effects_manifest`, `evidence_uris`, and sidecar pointers. `partial` = work landed with deviations — lead adjudicates before treating as done.

*Grounded in friction 23595 — orchestrator poll matcher bug on cursor-sdk implement shells, 2026-07-11.*

## Source

Split out of `orchestrator-workflow` § cursor-sdk Q/R calling contract — thread
consolidation (2026-09-01, agent-bus:9853 G5). Narrower trigger than the parent
skill: this fires specifically around a `team_dispatch(op=generate, seat=cursor-sdk)`
call, not on every lead/orchestrator decision. Content unedited beyond the split.
