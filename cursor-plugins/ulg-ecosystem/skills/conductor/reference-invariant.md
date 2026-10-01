## Invariant

```
conductor(root) ⇒
  packet(six_block) ∧ seat=cursor-sdk ∧ cost_aware_model_tier
  ∧ nest(specialists) ∧ ¬page_human(ranking)
  ∧ drive(all_open_G_rows → completion) ∧ ¬pause_for_interim_continue_ack
  ∧ land(own_verified_lane_B_branch) ⇐ admit_is_standing_merge_ack
  ∧ needs_attended ⇔ operator_only_gate
  ∧ checkout(lane_B) unless named(lane_A_reason)
  ∧ premium_conductor ⇒ announce(why)  # inform-then-proceed; ¬ default
```

Hop contract (bound `a:31807`):

```
hop(conductor) ⇒
  boundary = gated_row_close(Gn) ∧ ¬owed(designed_stop @ Gn)
  ∧ predecessor: journal_append ≺ CHECKPOINT(hop) ≺ closeout(stop: ROW_HOP)
  ∧ ¬ team_dispatch(reuse_thread=<own thread>) from a live seat      # F1/F2: 422/409 by design
  ∧ successor admitted by substrate ⇐ ledger.mark_terminal(predecessor)  # authority transition
  ∧ successor = top-level sibling: reuse_thread=<same worker>, ¬nest_under, new dispatch_id,
                same source_ref/packet_kind/lane/model/knobs/summon_mode, hop_seq+1
  ∧ mission_continuation ⊥ model_behavior     # granularity is the seat's; continuation is the substrate's
  ∧ owed(designed_stop) ⇒ that token, ¬ROW_HOP  # ROW_PINNED · HOLD_MERGE · OPERATOR_GATE · PARKED_TRANSPORT · DONE win
  ∧ live_nested(predecessor) ⇒ ¬hop            # W3: harvest first
  ∧ ¬live_external_gate(mission_lane)          # P1.2: CDP gate stream blocks hop_owed / successor_owed
```

`ROW_HOP` ends **a dispatch**, never the mission — not a pause (no ack, no
reply, no page), not a stop the operator answers, not a merge gate. The
`¬ pause between G-rows` and `¬ second gate on the mission's own merge`
invariants are untouched because nothing waits on anyone. Machine vocabulary
only: grader, reactor, journal — never the liaison register or the pager.

### External gate occupancy (binding — `todo:conductor-hop-wait-protocol` / a:32405)

Protocol landed GIW + Stargate (`todo:conductor-hop-wait-protocol`, R2 review
`cortex://notes/system/reviews/conductor-hop-wait-protocol-r2-review.md` § C8).

**Substrate read, not self-report.** Gate occupancy is read from the CDP lane
snapshot at `mark_terminal` / fire time — the conductor does **not** declare
"gate clear" in closeout to unblock hop. The hop reactor's `hop_owed` and
park-harvest `successor_owed` consult `external_gate_hop_verdict`; a live gate
⇒ **no successor POST** (reactor skip gate `live_external_gate`), even when the
closeout carries `stop: ROW_HOP`.

**Live gate definition.** Blocking when `stream_state` is live
(`queued` / `admitted` / `running` / `parked_waiting`), `purpose` ∈
`{review, operator-proxy, operator_proxy, mission}`, and `parent_thread`
matches the mission lane (`summoning_thread_id`, else worker `thread_id`).
**Seated identity without a live stream does not block** — occupancy follows
`stream_state`, not seat registration alone (R2′).

**Fire-time 409.** Stargate refuses a second external gate on the same lane
while one is still streaming: `cdp_external_gate_live` (409). Harvest the
in-flight gate before firing another `cdp/opus-5.5` review / operator-proxy gate
on that lane.

**Conductor posture.** After `ROW_HOP` with a CDP gate still in flight on the
mission lane, expect the substrate to **defer** the hop successor until the gate
stream completes — correct reactor behavior, not a stall to self-heal with
`reuse_thread`. Harvest the gate; the reactor retries on the next terminal or
watchdog tick. Probe indeterminate while a stronger-model harvest is still owed
⇒ hop withheld (`probe_indeterminate`); probe indeterminate with no harvest
owed ⇒ hop may proceed (intentional fail-open on the hop path; gate *fire* is
fail-closed — do not collapse the asymmetry).

