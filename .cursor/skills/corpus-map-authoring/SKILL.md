---
name: corpus-map-authoring
description: "When building a durable corpus map before encoding domain knowledge — multi-seat RAG harvest, Opus recon, and map structure for skill/spec authoring."
trigger_match_terms: ["corpus-map-authoring", "corpus_map_authoring", "corpus map", "preliminary map", "refined map", "rag recon map", "corpus harvest", "theme packet", "opus query", "durable recon", "corpus navigation", "research map", "grounding map"]
related_skills: ["corpus-grounded-skill-authoring", "skill-document-writing", "research-article-search", "document-ingestion", "cheap-recon-before-escalation", "cursor-sdk-instruction-standard", "consult-routing"]
---

# Corpus Map Authoring

Build a **durable corpus map** before encoding domain knowledge in a skill or dense spec. This skill governs the **multi-seat harvest workflow**; `corpus-grounded-skill-authoring` governs epistemics (evidence → skill claims); `skill-document-writing` governs SKILL.md form.

## Load order

| Task | Read, in order |
|---|---|
| Any corpus-map arc | this skill → `cortex://notes/system/references/corpus-map-workflow.md` |
| Composer harvest dispatch | + `cursor-sdk-instruction-standard` (D0 `<mcp_capabilities>`) |
| RAG scope guard | + `cheap-recon-before-escalation` § Optional RAG recon |
| After map complete → skill | + `corpus-grounded-skill-authoring` |

## When this fires

`need_durable_corpus_map(label) ⇒ this skill`. Typical triggers:

- Authoring a **knowledge skill** and no digest/map exists yet
- Investigation/densify/skeptic prep needing cited corpus anchors
- Operator ordered **ingest → classify → recon map → refine**

| Situation | Skill |
|---|---|
| Build harvest map from indexed corpus | **this skill** |
| Author skill claims from evidence | corpus-grounded-skill-authoring (after map) |
| Extend corpus (papers not yet indexed) | research-article-search → document-ingestion |
| One-off lookup | `rag(op="search")` only — not this workflow |
| Tier escalation / cheap recon ladder | cheap-recon-before-escalation |

## Pipeline (summary)

Full steps, acceptance, and anti-patterns: **`corpus-map-workflow.md`**.

| Phase | Seat | Output |
|---|---|---|
| **0 Substrate** | operator + scripts | indexed scopes + vocab classify |
| **1 Query design** | Opus High / lead | theme packet (scopes, queries, bound concerns) |
| **2 Harvest** | cursor-sdk Composer | theme sidecars + `preliminary-map.md` |
| **3 Refine** | web-claude Opus | `refined-map.md` (KEEP/DEMOTE/DROP); corpus frozen |
| **4 Ratify/distill** | Opus High | excerpts digest and/or `opus-ratification.md` |
| **5 Bind** | lead | skill load order, dispatch spec, or CHECKPOINT |

**Invariant:** Phase 1 before Phase 2; Phase 3 does not re-query RAG; Composer does not judge relevance.

## Phase 1 — Theme packet (lead)

Before dispatching Composer, author:

1. **Label** — stable slug (`todo:<slug>`, arc name, or skill slug)
2. **Bound concerns** — what the map must answer (fixed through refine)
3. **Scopes** — explicit list; include sibling/composite scopes when primaries may live off the obvious scope
4. **Themes** — `{name, scopes[], queries[]}` per concern; 3–5 queries each

Store at `notes/system/threads/{label}-corpus-theme-packet.md` or embed in an isolated dispatch thread.

## Phase 2 — Composer harvest (dispatch)

```text
team_dispatch(op=generate, seat=cursor-sdk, contract=light-bounded, dispatch_thread_id=<isolated>)
```

Packet MUST include:
- Theme packet (Phase 1)
- `<mcp_capabilities>` naming cortex paths: `notes/system/recon/{label}/`
- Per theme: `rag(op="recon", …)` or numbered discrete scoped searches (S1..Sn)
- Write `preliminary-map.md` — cluster only; `[RELEVANT]` tags; no KEEP/DROP
- SELF-CHECK: list every sidecar path + preliminary-map

**Proven labels:** `todo-cheap-recon-before-tier-escalation`, `todo-mcp-descriptor-slim-at-source`, `mcp-obviates-skills-wave2`.

## Phase 3 — web refine

Handoff to web-claude with: preliminary-map + sidecars + bound concerns.

Deliver `refined-map.md` under `notes/system/recon/{label}/`. Optional `navigation-map.md` for cross-theme index (cheap-recon 3424).

## Phase 4 — Ratify / distill

Opus reads refined map (+ machinery when implement-bound). Write:

- **Skill path:** `notes/system/references/{topic}-excerpts.md` (~500–800 words)
- **Arc path:** `opus-ratification.md` or synthesis sidecar under `notes/system/threads/`

Record scope-correction lessons when a re-run was needed.

## Phase 5 — Bind

| Downstream | Action |
|---|---|
| Knowledge skill | Point `corpus-grounded-skill-authoring` load order at excerpts + optional `{topic}-workflow-map.md` |
| Implement / densify | Mandatory read order in dispatch spec |
| Graph | Assert map URIs on todo/skill entity `evidence_uris` |

## Two exit modes

| Mode | Phase 4 primary artifact | Consumer |
|---|---|---|
| **Skill grounding** | `{topic}-excerpts.md` (+ optional workflow-map) | corpus-grounded-skill-authoring |
| **Problem solving** | `refined-map.md` + ratification/synthesis | densify, skeptic, implement spec |

Same harvest pipeline; differ only in Phase 4 distill shape and Phase 5 bind target.

## Anti-patterns

- Skip Phase 1 → Composer invents weak queries (4005 wave 1 v1)
- Bare `workflows` scope only → miss reflexion/self-refine/MoA (3434)
- web re-RAG during refine → breaks frozen-corpus rule
- Harvest deliverables in `tmp/` only → not durable (D0 violation)
- Confuse this workflow with cheap-recon tier escalation (related, not identical)

## Related skills

- corpus-grounded-skill-authoring — epistemics after map exists
- skill-document-writing — SKILL form, sidecar contracts, registration
- research-article-search — discover papers before ingest
- document-ingestion — bring corpus in before Phase 0b
- cheap-recon-before-escalation — RAG recon scope guard + tier ladder
- cursor-sdk-instruction-standard — Composer dispatch D0–D4
