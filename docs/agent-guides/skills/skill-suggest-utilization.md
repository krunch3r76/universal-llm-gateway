# Skill suggest — in-session discovery (primary path)

**North star:** optimal skill utilization under `project:agent-workflow-parity`.

**Feedback loop (web ↔ Cursor):** thread **1876** (and follow-ups) probe claude-web on
**available / effective / usable** skills; Cursor lands interface changes (boot orientation,
`skill_suggest` surface, adoption gates) from the gap table — then re-probe.

**Cursor:** `skill-suggest-utilization_ws.mdc` is **always-applied** — the call invariant is
resident every turn; this doc is the full playbook.

**Live surface:** MCP `skill_suggest` (first-class) → cortex-api `POST /skills/suggest`.
Index + bodies: `GET /skills`, `GET /skills/body`. Boot index unchanged: `GET /boot-skills`.

---

## When to call `skill_suggest`

Call at **conversational inflection points**, not every turn:

| Trigger | Example context snippet |
|---|---|
| Task/domain shift | "Now add an MCP tool…", "debug the event bus…" |
| Before consult / handoff / implement | Dispatch lane chosen; need task-specific skills |
| Before surface change | `canonical.yaml`, new REST route, pipeline add |
| After friction / defect triage | Category suggests an existing playbook |
| Mid-arc pivot | Operator reframes north star or active todo |

**Do not** call when boot-resident skills already cover the turn (e.g. routine `cortex`
entity_get, session-close with skills loaded).

---

## What to pass

```python
skill_suggest(
    loaded=["consult-routing", "dispatch-shape", "architecture-invariants"],
    conversation_context="Bounded 1–3 sentence task summary (≤16k chars).",
    limit=8,          # optional; default 8
    rerank=False,     # optional; default OFF — deterministic Stage A only
)
```

| Field | Rule |
|---|---|
| `loaded` | **Required.** Slugs, `agent_skill:<slug>`, or paths already in context (include boot-loaded + manually fetched). Duplicates tolerated. |
| `conversation_context` | Bounded turn/task summary; omit only when probing with empty context (returns insufficient-context path). |
| `agent` | Omit on MCP when seat resolves from session; pass explicitly when resolution fails. |

### `loaded[]` is caller-owned (session endpoints deferred)

Server-side session loaded-state (`GET/POST /skills/session/…`) is **not** implemented.
cortex-api has no conversation buffer; `loaded` must reflect what the agent already holds.
Track slugs as you fetch bodies; refresh `loaded` after each load.

---

## How to load suggested bodies

1. Take `id`, `slug`, `source_uri`, and `digest` from each suggestion (never invent a path from slug alone).
2. Resolve body by `source_uri`:

| `source_uri` pattern | Load |
|---|---|
| `cortex://agent-skills/<slug>.md` | `fs(cortex, op=read, path="agent-skills/<slug>.md")` **or** `GET /skills/body?id=<id>&expected_digest=<digest>` |
| `workspaces://universal-llm-gateway/...` | `fs(workspaces, op=read, path="<repo-relative path>")` |
| Other / missing | `GET /skills/body?id=<id>&expected_digest=<digest>` (409 on digest drift) |

3. Append the slug to your mental/session `loaded` list before the next `skill_suggest` call.

**Web / inline-only dispatch:** invariant skill **bodies** (`architecture-invariants`,
`ulg-architecture`) are auto-injected server-side (Track B Slice F + G3). Do not hand-fetch
them for every web generate unless the packet explicitly requires a fresh read.

---

## Boot vs in-session (do not conflate)

| Layer | Route / tool | Role |
|---|---|---|
| Boot index | `GET /boot-skills?for_agent=` | Seat-filtered manifest at session open (1637 trim) |
| HTTP index (non-boot) | `GET /skills?for_agent=&layer=` | Discovery envelope (skills, rules, or all) |
| In-session delta | `skill_suggest` | Ranked slugs **not** in `loaded` |
| Body | `GET /skills/body` or `fs` | Pull-on-demand only |

---

## Packet authors (handoff)

- **Dynamic task skills:** run `skill_suggest` (or rely on todo `required_skills` + `source_uri`
  lookup) before writing `<corpus>` `fs` lines.
- **Static invariants on web consult:** omit redundant `fs` lines for auto-injected invariant
  bodies unless verifying digest drift.
- **URI resolution table:** see `handoff-packet-authoring.md` § Skill URI Resolution when a
  skill is already known by entity id (not discovered via suggest).

Spec: `tasks/specs/skill-suggest-mcp-tool.md` · Todo: `todo:skill-suggest-mcp-tool` (implemented).
