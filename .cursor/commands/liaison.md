# /liaison <root> [attended|autonomous] [--interval S]

Seat this tab as the house liaison on continuity root `<root>` and arm the tick loop.

1. **Load** `Use the liaison skill` (`.cursor/skills/liaison/SKILL.md`) and `checkpoint-discipline`.
2. **Orient** — `agent_bus_read(thread_get, thread=<root>)` → read the tip CHECKPOINT (latest turn with
   `TYPE: CHECKPOINT`), open its scoreboard URI via `fs(sandbox="cortex", op="read")`. Do not read the thread
   linearly. If the operator wrote `resume <root>` first, the fence already poured — skip.
3. **Register** — `~/.venvs/universal/bin/python scripts/liaison-tick.py --root <root> --register <register> --once`
   and quote `budget`, `lanes`, `attention`. Default register: `attended`.
4. **Goal** — call `CreateGoal` once with the objective from the tip CHECKPOINT `## Objective` (append
   "register=<register>; root=agent-bus:<root>"). This gives Cursor-native persistence across turns.
5. **Arm the loop** (monitored shell, `block_until_ms: 0`, `notify_on_output` pattern `^AGENT_LOOP_TICK_liaison`,
   reason `liaison <root> tick`, debounce 15000). Any tab model may seat the liaison (skill § Seat model).
   This tab becomes the one liaison seat: the loop claims the seat lock as `ide:<transcript_id>` — resolve it
   with `scripts/liaison-ide-hop.py --find-transcript "/liaison <root>"` (the tab's first user message).
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

Stop: kill the loop PID, post a segment CHECKPOINT, `UpdateGoal(status=complete)` only if the objective is
actually met (otherwise leave active), say why the loop stopped.
