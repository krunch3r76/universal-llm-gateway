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
| General execution (contract-based, no packet) | `§ General execution lane (contract-based — no packet)` |
| Provider affordances vs roles | `§ Provider affordances vs team_dispatch roles (vocabulary)` |

**Quick ref — implement dispatch** (default = `cursor-sdk` generate, **dense packet required**; handoff = operator-attended fallback; canonical detail in cortex SOT § Dispatch targets / Implement lane):

```python
# DEFAULT — auto Composer, no IDE pickup
team_dispatch(op="generate", role="cursor-sdk", contract="implement",
              packet_path="tmp/reviews/{slug}-implement-packet.md",
              dispatch_thread_id="{arc-id}")

# FALLBACK — operator-attended IDE
team_dispatch(op="handoff", role="cursor-implement",
              source_ref="todo:{slug}", subject="Implement {slug}")
```

Introduced `todo:unified-admission-handoff-source-ref` (2026-06-08); arc shipped
`task:unified-implement-admission` (2026-06-10).
