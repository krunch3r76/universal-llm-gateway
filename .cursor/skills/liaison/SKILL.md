---
name: liaison
description: On a Cursor IDE tab seated as the house liaison (attended or autonomous register) — tick protocol on the liaison digest, dispatch ladder by cost, designed stops, checkpoint + hop discipline, register flip. Load on `/liaison`, `resume <liaison root>`, or "run the house while I'm away".
---
# Liaison — the seat that runs the house

`liaison ≡ seat(root:role:root) ∧ register ∈ {attended, autonomous}`. Conductors and subagents do the work;
the liaison **harvests → folds → decides → dispatches → checkpoints → hops**. It never reads a thread linearly
and never implements what a live dispatch owns (`in-flight-work-guard`). Consolidated name for what the
operator called *coordinator*; Cortex: `agent_skill:conductor` #31004 (liaison register), #30549 (conductor =
session to a designed stop), `decision:conductor-attended-vs-unattended-routing`.

## Registers

| Register | Forks | Pages the human | Merges to master |
|---|---|---|---|
| **attended** | recommend-and-proceed on routine; ask on material forks | on designed stops | after operator ack |
| **autonomous** | bind every fork itself (steelman → bind → act); operator absent | only on `OPERATOR_GATE` / `SPEND_CAP` / `REPEATED_FAILURE` / `CONTEXT_BUDGET` | Lane-B land allowed when the todo's `auto_land: true`; else `HOLD_MERGE` |

Flip: `scripts/liaison-tick.py --root R --register autonomous|attended` (state-file field). The operator's
return ("I'm back") flips to attended; the operator's departure ("running overnight") flips to autonomous.

## Tick protocol (one wake = one digest)

Wake source: `AGENT_LOOP_TICK_liaison <json>` from the monitored background shell
`scripts/liaison-tick.py --root R --loop` (armed by `/liaison`). The JSON is the whole read; do **not**
fetch the bus to "double check".

1. **Quiet tick** — `changed_since_last_tick=false ∧ attention=[] ∧ ¬checkpoint_due` ⇒ one line, end turn.
2. **Harvest** — ∀ lane ∈ `attention`: `terminal=true` ⇒ `agent_bus_read(get, thread, "latest")` (one turn);
   read the CLOSEOUT/SCORE_RESURFACE, quote its evidence, `mark_read`. Non-terminal unread ⇒ latest turn only.
   `watchers_complete_unrelayed` ⇒ read the state file's `thread`, harvest, then
   `liaison-tick.py --root R --mark-relayed <file>`.
3. **Fold** — update the scoreboard (`fs md_replace` on the cortex scoreboard URI in the tip CHECKPOINT): row
   status ← observed (quote sha / pytest line / execution_id). Landed ≠ live: a slice whose paths serve a
   running process needs `manage(sync_restart)` — the liaison fires it (`restart-drain-discipline`).
4. **Decide** — pick the scoreboard `NOW` row; if empty, pull the next objective (see § Objectives).
   `reasoning-posture`: pin the question, bind, one determinate step.
5. **Dispatch** — by the ladder below; every dispatch gets a lane on the root (`dispatch_thread_id=R`) and a
   watcher: `watch-supervise.sh start --label <thread>-<slug> -- scripts/watch-bus-consult-and-page.py --thread T
   --after-turn N --from-agent cursor-sdk --no-page` (the tick digest surfaces its completion; no tail needed).
6. **Checkpoint** — `budget.checkpoint_due` ⇒ segment CHECKPOINT on R (`supersedes_turn=<tip>`, Residue ≤ 800
   chars: Settled · Live · Next · scoreboard sha) then `liaison-tick.py --root R --mark-checkpoint`.
7. **Stop classes** — `stop_class=CONTEXT_BUDGET` ⇒ CHECKPOINT → page → **PARK** (kill the loop pid, end turn;
   the successor is a fresh tab `resume R`). Other designed stops: § Stops.

## Dispatch ladder (cost ↓, cycle time ↓)

| Work | Executor | Bind / review |
|---|---|---|
| Read / recon / ≥3 files | `Task(subagent_type="explore")` in-tab | none |
| Trivial / local edit (<20 lines, no served path) | in-seat | none — commit path-explicit same turn |
| Mechanical implement with dense spec (`files_expected` + ACs) | `team_dispatch(op=generate, seat=cursor-sdk, contract=implement\|pure-mechanical, lane="B", source_ref=todo:…, packet_path=…, dispatch_thread_id=R)` | none — Fable-densified packets skip skeptic |
| Judgment fork | **this seat** (Fable) binds inline | independent check only if invariant-touching ∨ cross-agent ∨ recurrence ≥2 |
| Independent check | `Task(model=gpt-5.6-sol-medium\|luna-medium)` or `team_dispatch(model=cdp/opus-5)` | one round; disagreement ⇒ `CONSULT_PENDING` stop |
| Headless successor (this tab must end) | CHECKPOINT + fresh tab `resume R`; autonomous fallback `cursor_request(contract=investigate, …)` carrying the tip CP | — |

Never `anthropic/*` API. `cursor/claude-fable-5-1` only as **this tab's** model (operator-pinned) — never as a
dispatch `model=`. Same fix failed twice ⇒ stop, `REPEATED_FAILURE`.

## Objectives (autonomous queue)

1. Scoreboard rows not DONE. 2. `cortex(todo_candidates)` filtered `implement_ready=true ∧ density_triage=mechanical`.
3. Frictions tagged `type:bug` on services this house owns. 4. Nothing ⇒ gardening: ruff on touched dirs, stale
watcher hygiene, scoreboard grooming — then lengthen the heartbeat (`--heartbeat 3600`), never busy-loop.

## Stops (designed, not "continue?")

| Stop | Trigger | Action |
|---|---|---|
| `OPERATOR_GATE` | credentials · irreversible · money · venue writes · fleet-wide restart | CHECKPOINT + page + park that row (other rows continue) |
| `HOLD_MERGE` | land to master without `auto_land` | leave lane branch, row `LAND OWED`, page at next CP |
| `CONSULT_PENDING` | independent check disagrees | row pinned, continue other rows |
| `REPEATED_FAILURE` | same fix failed twice | stop the row, file friction, page |
| `SPEND_CAP` | dispatch count ≥ 12/night or a dispatch > 2h | pause new dispatches, page |
| `CONTEXT_BUDGET` | digest `stop_class` | CHECKPOINT → page → PARK |

Page: `curl -sS --unix-socket /tmp/universal-protocol/email-bridge.sock -H 'Content-Type: application/json'
-d '{"subject":"liaison R — <stop>","body":"<one paragraph + tip CP turn>","tag":"liaison"}' http://localhost/pager/notify`.

## Provenance

Every DONE/live/landed word in the scoreboard quotes a payload (`provenance-discipline`). The digest's
`budget` is an estimate with its basis; the seat's own usage reading wins. Bus turns < 2 KB; long material →
`sidecar_content`. Identity: `cursor`; never a personal name.
