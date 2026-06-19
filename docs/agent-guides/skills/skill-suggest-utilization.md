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

### Loaded ledger (current contract)

**Landed today:** web agents **must pass `loaded[]`** on every `skill_suggest` call and
**maintain the list in working memory** across the session.

```python
LOADED = ["architecture-invariants", "ulg-architecture", "fs", …]  # grows as you fetch

skill_suggest(
    loaded=LOADED,
    conversation_context="Rich task context — any format you prefer (≤16k chars).",
    limit=8,          # optional; default 8
)
```

| Field | Rule |
|---|---|
| `loaded` | **Required** on MCP wire. Slugs already read this session (full or boot-critical sections). Server **also** merges `seat_preloaded` (boot/orientation inject slugs for web). Without listing a slug in `loaded`, Stage A may re-suggest it even after `fs` read. |
| `conversation_context` | Encode **your** read of the task: what you're doing, what seems relevant, what you already ruled in/out. Handoff excerpt, bus thread body, todo + ACs, bullets, or prose — any format, up to 16k chars. Do not compress to a one-liner. Omit only when probing empty context (returns `insufficient_context`). |
| `agent` | Omit on MCP when seat resolves from session; pass explicitly when resolution fails. |

**Partial reads count:** after `md_read` of boot-critical sections for a slug, append the
slug to `LOADED` before the next suggest — whole-body fetch is not required for dedup.

**Target contract (not landed):** `GET/POST /skills/session/…` registry so `fs` /
`GET /skills/body` fetches auto-register slugs and web agents pass `conversation_context`
only. Until that ships, **`loaded[]` is agent-maintained**.

**You reason; the tool returns delta.** At inflection points, form a judgment from boot +
task context + `LOADED`, then call `skill_suggest` to surface slugs you may have missed.
Accept or reject each hit on merits.

**Dispatch path (claude-web):** worker-hop relay uses **LLM reasoning over an extended candidate set** — Stargate `contract="light-bounded"` worker receives the full Stage-A candidate pool (`all_candidates`, score ≥ 0) and reorders/prunes via judgment. On timeout or error the relay falls back to Stage-A with `ranker_status="deterministic_fallback"`. Stage-B pipeline rerank (`skill-suggest-rank`, `SKILL_SUGGEST_RERANK_ENABLED`, default false) is a separate, server-env-gated experimental feature on the **direct** (non-worker) path — not agent-facing; do not attempt to toggle it from MCP calls.

### Batch preload (turn 1)

No single MCP tool reads skills **and** registers them. Use tiered `fs`:

| Pattern | When |
|---|---|
| `fs(op="read_multi", sandbox=…, paths=[…])` | Small index docs; one call per sandbox |
| `fs(op="md_list", …)` then `fs(op="md_read", section=…)` | Sectional playbooks (> ~6 KB) |
| Defer full read | `handoff-packet-authoring`, domain supersuits until trigger |

Full boot-lead patterns: `web-boot-lead.md`.

---

## How to load suggested bodies

1. Take `id`, `slug`, `source_uri`, and `digest` from each suggestion (never invent a path from slug alone).
2. Resolve body by `source_uri`:

| `source_uri` pattern | Load |
|---|---|
| `cortex://agent-skills/<slug>.md` | `fs(cortex, op=read, path="agent-skills/<slug>.md")` **or** `GET /skills/body?id=<id>&expected_digest=<digest>` |
| `workspaces://universal-llm-gateway/...` | `fs(workspaces, op=read, path="<repo-relative path>")` |
| Other / missing | `GET /skills/body?id=<id>&expected_digest=<digest>` (409 on digest drift) |

3. Fetch the body — append the slug to **`loaded[]`** before the next `skill_suggest`
   (session auto-registry from `fs` / `GET /skills/body` is **target**, not landed).

**Web / inline-only dispatch:** cortex skill **bodies** (`cortex-orientation`,
`cortex-provenance-discipline`) are auto-injected server-side (Track B Slice F + G3).
`architecture-invariants` / `ulg-architecture` are pair-injected on coding-dispatch paths
only — on web boot they are suggestible via `skill_suggest` when coding-relevant. Do not
hand-fetch auto-injected cortex bodies for every web generate unless the packet explicitly
requires a fresh read.

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
