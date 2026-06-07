---
name: consult-routing
description: On any consult, review, second opinion, handoff, team_dispatch, or frontier_dispatch request — read BEFORE choosing transport. Routes claude-web/claude-cursor via team_dispatch handoff, API roles via generate, thin pings via agent_bus.
---

# Consult Routing

**SOT:** `cortex://agent-skills/consult-routing.md` (universal boot skill — not repo SOT)

**Boot gate:** if `cortex_boot` ran this session, the briefing card **Consult routing gate**
is binding. Deep matrix: `projects/.cursor/rules/handoff-dispatchers.mdc` (project-level, above the repo).

Read the playbook before dispatching:

```
fs(sandbox="cortex", op="read", path="agent-skills/consult-routing.md")
```

## Handoff roster (`{platform}-{contract}`)

Only three roles. No `model=` or `handoff_contract` on the request.

| Role | Seat | Contract | Operator |
|------|------|----------|----------|
| `web-consult` | `claude-web` | consult | push bus message |
| `cursor-consult` | `claude-cursor` | consult | open IDE thread |
| `cursor-implement` | `claude-cursor` | implement | open IDE thread |

## Other paths

| Intent | Call |
|--------|------|
| Hands-off API consult | `team_dispatch(generate, role=reviewer\|…)` or `frontier_dispatch` |
| Thin ping | `agent_bus(post, to=claude-cursor, …)` — not handoff |
| Follow-up on open thread | `agent_bus(reply, …)` |
| Web-native todo (no cross-seat) | `Pick up todo:{slug}` |

Do not maintain a second long-form copy here or in `docs/agent-guides/skills/`.
