---
name: research-article-ingest
description: Download research papers, RFCs, and primary sources; register with RAG and index. Use when ingesting any primary source into the research corpus.
---

**Tool**: `scripts/ingest-article` (host-side CLI — outbound internet access).
Services make no outbound connections; downloads run on the host. RAG and Stargate are always running — if either is not responding, use the `service-lifecycle` skill to start it before proceeding.

## Primary Source vs Derivative (HARD)

∀ ingestion: committed artifact = raw served bytes — actual PDF, or HTML when no PDF exists. RAG extracts text at index time (`pymupdf4llm.to_markdown()` for PDFs, HTML reader for HTML).
¬ hand-curated summaries, extracted abstracts, or paraphrased entries — that is transcription, not ingestion.
∀ agent-authored content (memos, summaries, argument integration): belongs in Cortex as assertions, not in `docs/research/`.

Default: save served bytes as-is. arXiv → PDF; HTML-only source → HTML.
Paywall stub / abstract-only / preview → **STOP**, do not synthesize a substitute.
Cloudflare challenge → use `curl_cffi` path (§ below). Multiple authoritative formats → prefer arXiv (stable, versioned).

## Workflow

### 1. Locate
Search the web. Prefer arXiv (direct PDF URL). Collect: URL, title, authors, date.

### 2. Choose subdir and scope

| Subdirectory | Scope | Content |
|---|---|---|
| `rag-systems` | `rag_systems` | RAG architecture, evaluation, benchmarks |
| `software-agents` | `software_agents` | Agent workflows, tool registries, multi-agent SE |
| `agent-substrate` | `agent_substrate` | HTTP/MCP substrate, protocol primitives, web architecture |
| `workflows` | `workflows` | Pipeline architecture, agent orchestration |
| `knowledge-management` | `knowledge_systems` | PKM, second brain, agent memory |
| `graph-modeling` | `graph_modeling` | Property graphs, RDF/OWL, KG construction |
| `temporal-provenance` | `temporal_provenance` | Bitemporal, versioning, provenance |
| `belief-consistency` | `belief_consistency` | Belief revision, contradiction handling |
| `information-extraction` | `information_extraction` | NER, relation extraction, structured output |
| `code-retrieval` | `code_retrieval` | Code embeddings, AST chunking, dependency retrieval |
| `code-transformation` | `code_transformation` | LLM code review, refactoring, hallucination mitigation |
| `documentation` | `code_documentation` | Code doc generation, doc-code alignment |
| `prompting` | `small_llm_prompting` | Prompt engineering for small/local models |
| `llm/prompting` | `llm_prompting` | Prompt engineering for large/cloud models |

New subdir: (1) create dir; (2) add subdir→scope to `scripts/backfill_article_metadata.py` (`SUBDIRECTORY_TO_SCOPE`); (3) add scope block to `~/.gateway/rag.yaml` under `scopes:`; (4) add prefix to composite scopes (`all_corpus`, `research`).

### 3. Download and register

```bash
# arXiv
scripts/ingest-article --arxiv <ID> --subdir <subdir> --filename <slug>.pdf \
    --title "<title>" --authors "<authors>" --date <YYYY-MM-DD> --scope <scope>

# Non-arXiv (PDF or HTML)
scripts/ingest-article --url <url> --subdir <subdir> --filename <slug>.<ext> \
    --title "<title>" --authors "<authors>" --date <YYYY-MM-DD> --scope <scope>
```

### 4. Content-integrity check (HARD — gates step 5)

1. Read the file: `fs(sandbox="workspaces", op="read", path="universal-llm-gateway/docs/research/<subdir>/<file>")`
2. Verify **≥2** identity tokens present: author name · title word · date · citation.
3. < 2 tokens → **STOP**: do not index; surface URL, expected metadata, and first ~500 chars.

Multi-document or stub file → treat as content mismatch regardless of token count.

### 5. Index

```
rag(op="coverage")
```

Calling coverage scans all registered files and indexes any not yet indexed. Confirm the file appears in the relevant scope's `indexed_files` count with a fresh `last_indexed` timestamp before declaring done.

## Filename conventions

Lowercase hyphenated slug capturing the key contribution. Extension matches served format: `.pdf` for PDFs, `.html` for HTML-only sources.

## Batch ingestion

Write a download script following `scripts/download-doc-research-corpus.py` (async httpx + semaphore), then run `scripts/backfill_article_metadata.py`. Content-integrity MUST run per-file — one bad ID poisons downstream trust until detected.

## Cloudflare / bot-protected sources

Some hosts (FINRA, SEC, institutional) block on TLS fingerprint (JA3), not User-Agent. Try normal `httpx` first; on 403+`text/html` where a PDF was expected:

```python
from curl_cffi import requests as cffi_requests
session = cffi_requests.Session(impersonate="chrome")
r = session.get(url, timeout=30)
if r.status_code == 200 and (b"%PDF-" in r.content[:10] or "pdf" in r.headers.get("content-type", "")):
    open(dest, "wb").write(r.content)
```

Install: `~/.venvs/universal/bin/pip install curl_cffi`.

## Anti-Transcription Invariant (HARD)

∀ corpus entry: text MUST derive from a downloaded file on disk. ¬ transcribe from WebFetch responses — those are read artefacts, not provenance artefacts. Correct sequence: (1) download to disk; (2) read via `fs` MCP; (3) build entry from that disk read.

## Reading downloaded sources (fs MCP)

```
fs(sandbox="workspaces", op="read",    path="universal-llm-gateway/docs/research/<subdir>/<file>")
fs(sandbox="workspaces", op="md_list", path="universal-llm-gateway/docs/research/<subdir>/<file>.pdf")
fs(sandbox="workspaces", op="md_read", path="...<file>.pdf", section="<Heading>")
```

PDFs auto-convert via `pymupdf4llm.to_markdown()`. For tabular/columnar PDFs, use `finance_extract_pdf(path=...)` via MCP (pdfplumber, preserves table structure).

## What NOT to do

- ¬ add download logic to any service (no outbound access)
- ¬ manually insert into `rag_metadata.db` when the API is available
- ¬ skip content_hash — it's the join key for query-time enrichment
- ¬ write a hand-curated summary in place of the source
- ¬ index before content-integrity passes

## Related

- `cortex:agent-skills/legal-opinion-corpus-ingestion.md` — case-law/statute primary sources; same invariants
- `cortex:agent-skills/thirdparty-api-mirror.md` — vendor API doc primary-source pattern
- `cortex:agent-skills/corpus-cross-reference-discipline.md` — intake + writing-side identifier surfacing
- `service-lifecycle` skill — start/restart RAG or Stargate if not responding
