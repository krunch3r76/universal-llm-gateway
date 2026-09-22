# /liaison <root> [attended|autonomous] [--interval S]

Seat this tab as the house liaison on continuity root `<root>` and arm the tick loop.

1. **Load** `Use the liaison skill` (`cursor-plugins/ulg-ecosystem/skills/liaison/SKILL.md`) and `checkpoint-discipline`.
2. **Orient** — `agent_bus_read(thread_get, thread=<root>)` → read the tip CHECKPOINT (latest turn with
   `TYPE: CHECKPOINT`), open its scoreboard URI via `fs(sandbox="cortex", op="read")`. Do not read the thread
   linearly. If the operator wrote `resume <root>` first, the fence already poured — skip.
3. **Register** — `~/.venvs/universal/bin/python scripts/liaison-tick.py --root <root> --register <register> --once`
   and quote `budget`, `lanes`, `attention`. Default register: `attended`.
4. **Wakes — skip `CreateGoal`.** Native goals have no interval and inject continuation wakes that
   are not the house ticker. Do not arm a turn-by-turn watcher on in-flight lanes. A finish signal
   (`closeout turn=` / `consult complete`) is one harvest. Backup = `--loop` heartbeat **1200s
   (20 min)** only while a finish watcher is already live or a row is playable. `--interval S`
   overrides that heartbeat only.
5. **Arm the loop** (monitored shell, `block_until_ms: 0`, `notify_on_output` pattern `^AGENT_LOOP_TICK_liaison`,
   reason `liaison <root> tick`, debounce 15000). Any tab model may seat the liaison (skill § Seat model).
   This tab becomes the one liaison seat: the loop claims the seat lock as `ide:<transcript_id>` — resolve it
   with `scripts/liaison-ide-hop.py --find-transcript` on the tab's **first user message**. That is
   `resume <root>` when the operator typed resume first (this tab's specimen: `931b214d…`);
   `"/liaison <root>"` only when that slash line is the first turn — bare `"/liaison"` can resolve a
   **foreign** tab (specimen `866948bb…`).
   `"loop": "refused"` with `reason=held_preempt_requested` ⇒ a headless successor holds it and will park
   within one poll — re-arm after ~60 s; `reason=held` ⇒ another **attended tab** holds the seat — stay a
   worker tab (own legs and turns; no loop, no scoreboard Rows fold, no CHECKPOINT on the root) unless the
   operator's word moves the seat here: a fresh-tab `resume <root>` (other workstation) or "take over" ⇒ add
   `--take-over` to the command below, then re-arm after the live loop releases (~60 s):

   ```bash
   cd /mnt/torus/projects/universal-llm-gateway && ~/.venvs/universal/bin/python scripts/liaison-tick.py \
     --root <root> --register <register> --holder ide:<transcript_id> --loop --poll 60 --heartbeat ${INTERVAL:-1200}
   ```

   Title the shell `Loop liaison <root>: tick`. Record the PID in the scoreboard `## Loop` row.
   Digest consumers read the **last** stdout line of `--once` (sitecustomize prints validation lines first).
6. **First tick now** — run § Tick protocol on the `--once` digest from step 3 so the first server tick is
   not cold. End the turn; wakes arrive as `AGENT_LOOP_TICK_liaison` notifications.

Stop (loop kill / park, **no hop**): kill the loop PID, post CHECKPOINT. `UpdateGoal(complete)` only if
a leftover native goal is still injecting wakes, or the house objective is actually met.

Hop (`liaison-ide-hop.py` `ok`): harness retires this tab's `--loop`, watcher **pollers**,
`watch-supervise` tails, and `ide:` lock (`retire_departing_tab`); snapshots
`tmp/watchers/<label>.argv.json` before the poller SIGTERM. JSON + stderr emit
`LIAISON_HOP_TAB_GOAL_RELEASE`. **Same turn, before `RETIRED →`:** if a native goal is
active, `CallDynamicTool(cursor, UpdateGoal, {"status":"complete"})` (legacy
continuation-wake). Successor **rebuilds** start+tail from argv.json +
`--loop --heartbeat 1200`; skip `CreateGoal`. Answer `RETIRED → <id>`; never harvest.
