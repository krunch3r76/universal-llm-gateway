# Friction review

Friction rows are **assertions** on `service:{name}` with claims like `[tool_error] …`.
They record tool/schema/boot gaps (F5 funnel).

**Critical split:** `friction()` = **observation log**. A fix cycle = **codified bug ticket**
via `team_dispatch(op=handoff)` + six-block implement packet. Operator says "dispatch /
address / fix the friction" → open the ticket; do not stop at logging.

Full transport matrix: `fs(cortex, agent-skills/consult-routing.md)` § Codified bug reports.

## When to read

- After `cortex(tool="friction", …)` when the defect needs a fix cycle (not log-only).
- Operator: "review frictions", "address the friction", "dispatch to fix friction {id}".
- Before picking up a `type:bug` agent-bus thread about tooling.
- Unexpected MCP/cortex tool failures (log via `friction` if needed, then ticket if actionable).

## Codified bug ticket (two-phase fix cycle)

A codified bug ticket routes in **two phases** — investigate first, execute second.
**Default heuristic:** a friction/bug filed → **assume the investigation tier (Phase 1)**
unless the operator says mechanical-only or a dense implement spec already exists.

| Phase | When (default) | Transport | Tier |
|---|---|---|---|
| **1 — Investigate + decide** | Root cause unknown, multi-file/protocol, design choice open, most `friction()` categories except operator-confirmed mechanical-only | `web-consult` (consult packet) OR `cursor-consult` (Opus IDE) | web-claude / Opus |
| **2 — Execute** | A dense implement spec exists OR operator confirms mechanical-only | `cursor-implement` (Composer 2.5 Fast) OR web Path A inline fix via `fs` | Composer / web |

| Initiator / seat | Phase 1 (investigate) | Phase 2 (execute) |
|------------------|-----------|-----------|
| **claude-cursor** (IDE) | `team_dispatch(op=handoff, role=cursor-consult, packet_path=…)` | `team_dispatch(op=handoff, role=cursor-implement, packet_path=tmp/reviews/<slug>-cursor-packet.md, …)` against the Phase-1 spec |
| **claude-web** | `team_dispatch(op=handoff, role=web-consult, packet_path=…)` — consult packet | web Path A inline fix via `fs`, or `team_dispatch(op=handoff, role=web-implement, packet_path=…)` (acceptance criteria required) |
| Operator names different transport | Obey operator or stop and ask — never substitute `agent_bus` for named `team_dispatch` | — |

**Lifecycle (order matters):**

1. **Phase 1 — Investigate + decide** — `cursor-consult`/`web-consult` consult packet;
   reproduce, trace root cause, inventory touch points, resolve design choice → dense spec
2. **Phase 2 — Execute** — `cursor-implement` (or web inline) against the Phase-1 spec; patch, verify, restart if substrate change
3. **Report** — bus closeout: root cause, paths, verification evidence
4. **Secondary findings** — issues found during investigation → labeled in closeout;
   spin separate friction/handoff if they need their own cycle

**NOT** a codified bug ticket: `agent_bus`-only thin ping; `friction()` without handoff when
fix is required; redesign/graph-walk **before** Phase 1 investigate.

**Anti-pattern (premature execute):** `cursor-implement` as the **first** dispatch on a bug that
still needs a touch-point inventory or design resolution (incident: friction 13571 → thread 1377,
superseded by `web-consult` investigation). Default to the investigation tier; reach for
`cursor-implement` only against a dense spec or an operator-confirmed mechanical-only scope.

Web handoff preflight (mandatory): `agent-guides/web-agent-orientation.md` § Handoff preflight.

## Lookup paths

### Cross-service (default)

```
cortex(tool="frictions", arguments='{"limit": 30}')
cortex(tool="frictions", arguments='{"category": "tool_error", "seeded_by": "claude-web", "limit": 20}')
```

### One service

```
cortex(tool="assertions", arguments='{"entity_id": "service:mcp-server", "filter": "tool_error", "superseded": false, "limit": 20}')
```

### Bus queue

```
agent_bus(tool="threads", arguments='{"tags": ["type:bug"], "status": "active"}')
```

Filing agents may post bus threads separately from `friction()` — use both queues.

## Close (after fix)

```
cortex(tool="friction_close", arguments='{"assertion_id": ID, "resolution_kind": "agent_skill:slug"}')
```

Valid `resolution_kind`: `agent_skill:{slug}`, `workflow:{slug}`, `todo:{slug}`, `superseded`, `wontfix`.

## Log new friction (observation only)

```
cortex(tool="friction", arguments='{"service": "mcp-server", "category": "tool_error", "note": "...", "agent": "claude-web"}')
```

Categories: `tool_error`, `tool_mismatch`, `tool_absent`, `schema_gap`, `boot_drift`,
`lesson_gap`, `lesson_conflict`, `stale_context`.

## Avoid

- Treating `friction()` as submitting a fix-cycle ticket.
- `cortex(tool="search", …)` for `[tool_error]` alone — prefer `frictions` / `assertions`.
- Scraping `review_status=staged` — friction tickets are normal service assertions.
