# `agent-surface/` — Neutral-canonical sources for cross-target rule projection

This tree is the source of truth for rules that project to multiple agent targets:

- `sources/<name>.md` — section-tagged source files (markers: `<!-- target:cursor -->`,
  `<!-- target:grok-direct -->`, `<!-- target:* -->`).
- `table-variants/<name>.<target>.md` — pre-normalized per-target table content for
  rules whose table-shaped sub-section diverges per target without warranting a full
  source pivot (currently: `subagent-model-table.cursor.md` drift-check artifact +
  `subagent-model-table.grok.md` AGENTS.md splice content).

## Generator

`scripts/gen-rules` is the single emitter. See `scripts/gen-rules --help`.

| Target | Cursor outputs | grok-direct outputs |
|---|---|---|
| `cursor` | `.cursor/rules/cursor-boot_ws.mdc`, `.cursor/rules/mcp-tool-awareness_ws.mdc` + drift-check vs `core_ws.mdc` table | — |
| `grok-direct` | — | AGENTS.md splice into `gen-rules:start:<id>` markers (`boot-protocol`, `mcp-tool-awareness`, `subagent-model-table`) |

Run `scripts/gen-rules --target cursor --check` and
`scripts/gen-rules --target grok-direct --check` in CI; both must exit 0.

## Section IDs

| ID | Source | Cursor output | grok-direct AGENTS.md splice |
|---|---|---|---|
| `boot-protocol` | `sources/boot-protocol.md` | `.cursor/rules/cursor-boot_ws.mdc` | `<!-- gen-rules:start:boot-protocol -->` |
| `mcp-tool-awareness` | `sources/mcp-tool-awareness.md` | `.cursor/rules/mcp-tool-awareness_ws.mdc` | `<!-- gen-rules:start:mcp-tool-awareness -->` |
| `subagent-model-table` | `table-variants/subagent-model-table.{cursor,grok}.md` | drift-check only | `<!-- gen-rules:start:subagent-model-table -->` |
| `command-map` | `sources/command-map.md` | — | `docs/agent-guides/mvw-command-map.md` (`gen-rules --target command-map`) |

**Manual sync (cortex sandbox):** `sources/session-close-handoff-depth-gate.md` — paste or
merge into `agent-skills/session-close-handoff.md` when the cortex skill drifts (kernel
§ Depth dial already carries the FOL gate).

## Design

Authoritative spec: `tmp/prompts/phase3-neutral-canonical-design.md` (v3 final).

## Marker grammar

```
<!-- target:(cursor|grok-direct|\*) -->
...content...
<!-- /target:(cursor|grok-direct|\*) -->
```

All content in `sources/*.md` MUST be inside a `target:X` block. Unknown target names
are rejected.

`<!-- frontmatter:cursor ... -->` (single-block) declares YAML front matter emitted
only for the cursor target (wrapped in `---` fences).

## Heading normalization

Source files use `#` for the H1. The generator applies a +1 heading depth offset when
emitting grok-direct content into AGENTS.md. Files in `table-variants/` are pre-normalized
to AGENTS.md depth and bypass offsetting.
