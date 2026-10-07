---
name: liaison
description: House liaison on a continuity root. A played todo admits one conductor. Load on /liaison, resume of a liaison root, close the house, or run the house while away.
---
# Liaison — the seat that runs the house

`liaison ≡ seat(root:role:root) ∧ register ∈ {attended, autonomous}`. Conductors do the work;
the liaison **harvests → folds → decides → admits one conductor → checkpoints → hops**. ¬ linear-read a thread;
¬ implement what a live dispatch owns (`in-flight-work-guard`). Consolidated name for *coordinator*;
Cortex: `agent_skill:conductor` #31004 (liaison register), #30549 (conductor = session to a designed stop),
`decision:conductor-attended-vs-unattended-routing`.

**Play (binding).** `played(todo:{slug}) ⇒ admit(contract=conductor, source_ref=todo:{slug}, dispatch_thread_id=<root>, lane=B) ∧ end`.
Sketch, Compose, densify, and `contract=implement` are that conductor's steps — this seat does not fire them.
This binding wins over the dispatch ladder, Seat stays, and a digest one-step that says to Sketch or implement.

| Bad | Good |
|---|---|
| Sketch or `contract=implement` from this tab on a house row | One conductor admit; harvest its closeout |
| CDP generate on the house thread as the play | Conductor lane parented to the house |

**Not the same as** `runbook:liaison-seat-on-a-lane` — that runbook is voice/web liaison on a **foreign lane**;
this skill is the **IDE house seat** on a continuity root.

## FOL pipeline

```
∀ wake: one_digest ∧ ¬double_check_bus
played(todo:{slug}) ⇒ admit(conductor, source_ref=todo:{slug}, lane=B) ∧ end
liaison_seats(IDE ∪ cursor-sdk) ≤ 1 per root
∀ roots sharing master: isolate(paths ∧ locks) ≻ collide ∧ ¬discard(peer_work)
conductor_live ⇒ ¬arm(turn_watcher)
gate_blocks(row) ⇒ friction() ∧ card_row ∧ swap_NOW(gate) ∧ play(gate) ∧ ¬STAY(blocked)
played(row) ∧ landed(master) ⇒ todo-close ≺ add_next_ungated ∧ play
register=attended ∨ autonomous ⇒ land_on_green; HOLD_MERGE ⇔ operator_explicit_hold
go_under ⇔ operator_overnight_or_departure; CONTEXT_BUDGET(attended, work_left) ⇒ ide-hop (¬go_under)
ide_chain ⇒ ready=false; ticker ⇒ ready=true via --go-under only
below_Opus ∧ judgment_fork ⇒ cdp/opus-5.5 first (¬cursor/claude-*; ¬anthropic/*)
same_fix_failed_twice ⇒ REPEATED_FAILURE ∧ ¬third_variant
```

## Registers

| Register | Forks | Pages the human | Merges to master |
|---|---|---|---|
| **attended** | proceed on every fork with best-known default (plan/packet ≻ ladder); operator steers post-hoc — ¬park on spend/model re-confirm already named | designed stops | **land on green**. `HOLD_MERGE` only if operator **explicitly asked to hold** — ¬ operator ack as land gate (a:35726 · a:35731) |
| **autonomous** | bind every fork (steelman → bind → act); operator absent | only `OPERATOR_GATE` / `SPEND_CAP` / `REPEATED_FAILURE` / `CONTEXT_BUDGET` | **land on green**. `HOLD_MERGE` only if operator **explicitly asked to hold** — ¬ `auto_land` flag, ¬ missing chat ack |

Flip: `scripts/liaison-tick.py --root R --register autonomous|attended`. Return ("I'm back") → attended; departure ("running overnight") → autonomous.

### go under / come up

**`go under`** (aliases `run headless` · `hand off to cursor-sdk` · `go under <root>`) = hand house to gear-3 ticker and leave tab. **One verb, harness-owned** (`libs/bus_watch/go_under.py`):
1. seat authors CHECKPOINT (residue ≤ 800); `--mark-checkpoint` may ride on step 2;
2. `liaison-tick.py --root R --go-under --holder ide:<transcript_id>` — `register=autonomous`, `policy.ready=true`, release `ide:` seat, SIGTERM attended `--loop`s, start ticker if no `liaison-ticker-<R>.lock`, arm one handoff wake (`state.handoff.seq`). Refuses only `successor_model_unset`;
3. paste printed `UNDER → …` line and end. ¬hop, ¬dispatch after the verb.

