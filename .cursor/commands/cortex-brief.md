Pull a continuity briefing for this Cursor session (local TIER-2 stubs +
`cortex_brief`). Use when you need cortex/agent-bus/team-collab context for the
session — e.g. before posting to agent-bus, dispatching to frontier, working on
Cortex entities, or running session-close.

Default Cursor sessions are intentionally code-focused — the cortex/team stack
is **not** loaded by default. This command loads local TIER-2 stubs and pulls
`cortex_brief` (briefing card + `session_id` mint). It does **not** “boot into”
or inhabit Cortex.

---

## Instructions

### Step 1: TIER-2 rule reference (fetch on demand — do NOT pre-load)

These rules are navigable by section. **Do not read them in full at brief time.**
Use `fs(sandbox="workspaces", op="md_list", path="<repo-prefix>/<file>.mdc")` for
the TOC, then `md_read` with `section=<heading>` to load only the block you need.

| Rule | Repo path | Load when |
|---|---|---|
| `cursor-environment_ws.mdc` | `universal-llm-gateway/.cursor/rules/` | orienting to the IDE worksite |
| `cursor-birth_ws.mdc` (Claude) / `cursor-orion-birth_ws.mdc` (GPT) / `cursor-forge-birth_ws.mdc` (Grok) | `universal-llm-gateway/.cursor/rules/` | identity/persona questions arise |
| `mcp-tool-awareness_ws.mdc` | `universal-llm-gateway/.cursor/rules/` | before any non-trivial MCP call |
| `awareness_ws.mdc` | `universal-llm-gateway/.cursor/rules/` | before touching todos or vision |
| `agent-skills_ws.mdc` | `universal-llm-gateway/.cursor/rules/` | before looking up a skill |
| `mcp-debugging-ux_ws.mdc` | `universal-llm-gateway/.cursor/rules/` | before event/observability debug work |
| `cortex-essentials.mdc` | `projects/.cursor/rules/` | before any cortex CRUD call |
| `agent-identity-signoff.mdc` | `projects/.cursor/rules/` | before agent-bus/cortex closures or provenance writes |

Do NOT load on this command (load on their own triggers):
- `architecture-handoff-protocol.mdc` and `handoff-dispatchers.mdc` — load before
  any `team_dispatch` or `frontier_dispatch` call (or via `/diff-review`,
  `/review-plan`, Plan-mode consults, `/consult-architect`). Navigate via
  md_list → md_read to load only the block/dispatcher you need.
- `session-close.mdc` and the two `session-transcript-fidelity*.mdc` files —
  load on `/session-end` only.
- `workflow/plan-mode.mdc` — load on Plan-mode entry.
- `workflow/subagent-strategy.mdc` — load before launching a Task subagent.

### Step 2: Call cortex_brief

```
CallMcpTool(server="vortex-code", toolName="cortex_brief",
            arguments={"agent": "gemini-cursor"})
```

For a continuation brief (resuming a prior session), pass `transcript_id`:

```
CallMcpTool(server="vortex-code", toolName="cortex_brief",
            arguments={"agent": "gemini-cursor",
                       "transcript_id": "gemini-cursor-YYYY-MM-DD-HHMM"})
```

### Step 3: Internalize the briefing

Digest the briefing card. Surface to the user (briefly):
- **Deadlines** — time-sensitive items shaping priorities
- **Open todos / investigations** — what's in flight
- **Open tasks (arcs)** — grouping containers (`task_candidates`, Tasks section)
- **Unread agent-bus turns** — count + senders
- **Recent sessions** — continuity pickup if any

Hold `session_id` from the `cortex_brief` response for use with `supersede`,
`edge_create`, `relationship_create`, and `session_close`.

### Step 4: Mark briefing complete

This session now has the TIER-2 stubs loaded and a live `session_id`. Subsequent
invocations of `/session-end`, `/cortex-brief` etc. should NOT re-load these
stubs — they're already in context.

---

## Variants

| Invocation | Behavior |
|---|---|
| `/cortex-brief` | Fresh brief — load TIER-2 stubs + call `cortex_brief` |
| `/cortex-brief {transcript-id}` | Continuation brief — pass `transcript_id` to `cortex_brief` |

---

## Why this command exists

Default Cursor sessions only load engineering invariants needed for code work
(core_ws, system, modelid, transport, etc.). The cortex/agent-bus/team-collab
stack — persona, awareness, MCP conventions, endpoint-provenance rules — loads on demand
via this command (or implicitly via `/session-end` first invocation). Handoff/dispatch
rules load separately, only when a dispatch is imminent (see the "Do NOT load" block above).

This keeps default sessions lean and code-focused; you opt into the collaboration
context when you need it — via stubs + briefing card, not by inhabiting Cortex.
