## Subagent Model Selection (quick ref)

| Model | MCP in Cursor subagent? | Notes |
|---|---|---|
| Auto Efficiency | Yes | Mechanical batch work (or `gpt-5.3-codex` if explicit slug needed) |
| `claude-4.6-sonnet-medium-thinking` | Yes | **Default** for general subagent work in this workspace (MCP-capable) |
| `composer-2.5` | Yes | Code-composition tasks: codegen, refactors, implementation against a spec |
| `grok-4.3` | **No** | Specialized only: non-MCP frontier dispatch with pre-staged corpus/context inline |
| `gpt-5.5-high` | Yes | High-rework territory |
| `claude-opus-4-7-thinking-xhigh` | Yes | Last resort / cross-agent protocol |

**Invariant**: Default for general subagent work in this workspace: `claude-4.6-sonnet-medium-thinking` (MCP-capable). Use `grok-4.3` only for non-MCP frontier dispatch tasks where corpus is pre-staged inline.

Full policy: `subagent-strategy.mdc`.
