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

## Pass zoom-out duty

∀ `type:bug` agent-bus pickup or bug handoff to **claude-web** / **claude-cursor**: **zoom out in the pass** — widen beyond the filed symptom before Phase 1 completes or Phase 2 is declared done. SOT detail: `consult-routing.md` § Codified bug reports → Pass zoom-out duty.

| Pass phase | Mandatory zoom-out |
|---|---|
| **Phase 1** | Touch-point inventory; grep bug-class pattern service-wide; audit sibling call sites in the same subsystem |
| **Phase 2** | `[quality:bug-class-sweep]` before `declare_complete` — fix every instance or defer with `SF{n}` |
| **Closeout** | `## Secondary findings (labeled — separate cycle if pursued)` — `None observed.` if empty |

Per finding: disposition `verify-now` | `flag-deferred` | `spin-ticket`. Zoom-out runs inside the bug cycle; it does not block the primary fix with an open-ended redesign.

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

## Friction ID preflight (cursor — before `team_dispatch`)

**Incident:** friction 16849 / thread 1576 — operator said "friction 16737" (not a friction row);
Composer dispatched a densify consult anyway. Web burned a full investigate pass reconciling stale
packet claims vs live cortex (16724 on `service:universal-stargate`, task already `done`).

Before firing `team_dispatch(op=handoff)` on a friction arc:

1. **Resolve the ID** — friction rows are assertions on `service:{name}` with `[category]` claims.
   An assertion on `task:`/`decision:` is **not** a friction ID even if the number matches.
   ```
   cortex(tool="assertions", arguments='{"entity_id_prefix":"service:","filter":"[{category}]","limit":50}')
   ```
   Or direct lookup: confirm `entity_id` starts with `service:` and claim starts with `[`.

2. **Check disposition** — if the bound task is `workflow_state: done` or a resolution assertion
   already exists (`[resolved:…] Friction #N closed`), do **not** dispatch Phase-1 investigate;
   close the friction row or tell the operator the arc is complete.

3. **Stamp the packet** — corpus MUST include exact `entity_id` (e.g. `service:universal-stargate`,
   not `service:stargate`) and a **live-read timestamp** or instruct web to re-fetch state first.

4. **Operator intent** — if the operator's message is ambiguous (wrong ID, "typo", "don't dispatch"),
   **stop** — do not fire `team_dispatch`. Confirm the friction ID and intent in chat first.

## Void / recall (accidental dispatch)

When a handoff was fired by mistake (operator: "typo", "don't dispatch", "cancel thread N"):

**Cursor (dispatching seat)** — before web picks up:
```
agent_bus(tool="update_thread", arguments='{"thread":"<id>","tags":["dispatch:void"],"from_agent":"cursor"}')
agent_bus(tool="close", arguments='{"thread":"<id>","summary":"Void: accidental dispatch — operator typo. No work required."}')
```

**Web (receiving seat)** — turn-1 pickup gate (before loading packet / tier check / investigate):
- Thread tag `dispatch:void` → close immediately, one-line ack, **no** packet read, **no** findings.
- Turn body or operator chat contains void/recall/typo for this thread → same.
- Corpus "open friction" contradicts live `frictions`/`assertions` → **trust live state**, note
  stale packet in closeout, do not spend a pass reconciling unless operator confirms investigate.

Log the misfire: `cortex(tool="friction", service="agent-bus", category="tool_error", ...)`.

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
`lesson_gap`, `lesson_conflict`, `stale_context`, `doc_drift`, `protocol`.

## Avoid

- Fixing only the filed symptom without pass zoom-out (no touch-point sweep, no labeled secondary findings).
- Treating `friction()` as submitting a fix-cycle ticket.
- `cortex(tool="search", …)` for `[tool_error]` alone — prefer `frictions` / `assertions`.
- Scraping `review_status=staged` — friction tickets are normal service assertions.
