# friction:39223 — Nested-child park lift and pre-closeout harvest reply

**Genre:** implementation plan (cursor-sdk / GIW). Plan only. No service code in this commit.  
**Work key:** `friction:39223` (`service:git_integration_worker`, conductor hop reactor)  
**Base:** `06e6b85809bc1f2dfe65ba2c2fa1d24544098cf1` (ancestor of this lane HEAD; friction:39219). Citations re-read on that tree.  
**Shape A source:** commission cites closeout `tmp/reviews/closeouts/23245073b4ce-1b893546.md` (`CAUSE_FOUND`). That file is absent on this worktree. The mechanism below was read in source and matches the commission's cause.  
**Revision:** review `2e49349a-94ec-45d5-be35-6c147d763a45` (bus 16692#10), VERDICT CHANGES_REQUESTED. Ledger and 16671 turns re-read this revision.  
**Shape B source:** commission named `93776207ee20-f941cc96`. Ledger closeout has no `NEXT_ADMIT` line (id `f01914a3-…` is prose only). That row is test 4, not the id-matched matcher. The stamped harvest line used for the matcher is `54e6ff1a228a-efa531a4`: `NEXT_ADMIT: harvest 994dc2b4-d1dd-4206-bd97-2ad0109478bc`.

## Question and out of scope

How should a terminal conductor with `stop: PARKED_TRANSPORT` admit one successor when `NEXT_ADMIT: harvest <id>` names either a nested child (shape A) or a CDP review whose reply already landed (shape B)?

This plan does not change code. It does not land, push, restart, or steer 16671. It does not backfill `93776207ee20-f941cc96`.

## Steelman, then the split

The current split is coherent for a CDP review that has not replied. `PARKED_TRANSPORT` is an exit-persist stop, so `hop_owed` (`conductor_hop.py:1070`) is false for every such row. The reactor (`conductor_hop.py:1685-1687`) fires `park_harvest_owed` and returns. Continue (`conductor_park_harvest.py:692-702`) waits for any `web-anthropic` turn strictly after `closeout_turn`. That keeps a parked conductor from chaining until the review arrives, and it is why #52 (after #50) admitted while #49 (before #50) did not.

That predicate does not look at the awaited id. A harvest token that `_execution_id_target_for_parent` (`conductor_hop.py:391-430`) resolves to one nested lineage head is the child finishing, not a CDP reply. A stamped harvest id whose CDP reply header landed before `closeout_turn` is the review, not a missing reply. `93776207ee20-f941cc96` has no `NEXT_ADMIT` line, so it is the unstamped case (test 4), not proof of the id matcher. The 04:06–04:12 stall on `54e6ff1a228a-efa531a4` is out of scope (section below): the reply predicate was already true.

**Bound fork.** Shape A admits on the hop reactor when that nested head is terminal. Shape B stays on `park_harvest_continue` but matches the reply to the stamped harvest id, including a turn before `closeout_turn`. Rejected: teach `park_harvest_continue_owed` to treat nest terminal as a reply. That keeps two admit paths for one stop. Rejected: drop `after_turn` to 0 and accept any historical `web-anthropic` turn. That admits on an unrelated earlier message.

## Shared predicate

Add `_nested_harvest_head(row) -> tuple[str, bool] | None` next to `_execution_id_target_for_parent`.

1. Token from `_harvest_target_token` (`conductor_hop.py:286-304`) on `_next_admit_guard_text` (`conductor_hop.py:247-260`). No execution-id-shaped token returns `None` (`harvest G1`, `NEXT_ADMIT: none`, absent line).
2. Return `_execution_id_target_for_parent(token, parent_dispatch_id)` unchanged.

Do not relax that function. A token that also names a dispatch id (`:407-408`) or a CDP row that is a different id or still live (`:409-414`) stays `None`, and today's park-harvest path remains. One ledger head, `park_resumed_by` empty, `nest_under` / `resume_of` walk to this parent (`:358-388`, `:417-430`). The bool is terminal status, or false when `park_kind` is set.

`consult_pending_blocks_progression` (`libs/claude_bundles/conductor_stop.py:273-275`) stays in force when the head is `None`. When the head is not `None` and terminal, both `hop_owed` and `hop_body_build_refused` skip that block (must-fix 2, option b). `is_consult_pending_wait` is true only when the literal token is present (`conductor_stop.py:255-258`), so a true consult block always puts `CONSULT_PENDING` into `_lift_hold_tokens`. Exempting only `hop_owed` would still refuse the body (`:563-586`), stamp `next_admit_blocked`, and leave `_deferral_cleared` (`:1594-1598`) false. Rejected: option (a), drop the exemption and call shape A + `CONSULT_PENDING` out of scope. The fixture `_production_parked_row` stores both tokens (`test_conductor_park_harvest.py:528`). The two live 16671 rows re-read here store only `["PARKED_TRANSPORT"]` (`54e6ff1a228a-efa531a4`, `93776207ee20-f941cc96`); the lift still has to agree on both tokens so a both-token row is not a dead stamp.

## Shape A — behavior

When `_nested_harvest_head` is not `None`:

| Site | Change |
|---|---|
| `park_harvest_owed` (`conductor_park_harvest.py:160-204`, before the `hop_owed` call at `:197-203`) | Return false. Do not post the harvest wake. |
| `park_harvest_continue_owed` (`conductor_park_harvest.py:653-702`, before the snapshot at `:692`) | Return false. A `web-anthropic` reply must not admit this row. |
| `consult_pending_continue_owed` (`conductor_park_harvest.py:514-535`) | Return false when the head is not `None`. That path admits on any post-closeout `web-anthropic` reply and would be an id-unmatched second trigger. |
| `hop_owed` (`conductor_hop.py:1070-1072`) | If the only exit-persist token is `PARKED_TRANSPORT`, the head's bool is true, and `conductor_has_live_nested` is false, do not return false. `DONE` and any other `EXIT_PERSIST_STOPS` member still return false. Skip `consult_pending_blocks_progression` in this same case. |
| `hop_body_build_refused` (`conductor_hop.py:563-586`) | If the head's bool is true, return false unless `conductor_has_live_nested` (`:571-572`), even when `_lift_hold_tokens` contains `PARKED_TRANSPORT` and `CONSULT_PENDING`. `DONE`, `CONFIRM_PENDING`, `ROW_PINNED`, `HOLD_MERGE`, `OPERATOR_GATE` still refuse. `build_hop_team_dispatch_body` (`:1398-1402`) refuses every non-`park_harvest` reason while this stays true, so the lift has to be here, not only in `hop_owed`. |
| `_hop_skip_gate` (`conductor_hop.py:825-826`) | A non-`None` head must not return `mission_closed` for `PARKED_TRANSPORT` (with or without `CONSULT_PENDING`). A live child falls through to `live_nested` (`:837-838`). An open-park head (bool false because `park_kind` is set, `:426-427`) stamps `next_admit_blocked` on that same path. `mission_closed` is not a transient gate (`_deferral_stamp_allowed`, `:850-867`), so without the stamp a restart never sweeps the row, and the `-rN` child is not necessarily `park_child_dispatch_id`. |

Live child: reactor emits skip `live_nested`. `_emit_hop_skipped` (`:892-901`) restamps that as `hop_deferral_gate=next_admit_blocked` when the head resolves. That stamp is ledger state. `release_deferred_conductor_hops` (`:1646-1662`) is how a later sweep sees it. `_deferral_cleared` (`:1594-1598`) stays closed while `hop_body_build_refused` is true.

Terminal child: nest close already re-enters the parent reactor. `_mark_terminal_and_promote` (`routes/cursor_sdk.py:1672-1696`) calls `maybe_fire_conductor_hop_reactor` on the parent when `find_park_parent_any_status` (`cursor_dispatch_ledger.py:1934-1948`) finds this child in `park_child_dispatch_id`. Nest admit writes that column (`cursor_dispatch_ledger.py:1678-1691`). With the gates above, that call builds a hop body and posts one successor (`hop_reason` remains `silent` unless `ROW_HOP` is present; do not add a reason string).

**Match.** The nest close is matched by execution id, not by thread position. Closing child → `park_child_dispatch_id` → parent reactor → harvest token → ledger rows with that `execution_id` (`_collect_execution_id_rows`, `:346-355`) → one head whose `nest_under` / `resume_of` chain reaches this parent. A child whose id is not the token does not lift. A token with zero or two heads does not lift (`:423-424`).

## Specimen 2 is out of scope (must-fix 1)

`54e6ff1a228a-efa531a4` on 16671 is not a case this plan fixes. Ledger: `closeout_turn=62`, tokens `["PARKED_TRANSPORT"]`, `hop_next_admit=harvest 994dc2b4-d1dd-4206-bd97-2ad0109478bc`, `hop_seq=8` (highest conductor seq on the thread until the successor), `hop_parked` unset, `hop_admit_error` unset, no nested child. Turn #64 (04:06:29Z) is `from=web-anthropic`, subject `cdp reply — 994dc2b4`, header `execution_id: `994dc2b4-d1dd-4206-bd97-2ad0109478bc``. `qualifying_reply` (`libs/agent_bus_store/wait_status.py:183-217`) with `pinned=False` (`qualifying_reply_for_wait` `:420-422`; GIW passes no `execution_id` in `reply_arrived_on_thread`, `conductor_park_harvest.py:446-451`) is true for that turn after `after_turn=62`. `_latest_terminal_conductor_rows` (`cursor_sdk_park.py:241-276`) would select this row. Continue still did not admit until `hop_park_harvest_continued_at` 04:12:46Z, after relay #65 (04:12:07Z). Gates that return false without `hop.skipped` (`live_conductor_row_on_thread` `conductor_hop.py:972-987`, `conductor_has_live_nested`, `mission_open_for_row`, `evaluate_hop_budget`, a swallowed wait exception at `conductor_park_harvest.py:469-476`, `_mission_park_blocks_hop` at `conductor_hop_watchdog.py:191-192`) left no stamp, and the row later admitted, so the false one is not recoverable from the ledger. The reply predicate and the reactor reorder do not explain a stall that started after the reply. Do not claim this specimen.

## Third specimen — post-closeout reply admitted with no relay

`47f0d7585aa7-df5e02ed`: `closeout_turn=77`, tokens `["PARKED_TRANSPORT"]`, wake `hop_park_harvest_fired_at` 04:25:51Z (bus #78). The harvest id is mid-line in backticks (`NEXT_ADMIT: harvest 8d4a26eb-1e03-4600-ab57-a6a80a81964d`), so `hop_next_admit` is empty: `_NEXT_ADMIT_VALUE_RE` (`conductor_stop.py:291-293`) requires a line start. CDP reply #79 (04:30:46Z) is `web-anthropic`, subject `cdp reply — 8d4a26eb`, header `execution_id: `8d4a26eb-1e03-4600-ab57-a6a80a81964d``. The next turn is #80 (04:32:03Z), `from=conductor-hop`, subject `cursor-sdk generate — 15c116fe`. Ledger `hop_park_harvest_continued_at` is 04:32:04Z, successor `ef44ce11fc35-01978c4c`. No relay sits between #79 and #80. This is the current post-closeout any-reply path working. It is not a pre-closeout bug. Do not widen the NEXT_ADMIT parser for the mid-line form in this plan.

## Shape B — behavior

When the head is `None` and `_harvest_target_token` returns an id, `park_harvest_continue_owed` (`:692-702`) no longer uses `reply_arrived_on_thread(..., after_turn=closeout_turn)`.

Replace that snapshot with `_reply_matches_harvest_execution(row, token) -> bool`. Run it, and the reactor's continue check, inside `asyncio.to_thread`. `reply_arrived_on_thread` is a sync bus GET with a 15s timeout (`:455`); calling it on the closeout task blocks the loop. The watchdog already wraps `hop_owed` that way (`conductor_hop_watchdog.py:210`).

Match rule (must-fix 3), same boundary idea as `_prefix_match_id` (`conductor_hop.py:312-317`):

1. Token is whatever `_harvest_target_token` already accepts, including an 8–32 hex short id (`_HARVEST_ID_IN_ADMIT_RE`, `:265-274`).
2. A qualifying turn is `from_agent=web-anthropic` and either its subject is `cdp reply — <id>` or its body has a line `execution_id: `<id>``. The candidate id equals the token, or `candidate.startswith(token)` and the next character is absent or not a hex digit (so `994dc2b4` matches `994dc2b4-d1dd-4206-bd97-2ad0109478bc` and does not match a longer hex run with no boundary).
3. Prose that merely mentions the id does not qualify. Relay #65 names both `994dc2b4-…` and `46662aec` and is not a CDP reply header. Body-mention as a fallback is rejected for that reason.
4. Turns come from `_fetch_thread_turns` (`:341-362`) on the worker thread and on `summoning_thread_id` when it differs. Fetch failure returns false (`:469-476`).
5. `closeout_turn` is still required as an int (`:694-695`). The turn may be `<= closeout_turn`. Do not require `after_turn=closeout_turn`.
6. Watermark when the registry has one `started_at` for that token (`_harvest_execution_started_at_iso`, `:647-674`, then `resolve_consult_summoning_watermark_at_instant`, `:365-392`): the turn number must be greater than that watermark. The summon turn at the watermark does not count. If `started_at` is missing, a header match still qualifies.
7. Soft-spill does not keep a lead-in. `prepare_body_for_insert` (`body_auto_spill.py:171-260`) replaces an over-limit body with `AUTO_OVERFLOW_BRIEFING` (`:97-100`) plus a sidecar pointer. The `execution_id:` line is gone. Fail closed on the body header in that case. The subject is not spilled; `cdp reply — <id8>` still matches. Do not read the sidecar in this plan.

No harvest token: continue returns false. `93776207ee20-f941cc96` is this case (no `NEXT_ADMIT` line; `f01914a3-…` only inside prose). A pre-closeout reply must not admit it, and a post-closeout reply must not admit it either once the any-reply predicate is gone. `park_harvest_owed` may still post the wake when `harvest_still_owed` is true. Successor count stays 0.

`test_park_harvest_continue_owed_unchanged_for_live_harvest_id` (`test_conductor_park_harvest.py:569-598`) asserts `NEXT_ADMIT: harvest G1` still continues. Flip the G1 assertion. Keep the `inspect.getsource` checks at `:570-573` true on the edited function (`conductor_park_harvest_owed`, `status_incomplete_class`, and `work_outcome` stay absent). The live-uuid half must use a CDP-reply subject or `execution_id:` header, not a `reply_fn` that ignores the id. `reply_fn`, when a test passes one, is consulted only after the token check and only as "this token's header matched". Tests at `:546-566` that pass `lambda: True` must supply that header. Do not preserve "any reply after closeout".

`maybe_fire_conductor_hop_reactor` (`conductor_hop.py:1685`) evaluates continue, via `asyncio.to_thread`, and on true calls `fire_park_harvest_continue` (`:726`) before `park_harvest_owed`, then returns. Watchdog order is already `conductor_hop_watchdog.py:195-198`. Shape A is unaffected: continue is false, then `park_harvest_owed` is false, then the hop gate runs.

**Match.** Reply → awaited id is the harvest token against the CDP reply subject or the `execution_id:` header, prefix-at-boundary, including a turn before `closeout_turn`. It is not "any `web-anthropic` turn after `closeout_turn`" (`reply_arrived_on_thread`, `:435-468`). Nest close is not a shape B trigger.

## Where each change breaks, and the test that fails before it

Tests are new, hermetic, `@pytest.mark.offline`, in the style of `test_conductor_successor_g3.py` and `test_conductor_park_harvest.py`. On `06e6b858` each fails. Stamp strings in tests 3 and 4 are the ledger lines above, not invented short ids.

1. **GIW restart while the child is still live (shape A).** Parent terminal, stored tokens `{PARKED_TRANSPORT}`, `NEXT_ADMIT: harvest <child execution_id>`, child `running`, `nest_under` and `park_child_dispatch_id` set. `maybe_fire_conductor_hop_reactor` posts no team_dispatch, leaves `hop_park_harvest_fired_at` unset, and stamps `hop_deferral_gate=next_admit_blocked`. `release_deferred_conductor_hops` while the child is running admits 0. `mark_terminal` on the child, then the sweep, admits 1. Fails before: `park_harvest_owed` consumes the reactor (`:1685-1687`), no deferral stamp, and `hop_owed` stays false after the child is terminal (`:1070`). Open-park sibling of this test: head bool false (`park_kind` set), same stamp, sweep admits 0 until that head is terminal, then 1. Fails before: `_hop_skip_gate` returns `mission_closed` (`:825-826`), which is not stamped (`:850-867`).

2. **Nest close and a reply in the same turn (shape A).** Child already terminal. The `web-anthropic` turn that names the child is at `turn_number <= closeout_turn` (must-fix 4). Call the parent reactor and `maybe_fire_conductor_hop_watchdog`. Exactly one `post_conductor_hop_team_dispatch`, `hop_reason != "park_harvest"`, `hop_park_harvest_continued_at` unset. Fails before: await count is 0, because a pre-closeout reply does not make continue true (`:701`) and `park_harvest_owed` does not post a hop. A post-closeout reply would make continue true on base and the count would be 1 with `hop_reason=park_harvest`, which is why the turn pin and the reason assertion are both required.

3. **Reply for a different execution (shape B).** Closeout line `NEXT_ADMIT: harvest 994dc2b4-d1dd-4206-bd97-2ad0109478bc`. First assertion, before any matching turn is inserted: one `web-anthropic` turn after `closeout_turn` whose header is `execution_id: `f01914a3-015c-4449-be62-1ac2911bf02b`` (and whose prose may also mention `994dc2b4`). Continue is false. Fails before: any post-closeout `web-anthropic` turn makes `:701` true. Then insert a pre-closeout turn, subject `cdp reply — 994dc2b4`, header the full uuid. Continue is true and one `fire_park_harvest_continue` posts `hop_reason=park_harvest`. Fails before because `:701` requires `after_turn=closeout_turn`. Also assert short token `994dc2b4` matches that uuid and does not match `994dc2b4abcd` with no boundary. Reload the row with no `hop_successor` and run the watchdog: one admit. Fails before: continue is false, so the watchdog posts a wake.

4. **Park that never stamped a harvest id.** Use the `93776207` shape: `stop: PARKED_TRANSPORT`, no `NEXT_ADMIT` line, prose contains `f01914a3-015c-4449-be62-1ac2911bf02b`, and a `web-anthropic` CDP reply for that id both before and after `closeout_turn`. Continue is false, reactor posts no successor. Fails before: continue is true on the post-closeout reply. `harvest G1` stays in the flipped assertion of the existing test, same gate.

**Both-token shape A.** Stored tokens `{PARKED_TRANSPORT, CONSULT_PENDING}`, head terminal, no live nest. Reactor posts one hop, `hop_body_build_refused` is false. Fails before: the body refuse stays true (`:570` and `_LIFT_HOLD_TOKENS` `:909-911`), so the POST never happens. Fold into test 1, not a fifth scenario.

**Spill.** Subject `cdp reply — 994dc2b4` plus an auto-overflow body with no `execution_id:` line still matches. Same body with subject `CONSULT_REPLY relay` does not. The second fails before if any `web-anthropic` body that mentions the uuid counts.

**Loop block.** `inspect.getsource(maybe_fire_conductor_hop_reactor)` contains `to_thread` around the continue predicate. Fails before: the reactor does not evaluate continue (`:1685-1687`).

## One successor when two triggers fire

Two admits share one slot:

- `claim_stop_service` (`cursor_dispatch_ledger.py:2350-2370`) sets `serviced_admit` only while it is null. The second `post_conductor_hop_team_dispatch` (`conductor_hop.py:1492-1493`) gets `stop_not_claimed` and does not mint a row.
- The winner stamps `hop_successor`. `_successor_admitted` (`cursor_sdk_park.py:228-238`) makes `park_harvest_continue_owed` (`:688-691`) and `hop_owed` (`:1094-1095`) false on the next pass.
- Shape A is excluded from `park_harvest_continue_owed` and from `consult_pending_continue_owed`, so a reply that quotes the child id is not a second trigger.
- Shape B does not take the hop lift, so nest close of some other child is not a second trigger.
- Reactor-then-watchdog on the same true continue is the race test 2 covers for shape A and test 3's double call covers for shape B: second post is refused, `hop_park_harvest_continued_at` / `hop_successor` set once.

`release_stop_service` (`:2372-2394`) runs only when the POST failed. A won claim is not released by the loser.

## Out of scope

- The 04:06:29Z–04:12:46Z stall on `54e6ff1a228a-efa531a4` (specimen 2). Reply-after-closeout was already true.
- Code, land, push, `manage`, GIW restart, steer or backfill on agent-bus:16671.
- Relaxing CDP / dispatch-id ambiguity inside `_execution_id_target_for_parent`.
- Widening `_NEXT_ADMIT_VALUE_RE` for a mid-line `NEXT_ADMIT` (the `47f0d758` closeout). Reading a spill sidecar.
- `consult_pending_continue_owed` watermark (`conductor_park_harvest.py:512-535`) except the nested-head return named above. A consult that is not a nested child still waits for a post-watermark reply.
- `no_progress` producer continue, external-gate probe, hop budget caps.
- Park-harvest arm recipe (`build_park_harvest_arm_recipe`, `:228-233`) passing the conductor's own `execution_id` (`46662aec-…` on wake #63, `48b00444-…` on wake #78) instead of the awaited CDP id. Separate friction; not this diff.
- Successor admit for `ROW_PINNED`, `HOLD_MERGE`, `OPERATOR_GATE`, `DONE`, `CONFIRM_PENDING`.
- `friction:39219` steer-inject inheritance (landed at the base).
- Architecture docs and skill install.

## Review proposals (16692#10)

1. **Adopt.** Spill stores `AUTO_OVERFLOW_BRIEFING`, not a lead-in (`body_auto_spill.py:97-100`, `:254-260`). The `execution_id:` line is absent. Fail closed on that header. Subject `cdp reply — <id8>` still matches. Covered by the spill test.
2. **Adopt.** Continue's sync bus and registry reads on the reactor go through `asyncio.to_thread`. A 15s wait timeout (`conductor_park_harvest.py:455`) would otherwise hold the closeout task. Covered by the `to_thread` source assertion.
3. **Decline.** The arm recipe watches the conductor execution id, not the awaited CDP id (`:228-233`). That is a different defect. Named above as out of scope, not edited here.
4. **Adopt.** An open-park head (bool false) stamps `next_admit_blocked` instead of falling through to unstamped `mission_closed`. Covered by the open-park half of test 1. The `-rN` child need not be `park_child_dispatch_id`; the sweep is the lift.
5. **Adopt.** Test 3 asserts the different-id turn before inserting the matching pre-closeout turn. Both turns on one thread would make continue true and hide the negative.
6. **Adopt.** The edited `park_harvest_continue_owed` still satisfies `inspect.getsource` assertions at `test_conductor_park_harvest.py:570-573` (those three names stay out of the function).
7. **Adopt the header, decline the fallback.** Match `cdp reply — <id8>` and a line-start `execution_id:` header, prefix-at-boundary. A body mention is not a match: relay #65 names `994dc2b4-…` and would admit on the relay the stall was waiting to escape.

## Implement order

1. `_nested_harvest_head` and the shape A gates, including the `CONSULT_PENDING` lift and the open-park stamp. Test 1 (restart and open-park) and the both-token fold go red, then green.
2. `_reply_matches_harvest_execution`, reactor reorder, `to_thread`. Flip the G1 assertion without breaking `:570-573`. Test 3 and test 4 go red, then green.
3. Test 2 last, with the pre-closeout pin and `hop_reason != park_harvest`.

Verify with `$HOME/.venvs/universal/bin/pytest services/git_integration_worker/tests/test_conductor_park_harvest.py services/git_integration_worker/tests/test_conductor_successor_g3.py -q --tb=short`. Quote the `passed` line. No restart in the implement dispatch: this is ledger and reactor code, not a live process claim.
