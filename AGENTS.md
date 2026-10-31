# Universal LLM Gateway — Agent Guide

## Seat router (read first)

| Surface | Primary guide | MVW command index |
|---|---|---|
| **Cursor IDE** | `.cursor/rules/` + `/mnt/torus/projects/.cursor/rules/` — start `cursor-environment_ws.mdc`; session open via `cortex_brief(seat=<seat>)` | `.cursor/commands/` + `docs/agent-guides/mvw-command-map.md` |
| **web-claude** (vortex MCP) | `docs/agent-guides/README.md` → `rules/` (unified); `agent-guides/web-agent-orientation.md` | `docs/agent-guides/mvw-command-map.md` |
| **No-cortex** | `docs/agent-guides/no-cortex.md` | `docs/agent-guides/mvw-command-map.md` (read/plan only) |

Parity: `cortex://notes/system/specs/agent-workflow-parity.md` (`project:agent-workflow-parity`).

## Working tree, not the commit

`on_disk(path) ∨ loaded_by_process(path) ⇒ path ∈ ULG` (gitignored/untracked trees included). `¬report("not in the tree", from=git_ls_files)`. Check the working tree and state when a search honored gitignore.
