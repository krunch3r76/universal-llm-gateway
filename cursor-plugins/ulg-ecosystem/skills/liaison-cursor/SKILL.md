---
name: liaison-cursor
description: "Attended Cursor IDE house liaison with team_dispatch and the checkout. A played todo admits one conductor. Life implement stays on cursor_request. Load on IDE hop and resume of a liaison root."
---

# Liaison on Cursor

`seat = attended Cursor IDE ∧ holds(team_dispatch) ∧ holds(checkout) ⇒ this`.
Life, web-anthropic, and any seat without `team_dispatch` use `cursor_request`. Do not load this there.

`ide-continuity` owns harvest, fold, checkpoint, hop, and land. This skill owns **who is admitted**. A played todo admits one conductor.

## Dispatch

Unrelated `:8` missions mint a **new** house via `cursor_bridge(op=spawn_house, slug=…, prompt=…, gui_host=:8)` (or `spawn_tab`). Do not multiplex a second `todo:{slug}` onto an occupied single-mission root (`multiplex_refused`).

| Work | Fire |
|---|---|
| Seed / shape `todo:{slug}` or `friction` | `team_dispatch(op=generate, seat=cursor-sdk, model="cursor/gemini-4-argon", contract=freeform, lane=B, ...)` (`Use the lean-dispatch skill`) for Fast (`G5`), Mid (`G2`), or Slow (`G1`) (`decision:argon-advisor-todo-seeding` · `a:40189`) |
| Recon, loci unknown | `Task(subagent_type="explore")` in this tab |
| Play a `todo:{slug}` | `team_dispatch(op=generate, seat=cursor-sdk, contract=conductor, lane=B, source_ref=todo:…, dispatch_thread_id=<root>)` then end |
| Unpaid `PARKED_TRANSPORT` + reply on thread | After harvest: if no successor after one GIW sweep, fire `fire_park_harvest_continue` — ¬ hand `team_dispatch` hop |
| Script or upload, no repo edit | same cursor-sdk generate, `lane=B`, `contract=freeform` |
| Independent check of closeout | `team_dispatch(model=cdp/opus-5.5)` |
| Open design / strategy / retry(same_AC) fork | `pipeline(op="run", pipeline_id="divergent-ideation-v1", messages=[{"role": "user", "content": "<Schema-P JSON>"}])` before binding |
| High-stakes outbound prose (after OUTBOUND SPEC) | `pipeline(op="run", pipeline_id="divergent-ideation-v1", ...) -> pipeline(op="run", pipeline_id="writer-specialist-v1", ...)` |
| Land on green | in-seat `git merge` of `cursor-sdk/lane-<thread>`. Conflict ⇒ keep both |

`cursor_request` is life-only; code AutoJob door is `agent_bus(tool="request")`.

## Hop (:8 Xvfb)

Operator `hop` / `ide-hop`, or `CONTEXT_BUDGET` (≥35 tool calls or ≥55% window): seal and
`scripts/liaison-ide-hop.py --root <R> --row "<NOW>" --transcript-id <uuid> --gui-host :8 --force`
when the operator asked. Default GUI = `:8` Cursor Agents (`127.0.0.1:9338`).

## Wake

On every `BRIDGE_WAKE` or `liaison-tick.py --set now_row=…`, read digest/tick JSON `budget.stop_class`.
Operator issue: ¬ confirm+Stay on empty NOW; offer concrete actions + in-lock levers same turn (decision:impresario-proactive-offer-on-operator-issue · 17934).
Contradiction or open design fork ⇒ same turn: `pipeline(op=run, pipeline_id=divergent-ideation-v1)` and/or
`cdp/opus-5.5` · `cdp/fable-5.1` · `cursor/grok-4.7`, or execute the falsifying probe — ¬ park on partial read.

A watcher line `closeout turn=` is the harvest, once. `conductor_live ⇒ ¬arm(turn_watcher)`. When a conductor tail is armed, use `watch-supervise.sh tail --until-finish` (returns on `closeout turn=` or `stall-pop:`). Turn-by-turn watch only when the operator names that run. Arm `--loop --heartbeat 1200` only while a finish watcher is live or a row is playable — via `liaison-arm-loop.sh` then `liaison-monitor-loop.sh`; never as the agent Shell PID (a:38446). No playable row and no live watcher ⇒ SIGTERM this root's `--loop`. Loop-abort wakes: restate LOCKED harvest body or stay silent if already relayed. An `AGENT_LOOP_TICK_liaison` that only repeats this tab's CHECKPOINT is not an instruction to play.

Relayed closeout while tail still prints `stall-pop:` ⇒ finished; `watch-supervise.sh stop --label <label>`. Finish tail: no `notify_on_output`; shell completion = harvest turn (friction 37400).

Unpaid `PARKED_TRANSPORT` with the `web-anthropic` reply on the worker thread is owed park-harvest continue (`liaison` Seat stays 6): one successor check, then GIW single-admit — ¬ hand-admit via `team_dispatch`.

## Bad / good

| Bad | Good |
|---|---|
| Arm a tail and read each conductor turn | Wait for the closeout or a designed stop |
| Sketch or `contract=implement` on a house row | One `contract=conductor` admit, then land here |
| Another `--replace` after the same quoted error | A new gate, or stop `REPEATED_FAILURE` |
| Empty continue stamp + reply on thread, wait for "resume" or hand `team_dispatch` hop | One reconcile check, then GIW park-harvest continue |
