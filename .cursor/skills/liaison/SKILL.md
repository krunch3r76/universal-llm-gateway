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

**Not the same as** `runbook:liaison-seat-on-a-lane` — that runbook is voice/web liaison on a **foreign lane**;
this skill is the **IDE house seat** on a continuity root.

## Registers

| Register | Forks | Pages the human | Merges to master |
|---|---|---|---|
| **attended** | proceed on every fork with the best-known default (plan / packet target ≻ ladder default); the operator steers post-hoc — never park on a spend or model re-confirm the plan already named (operator 2026-09-11 20:00 PT: "the orchestrator runs without gating on me — I steer as needed") | on designed stops | after operator ack |
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
5. **Dispatch** — by the ladder below; every dispatch gets a lane on the root (`dispatch_thread_id=R`) and an
   **in-session watcher** per **`runbook:bus-consult-watcher`** (atomic legs 1–3 — arm, wake, relay; `--execution-id`
   from admit payload required). CDP producers: `cdp.generate.proof` carries `archive_uri`; on `delivery_failed`
   harvest the archive, not the bus.
6. **Checkpoint** — `budget.checkpoint_due` ⇒ segment CHECKPOINT on R (`supersedes_turn=<tip>`, Residue ≤ 800
   chars: Settled · Live · Next · scoreboard sha) then `liaison-tick.py --root R --mark-checkpoint`.
7. **Hop** (operator shape 2026-09-10: one IDE tab, hop on every checkpoint, successor headless) — after each
   CHECKPOINT in **autonomous** register: kill the loop pid → `liaison-tick.py --release --holder <me>` →
   spawn exactly one successor (§ Single-Fable cap) → end the turn. In **attended** register (operator
   2026-09-11 18:05 PT, spend 72→81%): CHECKPOINT, then hop the tab yourself as the turn's last action —
   `scripts/liaison-ide-hop.py --root R --row "<NOW>"` keystrokes `resume R` + `ARM:` lines (live poller
   labels) into a fresh Cursor chat on the GUI host named by **`policy.gui_host`** (`liaison-tick.py --set
   gui_host=jupiter`; refuses when unset) after raising the live Remote-SSH window by folder URI (refuses when
   the host has no Cursor window on the repo — hops 1–3 of 2026-09-11 hit an unattended host, then Firefox);
   the successor tab re-arms those tails, harvests every wake, folds, plans, dispatches, checkpoints, hops.
   Cadence: **Plan → Dispatch → Hop → Arm → Harvest all triggers → repeat**; one tab is live at a time.
8. **Stop classes** — `stop_class=CONTEXT_BUDGET` ⇒ CHECKPOINT → hop (autonomous) or page + PARK (attended).
   Other designed stops: § Stops.

## Seat model (operator 2026-09-11 20:37 PT: "Fable on IDE may not always be practical")

The liaison mechanics are **model-agnostic** — nothing in the tick loop, digest, resume fence, CHECKPOINT,
hop, or lock reads the tab model. Pick the IDE tab model in the picker; the discipline that changes is the
seat's own, not the house's:

| Tab model class | Serves as liaison? | What must change |
|---|---|---|
| Opus-class (`claude-opus-5`, Fable when affordable) | yes — binds judgment forks inline | nothing |
| Below Opus (Grok 4.6, Sonnet, Composer, GPT-5.6) | yes for harvest → fold → dispatch → CP → hop | presence-discipline P1–P4 are **explicit obligations**; every judgment bind goes to `cdp/opus-5` first (§ Reasoning recon) and the seat binds on the returned compact; premium binds the plan names still go to `cdp/fable` |

Observed 2026-09-11 (Cursor Projects window, Grok tab on this root): `/liaison` seated, Goal set, judgment
routed to `cdp/opus-5-high` — the ladder carried the reasoning. Fable stays the right seat on **claude.ai**
(`cdp/fable`) where the window is the product; in the IDE it is one option, not a requirement. What remains
Fable-specific here: gear `1-fable-mvp` (headless Fable successors) and the lock filename.

## Single-liaison-seat cap (operator 2026-09-10; generalized from "single-Fable")

`liaison_seats(IDE ∪ cursor-sdk) ≤ 1` per root, enforced by `tmp/watchers/liaison-fable.lock` via
`scripts/liaison-tick.py`: `--claim --holder ide:<transcript_id>|sdk:<dispatch_id> [--hop]` ·
`--release --holder …`. **Holder identity is the tab, not the root** — two attended tabs that both claim
`ide:<root>` silently co-hold (same string ⇒ re-claim succeeds); with `ide:<transcript_id>` the second tab is
`refused` (`reason=held`) and runs as a **worker tab**: its own legs and turns, no loop, no Rows fold, no
CHECKPOINT on the root. Take the seat over only with the operator's word or after the holder's lease
expired (`--release --holder <live>` then claim). Model seats refresh the
declared lease (`expires_at`) on each `--once` tick (`tick_seq` / `turns_seen`); `seat_lock_free` means
`holder is None ∨ now > expires_at`. The gear-3 **ticker** holds `liaison-ticker.lock` (`ticker:<root>`) —
it never takes the seat mutex and cannot write `preempt_by`. Attended `--loop` (gear 1/2) still claims the seat
lock; gear 3 uses `--loop --spawn-on-wake` instead. `lock.hops` is keyed by `night_id`; **cap per night** ⇒
CHECKPOINT + page, no successor.

