# Universal LLM Gateway — Agent Guide

## Seat router (read first)

Repo-root `AGENTS.md` is the **cold-start router** for every agent surface. Pick one row.

| Surface | Primary guide | MVW command index |
|---|---|---|
| **Cursor IDE** | `.cursor/rules/` + `/mnt/torus/projects/.cursor/rules/` — start `cursor-environment_ws.mdc`; session open via `cortex_brief(seat=<seat>)` | `.cursor/commands/` + `docs/agent-guides/mvw-command-map.md` |
| **web-claude** (vortex MCP) | `docs/agent-guides/README.md` → `rules/` (unified); `agent-guides/web-agent-orientation.md` | `docs/agent-guides/mvw-command-map.md` |
| **No-cortex** (GitHub connector, read-only reviewer) | `docs/agent-guides/no-cortex.md` | `docs/agent-guides/mvw-command-map.md` (read/plan column only) |

**Parity arc:** `cortex://notes/system/specs/agent-workflow-parity.md` (`project:agent-workflow-parity`).

## Working tree, not the commit

A path is part of ULG when it is on disk or a running process loads it. Git
tracking is a publish choice, not membership. Gitignored and untracked files
still run locally — `docs/agent-guides/rules/`, generated guides, and other
ignored trees included.

Do not report "not in the tree" from `git ls-files` or a search that skips
ignored files. Check the working tree. Say when a search honored gitignore.
