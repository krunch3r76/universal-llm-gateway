# `agent-surface/` — Neutral-canonical sources for cross-target rule projection

**Unified read surface (all agents):** generated markdown under
`docs/agent-guides/rules/` — primary path for **web-claude** and repo-only readers.

This tree is the **authoring** source of truth for rules that project to agent targets:

- `sources/<name>.md` — section-tagged source files (markers: `<!-- target:cursor -->`,
  `<!-- target:* -->`).
- `table-variants/<name>.<target>.md` — pre-normalized per-target table content for
  rules whose table-shaped sub-section diverges per target without warranting a full
  source pivot (currently: `subagent-model-table.cursor.md` drift-check artifact).

## Generator

`scripts/gen-rules` is the single emitter. See `scripts/gen-rules --help`.

| Target | Cursor outputs |
|---|---|
| `cursor` | `.cursor/rules/cursor-boot_ws.mdc`, plugin `mcp-tool-awareness_ulg.mdc` + drift-check vs `core_ws.mdc` table |

Run `scripts/gen-rules --target cursor --check` in CI; must exit 0.

## Section IDs

| ID | Source | Cursor output |
|---|---|---|
| `boot-protocol` | `sources/boot-protocol.md` | `.cursor/rules/cursor-boot_ws.mdc` |
| `mcp-tool-awareness` | `sources/mcp-tool-awareness.md` | `cursor-plugins/ulg-ecosystem/rules/mcp-tool-awareness_ulg.mdc` |
| `subagent-model-table` | `table-variants/subagent-model-table.cursor.md` | drift-check only |
| `command-map` | `sources/command-map.md` | `docs/agent-guides/mvw-command-map.md` (`gen-rules --target command-map`) |
| `agent-guides-rules` | MVW conduct manifest in `libs/gen_rules/agent_guides.py` | `docs/agent-guides/rules/*.md` (`gen-rules --target agent-guides-rules`) — incl. `capability-dispatch` |

**Manual sync (cortex sandbox):** `sources/session-close-handoff.md` (+ depth-gate section
from `session-close-handoff-depth-gate.md`) — merge into skill `session-close-handoff`
when the cortex skill drifts. Cursor binding: `projects/.cursor/rules/session-close.mdc` §6b.

## Design

Authoritative spec: `tmp/prompts/phase3-neutral-canonical-design.md` (v3 final).

## Marker grammar

```
<!-- target:(cursor|\*) -->
...content...
<!-- /target:(cursor|\*) -->
```

All content in `sources/*.md` MUST be inside a `target:X` block. Unknown target names
are rejected.

`<!-- frontmatter:cursor ... -->` (single-block) declares YAML front matter emitted
only for the cursor target (wrapped in `---` fences).

## Heading normalization

Source files use `#` for the H1. Files in `table-variants/` are pre-normalized
to output depth.