**When:** operator overnight/departure — **not** attended `CONTEXT_BUDGET` mid-arc (§ Tick step 7 **ide-hop**). PARK ¬ a step while `NOW` or unread lanes remain.

Overnight leftover = **hold | play | sit** (`spawn_wake.play_classify`): hold = live conductor owns addressed `todo:{slug}`; play = `now_row` names `todo:{slug}` and no live owner → admit conductor; sit = house attention, no addressed todo → headless liaison. Default `--go-under` is play-aware; `--go-under --sit` is the old 10534 chain. `successor_contract=conductor` on house generate ≠ play path.

**Come up** (operator "I'm back" / "come up" / "stop the ticker" / "take the house" — **not** `resume R`, which is check-in and leaves ticker driving): (1) SIGTERM `--loop --spawn-on-wake` ticker; (2) `--register attended --set ready=false`; (3) drop `induction_binds` that forbid IDE hop; (4) then `resume R` may `--take-over`. One driver: IDE-hop ⇒ `ready=false`; ticker ⇒ `--go-under` only on overnight/departure. Check-in `resume R` while UNDER must not run this sequence.

**Operator guide.** "How do I use …" / "what changed" / new ruling ⇒ **LOAD AND EXECUTE** `runbook:liaison-operator-guide` this turn. Attended IDE tab = override; gear-3 headless = fallback. GIW doorbell schedule retired/refuses. Ticker = only house wake.

## Tick protocol (one wake = one digest)

Wake source: `AGENT_LOOP_TICK_liaison <json>` from `scripts/liaison-monitor-loop.sh` (armed by `/liaison` via `liaison-arm-loop.sh`, then monitor with `notify_on_output`). Agent Shell must **not** be the `liaison-tick.py --loop` PID — that process is `setsid`+`nohup`'d so SIGTERM/harvest exit is not a `system_notification` that displaces LOCKED harvest SMS (a:38446). Monitor Shell = liveness token; when it dies the loop exits and releases the seat. JSON is the whole read; ¬ fetch bus to "double check". Read `digest.induction` **first**. Bind NOW: `--set now_row="<row>"`; standing binds: `--set induction_binds=…`; already-loaded: `induction_loaded`.

**Loop-abort wakes (a:38446):** if `system_notification` still reports monitor Shell stopped after harvest → restate LOCKED operator-facing body, or silence if already relayed — ¬ loop-status-only reply.

**After resume, before first move:** continuity card ## Skills → Use each slug; skip those named in induction `Loaded already (do not re-read)`.

**Wakes:** Native `CreateGoal` ≠ house ticker. Skip `CreateGoal` on `/liaison`, resume, hop pickup unless operator asks for a panel pin. Leftover goal still active at hop `ok` or arc close ⇒ `UpdateGoal(status=complete)`.

House wakes, cheapest first:
1. Finish signals (`closeout turn=` / `consult complete`) — one harvest. Conductor tail = `watch-supervise.sh tail --until-finish`. `stall-pop:` on already-relayed closeout ≠ wake (§ Seat stays)
2. `liaison-tick.py --loop` only while finish watcher live or row playable. Tick that only repeats this tab's CHECKPOINT ≠ wake
3. Heartbeat **1200s** — backup only while (2) holds. No playable row ∧ no live watcher ⇒ SIGTERM the loop. House stays

## Seat stays (operator 2026-09-22)

Binds. Later prose that conflicts loses.

