Block until a **tracked** background shell finishes, then report completion in
chat so the operator sees the outcome. Use when the operator says `/await-task`,
"await the poll", "wait for it", or "let me know when that finishes".

**Pair with launch discipline** — background work that should be awaitable MUST
use a tracked shell (`block_until_ms: 0`), **not** `nohup`. Only tracked shells
notify the agent on exit.

---

## Launch discipline (when starting background work)

1. Start with Shell `block_until_ms: 0` and `# close-monitoring` when the job may
   exceed 30s.
2. **¬ `nohup`** for work the operator may `/await-task` — detached daemons do
   not notify the agent.
3. Register immediately after the shell returns its `task_id`:

```bash
scripts/bg-task register \
  --task-id <SHELL_TASK_ID> \
  --slug <short-slug> \
  --title "<human title>" \
  [--log /tmp/logs/...] \
  [--wake-pattern 'AGENT_LOOP_WAKE_<PURPOSE>']
```

4. Tell the operator: slug, task_id, and that `/await-task <slug>` will block
   until done.

Optional: emit a unique `AGENT_LOOP_WAKE_<PURPOSE> {json}` line before `exit` so
the completion report can parse structured payload from the terminal file.

---

## `/await-task` — operator-triggered block

### Variants

| Invocation | Behavior |
|---|---|
| `/await-task` | Await all `running` tasks in registry (oldest first) |
| `/await-task <task_id>` | Await that shell task (numeric Cursor shell id) |
| `/await-task <slug>` | Resolve slug via registry → await that task |

### Steps

1. **Resolve targets**

```bash
scripts/bg-task resolve          # no arg → running tasks
scripts/bg-task resolve <ref>    # task_id or slug
```

If registry is empty, scan `terminals/` metadata for recent `status: running`
shells started this session — register retroactively if found, else report none.

2. **Block with Await** (one task at a time if multiple)

```
Await(task_id=<id>, block_until_ms=0)   # first check
Await(task_id=<id>, pattern=<wake_pattern or exit_code footer>)  # until done
```

Use `pattern` when the task emits `AGENT_LOOP_WAKE_*`. Otherwise poll until
terminal metadata shows `exit_code` (footer) or `status: succeeded|failed`.

**¬** spin in-turn `block_until_ms` polling loops >30s without operator `/await-task`.
This command IS the operator gate for long waits.

3. **Report in chat** (mandatory — operator must see finish)

- **Status:** succeeded / failed / timeout
- **Duration:** from terminal metadata `elapsed_ms` or registry timestamps
- **Evidence:** last 20 lines of terminal output OR matching wake JSON line
- **Follow-up:** if wake payload names a thread/exec, act or state next step

4. **Mark registry**

```bash
scripts/bg-task complete --task-id <id> --exit-code <n>
```

5. **Follow-up actions** from wake payload or arc context (poll closeout, bus
   reply, etc.) — only after step 3 report.

---

## Anti-patterns

| Bad | Good |
|---|---|
| `nohup … &` then hope agent wakes | Tracked shell + `scripts/bg-task register` |
| Agent polls every turn without operator `/await-task` | Operator says `/await-task`; agent blocks once |
| "Should be done" without terminal `exit_code` | Quote exit_code + wake line or tail evidence |
| Await without reporting in chat | Completion message is the whole point |

---

## Registry

Path: `tmp/bg-tasks/registry.json` (gitignored ephemeral; sole maintainer checkout).
