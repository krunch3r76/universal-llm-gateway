# Todo Lifecycle

**SOT:** `cortex://agent-skills/todo-lifecycle.md` — procedural Gate 1–9 playbook (seed → ship)
and known gotchas. Read via `fs(sandbox="cortex", op="read", path="agent-skills/todo-lifecycle.md")`.
Cursor-indexed entry: `.cursor/skills/todo-lifecycle/SKILL.md`.

Do not duplicate the cortex playbook body here — the cortex file owns the authoritative
procedure. Reference material (seed contract, grouping, closure sidecars) lives on
`rule:todo-lifecycle` → `docs/agent-guides/rules/todo-lifecycle.md`.

| Topic | Where |
|---|---|
| Gate 1–9 procedure + gotchas | `agent-skills/todo-lifecycle.md` (this skill) |
| Todo schema, seed contract, grouping | `rule:todo-lifecycle` / `docs/agent-guides/rules/todo-lifecycle.md` |
| Densify / implement dispatch shapes | `skill:consult-routing` |
| Pickup / implement admission | `skill:implement-todo` |