1. **One conductor.** This seat holds `team_dispatch` and the checkout. Played `todo:{slug}` → `team_dispatch(op=generate, seat=cursor-sdk, contract=conductor, source_ref=todo:{slug}, dispatch_thread_id=<root>, lane=B)` then end. Repo write = that conductor's nested `contract=implement`. ¬ this seat `contract=implement` or Sketch for a house row. `cursor_request` is life-only (life has no `team_dispatch`). On code, AutoJob admit door = `agent_bus(tool="request")`, not this seat's implement path.
2. **¬ close house on dead tail or empty wake.** Closeout already relayed + tail still `stall-pop:` → finished: `watch-supervise.sh stop --label <label>`. That tail ≠ watcher, hop, or seat-end reason. No playable next ∧ no live watcher ⇒ ¬ arm `--loop`; SIGTERM if running. Tick that only repeats CHECKPOINT ≠ play instruction. House stays; wake stops.
3. **Friction → house row → play gate.** Row cannot proceed: `friction()` on owner same turn + continuity card `## Rows`. **Gate** = friction current row cannot pass until resolved. Gate swaps into NOW (`--set now_row=` `Friction a:<n> …`). Blocked row becomes next. Play gate same turn on ladder in (1). ¬ STAY on blocked. ¬ page unless armed `OPERATOR_GATE`.
4. **Played row → todo-close → add next.** Played = land on master for that `todo:{slug}`. Same turn before next: `pipeline(op=run, pipeline_id=todo-close)` so `workflow_state=done`. Open card still = work: clock admits another conductor (re-admit ≠ replace operator overnight). After close, add every next ungated deliverable to `## Rows` + `--set now_row=`, then play. Clears `now_row=quiet`; re-arms `--loop --heartbeat 1200` if down. Ungated rows may run concurrent. Row waiting on prior play stays off NOW. `now_row=quiet` / empty NOW legal only when no open deliverable. ¬ close on `checks_failed`, `ROW_PINNED`, or `land_disposition: discard`.
5. **No turn-by-turn watch.** `conductor_live ⇒ ¬arm(turn_watcher)`. Review closeout or designed stop. Turn-by-turn only when operator names that run. ¬ rebuild start+tail for every in-flight lane on wake.
6. **Owed park-harvest resume — GIW single-admit, ¬ hand `team_dispatch`.** Harvest `stop: PARKED_TRANSPORT` whose worker thread already has `web-anthropic` reply after `closeout_turn`: check predecessor successor (`hop_successor` / ledger `hop_from`). If none after one GIW reconcile sweep → fire GIW park-harvest continue (`fire_park_harvest_continue` under `claim_stop_service`, `caller_agent=conductor-hop`). ¬ build `team_dispatch` with `hop_from`/`hop_reason`. ¬ status-report-and-wait. ¬ treat operator "resume the conductor" as gate. `HOLD_MERGE` holds land, not resume. Consult reply naming operator question ≠ armed `OPERATOR_GATE`. Distinct from GIW `park_live` cancel (a:38111) and hop-body refuse (a:38115).

| Bad | Good |
|---|---|
| Arm a tail and read each conductor turn | `tail --until-finish` |
| Rebuild start+tail for every in-flight lane at wake | Leave turns on bus until finish |
| PARKED_TRANSPORT + reply on thread, wait or hand-admit | One reconcile check, then GIW park-harvest continue if unpaid |
| Run `liaison-tick.py --loop` as agent Shell | `liaison-arm-loop.sh` + `liaison-monitor-loop.sh` (a:38446) |
| Loop-abort wake → "Heartbeat loop stopped…" only | Restate LOCKED body, or silence if already relayed |

## Tick steps

0. **Wake check** — ¬ arm turn-by-turn on in-flight (§ Seat stays 5). Arm `--loop --heartbeat 1200` only while finish watcher live or row playable. Skip `CreateGoal`.
1. **Quiet tick** — `changed_since_last_tick=false ∧ attention=[] ∧ ¬checkpoint_due` ⇒ one line, end.
2. **Harvest** — ∀ lane ∈ `attention`: `terminal=true` ⇒ `agent_bus_read(get, thread, "latest")`; quote CLOSEOUT/SCORE_RESURFACE; `mark_read`. Unpaid `PARKED_TRANSPORT` + consult wake on thread ⇒ successor check / GIW single-admit (§ Seat stays 6). Non-terminal unread ⇒ latest turn only. `watchers_complete_unrelayed` ⇒ harvest then `--mark-relayed <basename>` (`path.name` only). `kind=friction` / `digest.frictions` ⇒ § Friction score rows (≤ one `assertion_get` deepen).
3. **Fold** — update scoreboard (`fs md_replace` on tip-CHECKPOINT cortex URI): row status ← observed (quote sha / pytest / execution_id). Landed ≠ live: served paths need `manage(sync_restart)` — LOAD `restart-drain-discipline`.
4. **Decide** — compute Address. Unpaid `PARKED_TRANSPORT` with consult wake on thread = Address (§ Seat stays 6), not STAY / operator-resume gate. Played `todo:{slug}` admits/re-admits one conductor (§ Play). Conductor owns scoreboard NOW under `work_key=todo:{slug}` and nests Composer after Compose. ¬ this seat fire that implement. No seat bind ⇒ induction NOW **is** newest undispositioned friction. Gear 3 pins attention-tier NOW into `policy.now_row` and releases when spent. `--set now_row=…` wins while satisfied; `--set now_row=quiet` holds. `reasoning-posture`: pin question, bind forks.
5. **Admit** — Address = playable `todo:{slug}` ⇒ one conductor admit (§ Play) then end. Address = gate ⇒ play gate. Address = STAY/PARK ⇒ do that. ¬ Sketch / implement / CDP-generate as the play from this seat.
6. **Checkpoint** — when due or before hop/stop: tip CHECKPOINT (residue lean). `--mark-checkpoint` when harness needs it.
7. **Hop / stop classes** —
   - `CONTEXT_BUDGET` attended + remaining work ⇒ CHECKPOINT → `liaison-ide-hop.py` (§ Dispatch ladder Successor). `--go-under` wrong here.
   - `CONTEXT_BUDGET` empty NOW ∧ nothing unread ⇒ CHECKPOINT → STAY or PARK.
   - `--go-under` only operator overnight/departure (§ Registers).
   - Headless `sdk:` at budget ⇒ CHECKPOINT → release; ticker spawns.
   - Other designed stops: § Stops.
   - Hop land: successor attaches `tail --label` per ARM label; arms `--loop --heartbeat 1200` only while watcher live or row playable. Skip `CreateGoal`. Load `liaison-hop-retire_ulg` when unsure. Answer `RETIRED → <landed_transcript_id>` in one line; ¬ harvest after land.
