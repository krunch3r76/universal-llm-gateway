# Libraries — Agent Guide (grok-direct)

Shared Python libraries under `libs/` power Stargate, gateway, MCP tools, grokbuild, cortex store, transport, and pipelines. This file **scopes lib conventions** for grok-direct sessions; repo-wide identity, MCP, and worktree rules live in `/AGENTS.md`.

---

## Import and layout

| Item | Value |
|---|---|
| **PYTHONPATH** | `libs/` root via workspace `sitecustomize.py` |
| **Import style** | Direct package imports—e.g. `from process_ipc.core import ...`, `from grokbuild.worktree import WORKTREE_ROOT` |
| **Venv** | `$HOME/.venvs/universal` (Python 3.12) |

Do not add shim re-exports for “backward compatibility”—sole-maintainer codebase.

---

## Domain isolation

- Each domain library imports only: stdlib, third-party, and **its own** package tree.
- No circular imports between domain packages.
- Higher layers translate types; use **Protocol + factory** at boundaries—avoid reaching into unrelated domains’ concrete types.

---

## Modularization

Aligned with `/mnt/torus/projects/.cursor/rules/modularization.mdc`:

| Threshold | Action |
|---|---|
| ≤300 SLOC | OK |
| 301–400 | Refactor soon |
| >400 | Blocker—split before substantial edits |

Pre-edit: if file >350 SLOC and you add >20 lines, split first. Check: `scripts/modularize scan <path>`.

**SRP:** split functions that mix validation, orchestration, mutation, and I/O; split classes/handlers when they exceed ~200 / ~80 SLOC respectively.

---

## Logging

**Invariant:** library code uses `universal_logging` only:

```python
from universal_logging import get_logger, INFO, WARNING, ERROR
logger = get_logger(__name__)
```

`import logging` in `libs/` is a policy violation (same as `services/`).

---

## Notable packages (grok-direct touchpoints)

| Package | Role |
|---|---|
| `grokbuild/` | Worktree ops, dispatch runner, events (`mcp.grokbuild.*` factories) |
| `agent_seat/` | Seat slug normalization (`grok_direct` → `grok-direct` when registry updated) |
| `transport_utils/` | `DEFAULT_STARGATE_URL`, HTTP/UDS clients for Stargate |
| `cortex_store/` | Cortex DB access patterns |
| `process_ipc/`, `event_bus/` | IPC and event plumbing |

Grokbuild worktree root constant: `WORKTREE_ROOT` → `/mnt/torus/projects/ulg-grok-worktrees` (enforced in worker + lib validators).

---

## Quality gates

Before claiming Python changes done:

```bash
ruff check --select=UP --fix && ruff format && python -m compileall -q libs/<pkg>
```

Or MCP: `quality_gate` on touched paths.

---

## Cross-reference

| Topic | Location |
|---|---|
| Worktree / grokbuild protocol | `/AGENTS.md` § Worktree discipline; `agent-skills/grokbuild-v1.md` |
| Service deploy and ports | `services/AGENTS.md` |
| Event signal contracts | `docs/event-contracts.md` |
