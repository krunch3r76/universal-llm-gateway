---
name: research-article-search
description: On literature-search/recon tasks for extending a RAG research corpus, dispatching live web discovery, scoping candidates, or preparing ingest queues. Use before multi-model search fan-out.
---

# Research Article Search

Complements: `research-article-ingest` for download/index mechanics; `dispatch-workflow` for `team_dispatch` hygiene.

## Trigger

Use for research-corpus extension, literature survey during recon, or multi-model search fan-out: gatherer discovery → scoping pass → ingest.

## Two-pass invariant

`discovery ≠ scoping`. Do not collapse into one dispatch; scoping needs discovery results as input.

| Pass | Role/model | MCP | Purpose |
|---|---|---|---|
| Discovery | `gatherer`, `openai/gpt-5.5` | `true` | live web search via `web_search` + `browse` |
| Scoping | `reviewer`, Opus or GPT-5.5 | `false` OK | operator bias, dedup, subdir/scope assignment |

## Step 1 — Search brief

Write `/tmp/agent-bus-<slug>-search-brief.md` with:
1. task: one-sentence search goal;
2. topic framing: in/out scope + separate angles;
3. operator bias: inclusion bar, named-model requirement, counter-evidence obligation, contested numbers;
4. already seeded: arXiv IDs/URLs already in corpus;
5. output format: title, authors/year, arXiv ID or URL, source type, named models, research angle, one-sentence key finding.

Load existing seed from orchestration sidecar when present:
`fs(cortex, md_read, notes/system/threads/<thread-sidecar>.md, section="Step 1 SEED")`.

## Step 2 — Discovery dispatch

Only confirmed live-search path: `openai/gpt-5.5` with `mcp=True`.

```python
team_dispatch(op="generate", role="gatherer", model="openai/gpt-5.5",
  contract="light-bounded", mcp=True, max_tool_turns=15,
  dispatch_thread_id="<orchestration-thread-id>", caller_agent="cursor",
  tags=["project:<slug>", "type:research-search", "run:mcp-enabled"])
```

Before waiting, confirm response: `inline_only=false ∧ tool_surface="mcp"`.

Known non-live paths:

| Model/path | Issue |
|---|---|
| `anthropic/*` API with `mcp=True` | `tool_search` 300s timeout; no confirmed live web path (2026-06-29, friction 21132) |
| any `mcp=False` | parametric recall only |
| `grok-web` | deprecated; SuperGrok Heavy expired |
| `xai/grok-4.3` API with `mcp=True` | untested; prior run returned parametric |

Multiple perspectives ⇒ dispatch separate gatherers with distinct search angles.

## Step 3 — Wait and validate liveness

Use agent-bus wait for first gatherer reply. If output looks parametric, do **not** ingest; re-dispatch with confirmed MCP.

Live-search evidence: current-year arXiv IDs, novel framing not in training data, explicit search-language/tool-use acknowledgement.

## Step 4 — Scoping pass

Prompt includes full discovery output + operator bias. `mcp=False` is acceptable because source text is already returned.

```python
team_dispatch(op="generate", role="reviewer", model="anthropic/claude-opus-4-8",
  reasoning_effort="low", contract="light-bounded", mcp=False,
  dispatch_thread_id="<orchestration-thread-id>", caller_agent="cursor",
  tags=["project:<slug>", "type:scope-pass"])
```

Required output table: `arXiv ID | filename slug | subdir | scope | priority | reason | flag`.

Scoping must apply operator bias, dedup seeded articles, assign subdir/scope, flag unnamed/self-reported sources, rate HIGH/MEDIUM/LOW, and surface counter-evidence prominently.

## Step 5 — Optional scope org review

For material extensions (`>5 papers ∨ new_scope_candidate`), post scoping output and dispatch web-claude handoff to confirm/revise scope assignments, decide create-vs-fold, and drop peripheral papers.

New scope requires edits to:
1. `scripts/backfill_article_metadata.py::SUBDIRECTORY_TO_SCOPE`;
2. `~/.gateway/rag.yaml` under `scopes:`;
3. composite scopes `all_corpus` and `research`.

## Step 6 — Ingest

For confirmed candidates, execute `scripts/ingest-article` per `research-article-ingest` and run per-file content integrity before indexing.

```bash
scripts/ingest-article --arxiv <ID> --subdir <subdir> --filename <slug>.pdf \
  --title "<title>" --authors "<authors>" --date <YYYY-MM-DD> --scope <scope>
```

Batch ingestion: script like `scripts/download-doc-research-corpus.py`.

## Step 7 — Verify coverage

Run `rag(op="coverage")`; confirm each scope count and `last_indexed` freshness.

`¬ use semantic_search_for_verification`: cold/evicted `qwen3-embedding-8b` can yield `chunks_found=0` with 60s latency indistinguishable from not indexed. Use sqlite-backed coverage + “Index complete” chunk logs.

## Scope table

| Subdir | Scope | Content |
|---|---|---|
| `llm/prompting` | `llm_prompting` | large-model prompting, format effects, instruction following |
| `prompting` | `small_llm_prompting` | small/local model prompting |
| `workflows` | `workflows` | pipeline architecture, orchestration, long-context instruction following |
| `rag-systems` | `rag_systems` | RAG architecture/eval/benchmarks |
| `software-agents` | `software_agents` | agent workflows, multi-agent SE |
| `knowledge-management` | `knowledge_systems` | PKM, agent memory |
| `graph-modeling` | `graph_modeling` | property graphs, KG construction |
| `belief-consistency` | `belief_consistency` | belief revision, contradictions |
| `information-extraction` | `information_extraction` | NER, relation extraction, structured output |

Input-format comprehension papers ⇒ `llm/prompting` / `llm_prompting` unless volume warrants dedicated scope.

## Failure-targeting example: P1 Track 3

GPT-5.5 with `mcp=True` found 2025/2026 candidates and novel PRISMA/Format Tax framing; Opus `mcp=False` scoped results. Key counter-evidence: arXiv:2511.16707 found no significant Markdown/JSON/XML/plain-text difference (`p > 0.9`). Failed paths: Anthropic MCP timeout, xAI parametric-only, any `mcp=False` for discovery.