8. **Stop classes** — see § Stops.

## Seat model

Attended liaison tab uses **`/mcp/code`**. Repo writes / mechanical work via **`team_dispatch`** (`seat=cursor-sdk`, `lane=B`). Life Cowork (`/mcp/life`) still mounts `cursor_request` when that surface has no `team_dispatch` generate door.

Mechanics are **model-agnostic** (tick, digest, resume fence, CHECKPOINT, hop, lock). Pick IDE tab model in picker:

| Tab model class | Serves as liaison? | What must change |
|---|---|---|
| Opus-class (`claude-opus-5`, Fable when affordable) | yes — binds judgment forks inline | nothing |
| Below Opus (Grok 4.7, Sonnet, Composer, GPT-5.6) | yes for harvest → fold → dispatch → CP → hop | presence-discipline P1–P4 **explicit**; every judgment bind → `cdp/opus-5.5` first (§ Reasoning recon) unless criterion already closed on **named assertion + Explore locus** — then name CDP skip as rejected alternative and bind |

Fable on **claude.ai** (`cdp/fable-5.1`) remains a product seat; IDE optional. Gear `1-fable-mvp` and lock filename remain Fable-named. **Cursor Fable credit window closed** (2026-09-12) — ¬ escalate to `cursor/claude-fable-5-1`.

Woken claude.ai liaisons read house via latest `DIGEST <root>` turn (`agent_bus_read(fetch, thread=<root>, last=3, compact=true)`) when `policy.post_digest` true (gear 3 default).

## Single-liaison-seat cap

`liaison_seats(IDE ∪ cursor-sdk) ≤ 1` per root, enforced by `tmp/watchers/liaison-fable-<root>.lock` via `scripts/liaison-tick.py`: `--claim --holder ide:<transcript_id>|sdk:<dispatch_id> [--hop]` · `--release --holder …`.

Resolve `ide:<transcript_id>` with `liaison-ide-hop.py --find-transcript` from tab's **first user message**: `resume <root>` when that was first; `"/liaison <root>"` only when slash line is first. Bare `"/liaison"` can hit a **foreign** tab.

**Holder identity = tab, not root** — two attended tabs claiming `ide:<root>` silently co-hold; with `ide:<transcript_id>` second tab is `refused` (`reason=held`) → **worker tab** (own legs/turns; ¬loop, ¬Rows fold, ¬CHECKPOINT on root). Taking seat from live attended holder needs operator word: `resume <root>` in fresh tab **is** that word ⇒ `--take-over`. `/liaison` without the word stays worker. Headless `sdk:` preempted by any `ide:` claim.

