# friction:39223 — Nested-child park lift and pre-closeout harvest reply

**Genre:** implementation plan (cursor-sdk / GIW). Plan only. No service code in this commit.  
**Work key:** `friction:39223` (`service:git_integration_worker`, conductor hop reactor)  
**Base:** `06e6b85809bc1f2dfe65ba2c2fa1d24544098cf1` (ancestor of this lane HEAD; friction:39219). Citations re-read on that tree.  
**Shape A source:** commission cites closeout `tmp/reviews/closeouts/23245073b4ce-1b893546.md` (`CAUSE_FOUND`). That file is absent on this worktree. The mechanism below was read in source and matches the commission's cause.  
**Shape B source:** commission observation on agent-bus:16671 (conductor `93776207ee20-f941cc96`, closeout turn #50 at 03:45:33Z, review `f01914a3` reply at #49 at 03:45:25Z, operator relay #52 at 03:53:16Z, successor `beb1a5d5` at #53 at 03:53:38Z). This lane did not re-read 16671.

## Question and out of scope

How should a terminal conductor with `stop: PARKED_TRANSPORT` admit one successor when `NEXT_ADMIT: harvest <id>` names either a nested child (shape A) or a CDP review whose reply already landed (shape B)?

This plan does not change code. It does not land, push, restart, or steer 16671. It does not backfill `93776207ee20-f941cc96`.

## Steelman, then the split

The current split is coherent for a CDP review that has not replied. `PARKED_TRANSPORT` is an exit-persist stop, so `hop_owed` (`conductor_hop.py:1070`) is false for every such row. The reactor (`conductor_hop.py:1685-1687`) fires `park_harvest_owed` and returns. Continue (`conductor_park_harvest.py:692-702`) waits for any `web-anthropic` turn strictly after `closeout_turn`. That keeps a parked conductor from chaining until the review arrives, and it is why #52 (after #50) admitted while #49 (before #50) did not.

That predicate does not look at the awaited id. A harvest token that `_execution_id_target_for_parent` (`conductor_hop.py:391-430`) resolves to one nested lineage head is the child finishing, not a CDP reply. A reply that names the stamped harvest id and landed one turn before closeout is the review, not a missing reply. Those two cases stay on the park-harvest wake today, so no successor is admitted for the event that was actually awaited.

**Bound fork.** Shape A admits on the hop reactor when that nested head is terminal. Shape B stays on `park_harvest_continue` but matches the reply to the stamped harvest id, including a turn before `closeout_turn`. Rejected: teach `park_harvest_continue_owed` to treat nest terminal as a reply. That keeps two admit paths for one stop. Rejected: drop `after_turn` to 0 and accept any historical `web-anthropic` turn. That admits on an unrelated earlier message.

## Shared predicate

Add `_nested_harvest_head(row) -> tuple[str, bool] | None` next to `_execution_id_target_for_parent`.

1. Token from `_harvest_target_token` (`conductor_hop.py:286-304`) on `_next_admit_guard_text` (`conductor_hop.py:247-260`). No execution-id-shaped token returns `None` (`harvest G1`, `NEXT_ADMIT: none`, absent line).
2. Return `_execution_id_target_for_parent(token, parent_dispatch_id)` unchanged.

Do not relax that function. A token that also names a dispatch id (`:407-408`) or a CDP row that is a different id or still live (`:409-414`) stays `None`, and today's park-harvest path remains. One ledger head, `park_resumed_by` empty, `nest_under` / `resume_of` walk to this parent (`:358-388`, `:417-430`). The bool is terminal status, or false when `park_kind` is set.

`CONSULT_PENDING` does not veto this head. `consult_pending_blocks_progression` (`libs/claude_bundles/conductor_stop.py:273-275`) stays in force when the head is `None`. When the head is not `None`, the awaited id is the child, so the hop exemption below skips that block. A consult wait whose `NEXT_ADMIT` is not a nested child is unchanged. Rejected: leave the consult block absolute, which preserves shape A whenever the closeout also matches `is_consult_pending_wait` (mode-B `execution_id` / `poll_hint` and no archive line).

## Shape A — behavior

When `_nested_harvest_head` is not `None`:

| Site | Change |
|---|---|
| `park_harvest_owed` (`conductor_park_harvest.py:160-204`, before the `hop_owed` call at `:197-203`) | Return false. Do not post the harvest wake. |
| `park_harvest_continue_owed` (`conductor_park_harvest.py:653-702`, before the snapshot at `:692`) | Return false. A `web-anthropic` reply must not admit this row. |
| `hop_owed` (`conductor_hop.py:1070-1072`) | If the only exit-persist token is `PARKED_TRANSPORT`, the head's bool is true, and `conductor_has_live_nested` is false, do not return false. `DONE` and any other `EXIT_PERSIST_STOPS` member still return false. Skip `consult_pending_blocks_progression` only in this same case. |
| `hop_body_build_refused` (`conductor_hop.py:563-586`) | If the head's bool is true and the hold tokens (`_lift_hold_tokens`, `_LIFT_HOLD_TOKENS` at `:909-911`) contain `PARKED_TRANSPORT` and no other member of that set, return false unless `conductor_has_live_nested` (`:571-572`). `DONE`, `CONFIRM_PENDING`, `CONSULT_PENDING`, `ROW_PINNED`, `HOLD_MERGE`, `OPERATOR_GATE` still refuse. `build_hop_team_dispatch_body` (`:1398-1402`) refuses every non-`park_harvest` reason while this stays true, so the lift has to be here, not only in `hop_owed`. |
| `_hop_skip_gate` (`conductor_hop.py:825-826`) | `PARKED_TRANSPORT` alone plus a non-`None` head must not return `mission_closed`. Fall through so a live child hits `live_nested` (`:837-838`). |

Live child: reactor emits skip `live_nested`. `_emit_hop_skipped` (`:892-901`) restamps that as `hop_deferral_gate=next_admit_blocked` when the head resolves. That stamp is ledger state. `release_deferred_conductor_hops` (`:1646-1662`) is how a later sweep sees it. `_deferral_cleared` (`:1594-1598`) stays closed while `hop_body_build_refused` is true.

Terminal child: nest close already re-enters the parent reactor. `_mark_terminal_and_promote` (`routes/cursor_sdk.py:1672-1696`) calls `maybe_fire_conductor_hop_reactor` on the parent when `find_park_parent_any_status` (`cursor_dispatch_ledger.py:1934-1948`) finds this child in `park_child_dispatch_id`. Nest admit writes that column (`cursor_dispatch_ledger.py:1678-1691`). With the gates above, that call builds a hop body and posts one successor (`hop_reason` remains `silent` unless `ROW_HOP` is present; do not add a reason string).

**Match.** The nest close is matched by execution id, not by thread position. Closing child → `park_child_dispatch_id` → parent reactor → harvest token → ledger rows with that `execution_id` (`_collect_execution_id_rows`, `:346-355`) → one head whose `nest_under` / `resume_of` chain reaches this parent. A child whose id is not the token does not lift. A token with zero or two heads does not lift (`:423-424`).

## Shape B — behavior

When the head is `None` and the harvest token is an execution id, `park_harvest_continue_owed` (`:692-702`) no longer uses `reply_arrived_on_thread(..., after_turn=closeout_turn)`.

Replace that snapshot with `_reply_matches_harvest_execution(row, token) -> bool`:

1. Threads: worker `thread_id`, plus `summoning_thread_id` when it differs. Turns from `_fetch_thread_turns` (`conductor_park_harvest.py:341-362`). Fetch failure returns false (same fail-closed as `reply_arrived_on_thread`, `:469-476`).
2. A qualifying turn has `from_agent=web-anthropic` and a body that contains the harvest token as a whole id (the token bounded so a different id is not a prefix/suffix match).
3. Watermark: `_harvest_execution_started_at_iso` (`conductor_hop.py:647-674`) for that token, then `resolve_consult_summoning_watermark_at_instant` (`conductor_park_harvest.py:365-392`). The turn number must be greater than that watermark. The summon turn at the watermark does not count (same exclusion `_expected_harvest_reply_present` documents at `:677-687`).
4. If the registry does not yield one `started_at`, a turn that contains the token still qualifies, including `turn_number <= closeout_turn`. Do not require `after_turn=closeout_turn`. Missing both a token and a readable turn list stays false.
5. A turn that names some other execution id and does not contain this token does not qualify. `closeout_turn` remains required as an int (`:694-695`) so a row that never recorded closeout still does not continue.

`maybe_fire_conductor_hop_reactor` (`conductor_hop.py:1685`) evaluates `park_harvest_continue_owed` and, when true, `fire_park_harvest_continue` (`conductor_park_harvest.py:726`) before `park_harvest_owed`, then returns. That is the watchdog order already at `conductor_hop_watchdog.py:195-198`. Without the reactor reorder, closeout still posts the wake and only a later sweep admits. Shape A is unaffected: continue is false, then `park_harvest_owed` is false, then the hop gate runs.

No harvest token: continue returns false. Do not admit on a bare `web-anthropic` reply. `park_harvest_owed` is unchanged for a CDP id the head does not resolve, and for a non-id harvest line that still grades owed. The wake may still post. The successor may not, until a reply body contains an execution-id token — and with no token there is nothing to match, so continue stays false.

`test_park_harvest_continue_owed_unchanged_for_live_harvest_id` (`test_conductor_park_harvest.py:569-598`) asserts `NEXT_ADMIT: harvest G1` still continues. Flip the G1 assertion. The live-uuid half of that test must pass the new matcher (body contains that uuid), not a `reply_fn` that ignores the id. `reply_fn` stays the test seam: when provided, it is called only after the token check, with the token, and a true result still means "this id's reply is present". A `reply_fn` that cannot see the id must not be treated as a match for a different id. Tests that pass `lambda: True` for a row whose closeout has a real harvest uuid (`:546-566`) must supply a body that contains that uuid or a `reply_fn` that records the token. Do not preserve "any reply after closeout".

**Match.** Reply → awaited id is the harvest token in the turn body, on the worker or summoning thread, after that execution's `started_at` watermark when the registry has one row. It is not "any `web-anthropic` turn after `closeout_turn`" (`reply_arrived_on_thread`, `:435-468`). Nest close is not a shape B trigger: a CDP id that does not resolve to a nested head never enters shape A.

## Where each change breaks, and the test that fails before it

All four tests are new, hermetic, `@pytest.mark.offline`, ledger fixture in the style of `test_conductor_successor_g3.py` (nest) and `test_conductor_park_harvest.py` (reply). They assert the new behavior. On `06e6b858` each fails.

1. **GIW restart while the child is still live (shape A).** Parent terminal, stored tokens `{PARKED_TRANSPORT}`, `NEXT_ADMIT: harvest <child execution_id>`, child `running`, `nest_under` and `park_child_dispatch_id` set. `maybe_fire_conductor_hop_reactor` posts no team_dispatch, leaves `hop_park_harvest_fired_at` unset, and stamps `hop_deferral_gate=next_admit_blocked`. A second call, as a fresh process would, via `release_deferred_conductor_hops` while the child is running, admits 0. `mark_terminal` on the child, then `release_deferred_conductor_hops`, admits 1. Fails before: `park_harvest_owed` consumes the reactor (`:1685-1687`), no deferral stamp, and `hop_owed` stays false after the child is terminal (`:1070`), so the sweep admits 0. The stamp is the restart case: the nest-close re-fire (`routes/cursor_sdk.py:1696`) is gone if the process died between hold and child terminal; the watchdog sweep (`conductor_hop_watchdog.py:388`) is what remains.

2. **Nest close and a reply in the same turn (shape A).** Child already terminal, and a `web-anthropic` turn whose body contains the child execution id is visible to the reply reader. Call the parent reactor (nest-close path) and `maybe_fire_conductor_hop_watchdog` (continue path) in one test. `post_conductor_hop_team_dispatch` await count is 1. Fails before: await count is 0 (wake is a different poster; continue demands a post-closeout reply and would still be the wrong path for a nested head).

3. **Reply for a different execution (shape B).** `PARKED_TRANSPORT`, `NEXT_ADMIT: harvest <id A>`, one `web-anthropic` turn after `closeout_turn` whose body contains id B and not id A. `park_harvest_continue_owed` is false and the reactor does not post. Companion in the same test: a turn before `closeout_turn` whose body contains id A makes continue true and one `fire_park_harvest_continue` posts (`hop_reason=park_harvest`). The companion fails before because `:701` uses `after_turn=closeout_turn`. The different-id assertion fails before because any post-closeout `web-anthropic` turn makes `:701` true (`test_park_harvest_continue_owed_true_when_reply_arrived`).

4. **Park that never stamped a harvest id.** `PARKED_TRANSPORT`, no `NEXT_ADMIT` harvest id (`NEXT_ADMIT: none` or the line absent), `web-anthropic` reply after `closeout_turn`, and a nested child of a different execution id going terminal. Continue is false, reactor posts no successor, child terminal does not admit. Fails before: continue is true on the reply (`test_park_harvest_continue_owed_unchanged_for_live_harvest_id` G1 / `reply_fn` true) even though no execution id was stamped. `park_harvest_owed` may still be true when `harvest_still_owed` is true; the assertion is successor count 0, not wake count 0.

**Shape B restart.** Same row as the shape B companion, no `hop_successor`, no `hop_park_harvest_continued_at`, process memory empty. `maybe_fire_conductor_hop_watchdog` admits 1 because the reply is on the bus. Fails before: continue is false, watchdog falls through to `park_harvest_owed` and posts a wake instead of a successor. Fold this into test 3 as a second call after a re-load of the row, not a fifth scenario.

## One successor when two triggers fire

Two admits share one slot:

- `claim_stop_service` (`cursor_dispatch_ledger.py:2350-2370`) sets `serviced_admit` only while it is null. The second `post_conductor_hop_team_dispatch` (`conductor_hop.py:1492-1493`) gets `stop_not_claimed` and does not mint a row.
- The winner stamps `hop_successor`. `_successor_admitted` (`cursor_sdk_park.py:228-238`) makes `park_harvest_continue_owed` (`:688-691`) and `hop_owed` (`:1094-1095`) false on the next pass.
- Shape A is excluded from continue, so a reply that quotes the child id is not a second trigger.
- Shape B does not take the hop lift, so nest close of some other child is not a second trigger.
- Reactor-then-watchdog on the same true continue is the race test 2 covers for shape A and test 3's double call covers for shape B: second post is refused, `hop_park_harvest_continued_at` / `hop_successor` set once.

`release_stop_service` (`:2372-2394`) runs only when the POST failed. A won claim is not released by the loser.

## Out of scope

- Code, land, push, `manage`, GIW restart, steer or backfill on agent-bus:16671.
- Relaxing CDP / dispatch-id ambiguity inside `_execution_id_target_for_parent`.
- `consult_pending_continue_owed` watermark (`conductor_park_harvest.py:512-535`) except the nested-head skip named above. A consult that is not a nested child still waits for a post-watermark reply.
- `no_progress` producer continue, external-gate probe, hop budget caps, park-harvest arm-recipe text (`build_park_harvest_arm_recipe`, `:207-250`).
- Successor admit for `ROW_PINNED`, `HOLD_MERGE`, `OPERATOR_GATE`, `DONE`, `CONFIRM_PENDING`.
- Matching a reply by a bus field other than `from_agent` plus body text. Turns are not joined on an `execution_id` column in this path.
- `friction:39219` steer-inject inheritance (landed at the base).
- Architecture docs and skill install.

## Implement order

1. `_nested_harvest_head` and the five shape A gates. Test 1 and test 4 (no successor half) go red, then green.
2. `_reply_matches_harvest_execution` and the reactor reorder. Update the G1 continue assertion. Test 3 goes red, then green.
3. Test 2 last, after both gates exist, so the single-post assertion is meaningful.

Verify with `$HOME/.venvs/universal/bin/pytest services/git_integration_worker/tests/test_conductor_park_harvest.py services/git_integration_worker/tests/test_conductor_successor_g3.py -q --tb=short`. Quote the `passed` line. No restart in the implement dispatch: this is ledger and reactor code, not a live process claim.
