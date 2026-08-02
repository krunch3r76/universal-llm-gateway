---
name: rag-canonical-reference-reminder
description: 'On any RAG-touching task in this repo (ingest, scopes, indexing, coverage, vocabulary classification, or the overloaded Path A/B terminology) read the canonical RAG reference first.'
trigger_match_terms: ["rag-canonical-reference-reminder", "RAG ingest", "ingest article", "add paper", "research corpus", "index document", "RAG scope", "list scopes", "scope coverage", "coverage verification", "vocabulary classification", "vocab-classify", "corpus hints", "scope_vocabulary", "post-index refresh", "watermarks", "reindex", "RAG empty results", "Path A/B"]
related_skills: [research-article-ingest, research-article-search, mcp-surface-change]
---

# Skill: RAG Canonical Reference Reminder

On any RAG-touching task in this repo, read the canonical RAG reference before acting:
`cortex://notes/system/specs/rag-canonical-reference.md`.

It is the source of truth for RAG architecture, the source-of-truth hierarchy, scopes and runtime config, ingest routes, indexing/storage, search/retrieval, vocabulary and scope classification, coverage and verification, post-index enrichment/watermarks, and stale-source handling. Do not reconstruct these from priors — read the doc first, then act.

## Fires on
- Ingest: "RAG ingest", "ingest article", "add paper", "research corpus", "index document"
- Scopes + coverage: "RAG scope", "list scopes", "scope coverage", "coverage verification"
- Vocabulary: "vocabulary classification", "vocab-classify", "corpus hints", "scope_vocabulary"
- Refresh + debug: "post-index refresh", "watermarks", "reindex", "RAG empty results"
- Overloaded term: "Path A/B" near RAG, engram, retrieval-eval, or skill-retrieval design

## "Path A/B" is triple-overloaded — do not conflate
The label names three unrelated things. Do not assume which one applies; the reference disambiguates them in section 3:
- Ingest-route letters — retired (section 3.1)
- Engram retrieval-evaluation baseline — benchmarking only (section 3.2)
- Skill-retrieval design Parts A/B/C — design-stage, not shipped (section 3.3)

## Pointer only
This skill does not restate the reference. Read `cortex://notes/system/specs/rag-canonical-reference.md` (its section 13 specifies these triggers) first, and treat that doc — not this skill — as the source of truth.