**Stop a loop:** prefer SIGTERM on pid in `tmp/watchers/liaison-loop-<R>.pid` (from `liaison-arm-loop.sh`). Else `pkill -f 'liaison-tick[.]py --root <R> --loop'` then `pgrep -fc` = 0. Attended monitor Shell = `liaison-monitor-loop.sh` — not the python. ¬ arm `--loop` as agent Shell. Lock records `holder_pid`; only claimant / `--release` / operator override releases. Model seats refresh lease (`expires_at`) each `--once` tick. Gear-3 **ticker** holds `liaison-ticker-<root>.lock` — never takes seat mutex, cannot write `preempt_by`. Attended `--loop` (gear 1/2) claims seat lock; gear 3 uses `--loop --spawn-on-wake`.

**Idle forfeit** (`spawn_pending.idle_ide_forfeit`): under `register=autonomous`, `ide:` holder silent > `policy.ide_idle_forfeit_s` (default 1200s) ⇒ ticker releases and spawns. Attended never forfeits. `lock.hops` keyed by `night_id` on this root's lock; this-root cap = `policy.max_hops_per_night`. `lock.hops==8` ≠ designed stop.

(Specimen detail: L3 sidecar § Seat-lock specimens.)

## Peer-house conflicts

`∀ roots sharing master: isolate(paths ∧ locks) ≻ collide`. One liaison per root; several roots may still touch same files. `¬ discard(peer_work)` — blocked merge = reconcile, not hop-away or copy-land. **Use `git-posture` § Land**.

| Conflict | Action |
|---|---|
| Keepable overlap (both hunks valid) | attended IDE `git merge`; keep both; verify ACs; ¬ hop while unreconciled |
| Judgment (which hunk) | `cdp/opus-5.5` → `cdp/opus-5`. ¬ `cursor/claude-*` |
| Sensitive (`OPERATOR_GATE` · discard-risk · identity) | only after that ladder fails: page human |

`¬ page(human)` for ordinary merge conflict. `cdp/fable-5.1` only when operator names it.

## Headless successor (resume-fence pull)

Hop target = **message dispatch**, not packet file. Sit leftover on friction/disposition score row ≠ house successor: ticker spawns **row-bind hop** (default `cursor/grok-4.7` Standard + high via `policy.row_bind_model` / `row_bind_model_knobs`) to bind `ROW_CLASS: low|trio`. That seat ¬ edit repo (bind then STOP; remaining hops ticker-fired — § Friction).

```
# friction NOW / sit leftover — ticker, not house successor
team_dispatch(op=generate, seat=cursor-sdk, lane="B",
  model=cursor/grok-4.7,  # policy.row_bind_model; knobs effort=high, fast=false
  contract=freeform, prompt=<row-bind wake>, dispatch_thread_id=<R>,
  work_key=row-bind:<fid>:night-<night>)

# other sit wakes (checkpoint / hop, no friction row)
team_dispatch(op=generate, seat=cursor-sdk, contract=freeform, lane="B",
  model=<policy.successor_model>, prompt=<build_successor_message>,
  dispatch_thread_id=<R>, work_key=agent-bus:<R>,
  timeout_seconds=<max_hop_minutes*60+1800>)
```

`build_successor_message` (`libs/bus_watch/spawn_on_wake.py`) emits ≤2048 bytes with verbatim: `resume <R>`, `dispatch(tool="continuity"`, `agent_bus_read(thread_get`, `gear:`, `row=`, `tip_cp_ordinal=`, `contract: none`.

Gear-3 ticker fires on: unread **live** lane · finished **work** lane closeout once per turn count · `--go-under` handoff once · `checkpoint_due` once per CP epoch · undispositioned friction on charter-owned service once per assertion id (≤ `policy.friction_dispatch_cap`) · fresh `CONTEXT_BUDGET` from `sdk:` stream. Successor claims lock with `--hop`, runs ≤ 5 ticks / 60 min, checkpoints, releases, spawns next. Composer implement (`contract=implement`, omit `model=`) runs **alongside** — not Fable seats.

## Dispatch ladder (cost ↓, cycle time ↓)

`attended IDE ∧ team_dispatch ∧ checkout ∧ played(todo) ⇒ admit(job=conductor)` (§ Play). Code AutoJob admit = `agent_bus(tool="request")`. Life implement = `cursor_request`.

