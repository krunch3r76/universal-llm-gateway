# /liaison [<root>] [attended|autonomous] [--interval S]

**Impresario gear inference (before any arm step).** This tab's agent is the **Impresario** — infer
**Light**, **Attended**, or **Overnight** internally. Do not ask the operator to choose between
`/orchestrate` and `/liaison` or to name a gear. `/orchestrate` and `/liaison` share this same entry
(see `cursor-plugins/ulg-ecosystem/commands/orchestrate.md`).

| Gear | When | Action |
|---|---|---|
| **Light** | No continuity root and no durable objective or `todo:` (in-seat question, chat, small patch) | Answer in-seat. **Do not** `create_thread`, **do not** run `liaison-arm-loop.sh` or load the tick-protocol body. |
| **Attended (birth + seat)** | Operator named a root slug and/or a durable objective or `todo:`, and `agent_bus_read(thread_get, thread=<root>)` lacks `role:root` (or the root thread does not exist yet) | `Use the orchestration-lanes skill` — birth the house (create root thread, continuity doc, birth CHECKPOINT, stamp `role:root`), then run steps 1–6 below on `<root>`. |
| **Attended (seat only)** | `role:root` already on `<root>` | Steps 1–6 below (orient, register, arm loop). |
| **Overnight (metronome)** | Departure utterance (`go under`, `running overnight`, leaving, hand off overnight) | `Use the ide-continuity skill` § **go under** only (`liaison-tick.py --go-under`, gear-3 ticker). **Do not** start a new attended seat loop. |

Bare `/liaison` or `/orchestrate` with **no** root slug and **no** durable objective stays **Light** — do not mint an empty `role:root`. Bare `/liaison` with no root can still resolve a **foreign** tab when resolving `ide:<transcript_id>` (specimen `866948bb…`); prefer `/liaison <root>` or `resume <root>` when the house is known.

When steps 1–6 run (**Attended** only):

1. **Load** `Use the ide-continuity skill` (`cursor-plugins/ulg-ecosystem/skills/ide-continuity/SKILL.md`) and `checkpoint-discipline`.
2. **Orient** — `agent_bus_read(thread_get, thread=<root>)` → read the tip CHECKPOINT (latest turn with
   `TYPE: CHECKPOINT`), open its scoreboard URI via `fs(sandbox="cortex", op="read")`. Do not read the thread
   linearly. If the operator wrote `resume <root>` first, the fence already poured — skip.
3. **Register** — one digest for the house. Prefer MCP
   `continuity(op=tick, thread=<root>, register=<register>)` or CLI
   `~/.venvs/universal/bin/python scripts/liaison-tick.py --root <root> --register <register> --once`.
   Quote `budget`, `lanes`, `attention`. Default register: `attended`.
4. **Wakes — skip `CreateGoal`.** Native goals have no interval and inject continuation wakes that
   are not the house ticker. Do not arm a turn-by-turn watcher on in-flight lanes. A finish signal
   (`closeout turn=` / `consult complete`) is one harvest. Backup = `--loop` heartbeat **1200s**
   (20 min) only while a finish watcher is already live or a row is playable. `--interval S`
   overrides that heartbeat only.

   **Cursor-on-Xvfb `:8` / `orion-node` push-wake Glass houses** (`policy.gui_host` or
   `detect_cursor_gui_host` names a tracked Glass display — see `config/cursor-windows.json`):
   **do not** spawn `liaison-arm-loop.sh`, `liaison-monitor-loop.sh`, or
   `watch-supervise.sh` / `watch-bus-consult-and-page.py`. Wakes arrive via
   `endpoint_wake_dispatcher` → `display-chat-wake` (`BRIDGE_WAKE`). After register (step 3),
   end the turn **idle** for push delivery. Skip step 5 below.

