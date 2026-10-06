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
5. **Arm the loop — detach, then monitor the log** (a:38446). Do **not** run
   `liaison-tick.py --loop` as the agent Shell itself (`block_until_ms: 0` on that
   python process). SIGTERM/harvest exit of a Shell-owned loop is a
   `system_notification` wake that displaces LOCKED harvest SMS (specimen 15420
   after #115). Any tab model may seat the liaison (skill § Seat model).
   This tab becomes the one liaison seat: the loop claims the seat lock as
   `ide:<transcript_id>` — resolve it with `scripts/liaison-ide-hop.py
   --find-transcript` on the tab's **first user message**. That is `resume
   <root>` when the operator typed resume first (this tab's specimen:
   `931b214d…`); `"/liaison <root>"` only when that slash line is the first turn
   — bare `"/liaison"` can resolve a **foreign** tab (specimen `866948bb…`).
   `"loop": "refused"` / arm JSON `ok:false` with lock held ⇒ a headless
   successor holds it and will park within one poll — re-arm after ~60 s;
   `reason=held` ⇒ another **attended tab** holds the seat — stay a worker tab
   (own legs and turns; no loop, no scoreboard Rows fold, no CHECKPOINT on the
   root) unless the operator's word moves the seat here: a fresh-tab `resume
   <root>` (other workstation) or "take over" ⇒ add `--take-over` to the arm
   command, then re-arm after the live loop releases (~60 s):

   **(a) Spawn** — short Shell (foreground OK; must exit 0 immediately after arm):

   ```bash
   cd /mnt/torus/projects/universal-llm-gateway && \
     scripts/liaison-arm-loop.sh \
       --root <root> --register <register> --holder ide:<transcript_id> \
       --poll 60 --heartbeat ${INTERVAL:-1200}
   ```

   Quote the JSON last line (`ok`, `pid`, `log`, `monitor`). Record `pid` in the
   scoreboard `## Loop` row. On `ok:false`: do **not** start a monitor. Key the
   refuse on `reason` + `lock.holder` prefix — `held` with `ide:` ⇒ stay a worker
   tab (do not re-arm every 60 s); `held_preempt_requested` / `sdk:` ⇒ re-arm
   after ~60 s once the headless holder parks.

   **(b) Monitor** — only when arm JSON `ok:true`. Separate background Shell
   (`block_until_ms: 0`, `notify_on_output` pattern `^AGENT_LOOP_TICK_liaison`,
   reason `liaison <root> tick`, debounce 15000). Title `Loop liaison <root>:
   tick`. Use the `monitor` / `log` / `monitor_heartbeat` paths from the arm
   JSON (not a cwd-relative guess). If a monitor for this root is already live
   on this tab, re-arm runs **(a) only**:

   ```bash
   scripts/liaison-monitor-loop.sh --log <log from JSON> --heartbeat <monitor_heartbeat>
   ```

   That Shell is the liveness token: when it dies, the loop exits and releases
   the seat. Killing the python loop does **not** exit this Shell. Digest
   consumers still read the **last** stdout line of `--once`.
6. **First tick now** — run § Tick protocol on the `--once` digest from step 3 so the first server tick is
   not cold. End the turn; wakes arrive as `AGENT_LOOP_TICK_liaison` notifications.

Stop (loop kill / park, **no hop**): SIGTERM the loop `pid` from the arm JSON /
`tmp/watchers/liaison-loop-<root>.pid` (or
`pkill -f 'liaison-tick[.]py --root <R> --loop'`), then stop the monitor Shell
if this tab is leaving. Post CHECKPOINT. `UpdateGoal(complete)` only if a
leftover native goal is still injecting wakes, or the house objective is
actually met. If a loop-abort `system_notification` still arrives: restate the
LOCKED harvest body, or say nothing new if already relayed — never a
loop-status-only reply (a:38446).

Hop (`liaison-ide-hop.py` `ok`): harness retires this tab's `--loop`, `watch-supervise`
tails, and `ide:` lock (`retire_departing_tab`); house pollers survive. JSON + stderr emit
`LIAISON_HOP_TAB_GOAL_RELEASE`. **Same turn, before `RETIRED →`:** if a native goal is
active, `CallDynamicTool(cursor, UpdateGoal, {"status":"complete"})` (legacy
continuation-wake). Successor **attaches** `tail --label` per ARM label +
`liaison-arm-loop.sh` then `liaison-monitor-loop.sh` (`--heartbeat 1200`); skip
`CreateGoal`. Answer `RETIRED → <id>`; never harvest.
