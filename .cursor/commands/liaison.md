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
   reason `liaison <root> tick`, debounce 15000):

   ```bash
   cd /mnt/torus/projects/universal-llm-gateway && ~/.venvs/universal/bin/python scripts/liaison-tick.py \
     --root <root> --register <register> --loop --poll 60 --heartbeat ${INTERVAL:-900}
   ```

   Title the shell `Loop liaison <root>: tick`. Record the PID in the scoreboard `## Loop` row.
6. **First tick now** — run § Tick protocol on the `--once` digest from step 3 so the first server tick is
   not cold. End the turn; wakes arrive as `AGENT_LOOP_TICK_liaison` notifications.

Stop: kill the loop PID, post a segment CHECKPOINT, `UpdateGoal(status=complete)` only if the objective is
actually met (otherwise leave active), say why the loop stopped.
