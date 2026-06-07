# Modularize Invariants — moved to cortex

The canonical source is now:

- Cortex skill: `cortex://agent-skills/modularize-discipline.md`
- Cortex entity: `agent_skill:modularize-discipline`

This file remains as a redirect so anything that previously pointed to
`.cursor/invariants/modularize.md` resolves to a discoverable pointer rather
than vanishing.

## For packet builders (`/modularize` §2.2, `/overhaul` §2.1)

The `<invariants>` block should reference the cortex skill alongside the
universal architecture skills — no inline paste of this file is needed:

```
Read the following skills before code generation:
- cortex://agent-skills/architecture-invariants.md
- cortex://agent-skills/ulg-architecture.md
- cortex://agent-skills/modularize-discipline.md
```

The cortex skill carries the same rules previously in this file (SLOC ceilings,
forbidden generic module names, public surface preservation, docstring
requirement, change-scope discipline, logger replacement, local validation
discipline) plus companion-skill cross-references and anti-patterns.

## Promotion rationale

Moved 2026-05-27 to enable cross-seat reuse — non-Cursor dispatchers (web-claude
direct, frontier-mcp, Composer 2.5, grokbuild) can load the discipline via a
single `cortex://` reference instead of requiring workspace-path resolution.
See `cortex(entity_get, "agent_skill:modularize-discipline")` for the
promotion assertion (id: 11284) and rationale.
