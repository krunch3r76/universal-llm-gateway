---
name: liaison
description: On a Cursor IDE tab seated as the house liaison (attended or autonomous register) — tick protocol on the liaison digest, dispatch ladder by cost, designed stops, checkpoint + hop discipline, register flip, peer-house master conflict. Load on `/liaison`, `resume <liaison root>`, or "run the house while I'm away".
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
| **attended** | proceed on every fork with the best-known default (plan / packet target ≻ ladder default); the operator steers post-hoc — never park on a spend or model re-confirm the plan already named (operator 2026-09-11 20:00 PT: "the orchestrator runs without gating on me — I steer as needed") | on designed stops | **land on green** (AC met, merge the lane). `HOLD_MERGE` only if the operator **explicitly asked to hold** — ¬ operator ack as land gate (operator 2026-09-19, a:35726 SoT · a:35731) |
| **autonomous** | bind every fork itself (steelman → bind → act); operator absent | only on `OPERATOR_GATE` / `SPEND_CAP` / `REPEATED_FAILURE` / `CONTEXT_BUDGET` | **land on green** (AC met, merge the lane). `HOLD_MERGE` only if the operator **explicitly asked to hold** — ¬ `auto_land` flag, ¬ missing chat ack (operator 2026-09-12 07:33 PT) |

Flip: `scripts/liaison-tick.py --root R --register autonomous|attended` (state-file field). The operator's
return ("I'm back") flips to attended; the operator's departure ("running overnight") flips to autonomous.

**`go under`** (aliases `run headless` · `hand off to cursor-sdk` · `go under <root>`; operator 2026-09-12
21:50 PT) = hand this house to the gear-3 ticker and leave the tab. **One verb, owned by the harness**
(`libs/bus_watch/go_under.py`; 10534 2026-09-13 06:11Z parked at 99.6 % with the four-step prose unrun):
1. segment CHECKPOINT (residue ≤ 800) — the seat authors it; `--mark-checkpoint` may ride on step 2;
2. `liaison-tick.py --root R --go-under --holder ide:<transcript_id>` — flips `register=autonomous`, sets
   `policy.ready=true` (the ticker becomes the driver; an IDE-hop chain keeps `ready=false`), releases the `ide:` seat,
   SIGTERMs this root's attended `--loop`s, starts a ticker if none holds `liaison-ticker-<R>.lock`, and
   arms **one** handoff wake (`state.handoff.seq`) so the next poll spawns the successor whether or not any
   lane is unread. Refuses only `successor_model_unset` (`--set successor_model=<slug>` first);
3. paste the printed `UNDER → …` line and end the turn. No hop, no dispatch after the verb.
**When:** operator departure ("running overnight" · "hand off to ticker") — **not** attended
`CONTEXT_BUDGET` mid-arc (that path is § Tick step 7 **ide-hop**). PARK is not a step while `NOW` or
unread lanes remain.
Overnight leftover is **hold | play | sit** (`spawn_wake.play_classify`), not “ticker always
wakes a liaison.” Hold = a live conductor already owns the addressed `todo:{slug}` (doorbell
only). Play = `now_row` names `todo:{slug}` and no live owner → admit that conductor
(`source_ref=todo:{slug}`, `lane=B`); GIW `ROW_HOP` is the inner rotation. Sit = house
attention and no addressed todo → today's headless liaison. Default `--go-under` is
play-aware; `--go-under --sit` is the old 10534 chain on purpose. `successor_contract=conductor`
on the house generate is not the play path.

**Come up from under** (operator "I'm back" / "come up" / "stop the ticker" / "take the house" —
**not** `resume R`, which is liaison↔operator check-in and leaves the ticker driving): (1) SIGTERM this root's
`liaison-tick.py --loop --spawn-on-wake` ticker; (2) `liaison-tick.py --root R --register attended
--set ready=false`; (3) drop any `induction_binds` row that forbids IDE hop; (4) then `resume R` may
`--take-over` — the `ide:` claim preempts any lingering `sdk:` holder. One driver: IDE-hop chain ⇒
`ready=false`; ticker ⇒ `--go-under` only when the operator names overnight/departure. Check-in
`resume R` while UNDER must not run this sequence.

**Operator guide (living).** "How do I use …" / "what changed" / a new ruling or phase move ⇒ **LOAD AND
EXECUTE** `runbook:liaison-operator-guide` (`cortex://notes/runbooks/liaison-operator-guide.md`) — `cite(runbook)
⇒ load(cited_section*) ∧ execute(seat-bound_steps)` this turn: read the root's `…/<root>-operator-guide.md`,
answer from it in plain language, patch it the same turn. Today's liaison pattern: the attended IDE tab is the
override; gear-3 headless successor is the fallback. The GIW doorbell schedule
(`scripts/liaison-schedule-wake.py`) is retired and refuses. The ticker is the only house wake.

## Tick protocol (one wake = one digest)

