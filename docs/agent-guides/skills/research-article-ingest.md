---
name: research-article-ingest
description: Download research papers, RFCs, and primary sources; register with RAG and index. Use when ingesting any primary source into the research corpus.
---

# Research Article Ingest

**Tool:** host CLI `scripts/ingest-article`. Services do not make outbound downloads. If RAG/Stargate is down, use `service-lifecycle` before proceeding.

## Hard invariants

`∀ ingestion: committed_artifact = raw_served_bytes` — PDF when available; HTML only when no PDF exists. RAG extracts text at index time.

Forbidden: hand-curated summaries, extracted abstracts, paraphrased entries, WebFetch transcription, or agent-authored memos in `docs/research/`. Agent-authored synthesis belongs in Cortex assertions/notes.

`paywall_stub ∨ abstract_only ∨ preview ⇒ STOP`; do not synthesize a substitute.

Default source preference: arXiv PDF when available; HTML-only source → HTML; multiple authoritative formats → prefer stable/versioned arXiv.

## Workflow

1. **Locate.** Search web; collect URL, title, authors, date. Prefer direct arXiv PDF.
2. **Choose subdir/scope.** Use table below. New subdir requires: create dir; update `scripts/backfill_article_metadata.py::SUBDIRECTORY_TO_SCOPE`; add scope block to `~/.gateway/rag.yaml`; add prefix to `all_corpus` and `research` composite scopes.
3. **Download/register.** Run `scripts/ingest-article`.
4. **Integrity gate before indexing.** Read disk file via `fs`; verify ≥2 identity tokens from author/title/date/citation. `<2 tokens ∨ multi_doc ∨ stub ⇒ STOP` with URL, expected metadata, first ~500 chars.
5. **Index.** Run `rag(op="coverage")`; confirm relevant scope `indexed_files` and fresh `last_indexed` before declaring done.

## Subdir → scope

| Subdir | Scope | Content |
|---|---|---|
| `rag-systems` | `rag_systems` | RAG architecture/eval/benchmarks |
| `software-agents` | `software_agents` | Agent workflows, tool registries, multi-agent SE |
| `agent-substrate` | `agent_substrate` | HTTP/MCP substrate, protocol primitives, web architecture |
| `workflows` | `workflows` | Pipeline architecture, orchestration |
| `knowledge-management` | `knowledge_systems` | PKM, second brain, memory |
| `graph-modeling` | `graph_modeling` | Property graphs, RDF/OWL, KG construction |
| `temporal-provenance` | `temporal_provenance` | Bitemporal/versioning/provenance |
| `belief-consistency` | `belief_consistency` | Belief revision/contradictions |
| `information-extraction` | `information_extraction` | NER, relation extraction, structured output |
| `code-retrieval` | `code_retrieval` | Code embeddings, AST chunking, dependency retrieval |
| `code-transformation` | `code_transformation` | LLM code review/refactor/hallucination mitigation |
| `documentation` | `code_documentation` | Code doc generation/alignment |
| `prompting` | `small_llm_prompting` | Small/local model prompting |
| `llm/prompting` | `llm_prompting` | Large/cloud model prompting |

## Commands

```bash
# arXiv
scripts/ingest-article --arxiv <ID> --subdir <subdir> --filename <slug>.pdf \
  --title "<title>" --authors "<authors>" --date <YYYY-MM-DD> --scope <scope>

# Non-arXiv PDF/HTML
scripts/ingest-article --url <url> --subdir <subdir> --filename <slug>.<ext> \
  --title "<title>" --authors "<authors>" --date <YYYY-MM-DD> --scope <scope>
```

Filenames: lowercase hyphenated contribution slug; extension matches served format.

## Batch ingestion

Use a script shaped like `scripts/download-doc-research-corpus.py` (async `httpx` + semaphore), then `scripts/backfill_article_metadata.py`. Run the integrity gate per file; one bad ID poisons downstream trust.

## Bot-protected sources

Try normal `httpx` first. On `403 + text/html` where PDF expected, use `curl_cffi` impersonation:

```python
from curl_cffi import requests as cffi_requests
session = cffi_requests.Session(impersonate="chrome")
r = session.get(url, timeout=30)
if r.status_code == 200 and (b"%PDF-" in r.content[:10] or "pdf" in r.headers.get("content-type", "")):
    open(dest, "wb").write(r.content)
```

Install only if needed: `~/.venvs/universal/bin/pip install curl_cffi`.

## Reading downloaded sources

```text
fs(workspaces, read,    universal-llm-gateway/docs/research/<subdir>/<file>)
fs(workspaces, md_list, universal-llm-gateway/docs/research/<subdir>/<file>.pdf)
fs(workspaces, md_read, ...<file>.pdf, section=<Heading>)
```

PDFs convert with `pymupdf4llm`. For tabular/columnar PDFs, use the finance/pdfplumber extraction path.

## Do not

- add download logic to services;
- manually insert into `rag_metadata.db` when API works;
- skip `content_hash` (query-time enrichment join key);
- write a summary in place of the source;
- index before integrity passes.

## Related

`legal-opinion-corpus-ingestion`, `thirdparty-api-mirror`, `corpus-cross-reference-discipline`, `service-lifecycle`.
