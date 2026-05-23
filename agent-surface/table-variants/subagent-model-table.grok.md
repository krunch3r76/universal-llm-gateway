## Subagent Model Selection

For grok-direct sessions (operator-driven Grok CLI). The grok subagent taxonomy is
fundamentally different from Cursor's: only the CLI lead seat is MCP-capable; all
API-dispatched workers are non-MCP and suitable for pre-staged-corpus dispatch only.

| Model | MCP-capable? | Use for |
|---|---|---|
| `grok-build` | Yes (CLI lead seat) | Lead/orchestrator session model — operator-driven CLI in a workspace terminal. **NOT a dispatch target.** All MCP-requiring work routes through this seat. |
| `xai/grok-4.3__effort_low` | No | Mechanical batch with pre-staged corpus; lowest reasoning rung |
| `xai/grok-4.3__effort_medium` | No | Default API dispatch — moderate-effort pre-staged work |
| `xai/grok-4.3__effort_high` | No | High-effort pre-staged analysis |
| `xai/grok-4.3__effort_xhigh` | No | Extra-high effort; deepest reasoning rung over pre-staged corpus |
| `xai/grok-4.20-0309-non-reasoning` | No | Pre-staged code-composition: codegen, refactors, implementation against an inline spec |
| `xai/grok-4.20-0309-reasoning` | No | Pre-staged architectural / analytical reasoning |
| `xai/grok-4.20-multi-agent-0309` | No | Pre-staged multi-agent orchestration dispatch |

**Invariant**: ∀ subagent dispatch requiring in-subagent MCP (cortex assertions, agent_bus posts, observability, fs ops, vortex calls): API grok variants are **not suitable** — route to Sonnet 4.6 (cursor seat) or run as `grok-build` lead-seat work. API grok rows above are valid only for non-MCP frontier dispatch with corpus pre-staged inline.

**Default for general grok-native dispatch (non-MCP)**: `xai/grok-4.3__effort_medium`.

Full policy: consult `agent-skills/grokbuild-v1.md` (cortex) for handoff packet, mode/tier, sidecar, audit; `agent-skills/grokbuild-v2.md` for async dispatch lifecycle.