Wake source: `AGENT_LOOP_TICK_liaison <json>` from the monitored background shell
`scripts/liaison-tick.py --root R --loop` (armed by `/liaison`). The JSON is the whole read; do **not**
fetch the bus to "double check". Read `digest.induction` **first** — the planted address for this wake
(`WAKE <root>` · `Event:` stops/watchers/lanes · `NOW:` · `Loaded already (do not re-read)` · `Standing:` ·
one step; ≤ 700 bytes, `libs/bus_watch/induction.py`, operator bind 10479 #82/#118/#120). It is also the
first key of every `DIGEST <root>` bus turn, so a woken claude.ai liaison reads the same address. Bind NOW
for the next wake with `liaison-tick.py --root R --set now_row="<row>"`; standing operator binds go in
`--set induction_binds='["hopper paused (10479#210)"]'`, already-loaded skills in `induction_loaded`.
Keystroke paste of this block into the live tab (same uinput path as the hop, no Ctrl+n) is the planned IDE
transport; `cse_session(op=followup)` is the planned claude.ai transport — both are **planned / not yet wired**.

**Wakes (operator 2026-09-21):** Native `CreateGoal` is a Goals-panel label plus an unthrottled
continuation injector (no interval field). It is **not** the house ticker. Skip `CreateGoal` on
`/liaison`, resume, and hop pickup unless the operator asks for a panel pin. If a leftover goal is
still active on this tab at hop `ok` or arc close ⇒ `UpdateGoal(status=complete)` so Cursor stops
injecting (11912: goal kept waking a retired tab 5m45s). Do not mint a successor goal.

House wakes, cheapest first:
1. Finish signals (`closeout turn=` / `consult complete`) — one harvest, not each conductor turn. A conductor tail is `watch-supervise.sh tail --until-finish`: it returns on `closeout turn=` or `stall-pop:` and drops ordinary turns. `stall-pop:` on an already-relayed closeout is not a wake (§ Seat stays)
2. `liaison-tick.py --loop` only while a finish watcher is already live or a row is playable. A tick that only repeats this tab's CHECKPOINT is not a wake
3. Heartbeat **1200s (20 min)** — backup only while (2) holds: re-arm dead tails and check watcher health. No playable row and no live watcher ⇒ SIGTERM the loop. The house stays

## Seat stays (operator 2026-09-22)

Binds. Later prose that conflicts with them loses.

1. **cursor-sdk directly.** This seat holds `team_dispatch` and the checkout. Repo write, script, and upload go to `team_dispatch(op=generate, seat=cursor-sdk, contract=implement|pure-mechanical|none, lane=B)`. `cursor_request` is life-only. Life implement uses it because life has no `team_dispatch`. On code the AutoJob admit door is `agent_bus(tool="request")` (conductor commission, mission negotiation, unattended enqueue), not this seat's implement path.
2. **Do not close the house on a dead tail or an empty wake.** A conductor whose closeout is already relayed, while its tail still prints `stall-pop:`, is finished: `watch-supervise.sh stop --label <label>`. That tail is not a watcher, not a hop, and not a reason to end the seat. No playable next row and no live watcher ⇒ do not arm `--loop`, and SIGTERM this root's `--loop` if it is running. A tick that only repeats this tab's CHECKPOINT is not an instruction to play. The house stays. The wake stops.
3. **File the friction, add the house row, play a gate.** A row that cannot proceed: `friction()` on the owner the same turn, and a row on the continuity card `## Rows`. A **gate** is a friction the current row cannot pass until it is resolved. A gate swaps into NOW (`--set now_row=` `Friction a:<n> …`). The blocked row becomes the next row. Play the gate the same turn on the ladder in (1). Do not STAY on the blocked row. Do not page unless the gate is an armed `OPERATOR_GATE`.
4. **When a row has been played, add the next one.** A finished row does not empty the house. Same turn, add every next deliverable that is not gated on another row, to `## Rows` and `--set now_row=`, then play it. Adding that row clears `now_row=quiet` and re-arms `--loop --heartbeat 1200` if the loop is down. Ungated rows may run at the same time. A row that waits on some other row having been played first stays off NOW until that condition is true. `now_row=quiet` and an empty NOW are legal only when the house program has no open deliverable.
5. **No turn-by-turn watch.** `conductor_live ⇒ ¬arm(turn_watcher)`. A conductor posts many turns before it finishes. Review the closeout, or a designed stop (`CONSULT_PENDING`, stale heartbeat, empty seat). Turn-by-turn watch only when the operator names that run. `closeout turn=` is one harvest, not a turn stream. Rebuilding start+tail for every in-flight lane on wake is not the default.

| Bad | Good |
|---|---|
| Arm a tail and read each conductor turn | `tail --until-finish` — returns on closeout or stall-pop |
| Rebuild start+tail for every in-flight lane at wake | Leave those turns on the bus until the finish |

## Tick steps

0. **Wake check** — do not arm a turn-by-turn watcher on in-flight lanes (§ Seat stays 5). Arm `--loop --heartbeat 1200` only while a finish watcher is already live or a row is playable. Skip `CreateGoal`. `/liaison` command step 4 is the same bind.
1. **Quiet tick** — `changed_since_last_tick=false ∧ attention=[] ∧ ¬checkpoint_due` ⇒ one line, end turn.
2. **Harvest** — ∀ lane ∈ `attention`: `terminal=true` ⇒ `agent_bus_read(get, thread, "latest")` (one turn);
   read the CLOSEOUT/SCORE_RESURFACE, quote its evidence, `mark_read`. Non-terminal unread ⇒ latest turn only.
   `watchers_complete_unrelayed` ⇒ read the state file's `thread`, harvest, then
   `liaison-tick.py --root R --mark-relayed <basename>` (`path.name` only — e.g. `11924-foo.state.json`, not
   `tmp/watchers/11924-foo.state.json`; a prefixed path stays `watchers_complete_unrelayed`). `kind=friction`
   items and `digest.frictions` ⇒ § Friction
   score rows (one `assertion_get` deepen at most; the row already carries category · note · state).
3. **Fold** — update the scoreboard (`fs md_replace` on the cortex scoreboard URI in the tip CHECKPOINT): row
   status ← observed (quote sha / pytest line / execution_id). Landed ≠ live: a slice whose paths serve a
   running process needs `manage(sync_restart)` — **LOAD** `restart-drain-discipline` (`needed(restart) ⇒
   fire(restart)`; busy never skips).
4. **Decide** — **Archived as G-row picker** — liaison computes Address. Spawn or re-admit a conductor only for Address rows 3 and 5. Row 6 DISPATCH (`mechanical`, or `implement_ready` and stamped) goes to Composer and does not spawn a conductor; conductor owns scoreboard NOW under `work_key=todo:{slug}`. With no seat bind the induction's NOW **is** the newest undispositioned friction (§ Friction score rows). Gear 3 (`--spawn-on-wake`) pins the attention-tier NOW into `policy.now_row` and releases it when the lane is spent (`now_row_bind` provenance in tick.json). `--set now_row=…` always wins while satisfied; `--set now_row=quiet` holds the field. `reasoning-posture`: pin the question, bind, one determinate step.
5. **Dispatch** — by the ladder below; every dispatch gets a lane on the root (`dispatch_thread_id=R`) and an
   **in-session watcher**: **LOAD AND EXECUTE** `runbook:bus-consult-watcher` (all legs 1–3 — arm, wake,
   relay — before turn end; start-only / skipped tail / hold-turn = mis-arm). That watcher relays one
   consult reply or closeout. It is not a feed of the conductor's intermediate turns (§ Seat stays 5).
   cursor-sdk closeout: **exactly one** of `--dispatch-id` or `--execution-id` (script exits if both; prefer
   `--dispatch-id`). CDP consult: `--execution-id` from admit. CDP producers: `cdp.generate.proof` carries
   `archive_uri`; on `delivery_failed`
   harvest the archive, not the bus.
6. **Checkpoint** — `budget.checkpoint_due` ⇒ segment CHECKPOINT on R (`supersedes_turn=<tip>`, Residue ≤ 800
   chars: Settled · Live · Next · scoreboard sha) then `liaison-tick.py --root R --mark-checkpoint`.
7. **Hop** — hop only when autonomous follow-up remains (operator 2026-09-12 07:26 PT;
   supersedes hop-after-harvest). The hop opener is **paste-induction** (10479#82): a
   user-turn that steers attention, not a second copy of the skill. `¬` call that
   **hypnosis** until the attention-induction paradigm is defined (10158 / a:33210);
   hop FOL restatement is not it. **Skill transfer:** the opener names every skill
   the successor must load before it acts. The minimum is this skill — the line
   `LOAD the liaison skill (do not skim)`. Add any other skill the row needs in
   that same opener. A hop message that omits the liaison load is incomplete.
   The successor loads those skills; this tab's context does not carry over.
   Qualifies: live watcher tails — **¬** the ticker's
   closeout watcher on **your own** lane (`--exclude-lane <lane>`; waiting on
   yourself is not follow-up, and acting on it chains premium seats) · dispatchable NOW ·
   `CONTEXT_BUDGET` with remaining work. **STAY** (write `STAY: <reason>`): empty NOW ·
   quiet tick · hop script refuse · operator park · explicit hold-merge. Autonomous
   LAND OWED without an explicit hold is **land**, not a stay-for-ack. Harvest-complete
   with nothing next is a stay. **Not a close and not a wake:** a conductor whose
   closeout is already relayed, while its tail still prints `stall-pop:`, is finished —
   `watch-supervise.sh stop --label <label>`. That tail does not keep the seat. No playable
   next row and no live watcher ⇒ do not arm `--loop`, and SIGTERM this root's `--loop`
   if it is running. A tick that only repeats this tab's CHECKPOINT is not an instruction
   to play. The house stays; the wake stops. `scripts/liaison-ide-hop.py` refuses `no_autonomous_followup`
   unless `--force`. **Hop ordering (10479 hop-channel):** qualify → **`seal_hop_window`
   (`channel=hop`)** → message → keystroke → landed. Pass **`--transcript-id <departing tab
   uuid>`** (required; not inferred — `find_transcript_id` resolves the wrong tab). A hop
   that cannot seal does not keystroke. In-flight watcher ⇒ harvest in this tab; hop it only
   when this tab cannot continue. Hops do **not** stop because `lock.hops` equals 8; this-root
   cap is `policy.max_hops_per_night` on `liaison-fable-<root>.lock`. Attended hop is the
   last action **only when hopping** (`policy.gui_host` required; `ok` = landed transcript).
   Autonomous hop-qualifying CP: kill the loop → `--release` → one successor. One tab live.
   **`ok` retires this tab (structural — do not rely on successor re-arm to quiet this tab):**
   1. Harness (`retire_departing_tab`, `liaison-ide-hop.py` after `ok`): SIGTERM this root's
      attended `--loop`s; snapshot each poller's argv to `tmp/watchers/<label>.argv.json`
      then SIGTERM the poller (`watch-supervise.sh start` / pid file); SIGTERM
      `watch-supervise.sh tail --label` for every label that belongs to the root;
      `--release` the `ide:<transcript_id>` seat. `--forever` debug tails stay.
   2. Seat (Cursor-native; harness cannot): **`UpdateGoal(status=complete)`** only if a
      leftover native goal is still injecting wakes — ¬ mint a successor goal. Same turn,
      after `liaison-ide-hop.py` prints `ok` (stderr carries `LIAISON_HOP_TAB_GOAL_RELEASE`).
      Tool: `CallDynamicTool(namespace="cursor", toolName="UpdateGoal", arguments={"status":"complete"})`.
      Successor **rebuilds** ARM labels (`stop` leftover → `start -- <argv.json>` → `tail`)
      and arms `--loop --heartbeat 1200` only while a watcher is live or a row is playable. Skip `CreateGoal`.
      Load `liaison-hop-retire_ulg` when unsure.
   3. Answer `RETIRED → <landed_transcript_id>` in one line and never harvest
      (10479 hops 1→2, 2026-09-13 03:04Z: two tabs harvested 10584, CP #198 + #201, MCP
      recycled under the successor's read; 11912 2026-09-21: goal + loop survived land).
8. **Stop classes** — `CONTEXT_BUDGET` on an attended tab with remaining work (live watcher ·
   dispatchable NOW): CHECKPOINT → **`liaison-ide-hop.py`** (§ Dispatch ladder Successor row) — fresh
   tab ~40k orient; **`--go-under` is wrong here** (ticker path is slower/heavier). `CONTEXT_BUDGET`
   with empty NOW and nothing unread: CHECKPOINT → STAY or PARK. **`--go-under`** only on operator
   overnight/departure (§ Registers). Headless `sdk:` holder at budget: CHECKPOINT → release, ticker
   spawns. Other designed stops: § Stops.

## Seat model (operator 2026-09-11 20:37 PT: "Fable on IDE may not always be practical")

**MCP surfaces (cursor-auto admit):** the attended liaison tab uses **`/mcp/code`**. Code no longer mounts
the narrow `cursor_request` / `operator_request` tools — repo writes and mechanical work enqueue via
**`agent_bus(tool="request", to=cursor, …)`** (same GIW admit path). Life Cowork (`/mcp/life`) still mounts
`cursor_request` for implement/directive lanes because life has no `team_dispatch` generate door.

The liaison mechanics are **model-agnostic** — nothing in the tick loop, digest, resume fence, CHECKPOINT,
hop, or lock reads the tab model. Pick the IDE tab model in the picker; the discipline that changes is the
seat's own, not the house's:

| Tab model class | Serves as liaison? | What must change |
|---|---|---|
| Opus-class (`claude-opus-5`, Fable when affordable) | yes — binds judgment forks inline | nothing |
| Below Opus (Grok 4.7, Sonnet, Composer, GPT-5.6) | yes for harvest → fold → dispatch → CP → hop | presence-discipline P1–P4 are **explicit obligations**; every judgment bind goes to `cdp/opus-5` first (§ Reasoning recon) unless the criterion is already closed on a **named assertion + Explore locus** — then name the CDP skip as the rejected alternative and bind |

Fable on **claude.ai** (`cdp/fable`) is still a product seat; in the IDE it is optional. Gear `1-fable-mvp` and the lock filename remain Fable-named. **Cursor Fable credit window is closed** (operator 2026-09-12 07:48 PT) — ¬ escalate to `cursor/claude-fable-5-1`.

Woken claude.ai liaisons read the house through the latest `DIGEST <root>` turn (`agent_bus_read(fetch, thread=<root>, last=3, compact=true)`, subject starts with `DIGEST`) published by the host tick loop when `policy.post_digest` is true (gear 3 default).

## Single-liaison-seat cap (operator 2026-09-10; generalized from "single-Fable")

`liaison_seats(IDE ∪ cursor-sdk) ≤ 1` per root, enforced by `tmp/watchers/liaison-fable-<root>.lock` via
`scripts/liaison-tick.py`: `--claim --holder ide:<transcript_id>|sdk:<dispatch_id> [--hop]` ·
`--release --holder …`. When arming `/liaison` or claiming the seat, resolve `ide:<transcript_id>` with
`liaison-ide-hop.py --find-transcript` from the tab's **first user message**: `resume <root>` when that was
first; `"/liaison <root>"` only when the slash line is first. Bare `"/liaison"` can hit a **foreign** tab.
**Holder identity is the tab, not the root** — two attended tabs that both claim
`ide:<root>` silently co-hold (same string ⇒ re-claim succeeds); with `ide:<transcript_id>` the second tab is
`refused` (`reason=held`) and runs as a **worker tab**: its own legs and turns, no loop, no Rows fold, no
CHECKPOINT on the root. Taking the seat from a live attended holder needs the operator's word: `resume <root>`
typed in a fresh tab **is** that word (multi-workstation alternate) ⇒ claim with `--take-over` — the live loop
sees `preempt_by`, exits, releases; retry within one poll (commit edb46bab). A `/liaison` without the word is
`held` and stays a worker tab. Headless `sdk:` holders are preempted by any `ide:` claim. **Stopping a loop:**
`pkill -f 'liaison-tick[.]py --root <R> --loop'` then `pgrep -fc` = 0 — the IDE Shell PID is the pipeline wrapper,
not the python; killing it orphans the loop (specimen 04:11Z: two loops ping-ponged DIGEST turns every 30 s).
The `[.]` matters: `pkill -f` matches the *whole* command line of every process, including the bash wrapper
running your own `pkill` — a literal pattern kills the seat's shell first and the rest of the command never runs
(specimen hop 13, 04:44Z: silent empty output; the loop did die and released the lock via `holder_pid`).
The lock records `holder_pid`; only the claiming process (or `--release`, the operator override) can release, so
an orphan exiting no longer drops the live lease. Model seats refresh the
declared lease (`expires_at`) on each `--once` tick (`tick_seq` / `turns_seen`); `seat_lock_free` means
`holder is None ∨ now > expires_at`. The gear-3 **ticker** holds `liaison-ticker-<root>.lock` (`ticker:<root>`) —
it never takes the seat mutex and cannot write `preempt_by`. The legacy fleet file
`liaison-ticker.lock` is unread leftover (same posture as `liaison-fable.lock`). Attended `--loop` (gear 1/2) still claims the seat
lock; gear 3 uses `--loop --spawn-on-wake` instead. **Idle forfeit** (`spawn_pending.idle_ide_forfeit`): under
`register=autonomous` an `ide:<transcript_id>` holder whose tab transcript has been silent longer than
`policy.ide_idle_forfeit_s` (default 1200 s) is a stopped liaison — the ticker releases that lease and spawns
(10479 2026-09-13: `ide:ccd52168…` claimed 06:13Z, `tick_seq=0`, ticker held 90 min). Attended register never forfeits. `lock.hops` is keyed by `night_id` on
**this root's** `liaison-fable-<root>.lock` (`hop_cap.lock_hops_scope=root`); the legacy fleet file
`liaison-fable.lock` is unread leftover. **This-root cap** is `policy.max_hops_per_night`. Per-root
`lock.hops==8` is not a designed stop.

## Peer-house conflicts (operator 2026-09-12 07:48 PT)

`∀ roots sharing master: isolate(paths ∧ locks) ≻ collide`. One liaison per root; several roots may still
touch the same files. `¬ discard(peer_work)` — a blocked merge is reconcile, not hop-away or copy-land.
**Use the `git-posture` skill § Land** (merge the lane; keep both hunks).

| Conflict | Action |
|---|---|
| Keepable overlap (both hunks valid) | `git merge`; keep both; verify ACs; ¬ hop while unreconciled |
| Judgment (which hunk is right) | `cdp/opus-5` → 2nd pool (other CDP identity ∨ `cursor/claude-opus-5`) → opus-class bind. ¬ `cursor/claude-fable-5-1` |
| Sensitive (`OPERATOR_GATE` class · discard-risk · identity) | only after that ladder fails: page human |

`¬ page(human)` for an ordinary merge conflict. `cdp/fable` only when the operator names it.

## Headless successor (resume-fence pull)

The hop target is a **message dispatch**, not a packet file. Sit leftover on a
**friction/disposition score row** is not the house successor: the ticker spawns a
**row-bind hop** (default `cursor/grok-4.7` Standard + high effort via
`policy.row_bind_model` / `row_bind_model_knobs`; override with `cdp/opus-5` or
`cdp/fable` when needed) to bind `ROW_CLASS: low|trio`. That seat does **not** edit the
repo (bind then STOP; remaining hops are ticker-fired — § Friction score rows).

```
# friction NOW / sit leftover — ticker, not the house successor
team_dispatch(
  op=generate,
  seat=cursor-sdk,
  lane="B",
  model=cursor/grok-4.7,     # policy.row_bind_model; model_knobs effort=high fast=false
  contract=none,
  prompt=<row-bind wake>,    # BIND ROW_CLASS: low|trio before nested dispatch
  dispatch_thread_id=<R>,
  work_key=row-bind:<fid>:night-<night>,
)
```

Other sit wakes (checkpoint / hop, no friction row) stay `contract=none` `lane="B"`
`model=<policy.successor_model>`:

```
team_dispatch(
  op=generate,
  seat=cursor-sdk,
  contract=none,
  lane="B",  # sit/house generate: Lane A is 1 write-lease slot; do not queue overnight successors behind shared-master grok. Bind-only cortex hops remain the named A exception.
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
this body on: an unread **live** lane · a finished **work** lane's closeout, once per turn count
(`state.served_closeouts`; a successor's own closeout never wakes the next — `caller=liaison-ticker` /
`state.successor_threads`) · a `--go-under` handoff, once (`handoff.seq`) · `checkpoint_due`, once per CP epoch ·
an undispositioned friction on a charter-owned service, once per assertion id (`state.friction_rows_seen`, ≤
`policy.friction_dispatch_cap` per night; § Friction score rows) — sit leftover on forcing friction binds CDP `ROW_CLASS` first; remaining hops are **ticker-fired**, not CDP-implemented ·
a fresh `CONTEXT_BUDGET` from the `sdk:` holder's own stream. Hold reasons are the per-clause booleans on the
ticker's stdout line (`tmp/watchers/liaison-ticker-<R>.log` when started by `--go-under`). The successor claims
the lock with `--hop`, runs ≤ 5 ticks / 60 min, checkpoints, releases, spawns the next. Composer implement
dispatches (`contract=implement`, omit `model=`) run **alongside** — they are not Fable seats.

## Dispatch ladder (cost ↓, cycle time ↓)

`attended IDE ∧ team_dispatch ∧ checkout ⇒ implement = team_dispatch(op=generate, seat=cursor-sdk, contract=implement|pure-mechanical, lane=B)`. Code AutoJob admit is `agent_bus(tool="request")`. Life implement uses `cursor_request` because life has no `team_dispatch`.

| Work | Executor | Bind / review |
|---|---|---|
| Read / recon / ≥3 files | `Task(subagent_type="explore")` in-tab | none |
| Trivial / local edit (<20 lines, no served path) | in-seat (Opus-class only by default; the successor **dispatches** instead) | none — commit path-explicit same turn |
| Mechanical implement with dense spec (`files_expected` + ACs) | `team_dispatch(op=generate, seat=cursor-sdk, contract=implement\|pure-mechanical, lane="B", source_ref=todo:…, packet_path=…, dispatch_thread_id=R)` | none — Fable-densified packets skip skeptic |
| Repo write when this seat lacks `team_dispatch` or the checkout (life) | **`cursor_request(contract=implement)`** — never STAY, never needs-attended | Attended IDE with both: skill `liaison-cursor` (`team_dispatch` cursor-sdk). ¬ this row |
| Design / judgment fork (discriminator, architecture, bind table) | **`team_dispatch(model=cdp/fable)`** — decider of last resort; never STAY | one round; disagreement ⇒ CONSULT_PENDING |
| Judgment fork (independent check) | **this seat** binds inline when Opus-class; below Opus, § Reasoning recon first (`cdp/opus-5` wide read → bind on the compact) | independent check only if invariant-touching ∨ cross-agent ∨ recurrence ≥2 |
| Independent check / CDP judgment | **`team_dispatch(model=cdp/opus-5)`** — announce `CDP: <trigger> — <why>`; opus hops (`agent_bus hop`) to stay lean | one round; disagreement ⇒ `CONSULT_PENDING` stop |
| Long-context reasoning inside a work tab | `cursor/claude-opus-5` (Cursor Fable credit window closed) | **`cdp/fable`** only when the operator names it — never `cursor/claude-fable-5{,-1}` |
| Successor (this tab must end) | attended: CHECKPOINT + `scripts/liaison-ide-hop.py --root R --row "<NOW>" --transcript-id <this tab uuid>` (seals `channel=hop` then keystroke hop, fresh tab, ~40k-token orient vs 12–31M per headless hop); autonomous: § Headless successor (resume-fence pull) — the successor pulls the tip via `dispatch(tool="continuity")`; `cursor_request` is not a successor path (enqueues cursor-auto) | — |

**Reasoning recon** (operator-endorsed 2026-09-10 22:39 PT, observed on 10479#18): before a judgment bind, the
liaison sends the *wide read* to `cdp/opus-5` (`CDP: <trigger> — <why>`, tape cell / CP residue + the decision as
context) and binds on the returned compact. The premium seat never spends its window on breadth; it
spends it on the bind. This is the economy pattern, not an exception to the ladder.

Model walls (operator 2026-09-10; Fable-credit close 2026-09-12): default CDP seat is `cdp/opus-5`; `cdp/fable`
only when the operator names it. Never `anthropic/*` API. `cursor/claude-fable-5-1` is not an escalation
target (credit window closed) and never a `team_dispatch model=`. Same fix failed twice ⇒ stop, `REPEATED_FAILURE`.

## Economy gears (operator 2026-09-10: the Fable 1M Max spend window is finite)

The successor model is **policy, never a constant**. `scripts/liaison-tick.py --root R --set gear=<name>` (or any
`--set key=value`) writes the policy; every digest carries `policy`; successors copy `policy.successor_model`.

| Gear | Successor | Cadence | When |
|---|---|---|---|
| `1-fable-mvp` | `cursor/grok-4.7` (`effort=high`, `fast=false`; judgment hop, CDP for design forks) | ≤ 5 ticks / 60 min / poll 600 s | **do not select for overnight** — use gear 3; Cursor Fable credit window closed (2026-09-12) |
| `2-opus-hops` | `cursor/claude-opus-5` (no cost intent); CDP checks stay `cdp/opus-5` | ≤ 6 ticks | next iteration; Fable only in the attended window |
| `3-wake-on-attention` | **`cursor/grok-4.7`** (`effort=high`, `fast=false`) from the gear preset. The first play admit passes this same `successor_model` and knobs; it does not omit `model=`. Preset/default spawn allowlists that model only — premium presets still blocked (10534); Composer is not allowlisted until a mechanical hop exists. Override anytime with **`--set successor_model=<slug>`**. Spawned on the wake sources in § Headless successor (live unread · work closeout once · handoff once · `checkpoint_due` once) | poll 120 s via `scripts/liaison-tick.py --loop --spawn-on-wake`; ticker holds `liaison-ticker-<root>.lock`, **not** the seat mutex | **armed only by explicit `ready`** (`--set ready=true` or `--go-under`; `ready_source=override`). The register never arms it: an IDE-hop chain runs `register=autonomous` with the ticker policy-only, and a register-armed ticker put a second driver on 10479 (2026-09-13). One driver per house: IDE chain ⇒ `ready=false`; ticker ⇒ `--go-under` |

Shift = one command; takes effect at the **next** hop (a running successor keeps the gear it read). A live
`--loop` absorbs `--set` / `--mark-*` edits from another shell on its next poll (`libs/bus_watch/tick_state.py`
`absorb_operator_edits`, logged as `operator_edit_absorbed`); before 2026-09-12 the loop's in-memory state
clobbered them within one poll — verify a steer by re-reading `--policy` after the next tick. `SPEND_CAP`
(`policy.max_dispatches_per_night`) is **off by default** (operator 2026-09-13: "remove dispatch cap
permanently") — the ceiling is opt-in, and non-positive or absent means no nightly limit. When an operator
sets a positive cap it is a designed stop, not a gear change — page, don't downshift silently. Never let a successor pick a model itself; a refused model is an INFO + stop, never a fallback.

## Objectives (autonomous queue)

1. Scoreboard rows not DONE. 2. `cortex(todo_candidates)` filtered `implement_ready=true ∧ density_triage=mechanical`.
3. Friction score rows (§ below — they arrive in the digest; no query). 4. Nothing ⇒ gardening: ruff on touched
dirs, stale watcher hygiene, scoreboard grooming — then lengthen the heartbeat (`--heartbeat 3600`), never busy-loop.

## Friction score rows (operator 2026-09-13, 10595: "proactively address frictions … a mutable score by a conductor")

`todo:liaison-friction-score-rows` · `libs/bus_watch/friction_rows.py`. Open `friction()` assertions on the
house's **charter-owned** services are score rows: they enter the digest, need a disposition, and leave when
closed on the assertion. Same driver as everything else — no second loop.

| Leg | Mechanic |
|---|---|
| Charter | `--set owned_services=…` feeds the digest. Empty ⇒ the digest stays empty. A friction this seat files is still a house row on `## Rows`; it does not wait for that set |
| Harvest | `digest.frictions` (≤ 12, newest first: `id=a:<n>` · `owner` · `category` · `note` · `state` · `forcing`) + `digest.friction_summary` (`open` · `forcing` · `promoted` · `dispatch_cap` · `dispatched_tonight` · `error`). Read path: Cortex UDS `assertions` on the owner, non-superseded bracketed claims, 100-row window; `[feature]` asks and `[resolved:…]` closure rows are not rows |
| NOW | no seat bind (`summary_row` / `policy.now_row` empty) ⇒ the newest `forcing` row **is** NOW. A seat bind outranks a non-gate friction; that row stays an `Event:`. **A gate swaps.** |
| Disposition | **Ticker first spawn = row-bind hop** (default `cursor/grok-4.7` Standard + high effort), not Composer house generate. Agent posts `ROW_CLASS: low\|trio` then STOP; ticker fires remaining hops (LOW = implement lane B + later apply-all review; TRIO sketch default `cdp/opus-5` when no `todo:{slug}`). `--mark-friction` still records `direct-first` / `todo-minted` / `declined`. In-seat ≤20-line `direct-first` only after `ROW_CLASS: low`, and does not skip review+apply+land. Record: `liaison-tick.py --root R --mark-friction a:<n>:<disposition>` (operator key `friction_dispositions`; a live loop absorbs it next poll) |
| Close-back | **on the assertion**: `cortex(tool="friction_close", assertion_id=<n>, resolution_kind=todo:<slug> \| wontfix \| commit:<sha>)` — `todo-minted` / `declined` the same turn; `direct-first` when the fix lands. Superseded ⇒ the row leaves on the next harvest. A `todo-minted`/`declined` row still open = `state=close_pending` — you forgot the close |
| Ticker | the newest forcing ∧ unlatched row is promoted into `attention` (`kind=friction`) — **one per tick**, none once `policy.friction_dispatch_cap` (default 3) spawns are latched tonight; a successful spawn latches it in `state.friction_rows_seen` — **one spawn per assertion id**, a re-opened friction carries a new id. Latched-but-open rows remain NOW for the seat that woke. **Sit leftover on a forcing friction:** row-bind posts `ROW_CLASS: low|trio` then **STOP** (bind-only); the ticker latches the class and fires LOW (`contract=implement` lane B) or TRIO (`build_play_dispatch_body` when `todo:{slug}` else `trio_sketch_model` consult) — never the Composer house successor on the bind leg. **Review harvest:** ticker applies ALL suggestions (SHOULD-FIX, nits, unused-code, adjacent) as one lane-B house generate (`contract=none` + apply-all message — ¬ `contract=implement`+`prompt`, wire-rejected) — ¬ page ¬ come-up ¬ park for the next resume; frozen `ready=false` and an attended `ide:` check-in do not hold that apply (`a:36093`) |
| REPEATED_FAILURE | a **second** `direct-first` mark on the same row ⇒ `state=repeated_failure`: NOW reads "consult, then todo-minted \| declined; never a third variant" (`cdp/opus-5` first below Opus) |

## Gate swap (operator 2026-09-22)

A row that cannot proceed is not a stop. Same turn: `friction()` on the owner, then a continuity-card row (`## Rows`). A **gate** is a friction the current row cannot pass until it is resolved. A gate **swaps** into NOW (`--set now_row=` the friction line, `Friction a:<n> …`). The blocked row becomes the next row. Play the gate the same turn: first leg on the dispatch ladder (`team_dispatch` `seat=cursor-sdk` from this seat). Do not STAY on the blocked row. Do not page.

Not owned: pipelines, seat locks. Sit friction spawn is `spawn_wake.row_bind` (default Grok row-bind), not Composer house generate. Headless code successors dispatch `team_dispatch(seat=cursor-sdk)` for repo writes; life uses `cursor_request`. **`cdp/opus-5`** for judgment binds (doorbell duty line + § Reasoning recon). The IDE hop takes the friction
NOW verbatim as `--row`; a headless successor gets it as `row=`.

## Stops (designed, not "continue?")

| Stop | Trigger | Action |
|---|---|---|
| `OPERATOR_GATE` | credentials · irreversible · money · outbound/calendar that commit other people · fleet-wide restart — **only when armed** via `liaison-tick.py --operator-gate <row>` (records `source=operator`, `as_of`). Seat prose or `now_row` containing `OPERATOR_GATE` is **not** a gate, and the successor-writable `--set` channel refuses the key | CHECKPOINT + page + park **that row** (other rows continue). Clear with `--operator-gate clear`. ¬ a Cowork permission dialog, ¬ a missing scheduled-task tool, ¬ A7 first-ticker-night — those take the named fallback (gear-3 ticker). Hop refuses only on operator-sourced gate state, not on gate text in `now_row` |
| `HOLD_MERGE` | operator **explicitly** asked to hold the merge | leave lane branch, row `LAND OWED`, page; ¬ hop. Silence / missing `auto_land` is **not** a hold |
| `CONSULT_PENDING` | independent check disagrees | row pinned, continue other rows |
| `REPEATED_FAILURE` | same fix failed twice | file the friction, add the house row, and if it is the gate swap it into NOW and play it. Page only when the gate is `OPERATOR_GATE` |
| `SPEND_CAP` | **only when an operator set a positive `policy.max_dispatches_per_night`** — off by default (2026-09-13). Then: dispatch count ≥ cap (read from the digest at tick time — never a number frozen in prose; R14 / a:33104) or a dispatch > 2h | pause new dispatches, page |
| `CONTEXT_BUDGET` | digest `budget.stop_class` — `source=giw.sdk_stream` for a headless holder, **`source=ide.transcript` for an attended tab** (the tab's own JSONL: prose bytes/4 + `ide_tokens_per_tool_call` per call vs `policy.ide_window_tokens`, default 256k; the estimate carries `transcript_id` · `tool_calls` · `holder_basis`). `checkpoint_due` flips at 60 % of the same window. Before 2026-09-13 an IDE tab had no stop at all (10534 tab: 852 tool calls, 11 h, compacted repeatedly, commission lost). Once the house is under (autonomous, no `ide:` holder) the retired tab's reading is dropped from the digest so a headless successor never parks on it | attended tab **with remaining work**: CHECKPOINT → **`liaison-ide-hop.py --root R --row "<NOW>"`** (induction plants the command); attended tab empty NOW: STAY/PARK; **`--go-under`** only operator overnight/departure (§ Registers); headless `sdk:`: CHECKPOINT → release, ticker spawns. Operator on a larger tab model: `--set ide_window_tokens=<n>` |

Page: `curl -sS --unix-socket /tmp/universal-protocol/email-bridge.sock -H 'Content-Type: application/json'
-d '{"subject":"liaison R — <stop>","body":"<one paragraph + tip CP turn>","tag":"liaison"}' http://localhost/pager/notify`.

## Provenance

Every DONE/live/landed word in the scoreboard quotes a payload (`provenance-discipline`). The digest's
`budget` is an estimate with its basis; the seat's own usage reading wins. Bus turns < 2 KB; long material →
`sidecar_content`. Identity: `cursor`; never a personal name.
