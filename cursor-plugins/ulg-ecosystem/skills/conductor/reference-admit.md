## Admit

```text
# Default conductor (omit model= → cursor/grok-4.7)
# Precondition: todo shaped per § Admit from an existing plan; the materializer
# writes the "Use the conductor skill — …" line into <invariants> itself.
team_dispatch(
  op=generate,
  seat=cursor-sdk,
  job=conductor,
  source_ref="todo:{slug}",       # packet_path is REFUSED on this contract
  dispatch_thread_id={root},      # continuity root with turns, or pending-empty child of root
  # generation_options={summon_mode: confer_and_finish},  # optional; or todo attr
  model_knobs={"fast":"true"},
  lane="B",                       # DEFAULT — always pass explicitly. SOT:
                                   # consult-routing § cursor-sdk checkout lane.
                                   # omit = inherit only (nest/resume), ¬ preference.
                                   # lane="A" only on a named reason.
  # skills=["conductor"]  # optional document; ¬ mounted on cursor-sdk — Use-line wins
)
```

Preflight: `manage(action="status")` shows agent_bus + stargate + GIW up ∧
`busy_status` — if the chosen lane's write lease is held by another dispatch,
expect **queued**; record holder on the root CHECKPOINT. ¬ nest_under an
unrelated mission's lease. Receipt: quote `packet_path` (materialized),
`packet_sha256`, `scoreboard_uri`, `dispatch_id`, admitted thread.

**Post-admit check (binding — default regime is Lane B):** quote `active_by_lane` /
`holder_source_repo` from `busy_status`. Expected: `B≥1`, worktree under
`ulg-arc-worktrees/{repo_basename}/lane-*`, branch `cursor-sdk/lane-*`. Substrate **refuses**
`lane="B"` without a materialized worktree (`422 CURSOR_LANE_B_WORKTREE_MISSING`)
instead of silently admitting on shared master. If you see `A=1` and
`holder_source_repo=…/universal-llm-gateway` without a named Lane-A reason, the
admit selected Lane A (omitted/`lane="A"`) — stop nesting mechanical work onto
a B branch that isn't this checkout.