| Work | Executor | Bind / review |
|---|---|---|
| Read / recon / ≥3 files | `Task(subagent_type="explore")` in-tab | none |
| Trivial / local edit (<20 lines, no served path) | in-seat (Opus-class only by default; successor **dispatches** instead) | none — commit path-explicit same turn |
| Play a `todo:{slug}` | `team_dispatch(… contract=conductor, lane="B", source_ref=todo:…, dispatch_thread_id=R)` then end | conductor nests Sketch, Compose, densify, implement |
| Mechanical implement with dense spec | conductor's nest, not this seat | none — Fable-densified packets skip skeptic |
| Repo write when this seat lacks `team_dispatch` or checkout (life) | **`cursor_request(job=implement)`** — never STAY, never needs-attended | Attended IDE with both: skill `liaison-cursor`. ¬ this row |
| Design / judgment fork on played row | conductor's nest | one round inside conductor; disagreement ⇒ CONSULT_PENDING |
| Judgment fork (independent check) | this seat binds inline when Opus-class; below Opus, § Reasoning recon first | independent check only if invariant-touching ∨ cross-agent ∨ recurrence ≥2 |
| Independent check / CDP judgment | **`team_dispatch(model=cdp/opus-5.5)`** — announce `CDP: <trigger> — <why>` | one round; disagreement ⇒ CONSULT_PENDING |
| Long-context reasoning inside work tab | `cursor/grok-4.7` (card 256k, knob 500k) | **`cdp/fable`** only when Kaywan asks — never `cursor/claude-*` |
| Successor (this tab must end) | attended: CHECKPOINT + `scripts/liaison-ide-hop.py --root R --row "<NOW>" --transcript-id <uuid>`; autonomous: § Headless successor | — |

**Reasoning recon** (2026-09-10): before judgment bind, send *wide read* to `cdp/opus-5.5` (`CDP: <trigger> — <why>`, tape/CP residue + decision as context); bind on returned compact. Premium seat spends window on the bind, not breadth.

Reasoning strength low→high: `cdp/opus-5.5`, `cdp/opus-5`, `cdp/fable-5.1`, `cdp/fable-5`. Walk `high` → `extra` → `max` inside a rung before next wire.

Model walls: default CDP = `cdp/opus-5.5`; `cdp/fable-5.1` only when operator names it. Never `anthropic/*`. `cursor/claude-fable-5-1` ≠ escalation / `team_dispatch model=`. Same fix failed twice ⇒ stop, `REPEATED_FAILURE`.

## Economy gears

Successor model = **policy, never a constant**. `--set gear=<name>` (or `--set key=value`) writes policy; digests carry `policy`; successors copy `policy.successor_model`.

| Gear | Successor | Cadence | When |
|---|---|---|---|
| `1-fable-mvp` | `cursor/grok-4.7` (`effort=high`, `fast=false`) | ≤ 5 ticks / 60 min / poll 600 s | **¬ overnight** — use gear 3; Cursor Fable credit closed |
| `3-wake-on-attention` | **`cursor/grok-4.7`** (`effort=high`, `fast=false`) from preset; first play admit passes same `successor_model`+knobs (¬ omit `model=`). Override `--set successor_model=<slug>` | poll 120 s via `--loop --spawn-on-wake`; ticker holds `liaison-ticker-<root>.lock`, **not** seat mutex | **armed only by explicit `ready`** (`--set ready=true` or `--go-under`). IDE chain ⇒ `ready=false`; ticker ⇒ `--go-under` |

Shift takes effect at **next** hop. Live `--loop` absorbs `--set` / `--mark-*` on next poll (`absorb_operator_edits`). `SPEND_CAP` (`policy.max_dispatches_per_night`) **off by default** — positive cap = designed stop (page, ¬ silent downshift). ¬ let successor pick a model; refused model = INFO + stop, never fallback.

## Objectives (autonomous queue)

1. Scoreboard rows not DONE. 2. `cortex(todo_candidates)` filtered `implement_ready=true ∧ density_triage=mechanical`.
3. Friction score rows (§ below). 4. Nothing ⇒ gardening (ruff on touched dirs, stale watcher hygiene, scoreboard grooming) then lengthen heartbeat (`--heartbeat 3600`) — ¬ busy-loop.

## Friction score rows

`todo:liaison-friction-score-rows` · `libs/bus_watch/friction_rows.py`. Open `friction()` on house **charter-owned** services = score rows. Same driver — no second loop. Attended play of `todo:{slug}` follows § Play. Ticker LOW/TRIO fire = headless machine — **not** a license for this seat to Sketch or `job=implement`.

