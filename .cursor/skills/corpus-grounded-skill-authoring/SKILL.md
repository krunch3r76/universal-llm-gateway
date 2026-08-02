---
name: corpus-grounded-skill-authoring
description: "When authoring domain-knowledge skills — facts, thresholds, procedures — derive from corpus evidence not priors; governs content sourcing and drift control."
trigger_match_terms: ["corpus-grounded-skill-authoring", "corpus_grounded_skill_authoring", "corpus-grounded", "ground", "grounding", "domain skill", "knowledge skill", "evidence-grounded", "author from corpus", "domain corpus", "cite evidence", "skill from papers", "skill from docs"]
related_skills: ["corpus-map-authoring", "skill-document-writing", "frontier-model-instructions", "document-ingestion", "research-article-search"]
---

# Corpus-Grounded Skill Authoring

Author domain skills from **evidence, not priors**. This skill governs where a skill's content comes from and how it stays true; `corpus-map-authoring` governs the **harvest workflow** that produces durable maps/digests first; `skill-document-writing` governs the form (L1/L2/L3, lifecycle, registration); `frontier-model-instructions` governs the voice (FOL). Load map workflow + this skill + form/voice for a knowledge skill.

## When this fires

`author_skill ∧ content = domain_knowledge_from_sources ⇒ this skill`. A domain / knowledge skill encodes claims that must trace to a corpus — rules, thresholds, taxonomies, or procedures backed by papers / docs / transcripts / code.

| Situation | Skill |
|---|---|
| Domain claims sourced from a corpus | **this skill** + skill-document-writing |
| Pure SKILL.md mechanics / structure | skill-document-writing only |
| Human-reader prose | prose-discipline |
| Model-targeted procedural voice | frontier-model-instructions |

`¬corpus ⇒ ¬author`: no evidence base ⇒ ingest one first (§ Corpus surfaces) or decline. Priors are the failure mode — a plausible-but-unverified rule is worse than silence.

## Grounding loop

1. **Scope + locate corpus.** State the body of truth the skill must encode; enumerate corpora (`rag(op="list_scopes")`, entity graph, ingested docs, code). Missing ⇒ ingest first (`research-article-search` → `document-ingestion`).
2. **Map, then retrieve.** `¬digest ∧ ¬refined_map ⇒` run `corpus-map-authoring` (Phases 0–5) to produce sidecars + `refined-map.md` + optional `{topic}-excerpts.md` before extracting rules. Then pull from the map/digest and corpus (entity reads, fs reads), ¬ from priors. `load_bearing_claim ⇒ has(source_uri ∨ chunk_id ∨ assertion)`.
3. **Extract core rules from evidence.** Classify with the SkillReducer taxonomy (skill-document-writing § Body taxonomy). Keep evidence-backed `core_rule` / `procedure_step`; demote ungrounded generality to `background`.
4. **Ground in the entity graph.** Assert key claims as entities/assertions with `evidence_uris` → corpus; relate the skill entity to its corpus. **Entities are the SOT** — a domain claim lives as a verifiable, supersedable assertion, not just prose. Prefer entity / fs SOT over semantic RAG for anything that must stay current (RAG snapshots go stale).
5. **Author per skill-document-writing.** Form, L1/L2/L3, FOL, registration.
6. **Gate 1 (faithfulness) + freshness.** Every L2 concept traces to a source. Record the corpus snapshot/date; `corpus_updates ⇒ re-ground`.
7. **Register + grounding sidecar.** `register_skill_substrate(skill_id, skill_path=workspaces://universal-llm-gateway/.cursor/skills/{slug}/SKILL.md)` — the workspace `.cursor/skills` path is SOT; the legacy cortex mirror is generated (todo:consolidate-skill-sot). Write the grounding manifest (below).

## Corpus surfaces (domain-agnostic)

| Surface | Tool | Role |
|---|---|---|
| Semantic corpus | `rag(op="search"\|"list_scopes"\|"coverage", …)` | Retrieval over indexed papers/docs/transcripts. Offline authoring, ¬ runtime. |
| Entity graph | `cortex(tool="search"\|"entities"\|"entity_get", …)` | Structured SOT; edge / friction / assertion-bearing. **Preferred for current-truth claims.** |
| New corpus | `document-ingestion` skill · `rag(op="upsert_article", …)` | Bring a body of material in **before** grounding. |
| Literature recon | `research-article-search` skill | Extend the corpus with live discovery before ingest. |

RAG is offline authoring/audit, ¬ runtime skill discovery (runtime = native index + description triggers). A live entity/SOT beats a RAG snapshot for durability — cite the entity.

## Evidence discipline

- `∀ load_bearing_rule : ∃ source` (chunk_id, doc URI, or entity assertion). Unsourced ⇒ demote to `background` or cut.
- ¬ launder a prior as evidence: `corpus ¬states(claim) ⇒ skill ¬asserts(claim)`.
- Conflicting sources ⇒ record the conflict + pick with stated reason; ¬ silently choose.
- Snapshot every corpus consult (scope + query + date) so staleness is detectable.

## Grounding sidecar (minimal, inline)

```markdown
# Grounding manifest — {skill-slug}
**Date / corpus snapshot:** … · **Author:** …
## Corpus consulted
| Scope / source | Query / doc | Snapshot |
## Evidence map
| L2 rule | Source URI / chunk_id / assertion |
## Deviations / unsourced-cut
```

## Anti-patterns

- Domain claim written from priors, no corpus retrieval.
- Load-bearing rule with no source.
- Treating a semantic-RAG snapshot as durable truth (bind to entity/fs SOT).
- Restating skill-document-writing form rules here (link, ¬ duplicate).
- Skipping `¬corpus ⇒ ingest first`.

## Related skills

- corpus-map-authoring — multi-seat harvest workflow (Opus queries → Composer recon → web refine → distill)
- skill-document-writing — form, L1/L2/L3, lifecycle, registration
- frontier-model-instructions — FOL / voice / compression floor
- document-ingestion — bring a corpus in
- research-article-search — extend the corpus with live discovery
