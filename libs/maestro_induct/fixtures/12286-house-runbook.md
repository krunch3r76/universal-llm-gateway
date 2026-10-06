---
runbook: 12286-house
aliases: [operator-ear house, maestro on 12286, sit the ear]
mint: teach-once 2026-09-22 (web-anthropic)
provenance:
  - agent-bus:12286
  - cortex://notes/system/threads/12286-journal.md
  - cortex://notes/system/threads/12291-12291-g2-ear-bind.md
---

# 12286 house — maestro loop (invoked command)

**Trigger:** you are web-anthropic on Cowork and the operator says `resume 12286`, `sit the ear`, `maestro`, or a durable wake names house 12286.

**Refuse:** implementing in-seat · `team-dispatch` pipeline as a bypass of the cursor-auto lane · same-lane `cursor_request(thread=…)` resume (refused `seat.identity_unresolvable` until D3/a:36208 lands) · landing on a green closeout without reading the worktree · a wake that ends the turn without re-arming.

**Steps**
1. Boot: `cortex_brief(seat="web-anthropic")` → `agent_bus_read(get, thread=12286, turn_number="latest")` → tip CHECKPOINT → `fs(read cortex://notes/system/threads/12286-continuity.md)` → this runbook → `12286-journal.md` last entry. Do not read the root linearly.
2. Sweep every worker lane named in the CHECKPOINT (`fetch last=4 compact`). For each `CONSULT_PENDING` addressed to web-anthropic: read the named sidecar/spec, read code with `fs(thread=<worker>)` when ruling on code, then `send` one HARVEST turn on the worker lane (`RULING:` lines, `NEXT_ADMIT:` own line, sidecar_content for detail) + a pointer on 12286.
3. If the conductor session closed (CLOSEOUT after the consult): resume with `cursor_request(new_slug=12286-resume-<score>-hop<n>, parent_thread=12286, lane_role=sub_mission, contract=investigate, lane=B, desired_model=cursor/composer-2.5)` naming `team_dispatch(reuse_thread=<worker>, contract=conductor, source_ref=todo:<slug>, dispatch_thread_id=12286)`. If a successor already appeared (ROW_HOP pattern or D3 live), do nothing.
4. New score: spec stub at `cortex://notes/system/specs/<slug>.md` (Problem/Scope/Acceptance + G1–G7 table with `stop:` tokens + Executor + Invariants incl. the conductor Use-line), `entity_create todo:<slug>` with seed floor, edges, then `cursor_request(new_slug=12286-conductor-<slug>, contract=investigate, lane=B, …)` with the first-utterance spawn line.
5. Outsource judgment legs: Sketch/architecture → `cursor_request(contract=confer, desired_model=cdp/opus-5, desired_effort=xhigh)`; second reader at G6 → `pipeline(op="run", pipeline_id="diff-review", …)`.
6. Leg close: update `12286-continuity.md` (Leg N · Next-pickup), append `12286-journal.md` when a lesson landed, CHECKPOINT on 12286 (`supersedes_turn` = prior CP, `mark_read=true`), fix the root summary if a child notice overwrote it, re-arm `send_later` (12–20 min).
7. Land → live: after a G7 land touching GIW/agent-bus/stargate, `cursor_request(contract=propagate)` on the score's commission lane (drain-gated; never force); `fleet_liveness(code_ref=<sha>)` before any "live" claim; `pipeline(op="validate")` for landed pipelines.

**Falsifier:** a successor seat that loads this runbook and still needs the chat transcript to harvest the next CONSULT_PENDING.
