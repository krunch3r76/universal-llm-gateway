---
name: external-prose-rag-inject
description: >-
  Cursor/orchestrator only — assemble writing-sample injects in rag-context wire
  shape and activate frontier seats; ¬ claude.ai Customize surface.
trigger_match_terms:
  - external-prose-rag-inject
  - pseudo-RAG
  - rag-context inject
  - poetry_context
  - writing_context inject
  - assemble writing samples
---

# External prose — RAG inject (orchestrator)

`cursor ∨ orchestrator assembling writing_context for a frontier/CDP rewrite ⇒ Use this skill`.
`surface_class=cursor_only` — engineering stays behind the web intent barrier.
Web seats consume **retrieved writing samples** via `external-prose-decompose-recompose`
(intent only); they ¬ see pipeline names or wire recipes.

## Invariant

`pseudo_RAG ⇒ emulate(rag-context) ∧ activate(read_as_RAG)`.
Soft “style reference / if relevant” alone fails when GT/must-keeps compete.

**Fail-closed activation:** a URI/fs read of a pack does **not** count as
activated. Activated iff a pasted `rag(op="search")` payload is in-message
(or the dispatcher materializes `rag(op="search", mapped=true)` into the
message before the seat runs). Ambient absorption of cited URIs is the
failure mode this path exists to prevent.

## Preferred path — mapped search

```text
rag(op="list_mapped")   # discover keyed (scope, query) pairs — entries omit pack URIs
rag(op="search", query=<index query>, scope=<index scope>, mapped=true)
```

Index SOT: `config/mcp/rag_mapped_index.yaml` (exact normalized `(scope, query)`
→ durable pack URI). Hit returns the identical search envelope as live
rag-context (`status` / `pipeline` / `context` / `retrieval`); miss falls
through to live retrieval. Packs are pure `context` bodies — no frontmatter.

## Authoring checklist (mirror)

Primary SOT: `cortex://notes/system/briefs/rag-mapped-search-agent-brief.md`

1. Pure `context` body only (`[Source:…]` / `[Body evidence]` — no frontmatter)
2. Add index entry with exact `scope` + `query` + `uri`
3. Restart mcp-server to reload the index cache
4. Verify `rag(op=list_mapped)` shows the keys (no `uri` in catalog rows)
5. Verify `rag(op=search, mapped=true, …)` returns the pack envelope

## Activation header (before hits) — legacy inline inject only

When the mapped path is unavailable and a packet must paste the body inline:

```text
This block is the `context` payload from rag(op="search") / pipeline rag-context.
Read it as retrieved RAG context, not optional flavor. Use steals as architectural
priors unless they conflict with fact_ceiling / operator binds.
```

For web-facing prompt prose, prefer intent wording (“retrieved writing samples below”)
**plus** this activation so the model admits the channel — hide tool/pipeline jargon
from Customize Skills; keep it in the inject/orchestrator packet.

## Wire shape

Poetry-pipeline parity (`en-fa-super-romantic-v2` `{poetry_context}`):

1. Optional JSON envelope: `status`, `pipeline: rag-context`, `query`, `retrieval{…}`
2. Body: `[Source: … | score≈… | Last changed: …]` + `[Body evidence]` per hit
3. Steal / leave-behind after hits; ¬ copy biographies
4. Prefer `mapped=true` over pasting a durable markdown URI for the seat to open

Live path when index warm: `rag(op="search")` / `rag-context`. Else
`mapped=true` against the pack index, or assemble pseudo-RAG as last resort.

Precedent packs (v2 pure bodies, indexed):
`cortex://notes/legal/writing-samples/good-cause-plea-rag-context-v2.md`
· `cortex://notes/job-search/cover-letters/cover-letter-rag-context-v2.md`
· charter: `cortex://notes/system/threads/4917-external-prose-charter-stub.md`

## Genre packs (see index)

Authoritative table: `config/mcp/rag_mapped_index.yaml`. Seeded genres:

| Genre | Scope | Query key (normalized) |
|---|---|---|
| Cover / why-here | `cover_letter_samples` | government cover letter mission continuity… |
| Good-cause / reinstatement **plea** | `legal_writing_samples` | good cause late filing reinstatement plea… |

Legal short-form also binds `lawyer-stance` § Production short-form plea. After inject is stable, expand sources into RAG scope `legal_writing_samples`; keep the curated inject as the steals surface.

## Anti-pattern

| ✗ | ✓ |
|---|---|
| Put rag-context wire in a claude.ai / shared_sync skill body | Intent on web skill; wire here |
| Frontier rewrite with samples absent when corpus exists | Attach inject via `mapped=true` |
| Cite a pack URI and assume the seat will apply it | Materialize `rag(op="search", mapped=true)` payload in-message |
| Assume ambient absorption | Explicit activation / mapped search |
