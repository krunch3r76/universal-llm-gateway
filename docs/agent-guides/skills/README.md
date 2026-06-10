# Unified skills (procedural playbooks)

**Readers:** web-claude, connector, Cursor (via thin `.cursor/skills` stubs).

## Pattern

| Layer | Role |
|-------|------|
| **`docs/agent-guides/skills/`** | Git SOT (architecture, pipeline, …) |
| **cortex `agent-skills/`** | Boot-indexed playbooks (consult-routing, dispatch-workflow, …) |
| **cortex `agent_skill:*`** | Boot index; repo skills use `source_uri` → `workspaces://…/docs/agent-guides/skills/…` |
| **`.cursor/skills/<slug>/SKILL.md`** | Thin stub: trigger + `fs(cortex|workspaces, …)` read |

## Migrated (Track A — repo SOT)

| Slug | Repo path |
|------|-----------|
| `architecture-invariants` | `architecture-invariants.md` |
| `ulg-architecture` | `ulg-architecture.md` |
| `build-pipeline` | `build-pipeline/` (SKILL + reference files) |
| `refine-pipeline` | `refine-pipeline.md` |
| `friction-review` | `friction-review.md` — friction log vs codified bug ticket; pass zoom-out duty on bus pickup |

## Cortex SOT (boot-indexed — stub in `.cursor/skills/`)

| Slug | Cortex path | Cursor stub |
|------|-------------|-------------|
| `consult-routing` | `agent-skills/consult-routing.md` | `.cursor/skills/consult-routing/SKILL.md` |
| `dispatch-workflow` | `agent-skills/dispatch-workflow.md` | — (read from boot index on first dispatch) |

Do not hand-maintain duplicate long-form copies in cortex or `.cursor/skills`.
