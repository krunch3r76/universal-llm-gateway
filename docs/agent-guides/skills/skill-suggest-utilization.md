# Skill suggest — in-session discovery

North star: optimal skill utilization under `project:agent-workflow-parity`.

## Surface split

| Seat | Discovery path |
|---|---|
| Cursor IDE | Native `<available_skills>` + `.cursor/skills/<slug>/SKILL.md` stubs + description-gated `.mdc` rules. MCP `skill_suggest` callable at inflection points for delta discovery. |
| claude-web / API | MCP `skill_suggest` at inflection points. Boot index: `GET /boot-skills`. |

## Cursor IDE

1. Match task to `<available_skills>` by description.
2. Read `.cursor/skills/<slug>/SKILL.md` (stub defers to shared SOT).
3. Load description-gated rules when matched; use `md_read` for relevant section, not whole file.
4. Call `skill_suggest` at inflection points (domain shift, before consult/handoff/implement, after friction triage) for deltas beyond resident index. If unbound, `tool_search("skill_suggest")` first.

Todo `required_skills` + `entity_get` → `source_uri` still applies when known upfront.

## Web/API — when to call

Bind precondition: `skill_suggest` is deferred server-primary. If advertised but not callable, surface it with broad-keyword `tool_search("skill suggest skills loaded delta")`; do **not** use bare exact-name `tool_search("skill_suggest")`, which searches overflow-only and may return 0. Then call `skill_suggest` directly.

Call at conversational inflection points, not every turn:

| Trigger | Example |
|---|---|
| Task/domain shift | “add an MCP tool”, “debug event bus” |
| Before consult / handoff / implement | dispatch lane chosen; task-specific skills needed |
| Before surface change | `canonical.yaml`, new REST route, pipeline add |
| After friction / defect triage | category suggests playbook |
| Mid-arc pivot | operator reframes north star or todo |

Do not call when boot-resident skills already cover the turn.

Carve-out: web-consult/web-implement pickup ⇒ step-1 `skill_suggest` mandatory, confirmatory delta only. Load packet `<invariants>` skills first; hits for already-wired slugs confirm, not discover. Enrich event reports `skills_already_wired` vs `skills_added`.

## Call contract

Web agents must maintain and pass `loaded[]` on every call.

```python
LOADED = ["architecture-invariants", "ulg-architecture", "fs"]
skill_suggest(loaded=LOADED, conversation_context="Rich task context ≤16k chars", limit=8)
```

| Field | Rule |
|---|---|
| `loaded` | Required on MCP wire. Slugs read this session, full body or boot-critical sections. Server also merges `seat_preloaded`; if you omit a read slug, Stage A may re-suggest it. |
| `conversation_context` | Your task read: objective, relevant facts, ruled-in/out skills, handoff/todo/AC excerpts. Any format ≤16k. Omit only for empty-context probe. |
| `agent` | Omit when session resolves seat; pass explicitly only on resolution failure. |

Partial reads count: after `md_read` of relevant sections, append slug to `LOADED`. Target auto-session registry is not landed; `loaded[]` remains agent-maintained.

Reason first, then use the tool for deltas. Accept/reject suggestions on merits.

Dispatch path: claude-web worker-hop relay uses LLM reasoning over extended candidate set; timeout/error falls back to Stage A with `ranker_status="deterministic_fallback"`. Stage-B pipeline rerank is server-env-gated experimental direct path; do not toggle from MCP.

## Batch preload

No tool both reads skill bodies and registers them. Use tiered `fs`:

| Pattern | Use |
|---|---|
| `fs(read_multi)` | small index docs; one call per sandbox |
| `md_list` → `md_read(section=…)` | sectional playbooks >~6 KB |
| Defer full read | large/domain supersuits until trigger |

## Loading suggested bodies

1. Use suggestion `id`, `slug`, `source_uri`, `digest`. Never invent path from slug alone.
2. Resolve by `source_uri`:

| Source | Load |
|---|---|
| `cortex://agent-skills/<slug>.md` | `fs(cortex, read, agent-skills/<slug>.md)` or `GET /skills/body?id=<id>&expected_digest=<digest>` |
| `workspaces://universal-llm-gateway/...` | `fs(workspaces, read, <repo-relative path>)` |
| other/missing | `GET /skills/body?id=<id>&expected_digest=<digest>`; 409 on digest drift |

3. Append slug to `loaded[]` before next suggest.

Web/inline-only dispatch: cortex skill bodies (`cortex-orientation`, `cortex-provenance-discipline`) auto-inject server-side. `architecture-invariants` / `ulg-architecture` pair-inject on coding-dispatch paths only; on web boot they remain suggestible when coding-relevant. Do not hand-fetch auto-injected bodies unless packet requires fresh read.

## Boot vs in-session

| Layer | Route/tool | Role |
|---|---|---|
| Boot index | `GET /boot-skills?for_agent=` | seat-filtered manifest |
| HTTP index | `GET /skills?for_agent=&layer=` | skills/rules/all discovery envelope |
| In-session delta | `skill_suggest` | ranked slugs not in `loaded` |
| Body | `GET /skills/body` or `fs` | pull on demand |

Graph-backed discovery: when parent skill is in `loaded[]`, Stage A reads parent `related_skills` attribute (1-hop only; no traversal/closure) and boosts matching candidates. Boot cards expose the same attribute. Prefer `skill_suggest` for in-session delta; boot `related_skills` is orientation.

## Packet authors

- Dynamic task skills: run `skill_suggest` or use todo `required_skills` + `source_uri` before writing `<corpus>` `fs` lines.
- Static web-consult invariants: omit redundant `fs` lines for auto-injected invariant bodies unless verifying digest drift.
- Known skill by entity id: use `handoff-packet-authoring.md` § Skill URI Resolution.

Spec: `tasks/specs/skill-suggest-mcp-tool.md`. Todo: `todo:skill-suggest-mcp-tool`.