5. **Arm the loop (Jupiter / COSMIC attended IDE only — not `:8` / `orion-node` push-wake)** —
   detach, then monitor the log (a:38446). Do **not** run `liaison-tick.py --loop` as the agent
   Shell itself (`block_until_ms: 0` on that python process). SIGTERM/harvest exit of a Shell-owned
   loop is a `system_notification` wake that displaces LOCKED harvest SMS (specimen 15420 after #115).
   Any tab model may seat the liaison (skill § Seat model). This tab becomes the one liaison seat:
   the loop claims the seat lock as `ide:<transcript_id>` — resolve it with
   `continuity(op=hop, thread=<root>, find_transcript=…)` or
   `scripts/liaison-ide-hop.py --root <root> --find-transcript` on the tab's **first user message**.
   That is `resume <root>` when the operator typed resume first (this tab's specimen: `931b214d…`);
   `"/liaison <root>"` only when that slash line is the first turn — bare `"/liaison"` can resolve a
   **foreign** tab (specimen `866948bb…`). `"loop": "refused"` / arm JSON `ok:false` with lock held ⇒
   a headless successor holds it and will park within one poll — re-arm after ~60 s; `reason=held` ⇒
   another **attended tab** holds the seat — stay a worker tab (own legs and turns; no loop, no
   scoreboard Rows fold, no CHECKPOINT on the root) unless the operator's word moves the seat here: a
   fresh-tab `resume <root>` (other workstation) or "take over" ⇒ add `--take-over` to the arm
   command, then re-arm after the live loop releases (~60 s):

   **(a) Spawn** — short Shell (foreground OK; must exit 0 immediately after arm):

   ```bash
   cd /mnt/torus/projects/universal-llm-gateway && \
     scripts/liaison-arm-loop.sh \
       --root <root> --register <register> --holder ide:<transcript_id> \
       --poll 60 --heartbeat ${INTERVAL:-1200}
   ```

   Quote the JSON last line (`ok`, `pid`, `log`, `monitor`). Record `pid` in the scoreboard
   `## Loop` row. On `ok:false`: do **not** start a monitor. Key the refuse on `reason` +
   `lock.holder` prefix — `held` with `ide:` ⇒ stay a worker tab (do not re-arm every 60 s);
   `held_preempt_requested` / `sdk:` ⇒ re-arm after ~60 s once the headless holder parks.

   **(b) Monitor** — only when arm JSON `ok:true`. Separate background Shell (`block_until_ms: 0`,
   `notify_on_output` pattern `^AGENT_LOOP_TICK_liaison`, reason `liaison <root> tick`, debounce
   15000). Title `Loop liaison <root>: tick`. Use the `monitor` / `log` / `monitor_heartbeat` paths
   from the arm JSON (not a cwd-relative guess). If a monitor for this root is already live on this
   tab, re-arm runs **(a) only**:

   ```bash
   scripts/liaison-monitor-loop.sh --log <log from JSON> --heartbeat <monitor_heartbeat>
   ```

   That Shell is the liveness token: when it dies, the loop exits and releases the seat. Killing the
   python loop does **not** exit this Shell. Digest consumers still read the **last** stdout line of
   `--once` / `op=tick`.
6. **First tick now** — run § Tick protocol on the step 3 digest so the first server tick is not cold.
   On push-wake Glass houses, that digest is the whole arm — end idle. Else end the turn; wakes arrive
   as `AGENT_LOOP_TICK_liaison` notifications.

Stop (loop kill / park, **no hop**): SIGTERM the loop `pid` from the arm JSON /
`tmp/watchers/liaison-loop-<root>.pid` (or
`pkill -f 'liaison-tick[.]py --root <R> --loop'`), then stop the monitor Shell
if this tab is leaving. Post CHECKPOINT. `UpdateGoal(complete)` only if a
leftover native goal is still injecting wakes, or the house objective is
actually met. If a loop-abort `system_notification` still arrives: restate the
LOCKED harvest body, or say nothing new if already relayed — never a
loop-status-only reply (a:38446).

Hop (`continuity(op=hop, …)` or `liaison-ide-hop.py` `ok`): harness retires this tab's
`--loop`, Jupiter `watch-supervise` tails, and `ide:` lock (`retire_departing_tab`); house
pollers survive. JSON + stderr emit `LIAISON_HOP_TAB_GOAL_RELEASE`. **Same turn, before
`RETIRED →`:** if a native goal is active,
`CallDynamicTool(cursor, UpdateGoal, {"status":"complete"})` (legacy continuation-wake).
**Jupiter / COSMIC successor:** attach `tail --label` per ARM label + `liaison-arm-loop.sh`
then `liaison-monitor-loop.sh` (`--heartbeat 1200`). **`:8` / `orion-node` push-wake
successor:** push-wake only — rely on `BRIDGE_WAKE`; do **not** attach `watch-supervise.sh`,
`watch-bus-consult-and-page.py`, or `liaison-arm-loop.sh`. Skip `CreateGoal`. Answer
`RETIRED → <id>`; never harvest.
