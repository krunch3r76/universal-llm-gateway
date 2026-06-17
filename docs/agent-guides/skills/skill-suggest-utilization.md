# Skill suggest — in-session discovery

**North star:** optimal skill utilization under `project:agent-workflow-parity`.

## Surface split

| Seat | Discovery path |
|---|---|
| **Cursor IDE** | Native `<available_skills>` index + Read `.cursor/skills/<slug>/SKILL.md` stubs (defer to canonical/cortex). Rules (`.mdc`) carry always vs description-gated invariants. **¬** MCP `skill_suggest`. Resident: `skill-suggest-utilization_ws.mdc` (~10 lines). |
| **claude-web / API** | MCP `skill_suggest` at inflection points (§ Web below). Boot index: `GET /boot-skills`. |

---

## Cursor IDE (native skill surface)

1. Match task to a skill in `<available_skills>` by `description`.
2. Read `.cursor/skills/<slug>/SKILL.md` (stub → `fs` deferral to shared SOT).
3. Load description-gated **rules** when the task matches their `description` (e.g. dispatch/handoff rules — load one `##` section via md_read, not whole file).
4. **Do not** call `skill_suggest` — adds MCP latency; Cursor already indexes registered skills.

Todo `required_skills` + `entity_get` → `source_uri` still applies when the skill set is known upfront.

---

## Web — when to call `skill_suggest`

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

**Carve-out — web-consult densify pickup.** When picking up a `web-consult` /
`web-implement` handoff, step-1 `skill_suggest` is **mandatory** and is the one
exception to the boot-resident rule above. On this path it is **confirmatory
delta, not primary discovery**: the packet `<invariants>` and bus pointer have
already *wired* the dispatch-delegation skills (`consult-routing`,
`dispatch-shape`, `handoff-packet-authoring`). Load the packet `<invariants>`
skills first; treat `skill_suggest` hits for already-wired slugs as
confirmation, and do not narrate them as fresh discoveries. The enrich event
(`frontier.handoff.packet.enriched`) reports `skills_already_wired` vs
`skills_added` so the overlap is explicit.

---

## What to pass

**Web (claude-web):** pass **`conversation_context` only**. The skill server maintains
the session loaded set — boot auto-inject (returned as `seat_preloaded`) plus slugs
whose bodies were fetched this session via `GET /skills/body` or `fs`. Do **not**
manually build or pass a `loaded[]` ledger.

```python
skill_suggest(
    conversation_context="Rich task context — any format you prefer (≤16k chars).",
    limit=8,          # optional; default 8
)
```

| Field | Rule |
|---|---|
| `conversation_context` | Encode **your** read of the task: what you're doing, what seems relevant, what you already ruled in/out. Handoff excerpt, bus thread body, todo + ACs, bullets, or prose — any format, up to 16k chars. Do not compress to a one-liner. Omit only when probing empty context (returns `insufficient_context`). |
| `loaded` | **Server-owned on web** — session registry on the skill server, not agent memory. |
| `agent` | Omit on MCP when seat resolves from session; pass explicitly when resolution fails. |

**You reason; the tool returns delta.** At inflection points, form a judgment from boot +
task context, then call `skill_suggest` to surface slugs you may have missed. Accept or
reject each hit on merits.

**Dispatch path (claude-web):** worker-hop relay is **Stage-A + agent judgment only**.
Stage-B rerank (`skill-suggest-rank` pipeline) is **server-env-gated experimental-off**
(`SKILL_SUGGEST_RERANK_ENABLED`, default false) — not agent-facing; do not attempt to
toggle it from MCP calls.

**Implementation note:** full session registry (`GET/POST /skills/session/…`) is the
target contract; today the server merges **web seat preload** into the loaded set
automatically (`seat_preloaded` in the response). Wire-level `loaded` may still appear
on the MCP schema until session binding is complete — web agents must not treat it as
their bookkeeping responsibility.

---

## How to load suggested bodies

1. Take `id`, `slug`, `source_uri`, and `digest` from each suggestion (never invent a path from slug alone).
2. Resolve body by `source_uri`:

| `source_uri` pattern | Load |
|---|---|
| `cortex://agent-skills/<slug>.md` | `fs(cortex, op=read, path="agent-skills/<slug>.md")` **or** `GET /skills/body?id=<id>&expected_digest=<digest>` |
| `workspaces://universal-llm-gateway/...` | `fs(workspaces, op=read, path="<repo-relative path>")` |
| Other / missing | `GET /skills/body?id=<id>&expected_digest=<digest>` (409 on digest drift) |

3. Fetch the body — the skill server records the slug as loaded for your session when
   you pull via `GET /skills/body` or `fs` (web agents do not re-pass it on the next suggest).

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

### Graph-backed discovery (Slice B)

When a parent skill is already in `loaded[]`, Stage A reads that parent's
`related_skills` attribute (1-hop only — no relationship traversal, no transitive
closure) and applies a deterministic score boost to matching candidates. This
surfaces companions declared at ingest/backfill time without parsing SKILL.md
bodies or calling `cortex(relationships)`.

Boot cards expose the same attribute: `GET /boot-skills` includes
`related_skills: []` per skill when populated. Prefer `skill_suggest` for
in-session delta; use boot `related_skills` for orientation at session open.

---

## Packet authors (handoff)

- **Dynamic task skills:** run `skill_suggest` (or rely on todo `required_skills` + `source_uri`
  lookup) before writing `<corpus>` `fs` lines.
- **Static invariants on web consult:** omit redundant `fs` lines for auto-injected invariant
  bodies unless verifying digest drift.
- **URI resolution table:** see `handoff-packet-authoring.md` § Skill URI Resolution when a
  skill is already known by entity id (not discovered via suggest).

Spec: `tasks/specs/skill-suggest-mcp-tool.md` · Todo: `todo:skill-suggest-mcp-tool` (implemented).
