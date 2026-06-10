# Consult Routing

**SOT:** `cortex://agent-skills/consult-routing.md` — full playbook (R1/R2/R3, executor tier,
dispatch shapes, implement-lane `source_ref`). Read via `fs(sandbox="cortex", op="read", path="agent-skills/consult-routing.md")`
before dispatching. Boot skill; briefing card emits the compact index.
Cursor-indexed entry: `.cursor/skills/consult-routing/SKILL.md`.

Do not duplicate the cortex playbook body here — load the SOT section you need:

| Topic | Cortex section |
|---|---|
| Executor tier (R1/R2/R3) | `§ Executor tier & handoff mechanics` → `§ Canonical routing policy` |
| Implement lane `source_ref` | `§ Implement lane — source_ref` |
| Lane → transport table | `.cursor/rules/todo_ws.mdc` §Dispatch metadata |
| Handoff dispatch shapes | `projects/.cursor/rules/handoff-dispatchers.mdc` |

**Quick ref — implement dispatch** (canonical detail in cortex SOT):

```python
team_dispatch(op="handoff", role="cursor-implement",
              source_ref="todo:{slug}", subject="Implement {slug}")
```

Introduced `todo:unified-admission-handoff-source-ref` (2026-06-08); arc shipped
`task:unified-implement-admission` (2026-06-10).
