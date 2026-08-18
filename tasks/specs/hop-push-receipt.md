# Spec — hop cutover is a push receipt, not a poll

**Slug:** `hop-push-receipt`
**Kind:** implement (G1 content-contract bind + G2/G4 mechanical wiring + G3 doctrine sweep + G5 honesty fix)
**Root:** agent-bus:9444 (continuity root, `role:root`, profile `orchestrator_continuity`)
**Charter:** `cortex://notes/system/charters/hop-push-receipt.md`
**Scoreboard:** `cortex://notes/system/threads/9444-charter-scoreboard.md`
**Bind:** a:29822 (OPERATOR BIND 2026-08-17T19:32:55Z, CLOSED — do not re-litigate)

## Problem

`hop_cadence_succession_release.py::release_superseded_on_confirm` aborts the
predecessor execution after an idle streak the moment successor
`SEAT_REGISTRATION` is confirmed — without ever telling the predecessor seat.
The only way the predecessor (or an initiating supervisor watching it) learns
cutover happened is by polling `successor_seated` / generate harvest, often
for hours (9440 specimen). Separately, `handler.py`'s poll-timeout path
declares `status:failed` even when the ledger's own `last_heartbeat_at` is
fresh (9440 turn 75: heartbeat 31s old, `terminal:false`, real CLOSEOUT arrived
9 minutes later).

## G1 — Seating-confirm signal + paste content contract (this file's primary bind)

**Question:** who confirms seating, and what literal ``prompt_text`` gets
pasted into the predecessor's own CSE?

**Bind 1 — confirm authority (no fork):** `reconcile_succession_confirmations`
(`services/git_integration_worker/cursor_auto/hop_cadence_stall_reconcile.py:547-688`)
is the sole site that matches live `cdp_ask.active_work` membership against a
watch row, advances `registration_id`, and posts `TYPE: SEAT_REGISTRATION`
(`hop_cadence_seat_stamp.post_seat_registration_if_keyed`, called at
`:659-671`). The paste attaches to this exact call site — not a new poll, not
a second registry read.

**Bind 2 — content contract:** reuse the bare `TYPE: SEAT_STAND_DOWN` token,
**not** a new `HOP_SUCCEEDED` type. Implemented as
`hop_handoff.body.build_seat_stand_down_body` (mirrors
`build_seat_registration_stamp`'s field shape: `superseded_registration_id`,
`registration_id` (new/successor), `execution_id`, `parent_thread`,
`observed_at`, plus prose telling the predecessor it does not need to poll and
naming the expected ack).

Why bare `SEAT_STAND_DOWN` over a new token:
1. Already authorized end-to-end from the hop-watch `superseded_registration_id`
   field with `envelope="stand_down"` — `cdp_ask.cse_session_paste._watch_authorizes_predecessor_stand_down`
   (`libs/cdp_ask/cse_session_paste.py:60-91`) — zero new authorization code.
2. Already has a live consumer for the round trip: the predecessor ACKs with
   `TYPE: SEAT_STAND_DOWN_ACK`, consumed by
   `hop_cadence_standdown.lane_standdown_ack_open` (already-landed
   `todo:hop-cadence-standdown-inhibit-gate`) to inhibit a premature re-hop.
   A new `HOP_SUCCEEDED` token would need its own ack grammar and consumer
   for no behavioral gain.
3. Already the literal `prompt_text` fixture in
   `libs/cdp_ask/test_cse_session_paste.py` (`test_stand_down_authorized_from_hop_watch_without_request_triple`
   and the self-supersession test) — this bind matches what the primitive was
   already tested against.

Rejected: mint `TYPE: HOP_SUCCEEDED`. Fragments "predecessor told to stand
down" into two grammars for the same event with no new capability.

**Pinned (no deviation found):** `envelope="stand_down"` (hop-watch-authorized,
no request-triple needed) and `min_receipt="dom_paste"` (existing default).

**Gotcha caught by the builder's own test (`test_seat_stand_down_body_still_classifies_as_bare_non_ack`):**
the instructional prose must not spell the literal substring `TYPE: SEAT_STAND_DOWN_ACK`
adjacently — `cdp_ask.cse_session_ack.marker_type`'s regex
(`TYPE:\s*SEAT_STAND_DOWN_ACK\b`, case-insensitive, unanchored) would then
self-classify the *push* body as an *ack* if that text is ever scanned through
the same classifier. The body asks for "a SEAT_STAND_DOWN_ACK turn" — never
"TYPE:" immediately followed by "SEAT_STAND_DOWN_ACK".

**Files:** `libs/hop_handoff/body.py` (`build_seat_stand_down_body`),
`libs/hop_handoff/__init__.py` (export), `libs/hop_handoff/test_hop_handoff.py`
(regression + ack-collision guard).

## G2 — Auto pastes receipt into predecessor CSE at seating-confirm

New module `services/git_integration_worker/cursor_auto/hop_cadence_predecessor_push.py`
(`push_predecessor_receipt`) — keeps `hop_cadence_stall_reconcile.py` (already
716 lines, over the 400-line existing-file guidance) from growing further, per
`[quality]`. Called from **two** sites so retries stay push-aware without
re-pasting on every tick:

1. Main confirm loop, immediately before `_call_release(release, handle)`
   (`hop_cadence_stall_reconcile.py:633`) — using `new_reg` / `matched_key`
   already resolved at that point (`:611-623`).