## Headless successor (resume-fence pull)

The hop target is a **message dispatch**, not a packet file — `contract=none` always:

```
team_dispatch(
  op=generate,
  seat=cursor-sdk,
  contract=none,
  lane="A",
  model=<policy.successor_model>,
  prompt=<build_successor_message>,
  dispatch_thread_id=<R>,
  work_key=agent-bus:<R>,
  timeout_seconds=<max_hop_minutes*60+1800>,
)
```

`build_successor_message` (via `libs/bus_watch/spawn_on_wake.py`) emits ≤2048 bytes containing verbatim:
`resume <R>`, `dispatch(tool="continuity"`, `agent_bus_read(thread_get`, `gear:`, `row=`,
`tip_cp_ordinal=`, `contract: none`. Gear-3 ticker (`scripts/liaison-tick.py --loop --spawn-on-wake`) fires
this body when `attention`, `checkpoint_due`, or a fresh `CONTEXT_BUDGET` stop applies. The successor claims
the lock with `--hop`, runs ≤ 5 ticks / 60 min, checkpoints, releases, spawns the next. Composer implement
dispatches (`contract=implement`, omit `model=`) run **alongside** — they are not Fable seats.

## Dispatch ladder (cost ↓, cycle time ↓)

| Work | Executor | Bind / review |
|---|---|---|
| Read / recon / ≥3 files | `Task(subagent_type="explore")` in-tab | none |
| Trivial / local edit (<20 lines, no served path) | in-seat | none — commit path-explicit same turn |
| Mechanical implement with dense spec (`files_expected` + ACs) | `team_dispatch(op=generate, seat=cursor-sdk, contract=implement\|pure-mechanical, lane="B", source_ref=todo:…, packet_path=…, dispatch_thread_id=R)` | none — Fable-densified packets skip skeptic |
| Judgment fork | **this seat** binds inline when Opus-class; below Opus, § Reasoning recon first (`cdp/opus-5` wide read → bind on the compact) | independent check only if invariant-touching ∨ cross-agent ∨ recurrence ≥2 |
| Independent check / CDP judgment | **`team_dispatch(model=cdp/opus-5)`** — announce `CDP: <trigger> — <why>`; opus hops (`agent_bus hop`) to stay lean | one round; disagreement ⇒ `CONSULT_PENDING` stop |
| Long-context reasoning inside a work tab | Cursor **Fable 5.1 300k/1M Max** as the tab model when affordable (operator authorization 2026-09-10; optional — § Seat model) | `Task(model=claude-fable-5-1-thinking-max)` only from a non-Fable tab — redundant inside one |
| Successor (this tab must end) | attended: CHECKPOINT + `scripts/liaison-ide-hop.py --root R --row "<NOW>"` (keystroke hop, fresh tab, ~40k-token orient vs 12–31M per headless hop); autonomous: § Headless successor (resume-fence pull) — the successor pulls the tip via `dispatch(tool="continuity")`; `cursor_request` is not a successor path (enqueues cursor-auto) | — |

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
| `3-wake-on-attention` | `cursor/claude-opus-5`, spawned **only** when a digest has actionable `attention` (unread > 0) or `checkpoint_due` | poll 120 s via `scripts/liaison-tick.py --loop --spawn-on-wake`; ticker holds `liaison-ticker.lock`, **not** the seat mutex | **disarmed by default** (`policy.ready=false`); arm with explicit `--set ready=true`. First live night = operator gate (A7) |

Shift = one command; takes effect at the **next** hop (a running successor keeps the gear it read). A live
`--loop` absorbs `--set` / `--mark-*` edits from another shell on its next poll (`libs/bus_watch/tick_state.py`
`absorb_operator_edits`, logged as `operator_edit_absorbed`); before 2026-09-12 the loop's in-memory state
clobbered them within one poll — verify a steer by re-reading `--policy` after the next tick. `SPEND_CAP`
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
| `SPEND_CAP` | dispatch count ≥ `policy.max_dispatches_per_night` (read from the digest at tick time — never a number frozen in prose; R14 / a:33104) or a dispatch > 2h | pause new dispatches, page |
| `CONTEXT_BUDGET` | digest `stop_class` | CHECKPOINT → page → PARK |

Page: `curl -sS --unix-socket /tmp/universal-protocol/email-bridge.sock -H 'Content-Type: application/json'
-d '{"subject":"liaison R — <stop>","body":"<one paragraph + tip CP turn>","tag":"liaison"}' http://localhost/pager/notify`.

## Provenance

Every DONE/live/landed word in the scoreboard quotes a payload (`provenance-discipline`). The digest's
`budget` is an estimate with its basis; the seat's own usage reading wins. Bus turns < 2 KB; long material →
`sidecar_content`. Identity: `cursor`; never a personal name.
