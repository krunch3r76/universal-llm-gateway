<!-- target:* -->
# MVW command map (11 — cap exception: +`/overhaul`, thread 1415; +`/claude-ai-sync` ULG ops)

Ratified MVW (`decision:agent-workflow-parity-mvw`). Slash commands are **Cursor
affordances**; MCP seats use the equivalent tool calls directly. No-cortex seats
use only the **read/plan** column.

| Command | Cursor | web-claude / MCP seat | No-cortex (connector) |
|---|---|---|---|
| `/cortex-boot` | `.cursor/commands/cortex-boot.md` → `cortex_boot(agent=<family>-cursor)` + TIER-2 rules | `cortex_boot(agent="claude-web", …)`; `agent-guides/web-agent-orientation.md` | Static boot: `AGENTS.md` router + `docs/agent-guides/no-cortex.md` |
| `/agent-bus` | `.cursor/commands/agent-bus.md` → `scripts/agent-bus` or `agent_bus` MCP | `agent_bus(threads/fetch/post/reply/wait, …)` + cortex sidecars | Out of scope (writes); findings via operator paste |
| `/session-end` | `.cursor/commands/session-end.md` → `cortex(session_close, transcript_jsonl_path=…)` | `cortex(session_close, transcript_md=…)` | Out of scope |
| `/todo` | `.cursor/commands/todo.md` → `cortex(todo_candidates)` / `entity_create(type=todo)`; **`/todo pickup {slug}`** loads `implement-todo` skill | same cortex ops; **`Pick up todo:{slug}`** loads `implement-todo` skill | Read spec/todo entities only if inlined in packet |
| `/plan-seed` | `.cursor/commands/plan-seed.md` → spec write + `pipeline(plan-seed)` | `fs` spec + `pipeline(plan-seed)` or manual `plan:`/`todo:` entities | Out of scope (authoring) |
| `/create-implementation-plan` | `.cursor/commands/create-implementation-plan.md` → plan deck + cortex entities | `fs` + cortex entities per `implementation-plan-workflow` skill | Plan *review* only — packet + repo read |
| `/implement-plan` | `.cursor/commands/implement-plan.md` — Cursor/cursorbuild executes | Coordinate via `agent_bus` + dispatch; no slash emulation | Out of scope (execution) |
| `/consult-plan` | `.cursor/commands/consult-plan.md` → `team_dispatch(generate)` or `handoff` per skill `consult-routing` | same; six-block packet (`.cursor/skills/handoff-packet-authoring/SKILL.md`) | In scope — reviewer receives inlined packet |
| `/consult-review` | `.cursor/commands/consult-review.md` → reviewer dispatch | same | In scope — primary connector job |
| `/verify-implementation` | `.cursor/commands/verify-implementation.md` → checklist + gates | `fs` read-back + `observability` / dispatch as available | Read-only verification against named files |
| `/overhaul` | `.cursor/commands/overhaul.md` → 12-step directory pass; read `.cursor/skills/overhaul-program/SKILL.md` first | `fs` scan paths + `team_dispatch(handoff, role=web-consult)` for deep split **planning** + post-overhaul **review**, `team_dispatch(op=generate, role=cursor-sdk, packet_path=…, contract=implement)` for approved **mechanical apply** (default; dense packet) + `scripts/modularize` / `doc-generate` when operator approves each call | Read/plan only: modularize scan output + propose split/review plan; no execution |
| `/claude-ai-sync` | `.cursor/commands/claude-ai-sync.md` → `gen_claude_bundles` + `scripts/cortex/claude-ai-sync-jupiter` (status/upload on Jupiter); runbook `.cursor/skills/claude-ai-bundle-sync/SKILL.md` | `fs` regen + validate locally; `claude-ai-sync-jupiter` or operator SSH for CDP upload | Out of scope (no claude.ai UI access) |

**SOT:** this file. **Projection:** `docs/agent-guides/mvw-command-map.md` via
`scripts/gen-rules --target command-map`. **Drift:** `scripts/agent-surface-check`.

<!-- /target:* -->
