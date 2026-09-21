# /liaison <root> [attended|autonomous] [--interval S]

Seat this tab as the house liaison on continuity root `<root>` and arm the tick loop.

1. **Load** `Use the liaison skill` (`.cursor/skills/liaison/SKILL.md`) and `checkpoint-discipline`.
2. **Orient** — `agent_bus_read(thread_get, thread=<root>)` → read the tip CHECKPOINT (latest turn with
   `TYPE: CHECKPOINT`), open its scoreboard URI via `fs(sandbox="cortex", op="read")`. Do not read the thread
   linearly. If the operator wrote `resume <root>` first, the fence already poured — skip.
3. **Register** — `~/.venvs/universal/bin/python scripts/liaison-tick.py --root <root> --register <register> --once`
   and quote `budget`, `lanes`, `attention`. Default register: `attended`.
4. **Goal** — call `CreateGoal` once with the objective from the tip CHECKPOINT `## Objective` (append
   "register=<register>; root=agent-bus:<root>"). Goal-layer persistence only — **never** `work_key` / `source_ref`.
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
     --root <root> --register <register> --holder ide:<transcript_id> --loop --poll 60 --heartbeat ${INTERVAL:-900}
   ```

   Title the shell `Loop liaison <root>: tick`. Record the PID in the scoreboard `## Loop` row.
   Digest consumers read the **last** stdout line of `--once` (sitecustomize prints validation lines first).
6. **First tick now** — run § Tick protocol on the `--once` digest from step 3 so the first server tick is
   not cold. End the turn; wakes arrive as `AGENT_LOOP_TICK_liaison` notifications.

Stop (loop kill / park, **no hop**): kill the loop PID, post CHECKPOINT, leave the goal **active** unless
the house objective is actually met — then `UpdateGoal(status=complete)`.

Hop (`liaison-ide-hop.py` `ok`): harness retires this tab's `--loop`, `watch-supervise` tails, and `ide:`
lock (`retire_departing_tab`); JSON + stderr emit `LIAISON_HOP_TAB_GOAL_RELEASE`. **Same turn, before
`RETIRED →`:** `CallDynamicTool(cursor, UpdateGoal, {"status":"complete"})` — **tab-goal release** (house
stays open; ¬ arc close). Successor `CreateGoal` + ARM tails. Answer `RETIRED → <id>`; never harvest.