| Leg | Mechanic |
|---|---|
| Charter | `--set owned_services=…` feeds digest. Empty ⇒ digest empty. Friction this seat files is still a house row on `## Rows` |
| Harvest | `digest.frictions` (≤ 12) + `digest.friction_summary`. Read path: Cortex UDS `assertions` on owner |
| NOW | no seat bind ⇒ newest `forcing` row **is** NOW. Seat bind outranks non-gate friction. **A gate swaps.** |
| Disposition | **Ticker first spawn = row-bind hop**, not Composer house generate. Agent posts `ROW_CLASS: low\|trio` then STOP; ticker fires remaining (LOW = implement lane B + later apply-all review; TRIO sketch default `cdp/opus-5.5` when no `todo:{slug}`). `--mark-friction` records disposition. In-seat ≤20-line `direct-first` only after `ROW_CLASS: low`, does not skip review+apply+land |
| Close-back | **on the assertion**: `cortex(tool="friction_close", …)`. Superseded ⇒ leaves next harvest. `todo-minted`/`declined` still open = `state=close_pending` |
| Ticker | newest forcing ∧ unlatched → `attention` (`kind=friction`) — **one per tick**, none once `friction_dispatch_cap` (default 3) latched tonight; one spawn per assertion id. Sit leftover on forcing friction: row-bind posts class then **STOP**; ticker fires LOW/TRIO — never Composer house successor on bind leg. Review harvest: ticker applies ALL suggestions as one lane-B house generate (`job=freeform` + apply-all) — ¬ page ¬ come-up ¬ park |
| REPEATED_FAILURE | second `direct-first` on same row ⇒ `state=repeated_failure`: consult then todo-minted \| declined; never third variant |

## Gate swap

Covered by § Seat stays 3. Same-turn: `friction()` → card row → swap NOW → play gate on dispatch ladder. ¬ STAY on blocked. ¬ page. Sit friction spawn = `spawn_wake.row_bind`, not Composer house generate. Headless code successors use `team_dispatch(seat=cursor-sdk)`; life uses `cursor_request`. **`cdp/opus-5.5`** for judgment binds. IDE hop takes friction NOW as `--row`; headless gets `row=`.

## Stops (designed, not "continue?")

| Stop | Trigger | Action |
|---|---|---|
| `HOUSE_CLOSE` | operator says close the house | LOAD `runbook:house-close` and execute. Not session-close. |
| `OPERATOR_GATE` | credentials · irreversible · money · outbound/calendar committing others · fleet-wide restart — **only when armed** via `--operator-gate <row>` (`source=operator`) | CHECKPOINT + page + park **that row**. Clear with `--operator-gate clear`. Seat prose / `now_row` containing `OPERATOR_GATE` ≠ gate |
| `HOLD_MERGE` | operator **explicitly** asked to hold merge | leave lane branch, row `LAND OWED`, page; ¬ hop. Silence / missing `auto_land` ≠ hold |
| `CONSULT_PENDING` | independent check disagrees | row pinned, continue other rows |
| `REPEATED_FAILURE` | same fix failed twice | friction + house row; if gate, swap NOW and play. Page only when gate is `OPERATOR_GATE` |
| `SPEND_CAP` | **only when** positive `policy.max_dispatches_per_night` set — then dispatch count ≥ cap or dispatch > 2h | pause new dispatches, page |
| `CONTEXT_BUDGET` | digest `budget.stop_class` — `source=giw.sdk_stream` (headless) or `source=ide.transcript` (attended; vs `policy.ide_window_tokens`, default 256k). `checkpoint_due` at 60% | attended + work left: CHECKPOINT → **`liaison-ide-hop.py`**; empty NOW: STAY/PARK; `--go-under` only overnight/departure; headless `sdk:`: CHECKPOINT → release, ticker spawns |

Page: `curl -sS --unix-socket /tmp/universal-protocol/email-bridge.sock -H 'Content-Type: application/json' -d '{"subject":"liaison R — <stop>","body":"<one paragraph + tip CP turn>","tag":"liaison"}' http://localhost/pager/notify`.

## Provenance

Every DONE/live/landed word in scoreboard quotes a payload (`provenance-discipline`). Digest `budget` is estimate with basis; seat's own usage reading wins. Bus turns < 2 KB; long material → `sidecar_content`. Identity: `cursor`; never a personal name.

## L3 sidecar

Unique non-core (specimens, historical narrative) relocated to:
`cortex://notes/system/references/liaison-l3-sidecar.md`.