2. `reconcile_release_obligations` retry loop, before its own `_call_release`
   (`:538-539`) — `new_registration_id` from `row["registration_id"]` (already
   advanced at confirm time), `matched_execution_id` from
   `row["succession_confirm_record"]["superseding_execution_id"]`.

**Idempotency bind:** pass an explicit `idempotency_key = f"hop-cadence-stand-down:{thread_id}:{handle.registration_id}"`
to the satellite call — NOT the digest-of-`prompt_text` fallback in
`cse_session_paste._idempotency_key`, because `prompt_text` embeds
`observed_at` which is not stable across ticks. An explicit key makes a
retried tick a cache replay (no second DOM mutation) regardless of body
content drift.

**Watch-file staleness bind:** the satellite's authorization check reads
`load_watches()` fresh from disk. In the common path `superseded_registration_id`
was already persisted at hop-**fire** time (`hop_cadence_watch.mark_hop_fired`
saves immediately), so it predates confirm by a wide margin. The one legacy
edge (confirm sets `superseded_registration_id` for the first time this tick,
`:603-610`) must `save_watches` before the paste call for that thread, or the
satellite's fresh read will not see it and the paste fails closed
(`paste_unauthorized`) — acceptable (fails soft into G4's fallback) but avoid
it: flush eagerly.

**Scope bind:** only the primary `handle` (the recorded predecessor) gets a
push. The `non_holder_handles` "extra" loop (`:638-644`) is the
double-seat/id-space axis (`todo:overlapping-operator-tenancy`, parked,
already done) — out of scope here, left untouched.

**Files:** `libs/cdp_ask/client.py` (`CdpAskClient.paste`), new
`hop_cadence_predecessor_push.py`, `hop_cadence_stall_reconcile.py` (two call
sites), `hop_cadence_events.py` (`emit_predecessor_pushed`), new
`services/git_integration_worker/tests/test_hop_cadence_predecessor_push.py`,
extend `test_hop_cadence_stall_reconcile.py`.

## G4 — Succession release = paste-then-release, never abort-without-tell

`release_superseded_on_confirm` gains `paste_outcome: dict[str, Any] | None = None`.
Gate immediately before `http.abort(exec_id)`:

| `paste_outcome` | Action |
|---|---|
| `None` / `attempted` falsy | **Do not abort.** Return `deferred`, reason `predecessor_push_not_attempted` — self-heals next tick once G2's call site supplies an outcome. Emit `hop_cadence_release_without_receipt`. |
| `attempted=True, ok=False` | Abort proceeds (existing idle-streak gate still applies) — attempt-then-fallback per the charter steelman. Emit `hop_cadence_release_without_receipt` (reason `predecessor_push_failed`) so a "never told" recurrence stays observable. |
| `attempted=True, ok=True` | Abort proceeds normally, no distinct tag. |

This literally encodes the charter's bind: "release must not fire until a
paste attempt (successful or failed) has been recorded for that handle this
confirm tick; a failed attempt still permits the existing idle-streak abort as
a last resort, tagged distinctly."

**Backward-compat bind:** existing tests in
`test_hop_cadence_succession_release_r7.py` that exercise abort/terminalize
behavior unrelated to the push dimension pass
`paste_outcome={"attempted": True, "ok": True}` explicitly (the "told
successfully" case) so their existing assertions keep meaning what they meant
before this change. New tests cover the three-way gate above.

**Files:** `hop_cadence_succession_release.py`, `hop_cadence_stall_reconcile.py`
(wire `paste_outcome` through both `_call_release` sites),
`hop_cadence_events.py` (`emit_release_without_receipt`),
`test_hop_cadence_succession_release_r7.py`.

## G3 — Retire initiating-seat poll doctrine

Investigate-then-bind, independent of G2/G4. Candidates named in the charter
(`claude-ai-cdp-navigation` § Dual-completion, `cdp-operator-proxy`
KEEP-ALIVE doctrine). Valid closure: "no doctrine text instructs polling
`successor_seated`/harvest for hop cutover specifically" — the 9440 pattern
was ad hoc operator behavior, not written guidance, in which case the row
closes with that finding rather than inventing an edit.

## G5 — Auto poll-timeout honesty (9440 turn 75 class)

`handler.py:574-585`'s `if not polled.get("terminal"): ... terminal_failed(...)`
must consult `polled["last"]["last_heartbeat_at"]` (already threaded through
by `nested_sdk.poll_dispatch_terminal`, `:240-259`) before declaring failure.
Fresh heartbeat (age < 60s) ⇒ re-enter the poll for another budget window
instead of `terminal_failed`. Independent of G1–G4; different files
(`handler.py`, `nested_sdk.py`).

## G6 — Tests + Lane-B land

Scoped tests green (`test_hop_handoff.py`, `test_cse_session_paste.py`,
`test_hop_cadence_stall_reconcile.py`, `test_hop_cadence_succession_release_r7.py`,
new `test_hop_cadence_predecessor_push.py`, `test_cursor_auto_progress.py` or
wherever G5's regression lands), ruff + compileall clean on touched files,
committed path-explicit on this dispatch's own Lane-B branch
(`cursor-sdk/lane-9444`). `land_disposition` declared per `git-posture`.

## Out of scope (unchanged from charter)

Id-space exclusion / self-supersede capture (parked,
`todo:overlapping-operator-tenancy`, done); a second Opus/Fable forensic pass;
enrolling charter-runner on 9444; `todo:auto-job-surface-opacity`,
`todo:hop-cadence-standdown-inhibit-gate` (done), `todo:hop-terminal-vs-successor-liveness`
— cite only.
