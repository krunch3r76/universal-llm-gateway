---
name: research-article-search
description: On literature-search/recon tasks for extending a RAG research corpus, dispatching live web discovery, scoping candidates, or preparing ingest queues. Use before multi-model search fan-out.
trigger_match_terms: ["research-article-search", "research_article_search", "literature-search", "recon", "tasks", "extending", "rag", "research", "corpus", "dispatching", "live", "web"]
---

# Research Article Search

Complements: `research-article-ingest` for download/index mechanics; `dispatch-workflow` for `team_dispatch` hygiene.

## Trigger

Use for research-corpus extension, literature survey during recon, or multi-model search fan-out: web-search discovery → scoping pass → ingest.

## Two-pass invariant

`discovery ≠ scoping`. Do not collapse into one dispatch; scoping needs discovery results as input.

**Discovery is a pass, not a `team_dispatch` role.** Naming “discovery” in operator prose must carry model + substrate (see `composer-standing-reply-format` § In-flight step naming).

### Preferred discovery chain (bound 2026-09-18 — agent-bus:11649 + assertion 35408)

`gpt-5-search-api` is **demoted** for literature discovery (theme-drift / shallow). Default chain — each pass **adds** against an explicit exclude list:

| Order | Model + substrate | Purpose |
|---|---|---|
| 1 | `openrouter/perplexity/sonar-deep-research` via `pipeline(chat-dispatch)` | first deep lit pass |
| 2 | `openai/gpt-5.6-sol` via `pipeline(chat-dispatch)` | second pass — OpenAI direct; omit `server_tools` so the card can inject `web_search_preview` |
| 3 (final searcher + scoping) | `cdp/opus-5` (cortex-packaged) or cursor-sdk live-checkout Opus when browse is required | last live find + judgment — **not** the discovery opener |

**Wire invariant:** `wire_id = provider_prefix + model`. `GET :9999/v1/models` listing ≠ upstream live.

**Liveness gate:** a SKU is live only if it appears in this table after the last probe, or a probe other than `GET /v1/models` succeeded this session (OpenRouter public list, `:9999/api/models` row, or a non-404 provider response).

**Final-searcher rule:** CDP/cursor Opus runs **last** so earlier deep passes densify the exclude list first. ¬ put CDP/cursor first as the default discovery opener.

**Chain rule:** each dispatch receives (a) themes/brief, (b) **exclude list** of arXiv IDs / DOIs / URLs already found, (c) instruction: return **only novel** rows. Overlaps that slip through are **expected chain noise** — scrub at merge/scoping; ¬ treat as pass failure or blocker.

**Open-access ingest gate (operator constraint):** no institutional library for paywalled PDFs. Discovery may *cite* paywalled work as “no-ingest”; **ingest queue = OA only** (arXiv PDF, ACL Anthology OA, PMC, OpenReview PDF, author PDF). Aligns with `research-article-ingest` (`paywall_stub ∨ abstract_only ⇒ STOP`).

| Retired / demoted / wrong wire | Why |
|---|---|
| `openai/o4-mini-deep-research` (+ `-2025-06-26`) | **retired** — upstream 404 / dead SKU |
| `openai/o3-deep-research` (+ snapshot) | **retired** — superseded chain |
| `openai/gpt-5-search-api` | demoted — theme drift / not lit-agent depth |
| general `openai/gpt-5.5` (+ tools) | demoted — frontier chat SKU; ¬ discovery default |
| `anthropic/*` API mcp | prohibited / timeout path |
| `grok-web` | deprecated |
| `perplexity/sonar-deep-research` (no `openrouter/` prefix) | wrong wire |
| `openrouter/openai/gpt-5.6-sol` | wrong wire — use `openai/gpt-5.6-sol` unless explicitly wanted |

Scoping: after the chain merge — `cdp/opus-5` or cursor-sdk Opus as appropriate (Step 4).

## Step 1 — Search brief

Write `/tmp/agent-bus-<slug>-search-brief.md` with:
1. task: one-sentence search goal;
2. topic framing: in/out scope + separate angles;
3. operator bias: inclusion bar, named-model requirement, counter-evidence obligation, contested numbers;
4. already seeded: arXiv IDs/URLs already in corpus;
5. output format: title, authors/year, arXiv ID or URL, source type, named models, research angle, one-sentence key finding.

