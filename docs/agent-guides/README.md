# Agent guides (repo read surface)

Guides for **all seats**. Many files here are gitignored (`**/*.md`) and still
on disk; a missing git entry is not absence. MCP-capable agents use
`fs(sandbox="workspaces", path="universal-llm-gateway/docs/agent-guides/...")`.

## Boot order

1. Repo root **`AGENTS.md`** — seat router (Cursor / web / no-cortex)
2. **This directory** — shared substance (not seat-specific)
3. Seat supplements — `.cursor/rules/` (Cursor triggers), cortex `agent-skills/` (procedures)

## Paths

| Guide | Audience |
|-------|----------|
| [mvw-command-map.md](mvw-command-map.md) | MVW slash-equivalents (generated) |
| [no-cortex.md](no-cortex.md) | GitHub connector / read-only reviewer |
| [orchestration-threads.md](orchestration-threads.md) | Continuity roots and formal mission lanes |
| [rules/](rules/) | **Unified conduct rules** (generated — `gen-rules --target agent-guides-rules`) |
| [skills/](skills/) | **Unified procedural skills** — repo SOT; cortex + `.cursor/skills` stubs point here |
| [invariants-bundle.md](invariants-bundle.md) | Optional aggregate; may fold into `rules/` or `skills/` once migrated |

**Parity arc:** `tasks/specs/agent-workflow-parity.md` — rules unification proceeds
**independent of** SuperGrok co-pilot validation.
