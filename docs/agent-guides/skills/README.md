# Unified skills (procedural playbooks)

**Readers:** web-claude, connector, Cursor (via thin `.cursor/skills` stubs).

## Pattern

| Layer | Role |
|-------|------|
| **`docs/agent-guides/skills/`** | Git SOT (architecture, pipeline, …) |
| **cortex `agent-skills/`** | Boot-indexed playbooks (consult-routing, dispatch-workflow, …) |
| **cortex `agent_skill:*`** | Boot index; repo skills use `source_uri` → `workspaces://…/docs/agent-guides/skills/…` |
| **`.cursor/skills/<slug>/SKILL.md`** | Thin stub: trigger + `fs(cortex|workspaces, …)` read |

## Single resolver

Author the skill body once → register `agent_skill:*` with `source_uri` → all seats
resolve via that URI (`skill_suggest` emits authoritative `source_uri`; SF1 done).
Cursor rules = thin `alwaysApply` invariants; skills = procedural SOT in repo docs OR
cortex `agent-skills/` — not both. Packet authors: `entity_get` before every skill `fs`
line (`handoff-packet-authoring.md` § Skill load resolution).

## Migrated (Track A — repo SOT)

| Slug | Repo path |
|------|-----------|
| `architecture-invariants` | `architecture-invariants.md` · deferred: `architecture-invariants/events-docs.md`, `architecture-invariants/quality-gates.md` · stub: `.cursor/skills/architecture-invariants/SKILL.md` |
| `ulg-architecture` | `ulg-architecture.md` · deferred: `ulg-architecture/model-lifecycle.md`, `ulg-architecture/service-ops.md` · stub: `.cursor/skills/ulg-architecture/SKILL.md` |
| `build-pipeline` | `build-pipeline/` (SKILL + reference files) |
| `refine-pipeline` | `refine-pipeline.md` |
| `friction-review` | `friction-review.md` — friction log vs codified bug ticket; pass zoom-out duty on bus pickup |
| `handoff-packet-authoring` | `handoff-packet-authoring.md` — stage→densify→wrap lifecycle gates + six-block packet contract · stub: `.cursor/skills/handoff-packet-authoring/SKILL.md` |
| `agent-guidance-writing` | `agent-guidance-writing.md` — rules/skills/agent-guides authoring · stub: `.cursor/skills/agent-guidance-writing/SKILL.md` |
| `git-posture` | `git-posture.md` — truth substrate, execution lanes, no-diffs-to-LLMs · stub: `.cursor/skills/git-posture/SKILL.md` · Cursor rule stub: `.cursor/rules/commit-and-git-scope_ws.mdc` |
| `web-boot-lead` | `web-boot-lead.md` — web session open: `cortex_boot` call shape, skip-boot for bound coding, tiered skill preload |

## Cortex SOT (boot-indexed — stub in `.cursor/skills/`)

| Slug | Cortex path | Cursor stub |
|------|-------------|-------------|
| `consult-routing` | `agent-skills/consult-routing.md` | `.cursor/skills/consult-routing/SKILL.md` |
| `skill-document-writing` | `agent-skills/skill-document-writing.md` | — (no Cursor stub yet; read from boot index) |
| `dispatch-workflow` | `agent-skills/dispatch-workflow.md` | — (read from boot index on first dispatch) |
| `orchestrator-core` | `agent-skills/orchestrator-core.md` — Domain-neutral lead orchestration core — decompose→fan-out→adjudicate→close-back, context-conservation, delegation grammar, composition-seam binding table (6 fork-classes), 5-mode vocabulary (execute/conform/converse/coordinate/monitor). Auto-injects on every lead boot. | — (boot-indexed; no Cursor stub yet) |

Do not hand-maintain duplicate long-form copies in cortex or `.cursor/skills`.