Load existing seed from orchestration sidecar when present:
`fs(cortex, md_read, notes/system/threads/<thread-sidecar>.md, section="Step 1 SEED")`.

## Step 2 — Discovery dispatch (chained deep research)

Write/update exclude seed: `cortex://notes/system/threads/<thread>-discovery-exclude.md` (arXiv IDs + titles already found).

```python
pipeline(op="async", pipeline_id="chat-dispatch",
  options={"model": "openrouter/perplexity/sonar-deep-research"},  # then openai/gpt-5.6-sol
  messages=[{"role": "user", "content": "<brief + exclude list + OA-only ingest flag>"}],
  result_delivery={
    "bus_thread": "<orchestration-thread-id>",
    "bus_from_agent": "cursor",
    "bus_to_agent": "cursor",
    "bus_subject": "DONE discovery — sonar-deep-research",
  })
```

Second pass — same shape, `options={"model": "openai/gpt-5.6-sol"}`; omit `server_tools` so the capability card injects `web_search_preview`.

After each pass: append novel IDs to the exclude seed before the next model. Deep research may run minutes — async + poll; ¬ treat search-api as substitute.

Known non-live / wrong-SKU paths: see retired/demoted table above; also `anthropic/*` API with `mcp=True` (`tool_search` 300s timeout, friction 21132) and parametric-only non-research models (recall only — not discovery).

## Step 3 — Wait and validate liveness

For search-family discovery: poll `pipeline(op="result", execution_id=…)` (or consume `result_delivery` bus pointer). For CDP complement: wait via `team_dispatch` `poll_hint` until `archive_uri` is set (**escape:** `project_ask(op="poll")` only when that execution was via `project_ask`).

If output looks parametric (no live links / no current-year finds), do **not** ingest; re-dispatch on the search SKU (or CDP complement), not on general `gpt-5.5`.

Live-search evidence: current-year arXiv IDs, novel framing not in training data, explicit search-language/tool-use acknowledgement.

## Step 4 — Scoping pass

Prompt includes full discovery output + operator bias. Prefer **Opus via CDP**
(`team_dispatch(model=cdp/opus-5)`, cortex-packaged brief; `project_ask` escape
only). `anthropic/*` Stargate API remains prohibited by default
(`decision:anthropic-family-dispatch-substrate`).

Fallback when CDP unavailable: `team_dispatch` reviewer with a non-Anthropic API model, or `seat=cursor-sdk` + `cursor/claude-opus-4-8` when live checkout browse is required — not general `openai/gpt-5.5`.

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
  --title "<title>" --authors "<authors>" --date <YYYY-MM-DD> --scope <scope> --index
```

Batch ingestion: script like `scripts/download-doc-research-corpus.py`.


**Indexing is a separate step — do not omit it.** Without `--index`, the file is indexed only on the next RAG restart or watcher cycle (not immediately), so a verbatim copy of the command without it leaves new files on disk but unsearchable until then — the +0-coverage trap (assertion **21956**, thread 4041). Pass `--index` to trigger immediate indexing via `POST /index` (requires RAG running), then confirm with Step 7 `rag(op="coverage")` that `indexed_files` rose and `last_indexed` is fresh before treating ingest as done.

## Step 7 — Verify coverage

Run `rag(op="coverage")`; confirm each scope count and `last_indexed` freshness.

`¬ use semantic_search_for_verification`: cold/evicted `qwen3-embedding-8b` can yield `chunks_found=0` with 60s latency indistinguishable from not indexed. Use sqlite-backed coverage + “Index complete” chunk logs.

## Scope table

| Subdir | Scope | Content |
|---|---|---|
| `llm/prompting` | `llm_prompting` | large-model prompting, format effects, instruction following |
| `writing` | `writing` | human writing craft — cogency, fluency, persuasion, revision, register |
| `llm/writing` | `llm_writing_anti` | anti-specimen — how LLMs should not write to humans |
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
