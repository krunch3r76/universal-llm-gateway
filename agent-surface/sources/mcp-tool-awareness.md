<!-- frontmatter:cursor
description: MCP tool calling convention for Cursor — CallMcpTool vortex-code/vortex-life syntax, fs markdown ops (md_read/md_replace/md_append), sandbox routing (cortex/context/workspaces), response-store flagged-payload semantics. Load before invoking any MCP tool (cortex, fs, agent_bus, observability, rag, pipeline, dispatch, team_dispatch).
alwaysApply: false
-->
<!-- target:cursor -->
# MCP Tool Awareness (Cursor)

## Calling Convention

Cursor has **two** vortex MCP bridges (mcp.json keys = CallMcpTool `server=` ids):

| Server id | Mount | Use |
|---|---|---|
| `vortex-code` | `/mcp/code` | Default coding seat — cortex, team_dispatch, manage, … |
| `vortex-life` | `/mcp/life` | Life-lead only — thin life surface |

Legacy monolithic `user-vortex` is **retired**. Prefer `vortex-code` unless life-leading.

**Cursor harness note:** mcp.json keys are `vortex-code` / `vortex-life`. The live CallMcpTool
`server=` id may appear as `user-vortex-code` / `user-vortex-life` (Cursor `user-` prefix on
configured servers). Match the session catalog; do not invent a bare `user-vortex`.

```
CallMcpTool(server="vortex-code", toolName="<tool>", arguments={...})
# if catalog lists user-vortex-code, use that string instead
```

When rules or docs use shorthand like `fs(sandbox="cortex", op="read", path="...")`,
translate to `CallMcpTool(server="vortex-code", toolName="fs", arguments={"sandbox": "cortex", "op": "read", "path": "..."})`.

**Dispatch-style tools** (`cortex`, `agent_bus`, `dispatch`, `rag`): the outer
`CallMcpTool` envelope is an object, but the inner `arguments` field MUST be a
**JSON string**, not a nested object. See skill `dispatch-shape`.

```
CallMcpTool(server="vortex-code", toolName="agent_bus", arguments={
  "tool": "fetch",
  "arguments": "{\"thread\": \"111\", \"last\": 3, \"compact\": true}"
})
```
<!-- /target:cursor -->
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
<!-- target:* -->
## Descriptor Reads — Stub-First (context discipline)

**Do NOT full-`Read` `mcps/**/tools/*.json` to learn an op/param.** A descriptor is one
giant `description` blob (cortex.json ≈ 15.7k chars); one read pulls ~40 ops' worth of
prose. Cached descriptors can also be STALE (e.g. `team_dispatch.json` lacks `handoff`/
`contract`/`source_ref` the server ships) — **live `mcp_get_tools`/catalog is authoritative**.

Order (stop at the first that answers):
1. **Stub/SOT** — `cortex-essentials`, `handoff-dispatchers`+`dispatch-workflow`, `fs`/this rule, `agent-bus-discipline`, `tool-reference` §rag.
2. **`mcp_get_tools` / grep** the targeted field (`Grep -C 3-5`) — never full-read for one field.
3. **Cached descriptor — with a freshness check** (cross-check the live catalog before trusting an absence).
4. **Full read — once per novel tool per session**, and note why.

Full discipline + "already covered" map: `mcp-descriptor-read-discipline-stub.mdc`.
<!-- /target:* -->
<!-- target:cursor -->
## Continue Mode (Transcript Pickup)

When the opening message contains `transcript:cursor-YYYY-MM-DD-HHmm`, run
three parallel calls: (1) `fs md_read` the session summary section, (2)
`agent_bus fetch thread=480` for activity journal, (3) `cortex entities
type=transcript limit=6` for session arc. Present a ~5-line orientation,
then ask what to work on. Do NOT call `cortex_brief`.
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

MCP client tools are governed by the single `mcp` boolean (default on for tool-capable families; `false` forces inline-only). Remote-connector vs client-side-loop selection is internal and card-derived — not a caller parameter. Server-side provider built-ins are governed independently by the optional `server_tools` knob (omit = ALL; `false` suppresses card-derived built-ins). Agents should not reason about injection details.
<!-- /target:cursor -->
