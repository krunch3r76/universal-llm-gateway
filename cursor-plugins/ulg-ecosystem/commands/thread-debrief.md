Review recently closed agent-bus threads and surface durable artifact updates.

## Instructions

### 1. Fetch Recently Closed Threads

```
CallMcpTool(server="user-vortex", toolName="agent_bus_threads", arguments={
  "status": "closed"
})
```

### 2. Identify Candidates

From the closed threads list, select threads that:
- Have `unread_count > 0` (protocol violation — closing should mark all read), OR
- Were explicitly named by the user (e.g. `/thread-debrief 027`)

If no candidates found: report "No recently closed threads to debrief."

**Note**: The thread closure protocol auto-marks all turns read when closing
(see `/agent-bus` Thread Closure Protocol). Closed threads with `unread_count > 0`
indicate a thread was closed without following the protocol — possibly via direct
API call or older tooling. Treat these as genuine unread content to review.

### 3. Load Thread Context

**Preferred**: Read `tmp/thread-debrief-log.md` (built incrementally by
`/agent-bus` step 4). Filter entries matching candidate thread IDs.
This contains structured summaries of every turn already processed —
files touched, decisions made, follow-ups identified.

**Fallback** (thread not in debrief log, or `--all` with old threads):
Fetch full thread history from the bus:

```
CallMcpTool(server="user-vortex", toolName="agent_bus_fetch", arguments={
  "thread": "THREAD_ID",
  "last": 50,
  "mark_read": false,
  "compact": false
})
```

Use **mark_read: false** so turns stay unacknowledged and can be PATCHed or superseded (e.g. move to another thread). Threads may reappear as candidates until the user marks them read or closes them elsewhere.

### 4. Review Against Artifact Checklist

For each thread, analyze the conversation and determine if updates are warranted
to any of the following. Report findings per thread as a checklist.

| Artifact | Location | Trigger |
|---|---|---|
| **Cursor rules** | `.cursor/rules/` | New invariant, convention, or policy established |
| **Cursor commands** | `.cursor/commands/` | New workflow pattern or command gap identified |
| **Lessons** | `tasks/lessons/` | Mistake corrected, non-obvious gotcha discovered |
| **Architecture docs** | `docs/architecture/` | Subsystem behavior changed, new component added |
| **Service READMEs** | `services/*/README.md` | API surface changed, new endpoints, config changes |
| **MCP docs** | `docs/mcp-integration.md` | New tools, changed tool signatures, new access patterns |
| **Cortex assertions** | via cortex-api | Significant decisions, new entities, status changes (note: `/agent-bus` step 5 now seeds assertions inline — check if already seeded before duplicating) |
| **Event contracts** | `docs/event-contracts.md` | New signals, changed payloads |
| **Vision/roadmap** | `docs/VISION.md` | Strategic direction shift |
| **Skills** | `.cursor/skills/` | New repeatable multi-step workflow emerged |
| **Todos** | cortex-api `/todos` | Follow-up work identified but not yet tracked |

### 5. Report

For each thread, output:

```
### Thread {ID}: {slug}
Summary: {thread summary}

**Updates warranted:**
- [ ] {artifact type} — {location} — {what to add/change and why}
- [ ] ...

**No update needed:** {artifact types checked with no findings}
```

If a thread produced no artifact updates: "No durable artifact updates needed."

### 6. Act (with approval)

After presenting findings across all threads, ask the user which updates to
proceed with. Do NOT make changes without approval.

## Variants

| Invocation | Behavior |
|---|---|
| `/thread-debrief` | Review all closed threads with unread turns |
| `/thread-debrief {thread}` | Review a specific closed thread by ID |
| `/thread-debrief --all` | Review ALL closed threads (regardless of unread status) |

### `/thread-debrief {thread}`

Override candidate selection: fetch and review only the named thread,
regardless of unread status.

### `/thread-debrief --all`

Override candidate selection: review every closed thread. Useful for
periodic comprehensive review.
