---
name: skill-suggest-utilization
description: "Optional in-session skill delta-ranking via MCP skill_suggest — native discovery is primary (boot index + description-gated stubs). Load when you need explicit relevance ranking beyond the resident index."
---

# Skill suggest — optional delta-ranking

North star: optimal skill utilization under `project:agent-workflow-parity`.

**Discovery is native.** All seats discover skills via resident boot index + description-gated triggers — Cursor `<available_skills>` / `.cursor/skills/<slug>/SKILL.md` stubs + `.mdc` rules; claude-web/API boot manifest + Claude.ai customization panel. `skill_suggest` is retained as an **optional** explicit delta-ranking call when you need ranked slugs beyond what native discovery surfaced.

## Surface split

| Seat | Primary discovery | Optional delta-ranking |
|---|---|---|
| Cursor IDE | Native `<available_skills>` + `.cursor/skills/<slug>/SKILL.md` stubs + description-gated `.mdc` rules | `skill_suggest(loaded=[], conversation_context=…)` |
| claude-web / API | Boot index (`GET /boot-skills`) + description triggers | Same optional call |

## Native discovery (primary — all seats)

1. Match task to `<available_skills>` / boot manifest by description.
2. Read `.cursor/skills/<slug>/SKILL.md` stub (defers to shared SOT).
3. Load description-gated rules when matched; use `md_read` for relevant section, not whole file.
4. Todo `required_skills` + `entity_get` → `source_uri` when known upfront.

## Optional `skill_suggest` (delta-ranking only)

Call only when native discovery left gaps and you want server-ranked slugs not yet in `loaded[]`. Not mandatory at inflection points.

Bind precondition: `skill_suggest` is deferred server-primary. If advertised but not callable, surface it with broad-keyword `tool_search("skill suggest skills loaded delta")`; do **not** use bare exact-name `tool_search("skill_suggest")`, which searches overflow-only and may return 0.

| Trigger (optional) | Example |
|---|---|
| Context shift beyond resident index | new domain after native stubs exhausted |
| Confirmatory check | verify no missed slugs before handoff |

Web-consult/web-implement pickup: load packet `<invariants>` skills first via `source_uri`; optional `skill_suggest` is confirmatory only — hits for already-wired slugs confirm, not discover.

## Call contract (when used)

Maintain and pass `loaded[]` on every call.

```python
LOADED = ["architecture-invariants", "ulg-architecture", "fs"]
skill_suggest(loaded=LOADED, conversation_context="Rich task context ≤16k chars", limit=8)
```

| Field | Rule |
|---|---|
| `loaded` | Required on MCP wire. Slugs read this session. Server also merges `seat_preloaded`. |
| `conversation_context` | Task read: objective, relevant facts, ruled-in/out skills. ≤16k. |
| `agent` | Omit when session resolves seat. |

Partial reads count: after `md_read` of relevant sections, append slug to `LOADED`.

Dispatch path: claude-web worker-hop relay uses LLM reasoning; timeout/error falls back to Stage A. Stage-B rerank is server-env-gated; do not toggle from MCP.

## Loading suggested bodies

1. Use suggestion `id`, `slug`, `source_uri`, `digest`. Never invent path from slug alone.
2. Resolve by `source_uri`:

| Source | Load |
|---|---|
| `cortex://agent-skills/<slug>.md` | `fs(cortex, read, agent-skills/<slug>.md)` or `GET /skills/body?id=<id>&expected_digest=<digest>` |
| `workspaces://universal-llm-gateway/...` | `fs(workspaces, read, <repo-relative path>)` |
| other/missing | `GET /skills/body?id=<id>&expected_digest=<digest>`; 409 on digest drift |

3. Append slug to `loaded[]` before next suggest.

## Boot vs in-session

| Layer | Route/tool | Role |
|---|---|---|
| Boot index | `GET /boot-skills?for_agent=` | seat-filtered manifest (native) |
| HTTP index | `GET /skills?for_agent=&layer=` | skills/rules/all discovery envelope |
| Optional delta | `skill_suggest` | ranked slugs not in `loaded` |
| Body | `GET /skills/body` or `fs` | pull on demand |

Boot `related_skills` attribute is orientation; native stubs + manifest are the discovery path.

## Packet authors

- Dynamic task skills: use todo `required_skills` + `source_uri` before writing `<corpus>` `fs` lines; optional `skill_suggest` for confirmatory delta.
- Known skill by entity id: use `handoff-packet-authoring.md` § Skill URI Resolution.

Spec: `tasks/specs/skill-suggest-mcp-tool.md`. Todo: `todo:skill-suggest-mcp-tool`.
