<!-- frontmatter:cursor
description: MCP tool calling convention for Cursor — CallMcpTool/user-vortex syntax, fs markdown ops (md_read/md_replace/md_append), sandbox routing (cortex/context/workspaces), response-store flagged-payload semantics. Load before invoking any MCP tool (cortex, fs, agent_bus, observability, rag, pipeline, dispatch, team_dispatch).
alwaysApply: false
-->
<!-- target:cursor -->
# MCP Tool Awareness (Cursor)

## Calling Convention

All MCP tools live on the `user-vortex` server. In Cursor, call via:

```
CallMcpTool(server="user-vortex", toolName="<tool>", arguments={...})
```

When rules or docs use shorthand like `fs(sandbox="cortex", op="read", path="...")`,
translate to `CallMcpTool(server="user-vortex", toolName="fs", arguments={"sandbox": "cortex", "op": "read", "path": "..."})`.

**Dispatch-style tools** (`cortex`, `agent_bus`, `dispatch`, `rag`): the outer
`CallMcpTool` envelope is an object, but the inner `arguments` field MUST be a
**JSON string**, not a nested object. See `agent-skills/dispatch-shape.md`.

```
CallMcpTool(server="user-vortex", toolName="agent_bus", arguments={
  "tool": "fetch",
  "arguments": "{\"thread\": \"111\", \"last\": 3, \"compact\": true}"
})
```
<!-- /target:cursor -->
<!-- target:grok-direct -->
# MCP Tool Awareness

## Calling Convention

All MCP tools are called natively in grok-direct — no `CallMcpTool(...)` wrapper.
Shorthand forms used in rules and docs are direct call syntax:

- `fs(sandbox="cortex", op="read", path="...")` — filesystem operations
- `cortex(tool="entities", arguments='{"type": "decision", "limit": 20}')` — cortex operations
- `agent_bus(tool="threads", arguments='{"status": "active"}')` — agent bus operations

Dispatch-style `arguments` is always a **JSON string** on the wire — never a bare
object. After `team_dispatch(op=handoff)`, use `poll_hint.arguments_json` for MCP
`agent_bus` calls. Full shape: `agent-skills/dispatch-shape.md`.

When rules or docs reference `CallMcpTool(server="user-vortex", toolName=X,
arguments=A)`, translate to native `X(A)` call shorthand.
<!-- /target:grok-direct -->
<!-- target:* -->
## fs — Markdown Ops (PRIMARY)

For large markdown files, prefer section-level access over full reads:

| Op | Purpose | Extra args |
|---|---|---|
| `md_list` | Heading tree (TOC) | `path` |
| `md_read` | Read one section by heading | `path`, `section` |
| `md_replace` | Replace section body | `path`, `section`, `content` |
| `md_append` | Append to section body | `path`, `section`, `content` |
| `md_delete` | Delete section + body | `path`, `section` |
| `list` | Directory listing | `path`, optional `max_depth` (default 3) |
| `find` | Filename/glob locate (workspaces only) | `path` (repo scope), `content` (pattern) |
| `read` | Full file read | `path` (repo-relative refs auto-resolve) |
| `write` | Write/overwrite file | `path`, `content` |

## Sandbox Routing

| Sandbox | Root | Example path |
|---|---|---|
| `cortex` | MCP data dir | `notes/system/transcripts/cursor-2026-04-08-0127.md` |
| `context` | `tasks/` | `specs/some-spec.md` |
| `workspaces` | `/mnt/torus/projects/` | `universal-llm-gateway/docs/tool-reference.md` |

`workspaces` paths MUST include repo name prefix. `context` paths are relative to `tasks/`.
<!-- /target:* -->
<!-- target:cursor -->
## Continue Mode (Transcript Pickup)

When the opening message contains `transcript:cursor-YYYY-MM-DD-HHmm`, run
three parallel calls: (1) `fs md_read` the session summary section, (2)
`agent_bus fetch thread=480` for activity journal, (3) `cortex entities
type=transcript limit=6` for session arc. Present a ~5-line orientation,
then ask what to work on. Do NOT call `cortex_boot`.
<!-- /target:cursor -->
<!-- target:* -->
## Response Store — NOT a failure signal

**Invariant**: `Large ... payload flagged / Stored as rs_XXXXX` is a **caching notice**, not an error. The MCP infrastructure stores large responses for deferred retrieval when they exceed ~128KB. The underlying write or call **succeeded**. Do NOT interpret this message as a failure or retry the call.

```
# This output means the call SUCCEEDED:
Large cortex knowledge payload flagged.
Size: 1536.2KB over 128.0KB threshold.
Stored as: rs_ea08ab (expires in 10 min).
```

**Verification before concluding failure** — ∀ write operations that return a flagged-payload response:

| Operation | Verify with |
|---|---|
| `cortex(tool="session_close", ...)` | `cortex(tool="entity_get", arguments='{"entity_id": "transcript:YYYY-MM-DD-HHmm"}')` — 200 + entity present = succeeded |
| `cortex(tool="assert", ...)` | `cortex(tool="entity_get", arguments='{"entity_id": "..."}')` — assertion appears in entity = succeeded |
| `fs(op="write", ...)` | `fs(op="read", ...)` same path — content present = succeeded |
| `agent_bus(tool="reply", ...)` | Response contains `turn_number` field = succeeded |

**Anti-pattern**: Retrying a write that already succeeded because the response was flagged produces duplicate entities, duplicate assertions, or duplicate thread turns. Always verify first.

For write-side verification, querying the written artifact directly (entity_get, fs read) is the canonical path — it confirms the durable write, not just the cached response payload.
<!-- /target:* -->
<!-- target:cursor -->
## MCP Defaults

MCP is enabled by default (`mcp=true`). `remote_mcp` is automatically enabled for Anthropic models (native `mcp_toolset` path) and uses the client-side gateway tool loop for all other providers. Agents should not override or reason about injection details.
<!-- /target:cursor -->
