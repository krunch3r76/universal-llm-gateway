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
7. **Hop** (operator shape 2026-09-10: one IDE tab, hop on every checkpoint, successor headless) — after each
   CHECKPOINT in **autonomous** register: kill the loop pid → `liaison-tick.py --release --holder <me>` →
   spawn exactly one successor (§ Single-Fable cap) → end the turn. In **attended** register: CHECKPOINT, keep
   ticking; the operator hops with `resume R` in the same single tab when they choose.
8. **Stop classes** — `stop_class=CONTEXT_BUDGET` ⇒ CHECKPOINT → hop (autonomous) or page + PARK (attended).
   Other designed stops: § Stops.

## Single-Fable cap (operator 2026-09-10)

`fable_seats(IDE ∪ cursor-sdk) ≤ 1`, enforced by `tmp/watchers/liaison-fable.lock` via `scripts/liaison-tick.py`:
`--claim --holder ide:<root>|sdk:<dispatch_id> [--hop]` · `--release --holder …` · the `--loop` claims and
refreshes it every poll and releases on exit/SIGTERM. Held ⇒ exit 3 — never run a second Fable. An `ide:` claim
against a live `sdk:` holder writes `preempt_by`; the headless loop parks on its next poll (attended outranks).
Stale holder (> 30 min silent) is breakable. `lock.hops` counts hops; **cap 8 per night** ⇒ CHECKPOINT + page,
no successor.

Headless successor (the hop target): `team_dispatch(op=generate, seat=cursor-sdk, contract=none, lane="A",
model=cursor/claude-fable-5-1, cost_intent=deliberate_high_cost, cost_intent_reason=…, packet_path=
tmp/prompts/liaison-successor-<R>.md, dispatch_thread_id=R, work_key=agent-bus:<R>, timeout_seconds=5400)` —
the packet claims the lock with `--hop`, runs ≤ 5 ticks / 60 min, checkpoints, releases, spawns the next.
Composer implement dispatches (`contract=implement`, omit `model=`) run **alongside** — they are not Fable seats.

## Dispatch ladder (cost ↓, cycle time ↓)

| Work | Executor | Bind / review |
|---|---|---|
| Read / recon / ≥3 files | `Task(subagent_type="explore")` in-tab | none |
| Trivial / local edit (<20 lines, no served path) | in-seat | none — commit path-explicit same turn |
| Mechanical implement with dense spec (`files_expected` + ACs) | `team_dispatch(op=generate, seat=cursor-sdk, contract=implement\|pure-mechanical, lane="B", source_ref=todo:…, packet_path=…, dispatch_thread_id=R)` | none — Fable-densified packets skip skeptic |
| Judgment fork | **this seat** binds inline (Fable tab) | independent check only if invariant-touching ∨ cross-agent ∨ recurrence ≥2 |
| Independent check / CDP judgment | **`team_dispatch(model=cdp/opus-5)`** — announce `CDP: <trigger> — <why>`; opus hops (`agent_bus hop`) to stay lean | one round; disagreement ⇒ `CONSULT_PENDING` stop |
| Long-context reasoning inside a work tab | Cursor **Fable 5.1 300k/1M Max** as the tab model (operator authorization 2026-09-10) | `Task(model=claude-fable-5-1-thinking-max)` only from a non-Fable tab — redundant inside one |
| Headless successor (this tab must end) | CHECKPOINT + fresh tab `resume R`; autonomous fallback `cursor_request(contract=investigate, …)` carrying the tip CP | — |

**Reasoning recon** (operator-endorsed 2026-09-10 22:39 PT, observed on 10479#18): before a judgment bind, the
liaison sends the *wide read* to `cdp/opus-5` (`CDP: <trigger> — <why>`, tape cell / CP residue + the decision as
context) and binds on the returned compact. The premium seat (Fable) never spends its window on breadth; it
spends it on the bind. This is the economy pattern, not an exception to the ladder.

Model walls (operator 2026-09-10): `cdp/fable` usage is **limited** — default CDP seat is `cdp/opus-5`; `cdp/fable`
only when the operator names it. Never `anthropic/*` API. `cursor/claude-fable-5-1` is a **tab model** (this
seat, work tabs) — never a `team_dispatch model=`. Same fix failed twice ⇒ stop, `REPEATED_FAILURE`.

## Economy gears (operator 2026-09-10: the Fable 1M Max spend window is finite)

The successor model is **policy, never a constant**. `scripts/liaison-tick.py --root R --set gear=<name>` (or any
`--set key=value`) writes the policy; every digest carries `policy`; successors copy `policy.successor_model`.

| Gear | Successor | Cadence | When |
|---|---|---|---|
| `1-fable-mvp` (tonight) | `cursor/claude-fable-5-1` + `cost_intent=deliberate_high_cost` | ≤ 5 ticks / 60 min / poll 600 s | MVP proving; window holds |
| `2-opus-hops` | `cursor/claude-opus-5` (no cost intent); CDP checks stay `cdp/opus-5` | ≤ 6 ticks | next iteration; Fable only in the attended window |
| `3-wake-on-attention` | `cursor/claude-opus-5`, spawned **only** when a digest has `attention` or `checkpoint_due` (`wake_on_attention_only`) | poll 120 s, no model between events | economy; needs the spawn-on-wake leg (R8) |

Shift = one command; takes effect at the **next** hop (a running successor keeps the gear it read). `SPEND_CAP`
(`policy.max_dispatches_per_night`, default 12) is a designed stop, not a gear change — page, don't downshift
silently. Never let a successor pick a model itself; a refused model is an INFO + stop, never a fallback.

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
