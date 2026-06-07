---
name: dispatch-shape
description: On any cortex, agent_bus, dispatch, or rag MCP call — or when copying poll_hint after team_dispatch handoff — read BEFORE calling. arguments must be a JSON string, not a nested object.
---

# Dispatch Shape

**SOT:** `cortex://agent-skills/dispatch-shape.md`

Read before the first dispatch-style MCP call or handoff poll:

```
fs(sandbox="cortex", op="read", path="agent-skills/dispatch-shape.md")
```

## Quick rule

| Tool family | Inner `arguments` type |
|---|---|
| `cortex`, `agent_bus`, `dispatch`, `rag` | **JSON string** |
| `fs`, `observability`, `manage`, `team_dispatch` | Typed top-level object |

## Handoff poll

After `team_dispatch(op=handoff)`:

```
agent_bus(tool=poll_hint.tool, arguments=poll_hint.arguments_json)
```

Use `arguments_json`, not `poll_hint.arguments` (object) — Cursor/web MCP clients reject the object form.

## Escape hatch

`scripts/agent-bus` CLI (see `/agent-bus` command) bypasses MCP shape validation.
